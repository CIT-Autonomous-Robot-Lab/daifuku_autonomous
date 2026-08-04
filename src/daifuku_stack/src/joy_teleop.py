#!/usr/bin/env python3

"""ゲームパッドで手動走行と自律走行を切り替える。

robot_bringup.launch.py が joy:=true (既定) のとき joy_node と一緒に立てる。
想定しているのは XInput 互換のゲームパッド。
上流の teleop_twist_joy を使わないのは、長押しと同時押しでモードを切り替える
必要があるためで、あちらは押している間だけ速度を出す (デッドマン) しかできない。

  * STARTを3秒            … teleop の入/切をトグルする
  * START+BACKを同時に3秒  … teleop を切り、保存したウェイポイントの巡回を始める
  * RB を押している間      … ブースト (上限が linear_max_speed から boost へ上がる)

出す先は **/cmd_vel_teleop** (twist_mux の優先度 100 側)。/cmd_vel は自律側の
出力なので、そちらへ出すと自律走行中に取り合いになる。

## teleop 中は「出しっぱなし」にしてある

twist_mux の優先度は非常停止ではなく、勝つのは publish しているあいだ +
timeout (0.5 s) だけである。したがって teleop 入のあいだはスティックが中立でも
ゼロ速度を publish し続ける。そうしないとスティックから手を離して 0.5 秒後に
自律側の /cmd_vel が通り、機体が勝手に走り出す。

同じ理由で、teleop に入るときは走行中のゴールを**取り消す**。優先度で押さえて
いるだけでは、teleop を切った瞬間に元のゴールが再開する。

teleop を切るとき (と巡回を始めるとき) は、ゼロを stop_tail 秒だけ出してから
黙る。黙るだけだと本体ドライバは最後に受けた速度を保持し続ける
(cmd_vel_timeout の既定は 60 秒) ので、最後に届いた指令をゼロにしておく。

## 受信が途切れたら止める

無線のゲームパッドは、受信機を抜かれても電池が切れても /joy が来なくなるだけで
エラーは出ない。joy_timeout 秒来なければゼロを出し、長押しの計測も捨てる
(押しっぱなしのまま切れたのを 3 秒後の切り替えとして拾わないため)。

ボタンと軸の番号は **XInput (X モード)** のときのもの。DirectInput のパッドや、
モード切り替えを持つ機種を D 側にしたものは総入れ替えになるので、パラメータで
直すこと (`ros2 topic echo /joy` で押しながら確かめる)。左スティックと十字キーを
入れ替えるモードを持つ機種もある。
"""

import os
import sys
import time

from ament_index_python.packages import get_package_share_directory

from action_msgs.srv import CancelGoal

from geometry_msgs.msg import PoseStamped
from geometry_msgs.msg import Twist

from nav2_msgs.action import FollowWaypoints

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import QoSProfile

from sensor_msgs.msg import Joy

from std_msgs.msg import Bool

import yaml

# joy_buttons.py は同じ lib/daifuku_stack に入る (CMakeLists.txt の install(PROGRAMS))。
# share/ 側の src/ から直接叩かれることもあるので、両方で通るように自分の隣を見る。
_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from joy_buttons import COMBO, HoldLatch, TOGGLE, axis, axis_to_speed, pressed  # noqa: E402


def load_waypoints(path):
    """ウェイポイントの YAML を PoseStamped の並びに読む。

    書式は daifuku_waypoint_manager パネルが保存するものと同じ
    (frame_id + waypoints[].position/orientation)。パネルは RViz プラグインで
    実機には載らないので、ここでは同じ書式を読むだけの実装を持っている。

    Raises:
        ValueError: 書式が違うとき。1 点でも欠けていれば読み込みごと失敗させる
            (黙って飛ばすと、経路の途中が抜けた巡回が静かに走ってしまう)。
    """
    with open(path) as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise ValueError("top level is not a mapping")

    frame_id = document.get("frame_id", "map")
    entries = document.get("waypoints")
    if not entries:
        raise ValueError("no waypoints")

    poses = []
    for index, entry in enumerate(entries):
        try:
            position = entry["position"]
            orientation = entry["orientation"]
            pose = PoseStamped()
            pose.header.frame_id = frame_id
            pose.pose.position.x = float(position["x"])
            pose.pose.position.y = float(position["y"])
            pose.pose.position.z = float(position.get("z", 0.0))
            pose.pose.orientation.x = float(orientation["x"])
            pose.pose.orientation.y = float(orientation["y"])
            pose.pose.orientation.z = float(orientation["z"])
            pose.pose.orientation.w = float(orientation["w"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("waypoint %d: %s" % (index, exc))
        poses.append(pose)
    return frame_id, poses


class JoyTeleop(Node):

    def __init__(self):
        super().__init__("joy_teleop")

        self.declare_parameter("axis_linear", 1)
        self.declare_parameter("axis_angular", 0)
        self.declare_parameter("deadzone", 0.15)
        self.declare_parameter("linear_min_speed", 0.15)
        self.declare_parameter("linear_max_speed", 0.35)
        self.declare_parameter("linear_boost_speed", 0.5)
        self.declare_parameter("angular_min_speed", 0.3)
        self.declare_parameter("angular_max_speed", 1.0)
        self.declare_parameter("angular_boost_speed", 1.5)
        self.declare_parameter("button_toggle", 7)
        self.declare_parameter("button_waypoints", 6)
        self.declare_parameter("button_boost", 5)
        self.declare_parameter("hold_seconds", 3.0)
        self.declare_parameter("publish_rate", 20.0)
        self.declare_parameter("joy_timeout", 0.5)
        self.declare_parameter("stop_tail", 1.0)
        self.declare_parameter("cancel_window", 2.0)
        self.declare_parameter("start_enabled", False)
        self.declare_parameter("waypoints_file", "")
        self.declare_parameter("follow_waypoints_action", "follow_waypoints")
        self.declare_parameter("navigate_to_pose_action", "navigate_to_pose")

        def value(name):
            return self.get_parameter(name).value

        self._axis_linear = int(value("axis_linear"))
        self._axis_angular = int(value("axis_angular"))
        self._deadzone = float(value("deadzone"))
        self._linear_min = float(value("linear_min_speed"))
        self._linear_max = float(value("linear_max_speed"))
        self._linear_boost = float(value("linear_boost_speed"))
        self._angular_min = float(value("angular_min_speed"))
        self._angular_max = float(value("angular_max_speed"))
        self._angular_boost = float(value("angular_boost_speed"))
        self._button_toggle = int(value("button_toggle"))
        self._button_waypoints = int(value("button_waypoints"))
        self._button_boost = int(value("button_boost"))
        self._joy_timeout = float(value("joy_timeout"))
        self._stop_tail = float(value("stop_tail"))
        self._cancel_window = float(value("cancel_window"))

        self._waypoints_file = value("waypoints_file") or os.path.join(
            get_package_share_directory("daifuku_stack"),
            "waypoints",
            "waypoints_tsudanuma.yaml",
        )

        self._latch = HoldLatch(float(value("hold_seconds")))
        self._enabled = bool(value("start_enabled"))
        self._axes = []
        self._buttons = []
        self._joy_at = None
        self._stop_until = 0.0
        self._cancel_until = 0.0
        self._cancel_at = 0.0
        self._state_at = 0.0
        self._warned_stale = False
        self._goal_handle = None
        self._goal_pending = False

        self._cmd_pub = self.create_publisher(Twist, "cmd_vel_teleop", 10)
        # 状態は遅れて繋いだ購読者にも見せたい (「なぜ動かない」を追うため)。
        self._state_pub = self.create_publisher(
            Bool, "~/enabled",
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )
        self.create_subscription(Joy, "joy", self._on_joy, 10)

        self._follow_action = value("follow_waypoints_action")
        follow_action = self._follow_action
        self._follow_client = ActionClient(self, FollowWaypoints, follow_action)
        # 取り消しはアクションクライアント経由ではなくキャンセルサービスへ直接出す。
        # 空の goal_info は「そのサーバの全ゴール」を意味するので、自分が投げた
        # ゴールだけでなく RViz のパネルや Nav2 Goal から走り出したものも止まる。
        self._cancel_clients = [
            self.create_client(CancelGoal, "%s/_action/cancel_goal" % name)
            for name in (follow_action, value("navigate_to_pose_action"))
        ]

        # 起動時に一度読んでおく。走らせようとした瞬間に「書式が違う」と分かるより、
        # 立ち上げのログで分かるほうがよい (読むのは押されたときに読み直す)。
        try:
            _, poses = load_waypoints(self._waypoints_file)
            self.get_logger().info(
                "waypoints: %d points from %s" % (len(poses), self._waypoints_file)
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            self.get_logger().warning(
                "waypoints file unusable (%s): %s" % (self._waypoints_file, exc)
            )

        period = 1.0 / max(float(value("publish_rate")), 1.0)
        self.create_timer(period, self._tick)
        self._publish_state()
        self.get_logger().info(
            "joy teleop %s; hold START %.0fs to toggle, START+BACK %.0fs to follow waypoints"
            % ("on" if self._enabled else "off", self._latch.hold_seconds,
               self._latch.hold_seconds)
        )

    # ── 入力 ────────────────────────────────────────────────────────────

    def _on_joy(self, msg):
        self._axes = list(msg.axes)
        self._buttons = list(msg.buttons)
        # ヘッダのスタンプではなく受信時刻を使う。teleop で見たいのは「指令が
        # 途切れていないか」であって送信元の時刻ではないし、単調時計なら NTP の
        # 時刻合わせで判定が飛ばない。
        self._joy_at = time.monotonic()
        self._warned_stale = False

    # ── 周期処理 ────────────────────────────────────────────────────────

    def _tick(self):
        now = time.monotonic()
        fresh = self._joy_at is not None and (now - self._joy_at) <= self._joy_timeout

        if fresh:
            action = self._latch.update(
                now,
                pressed(self._buttons, self._button_toggle),
                pressed(self._buttons, self._button_waypoints),
            )
            if action == COMBO:
                self._start_waypoints()
            elif action == TOGGLE:
                self._set_enabled(not self._enabled)
        else:
            self._latch.reset()
            if self._enabled and not self._warned_stale:
                self._warned_stale = True
                self.get_logger().warning("no joy for %.1f s; holding still"
                                          % self._joy_timeout)

        # 取り消しは 1 回では足りない。waypoint_follower は stop_on_failure: false
        # なので、navigate_to_pose 側が先に取り消されると「1 点失敗した」と見なして
        # **次の点へ新しいゴールを出す**。こちらの取り消しはもう飛んだあとなので、
        # それは残る。そこでしばらく出し続ける (取り消しは何度出しても副作用が無い)。
        if now < self._cancel_until and now >= self._cancel_at:
            self._cancel_at = now + 0.25
            self._cancel_goals()

        # 状態は変化時だけでなく定期的にも出す。TRANSIENT_LOCAL は購読側も
        # transient_local で繋がないと過去の 1 件が届かず、素の
        # `ros2 topic echo` (volatile) では次の切り替えまで何も出ないため。
        if now - self._state_at >= 1.0:
            self._publish_state()

        if self._enabled:
            self._cmd_pub.publish(self._twist() if fresh else Twist())
        elif now < self._stop_until:
            self._cmd_pub.publish(Twist())

    def _twist(self):
        boost = pressed(self._buttons, self._button_boost)
        linear_max = self._linear_boost if boost else self._linear_max
        angular_max = self._angular_boost if boost else self._angular_max

        twist = Twist()
        twist.linear.x = axis_to_speed(
            axis(self._axes, self._axis_linear),
            self._deadzone, self._linear_min, linear_max,
        )
        twist.angular.z = axis_to_speed(
            axis(self._axes, self._axis_angular),
            self._deadzone, self._angular_min, angular_max,
        )
        return twist

    # ── モード ──────────────────────────────────────────────────────────

    def _set_enabled(self, enabled):
        self._enabled = enabled
        now = time.monotonic()
        if enabled:
            # 優先度で押さえるだけでは、teleop を切った瞬間に元のゴールが再開する。
            # 実際に取り消すのは _tick で、cancel_window のあいだ繰り返す。
            self._cancel_until = now + self._cancel_window
            self._cancel_at = 0.0
            self._cmd_pub.publish(Twist())
        else:
            self._cancel_until = 0.0
            self._stop_until = now + self._stop_tail
        self._publish_state()
        self.get_logger().info("teleop %s" % ("on" if enabled else "off"))

    def _publish_state(self):
        message = Bool()
        message.data = self._enabled
        self._state_pub.publish(message)
        self._state_at = time.monotonic()

    def _cancel_goals(self):
        for client in self._cancel_clients:
            if not client.service_is_ready():
                continue
            # 空の goal_info = そのサーバの全ゴール。
            client.call_async(CancelGoal.Request())
        self._goal_handle = None
        self._goal_pending = False

    # ── ウェイポイント巡回 ──────────────────────────────────────────────

    def _start_waypoints(self):
        if self._goal_pending or self._goal_handle is not None:
            self.get_logger().warning(
                "waypoints already running; hold START to take over first"
            )
            return

        # 取り消しを出しているあいだに投げると、あとから届いた取り消しが今から
        # 出すゴールを巻き込む。hold_seconds > cancel_window なら普通は来ない。
        if time.monotonic() < self._cancel_until:
            self.get_logger().warning("still cancelling the previous goal; try again")
            return

        try:
            frame_id, poses = load_waypoints(self._waypoints_file)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            self.get_logger().error(
                "cannot read %s: %s" % (self._waypoints_file, exc)
            )
            return

        if not self._follow_client.server_is_ready():
            # navigation.launch.py が立っていないか、lifecycle がまだ activate
            # されていない。ここで黙ると「押したのに何も起きない」になる。
            self.get_logger().error(
                "%s action server is not available; is navigation.launch.py up?"
                % self._follow_action
            )
            return

        self._set_enabled(False)

        stamp = self.get_clock().now().to_msg()
        for pose in poses:
            pose.header.stamp = stamp

        goal = FollowWaypoints.Goal()
        goal.poses = poses
        self._goal_pending = True
        future = self._follow_client.send_goal_async(goal)
        future.add_done_callback(self._on_goal_response)
        self.get_logger().info(
            "following %d waypoints in %s" % (len(poses), frame_id)
        )

    def _on_goal_response(self, future):
        self._goal_pending = False
        handle = future.result()
        if not handle.accepted:
            self.get_logger().error("waypoint goal rejected")
            return
        self._goal_handle = handle
        handle.get_result_async().add_done_callback(self._on_goal_result)

    def _on_goal_result(self, future):
        self._goal_handle = None
        result = future.result()
        missed = list(getattr(result.result, "missed_waypoints", []))
        self.get_logger().info(
            "waypoints finished (status %d, missed %d)" % (result.status, len(missed))
        )

    def stop(self):
        """終了時に最後の指令をゼロにしておく。

        ドライバは最後に受けた速度を cmd_vel_timeout (既定 60 秒) のあいだ保持
        するので、走っている状態でノードだけ落ちると走り続ける。
        """
        try:
            self._cmd_pub.publish(Twist())
        except Exception:  # noqa: BLE001 - 落ちる途中なので何が来ても握る
            pass


def main():
    rclpy.init()
    node = JoyTeleop()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
