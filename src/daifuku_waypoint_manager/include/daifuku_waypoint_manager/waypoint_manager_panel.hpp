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
#include <nav2_msgs/action/follow_waypoints.hpp>
#include <nav_msgs/msg/path.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp_action/rclcpp_action.hpp>
#include <rviz_common/panel.hpp>
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
  ~WaypointManagerPanel() override;

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
  void sendCancelBurst();
  void selectionChanged();
  void setStatus(const QString & status);
  void handleResult(int result_code, int missed_count);

private:
  // NavigateThroughPoses ではなく FollowWaypoints を使う。前者の BT は
  // ComputePathThroughPoses を要求するが、planner:=vi (daifuku_stack の既定) では
  // その action が存在せず、木がスタブ (常に失敗) に差し替えられているため。
  using FollowWaypoints = nav2_msgs::action::FollowWaypoints;
  using GoalHandleFollowWaypoints = rclcpp_action::ClientGoalHandle<FollowWaypoints>;
  using CancelGoalSrv = action_msgs::srv::CancelGoal;

  void buildUi();
  // 「巡回中」の表示をやめて Start を押せる状態へ戻す。ゴールも手放すので、
  // そのゴールのコールバックは以後届かない。
  void resetGoalState();
  QString resultText(int result_code, int missed_count) const;
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
  // これは他ノードが読むためのもの — vi_planner の先読み (waypoint_prefetch) が
  // 「いま向かっている点の次はどこか」をこれで知る。
  void publishWaypointPath();
  bool leadPoint(geometry_msgs::msg::Point * point, QString * reason) const;
  void updateLead();
  void updateButtons();
  void addClickedPoint(const geometry_msgs::msg::PointStamped & point);
  void addClickedPose(const geometry_msgs::msg::PoseStamped & pose);
  bool readYamlFile(const QString & filename, std::vector<geometry_msgs::msg::PoseStamped> * poses,
                    std::string * frame_id, QString * error) const;
  bool writeYamlFile(const QString & filename, QString * error) const;
  QString formatWaypoint(int index, const geometry_msgs::msg::PoseStamped & pose) const;

  std::string frame_id_{"map"};
  std::vector<geometry_msgs::msg::PoseStamped> waypoints_;
  rclcpp::Node::SharedPtr node_;
  rclcpp::Publisher<visualization_msgs::msg::MarkerArray>::SharedPtr marker_publisher_;
  rclcpp::Publisher<nav_msgs::msg::Path>::SharedPtr path_publisher_;
  rclcpp::Subscription<geometry_msgs::msg::PointStamped>::SharedPtr clicked_point_subscription_;
  rclcpp::Subscription<geometry_msgs::msg::PoseStamped>::SharedPtr clicked_pose_subscription_;
  rclcpp_action::Client<FollowWaypoints>::SharedPtr action_client_;
  // 取り消しは action クライアントではなくキャンセルサービスへ直接出す
  // (joy_teleop._cancel_goals と同じ)。理由は 2 つ: 空の goal_info が
  // 「そのサーバの全ゴール」を意味するのでパネルが送っていないゴールも止められる
  // ことと、FollowWaypoints 以外のサーバへも投げられること。
  std::vector<rclcpp::Client<CancelGoalSrv>::SharedPtr> cancel_clients_;
  std::vector<QString> cancel_client_names_;
  // **ここが最後の shared_ptr。** rclcpp_action のクライアントは goal handle を
  // weak_ptr で持つので、これを落とすとそのゴールの feedback も結果も来なくなる
  // (= resetGoalState はコールバックを止める操作でもある)。
  GoalHandleFollowWaypoints::SharedPtr active_goal_;
  // ゴールを送ってから goal_response_callback が返るまでの窓。この間 active_goal_ は
  // まだ空なので、waypoint の編集 (refreshList 経由の updateButtons) が Start を
  // 押せる状態に戻してしまい、二重にゴールを送れる。
  bool goal_pending_{false};
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
};

}  // namespace daifuku_waypoint_manager

#endif  // DAIFUKU_WAYPOINT_MANAGER__WAYPOINT_MANAGER_PANEL_HPP_
