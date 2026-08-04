#include "daifuku_waypoint_manager/waypoint_manager_panel.hpp"

#include <cmath>
#include <utility>

#include <QFileDialog>
#include <QFileInfo>
#include <QHBoxLayout>
#include <QLabel>
#include <QListWidget>
#include <QMessageBox>
#include <QMetaObject>
#include <QPointer>
#include <QPushButton>
#include <QSaveFile>
#include <QTimer>
#include <QVBoxLayout>

#include <pluginlib/class_list_macros.hpp>
#include <rviz_common/display_context.hpp>
#include <rviz_common/frame_manager_iface.hpp>
#include <rviz_common/ros_integration/ros_node_abstraction.hpp>
#include <yaml-cpp/yaml.h>

namespace daifuku_waypoint_manager
{

namespace
{
constexpr char kMarkerTopic[] = "/waypoint_markers";
constexpr char kActionName[] = "/follow_waypoints";
constexpr char kClickedPointTopic[] = "/clicked_point";
constexpr char kWaypointPoseTopic[] = "/waypoint_pose";
// 機体の位置を取る TF フレーム。本リポジトリの約束は base_footprint だが、それを出さない
// 構成でも線が消えないよう base_link まで見る。
constexpr char kRobotFrame[] = "base_footprint";
constexpr char kRobotFrameFallback[] = "base_link";
constexpr int kLeadIntervalMs = 500;
// これ未満しか機体が動いていなければマーカを出し直さない。
constexpr double kLeadMoveThreshold = 0.05;

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
  action_client_ = rclcpp_action::create_client<FollowWaypoints>(node_, kActionName);
  lead_timer_ = new QTimer(this);
  connect(lead_timer_, &QTimer::timeout, this, &WaypointManagerPanel::updateLead);
  lead_timer_->start(kLeadIntervalMs);
  publishMarkers();
  setStatus("Waiting");
  updateButtons();
}

void WaypointManagerPanel::buildUi()
{
  auto * layout = new QVBoxLayout(this);
  layout->setContentsMargins(4, 4, 4, 4);

  auto * click_hint = new QLabel(
    "Select RViz's 2D Goal Pose tool, then click and drag on the map to add a waypoint. "
    "The click sets the position and the drag direction sets the waypoint orientation.", this);
  click_hint->setWordWrap(true);
  layout->addWidget(click_hint);

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
  connect(
    waypoint_list_, &QListWidget::currentRowChanged, this,
    &WaypointManagerPanel::selectionChanged);
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
  geometry_msgs::msg::PoseStamped pose;
  pose.header = point.header;
  pose.pose.position = point.point;
  pose.pose.orientation.w = 1.0;
  waypoints_.push_back(pose);
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
  suppress_selection_publish_ = true;
  waypoint_list_->clear();
  for (size_t i = 0; i < waypoints_.size(); ++i) {
    waypoint_list_->addItem(formatWaypoint(static_cast<int>(i), waypoints_[i]));
  }
  if (selected >= 0 && selected < waypoint_list_->count()) {
    waypoint_list_->setCurrentRow(selected);
  }
  suppress_selection_publish_ = false;
  updateButtons();
}

void WaypointManagerPanel::selectionChanged()
{
  if (suppress_selection_publish_) {
    return;
  }
  // 色と ns/id は変わらないので DELETEALL は要らない (付けると全マーカが作り直されて瞬く)。
  publishMarkers(false);
}

void WaypointManagerPanel::publishMarkers(bool reset)
{
  if (!marker_publisher_) {
    return;
  }
  visualization_msgs::msg::MarkerArray markers;
  if (reset) {
    visualization_msgs::msg::Marker clear;
    clear.action = visualization_msgs::msg::Marker::DELETEALL;
    markers.markers.push_back(clear);
  }

  const auto stamp = node_->now();

  // 機体から 1 点目までの区間。巡回中は引かない (1 点目はもう後ろにあるため。いま
  // どこへ向かっているかは Nav2 の Path 表示に出る)。
  geometry_msgs::msg::Point lead;
  QString lead_reason;
  const bool draw_lead = !active_goal_ && !goal_pending_ && leadPoint(&lead, &lead_reason);
  if (draw_lead) {
    visualization_msgs::msg::Marker lead_marker;
    lead_marker.header.frame_id = frame_id_;
    lead_marker.header.stamp = stamp;
    lead_marker.ns = "waypoint_lead";
    lead_marker.id = 0;
    lead_marker.type = visualization_msgs::msg::Marker::LINE_STRIP;
    lead_marker.action = visualization_msgs::msg::Marker::ADD;
    lead_marker.scale.x = 0.06;
    lead_marker.color.r = 1.0F;
    lead_marker.color.g = 0.7F;
    lead_marker.color.b = 0.0F;
    lead_marker.color.a = 0.5F;
    lead_marker.points.push_back(lead);
    lead_marker.points.push_back(waypoints_.front().pose.position);
    markers.markers.push_back(lead_marker);
    lead_origin_ = lead;
  } else if (lead_drawn_ && !reset) {
    // DELETEALL を付けない出し直しでは、消えた区間を明示的に消す。
    visualization_msgs::msg::Marker lead_marker;
    lead_marker.ns = "waypoint_lead";
    lead_marker.id = 0;
    lead_marker.action = visualization_msgs::msg::Marker::DELETE;
    markers.markers.push_back(lead_marker);
  }
  lead_drawn_ = draw_lead;

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

  // リストで選んでいる 1 点。73 点あると番号のラベルだけでは地図上のどれか分からない。
  const int selected = waypoint_list_ ? waypoint_list_->currentRow() : -1;
  if (selected >= 0 && selected < static_cast<int>(waypoints_.size())) {
    visualization_msgs::msg::Marker highlight;
    highlight.header.frame_id = frame_id_;
    highlight.header.stamp = stamp;
    highlight.ns = "waypoint_selected";
    highlight.id = 0;
    highlight.type = visualization_msgs::msg::Marker::CYLINDER;
    highlight.action = visualization_msgs::msg::Marker::ADD;
    highlight.pose.position = waypoints_[selected].pose.position;
    highlight.pose.orientation.w = 1.0;
    highlight.scale.x = 0.9;
    highlight.scale.y = 0.9;
    highlight.scale.z = 0.02;
    highlight.color.r = 1.0F;
    highlight.color.g = 0.95F;
    highlight.color.b = 0.2F;
    highlight.color.a = 0.45F;
    markers.markers.push_back(highlight);
  } else {
    // 選択が外れたとき。DELETEALL を付けない出し直しでは明示的に消さないと残る。
    visualization_msgs::msg::Marker highlight;
    highlight.ns = "waypoint_selected";
    highlight.id = 0;
    highlight.action = visualization_msgs::msg::Marker::DELETE;
    markers.markers.push_back(highlight);
  }

  for (size_t i = 0; i < waypoints_.size(); ++i) {
    const auto & waypoint = waypoints_[i];
    const bool is_selected = static_cast<int>(i) == selected;
    visualization_msgs::msg::Marker arrow;
    arrow.header = waypoint.header;
    arrow.header.stamp = stamp;
    arrow.ns = "waypoint_arrow";
    arrow.id = static_cast<int>(i);
    arrow.type = visualization_msgs::msg::Marker::ARROW;
    arrow.action = visualization_msgs::msg::Marker::ADD;
    arrow.pose = waypoint.pose;
    arrow.scale.x = is_selected ? 0.75 : 0.55;
    arrow.scale.y = is_selected ? 0.18 : 0.12;
    arrow.scale.z = is_selected ? 0.18 : 0.12;
    arrow.color.r = is_selected ? 1.0F : 0.1F;
    arrow.color.g = is_selected ? 0.95F : 0.8F;
    arrow.color.b = is_selected ? 0.2F : 1.0F;
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

bool WaypointManagerPanel::leadPoint(geometry_msgs::msg::Point * point, QString * reason) const
{
  if (waypoints_.empty()) {
    *reason = QString();
    return false;
  }
  auto * context = getDisplayContext();
  auto * frame_manager = context ? context->getFrameManager() : nullptr;
  if (!frame_manager) {
    *reason = QString();
    return false;
  }
  // FrameManager が返すのは Fixed Frame から見た姿勢なので、waypoint と同じ座標系で
  // ないと線がずれる。
  const QString fixed_frame = context->getFixedFrame();
  if (fixed_frame.toStdString() != frame_id_) {
    *reason = QString("Fixed Frame (%1) differs from waypoint frame (%2) - no line from the robot")
      .arg(fixed_frame).arg(QString::fromStdString(frame_id_));
    return false;
  }

  Ogre::Vector3 position;
  Ogre::Quaternion orientation;
  // std::string に包む。const char[] のままだと Header を取るテンプレート側に持って
  // いかれてコンパイルが通らない。
  if (!frame_manager->getTransform(std::string(kRobotFrame), position, orientation) &&
    !frame_manager->getTransform(std::string(kRobotFrameFallback), position, orientation))
  {
    *reason = QString("No TF for %1 - no line from the robot").arg(kRobotFrame);
    return false;
  }
  point->x = position.x;
  point->y = position.y;
  point->z = position.z;
  *reason = QString();
  return true;
}

void WaypointManagerPanel::updateLead()
{
  if (!marker_publisher_ || waypoints_.empty()) {
    return;
  }
  geometry_msgs::msg::Point lead;
  QString reason;
  const bool draw_lead = !active_goal_ && !goal_pending_ && leadPoint(&lead, &reason);
  if (reason != lead_reason_) {
    lead_reason_ = reason;
    // 線が出ない理由は 1 度だけ出す。毎回書くとステータス行が編集の結果を上書きし続ける。
    if (!reason.isEmpty()) {
      setStatus(reason);
    }
  }
  if (draw_lead == lead_drawn_) {
    if (!draw_lead) {
      return;
    }
    const double moved = std::hypot(
      lead.x - lead_origin_.x, lead.y - lead_origin_.y, lead.z - lead_origin_.z);
    if (moved < kLeadMoveThreshold) {
      return;
    }
  }
  publishMarkers(false);
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
    setStatus("Error: /follow_waypoints action server is unavailable");
    return;
  }
  FollowWaypoints::Goal goal;
  goal.poses = waypoints_;
  for (auto & pose : goal.poses) {
    pose.header.frame_id = frame_id_;
    pose.header.stamp = node_->now();
  }
  QPointer<WaypointManagerPanel> panel(this);
  rclcpp_action::Client<FollowWaypoints>::SendGoalOptions options;
  options.goal_response_callback = [panel](GoalHandleFollowWaypoints::SharedPtr goal_handle) {
      if (!panel) {return;}
      QMetaObject::invokeMethod(panel, [panel, goal_handle]() {
        if (!panel) {return;}
        panel->goal_pending_ = false;
        if (!goal_handle) {
          panel->setStatus("Error: FollowWaypoints goal was rejected");
        } else {
          panel->active_goal_ = goal_handle;
          panel->setStatus("Following waypoints");
        }
        panel->updateButtons();
      }, Qt::QueuedConnection);
    };
  options.feedback_callback = [panel](GoalHandleFollowWaypoints::SharedPtr,
      const std::shared_ptr<const FollowWaypoints::Feedback> feedback) {
      if (!panel) {return;}
      const int current = static_cast<int>(feedback->current_waypoint);
      QMetaObject::invokeMethod(panel, [panel, current]() {
        if (!panel) {return;}
        panel->setStatus(
          QString("Following waypoint %1 / %2").arg(current + 1).arg(
            static_cast<int>(panel->waypoints_.size())));
      }, Qt::QueuedConnection);
    };
  options.result_callback = [panel](const GoalHandleFollowWaypoints::WrappedResult & result) {
    if (!panel) {return;}
      const int code = static_cast<int>(result.code);
      const int missed = result.result ? static_cast<int>(result.result->missed_waypoints.size()) : 0;
      QMetaObject::invokeMethod(panel, [panel, code, missed]() {
        if (panel) {panel->handleResult(code, missed);}
      }, Qt::QueuedConnection);
    };
  goal_pending_ = true;
  action_client_->async_send_goal(goal, options);
  setStatus("Sending FollowWaypoints goal...");
  updateButtons();
}

void WaypointManagerPanel::cancelFollowing()
{
  if (!active_goal_ || !action_client_) {
    setStatus("No active FollowWaypoints goal");
    return;
  }
  action_client_->async_cancel_goal(active_goal_);
  setStatus("Cancellation requested");
}

void WaypointManagerPanel::handleResult(int result_code, int missed_count)
{
  active_goal_.reset();
  goal_pending_ = false;
  QString status;
  if (result_code == static_cast<int>(rclcpp_action::ResultCode::SUCCEEDED)) {
    status = "Succeeded";
  } else if (result_code == static_cast<int>(rclcpp_action::ResultCode::CANCELED)) {
    status = "Canceled";
  } else if (result_code == static_cast<int>(rclcpp_action::ResultCode::ABORTED)) {
    status = "Failed (aborted)";
  } else {
    status = "Failed (unknown result)";
  }
  // 取りこぼしは結果コードによらず出す。stop_on_failure:=false
  // (config/nav2/behaviors.yaml) なら SUCCEEDED で返るが、true にすると同じ
  // missed_waypoints を積んだまま ABORTED で返るため、そこで数を落とさない。
  if (missed_count > 0) {
    status += QString(" - %1 waypoint(s) missed").arg(missed_count);
  }
  setStatus(status);
  updateButtons();
}

void WaypointManagerPanel::setStatus(const QString & status)
{
  status_label_->setText("Status: " + status);
}

void WaypointManagerPanel::updateButtons()
{
  start_button_->setEnabled(
    !waypoints_.empty() && !active_goal_ && !goal_pending_ && action_client_);
  cancel_button_->setEnabled(static_cast<bool>(active_goal_));
}

}  // namespace daifuku_waypoint_manager

PLUGINLIB_EXPORT_CLASS(daifuku_waypoint_manager::WaypointManagerPanel, rviz_common::Panel)
