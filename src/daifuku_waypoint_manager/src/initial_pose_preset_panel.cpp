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

// Ported from CIT Autonomous Robot Lab's initial_pose_preset_panel.
#include "daifuku_waypoint_manager/initial_pose_preset_panel.hpp"

#include <algorithm>
#include <cmath>

#include <QComboBox>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMetaObject>
#include <QPushButton>
#include <QSaveFile>
#include <QStandardPaths>
#include <QVBoxLayout>

#include <pluginlib/class_list_macros.hpp>
#include <rviz_common/config.hpp>
#include <rviz_common/display_context.hpp>
#include <rviz_common/ros_integration/ros_node_abstraction.hpp>
#include <tf2/exceptions.h>
#include <yaml-cpp/yaml.h>

namespace initial_pose_preset_panel
{

namespace
{
constexpr char kInitialPoseTopic[] = "/initialpose";
constexpr char kMapFrame[] = "map";
constexpr char kBaseFrame[] = "base_link";

bool isFinitePose(const geometry_msgs::msg::PoseWithCovarianceStamped & pose)
{
  const auto & p = pose.pose.pose.position;
  const auto & q = pose.pose.pose.orientation;
  const double q_squared = q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w;
  return !pose.header.frame_id.empty() && std::isfinite(p.x) && std::isfinite(p.y) &&
         std::isfinite(p.z) && std::isfinite(q_squared) && q_squared > 1e-12 &&
         std::all_of(pose.pose.covariance.begin(), pose.pose.covariance.end(),
           [](double value) {return std::isfinite(value);});
}
}  // namespace

InitialPosePresetPanel::InitialPosePresetPanel(QWidget * parent)
: rviz_common::Panel(parent),
  preset_file_path_(QStandardPaths::writableLocation(QStandardPaths::AppConfigLocation) +
  "/initial_pose_preset_panel/initialpose/initial_pose_presets.yaml")
{
  buildUi();
}

void InitialPosePresetPanel::load(const rviz_common::Config & config)
{
  rviz_common::Panel::load(config);
  QString path;
  if (config.mapGetString("Preset File", &path) && !path.isEmpty()) {
    if (!QFileInfo(path).isFile()) {
      setStatus("Error: preset file not found: " + path);
      return;
    }
    QString error;
    if (!loadPresets(path, &error)) {
      setStatus("Error: " + error);
      return;
    }
    preset_file_path_ = path;
    path_edit_->setText(path);
    refreshPresetCombo();
  }
}

void InitialPosePresetPanel::save(rviz_common::Config config) const
{
  rviz_common::Panel::save(config);
  config.mapSetValue("Preset File", preset_file_path_);
}

void InitialPosePresetPanel::onInitialize()
{
  auto ros_node_abstraction = getDisplayContext()->getRosNodeAbstraction().lock();
  if (!ros_node_abstraction) {
    setStatus("Error: RViz ROS node is unavailable");
    return;
  }
  node_ = ros_node_abstraction->get_raw_node();
  tf_buffer_ = std::make_unique<tf2_ros::Buffer>(node_->get_clock());
  tf_listener_ = std::make_shared<tf2_ros::TransformListener>(*tf_buffer_);
  initial_pose_publisher_ = node_->create_publisher<geometry_msgs::msg::PoseWithCovarianceStamped>(
    kInitialPoseTopic, rclcpp::QoS(10));
  initial_pose_subscription_ = node_->create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
    kInitialPoseTopic, rclcpp::QoS(10),
    [this](geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr pose) {
      QMetaObject::invokeMethod(this, [this, pose]() {current_pose_ = *pose;}, Qt::QueuedConnection);
    });

  QString error;
  if (!loadPresets(preset_file_path_, &error)) {
    setStatus("Error: " + error);
    return;
  }
  refreshPresetCombo();
  setStatus("Save a 2D Pose Estimate or the current TF robot pose");
}

void InitialPosePresetPanel::buildUi()
{
  auto * layout = new QVBoxLayout(this);
  layout->setContentsMargins(4, 4, 4, 4);

  path_edit_ = new QLineEdit(preset_file_path_, this);
  path_edit_->setReadOnly(true);
  layout->addWidget(path_edit_);
  auto * file_row = new QHBoxLayout();
  auto * open_button = new QPushButton("Open YAML", this);
  auto * save_as_button = new QPushButton("Save As...", this);
  connect(open_button, &QPushButton::clicked, this, &InitialPosePresetPanel::openPresetFile);
  connect(save_as_button, &QPushButton::clicked, this, &InitialPosePresetPanel::savePresetFileAs);
  file_row->addWidget(open_button);
  file_row->addWidget(save_as_button);
  layout->addLayout(file_row);

  preset_combo_ = new QComboBox(this);
  layout->addWidget(preset_combo_);

  auto * apply_button = new QPushButton("Apply", this);
  auto * delete_button = new QPushButton("Delete", this);
  connect(apply_button, &QPushButton::clicked, this, &InitialPosePresetPanel::applySelected);
  connect(delete_button, &QPushButton::clicked, this, &InitialPosePresetPanel::deleteSelected);
  auto * action_row = new QHBoxLayout();
  action_row->addWidget(apply_button);
  action_row->addWidget(delete_button);
  layout->addLayout(action_row);

  auto * add_row = new QHBoxLayout();
  name_edit_ = new QLineEdit(this);
  name_edit_->setPlaceholderText("Preset name");
  auto * add_button = new QPushButton("Add Current", this);
  auto * add_robot_button = new QPushButton("Add Robot Pose", this);
  connect(add_button, &QPushButton::clicked, this, &InitialPosePresetPanel::addCurrent);
  connect(add_robot_button, &QPushButton::clicked, this, &InitialPosePresetPanel::addRobotPose);
  add_row->addWidget(name_edit_);
  add_row->addWidget(add_button);
  add_row->addWidget(add_robot_button);
  layout->addLayout(add_row);
  status_label_ = new QLabel(this);
  status_label_->setWordWrap(true);
  layout->addWidget(status_label_);
}

void InitialPosePresetPanel::applySelected()
{
  const int index = preset_combo_->currentIndex();
  if (index < 0 || index >= static_cast<int>(presets_.size()) || !initial_pose_publisher_) {
    setStatus("Error: select a preset");
    return;
  }
  auto pose = presets_[index].pose;
  pose.header.stamp = node_->now();
  initial_pose_publisher_->publish(pose);
  setStatus("Applied " + QString::fromStdString(presets_[index].name));
}

void InitialPosePresetPanel::addCurrent()
{
  if (!current_pose_ || !isFinitePose(*current_pose_)) {
    setStatus("Error: set an initial pose with 2D Pose Estimate first");
    return;
  }
  addPose(*current_pose_);
}

void InitialPosePresetPanel::addRobotPose()
{
  if (!tf_buffer_) {
    setStatus("Error: TF listener is unavailable");
    return;
  }
  try {
    const auto transform = tf_buffer_->lookupTransform(kMapFrame, kBaseFrame, tf2::TimePointZero);
    geometry_msgs::msg::PoseWithCovarianceStamped pose;
    pose.header = transform.header;
    pose.pose.pose.position.x = transform.transform.translation.x;
    pose.pose.pose.position.y = transform.transform.translation.y;
    pose.pose.pose.position.z = transform.transform.translation.z;
    pose.pose.pose.orientation = transform.transform.rotation;
    // TF has no uncertainty. Use the usual initial-pose defaults: 0.5 m (XY) and ~15 deg (yaw).
    pose.pose.covariance[0] = 0.25;
    pose.pose.covariance[7] = 0.25;
    pose.pose.covariance[35] = 0.0685;
    addPose(pose);
  } catch (const tf2::TransformException & exception) {
    setStatus(QString("Error: cannot transform %1 to %2: %3")
      .arg(kBaseFrame, kMapFrame, exception.what()));
  }
}

void InitialPosePresetPanel::addPose(const geometry_msgs::msg::PoseWithCovarianceStamped & pose)
{
  if (!isFinitePose(pose)) {
    setStatus("Error: robot pose is invalid");
    return;
  }
  const QString name = name_edit_->text().trimmed();
  if (name.isEmpty()) {
    setStatus("Error: enter a preset name");
    return;
  }
  const std::string name_text = name.toStdString();
  if (std::any_of(presets_.begin(), presets_.end(), [&name_text](const Preset & preset) {
      return preset.name == name_text;
    }))
  {
    setStatus("Error: a preset with that name already exists");
    return;
  }
  presets_.push_back({name_text, pose});
  QString error;
  if (!savePresets(preset_file_path_, &error)) {
    presets_.pop_back();
    setStatus("Error: " + error);
    return;
  }
  refreshPresetCombo();
  preset_combo_->setCurrentIndex(preset_combo_->count() - 1);
  name_edit_->clear();
  setStatus("Saved " + name);
}

void InitialPosePresetPanel::deleteSelected()
{
  const int index = preset_combo_->currentIndex();
  if (index < 0 || index >= static_cast<int>(presets_.size())) {
    setStatus("Error: select a preset");
    return;
  }
  const Preset removed = presets_[index];
  presets_.erase(presets_.begin() + index);
  QString error;
  if (!savePresets(preset_file_path_, &error)) {
    presets_.insert(presets_.begin() + index, removed);
    setStatus("Error: " + error);
    return;
  }
  refreshPresetCombo();
  setStatus("Deleted " + QString::fromStdString(removed.name));
}

void InitialPosePresetPanel::refreshPresetCombo()
{
  const int selected = preset_combo_->currentIndex();
  preset_combo_->clear();
  for (const auto & preset : presets_) {
    preset_combo_->addItem(QString::fromStdString(preset.name));
  }
  if (selected >= 0 && selected < preset_combo_->count()) {
    preset_combo_->setCurrentIndex(selected);
  }
}

void InitialPosePresetPanel::openPresetFile()
{
  const QString filename = QFileDialog::getOpenFileName(
    this, "Open pose presets", preset_file_path_, "YAML files (*.yaml *.yml);;All files (*)");
  if (filename.isEmpty()) {
    return;
  }
  if (!QFileInfo(filename).isFile()) {
    setStatus("Error: preset file not found: " + filename);
    return;
  }
  QString error;
  if (!loadPresets(filename, &error)) {
    setStatus("Error: " + error);
    return;
  }
  preset_file_path_ = filename;
  path_edit_->setText(filename);
  refreshPresetCombo();
  Q_EMIT configChanged();
  setStatus("Loaded " + filename);
}

void InitialPosePresetPanel::savePresetFileAs()
{
  const QString filename = QFileDialog::getSaveFileName(
    this, "Save pose presets", preset_file_path_, "YAML files (*.yaml *.yml);;All files (*)");
  if (filename.isEmpty()) {
    return;
  }
  QString error;
  if (!savePresets(filename, &error)) {
    setStatus("Error: " + error);
    return;
  }
  preset_file_path_ = filename;
  path_edit_->setText(filename);
  Q_EMIT configChanged();
  setStatus("Saved " + filename);
}

bool InitialPosePresetPanel::loadPresets(const QString & filename, QString * error)
{
  if (!QFile::exists(filename)) {
    presets_.clear();
    return true;
  }
  try {
    const YAML::Node entries = YAML::LoadFile(filename.toStdString())["presets"];
    if (!entries || !entries.IsSequence()) {
      *error = "invalid presets YAML";
      return false;
    }
    std::vector<Preset> loaded;
    for (const auto & entry : entries) {
      Preset preset;
      preset.name = entry["name"].as<std::string>();
      auto & pose = preset.pose;
      pose.header.frame_id = entry["frame_id"].as<std::string>();
      const YAML::Node position = entry["position"];
      const YAML::Node orientation = entry["orientation"];
      pose.pose.pose.position.x = position["x"].as<double>();
      pose.pose.pose.position.y = position["y"].as<double>();
      pose.pose.pose.position.z = position["z"].as<double>();
      pose.pose.pose.orientation.x = orientation["x"].as<double>();
      pose.pose.pose.orientation.y = orientation["y"].as<double>();
      pose.pose.pose.orientation.z = orientation["z"].as<double>();
      pose.pose.pose.orientation.w = orientation["w"].as<double>();
      const YAML::Node covariance = entry["covariance"];
      if (!covariance || !covariance.IsSequence() || covariance.size() != pose.pose.covariance.size()) {
        *error = "each preset requires 36 covariance values";
        return false;
      }
      for (size_t i = 0; i < pose.pose.covariance.size(); ++i) {
        pose.pose.covariance[i] = covariance[i].as<double>();
      }
      if (preset.name.empty() || !isFinitePose(pose)) {
        *error = "preset has an invalid name or pose";
        return false;
      }
      loaded.push_back(std::move(preset));
    }
    presets_ = std::move(loaded);
  } catch (const YAML::Exception & exception) {
    *error = QString("invalid YAML: %1").arg(exception.what());
    return false;
  }
  return true;
}

bool InitialPosePresetPanel::savePresets(const QString & filename, QString * error) const
{
  if (!QDir().mkpath(QFileInfo(filename).absolutePath())) {
    *error = "cannot create configuration directory";
    return false;
  }
  YAML::Emitter out;
  out << YAML::BeginMap << YAML::Key << "presets" << YAML::Value << YAML::BeginSeq;
  for (const auto & preset : presets_) {
    const auto & pose = preset.pose.pose.pose;
    out << YAML::BeginMap << YAML::Key << "name" << YAML::Value << preset.name;
    out << YAML::Key << "frame_id" << YAML::Value << preset.pose.header.frame_id;
    out << YAML::Key << "position" << YAML::Value << YAML::BeginMap;
    out << YAML::Key << "x" << YAML::Value << pose.position.x << YAML::Key << "y" << YAML::Value << pose.position.y;
    out << YAML::Key << "z" << YAML::Value << pose.position.z << YAML::EndMap;
    out << YAML::Key << "orientation" << YAML::Value << YAML::BeginMap;
    out << YAML::Key << "x" << YAML::Value << pose.orientation.x << YAML::Key << "y" << YAML::Value << pose.orientation.y;
    out << YAML::Key << "z" << YAML::Value << pose.orientation.z << YAML::Key << "w" << YAML::Value << pose.orientation.w << YAML::EndMap;
    out << YAML::Key << "covariance" << YAML::Value << YAML::Flow << YAML::BeginSeq;
    for (const double value : preset.pose.pose.covariance) {out << value;}
    out << YAML::EndSeq << YAML::EndMap;
  }
  out << YAML::EndSeq << YAML::EndMap;
  QSaveFile file(filename);
  if (!out.good() || !file.open(QIODevice::WriteOnly | QIODevice::Text) ||
    file.write(out.c_str(), static_cast<qint64>(out.size())) != static_cast<qint64>(out.size()) ||
    !file.commit())
  {
    *error = "cannot save presets YAML";
    return false;
  }
  return true;
}

void InitialPosePresetPanel::setStatus(const QString & status)
{
  setToolTip(status);
  status_label_->setText(status);
}

}  // namespace initial_pose_preset_panel

PLUGINLIB_EXPORT_CLASS(initial_pose_preset_panel::InitialPosePresetPanel, rviz_common::Panel)
