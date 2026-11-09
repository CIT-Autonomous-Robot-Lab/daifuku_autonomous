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

from pathlib import Path

from daifuku_bringup_launch import composition, navigation_args
from daifuku_config import params
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, GroupAction, IncludeLaunchDescription
from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions
import pytest
import rclpy
import yaml


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
    monkeypatch.setattr(
        navigation_args, "get_package_share_directory", lambda package: str(tmp_path),
    )
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
    monkeypatch.setattr(
        navigation_args, "get_package_share_directory", lambda package: str(tmp_path),
    )
    context = LaunchContext()
    for action in composition.generate_navigation("tsukuba2025").entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    # When: the composition builds include actions, before any process is started.
    actions = composition._assemble_navigation(context, site="tsukuba2025")
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


@pytest.mark.parametrize("explicit,expected", [("", "vulkan"), ("cpu", "cpu")])
def test_compute_backend_environment_and_explicit_override_reach_localization(
    monkeypatch, explicit, expected,
):
    # Given: the GPU container default and an optional explicit CPU selection.
    monkeypatch.setenv("EMCL3D_COMPUTE_BACKEND", "vulkan")
    context = LaunchContext()
    if explicit:
        context.launch_configurations["emcl3d_compute_backend"] = explicit
    for action in composition.generate_navigation("19f").entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    # When: the public entry resolves its component include.
    actions = composition._assemble_navigation(context, site="19f")
    includes = []
    pending = list(actions)
    while pending:
        action = pending.pop()
        if isinstance(action, GroupAction):
            pending.extend(action.get_sub_entities())
        elif isinstance(action, IncludeLaunchDescription):
            includes.append(action)
    arguments = [{
        name: perform_substitutions(context, normalize_to_list_of_substitutions(value))
        for name, value in include.launch_arguments
    } for include in includes]
    # Then: the environment default or explicit argument reaches the component unchanged.
    localization = next(bundle for bundle in arguments if "emcl3d_params_file" in bundle)
    assert localization["emcl3d_compute_backend"] == expected


def test_navigation_sentinel_watches_new_custom_fragments(tmp_path, monkeypatch):
    from daifuku_config.config_sentinel import ConfigSentinel
    from launch.actions import OpaqueFunction
    from launch_ros.actions import Node
    from launch_ros.utilities import evaluate_parameters

    source = Path(__file__).resolve().parents[2]
    for module in (composition, navigation_args):
        monkeypatch.setattr(module, "get_package_share_directory",
                            lambda package: str(source / package))
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "smoother.yaml").write_text("velocity_smoother: {}\n", encoding="utf-8")
    context = LaunchContext()
    for action in composition.generate_navigation(None).entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations["params_dir"] = str(custom)
    actions = composition._assemble_navigation(context, site=None)
    watch = next(action for action in actions if isinstance(action, OpaqueFunction)
                 and action._OpaqueFunction__function == params.sentinel_actions)
    sentinel_action = next(action for action in watch.execute(context) if isinstance(action, Node))
    parameters = evaluate_parameters(context, sentinel_action._Node__parameters)[0]
    parameter_file = tmp_path / "sentinel.yaml"
    parameter_file.write_text(yaml.safe_dump({
        "config_sentinel": {"ros__parameters": parameters},
    }), encoding="utf-8")
    rclpy.init(args=["--ros-args", "--params-file", str(parameter_file)])
    node = None
    try:
        node = ConfigSentinel()
        (custom / "added.yaml").write_text("velocity_smoother: {}\n", encoding="utf-8")
        node._min_uptime = 0.0
        node._poll()
        assert not node.exit_requested
        (custom / "added.yaml").write_text("new_node: {}\n", encoding="utf-8")
        node._poll()
        assert node.exit_requested
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize("site", [None, *params.available_overrides()])
@pytest.mark.parametrize("use_sim_time", ["true", "false"])
@pytest.mark.parametrize("driver_default", ["true", "false"])
def test_scan_defaults_follow_clock_and_driver_selection(
    monkeypatch, site, use_sim_time, driver_default,
):
    # Given: a selected site, environment defaults and an explicit clock argument.
    monkeypatch.setenv("LIDAR", "urg")
    monkeypatch.setenv("LIDAR_DRIVER", driver_default)
    context = LaunchContext()
    context.launch_configurations["use_sim_time"] = use_sim_time
    # When: declarations evaluate their dependencies in launch order.
    for action in composition.generate_navigation(site).entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    selected = context.launch_configurations
    # Then: split scans keep the leveled frame and disable default drivers in simulation.
    separate_scan = site == "tsukuba2025"
    driver_enabled = driver_default == "true" and not (
        separate_scan and use_sim_time == "true"
    )
    assert selected["lidar"] == ("mid360" if separate_scan else "urg")
    assert (selected["lidar_driver"].lower() == "true") == driver_enabled
    assert (selected["leveling"] == "true") == separate_scan
    assert (selected["use_system_monitor"] == "true") == (not separate_scan)
    assert (selected["restamp_cloud"].lower() == "true") == (
        separate_scan and driver_enabled and use_sim_time == "false"
    )
