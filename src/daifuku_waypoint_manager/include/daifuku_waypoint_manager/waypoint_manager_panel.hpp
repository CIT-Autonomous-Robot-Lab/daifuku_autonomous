#ifndef DAIFUKU_WAYPOINT_MANAGER__WAYPOINT_MANAGER_PANEL_HPP_
#define DAIFUKU_WAYPOINT_MANAGER__WAYPOINT_MANAGER_PANEL_HPP_

#include <cstdint>
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
  void watchActionServer();
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
  // 「巡回中」の表示をやめて Start を押せる状態へ戻す。goal_epoch_ を進めるので、
  // 諦めたゴールのコールバックが後から届いても「巡回中」には戻さない
  // (戻す道は readoptGoal だけ)。
  void resetGoalState();
  // 諦めたあとに feedback が届いた = 巡回はまだ続いている。ゴールを持ち直して
  // 「巡回中」の表示へ戻す。戻せたときだけ true。
  bool readoptGoal(std::uint64_t epoch, const GoalHandleFollowWaypoints::SharedPtr & goal_handle);
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
  GoalHandleFollowWaypoints::SharedPtr active_goal_;
  // resetGoalState で手放したゴール。**捨てずに持っておく** — rclcpp_action の
  // クライアントは goal handle を weak_ptr で持つので、最後の shared_ptr を落とすと
  // feedback も結果も二度と来なくなり、巡回が続いていてもパネルは知りようがなくなる。
  GoalHandleFollowWaypoints::SharedPtr abandoned_goal_;
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
  // action サーバが居ない状態が続いた tick 数 (lead_timer_ で数える)。
  int server_gone_ticks_{0};
  // ゴール 1 回ぶんの世代。諦めた (resetGoalState した) 後に届いたコールバックを
  // 捨てるために使う。捨てないと、戻したはずの表示が「巡回中」に戻る。
  std::uint64_t goal_epoch_{0};
  // 最後に送ったゴールの世代。諦めた後に届いた feedback がそれなのか、それとも
  // もっと新しい Start に追い越された古いものなのかを readoptGoal が見分ける。
  std::uint64_t latest_goal_epoch_{0};

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
