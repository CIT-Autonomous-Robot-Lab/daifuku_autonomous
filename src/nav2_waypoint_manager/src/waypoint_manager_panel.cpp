#include "nav2_waypoint_manager/waypoint_manager_panel.hpp"

#include <cmath>
#include <fstream>
#include <utility>

#include <QDoubleSpinBox>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QListWidget>
#include <QMessageBox>
#include <QMetaObject>
#include <QPointer>
#include <QPushButton>
#include <QSaveFile>
#include <QVBoxLayout>

#include <pluginlib/class_list_macros.hpp>
#include <rviz_common/display_context.hpp>
#include <rviz_common/ros_integration/ros_node_abstraction.hpp>
#include <yaml-cpp/yaml.h>

namespace nav2_waypoint_manager
{

namespace
{
constexpr char kMarkerTopic[] = "/waypoint_markers";
constexpr char kActionName[] = "/navigate_through_poses";
constexpr char kClickedPointTopic[] = "/clicked_point";
constexpr char kWaypointPoseTopic[] = "/waypoint_pose";

QDoubleSpinBox * makeSpinBox(QWidget * parent, double minimum, double maximum, double value)
{
  auto * spinbox = new QDoubleSpinBox(parent);
  spinbox->setRange(minimum, maximum);
  spinbox->setDecimals(3);
  spinbox->setSingleStep(0.1);
  spinbox->setValue(value);
  return spinbox;
}
}  // namespace

WaypointManagerPanel::WaypointManagerPanel(QWidget * parent)
: rviz_common::Panel(parent)
{
  buildUi();
}

WaypointManagerPanel::~WaypointManagerPanel()
{
  if (active_goal_ && action_client_) {
    action_client_->async_cancel_goal(active_goal_);
  }
}

void WaypointManagerPanel::onInitialize()
{
  auto ros_node_abstraction = getDisplayContext()->getRosNodeAbstraction().lock();
  if (!ros_node_abstraction) {
    setStatus("Error: RViz ROS node is unavailable");
    return;
  }

  node_ = ros_node_abstraction->get_raw_node();
  marker_publisher_ = node_->create_publisher<visualization_msgs::msg::MarkerArray>(
    kMarkerTopic, rclcpp::QoS(1).transient_local());
  clicked_point_subscription_ = node_->create_subscription<geometry_msgs::msg::PointStamped>(
    kClickedPointTopic, rclcpp::QoS(10),
    [this](geometry_msgs::msg::PointStamped::SharedPtr point) {
      QMetaObject::invokeMethod(this, [this, point]() {addClickedPoint(*point);}, Qt::QueuedConnection);
    });
  clicked_pose_subscription_ = node_->create_subscription<geometry_msgs::msg::PoseStamped>(
    kWaypointPoseTopic, rclcpp::QoS(10),
    [this](geometry_msgs::msg::PoseStamped::SharedPtr pose) {
      QMetaObject::invokeMethod(this, [this, pose]() {addClickedPose(*pose);}, Qt::QueuedConnection);
    });
  action_client_ = rclcpp_action::create_client<NavigateThroughPoses>(node_, kActionName);
  publishMarkers();
  setStatus("Waiting");
  updateButtons();
}

void WaypointManagerPanel::buildUi()
{
  auto * layout = new QVBoxLayout(this);
  layout->setContentsMargins(4, 4, 4, 4);

  auto * input_form = new QFormLayout();
  x_spinbox_ = makeSpinBox(this, -100000.0, 100000.0, 0.0);
  y_spinbox_ = makeSpinBox(this, -100000.0, 100000.0, 0.0);
  z_spinbox_ = makeSpinBox(this, -100000.0, 100000.0, 0.0);
  yaw_spinbox_ = makeSpinBox(this, -360.0, 360.0, 0.0);
  input_form->addRow("X [m]", x_spinbox_);
  input_form->addRow("Y [m]", y_spinbox_);
  input_form->addRow("Z [m]", z_spinbox_);
  input_form->addRow("Yaw [deg]", yaw_spinbox_);
  layout->addLayout(input_form);

  auto * click_hint = new QLabel(
    "Mouse input: select RViz's 2D Goal Pose tool, then click and drag on the map. "
    "The click sets the position and the drag direction sets the waypoint orientation.", this);
  click_hint->setWordWrap(true);
  layout->addWidget(click_hint);

  auto * add_button = new QPushButton("Add Waypoint", this);
  connect(add_button, &QPushButton::clicked, this, &WaypointManagerPanel::addWaypoint);
  layout->addWidget(add_button);

  auto * delete_row = new QHBoxLayout();
  auto * delete_selected = new QPushButton("Delete Selected", this);
  auto * delete_last = new QPushButton("Delete Last", this);
  auto * clear = new QPushButton("Clear", this);
  connect(delete_selected, &QPushButton::clicked, this, &WaypointManagerPanel::deleteSelected);
  connect(delete_last, &QPushButton::clicked, this, &WaypointManagerPanel::deleteLast);
  connect(clear, &QPushButton::clicked, this, &WaypointManagerPanel::clearWaypoints);
  delete_row->addWidget(delete_selected);
  delete_row->addWidget(delete_last);
  delete_row->addWidget(clear);
  layout->addLayout(delete_row);

  layout->addWidget(new QLabel("Waypoint List", this));
  waypoint_list_ = new QListWidget(this);
  waypoint_list_->setSelectionMode(QAbstractItemView::SingleSelection);
  layout->addWidget(waypoint_list_);

  auto * reorder_row = new QHBoxLayout();
  auto * up_button = new QPushButton("Move Up", this);
  auto * down_button = new QPushButton("Move Down", this);
  connect(up_button, &QPushButton::clicked, this, &WaypointManagerPanel::moveUp);
  connect(down_button, &QPushButton::clicked, this, &WaypointManagerPanel::moveDown);
  reorder_row->addWidget(up_button);
  reorder_row->addWidget(down_button);
  layout->addLayout(reorder_row);

  auto * file_row = new QHBoxLayout();
  auto * save_button = new QPushButton("Save YAML", this);
  auto * load_button = new QPushButton("Load YAML", this);
  connect(save_button, &QPushButton::clicked, this, &WaypointManagerPanel::saveYaml);
  connect(load_button, &QPushButton::clicked, this, &WaypointManagerPanel::loadYaml);
  file_row->addWidget(save_button);
  file_row->addWidget(load_button);
  layout->addLayout(file_row);

  auto * action_row = new QHBoxLayout();
  start_button_ = new QPushButton("Start", this);
  cancel_button_ = new QPushButton("Cancel", this);
  connect(start_button_, &QPushButton::clicked, this, &WaypointManagerPanel::startFollowing);
  connect(cancel_button_, &QPushButton::clicked, this, &WaypointManagerPanel::cancelFollowing);
  action_row->addWidget(start_button_);
  action_row->addWidget(cancel_button_);
  layout->addLayout(action_row);

  status_label_ = new QLabel("Status: Waiting", this);
  status_label_->setWordWrap(true);
  layout->addWidget(status_label_);
}

geometry_msgs::msg::PoseStamped WaypointManagerPanel::poseFromInputs() const
{
  geometry_msgs::msg::PoseStamped pose;
  pose.header.frame_id = frame_id_;
  pose.pose.position.x = x_spinbox_->value();
  pose.pose.position.y = y_spinbox_->value();
  pose.pose.position.z = z_spinbox_->value();
  const double half_yaw = yaw_spinbox_->value() * M_PI / 360.0;
  pose.pose.orientation.z = std::sin(half_yaw);
  pose.pose.orientation.w = std::cos(half_yaw);
  return pose;
}

void WaypointManagerPanel::addWaypoint()
{
  if (frame_id_.empty()) {
    setStatus("Error: frame_id is not set");
    return;
  }
  waypoints_.push_back(poseFromInputs());
  refreshList();
  publishMarkers();
}

void WaypointManagerPanel::addClickedPoint(const geometry_msgs::msg::PointStamped & point)
{
  if (point.header.frame_id.empty()) {
    setStatus("Error: clicked point has no frame_id");
    return;
  }
  if (!waypoints_.empty() && point.header.frame_id != frame_id_) {
    setStatus("Error: clicked point frame_id differs from existing waypoints");
    return;
  }

  frame_id_ = point.header.frame_id;
  x_spinbox_->setValue(point.point.x);
  y_spinbox_->setValue(point.point.y);
  z_spinbox_->setValue(point.point.z);
  waypoints_.push_back(poseFromInputs());
  refreshList();
  publishMarkers();
  setStatus(QString("Added waypoint %1 from map click").arg(waypoints_.size() - 1));
}

void WaypointManagerPanel::addClickedPose(const geometry_msgs::msg::PoseStamped & pose)
{
  if (pose.header.frame_id.empty()) {
    setStatus("Error: clicked pose has no frame_id");
    return;
  }
  if (!waypoints_.empty() && pose.header.frame_id != frame_id_) {
    setStatus("Error: clicked pose frame_id differs from existing waypoints");
    return;
  }

  frame_id_ = pose.header.frame_id;
  waypoints_.push_back(pose);
  x_spinbox_->setValue(pose.pose.position.x);
  y_spinbox_->setValue(pose.pose.position.y);
  z_spinbox_->setValue(pose.pose.position.z);
  const double yaw = std::atan2(
    2.0 * (pose.pose.orientation.w * pose.pose.orientation.z +
    pose.pose.orientation.x * pose.pose.orientation.y),
    1.0 - 2.0 * (pose.pose.orientation.y * pose.pose.orientation.y +
    pose.pose.orientation.z * pose.pose.orientation.z));
  yaw_spinbox_->setValue(yaw * 180.0 / M_PI);
  refreshList();
  publishMarkers();
  setStatus(QString("Added waypoint %1 from click and drag").arg(waypoints_.size() - 1));
}

void WaypointManagerPanel::deleteSelected()
{
  const int index = waypoint_list_->currentRow();
  if (index < 0 || index >= static_cast<int>(waypoints_.size())) {
    setStatus("Error: select a waypoint to delete");
    return;
  }
  waypoints_.erase(waypoints_.begin() + index);
  refreshList();
  publishMarkers();
}

void WaypointManagerPanel::deleteLast()
{
  if (waypoints_.empty()) {
    setStatus("Error: no waypoint to delete");
    return;
  }
  waypoints_.pop_back();
  refreshList();
  publishMarkers();
}

void WaypointManagerPanel::clearWaypoints()
{
  waypoints_.clear();
  refreshList();
  publishMarkers();
}

void WaypointManagerPanel::moveUp()
{
  const int index = waypoint_list_->currentRow();
  if (index <= 0 || index >= static_cast<int>(waypoints_.size())) {
    return;
  }
  std::swap(waypoints_[index], waypoints_[index - 1]);
  refreshList();
  waypoint_list_->setCurrentRow(index - 1);
  publishMarkers();
}

void WaypointManagerPanel::moveDown()
{
  const int index = waypoint_list_->currentRow();
  if (index < 0 || index + 1 >= static_cast<int>(waypoints_.size())) {
    return;
  }
  std::swap(waypoints_[index], waypoints_[index + 1]);
  refreshList();
  waypoint_list_->setCurrentRow(index + 1);
  publishMarkers();
}

QString WaypointManagerPanel::formatWaypoint(
  int index, const geometry_msgs::msg::PoseStamped & pose) const
{
  return QString("%1: x=%2, y=%3, z=%4")
         .arg(index)
         .arg(pose.pose.position.x, 0, 'f', 3)
         .arg(pose.pose.position.y, 0, 'f', 3)
         .arg(pose.pose.position.z, 0, 'f', 3);
}

void WaypointManagerPanel::refreshList()
{
  const int selected = waypoint_list_->currentRow();
  waypoint_list_->clear();
  for (size_t i = 0; i < waypoints_.size(); ++i) {
    waypoint_list_->addItem(formatWaypoint(static_cast<int>(i), waypoints_[i]));
  }
  if (selected >= 0 && selected < waypoint_list_->count()) {
    waypoint_list_->setCurrentRow(selected);
  }
  updateButtons();
}

void WaypointManagerPanel::publishMarkers()
{
  if (!marker_publisher_) {
    return;
  }
  visualization_msgs::msg::MarkerArray markers;
  visualization_msgs::msg::Marker clear;
  clear.action = visualization_msgs::msg::Marker::DELETEALL;
  markers.markers.push_back(clear);

  const auto stamp = node_->now();
  if (waypoints_.size() >= 2) {
    visualization_msgs::msg::Marker route;
    route.header.frame_id = frame_id_;
    route.header.stamp = stamp;
    route.ns = "waypoint_route";
    route.id = 0;
    route.type = visualization_msgs::msg::Marker::LINE_STRIP;
    route.action = visualization_msgs::msg::Marker::ADD;
    route.scale.x = 0.06;
    route.color.r = 1.0F;
    route.color.g = 0.7F;
    route.color.b = 0.0F;
    route.color.a = 1.0F;
    route.points.reserve(waypoints_.size());
    for (const auto & waypoint : waypoints_) {
      route.points.push_back(waypoint.pose.position);
    }
    markers.markers.push_back(route);
  }

  for (size_t i = 0; i < waypoints_.size(); ++i) {
    const auto & waypoint = waypoints_[i];
    visualization_msgs::msg::Marker arrow;
    arrow.header = waypoint.header;
    arrow.header.stamp = stamp;
    arrow.ns = "waypoint_arrow";
    arrow.id = static_cast<int>(i);
    arrow.type = visualization_msgs::msg::Marker::ARROW;
    arrow.action = visualization_msgs::msg::Marker::ADD;
    arrow.pose = waypoint.pose;
    arrow.scale.x = 0.55;
    arrow.scale.y = 0.12;
    arrow.scale.z = 0.12;
    arrow.color.r = 0.1F;
    arrow.color.g = 0.8F;
    arrow.color.b = 1.0F;
    arrow.color.a = 1.0F;
    markers.markers.push_back(arrow);

    visualization_msgs::msg::Marker label;
    label.header = arrow.header;
    label.ns = "waypoint_label";
    label.id = static_cast<int>(i);
    label.type = visualization_msgs::msg::Marker::TEXT_VIEW_FACING;
    label.action = visualization_msgs::msg::Marker::ADD;
    label.pose.position = waypoint.pose.position;
    label.pose.position.z += 0.35;
    label.pose.orientation.w = 1.0;
    label.scale.z = 0.25;
    label.color.r = 1.0F;
    label.color.g = 1.0F;
    label.color.b = 1.0F;
    label.color.a = 1.0F;
    label.text = std::to_string(i);
    markers.markers.push_back(label);
  }
  marker_publisher_->publish(markers);
}

bool WaypointManagerPanel::writeYamlFile(const QString & filename, QString * error) const
{
  YAML::Emitter out;
  out << YAML::BeginMap << YAML::Key << "frame_id" << YAML::Value << frame_id_;
  out << YAML::Key << "waypoints" << YAML::Value << YAML::BeginSeq;
  for (size_t i = 0; i < waypoints_.size(); ++i) {
    const auto & pose = waypoints_[i].pose;
    out << YAML::BeginMap << YAML::Key << "name" << YAML::Value << "waypoint_" + std::to_string(i);
    out << YAML::Key << "position" << YAML::Value << YAML::BeginMap;
    out << YAML::Key << "x" << YAML::Value << pose.position.x;
    out << YAML::Key << "y" << YAML::Value << pose.position.y;
    out << YAML::Key << "z" << YAML::Value << pose.position.z << YAML::EndMap;
    out << YAML::Key << "orientation" << YAML::Value << YAML::BeginMap;
    out << YAML::Key << "x" << YAML::Value << pose.orientation.x;
    out << YAML::Key << "y" << YAML::Value << pose.orientation.y;
    out << YAML::Key << "z" << YAML::Value << pose.orientation.z;
    out << YAML::Key << "w" << YAML::Value << pose.orientation.w << YAML::EndMap << YAML::EndMap;
  }
  out << YAML::EndSeq << YAML::EndMap;
  if (!out.good()) {
    *error = QString("YAML serialization failed: %1").arg(out.GetLastError().c_str());
    return false;
  }

  QSaveFile file(filename);
  if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) {
    *error = QString("Cannot open file for writing: %1").arg(file.errorString());
    return false;
  }
  if (file.write(out.c_str(), static_cast<qint64>(out.size())) < 0 || !file.commit()) {
    *error = QString("Cannot save YAML: %1").arg(file.errorString());
    return false;
  }
  return true;
}

void WaypointManagerPanel::saveYaml()
{
  if (frame_id_.empty()) {
    setStatus("Error: frame_id is not set");
    return;
  }
  const QString filename = QFileDialog::getSaveFileName(this, "Save Waypoints", QString(), "YAML files (*.yaml *.yml)");
  if (filename.isEmpty()) {
    return;
  }
  if (QFileInfo::exists(filename) && QMessageBox::question(
      this, "Overwrite file", QString("Overwrite %1?").arg(filename),
      QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes)
  {
    return;
  }
  QString error;
  if (!writeYamlFile(filename, &error)) {
    setStatus("Error: " + error);
    return;
  }
  setStatus("Saved " + filename);
}

bool WaypointManagerPanel::readYamlFile(
  const QString & filename, std::vector<geometry_msgs::msg::PoseStamped> * poses,
  std::string * loaded_frame_id, QString * error) const
{
  try {
    const YAML::Node root = YAML::LoadFile(filename.toStdString());
    if (!root.IsMap() || !root["frame_id"] || !root["frame_id"].IsScalar()) {
      *error = "Missing or invalid frame_id";
      return false;
    }
    *loaded_frame_id = root["frame_id"].as<std::string>();
    if (loaded_frame_id->empty()) {
      *error = "frame_id must not be empty";
      return false;
    }
    const YAML::Node entries = root["waypoints"];
    if (!entries || !entries.IsSequence()) {
      *error = "Missing or invalid waypoints sequence";
      return false;
    }
    poses->clear();
    for (const auto & entry : entries) {
      const YAML::Node position = entry["position"];
      const YAML::Node orientation = entry["orientation"];
      if (!entry.IsMap() || !position || !orientation) {
        *error = "Each waypoint requires position and orientation";
        return false;
      }
      geometry_msgs::msg::PoseStamped pose;
      pose.header.frame_id = *loaded_frame_id;
      pose.pose.position.x = position["x"].as<double>();
      pose.pose.position.y = position["y"].as<double>();
      pose.pose.position.z = position["z"].as<double>();
      pose.pose.orientation.x = orientation["x"].as<double>();
      pose.pose.orientation.y = orientation["y"].as<double>();
      pose.pose.orientation.z = orientation["z"].as<double>();
      pose.pose.orientation.w = orientation["w"].as<double>();
      const double quaternion_squared =
        pose.pose.orientation.x * pose.pose.orientation.x + pose.pose.orientation.y * pose.pose.orientation.y +
        pose.pose.orientation.z * pose.pose.orientation.z + pose.pose.orientation.w * pose.pose.orientation.w;
      if (!std::isfinite(pose.pose.position.x) || !std::isfinite(pose.pose.position.y) ||
        !std::isfinite(pose.pose.position.z) || !std::isfinite(quaternion_squared) || quaternion_squared < 1e-12)
      {
        *error = "Waypoint contains non-finite coordinates or a zero quaternion";
        return false;
      }
      poses->push_back(pose);
    }
  } catch (const YAML::Exception & exception) {
    *error = QString("Invalid YAML: %1").arg(exception.what());
    return false;
  }
  return true;
}

void WaypointManagerPanel::loadYaml()
{
  const QString filename = QFileDialog::getOpenFileName(this, "Load Waypoints", QString(), "YAML files (*.yaml *.yml)");
  if (filename.isEmpty()) {
    return;
  }
  std::vector<geometry_msgs::msg::PoseStamped> loaded;
  std::string loaded_frame_id;
  QString error;
  if (!readYamlFile(filename, &loaded, &loaded_frame_id, &error)) {
    setStatus("Error: " + error);
    return;
  }

  if (!waypoints_.empty()) {
    QMessageBox choice(this);
    choice.setWindowTitle("Load Waypoints");
    choice.setText("How should loaded waypoints be applied?");
    auto * replace = choice.addButton("Replace", QMessageBox::AcceptRole);
    auto * append = choice.addButton("Append", QMessageBox::ActionRole);
    auto * cancel = choice.addButton(QMessageBox::Cancel);
    choice.exec();
    if (choice.clickedButton() == nullptr || choice.clickedButton() == cancel) {
      return;
    }
    if (choice.clickedButton() == append) {
      if (loaded_frame_id != frame_id_) {
        setStatus("Error: cannot append waypoints with a different frame_id");
        return;
      }
      waypoints_.insert(waypoints_.end(), loaded.begin(), loaded.end());
    } else if (choice.clickedButton() == replace) {
      waypoints_ = std::move(loaded);
      frame_id_ = loaded_frame_id;
    } else {
      return;
    }
  } else {
    waypoints_ = std::move(loaded);
    frame_id_ = loaded_frame_id;
  }
  refreshList();
  publishMarkers();
  setStatus("Loaded " + filename);
}

void WaypointManagerPanel::startFollowing()
{
  if (waypoints_.empty()) {
    setStatus("Error: no waypoints registered");
    return;
  }
  if (frame_id_.empty()) {
    setStatus("Error: frame_id is not set");
    return;
  }
  if (!action_client_ || !action_client_->action_server_is_ready()) {
    setStatus("Error: /navigate_through_poses action server is unavailable");
    return;
  }
  NavigateThroughPoses::Goal goal;
  goal.poses = waypoints_;
  for (auto & pose : goal.poses) {
    pose.header.frame_id = frame_id_;
    pose.header.stamp = node_->now();
  }
  QPointer<WaypointManagerPanel> panel(this);
  rclcpp_action::Client<NavigateThroughPoses>::SendGoalOptions options;
  options.goal_response_callback = [panel](GoalHandleNavigateThroughPoses::SharedPtr goal_handle) {
      if (!panel) {return;}
      QMetaObject::invokeMethod(panel, [panel, goal_handle]() {
        if (!panel) {return;}
        if (!goal_handle) {
          panel->setStatus("Error: NavigateThroughPoses goal was rejected");
        } else {
          panel->active_goal_ = goal_handle;
          panel->setStatus("Navigating through waypoints");
        }
        panel->updateButtons();
      }, Qt::QueuedConnection);
    };
  options.result_callback = [panel](const GoalHandleNavigateThroughPoses::WrappedResult & result) {
    if (!panel) {return;}
      const int code = static_cast<int>(result.code);
      QMetaObject::invokeMethod(panel, [panel, code]() {
        if (panel) {panel->handleResult(code);}
      }, Qt::QueuedConnection);
    };
  action_client_->async_send_goal(goal, options);
  setStatus("Sending NavigateThroughPoses goal...");
  start_button_->setEnabled(false);
}

void WaypointManagerPanel::cancelFollowing()
{
  if (!active_goal_ || !action_client_) {
    setStatus("No active NavigateThroughPoses goal");
    return;
  }
  action_client_->async_cancel_goal(active_goal_);
  setStatus("Cancellation requested");
}

void WaypointManagerPanel::handleResult(int result_code)
{
  active_goal_.reset();
  if (result_code == static_cast<int>(rclcpp_action::ResultCode::SUCCEEDED)) {
    setStatus("Succeeded");
  } else if (result_code == static_cast<int>(rclcpp_action::ResultCode::CANCELED)) {
    setStatus("Canceled");
  } else if (result_code == static_cast<int>(rclcpp_action::ResultCode::ABORTED)) {
    setStatus("Failed (aborted)");
  } else {
    setStatus("Failed (unknown result)");
  }
  updateButtons();
}

void WaypointManagerPanel::setStatus(const QString & status)
{
  status_label_->setText("Status: " + status);
}

void WaypointManagerPanel::updateButtons()
{
  start_button_->setEnabled(!waypoints_.empty() && !active_goal_ && action_client_);
  cancel_button_->setEnabled(static_cast<bool>(active_goal_));
}

}  // namespace nav2_waypoint_manager

PLUGINLIB_EXPORT_CLASS(nav2_waypoint_manager::WaypointManagerPanel, rviz_common::Panel)
