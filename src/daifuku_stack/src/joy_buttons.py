#!/usr/bin/env python3

"""ゲームパッドの長押し判定と、スティック -> 速度の写像。

joy_teleop.py から import される。ここに rclpy を持ち込まないのは、この 2 つが
joy_teleop.py で唯一「実機に載せる前に確かめられる」部分だからである。ROS も
ジョイスティックも要らないので、開発ホスト (Windows) で素の python から
呼んで検算できる。

長押しの判定を分けてあるのは、START 単独 3 秒 (teleop 切り替え) と START+BACK
同時 3 秒 (ウェイポイント走行開始) が**必ず重なる**ため。素直にボタンごとの
タイマーを置くと、同時押しの途中で単独側が先に発火してモードが裏返る。
"""

# HoldLatch.update() の戻り値。
TOGGLE = "toggle"
COMBO = "combo"


class HoldLatch:
    """2 つのボタンの「単独長押し」と「同時長押し」を取り違えずに拾う。

    毎周期 update() を呼ぶ。判定は押した瞬間ではなく **3 秒経った時点で押されて
    いる組** で決めるので、単独長押しのつもりでも 3 秒目に副ボタンが入っていれば
    同時押しのほうが返る。

    2 つの規則で誤発火を防いでいる:

      * 一度返したら**両方を離すまで**二度と返さない。押しっぱなしにしても
        3 秒ごとに再発火しない。
      * 主ボタンを押しているあいだに副ボタンが一度でも入ったら、その主ボタンを
        離すまで単独長押しは返さない。同時押しをやりかけて途中でやめたとき
        (副ボタンだけ先に離したとき) にモードが裏返らないようにするため。
    """

    def __init__(self, hold_seconds):
        self.hold_seconds = hold_seconds
        self._main_since = None
        self._sub_since = None
        self._latched = False
        self._poisoned = False

    def reset(self):
        """押下の履歴を捨てる。

        ジョイスティックの受信が途切れたら呼ぶ。最後に見えたボタンの状態を
        押しっぱなしとして数え続けると、電池切れや受信機の抜けが 3 秒後の
        モード切り替えになって現れる。
        """
        self._main_since = None
        self._sub_since = None
        self._latched = False
        self._poisoned = False

    def update(self, now, main, sub):
        """今のボタンの状態を渡し、成立した長押しを返す。

        Args:
            now: 単調増加の秒 (time.monotonic())。壁時計を渡さないこと。
            main: 主ボタン (START) が押されているか。
            sub: 副ボタン (BACK) が押されているか。

        Returns:
            TOGGLE / COMBO / None。
        """
        if main:
            if self._main_since is None:
                self._main_since = now
        else:
            self._main_since = None
            self._poisoned = False

        if sub:
            if self._sub_since is None:
                self._sub_since = now
            if main:
                self._poisoned = True
        else:
            self._sub_since = None

        if not main and not sub:
            self._latched = False

        if self._latched or not main:
            return None

        if sub:
            # 同時押しの経過は「あとから押されたほう」から数える。
            since = max(self._main_since, self._sub_since)
            if now - since >= self.hold_seconds:
                self._latched = True
                return COMBO
            return None

        if self._poisoned:
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
