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

"""自分の launch が起動時に読んだ設定が、その後書き変わっていないかを見張るノード。

**このノードが塞ぐのは「直したのに立て直し忘れる」穴。** ROS 2 のパラメータは
ノードが起動時に読んで終わりなので、`overrides/*.yaml` を直しても、走っている
ノードには何も届かない — エラーも警告も出ないまま、前の値で走り続ける。

見ているのは 2 つ。

  * **設定値** … params.config_digest。自分のパッケージの configs/ 全体と、
    overrides の自分の部分木。**正規化してから指紋を取る**ので、コメントを
    直しただけでは反応しない
  * **場所** … /daifuku/site (site_manager の告知)。自分が起動時に使った名前と
    違う名前が流れてきたら、別の場所へ切り替わったということ

変化を見つけたら**まず大声で言う**。そのうえで、次の 4 つが揃ったときだけ
自分を終了 (SENTINEL_RESTART_CODE) して launch を落とす。落ちたあとどうなるかは
呼び元次第で、機体 (raspicat サービス) は compose の restart: unless-stopped が
上げ直し、人が立てた navigation はそのまま終わる。

  1. follow … 人が overrides:= を明示していない (明示した構成を勝手に壊さない)
  2. action:=shutdown
  3. **その設定で本当に立つ** (params.precheck)。yaml の綴り違い 1 つで
     機体が上がり直し続けるのを防ぐ、いちばん大事な条件
  4. **機体が止まっている**。走行中に落とすと、上がり直した先で
     prepare_mid360_imu がジャイロのバイアスを測れず (静止区間が要る)、
     補正なしのまま走り出す

起動直後 min_uptime_sec のあいだは落ちない (上がった直後にまた落ちる輪を作らない)。
"""

import sys
import time

import rclpy
import yaml
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from std_msgs.msg import String

from . import params

SITE_TOPIC = "/daifuku/site"

LATCHED = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)


class ConfigSentinel(Node):
    """起動時の設定と今の設定を見比べる。"""

    def __init__(self):
        super().__init__("config_sentinel")

        self._package = self.declare_parameter("package", "").value
        self._config_root = self.declare_parameter("config_root", "").value
        self._site = self.declare_parameter("site", "").value
        self._digest = self.declare_parameter("digest", "").value
        # 人が overrides:= を明示した launch では false。**言うだけで落とさない。**
        self._follow = self.declare_parameter("follow", False).value
        # warn にすると、追随できる構成でも言うだけになる (段階 1 相当)。
        self._action = self.declare_parameter("action", "shutdown").value

        self._period = self.declare_parameter("poll_period", 2.0).value
        # 上がった直後に落ちる輪を作らないための猶予。**この長さのあいだは
        # どんな変化でも落ちない。**
        self._min_uptime = self.declare_parameter("min_uptime_sec", 30.0).value
        self._repeat = self.declare_parameter("repeat_sec", 30.0).value

        # 静止の判定。ジャイロのバイアスを起動後の静止区間から測る都合で、
        # **走行中に立て直してはいけない** (docs/setup/lidar.md)。
        self._require_still = self.declare_parameter("require_still", True).value
        self._still_speed = self.declare_parameter("still_speed", 0.02).value
        self._still_yaw_rate = self.declare_parameter("still_yaw_rate", 0.05).value
        self._still_seconds = self.declare_parameter("still_seconds", 3.0).value
        odom_topic = self.declare_parameter("odom_topic", "/odom").value
        # /odom が来ないまま待ち続けると、切り替えが黙って効かないのと同じになる。
        # この時間を過ぎたら「判定できないので止まっているものとして扱う」。
        self._odom_grace = self.declare_parameter("odom_grace_sec", 10.0).value

        self._started = time.monotonic()
        self._last_moving = None
        self._last_odom = None
        self._announced = ""
        self._said_at = 0.0
        self._no_manager_said = False
        self.exit_requested = False

        if not self._package or not self._config_root:
            raise RuntimeError("package と config_root は必須です")

        self.create_subscription(String, SITE_TOPIC, self._on_site, LATCHED)
        self.create_subscription(Odometry, odom_topic, self._on_odom, 10)
        self.create_timer(self._period, self._poll)

        self.get_logger().info(
            f"config_sentinel: {self._package} / site={self._site or '(なし)'} / "
            f"digest={self._digest} / "
            + ("設定が変わったら立て直します" if self._following() else
               "変化は言うだけで、立て直しはしません")
        )

    # ── 入力 ──────────────────────────────────────────────────────────────

    def _on_site(self, msg):
        try:
            self._announced = (yaml.safe_load(msg.data) or {}).get("site", "")
        except yaml.YAMLError:
            # JSON は YAML の部分集合なので safe_load で読める。読めないものが
            # 流れてきたら告知の側の問題なので、こちらは黙って前の値を保つ。
            self.get_logger().warning(f"{SITE_TOPIC} を読めません: {msg.data!r}")

    def _on_odom(self, msg):
        now = time.monotonic()
        self._last_odom = now
        twist = msg.twist.twist
        fast = (
            abs(twist.linear.x) > self._still_speed
            or abs(twist.linear.y) > self._still_speed
            or abs(twist.angular.z) > self._still_yaw_rate
        )
        if fast:
            self._last_moving = now

    # ── 判定 ──────────────────────────────────────────────────────────────

    def _following(self):
        return self._follow and self._action == "shutdown"

    def _still(self):
        """機体が止まっているか。

        /odom が一度も来ない (あるいは途絶えた) ときは**止まっているものとして
        扱う**。ここで待ち続けると、オドメトリが死んでいるだけで切り替えが
        永久に効かなくなり、しかもその理由がどこにも出ない。
        """
        now = time.monotonic()
        if self._last_odom is None or now - self._last_odom > self._odom_grace:
            return True, "/odom が来ていないので静止として扱います"
        if self._last_moving is None:
            return True, ""
        waited = now - self._last_moving
        if waited >= self._still_seconds:
            return True, ""
        return False, f"機体が動いています (静止 {waited:.1f}s / {self._still_seconds}s)"

    def _changes(self):
        """起動時から変わったものを並べる。

        Returns:
            (理由の並び, 切り替え先の場所)。壊れて読めないときは
            ("壊れている" 旨の理由, None) を返し、**立て直しには進ませない**。
        """
        reasons, target = [], self._site

        if self._announced and self._announced != self._site:
            reasons.append(f"場所が変わりました: {self._site or '(なし)'} -> {self._announced}")
            target = self._announced

        try:
            now = params.config_digest(self._site, self._package, self._config_root)
        except (OSError, yaml.YAMLError) as err:
            # 書きかけを読んだだけかもしれないので、これを立て直しの理由にしない。
            return [f"設定ファイルを読めません: {err}"], None
        if now != self._digest:
            reasons.append(f"設定値が書き変わりました (digest {self._digest} -> {now})")

        return reasons, target

    # ── 本体 ──────────────────────────────────────────────────────────────

    def _say(self, reasons, tail):
        """同じことを毎周期は言わない (repeat_sec ごと)。"""
        now = time.monotonic()
        if now - self._said_at < self._repeat:
            return
        self._said_at = now
        self.get_logger().error(
            f"起動したときの設定と今の設定が違います ({self._package}):\n  "
            + "\n  ".join(reasons) + "\n" + tail
        )

    def _poll(self):
        if self.exit_requested:
            return

        if (not self._announced and not self._no_manager_said
                and time.monotonic() - self._started > 60.0):
            self._no_manager_said = True
            self.get_logger().warning(
                f"{SITE_TOPIC} に誰も出していません。site_manager が居ないので、"
                "**場所の切り替えは検出できません** (設定値の書き換えは見ています)。"
            )

        reasons, target = self._changes()
        if not reasons:
            return

        if target is None:
            self._say(reasons, "壊れているあいだは何もしません (直れば続きから見ます)。")
            return

        if not self._following():
            why = ("overrides:= を明示して立てたので追随しません"
                   if not self._follow else "action:=warn なので落としません")
            self._say(reasons, f"{why}。反映するには立て直してください。")
            return

        ok, reason = params.precheck(target, self._package, self._config_root)
        if not ok:
            self._say(reasons, f"**この設定では立ち上がらないので、立て直しません**: {reason}")
            return

        uptime = time.monotonic() - self._started
        if uptime < self._min_uptime:
            self._say(reasons, f"起動直後なので待ちます ({uptime:.0f}s / {self._min_uptime:.0f}s)。")
            return

        if self._require_still:
            still, note = self._still()
            if not still:
                self._say(reasons, f"{note}。止まったら立て直します。")
                return
            if note:
                self.get_logger().warning(note)

        self.get_logger().warning(
            "設定が変わったので立て直します:\n  " + "\n  ".join(reasons)
        )
        self.exit_requested = True


def main(args=None):
    rclpy.init(args=args)
    node = ConfigSentinel()
    try:
        while rclpy.ok() and not node.exit_requested:
            rclpy.spin_once(node, timeout_sec=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        requested = node.exit_requested
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    # **この終了コードちょうどのときだけ** launch が落ちる (params.py の
    # _on_sentinel_exit)。0 で落とすと、このノードがバグで終わっただけでも
    # 機体が上がり直してしまう。
    if requested:
        sys.exit(params.SENTINEL_RESTART_CODE)


if __name__ == "__main__":
    main()
