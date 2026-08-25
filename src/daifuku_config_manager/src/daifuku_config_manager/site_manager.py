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

"""今どこで走らせるか (src/daifuku_config/site) を ROS から見えるようにするノード。

**このノードは何も立て直さない。** やるのは 3 つだけ:

  1. `site` パラメータで場所を受ける。**書く前に検査する** (params.precheck) ので、
     `ros2 param set` は綴り違いや壊れた overrides を**ファイルに残さず**断る
  2. src/daifuku_config/site を書く (名前 1 語。temp + rename で書きかけを読ませない)
  3. 今の場所を /daifuku/site へ latch して流す

立て直すのは各 launch の config_sentinel で、こちらの出す値と自分が起動時に使った
値を見比べている。**役割を分けてあるのは、機体が走行中かどうかを知っているのは
各 launch の側だから** — ここで「書いた瞬間に落とす」と、走行中でも落ちてしまう。

人が素手で src/daifuku_config/site を直したときのために、ファイルは 2 秒ごとに読み直す
(tools/site.sh も editor も同じ経路に乗る)。

  ros2 param get /site_manager site          今どこか
  ros2 param set /site_manager site 19f      切り替える (検査して書いて流す)
  ros2 topic echo /daifuku/site              流れている値
"""

import json
import os

import rclpy
from rcl_interfaces.msg import SetParametersResult
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from std_msgs.msg import String

from . import params

# 場所の告知。**絶対名にしてある** — namespace:= を付けた構成でも、機体側と
# 自律移動側が同じ 1 本を見なければ意味が無い。
SITE_TOPIC = "/daifuku/site"

# 立ち上がりが前後しても取りこぼさないよう latch する (config_sentinel は
# あとから上がってくる)。
LATCHED = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)


def validate(site):
    """その場所に切り替えてよいかを、**両方のパッケージについて**確かめる。

    片方だけ見ると、機体は上がるのに navigation が立たない (あるいはその逆の)
    場所へ切り替えられてしまう。**その状態で sentinel が launch を落とすと、
    上がり直しては同じ理由で落ちる**ので、ここで止めるのが唯一の関門になる。
    2026-08-25 に機体側の部分木が空になった (場所ごとに変わる設定は
    daifuku_stack へ移った) が、**両方見るのは変えない** — 機体側にまた置いた
    ときに黙って素通しになるのを防ぐため。

    **どちらのパッケージの部分木も必ず見る。** 設定の実体は daifuku_config に
    あるので、daifuku_stack が入っていない機械でも `daifuku_stack:` の下の綴り
    違いは見つかる (設定が各パッケージの share に居たころは、入っていない側を
    飛ばすしかなかった)。

    Returns:
        (通るか, 理由)。通るなら理由は ""。
    """
    if not params.is_site(site):
        return False, f"場所の名前になっていません: {site!r}"
    path = params.overrides_path(site)
    if not os.path.isfile(path):
        available = ", ".join(params.available_overrides()) or "(なし)"
        return False, f"そんな場所はありません: {site} ({path} が無い)。選べるのは {available}"

    for package in params.KNOWN_PACKAGES:
        ok, reason = params.precheck(site, package, params.config_root(package))
        if not ok:
            return False, f"{package}: {reason}"
    return True, ""


def write_site(path, site):
    """src/daifuku_config/site を書く。**書きかけを読ませない。**

    ファイルは名前 1 語しか持たないので書式は無い (説明は
    src/daifuku_config/README.md)。temp + rename にしてあるのは、読み手
    (params.py の import 時) が truncate の隙に空を読むと、エラーも警告も出ないまま
    _FALLBACK_SITE の場所で立ち上がるため。

    symlink は**先に実体へ解決する**。`--symlink-install` では
    install/share/daifuku_config/site が src/ への symlink なので、そこへ
    os.replace すると**symlink が普通のファイルに化けて、以降 src/ 側の変更が
    届かなくなる**。
    """
    real = os.path.realpath(path)
    tmp = os.path.join(os.path.dirname(real) or ".", ".site.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(site + "\n")
    os.replace(tmp, real)


class SiteManager(Node):
    """src/daifuku_config/site の読み書きと告知。"""

    def __init__(self):
        super().__init__("site_manager")

        default_file = params.site_file()
        self._file = self.declare_parameter("site_file", default_file).value
        self._period = self.declare_parameter("poll_period", 2.0).value

        current = params.read_site_file(os.path.realpath(self._file))
        self._site = current
        # ファイルが真実。パラメータはその写しで、**宣言のときだけは検査しない** —
        # ここで投げると機体が上がらなくなるし、壊れているなら sentinel も
        # precheck で止まるので、立て直しには進まない。
        self.declare_parameter("site", current)
        ok, reason = validate(current) if current else (False, "src/daifuku_config/site が空です")
        if ok:
            self.get_logger().info(f"site: {current} ({self._file})")
        else:
            self.get_logger().error(
                f"src/daifuku_config/site の値が使えません ({reason})。"
                "このまま流しますが、切り替えは通らないままです。"
            )

        self._pub = self.create_publisher(String, SITE_TOPIC, LATCHED)
        self._publish()

        # 自分で set_parameters するときにコールバックが再入して、同じ値をもう一度
        # 書きにいくのを避ける。
        self._syncing = False
        self.add_on_set_parameters_callback(self._on_set)
        self.create_timer(self._period, self._poll)

    def _publish(self):
        msg = String()
        msg.data = json.dumps(
            {"site": self._site, "file": os.path.realpath(self._file)},
            ensure_ascii=False, sort_keys=True,
        )
        self._pub.publish(msg)

    def _on_set(self, parameters):
        """site:= を受けて、通れば書く。

        **Humble の rclpy はこのコールバックしか持たない** (値が入る前に呼ばれる
        add_on_set_parameters_callback だけで、入った後の post 版は Humble には
        無い)。したがって書き込みは値が確定する前に起きる。**通らなければ
        ファイルには一切触らない**ので、外から見た結果は変わらない。
        """
        for param in parameters:
            if param.name != "site":
                continue
            site = (param.value or "").strip()
            if self._syncing:
                continue
            if site == self._site:
                continue
            ok, reason = validate(site)
            if not ok:
                self.get_logger().error(f"site:={site} は受け付けません: {reason}")
                return SetParametersResult(successful=False, reason=reason)
            try:
                write_site(self._file, site)
            except OSError as err:
                self.get_logger().error(f"src/daifuku_config/site を書けません: {err}")
                return SetParametersResult(successful=False, reason=str(err))
            self.get_logger().warning(
                f"site: {self._site} -> {site} (src/daifuku_config/site を書きました)")
            self._site = site
            self._publish()
        return SetParametersResult(successful=True)

    def _poll(self):
        """素手で直されたときのために読み直す。

        検査に通らない値でも**そのまま流す**。ここで握り潰すと、人が書いた値と
        流れている値が食い違ったまま黙るので、どちらも見えなくなる。立て直しの
        手前で止めるのは sentinel の precheck。
        """
        site = params.read_site_file(os.path.realpath(self._file))
        if not site or site == self._site:
            return
        ok, reason = validate(site)
        if ok:
            self.get_logger().warning(
                f"site: {self._site} -> {site} (src/daifuku_config/site が変わりました)")
        else:
            self.get_logger().error(
                f"src/daifuku_config/site が {site} になりましたが、この場所では立ちません: {reason}"
            )
        self._site = site
        self._syncing = True
        try:
            self.set_parameters([Parameter("site", Parameter.Type.STRING, site)])
        finally:
            self._syncing = False
        self._publish()


def main(args=None):
    rclpy.init(args=args)
    node = SiteManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
