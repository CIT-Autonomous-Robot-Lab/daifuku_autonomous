#!/usr/bin/env python3

"""ゲームパッドで手動走行と自律走行を切り替える。

robot_bringup.launch.py が joy:=true (既定) のとき joy_node と一緒に立てる。
想定しているのは XInput 互換のゲームパッド。
上流の teleop_twist_joy を使わないのは、長押しと同時押しでモードを切り替える
必要があるためで、あちらは押している間だけ速度を出す (デッドマン) しかできない。

  * STARTを長押し          … teleop の入/切をトグルする (ピロリ↑ / ピロリ↓)
  * BACKを長押しして離す    … モータ電源の入/切をトグルする (ピロリピロリ↑ / ↓)
  * START+BACKを同時に長押し … teleop を切り、保存したウェイポイントの巡回を始める (ピピピ)
  * RB を押している間      … ブースト (上限が linear_max_speed から boost へ上がる)

長押しの時間はどれも hold_seconds (既定 2 秒)。BACK 単独だけ**離した時点**で決まる
のは joy_buttons.HoldLatch の docstring のとおり。

出す先は **/cmd_vel_teleop** (twist_mux の優先度 100 側)。/cmd_vel は自律側の
出力なので、そちらへ出すと自律走行中に取り合いになる。

## 指令は刻んで出す (加減速)

スティックの値をそのまま速度にはしない。**本体ドライバは受けた速度をそのまま
ステップ周波数にする**ので (raspicat_driver の `_drive`。公式実装も同じ)、段差の
ある指令はそのまま段差のある周波数になり、ステッパは自起動域を超えたところで
脱調する — 指令より遅く、左右バラバラに回るのに**エラーは何も出ない**。そこで
linear_accel / linear_decel (と angular 側) で変化率を抑える。詳しくは
joy_buttons.RateLimiter。

刻むのは**このノードが出す区間だけ**である。teleop に入った時点で自律側が
走らせていても相手の速度は分からないので、そこは段差のまま (従来どおりゼロを
出して奪う)。自律側の加減速は velocity_smoother の担当。

## モータ電源は「切る」ほうが本命

twist_mux の優先度は非常停止ではない。実際に止めるのはモータ電源で、これまでは
`control.sh motor off` (= /motor_power サービス) しか手が無かった。BACK 単独の
長押しは**その手をパッドに出したもの**である。切れば twist_mux も自律側も関係なく
車輪が止まる。

いま入っているかどうかは**こちらでは分からない** (ドライバは状態を出さない) ので、
自分が投げた要求だけを数えている。起点は motor_power_start_state で、ドライバの
initial_motor_power と合わせておくこと。`control.sh motor` など外から変えられると
1 回ぶんずれる (次の長押しが空振りして、その次で揃う)。

## モードは音でも伝える

長押しは hold_seconds (既定 2 秒) 経つまで何も起きず、切り替わった先はスティックを倒すまで見分けが
付かない。手元にノート PC が無ければログも見えないので、切り替わった時点で
**/buzzer** (`std_msgs/Int16`、値は Hz・0 で停止) へ短い旋律を出す。旋律そのものは
joy_buttons.py の TUNE_* にある。押しても効かなかったとき (巡回中にもう一度
押した、navigation.launch.py が立っていない、YAML が読めない) にも鳴らすのは、
そこが**今まで完全に無音だった**ためで、「長押ししたのに何も起きない」が一番
分かりにくい。

購読しているのは本体ドライバで、自前実装 (driver:=original) も公式実装
(既定の driver:=raspimouse) も同じトピック・同じ型で受ける。**鳴らなくても
走行には何の影響もない** — ドライバが activate されていない、`use_buzzer:=false`、
ブザーのピンを掴めなかった、のどれでもエラーは出ずにただ無音になる。

ブザーには cmd_vel のような timeout が無く、ドライバは最後に受けた周波数を
**鳴らし続ける**。旋律の途中でこのノードが落ちると鳴りっぱなしになるので、
stop() で 0 を出しておく。

## teleop 中は「出しっぱなし」にしてある

twist_mux の優先度は非常停止ではなく、勝つのは publish しているあいだ +
timeout (0.5 s) だけである。したがって teleop 入のあいだはスティックが中立でも
ゼロ速度を publish し続ける。そうしないとスティックから手を離して 0.5 秒後に
自律側の /cmd_vel が通り、機体が勝手に走り出す。

同じ理由で、teleop に入るときは走行中のゴールを**取り消す**。優先度で押さえて
いるだけでは、teleop を切った瞬間に元のゴールが再開する。

teleop を切るとき (と巡回を始めるとき) は、減速で降りきってから、さらに
stop_tail 秒ゼロを出してから黙る。黙るだけだと本体ドライバは最後に受けた速度を
保持し続けるためで、最後に届いた指令をゼロにしておく。自前実装
(driver:=original) は cmd_vel_timeout の 60 秒で止まるが、公式実装 (既定の
driver:=raspimouse) はこのキーを持たず、いつ止まるかは**未確認**である。
**降りきるまでを stop_tail 任せにしていない**のは、減速に stop_tail より時間の
かかる設定にされたときに、途中の速度で黙って走り続けてしまうため。

## 受信が途切れたら止める

無線のゲームパッドは、受信機を抜かれても電池が切れても /joy が来なくなるだけで
エラーは出ない。joy_timeout 秒来なければゼロへ**減速し** (段差で落とすと脱調して
惰性で滑るので、そのほうが速く止まる)、長押しの計測も捨てる (押しっぱなしのまま
切れたのを長押しの成立として拾わないため)。即断したいときはモータ電源のほう。

ボタンと軸の番号は **XInput (X モード)** のときのもの。DirectInput のパッドや、
モード切り替えを持つ機種を D 側にしたものは総入れ替えになるので、パラメータで
直すこと (`ros2 topic echo /joy` で押しながら確かめる)。左スティックと十字キーを
入れ替えるモードを持つ機種もある。
"""

import math
import os
import sys
import time

from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal

from geometry_msgs.msg import PoseStamped
from geometry_msgs.msg import Twist

from nav2_msgs.action import FollowWaypoints

from nav_msgs.msg import Path

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import QoSProfile

from sensor_msgs.msg import Joy

from std_msgs.msg import Bool
from std_msgs.msg import Int16

from std_srvs.srv import SetBool

import yaml

# joy_buttons.py は同じ lib/daifuku_stack に入る (CMakeLists.txt の install(PROGRAMS))。
# share/ 側の src/ から直接叩かれることもあるので、両方で通るように自分の隣を見る。
_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from joy_buttons import (  # noqa: E402
    COMBO, HoldLatch, MOTOR, RateLimiter, TOGGLE, TUNE_FINISHED, TUNE_MOTOR_OFF,
    TUNE_MOTOR_ON, TUNE_REFUSED, TUNE_TELEOP_OFF, TUNE_TELEOP_ON, TUNE_WAYPOINTS,
    TunePlayer, axis, axis_to_speed, pressed,
)

# 旋律の音の変わり目を拾う周期 [s]。一番短い音 (60 ms) の 1/6 で、publish するのは
# 変わり目だけなのでトピックには乗らない。_tick と分けてあるのは、あちらが
# publish_rate (パラメータ) 次第で 50 ms 粒度になり、音長がそれに引きずられるため。
TUNE_PERIOD = 0.01


def load_waypoints(path):
    """ウェイポイントの YAML を PoseStamped の並びに読む。

    書式は daifuku_waypoint_manager パネルが保存するものと同じ
    (frame_id + waypoints[].position/orientation)。パネルは RViz プラグインで
    実機には載らないので、ここでは同じ書式を読むだけの実装を持っている。

    **受け入れる書式はパネルの readYamlFile (waypoint_manager_panel.cpp) と
    そろえてある。** 片方だけが通す形にすると、手で書いた順路が「実機では走るのに
    パネルでは開けない」(あるいはその逆) になる。決まりは 3 つ:

      * frame_id は必須。既定を持たせると、書き忘れた順路が黙って map 上の
        座標として走る (座標系を取り違えると全点が地図の外に出る)
      * position.z は省略可 (0.0)。接地して走る機体なので、無くても意味が決まる
      * 有限でない値と、長さが 0 のクォータニオンは弾く。NaN のまま
        FollowWaypoints へ投げると Nav2 の側で黙って落ちる

    Raises:
        ValueError: 書式が違うとき。1 点でも欠けていれば読み込みごと失敗させる
            (黙って飛ばすと、経路の途中が抜けた巡回が静かに走ってしまう)。
    """
    # encoding を明示する。同梱の waypoints_tsudanuma.yaml は冒頭に日本語の注記を
    # 持っていて、ロケールが C の環境 (実機のコンテナは LANG を持たない) では
    # 既定の encoding が ASCII になり、**読み込みごと失敗する**。
    # daifuku_stack_launch/params.py が同じ理由で明示しているのと同じ。
    with open(path, encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise ValueError("top level is not a mapping")

    frame_id = document.get("frame_id")
    if not frame_id or not isinstance(frame_id, str):
        raise ValueError("missing or invalid frame_id")
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
        _validate_pose(index, pose)
        poses.append(pose)
    return frame_id, poses


def _validate_pose(index, pose):
    """有限でない座標と、長さが 0 のクォータニオンを弾く (パネルと同じ判定)。"""
    position = pose.pose.position
    quaternion = pose.pose.orientation
    norm_squared = (
        quaternion.x ** 2 + quaternion.y ** 2 + quaternion.z ** 2 + quaternion.w ** 2
    )
    if not all(math.isfinite(v) for v in (position.x, position.y, position.z)):
        raise ValueError("waypoint %d: position is not finite" % index)
    if not math.isfinite(norm_squared) or norm_squared < 1e-12:
        raise ValueError(
            "waypoint %d: orientation is not a usable quaternion" % index
        )


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
        self.declare_parameter("linear_accel", 0.9)
        self.declare_parameter("linear_decel", 1.8)
        self.declare_parameter("angular_accel", 6.0)
        self.declare_parameter("angular_decel", 12.0)
        self.declare_parameter("button_toggle", 7)
        self.declare_parameter("button_waypoints", 6)
        self.declare_parameter("button_boost", 5)
        self.declare_parameter("hold_seconds", 2.0)
        self.declare_parameter("publish_rate", 20.0)
        self.declare_parameter("joy_timeout", 0.5)
        self.declare_parameter("stop_tail", 1.0)
        self.declare_parameter("cancel_window", 2.0)
        self.declare_parameter("start_enabled", False)
        self.declare_parameter("buzzer", True)
        self.declare_parameter("motor_power_service", "motor_power")
        self.declare_parameter("motor_power_start_state", False)
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
        # 刻むのは目標のほうではなく publish する値。目標 (axis_to_speed の出力) は
        # 不感帯の外側で min_speed に飛ぶ段差を持つので、そこも一緒に均される。
        self._linear_ramp = RateLimiter(value("linear_accel"), value("linear_decel"))
        self._angular_ramp = RateLimiter(value("angular_accel"), value("angular_decel"))
        self._button_toggle = int(value("button_toggle"))
        self._button_waypoints = int(value("button_waypoints"))
        self._button_boost = int(value("button_boost"))
        self._joy_timeout = float(value("joy_timeout"))
        self._stop_tail = float(value("stop_tail"))
        self._cancel_window = float(value("cancel_window"))

        # 既定値は持たせない。順路は地図と対でしか意味を持たないので、既定の 1 つを
        # 忍ばせると別の地図で立てたときに黙って噛み合わないものを走らせてしまう
        # (全点が地図の外に出ても plan が失敗するだけで、外からは recovery の
        # spin が延々回っているようにしか見えない)。空なら START+BACK は断る。
        self._waypoints_file = value("waypoints_file")

        self._latch = HoldLatch(float(value("hold_seconds")))
        self._enabled = bool(value("start_enabled"))
        self._axes = []
        self._buttons = []
        self._joy_at = None
        self._tick_at = None
        self._stop_until = 0.0
        self._cancel_until = 0.0
        self._cancel_at = 0.0
        self._state_at = 0.0
        self._warned_stale = False
        self._goal_handle = None
        self._goal_pending = False
        self._tune = TunePlayer()
        # ドライバは電源の状態を出さないので、自分が投げた要求だけを数える。
        self._motor_power = bool(value("motor_power_start_state"))
        self._motor_pending = False

        self._cmd_pub = self.create_publisher(Twist, "cmd_vel_teleop", 10)
        # 状態は遅れて繋いだ購読者にも見せたい (「なぜ動かない」を追うため)。
        self._state_pub = self.create_publisher(
            Bool, "~/enabled",
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )
        # 順路そのもの。vi_planner の先読み (waypoint_prefetch) が「いま向かって
        # いる点の次はどこか」をこれで知る。RViz のパネル
        # (daifuku_waypoint_manager) も同じトピックへ同じものを出すが、**実機の
        # イメージにパネルは入っていない** ので、ここから始めた巡回では
        # こちらが唯一の出どころになる。latch するのは vi_planner が後から
        # 上がることがあるため。
        self._path_pub = self.create_publisher(
            Path, "waypoints",
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )
        self.create_subscription(Joy, "joy", self._on_joy, 10)

        # ブザーは本体ドライバが購読する。買い手が居なくても publish は通るので、
        # 立っていない・ピンを掴めていないときは黙るだけになる。
        self._buzzer_pub = None
        if bool(value("buzzer")):
            self._buzzer_pub = self.create_publisher(Int16, "buzzer", 10)
            self.create_timer(TUNE_PERIOD, self._tune_tick)

        self._motor_service = value("motor_power_service")
        self._motor_client = self.create_client(SetBool, self._motor_service)

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
        if not self._waypoints_file:
            self.get_logger().warning(
                "waypoints_file is not set; START+BACK will refuse to start a patrol"
            )
        else:
            try:
                frame_id, poses = load_waypoints(self._waypoints_file)
                self.get_logger().info(
                    "waypoints: %d points from %s" % (len(poses), self._waypoints_file)
                )
                self._publish_waypoints(frame_id, poses)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                self.get_logger().warning(
                    "waypoints file unusable (%s): %s" % (self._waypoints_file, exc)
                )

        period = 1.0 / max(float(value("publish_rate")), 1.0)
        # 加減速に使う経過時間の上限。タイマが詰まって数秒飛んだとき、その分を
        # まるごと積むと 1 回で最高速まで飛ぶ (刻んだ意味が無くなる)。
        self._max_dt = 5.0 * period
        self.create_timer(period, self._tick)
        self._publish_state()
        self.get_logger().info(
            "joy teleop %s; hold %.1fs: START toggles teleop, BACK (on release) toggles "
            "motor power, START+BACK %s"
            % (
                "on" if self._enabled else "off",
                self._latch.hold_seconds,
                "follows waypoints" if self._waypoints_file
                else "is disabled (no waypoints_file)",
            )
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
        dt = self._elapsed(now)
        fresh = self._joy_at is not None and (now - self._joy_at) <= self._joy_timeout

        if fresh:
            action = self._latch.update(
                now,
                pressed(self._buttons, self._button_toggle),
                pressed(self._buttons, self._button_waypoints),
            )
            if action == COMBO:
                # 音は _start_waypoints が選ぶ (始まったか、断ったか)。
                self._start_waypoints()
            elif action == MOTOR:
                self._toggle_motor_power()
            elif action == TOGGLE:
                self._set_enabled(not self._enabled)
                self._play(TUNE_TELEOP_ON if self._enabled else TUNE_TELEOP_OFF)
        else:
            self._latch.reset()
            if self._enabled and not self._warned_stale:
                self._warned_stale = True
                self.get_logger().warning("no joy for %.1f s; slowing to a stop"
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

        # teleop を切ったあとは stop_tail を待たずに、**刻みがゼロに着くまで**
        # 出し続ける。stop_tail (1 秒) より減速に時間がかかる設定にされると、
        # 途中で黙った速度をドライバが保持したまま走り続けるため。
        if self._enabled:
            self._cmd_pub.publish(self._twist(dt) if fresh else self._coast(dt))
        elif now < self._stop_until or not self._at_rest():
            self._cmd_pub.publish(self._coast(dt))

    def _elapsed(self, now):
        """前回の _tick からの経過 [s]。上限は max_dt、初回は 0。

        タイマの公称周期ではなく実時間で刻む。周期がゆらいでも同じ入力なら
        同じ時間で最高速に着く (刻みが落ちた分だけ立ち上がりが伸びない)。
        """
        previous = self._tick_at
        self._tick_at = now
        if previous is None:
            return 0.0
        return min(now - previous, self._max_dt)

    def _twist(self, dt):
        boost = pressed(self._buttons, self._button_boost)
        linear_max = self._linear_boost if boost else self._linear_max
        angular_max = self._angular_boost if boost else self._angular_max

        twist = Twist()
        twist.linear.x = self._linear_ramp.update(
            axis_to_speed(
                axis(self._axes, self._axis_linear),
                self._deadzone, self._linear_min, linear_max,
            ),
            dt,
        )
        twist.angular.z = self._angular_ramp.update(
            axis_to_speed(
                axis(self._axes, self._axis_angular),
                self._deadzone, self._angular_min, angular_max,
            ),
            dt,
        )
        return twist

    def _coast(self, dt):
        """目標をゼロにして刻む。受信断のときと teleop を切ったあとに出す。

        受信断でも段差で 0 に落とさないのは、ドライバがそれをそのままステップ
        周波数の段差にするためで、**脱調して惰性で滑るより decel で止めたほうが
        速く止まる**。止まるまでは linear_decel で決まる (0.8 m/s なら 0.44 秒・
        0.18 m)。本当に即断したいときはモータ電源 (BACK 長押し) のほう。
        """
        twist = Twist()
        twist.linear.x = self._linear_ramp.update(0.0, dt)
        twist.angular.z = self._angular_ramp.update(0.0, dt)
        return twist

    def _at_rest(self):
        """刻みがゼロに着いているか (出すのをやめてよいか)。"""
        return self._linear_ramp.value == 0.0 and self._angular_ramp.value == 0.0

    # ── モード ──────────────────────────────────────────────────────────

    def _set_enabled(self, enabled):
        """teleop の入/切を切り替える。**音は鳴らさない。**

        _start_waypoints が「巡回を始める前に teleop を切っておく」ためにも呼ぶ
        ので、ここで鳴らすと START+BACK が「ピロリ↓ + ピピピ」になり、そもそも
        teleop が入っていなかったときは切ってもいないのに切った音が出る。呼び手が
        鳴らすこと。
        """
        self._enabled = enabled
        now = time.monotonic()
        if enabled:
            # 優先度で押さえるだけでは、teleop を切った瞬間に元のゴールが再開する。
            # 実際に取り消すのは _tick で、cancel_window のあいだ繰り返す。
            self._cancel_until = now + self._cancel_window
            self._cancel_at = 0.0
            # 刻みの起点をゼロへ戻す。ここで出すゼロと揃えておかないと、次の
            # _tick が「前は出ていた」ことにして減速で降りてくる。
            # **自律側が走らせている最中に入ってもここは段差になる** (こちらは
            # 相手の速度を知らない)。加減速が効くのはこのノードが出す区間だけ。
            self._linear_ramp.reset()
            self._angular_ramp.reset()
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

    # ── モータ電源 ──────────────────────────────────────────────────────

    def _toggle_motor_power(self):
        """モータ電源を入/切する。切るほうは非常停止として使われる。

        いま入っているかは分からない (ドライバは状態を出さない) ので、自分が
        投げた要求だけを数えている。外から変えられると 1 回ぶんずれる。
        """
        if self._motor_pending:
            # 前の要求がまだ返っていない。二度押しで往復させない。
            self._refuse("motor power request still in flight", warning=True)
            return

        if not self._motor_client.service_is_ready():
            self._refuse(
                "%s service is not available; is the driver up and activated?"
                % self._motor_service
            )
            return

        wanted = not self._motor_power
        request = SetBool.Request()
        request.data = wanted
        # 要求を先に投げる。切るほうは非常停止なので、音 (最長 0.41 秒) を
        # ボタンとサービス呼び出しのあいだに挟まない。
        self._motor_pending = True
        future = self._motor_client.call_async(request)
        future.add_done_callback(self._on_motor_power_response)
        self._motor_power = wanted
        self._play(TUNE_MOTOR_ON if wanted else TUNE_MOTOR_OFF)
        self.get_logger().info("motor power %s" % ("on" if wanted else "off"))

    def _on_motor_power_response(self, future):
        self._motor_pending = False
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001 - サービス側の失敗は何でも拾う
            response = None
            self.get_logger().error("motor power call failed: %s" % exc)
        if response is not None and response.success:
            return
        # 通らなかったのなら数えた側を戻す。戻さないと、次の長押しが「切る」
        # つもりで「入れる」になる。
        self._motor_power = not self._motor_power
        if response is None:
            # 例外の中身は上で出してある。ここは音だけ。
            self._play(TUNE_REFUSED)
        else:
            self._refuse(
                "motor power refused: %s" % (response.message or "(no message)")
            )

    # ── 音 ──────────────────────────────────────────────────────────────

    def _play(self, tune):
        """モードが変わったことを音で伝える。鳴らなくても走行には影響しない。"""
        if self._buzzer_pub is None:
            return
        self._tune.start(tune, time.monotonic())

    def _refuse(self, message, warning=False):
        """押されたが効かなかったことを、ログと音の両方で伝える。

        **断る分岐は必ずここを通すこと。** 「長押ししたのに何も起きない」が一番
        分かりにくく、実機ではログも見えない。ログだけ書いて音を鳴らし忘れると、
        パッドの側からは無反応と区別が付かなくなる。
        """
        if warning:
            self.get_logger().warning(message)
        else:
            self.get_logger().error(message)
        self._play(TUNE_REFUSED)

    def _tune_tick(self):
        """音の変わり目だけを /buzzer へ出す。"""
        changed, frequency = self._tune.update(time.monotonic())
        if changed:
            message = Int16()
            message.data = frequency
            self._buzzer_pub.publish(message)

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
        if not self._waypoints_file:
            # 既定の順路を持たないので、設定していなければここで終わり。黙って
            # 何かを走らせるより、押しても始まらないほうが安全側。
            self._refuse(
                "waypoints_file is not set; pick a route that matches map:= first"
            )
            return

        if self._goal_pending or self._goal_handle is not None:
            self._refuse(
                "waypoints already running; hold START to take over first",
                warning=True,
            )
            return

        # 取り消しを出しているあいだに投げると、あとから届いた取り消しが今から
        # 出すゴールを巻き込む。hold_seconds > cancel_window なら普通は来ない。
        if time.monotonic() < self._cancel_until:
            self._refuse(
                "still cancelling the previous goal; try again", warning=True
            )
            return

        try:
            frame_id, poses = load_waypoints(self._waypoints_file)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            self._refuse("cannot read %s: %s" % (self._waypoints_file, exc))
            return

        if not self._follow_client.server_is_ready():
            # navigation.launch.py が立っていないか、lifecycle がまだ activate
            # されていない。ここで黙ると「押したのに何も起きない」になる。
            self._refuse(
                "%s action server is not available; is navigation.launch.py up?"
                % self._follow_action
            )
            return

        self._set_enabled(False)

        stamp = self.get_clock().now().to_msg()
        for pose in poses:
            pose.header.stamp = stamp

        # ゴールを投げる前に順路を出す。先読み (waypoint_prefetch) は 1 点目の
        # 計画が来た時点で並びを引くので、後出しにすると 1 点目ぶんだけ間に合わない。
        self._publish_waypoints(frame_id, poses)

        goal = FollowWaypoints.Goal()
        goal.poses = poses
        self._goal_pending = True
        future = self._follow_client.send_goal_async(goal)
        future.add_done_callback(self._on_goal_response)
        self._play(TUNE_WAYPOINTS)
        self.get_logger().info(
            "following %d waypoints in %s" % (len(poses), frame_id)
        )

    def _publish_waypoints(self, frame_id, poses):
        """順路を latch して出す (vi_planner の先読み用。見せるためのものではない)。"""
        path = Path()
        path.header.frame_id = frame_id
        path.header.stamp = self.get_clock().now().to_msg()
        path.poses = poses
        for pose in path.poses:
            # header ごと差し替えない。poses は呼び出し元がそのまま
            # FollowWaypoints のゴールに使う同じ配列なので、1 つの header を
            # 共有させると片方を触ったつもりが全点に効く。
            pose.header.frame_id = frame_id
            pose.header.stamp = path.header.stamp
        self._path_pub.publish(path)

    def _on_goal_response(self, future):
        self._goal_pending = False
        handle = future.result()
        if not handle.accepted:
            self._refuse("waypoint goal rejected")
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
        # 走り切った音は 1 点も外さなかったときだけ。waypoint_follower は
        # stop_on_failure: false なので、**全点が地図の外でも SUCCEEDED で返る**
        # (地図と経路を取り違えたときがこれ)。取り消しのときは鳴らさない。取り消す
        # のは teleop へ移ったときで、その音を出した直後にここへ来るため、鳴らすと
        # 切り替えの音を潰してしまう (nav2_util の SimpleActionServer は
        # is_canceling() を見て canceled() を返すので、ここは必ず CANCELED になる)。
        if result.status == GoalStatus.STATUS_SUCCEEDED and not missed:
            self._play(TUNE_FINISHED)
        elif result.status != GoalStatus.STATUS_CANCELED:
            self._play(TUNE_REFUSED)

    def stop(self):
        """終了時に最後の指令と最後の音をゼロにしておく。

        ドライバは最後に受けた速度を cmd_vel_timeout (既定 60 秒) のあいだ保持
        するので、走っている状態でノードだけ落ちると走り続ける。ブザーのほうは
        timeout が**無い**ので、旋律の途中で落ちると誰かが 0 を出すまで鳴り続ける。
        """
        try:
            self._cmd_pub.publish(Twist())
        except Exception:  # noqa: BLE001 - 落ちる途中なので何が来ても握る
            pass
        if self._buzzer_pub is None:
            return
        self._tune.stop()
        try:
            message = Int16()
            message.data = 0
            self._buzzer_pub.publish(message)
        except Exception:  # noqa: BLE001 - 同上
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
