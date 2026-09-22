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

"""site_manager が場所の切り替えをどう断り、どう告知するかを確かめる。

launch_testing の統合テスト (`colcon test` が拾う。実機も Docker も要らない)。
見るのは ROS の口を通したときにしか出てこない 3 つ:

  1. `/daifuku/site` は **latch** されている — ノードが上がったあとに購読しても
     取りこぼさない (config_sentinel はあとから上がってくる)
  2. **通らない場所はファイルに残さない** — `ros2 param set` を断る。ここが
     唯一の関門で、抜けると「上がっては同じ理由で落ちる」輪になる
  3. 通る場所なら site ファイルを書き、告知もその値へ動く

site ファイルは temp の写しを渡す (`site_file` パラメータ)。リポジトリの
src/daifuku_config/site を書き換えると、走っている機体や隣の作業を巻き込む。
"""

import json
import os
import subprocess
import tempfile
import time
import unittest

from daifuku_config_manager import params
from daifuku_config_manager.site_manager import LATCHED, SITE_TOPIC
import launch
from launch.actions import SetEnvironmentVariable
from launch_ros.actions import Node
import launch_testing
import launch_testing.actions
import launch_testing.asserts
import pytest
import rclpy
from std_msgs.msg import String

# test_config_sentinel_launch.py と同じ理由 (実機の上で回しても本物と混ざらない)。
TEST_DOMAIN_ID = "77"


@pytest.mark.launch_test
def generate_test_description():
    start = params.read_site_file(params.site_file())
    others = [name for name in params.available_overrides() if name != start]
    assert others, f"overrides/ に {start} 以外の場所が無いので切り替えを試せない"

    site_file = os.path.join(tempfile.mkdtemp(prefix="daifuku_site_"), "site")
    with open(site_file, "w", encoding="utf-8") as f:
        f.write(start + "\n")

    manager = Node(
        package="daifuku_config_manager",
        executable="site_manager",
        output="screen",
        parameters=[{"site_file": site_file, "poll_period": 0.5}],
    )
    return (
        launch.LaunchDescription([
            SetEnvironmentVariable("ROS_DOMAIN_ID", TEST_DOMAIN_ID),
            SetEnvironmentVariable("ROS_LOCALHOST_ONLY", "1"),
            manager,
            launch_testing.actions.ReadyToTest(),
        ]),
        {
            "manager": manager,
            "site_file": site_file,
            "start_site": start,
            "other_site": others[0],
        },
    )


def _param_set(value):
    """`ros2 param set /site_manager site <value>` が通ったか。"""
    done = subprocess.run(
        ["ros2", "param", "set", "/site_manager", "site", value],
        capture_output=True, text=True, timeout=60,
    )
    return "successful" in done.stdout


def _spin_until(node, ready, timeout, what):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        rclpy.spin_once(node, timeout_sec=0.2)
        if ready():
            return
    raise AssertionError(f"{timeout}s 待っても {what}")


class TestSiteManager(unittest.TestCase):

    def test_announces_and_guards_the_site(self, site_file, start_site, other_site):
        rclpy.init()
        try:
            node = rclpy.create_node("site_manager_launch_test")

            # **告知を見つけてから購読する。** site_manager が出すのは
            # __init__ の 1 回だけなので、先に購読してしまうと latch が効いて
            # いなくても受け取れてしまい、試験にならない。
            _spin_until(
                node, lambda: node.count_publishers(SITE_TOPIC) > 0,
                30, f"{SITE_TOPIC} の出し手が見つからない",
            )
            heard = []
            node.create_subscription(String, SITE_TOPIC, heard.append, LATCHED)

            # 出したあとに購読した。latch していなければここで詰まる。
            _spin_until(node, lambda: heard, 30, f"{SITE_TOPIC} に何も来ない")
            self.assertEqual(json.loads(heard[-1].data)["site"], start_site)

            # 綴り違いは断る。**断ったならファイルにも残っていない。**
            self.assertFalse(_param_set("nowhere_like_this"))
            self.assertEqual(params.read_site_file(site_file), start_site)

            # 実在する場所なら書いて、告知もそちらへ動く。
            self.assertTrue(_param_set(other_site))
            self.assertEqual(params.read_site_file(site_file), other_site)
            _spin_until(
                node,
                lambda: json.loads(heard[-1].data)["site"] == other_site,
                30, f"{SITE_TOPIC} が {other_site} にならない",
            )
        finally:
            rclpy.shutdown()


@launch_testing.post_shutdown_test()
class TestSiteManagerExitCode(unittest.TestCase):

    def test_exits_cleanly(self, proc_info, manager):
        launch_testing.asserts.assertExitCodes(
            proc_info,
            [launch_testing.asserts.EXIT_OK, -2, -15],
            process=manager,
        )
