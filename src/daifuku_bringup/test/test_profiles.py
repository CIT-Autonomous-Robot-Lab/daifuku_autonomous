# Copyright 2026 Keita Sekiguchi / nop
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

"""全公開プロファイルの実ファイルで設定合成と監視の配線を検査する。"""

import os

from daifuku_bringup_launch import composition
from daifuku_config import params
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, OpaqueFunction, SetLaunchConfiguration
from launch_ros.actions import Node
import pytest


@pytest.mark.parametrize("site", params.available_overrides())
def test_installed_site_composes_its_maps_and_one_watcher(site):
    # Given: installed settings/assets and the actual fixed-site entry defaults.
    context = LaunchContext()
    for action in composition.generate_navigation(site).entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    # When: real composition, map checks and fingerprint generation run without processes.
    generated = []
    for action in composition._assemble_navigation(context, site=site):
        if isinstance(action, OpaqueFunction):
            results = action.execute(context) or []
            generated.extend(results)
            for result in results:
                if isinstance(result, SetLaunchConfiguration):
                    result.execute(context)
    watchers = [
        action for action in generated
        if isinstance(action, Node) and action.node_package == "daifuku_config"
    ]
    # Then: both declared maps resolve and one top-level watcher owns the selected scope.
    assert context.launch_configurations["overrides"] == site
    assert os.path.isfile(context.launch_configurations["map"])
    assert os.path.isfile(context.launch_configurations["map_loc"])
    assert len(watchers) == 1
    assert os.path.isfile(context.launch_configurations["params_file"])
