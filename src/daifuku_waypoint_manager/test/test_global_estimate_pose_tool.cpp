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

#include <QApplication>
#include <QDialog>
#include <QElapsedTimer>
#include <QLabel>
#include <QPushButton>

#include <functional>
#include <chrono>

#include <emcl3d/action/global_localization.hpp>
#include <gtest/gtest.h>
#include <pluginlib/class_loader.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <rviz_common/display_context.hpp>
#include <rviz_common/properties/property.hpp>
#include <rviz_common/ros_integration/ros_node_abstraction.hpp>
#include <rviz_common/tool.hpp>

class TestContext : public rviz_common::DisplayContext
{
public:
  std::shared_ptr<rviz_common::ros_integration::RosNodeAbstraction> node;
  Ogre::SceneManager * getSceneManager() const override {return nullptr;}
  rviz_common::WindowManagerInterface * getWindowManager() const override {return nullptr;}
  std::shared_ptr<rviz_common::interaction::SelectionManagerIface>
  getSelectionManager() const override {return nullptr;}
  std::shared_ptr<rviz_common::interaction::HandlerManagerIface>
  getHandlerManager() const override {return nullptr;}
  std::shared_ptr<rviz_common::interaction::ViewPickerIface>
  getViewPicker() const override {return nullptr;}
  rviz_common::FrameManagerIface * getFrameManager() const override {return nullptr;}
  QString getFixedFrame() const override {return "map";}
  uint64_t getFrameCount() const override {return 0;}
  rviz_common::DisplayFactory * getDisplayFactory() const override {return nullptr;}
  rviz_common::ros_integration::RosNodeAbstractionIface::WeakPtr
  getRosNodeAbstraction() const override {return node;}
  void handleChar(QKeyEvent *, rviz_common::RenderPanel *) override {}
  void handleMouseEvent(const rviz_common::ViewportMouseEvent &) override {}
  rviz_common::ToolManager * getToolManager() const override {return nullptr;}
  rviz_common::ViewManager * getViewManager() const override {return nullptr;}
  rviz_common::transformation::TransformationManager *
  getTransformationManager() override {return nullptr;}
  rviz_common::DisplayGroup * getRootDisplayGroup() const override {return nullptr;}
  uint32_t getDefaultVisibilityBit() const override {return 0;}
  rviz_common::BitAllocator * visibilityBits() override {return nullptr;}
  void setStatus(const QString &) override {}
  QString getHelpPath() const override {return {};}
  std::shared_ptr<rclcpp::Clock> getClock() override {return node->get_raw_node()->get_clock();}
  void lockRender() override {}
  void unlockRender() override {}
  void queueRender() override {}
};

TEST(GlobalEstimatePose, PluginLoadsAndReportsUnavailableWithoutBlocking)
{
  int argc = 1;
  char name[] = "test_global_estimate_pose";
  char * argv[] = {name, nullptr};
  QApplication app(argc, argv);
  pluginlib::ClassLoader<rviz_common::Tool> loader("rviz_common", "rviz_common::Tool");
  auto tool = loader.createSharedInstance("daifuku_waypoint_manager/GlobalEstimatePose");
  QElapsedTimer activation;
  activation.start();
  tool->activate();
  EXPECT_LT(activation.elapsed(), 1000);
  auto * status = tool->getPropertyContainer()->subProp("Status");
  QElapsedTimer deadline;
  deadline.start();
  while (!status->getValue().toString().startsWith("Unavailable") && deadline.elapsed() < 2000) {
    app.processEvents();
  }
  EXPECT_EQ(status->getValue().toString(), "Unavailable: /emcl3d/global_localization");
  QDialog * dialog = nullptr;
  for (auto * widget : QApplication::topLevelWidgets()) {
    if (widget->windowTitle() == "Global Estimate Pose") {
      dialog = qobject_cast<QDialog *>(widget);
    }
  }
  ASSERT_NE(dialog, nullptr);
  EXPECT_TRUE(dialog->isVisible());
  for (auto * button : dialog->findChildren<QPushButton *>()) {
    if (button->text() == "Cancel search") {
      EXPECT_FALSE(button->isEnabled());
    }
  }
  EXPECT_TRUE(dialog->grab().save("/tmp/global-estimate-pose-unavailable.png"));
  dialog->hide();
  tool->deactivate();
  tool->activate();
  EXPECT_TRUE(dialog->isVisible());

  rclcpp::init(0, nullptr);
  TestContext context;
  context.node =
    std::make_shared<rviz_common::ros_integration::RosNodeAbstraction>("global_pose_test");
  tool->initialize(&context);
  EXPECT_EQ(tool->getName(), "Global Estimate Pose");
  using Action = emcl3d::action::GlobalLocalization;
  using ServerGoal = rclcpp_action::ServerGoalHandle<Action>;
  auto node = context.node->get_raw_node();
  std::shared_ptr<ServerGoal> pending;
  bool reject = false;
  int requests = 0;
  auto server = rclcpp_action::create_server<Action>(
    node, "/emcl3d/global_localization",
    [&](const auto &, std::shared_ptr<const Action::Goal> goal) {
      ++requests;
      EXPECT_FALSE(goal->estimate_only);
      EXPECT_FALSE(goal->use_search_bounds);
      return reject ? rclcpp_action::GoalResponse::REJECT :
             rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
    },
    [](const auto &) {return rclcpp_action::CancelResponse::ACCEPT;},
    [&](std::shared_ptr<ServerGoal> goal) {pending = goal;});
  auto pump = [&](const std::function<bool()> & condition) {
      QElapsedTimer timeout;
      timeout.start();
      while (!condition() && timeout.elapsed() < 5000) {
        rclcpp::spin_some(node);
        app.processEvents();
      }
      return condition();
    };
  tool->activate();
  ASSERT_TRUE(pump([&]() {
    if (!pending && status->getValue().toString().startsWith("Unavailable")) {
      tool->activate();
    }
    return pending != nullptr;
  }));
  auto feedback = std::make_shared<Action::Feedback>();
  feedback->stage = "searching";
  feedback->evaluated_candidates = 3;
  feedback->total_candidates = 10;
  auto feedback_timer = node->create_wall_timer(std::chrono::milliseconds(50),
    [pending, feedback]() {pending->publish_feedback(feedback);});
  ASSERT_TRUE(pump([&]() {return status->getValue().toString().contains("3 / 10");}));
  feedback_timer.reset();
  tool->activate();
  EXPECT_EQ(requests, 1);
  auto success = std::make_shared<Action::Result>();
  success->success = true;
  success->applied = true;
  success->code = "SUCCESS";
  success->message = "test result";
  success->pose.pose.pose.orientation.w = 1.0;
  pending->succeed(success);
  ASSERT_TRUE(pump([&]() {return status->getValue().toString().startsWith("Pose applied");}));
  EXPECT_TRUE(dialog->grab().save("/tmp/global-estimate-pose-success.png"));

  pending.reset();
  tool->deactivate();
  tool->activate();
  ASSERT_TRUE(pump([&]() {return pending != nullptr;}));
  for (auto * button : dialog->findChildren<QPushButton *>()) {
    if (button->text() == "Cancel search") {
      ASSERT_TRUE(pump([&]() {return button->isEnabled();}));
      button->click();
    }
  }
  ASSERT_TRUE(pump([&]() {return pending->is_canceling();}));
  auto canceled = std::make_shared<Action::Result>();
  canceled->code = "CANCELED";
  pending->canceled(canceled);
  ASSERT_TRUE(pump([&]() {return status->getValue().toString().startsWith("Canceled");}));

  pending.reset();
  tool->deactivate();
  tool->activate();
  ASSERT_TRUE(pump([&]() {return pending != nullptr;}));
  auto failed = std::make_shared<Action::Result>();
  failed->code = "AMBIGUOUS";
  failed->message = "multiple hypotheses";
  pending->abort(failed);
  ASSERT_TRUE(pump([&]() {return status->getValue().toString().contains("Failed [AMBIGUOUS]");}));

  pending.reset();
  tool->deactivate();
  tool->activate();
  ASSERT_TRUE(pump([&]() {return pending != nullptr;}));
  success->applied = false;
  pending->succeed(success);
  ASSERT_TRUE(pump([&]() {
    return status->getValue().toString().startsWith("Pose estimated (not applied)");
  }));

  pending.reset();
  reject = true;
  tool->deactivate();
  tool->activate();
  ASSERT_TRUE(pump([&]() {return status->getValue().toString().startsWith("Rejected");}));
  tool.reset();
  server.reset();
  rclcpp::shutdown();
}
