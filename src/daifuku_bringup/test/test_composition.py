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

"""公開入口が場所と機能の接続を決める。"""

from daifuku_bringup_launch import composition
from daifuku_config import params
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, GroupAction, IncludeLaunchDescription
from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions
import pytest


def test_fixed_site_rejects_another_override():
    # Given: a caller attempts to change a fixed site through launch arguments.
    context = LaunchContext()
    context.launch_configurations.update({"overrides": "tsukuba2026", "extra_params_file": ""})
    # When/Then: the entry refuses it before composing or launching nodes.
    with pytest.raises(RuntimeError, match="site=19f"):
        composition.validate_site(context, site="19f")


def test_fixed_site_ignores_global_site_and_environment(monkeypatch, tmp_path):
    # Given: the global default names a different location.
    monkeypatch.setenv("OVERRIDES", "tsukuba2026")
    monkeypatch.setattr(params, "config_root", lambda package: str(tmp_path))
    monkeypatch.setattr(composition, "get_package_share_directory", lambda package: str(tmp_path))
    # When: the 19F public entry declares its arguments.
    context = LaunchContext()
    for action in composition.generate_navigation("19f").entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    # Then: site identity comes from the entry, not either global selector.
    assert context.launch_configurations["overrides"] == "19f"


def test_dual_scan_uses_isolated_topics_and_localization_frame(monkeypatch, tmp_path):
    # Given: a Tsukuba composition with its two explicit source profiles.
    monkeypatch.setattr(params, "config_root", lambda package: str(tmp_path))
    monkeypatch.setattr(params, "site_meta", lambda context: {})
    monkeypatch.setattr(composition, "get_package_share_directory", lambda package: str(tmp_path))
    context = LaunchContext()
    for action in composition.generate_navigation("tsukuba2025").entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    # When: the composition builds include actions, before any process is started.
    actions = composition._prepare(context, site="tsukuba2025")
    includes = [child for action in actions if isinstance(action, GroupAction)
                for child in action.get_sub_entities()
                if isinstance(child, IncludeLaunchDescription)]
    bundles = [{
        name: perform_substitutions(context, normalize_to_list_of_substitutions(value))
        for name, value in include.launch_arguments
    } for include in includes]
    scan = next(arguments for arguments in bundles if arguments.get("scan_topic") == "/scan_loc")
    # Then: localization keeps the leveled projection and distinct intermediate topics.
    assert scan["projection_name"] == "pointcloud_to_laserscan_loc"
    assert scan["base_frame"] == "localization_gravity"
    assert scan["scan_raw_topic"] == "/scan_loc_raw"
    assert scan["filtered_cloud_topic"] == "/livox/lidar_localization"
    assert scan["restamp_scan"] == "false"
