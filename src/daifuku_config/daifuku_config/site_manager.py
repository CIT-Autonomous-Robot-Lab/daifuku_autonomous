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

"""任意の次回起動用 site 選択を検査・保存・告知する。

機体からは自動起動しない。実行中の固定プロファイルは変更しない。
"""

import os

import rclpy
from rcl_interfaces.msg import SetParametersResult
from rclpy.node import Node
from rclpy.parameter import Parameter
from std_msgs.msg import String

from . import params
from .site_bus import LATCHED, SITE_TOPIC, encode_site


def validate(site):
    """選択先を保存する前に、全機能の override を検査する。"""
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
    が truncate の隙に空を読まず、次回用の選択を一つの値として受け取るため。

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
        msg.data = encode_site(self._site, os.path.realpath(self._file))
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
