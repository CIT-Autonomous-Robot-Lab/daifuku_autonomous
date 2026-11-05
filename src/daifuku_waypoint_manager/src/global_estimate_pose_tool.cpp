// Copyright 2026 Keita Sekiguchi / nop
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <cmath>
#include <memory>
#include <mutex>
#include <string>

#include <QDialog>
#include <QLabel>
#include <QPushButton>
#include <QTimer>
#include <QVBoxLayout>

#include <emcl3d/action/global_localization.hpp>
#include <pluginlib/class_list_macros.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <rviz_common/display_context.hpp>
#include <rviz_common/properties/string_property.hpp>
#include <rviz_common/ros_integration/ros_node_abstraction.hpp>
#include <rviz_common/tool.hpp>

namespace daifuku_waypoint_manager
{

class GlobalEstimatePoseTool : public rviz_common::Tool
{
  using Action = emcl3d::action::GlobalLocalization;
  using GoalHandle = rclcpp_action::ClientGoalHandle<Action>;

  // ROS コールバックは Tool を参照せず、Qt タイマだけが UI を更新する。
  struct State
  {
    std::mutex mutex;
    GoalHandle::SharedPtr goal;
    QString message{"Ready"};
    bool busy{false};
    bool cancel_requested{false};
    bool cancel_sent{false};
    bool closed{false};
  };

public:
  GlobalEstimatePoseTool()
  : state_(std::make_shared<State>())
  {
    status_ = new rviz_common::properties::StringProperty(
      "Status", "Ready", "Global localization result", getPropertyContainer());
    status_->setReadOnly(true);
    dialog_ = std::make_unique<QDialog>();
    dialog_->setWindowTitle("Global Estimate Pose");
    dialog_->setMinimumWidth(460);
    auto * layout = new QVBoxLayout(dialog_.get());
    label_ = new QLabel("Ready", dialog_.get());
    label_->setWordWrap(true);
    layout->addWidget(label_);
    cancel_ = new QPushButton("Cancel search", dialog_.get());
    cancel_->setEnabled(false);
    layout->addWidget(cancel_);
    auto * close = new QPushButton("Close", dialog_.get());
    layout->addWidget(close);
    connect(close, &QPushButton::clicked, dialog_.get(), &QDialog::hide);
    connect(cancel_, &QPushButton::clicked, this, [this]() {
      std::lock_guard<std::mutex> lock(state_->mutex);
      state_->cancel_requested = true;
      state_->message = "Cancel requested; waiting for server";
    });
    auto * timer = new QTimer(this);
    connect(timer, &QTimer::timeout, this, [this]() {refresh();});
    timer->start(100);
  }

  ~GlobalEstimatePoseTool() override
  {
    GoalHandle::SharedPtr goal;
    {
      std::lock_guard<std::mutex> lock(state_->mutex);
      state_->closed = true;
      goal = state_->goal;
    }
    if (goal && client_) {
      try {
        client_->async_cancel_goal(goal);
      } catch (const std::exception &) {
        // 終了中は UI へ通知できない。
      }
    }
  }

  void onInitialize() override
  {
    setName("Global Estimate Pose");
    setDescription("Search the emcl3d map for the robot pose");
    const auto abstraction = context_->getRosNodeAbstraction().lock();
    if (abstraction) {
      client_ = rclcpp_action::create_client<Action>(
        abstraction->get_raw_node(), "/emcl3d/global_localization");
    }
  }

  void activate() override
  {
    dialog_->show();
    dialog_->raise();
    std::lock_guard<std::mutex> lock(state_->mutex);
    if (state_->busy) {
      return;
    }
    if (!client_ || !client_->action_server_is_ready()) {
      state_->message = "Unavailable: /emcl3d/global_localization";
      return;
    }
    state_->busy = true;
    state_->cancel_requested = false;
    state_->cancel_sent = false;
    state_->message = "Requesting global localization";
    auto state = state_;
    std::weak_ptr<rclcpp_action::Client<Action>> weak_client = client_;
    rclcpp_action::Client<Action>::SendGoalOptions options;
    options.goal_response_callback = [state, weak_client](GoalHandle::SharedPtr goal) {
        bool closed;
        {
          std::lock_guard<std::mutex> guard(state->mutex);
          state->goal = goal;
          closed = state->closed;
          if (!goal) {
            state->busy = false;
            state->message = "Rejected: server is busy or request is invalid";
          }
        }
        if (closed && goal) {
          if (auto client = weak_client.lock()) {
            try {
              client->async_cancel_goal(goal);
            } catch (const std::exception &) {
              // Tool は既に閉じられている。
            }
          }
        }
      };
    options.feedback_callback =
      [state](GoalHandle::SharedPtr, const std::shared_ptr<const Action::Feedback> feedback) {
        std::lock_guard<std::mutex> guard(state->mutex);
        if (!state->cancel_requested) {
          state->message = QString("%1: %2 / %3 candidates, inliers %4%, %5 s")
            .arg(QString::fromStdString(feedback->stage))
            .arg(static_cast<qulonglong>(feedback->evaluated_candidates))
            .arg(static_cast<qulonglong>(feedback->total_candidates))
            .arg(feedback->best_inlier_ratio * 100.0, 0, 'f', 1)
            .arg(feedback->elapsed_seconds, 0, 'f', 1);
        }
      };
    options.result_callback = [state](const GoalHandle::WrappedResult & result) {
        std::lock_guard<std::mutex> guard(state->mutex);
        state->busy = false;
        state->goal.reset();
        QString outcome = "Failed";
        if (result.code == rclcpp_action::ResultCode::CANCELED) {
          outcome = "Canceled";
        } else if (result.code == rclcpp_action::ResultCode::SUCCEEDED &&
          result.result && result.result->success)
        {
          outcome = result.result->applied ? "Pose applied" : "Pose estimated (not applied)";
          const auto & pose = result.result->pose.pose.pose;
          const auto & q = pose.orientation;
          const double yaw = std::atan2(
            2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z));
          outcome += QString(": x=%1, y=%2 m, yaw=%3 rad")
            .arg(pose.position.x, 0, 'f', 2).arg(pose.position.y, 0, 'f', 2)
            .arg(yaw, 0, 'f', 2);
        }
        state->message = outcome;
        if (result.result) {
          state->message += QString(" [%1]: %2")
            .arg(QString::fromStdString(result.result->code),
            QString::fromStdString(result.result->message));
        }
      };
    try {
      Action::Goal goal;
      goal.estimate_only = false;
      goal.use_search_bounds = false;
      client_->async_send_goal(goal, options);
    } catch (const std::exception & error) {
      state_->busy = false;
      state_->message = QString("Request failed: %1").arg(error.what());
    }
  }

  void deactivate() override {}

private:
  void refresh()
  {
    QString message;
    GoalHandle::SharedPtr cancel_goal;
    bool busy;
    bool cancel_requested;
    {
      std::lock_guard<std::mutex> lock(state_->mutex);
      message = state_->message;
      busy = state_->busy;
      cancel_requested = state_->cancel_requested;
      if (busy && cancel_requested && !state_->cancel_sent && state_->goal) {
        cancel_goal = state_->goal;
        state_->cancel_sent = true;
      }
    }
    if (status_->getString() != message) {
      label_->setText(message);
      status_->setString(message);
      setStatus(message);
    }
    cancel_->setEnabled(busy && !cancel_requested);
    if (cancel_goal) {
      auto state = state_;
      try {
        client_->async_cancel_goal(cancel_goal, [state, cancel_goal](auto response) {
            std::lock_guard<std::mutex> guard(state->mutex);
            if (state->busy && state->goal == cancel_goal && response->goals_canceling.empty()) {
              state->cancel_requested = false;
              state->cancel_sent = false;
              state->message = "Cancellation rejected; waiting for result";
            }
          });
      } catch (const std::exception & error) {
        std::lock_guard<std::mutex> lock(state_->mutex);
        state_->message = QString("Cancel failed: %1; waiting for result").arg(error.what());
        state_->cancel_requested = false;
        state_->cancel_sent = false;
      }
    }
  }

  std::shared_ptr<State> state_;
  rclcpp_action::Client<Action>::SharedPtr client_;
  rviz_common::properties::StringProperty * status_;
  std::unique_ptr<QDialog> dialog_;
  QLabel * label_;
  QPushButton * cancel_;
};

}  // namespace daifuku_waypoint_manager

PLUGINLIB_EXPORT_CLASS(daifuku_waypoint_manager::GlobalEstimatePoseTool, rviz_common::Tool)
