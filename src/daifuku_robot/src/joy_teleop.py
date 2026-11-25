#!/usr/bin/env python3
# Copyright 2026 Keita Sekiguchi / nop
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ゲームパッドで手動走行と自律走行を切り替える.

robot_bringup.launch.py が joy:=true (既定) のとき joy_node と一緒に立てる。
想定しているのは XInput 互換のゲームパッド。
上流の teleop_twist_joy を使わないのは、長押しと同時押しでモードを切り替える
必要があるためで、あちらは押している間だけ速度を出す (デッドマン) しかできない。

  * STARTを長押し          … teleop の入/切をトグルする (ピロリ↑ / ピロリ↓)
  * BACKを長押し           … モータ電源の入/切をトグルする (ピロリピロリ↑ / ↓)
  * RB を押している間      … ブースト (上限が linear_max_speed から boost へ上がる)
  * B (丸) を押す          … 停止点で止まっている巡回を再開する (/waypoint/resume)

左スティックで前後・旋回を操作し、右スティックの左右が不感帯を超えたときは
旋回だけ右を優先する。後退入力中は旋回の向きを反転する。

長押しの時間はどれも hold_seconds (既定 2 秒) で、押し続けてその時間が経った時点で
決まる (joy_buttons.HoldLatch)。START と BACK を同時に押したときは
どちらも起きない。ウェイポイント巡回はこのノードでは始めず、RViz の
daifuku_waypoint_manager パネルから始める。B は長押しを待たず、押し始めで 1 回だけ
/waypoint/resume を出す。止まっているかどうかは知らないので、判断は機体側の
waypoint_runner (daifuku_navigation) に任せる。

出す先は **/cmd_vel_teleop** (twist_mux の優先度 100 側)。自律側の /cmd_vel は
優先度 10 なので、手動指令が優先される。走行中のゴールは取り消さないので、
teleop を切ると自律走行がそのまま再開する
(下の「teleop 中は『出しっぱなし』にしてある」)。

## 指令は刻んで出す (加減速)

スティックの値をそのまま速度にはしない。**本体ドライバは受けた速度をそのまま
指令周波数にする**ので (raspicat_driver の `_drive`。公式実装も同じ)、段差の
ある指令はそのまま段差のある周波数になる。速度の閉ループは制御基板の中にあるが、
立ち上がりが急すぎればそこが飽和して指令より遅く・左右バラバラに回り、**エラーは
何も出ない**。そこで linear_accel / linear_decel (と angular 側) で変化率を抑える。
詳しくは joy_buttons.RateLimiter。**根拠は 2026-08-09 まで「ステッパの自起動域と
脱調」だったが、モータはギアエンコーダ付き DC だった。値も症状も未実測。**

刻むのは**このノードが出す区間だけ**である。teleop に入った時点で自律側が
走らせていても相手の速度は分からないので、そこは段差のまま (優先度で自律側を
上書きし、ゼロから出し直す)。自律側の加減速は velocity_smoother の担当。

## モータ電源は「切る」ほうが本命

twist_mux の優先度は非常停止ではない。実際に止めるのはモータ電源で、これまでは
`control.sh motor off` (= /motor_power サービス) しか手が無かった。BACK 単独の
長押しは**その手をパッドに出したもの**である。切れば twist_mux も自律側も関係なく
車輪が止まる。

いま入っているかは自前実装 (driver:=original) が **/motor_power_state**
(`std_msgs/Bool`、latch) で出すので、それを購読して使う。公式実装
(既定の driver:=raspimouse) にはその口が無いので、来ないあいだは**自分が投げた
要求だけを数える**。起点は motor_power_start_state で、そのときはドライバの
initial_motor_power と合わせておくこと。`control.sh motor` や RViz のパネルから
外して変えられると 1 回ぶんずれる (次の長押しが空振りして、その次で揃う)。

## モードは音でも伝える

長押しは hold_seconds (既定 2 秒) 経つまで何も起きず、切り替わった先はスティックを倒すまで見分けが
付かない。手元にノート PC が無ければログも見えないので、切り替わった時点で
**/buzzer** (`std_msgs/Int16`、値は Hz・0 で停止) へ短い旋律を出す。旋律そのものは
joy_buttons.py の TUNE_* にある。押しても効かなかったとき (モータ電源の要求が
返っていない、ドライバが立っていない) にも鳴らすのは、
そこが**今まで完全に無音だった**ためで、「長押ししたのに何も起きない」が一番
分かりにくい。

購読しているのは本体ドライバで、自前実装 (driver:=original) も公式実装
(既定の driver:=raspimouse) も同じトピック・同じ型で受ける。**鳴らなくても
走行には何の影響もない** — ドライバが activate されていない、`use_buzzer:=false`、
ブザーのピンを掴めなかった、のどれでもエラーは出ずにただ無音になる。

ブザーには cmd_vel のような timeout が無く、ドライバは最後に受けた周波数を
**鳴らし続ける**。旋律の途中でこのノードが落ちると鳴りっぱなしになるので、
stop() で 0 を出しておく。

## いまのモードは LED でも伝える

音は**切り替わった瞬間しか**伝えない。2 秒前に鳴ったのを聞き逃せば、いま teleop が
入っているのかは結局スティックを倒すまで分からない。そこで **/leds**
(`raspimouse_msgs/Leds`、ドライバが購読する 4 灯) へ現在の状態を出す
(led0=teleop 入、led1=モータ電源、led3=/joy 受信中。led2 は使わず消したまま)。音を消していない
のは、機体を見ていない・見えない位置にいるときは音のほうが届くため。

**変化時だけでなく 1 秒ごとにも出す。** ドライバは deactivate で LED を消すが
activate で戻さないので、lifecycle を回すと次にボタンを押すまで消灯したままに
なる (エラーは出ない)。~/enabled と同じ枝で出しているのはこのため。

**led1 (モータ電源) はドライバが出す /motor_power_state をそのまま映す。** 公式実装
(driver:=raspimouse) はそれを出さないので、そのときだけ要求の写し (`MotorPower`)
に落ちる — 外から変えられると 1 回ぶんずれるのはその構成のとき。

## teleop 中は「出しっぱなし」にしてある

**自律側を黙らせているのは twist_mux の優先度である** (/cmd_vel_teleop の 100 が
/cmd_vel の 10 より上)。teleop に入っても /follow_waypoints と /navigate_to_pose の
ゴールは**取り消さない**。取り消すと、teleop を切ったあとに自律走行が再開せず、
入れ直すしかなくなるため。走行を止めたいときは操作パネルの「中断」か
ウェイポイントパネルの Cancel を使い、確実に止めるならモータ電源を切る。

teleop 入のあいだはスティックが中立でもゼロ速度を publish し続ける。
twist_mux は勝っているトピックがメッセージを受けたときしか中継しないので、黙ると
本体ドライバに何も届かず、ドライバは最後の指令を cmd_vel_timeout (既定 60 s) まで
保持するからである。

teleop を切るときは、減速で降りきってから、さらに
stop_tail 秒ゼロを出してから黙る。黙るだけだと本体ドライバは最後に受けた速度を
保持し続けるためで、最後に届いた指令をゼロにしておく。自前実装
(driver:=original) は cmd_vel_timeout の 60 秒で止まるが、公式実装 (既定の
driver:=raspimouse) はこのキーを持たず、いつ止まるかは**未確認**である。
**降りきるまでを stop_tail 任せにしていない**のは、減速に stop_tail より時間の
かかる設定にされたときに、途中の速度で黙って走り続けてしまうため。

## 受信が途切れたら止める

無線のゲームパッドは、受信機を抜かれても電池が切れても /joy が来なくなるだけで
エラーは出ない。joy_timeout 秒来なければゼロへ**減速し** (段差で落とすとドライバが
そのまま周波数の段差にするため)、長押しの計測も捨てる (押しっぱなしのまま
切れたのを長押しの成立として拾わないため)。即断したいときはモータ電源のほう。

ボタンと軸の番号は **XInput (X モード)** のときのもの。DirectInput のパッドや、
モード切り替えを持つ機種を D 側にしたものは総入れ替えになるので、パラメータで
直すこと (`ros2 topic echo /joy` で押しながら確かめる)。左スティックと十字キーを
入れ替えるモードを持つ機種もある。
"""

import os
import sys
import time

from geometry_msgs.msg import Twist
from raspimouse_msgs.msg import Leds
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import Joy
from std_msgs.msg import Bool, Empty, Int16
from std_srvs.srv import SetBool

# joy_buttons.py は同じ lib/daifuku_robot に入る (CMakeLists.txt の install(PROGRAMS))。
# share/ 側の src/ から直接叩かれることもあるので、両方で通るように自分の隣を見る。
_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from joy_buttons import (  # noqa: E402
    axis,
    axis_to_speed,
    HoldLatch,
    MOTOR,
    pressed,
    RateLimiter,
    TOGGLE,
    TUNE_MOTOR_OFF,
    TUNE_MOTOR_ON,
    TUNE_REFUSED,
    TUNE_TELEOP_OFF,
    TUNE_TELEOP_ON,
    TunePlayer,
)
from joy_motor import MotorPower  # noqa: E402

# 旋律の音の変わり目を拾う周期 [s]。一番短い音 (60 ms) の 1/6 で、publish するのは
# 変わり目だけなのでトピックには乗らない。_tick と分けてあるのは、あちらが
# publish_rate (パラメータ) 次第で 50 ms 粒度になり、音長がそれに引きずられるため。
TUNE_PERIOD = 0.01


class JoyTeleop(Node):

    def __init__(self):
        super().__init__('joy_teleop')

        self.declare_parameter('axis_linear', 1)
        self.declare_parameter('axis_angular', 0)
        self.declare_parameter('axis_angular_right', 3)
        self.declare_parameter('deadzone', 0.15)
        self.declare_parameter('linear_min_speed', 0.1)
        self.declare_parameter('linear_max_speed', 0.35)
        self.declare_parameter('linear_boost_speed', 0.5)
        self.declare_parameter('angular_min_speed', 0.3)
        self.declare_parameter('angular_max_speed', 1.0)
        self.declare_parameter('angular_boost_speed', 1.5)
        self.declare_parameter('linear_accel', 0.9)
        self.declare_parameter('linear_decel', 1.8)
        self.declare_parameter('angular_accel', 6.0)
        self.declare_parameter('angular_decel', 12.0)
        self.declare_parameter('button_toggle', 7)
        self.declare_parameter('button_motor', 6)
        self.declare_parameter('button_boost', 5)
        self.declare_parameter('button_resume', 1)
        self.declare_parameter('hold_seconds', 2.0)
        self.declare_parameter('publish_rate', 20.0)
        self.declare_parameter('joy_timeout', 0.5)
        self.declare_parameter('stop_tail', 1.0)
        self.declare_parameter('start_enabled', False)
        self.declare_parameter('buzzer', True)
        self.declare_parameter('leds', True)
        self.declare_parameter('motor_power_service', 'motor_power')
        self.declare_parameter('motor_power_start_state', False)

        def value(name):
            return self.get_parameter(name).value

        self._axis_linear = int(value('axis_linear'))
        self._axis_angular = int(value('axis_angular'))
        self._axis_angular_right = int(value('axis_angular_right'))
        self._deadzone = float(value('deadzone'))
        self._linear_min = float(value('linear_min_speed'))
        self._linear_max = float(value('linear_max_speed'))
        self._linear_boost = float(value('linear_boost_speed'))
        self._angular_min = float(value('angular_min_speed'))
        self._angular_max = float(value('angular_max_speed'))
        self._angular_boost = float(value('angular_boost_speed'))
        # 刻むのは目標のほうではなく publish する値。目標 (axis_to_speed の出力) は
        # 不感帯の外側で min_speed に飛ぶ段差を持つので、そこも一緒に均される。
        self._linear_ramp = RateLimiter(value('linear_accel'), value('linear_decel'))
        self._angular_ramp = RateLimiter(value('angular_accel'), value('angular_decel'))
        self._button_toggle = int(value('button_toggle'))
        self._button_motor = int(value('button_motor'))
        self._button_boost = int(value('button_boost'))
        self._button_resume = int(value('button_resume'))
        self._joy_timeout = float(value('joy_timeout'))
        self._stop_tail = float(value('stop_tail'))

        self._latch = HoldLatch(float(value('hold_seconds')))
        self._enabled = bool(value('start_enabled'))
        self._axes = []
        self._buttons = []
        self._joy_at = None
        self._tick_at = None
        self._stop_until = 0.0
        self._state_at = 0.0
        self._leds = None
        self._leds_at = 0.0
        self._warned_stale = False
        # 前の周期に再開ボタンが押されていたか。押し始めの 1 回だけ出すために持つ。
        self._resume_held = False
        self._tune = TunePlayer()
        # ドライバが状態を出さない構成 (driver:=raspimouse) 用の写しは MotorPower
        # が持つ。自分が投げた要求だけを数えるので、外から変えられるとずれる。
        self._motor = MotorPower(value('motor_power_start_state'))

        self._cmd_pub = self.create_publisher(Twist, 'cmd_vel_teleop', 10)
        # 状態は遅れて繋いだ購読者にも見せたい (「なぜ動かない」を追うため)。
        self._state_pub = self.create_publisher(
            Bool, '~/enabled',
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )
        self.create_subscription(Joy, 'joy', self._on_joy, 10)
        # 停止点で止まった巡回の再開。受けるのは機体側の waypoint_runner で、
        # 止まっていなければ聞き流す。RViz の PC が居なくても届く。
        self._resume_pub = self.create_publisher(Empty, 'waypoint/resume', 10)

        # ブザーは本体ドライバが購読する。買い手が居なくても publish は通るので、
        # 立っていない・ピンを掴めていないときは黙るだけになる。
        self._buzzer_pub = None
        if bool(value('buzzer')):
            self._buzzer_pub = self.create_publisher(Int16, 'buzzer', 10)
            self.create_timer(TUNE_PERIOD, self._tune_tick)

        # LED も同じくドライバが購読する。こちらは旋律ではなく現在の状態なので、
        # 変化時と 1 秒ごとの両方で出す (_publish_state と同じ枝)。
        self._leds_pub = None
        if bool(value('leds')):
            self._leds_pub = self.create_publisher(Leds, 'leds', 10)

        self._motor_service = value('motor_power_service')
        self._motor_client = self.create_client(SetBool, self._motor_service)
        # 自前実装だけが出す実際の電源状態。latch なので、こちらが後から上がっても
        # 現在値が 1 通届く (購読側も transient_local でないと届かない)。
        self.create_subscription(
            Bool, 'motor_power_state', self._on_motor_state,
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )

        period = 1.0 / max(float(value('publish_rate')), 1.0)
        # 加減速に使う経過時間の上限。タイマが詰まって数秒飛んだとき、その分を
        # まるごと積むと 1 回で最高速まで飛ぶ (刻んだ意味が無くなる)。
        self._max_dt = 5.0 * period
        self.create_timer(period, self._tick)
        self._publish_state()
        self.get_logger().info(
            'joy teleop %s; hold %.1fs: START toggles teleop, BACK toggles '
            'motor power; button %d resumes waypoints'
            % ('on' if self._enabled else 'off', self._latch.hold_seconds, self._button_resume)
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
                pressed(self._buttons, self._button_motor),
            )
            if action == MOTOR:
                self._toggle_motor_power()
            elif action == TOGGLE:
                self._set_enabled(not self._enabled)
                self._play(TUNE_TELEOP_ON if self._enabled else TUNE_TELEOP_OFF)
            # 長押しを待たず、押し始めで 1 回だけ出す。押しっぱなしでも繰り返さない。
            resume = pressed(self._buttons, self._button_resume)
            if resume and not self._resume_held:
                self._resume_pub.publish(Empty())
                self.get_logger().info('resume requested for waypoint following')
            self._resume_held = resume
        else:
            self._latch.reset()
            # 受信が戻ったときに握ったままだったら、それは押し始めとして数えない。
            self._resume_held = True
            if self._enabled and not self._warned_stale:
                self._warned_stale = True
                self.get_logger().warning('no joy for %.1f s; slowing to a stop'
                                          % self._joy_timeout)

        # 状態は変化時だけでなく定期的にも出す。TRANSIENT_LOCAL は購読側も
        # transient_local で繋がないと過去の 1 件が届かず、素の
        # `ros2 topic echo` (volatile) では次の切り替えまで何も出ないため。
        if now - self._state_at >= 1.0:
            self._publish_state()

        # LED は _tick から出す。ここなら状態が変わった call site を数え上げずに
        # 済み (20 Hz で見に来るので押してから 50 ms 以内に映る)、受信断かどうか
        # (led3) を知っているのもここだけ。
        self._publish_leds(fresh)

        # teleop を切ったあとは stop_tail を待たずに、**刻みがゼロに着くまで**
        # 出し続ける。stop_tail (1 秒) より減速に時間がかかる設定にされると、
        # 途中で黙った速度をドライバが保持したまま走り続けるため。
        if self._enabled:
            self._cmd_pub.publish(self._twist(dt) if fresh else self._coast(dt))
        elif now < self._stop_until or not self._at_rest():
            self._cmd_pub.publish(self._coast(dt))

    def _elapsed(self, now):
        """前回の _tick からの経過 [s]。上限は max_dt、初回は 0.

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

        linear = axis_to_speed(
            axis(self._axes, self._axis_linear),
            self._deadzone, self._linear_min, linear_max,
        )
        steering = axis(self._axes, self._axis_angular_right)
        if abs(steering) <= self._deadzone:
            steering = axis(self._axes, self._axis_angular)
        angular = axis_to_speed(
            steering, self._deadzone, self._angular_min, angular_max,
        )
        if linear < 0.0:
            angular = -angular

        twist = Twist()
        twist.linear.x = self._linear_ramp.update(linear, dt)
        twist.angular.z = self._angular_ramp.update(angular, dt)
        return twist

    def _coast(self, dt):
        """目標をゼロにして刻む。受信断のときと teleop を切ったあとに出す.

        受信断でも段差で 0 に落とさないのは、ドライバがそれをそのまま指令
        周波数の段差にするため。止まるまでは linear_decel で決まる (0.8 m/s なら
        0.44 秒・0.18 m)。**2026-08-09 まで「段差だと脱調して惰性で滑る」を根拠に
        していたが、モータは DC で閉ループは基板の中なので、段差のほうが速く
        止まる可能性がある (未実測)。** 即断はモータ電源 (BACK 長押し) のほう。
        """
        twist = Twist()
        twist.linear.x = self._linear_ramp.update(0.0, dt)
        twist.angular.z = self._angular_ramp.update(0.0, dt)
        return twist

    def _at_rest(self):
        """刻みがゼロに着いているか (出すのをやめてよいか)."""
        return self._linear_ramp.value == 0.0 and self._angular_ramp.value == 0.0

    # ── モード ──────────────────────────────────────────────────────────

    def _set_enabled(self, enabled):
        """Teleop の入/切を切り替える。音は呼び手が鳴らす."""
        self._enabled = enabled
        now = time.monotonic()
        if enabled:
            # 走行中のゴールは取り消さない。手動指令が優先度で勝ち、teleop を
            # 切れば自律走行がそのまま再開する。
            # 刻みの起点をゼロへ戻す。ここで出すゼロと揃えておかないと、次の
            # _tick が「前は出ていた」ことにして減速で降りてくる。
            # **自律側が走らせている最中に入ってもここは段差になる** (こちらは
            # 相手の速度を知らない)。加減速が効くのはこのノードが出す区間だけ。
            self._linear_ramp.reset()
            self._angular_ramp.reset()
            self._cmd_pub.publish(Twist())
        else:
            self._stop_until = now + self._stop_tail
        self._publish_state()
        self.get_logger().info('teleop %s' % ('on' if enabled else 'off'))

    def _publish_state(self):
        message = Bool()
        message.data = self._enabled
        self._state_pub.publish(message)
        self._state_at = time.monotonic()

    def _publish_leds(self, fresh):
        """いまのモードを 4 灯へ映す。変わったときと 1 秒ごとに出す.

        1 秒ごとにも出すのは、ドライバが deactivate で LED を消す一方 activate で
        戻さないため。変化時だけにすると lifecycle を回した先で消えたままになり、
        **エラーは出ない**。

        led1 の出どころは MotorPower.is_on (ドライバが出していればその状態、
        公式実装のときだけ要求の写し)。
        """
        if self._leds_pub is None:
            return
        state = (
            self._enabled,                                          # teleop 入
            self._motor.is_on(),                                    # モータ電源
            False,                                                  # 未使用
            fresh,                                                  # /joy が来ている
        )
        now = time.monotonic()
        if state == self._leds and now - self._leds_at < 1.0:
            return
        self._leds = state
        self._leds_at = now
        self._leds_pub.publish(
            Leds(led0=state[0], led1=state[1], led2=state[2], led3=state[3])
        )

    # ── モータ電源 ──────────────────────────────────────────────────────

    def _on_motor_state(self, msg):
        """ドライバが出す実際のモータ電源の状態 (自前実装だけが出す).

        読む側 (_tick と _toggle_motor_power) と同じスレッドで走る前提で、鍵は
        持っていない。main() が rclpy.spin (単スレッド) だからで、
        MultiThreadedExecutor へ移すならここは非常停止の判断に効く値である。
        """
        self._motor.note_driver_state(msg.data)

    def _toggle_motor_power(self):
        """モータ電源を入/切する。切るほうは非常停止として使われる.

        いま入っているかは MotorPower.is_on から取る。ドライバが状態を出していない
        構成では自分が投げた要求の写しなので、外から変えられると 1 回ぶんずれる。
        """
        if self._motor.pending:
            # 前の要求がまだ返っていない。二度押しで往復させない。
            self._refuse('motor power request still in flight', warning=True)
            return

        if not self._motor_client.service_is_ready():
            self._refuse(
                '%s service is not available; is the driver up and activated?'
                % self._motor_service
            )
            return

        wanted = not self._motor.is_on()
        request = SetBool.Request()
        request.data = wanted
        # 要求を先に投げる。切るほうは非常停止なので、音 (最長 0.41 秒) を
        # ボタンとサービス呼び出しのあいだに挟まない。
        self._motor.begin_request(wanted)
        future = self._motor_client.call_async(request)
        future.add_done_callback(self._on_motor_power_response)
        self._play(TUNE_MOTOR_ON if wanted else TUNE_MOTOR_OFF)
        self.get_logger().info('motor power %s' % ('on' if wanted else 'off'))

    def _on_motor_power_response(self, future):
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001 - サービス側の失敗は何でも拾う
            response = None
            self.get_logger().error('motor power call failed: %s' % exc)
        if response is not None and response.success:
            self._motor.complete_request()
            return
        # 通らなかったのなら数えた側を戻す。戻さないと、写しを使う構成
        # (driver:=raspimouse) で次の長押しが「切る」つもりで「入れる」になる。
        self._motor.revert_request()
        if response is None:
            # 例外の中身は上で出してある。ここは音だけ。
            self._play(TUNE_REFUSED)
        else:
            self._refuse(
                'motor power refused: %s' % (response.message or '(no message)')
            )

    # ── 音 ──────────────────────────────────────────────────────────────

    def _play(self, tune):
        """モードが変わったことを音で伝える。鳴らなくても走行には影響しない."""
        if self._buzzer_pub is None:
            return
        self._tune.start(tune, time.monotonic())

    def _refuse(self, message, warning=False):
        """押されたが効かなかったことを、ログと音の両方で伝える.

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
        """音の変わり目だけを /buzzer へ出す."""
        changed, frequency = self._tune.update(time.monotonic())
        if changed:
            message = Int16()
            message.data = frequency
            self._buzzer_pub.publish(message)

    def stop(self):
        """終了時に最後の指令と最後の音をゼロにしておく.

        ドライバは最後に受けた速度を cmd_vel_timeout (既定 60 秒) のあいだ保持
        するので、走っている状態でノードだけ落ちると走り続ける。ブザーのほうは
        timeout が**無い**ので、旋律の途中で落ちると誰かが 0 を出すまで鳴り続ける。
        LED も同じで、消さずに落ちると**最後のモードを主張したまま点きっぱなしに
        なる** (音が鳴り止むのと違い、点いた LED は状態の断定として読まれる)。
        """
        try:
            self._cmd_pub.publish(Twist())
        except Exception:  # noqa: BLE001 - 落ちる途中なので何が来ても握る
            pass
        if self._leds_pub is not None:
            try:
                self._leds_pub.publish(Leds())
            except Exception:  # noqa: BLE001 - 同上
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


if __name__ == '__main__':
    main()
