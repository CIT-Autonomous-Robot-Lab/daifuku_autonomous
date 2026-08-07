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

"""ゲームパッドの長押し判定と、スティック -> 速度の写像と、モードを伝える音。

joy_teleop.py から import される。ここに rclpy を持ち込まないのは、ここが
joy_teleop.py で唯一「実機に載せる前に確かめられる」部分だからである。ROS も
ジョイスティックもブザーも要らないので、開発ホスト (Windows) で素の python から
呼んで検算できる。

長押しの判定を分けてあるのは、START 単独 (teleop 切り替え)・BACK 単独 (モータ電源)・
START+BACK 同時 (ウェイポイント走行開始) の 3 つが**必ず重なる**ため。素直に
ボタンごとのタイマーを置くと、同時押しの途中で単独側が先に発火してモードが裏返る。
"""

# HoldLatch.update() の戻り値。
TOGGLE = "toggle"
COMBO = "combo"
MOTOR = "motor"

# モードを伝える旋律。(周波数 [Hz], 長さ [s]) の並びで、0 は無音。
#
# 何をどう区別しているか (下の数字の読み方):
#   向き   … 上がりが「入った・走り切った」、下がりが「切れた」
#   リズム … 短く 3 回が「始まった」、低く長く 2 回が「効かなかった」
#   長さ   … モータ電源だけ同じ向きを 2 回繰り返す。teleop の入/切と向きが同じなのに
#            意味の重さが違う (切るほうは非常停止として使う) ため
#   無音   … 同じ周波数を続けて出しても切れ目が聞こえないので挟む
#            (ドライバは周波数を保持するだけで、音は途切れない)
#
# **高さでは区別していない。** 全部 1175〜2093 Hz に収めてあるのは、実機で聞き取り
# やすい帯がそこだけだったため (掃引の結果は ../../../docs/usage/joystick.md)。
TUNE_TELEOP_ON = ((1397, 0.08), (1976, 0.14))            # ピロリ↑
TUNE_TELEOP_OFF = ((1976, 0.08), (1397, 0.14))           # ピロリ↓
TUNE_WAYPOINTS = (                                       # ピピピ (短く 3 回)
    (2093, 0.07), (0, 0.06), (2093, 0.07), (0, 0.06), (2093, 0.16),
)
TUNE_FINISHED = ((1397, 0.10), (1760, 0.10), (2093, 0.22))   # ピロリロ↑
TUNE_REFUSED = ((1175, 0.14), (0, 0.07), (1175, 0.26))       # ブッブー (低く長く 2 回)
TUNE_MOTOR_ON = (                                        # ピロリピロリ↑
    (1397, 0.07), (1976, 0.09), (0, 0.05), (1397, 0.07), (1976, 0.13),
)
TUNE_MOTOR_OFF = (                                       # ピロリピロリ↓
    (1976, 0.07), (1397, 0.09), (0, 0.05), (1976, 0.07), (1397, 0.13),
)


class TunePlayer:
    """旋律を「いま出すべき周波数」に変える。時計は呼び出し側が渡す。

    ドライバのブザーは周波数を 1 つ受け取って**そのまま鳴らし続ける**だけなので、
    旋律にするには音の変わり目ごとに次の値を出してやる必要がある。ここはその
    変わり目だけを教える (毎周期 publish すると、鳴っている音は同じなのに
    トピックが 100 Hz で埋まる)。

    音長は前の音の終わりからの積算で決める。update() の呼ばれ方がゆらいでも
    旋律全体が伸びないようにするためで、周期が音長より粗ければ音は鳴らずに
    飛ばされる (短い音が伸びて次の音を押し出すより、そのほうが分かりやすい)。
    """

    def __init__(self):
        self._notes = []
        self._index = 0
        self._until = 0.0
        self._frequency = 0

    def start(self, tune, now):
        """旋律を頭から鳴らし始める。鳴っている途中なら差し替える。

        Args:
            tune: (周波数 [Hz], 長さ [s]) の並び。
            now: 単調増加の秒 (time.monotonic())。
        """
        self._notes = list(tune)
        self._index = 0
        # 1 音目は次の update() で拾う (start() では何も返さない)。
        self._until = now

    def stop(self):
        """鳴らしかけを捨てる。無音そのものは呼び出し側が出す。"""
        self._notes = []
        self._index = 0
        self._frequency = 0

    @property
    def playing(self):
        return bool(self._notes)

    def update(self, now):
        """今の周波数と、それが前回から変わったかを返す。

        Returns:
            (変わったか, 周波数 [Hz])。周波数 0 は無音。旋律を鳴らし終えた回だけ
            (True, 0) を返し、そのあとは (False, 0) を返し続ける。
        """
        changed = False
        while self._index < len(self._notes) and now >= self._until:
            frequency, seconds = self._notes[self._index]
            self._index += 1
            self._until += seconds
            if frequency != self._frequency:
                self._frequency = frequency
                changed = True

        if self._notes and self._index >= len(self._notes) and now >= self._until:
            self._notes = []
            self._index = 0
            if self._frequency != 0:
                self._frequency = 0
                changed = True

        return changed, self._frequency


class HoldLatch:
    """2 つのボタンの「単独長押し」と「同時長押し」を取り違えずに拾う。

    毎周期 update() を呼ぶ。3 つの長押しを見分ける — 主単独 (TOGGLE)、副単独
    (MOTOR)、同時 (COMBO)。

    **主と同時は「hold_seconds 経った時点で押されている組」で決める。** 主単独の
    つもりでも、そのとき副が入っていれば同時のほうが返る。

    **副単独だけは「離した時点」で決める。** 経過した時点にすると、同時押しを
    やりかけて主だけ先に離したとき、副を握ったままでは何も返せなくなる (同時の
    やりかけとして毒が回っているため)。押しているのに永久に反応しない状態になる
    ので、離す動作で必ず決着させる。副単独がモータ電源の入/切なのでなおさら。

    3 つの規則で誤発火を防いでいる:

      * 一度返したら**両方を離すまで**二度と返さない。押しっぱなしにしても
        hold_seconds ごとに再発火しない (副単独は離してから返すので関係ない)。
      * 主を押しているあいだに副が一度でも入ったら、その主を離すまで主単独は
        返さない。同時押しをやりかけて途中でやめたとき (副だけ先に離したとき) に
        モードが裏返らないようにするため。
      * 副を押しているあいだに主が一度でも入ったら、その副を離しても副単独は
        返さない。同時押しは常にこちらより優先される。
    """

    def __init__(self, hold_seconds):
        self.hold_seconds = hold_seconds
        self._main_since = None
        self._sub_since = None
        self._latched = False
        self._main_poisoned = False
        self._sub_poisoned = False

    def reset(self):
        """押下の履歴を捨てる。

        ジョイスティックの受信が途切れたら呼ぶ。最後に見えたボタンの状態を
        押しっぱなしとして数え続けると、電池切れや受信機の抜けが hold_seconds 後の
        モード切り替えになって現れる。副単独も返さない (押下そのものを無かった
        ことにするので、次に見えた「離す」は離す動作として数えない)。
        """
        self._main_since = None
        self._sub_since = None
        self._latched = False
        self._main_poisoned = False
        self._sub_poisoned = False

    def update(self, now, main, sub):
        """今のボタンの状態を渡し、成立した長押しを返す。

        Args:
            now: 単調増加の秒 (time.monotonic())。壁時計を渡さないこと。
            main: 主ボタン (START) が押されているか。
            sub: 副ボタン (BACK) が押されているか。

        Returns:
            TOGGLE / COMBO / MOTOR / None。
        """
        released = None

        if main:
            if self._main_since is None:
                self._main_since = now
        else:
            self._main_since = None
            self._main_poisoned = False

        if sub:
            if self._sub_since is None:
                self._sub_since = now
                self._sub_poisoned = False
            if main:
                self._main_poisoned = True
                self._sub_poisoned = True
        else:
            # 副を離した瞬間。長押しが成立していて同時押しの毒が回っていなければ、
            # ここで副単独として決着させる。
            if (
                self._sub_since is not None
                and not self._sub_poisoned
                and now - self._sub_since >= self.hold_seconds
            ):
                released = MOTOR
            self._sub_since = None
            self._sub_poisoned = False

        if not main and not sub:
            self._latched = False

        if released is not None:
            # 主も同時に成立することはない。副を押していたあいだに主が入って
            # いれば主のほうにも毒が回っている。
            return released

        if self._latched or not main:
            return None

        if sub:
            # 同時押しの経過は「あとから押されたほう」から数える。
            since = max(self._main_since, self._sub_since)
            if now - since >= self.hold_seconds:
                self._latched = True
                return COMBO
            return None

        if self._main_poisoned:
            return None

        if now - self._main_since >= self.hold_seconds:
            self._latched = True
            return TOGGLE
        return None


def axis_to_speed(value, deadzone, min_speed, max_speed):
    """スティックの傾き (-1..1) を速度へ写す。

    不感帯を出た**すぐ外側で min_speed に飛ぶ**のは意図的。min_speed 未満は
    ステップ周波数が低すぎて機体が唸るだけで進まない領域なので (raspicat_driver
    の min_step_frequency)、そこへ写しても操作の分解能にならない。そこから
    max_speed までを線形に割り当てる。

    Args:
        value: 軸の値 (-1..1)。符号がそのまま速度の向きになる。
        deadzone: この絶対値以下は 0 にする (0 <= deadzone < 1)。
        min_speed: 不感帯を出た直後の速さ。
        max_speed: 目一杯倒したときの速さ。

    Returns:
        速度 [m/s] または [rad/s]。
    """
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    span = 1.0 - deadzone
    scale = 1.0 if span <= 0.0 else min((magnitude - deadzone) / span, 1.0)
    speed = min_speed + scale * (max_speed - min_speed)
    return speed if value > 0.0 else -speed


def _approach(value, target, limit):
    """value を target へ、1 回で limit までしか動かさずに近づける。"""
    delta = target - value
    if delta > limit:
        return value + limit
    if delta < -limit:
        return value - limit
    return target


class RateLimiter:
    """指令速度の変化率を抑える (加減速)。時計は呼び出し側が渡す。

    **本体ドライバは指令をそのままステップ周波数にする** (raspicat_driver の
    `_drive`。公式実装も同じ) ので、段差のある指令はそのまま段差のある周波数に
    なる。0 から 0.8 m/s は 725 Hz への飛びで、ステッパは自起動域を超えると
    脱調して**指令より遅く・左右バラバラに**回る (エラーは出ない)。ここで刻むのは
    そのため。止まるほうも同じで、段差で 0 に落とすと積荷が前へ倒れる。

    加速と減速で上限を分ける。**減速のほうを大きくする** — 立ち上がりが緩いのは
    操作感の問題で済むが、止まるのが遅いのは安全側でないため。

    前後を入れ替えるときは、0 までを減速・0 から先を加速の上限で刻む。1 回の刻みに
    両方が入るので、**0 を跨いだ瞬間に加速側の上限が緩まない**。

    accel / decel に 0 以下を渡すとその側は制限しない (段差のまま通す)。
    """

    def __init__(self, accel, decel):
        """
        Args:
            accel: 速さが増える向きの上限 [単位/s^2]。0 以下で制限しない。
            decel: 速さが減る向きの上限 [単位/s^2]。0 以下で制限しない。
        """
        self.accel = float(accel)
        self.decel = float(decel)
        self._value = 0.0

    @property
    def value(self):
        """最後に返した値。まだ動かしていなければ 0.0。"""
        return self._value

    def reset(self, value=0.0):
        """刻みの起点を置き直す。次の update() はここから始まる。"""
        self._value = float(value)
        return self._value

    def update(self, target, dt):
        """target へ dt 秒ぶんだけ近づけた値を返す。

        Args:
            target: 目標の速度。
            dt: 前回からの経過 [s]。0 以下なら動かさない (値はそのまま返す)。

        Returns:
            刻んだあとの速度。上限に掛からなければ target そのもの。
        """
        target = float(target)
        value = self._value
        if dt <= 0.0 or target == value:
            return value

        if value * target < 0.0:
            # 前後の入れ替え。0 までにかかる時間を先に引く。
            to_zero = abs(value) / self.decel if self.decel > 0.0 else 0.0
            if to_zero >= dt:
                value = _approach(value, 0.0, _budget(self.decel, dt))
            else:
                value = _approach(0.0, target, _budget(self.accel, dt - to_zero))
        elif abs(target) >= abs(value):
            value = _approach(value, target, _budget(self.accel, dt))
        else:
            value = _approach(value, target, _budget(self.decel, dt))

        self._value = value
        return value


def _budget(rate, dt):
    """この刻みで動かしてよい量。rate が 0 以下なら制限しない。"""
    return rate * dt if rate > 0.0 else float("inf")


def pressed(buttons, index):
    """buttons[index] が押されているか。範囲外なら False。

    sensor_msgs/Joy の buttons の長さはドライバと機種で変わる (同じパッドでも
    XInput と DirectInput で変わる)。範囲外を例外にすると、ボタンの割り当てを
    間違えただけでノードが死ぬ。
    """
    return 0 <= index < len(buttons) and bool(buttons[index])


def axis(axes, index):
    """axes[index] を返す。範囲外なら 0.0 (pressed と同じ理由)。"""
    return float(axes[index]) if 0 <= index < len(axes) else 0.0
