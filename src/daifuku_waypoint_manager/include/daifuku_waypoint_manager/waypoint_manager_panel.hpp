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

#ifndef DAIFUKU_WAYPOINT_MANAGER__WAYPOINT_MANAGER_PANEL_HPP_
#define DAIFUKU_WAYPOINT_MANAGER__WAYPOINT_MANAGER_PANEL_HPP_

#include <memory>
#include <string>
#include <vector>

#include <QString>
#include <QWidget>

#include <action_msgs/srv/cancel_goal.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <geometry_msgs/msg/point_stamped.hpp>
#include <nav_msgs/msg/path.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rviz_common/panel.hpp>
#include <std_msgs/msg/empty.hpp>
#include <std_msgs/msg/int32.hpp>
#include <std_msgs/msg/int32_multi_array.hpp>
#include <std_msgs/msg/string.hpp>
#include <visualization_msgs/msg/marker_array.hpp>

class QLabel;
class QListWidget;
class QPushButton;
class QTimer;

namespace daifuku_waypoint_manager
{

class WaypointManagerPanel : public rviz_common::Panel
{
  Q_OBJECT

public:
  explicit WaypointManagerPanel(QWidget * parent = nullptr);

  void onInitialize() override;

private Q_SLOTS:
  void deleteSelected();
  void deleteLast();
  void clearWaypoints();
  void moveUp();
  void moveDown();
  void saveYaml();
  void loadYaml();
  void startFollowing();
  void cancelFollowing();
  void toggleStop();
  void resumeFollowing();
  void sendCancelBurst();
  void selectionChanged();
  void setStatus(const QString & status);

private:
  using CancelGoalSrv = action_msgs::srv::CancelGoal;

  void buildUi();
  void refreshList();
  // reset=false のときは DELETEALL を付けない。同じ ns/id は ADD で上書きされるので、
  // タイマからの出し直し (updateLead) で全マーカが作り直されて瞬くのを避ける。
  // **その代わり、消えたマーカは append 側が明示的に DELETE を積む必要がある。**
  void publishMarkers(bool reset = true);
  // publishMarkers が積む 4 種類。lead だけは lead_drawn_ / lead_origin_ を更新
  // するので const ではない。
  void appendLeadMarker(
    visualization_msgs::msg::MarkerArray * markers, const rclcpp::Time & stamp, bool reset);
  void appendRouteMarker(
    visualization_msgs::msg::MarkerArray * markers, const rclcpp::Time & stamp) const;
  void appendSelectionMarker(
    visualization_msgs::msg::MarkerArray * markers, const rclcpp::Time & stamp,
    int selected) const;
  void appendWaypointMarkers(
    visualization_msgs::msg::MarkerArray * markers, const rclcpp::Time & stamp,
    int selected) const;
  // 巡回の順路そのものを nav_msgs/Path で latch する。マーカ (見せるため) と違い、
  // これは他ノードが読むためのもの — 機体側の waypoint_runner が区間を切り、
  // vi_planner の先読み (waypoint_prefetch) が「いま向かっている点の次」を知る。
  void publishWaypointPath();
  // 停止点の番号を latch する。順路と別に出すのは、停止点の切り替えでは順路が
  // 変わらず、waypoint_runner が停止中の位置を捨てずに済むようにするため。
  void publishStops();
  bool leadPoint(geometry_msgs::msg::Point * point, QString * reason) const;
  void updateLead();
  void updateButtons();
  void addClickedPoint(const geometry_msgs::msg::PointStamped & point);
  void addClickedPose(const geometry_msgs::msg::PoseStamped & pose);
  bool readYamlFile(const QString & filename, std::vector<geometry_msgs::msg::PoseStamped> * poses,
                    std::vector<bool> * stops, std::string * frame_id, QString * error) const;
  bool writeYamlFile(const QString & filename, QString * error) const;
  QString formatWaypoint(int index, const geometry_msgs::msg::PoseStamped & pose) const;

  std::string frame_id_{"map"};
  std::vector<geometry_msgs::msg::PoseStamped> waypoints_;
  // waypoints_ と同じ並び。true の点に着いたら巡回を止め、Resume を待つ。
  std::vector<bool> stop_flags_;
  // waypoint_runner の /waypoint/next。次に送る区間の先頭で、0 より大きいあいだは
  // 停止点で止まっている (Resume が押せる)。-1 は走行中。
  int robot_next_{0};
  rclcpp::Node::SharedPtr node_;
  rclcpp::Publisher<visualization_msgs::msg::MarkerArray>::SharedPtr marker_publisher_;
  rclcpp::Publisher<nav_msgs::msg::Path>::SharedPtr path_publisher_;
  rclcpp::Subscription<geometry_msgs::msg::PointStamped>::SharedPtr clicked_point_subscription_;
  rclcpp::Subscription<geometry_msgs::msg::PoseStamped>::SharedPtr clicked_pose_subscription_;
  rclcpp::Publisher<std_msgs::msg::Int32MultiArray>::SharedPtr stops_publisher_;
  rclcpp::Publisher<std_msgs::msg::Empty>::SharedPtr start_publisher_;
  rclcpp::Publisher<std_msgs::msg::Empty>::SharedPtr resume_publisher_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr status_subscription_;
  rclcpp::Subscription<std_msgs::msg::Int32>::SharedPtr next_subscription_;
  // 取り消しは action クライアントではなくキャンセルサービスへ直接出す。
  // 理由は 2 つ: 空の goal_info が
  // 「そのサーバの全ゴール」を意味するのでパネルが送っていないゴールも止められる
  // ことと、FollowWaypoints 以外のサーバへも投げられること。
  std::vector<rclcpp::Client<CancelGoalSrv>::SharedPtr> cancel_clients_;
  std::vector<QString> cancel_client_names_;
  // 残りの取り消し回数。1 発では無線で落ちたときに何も起きないので、繰り返す。
  int cancel_bursts_{0};
  // その取り消しで 1 度でもサービスへ出せたか。全部 not ready のまま終わったら、
  // 「押したのに何も起きない」ではなくサーバが居ないことを出す。
  bool cancel_sent_any_{false};
  QTimer * cancel_timer_{nullptr};

  // 機体から 1 点目へ引く線。この区間だけ機体に追随して動くので、編集のたびに出す
  // publishMarkers() とは別にタイマで出し直す。lead_drawn_ / lead_origin_ は前回どこに
  // 引いたかで、動いていなければ出し直さない (73 点あると全マーカの再送になるため)。
  QTimer * lead_timer_{nullptr};
  bool lead_drawn_{false};
  geometry_msgs::msg::Point lead_origin_;
  QString lead_reason_;

  // refreshList() の clear() / setCurrentRow() でも currentRowChanged が飛ぶので、その間は
  // selectionChanged() からの出し直しを止める (呼び出し元が直後に publishMarkers() する)。
  bool suppress_selection_publish_{false};

  QListWidget * waypoint_list_{nullptr};
  QLabel * status_label_{nullptr};
  QPushButton * start_button_{nullptr};
  QPushButton * cancel_button_{nullptr};
  QPushButton * resume_button_{nullptr};
};

}  // namespace daifuku_waypoint_manager

#endif  // DAIFUKU_WAYPOINT_MANAGER__WAYPOINT_MANAGER_PANEL_HPP_
