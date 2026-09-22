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

"""config_sentinel が設定の書き換えをどう申告するかを確かめる。

launch_testing の統合テスト (`colcon test` が拾う。実機も Docker も要らない)。
見張りが返す**終了コードは launch を落とす合図そのもの**なので、ノードを本当に
プロセスとして立てないと確かめられない。

  1. 起動時の指紋と今の設定が違えば、見張りは SENTINEL_RESTART_CODE で終わる
  2. その値でだけ launch が畳まれる (params._on_sentinel_exit)。**0 にすると
     ノードがバグで落ちただけでも機体が上がり直す**ので、ここが要点

設定ファイルは触らない。起動時の指紋 (digest) を嘘の値で渡せば「起動したときと
違う」状態が作れるので、ソースの yaml を書き換えて他のテストや作業中の
ワークスペースを巻き込む必要が無い。

**launch 木には handler を registerしない。** 本番どおり繋ぐと、見張りが終わった
瞬間に launch service が畳まれ、テストが走り出す前に終わることがある
(launch_testing は "Processes under test stopped before tests completed" で落ちる)。
見張りが死んでも launch が生き残るよう KeepAliveProc を立て、handler の側は
純粋関数として下で確かめる。
"""

import unittest

from daifuku_config_manager import params
import launch
from launch.actions import EmitEvent, SetEnvironmentVariable
from launch_ros.actions import Node
import launch_testing
import launch_testing.actions
import launch_testing.asserts
import launch_testing.util
import pytest

# 走っている機体と同じ領域に出ないようにする。config_sentinel は名前を変えられるが、
# 隣の test_site_manager_launch.py が立てる site_manager は名前が固定なので、
# 実機の上で colcon test を回すと本物と 2 つになる。
TEST_DOMAIN_ID = "77"

# 見張らせる部分木。daifuku_stack でも daifuku_bringup でもよいが、
# 指紋の対象 (config_root) が空でないほうが試験として意味がある。
PACKAGE = "daifuku_stack"


class _Exited:
    """OnProcessExit が handler へ渡すもののうち、handler が見るのは 1 つだけ。"""

    def __init__(self, returncode):
        self.returncode = returncode


@pytest.mark.launch_test
def generate_test_description():
    sentinel = Node(
        package="daifuku_config_manager",
        executable="config_sentinel",
        name="config_sentinel_test",
        output="screen",
        parameters=[{
            "package": PACKAGE,
            "config_root": params.config_root(PACKAGE),
            "site": params.read_site_file(params.site_file()),
            # わざと嘘の指紋。これで「起動時と設定が違う」状態になる。
            "digest": "stale",
            # follow (人が overrides:= を明示していない) + shutdown で初めて落ちる。
            "follow": True,
            "action": "shutdown",
            # 既定は 30 秒。そのままだとテストがその間ただ待つ。
            "min_uptime_sec": 0.0,
            "poll_period": 0.2,
            # /odom は誰も出さない。既定の 10 秒を待たずに「静止」と見なさせる
            # (_still の「来ないなら止まっている扱い」の側に入る)。
            "odom_grace_sec": 0.0,
        }],
    )
    return (
        launch.LaunchDescription([
            SetEnvironmentVariable("ROS_DOMAIN_ID", TEST_DOMAIN_ID),
            SetEnvironmentVariable("ROS_LOCALHOST_ONLY", "1"),
            sentinel,
            # 見張りが終わっても launch を畳ませない番人 (上の docstring)。
            launch_testing.util.KeepAliveProc(),
            launch_testing.actions.ReadyToTest(),
        ]),
        {"sentinel": sentinel},
    )


class TestSentinelNoticesTheChange(unittest.TestCase):

    def test_sentinel_gives_up(self, proc_info, sentinel):
        proc_info.assertWaitForShutdown(process=sentinel, timeout=30)


@launch_testing.post_shutdown_test()
class TestSentinelExitCode(unittest.TestCase):

    def test_exit_code_is_the_restart_code(self, proc_info, sentinel):
        launch_testing.asserts.assertExitCodes(
            proc_info, [params.SENTINEL_RESTART_CODE], process=sentinel,
        )

    def test_only_the_restart_code_takes_the_launch_down(self):
        # 0 だと「バグで落ちただけ」と見分けが付かず、restart: unless-stopped と
        # 組んで機体が上がり直し続ける (params.SENTINEL_RESTART_CODE のコメント)。
        self.assertNotEqual(params.SENTINEL_RESTART_CODE, 0)
        emitted = params._on_sentinel_exit(_Exited(params.SENTINEL_RESTART_CODE), None)
        self.assertTrue(any(isinstance(action, EmitEvent) for action in emitted))
        # 0 は launch 自身の停止に巻き込まれた正常終了、負値はシグナル、
        # それ以外は見張りのバグ。**どれも機体を上げ直す理由にはならない。**
        for returncode in (0, 1, 2, -2, -15):
            emitted = params._on_sentinel_exit(_Exited(returncode), None)
            self.assertFalse(
                any(isinstance(action, EmitEvent) for action in emitted),
                f"rc={returncode} で launch を落としています",
            )
