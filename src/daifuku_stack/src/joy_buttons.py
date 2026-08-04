#!/usr/bin/env python3

"""ゲームパッドの長押し判定と、スティック -> 速度の写像と、モードを伝える音。

joy_teleop.py から import される。ここに rclpy を持ち込まないのは、ここが
joy_teleop.py で唯一「実機に載せる前に確かめられる」部分だからである。ROS も
ジョイスティックもブザーも要らないので、開発ホスト (Windows) で素の python から
呼んで検算できる。

長押しの判定を分けてあるのは、START 単独 3 秒 (teleop 切り替え) と START+BACK
同時 3 秒 (ウェイポイント走行開始) が**必ず重なる**ため。素直にボタンごとの
タイマーを置くと、同時押しの途中で単独側が先に発火してモードが裏返る。
"""

# HoldLatch.update() の戻り値。
TOGGLE = "toggle"
COMBO = "combo"

# モードを伝える旋律。(周波数 [Hz], 長さ [s]) の並びで、0 は無音。
#
# 長押しは 3 秒経つまで何も起きないうえ、切り替わった先はスティックを倒すまで
# 見分けが付かない。手元にノート PC が無いときはログも見えないので、切り替わった
# ことと切り替わった先を音だけで区別できるようにしてある。
#
# 動くほうは向きで区別する — 上がりが「入った・走り切った」、下がりが「切れた」。
# 同じ高さを繰り返すほうはリズムで区別する — 短く 3 回が「始まった」、低く長く
# 2 回が「効かなかった」。無音を挟んであるのは、同じ周波数を続けて出しても切れ目が
# 聞こえないため (ドライバ側は周波数を保持するだけで、音は途切れない)。
#
# **全部 1175〜2093 Hz に収めてある。** 実機 (Pi 5 + Raspberry Pi Cat の圧電ブザー) で
# 600〜2800 Hz を 8 段に掃引したところ、周囲の雑音の中で聞き取りやすいのは
# 1200〜2100 Hz だった (2026-08-04)。低い音は圧電では鳴りにくく、3 kHz より上は
# 逆に耳へ刺さるので、否定側も帯の下端で止めて**高さでは区別していない**。
# ドライバの上限は buzzer_max_frequency (既定 5000 Hz)。
TUNE_TELEOP_ON = ((1397, 0.08), (1976, 0.14))            # ピロリ↑
TUNE_TELEOP_OFF = ((1976, 0.08), (1397, 0.14))           # ピロリ↓
TUNE_WAYPOINTS = (                                       # ピピピ (短く 3 回)
    (2093, 0.07), (0, 0.06), (2093, 0.07), (0, 0.06), (2093, 0.16),
)
TUNE_FINISHED = ((1397, 0.10), (1760, 0.10), (2093, 0.22))   # ピロリロ↑
TUNE_REFUSED = ((1175, 0.14), (0, 0.07), (1175, 0.26))       # ブッブー (低く長く 2 回)


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
