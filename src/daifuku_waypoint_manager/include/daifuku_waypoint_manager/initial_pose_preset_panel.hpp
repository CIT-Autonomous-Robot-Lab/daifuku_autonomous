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

#ifndef INITIAL_POSE_PRESET_PANEL__INITIAL_POSE_PRESET_PANEL_HPP_
#define INITIAL_POSE_PRESET_PANEL__INITIAL_POSE_PRESET_PANEL_HPP_

#include <optional>
#include <string>
#include <vector>

#include <QWidget>

#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rviz_common/panel.hpp>
#include <tf2_ros/buffer.h>
#include <tf2_ros/transform_listener.h>

class QComboBox;
class QLabel;
class QLineEdit;

namespace initial_pose_preset_panel
{

class InitialPosePresetPanel : public rviz_common::Panel
{
  Q_OBJECT

public:
  explicit InitialPosePresetPanel(QWidget * parent = nullptr);
  void onInitialize() override;
  void load(const rviz_common::Config & config) override;
  void save(rviz_common::Config config) const override;

private Q_SLOTS:
  void applySelected();
  void addCurrent();
  void addRobotPose();
  void deleteSelected();
  void openPresetFile();
  void savePresetFileAs();

private:
  struct Preset
  {
    std::string name;
    geometry_msgs::msg::PoseWithCovarianceStamped pose;
  };

  void buildUi();
  void addPose(const geometry_msgs::msg::PoseWithCovarianceStamped & pose);
  void refreshPresetCombo();
  void setStatus(const QString & status);
  bool loadPresets(const QString & filename, QString * error);
  bool savePresets(const QString & filename, QString * error) const;

  rclcpp::Node::SharedPtr node_;
  rclcpp::Publisher<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr initial_pose_publisher_;
  rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr initial_pose_subscription_;
  std::unique_ptr<tf2_ros::Buffer> tf_buffer_;
  std::shared_ptr<tf2_ros::TransformListener> tf_listener_;
  std::optional<geometry_msgs::msg::PoseWithCovarianceStamped> current_pose_;
  std::vector<Preset> presets_;
  QString preset_file_path_;

  QComboBox * preset_combo_{nullptr};
  QLineEdit * name_edit_{nullptr};
  QLineEdit * path_edit_{nullptr};
  QLabel * status_label_{nullptr};
};

}  // namespace initial_pose_preset_panel

#endif  // INITIAL_POSE_PRESET_PANEL__INITIAL_POSE_PRESET_PANEL_HPP_
