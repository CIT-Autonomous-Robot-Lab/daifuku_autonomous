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

"""実ノードの判定を仮想時刻で進め、再起動条件を確かめる。"""

from daifuku_config import monitoring
from daifuku_config.config_sentinel import ConfigSentinel
from daifuku_config import config_sentinel
from daifuku_config import params
from launch import LaunchContext
from nav_msgs.msg import Odometry
import pytest


class Log:

    def error(self, message):
        pass

    def warning(self, message):
        pass


@pytest.fixture
def sentinel(tmp_path, monkeypatch):
    path = tmp_path / "params.yaml"
    path.write_text("node:\n  ros__parameters:\n    value: 1\n", encoding="utf-8")
    monkeypatch.setattr(config_sentinel.time, "monotonic", lambda: 100.0)
    node = ConfigSentinel.__new__(ConfigSentinel)
    node._package = "daifuku_robot"
    node._config_root = str(tmp_path)
    node._packages = None
    node._files = []
    node._site = ""
    node._watch_site = False
    node._follow = False
    node._announced = ""
    node._digest = monitoring.config_digest("", node._package, str(tmp_path))
    node._action = "shutdown"
    node._no_manager_said = False
    node._started = 0.0
    node._min_uptime = 0.0
    node._repeat = 0.0
    node._said_at = 0.0
    node._require_still = True
    node._still_speed = 0.02
    node._still_yaw_rate = 0.05
    node._still_seconds = 3.0
    node._odom_grace = 10.0
    node._last_odom = None
    node._last_moving = None
    node.exit_requested = False
    monkeypatch.setattr(node, "get_logger", lambda: Log())
    return node, path


def test_fixed_profile_file_change_requests_restart(sentinel):
    node, path = sentinel
    path.write_text("node:\n  ros__parameters:\n    value: 2\n", encoding="utf-8")
    node._poll()
    assert node.exit_requested


def test_fixed_profile_ignores_new_site_announcement(sentinel):
    node, _path = sentinel
    node._announced = "other"
    node._poll()
    assert not node.exit_requested


def test_moving_robot_waits_until_stationary(sentinel, monkeypatch):
    node, path = sentinel
    path.write_text("node:\n  ros__parameters:\n    value: 2\n", encoding="utf-8")
    odom = Odometry()
    odom.twist.twist.linear.x = 0.3
    node._on_odom(odom)
    node._poll()
    assert not node.exit_requested
    monkeypatch.setattr(config_sentinel.time, "monotonic", lambda: 104.0)
    node._poll()
    assert node.exit_requested


def test_invalid_selected_file_prevents_restart(sentinel):
    node, path = sentinel
    path.write_text("node: [", encoding="utf-8")
    node._poll()
    assert not node.exit_requested


def test_warn_only_does_not_restart(sentinel):
    node, path = sentinel
    node._action = "warn"
    path.write_text("node:\n  ros__parameters:\n    value: 2\n", encoding="utf-8")
    node._poll()
    assert not node.exit_requested


def test_reusable_defaults_ignore_site_and_environment(monkeypatch):
    monkeypatch.setenv("OVERRIDES", "another_site")
    action = params.declare_args()[0]
    assert "".join(part.text for part in action.default_value) == "none"


def test_site_entry_can_set_a_fixed_default():
    action = params.declare_args(default_overrides="tsukuba2025")[0]
    assert "".join(part.text for part in action.default_value) == "tsukuba2025"


def test_composition_applies_only_owned_overlays_and_replaces_lists(tmp_path, monkeypatch):
    from daifuku_config import paths
    from daifuku_config.overlay import load

    root = tmp_path / "navigation"
    root.mkdir()
    base = root / "planner.yaml"
    base.write_text(
        "vi_planner:\n  ros__parameters:\n    actions: [1, 2]\n    speed: 1\n",
        encoding="utf-8",
    )
    site = tmp_path / "sites" / "selected"
    site.mkdir(parents=True)
    (site / "overrides.yaml").write_text(
        "daifuku_navigation:\n  vi_planner:\n    ros__parameters:\n"
        "      actions: [3]\n      speed: 2\n"
        "daifuku_mapping:\n  slam_toolbox:\n    ros__parameters:\n      speed: 99\n",
        encoding="utf-8",
    )
    extra = tmp_path / "extra.yaml"
    extra.write_text(
        "daifuku_navigation:\n  vi_planner:\n    ros__parameters:\n      speed: 4\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "_share", lambda: str(tmp_path))
    context = LaunchContext()
    context.launch_configurations.update(overrides="selected", extra_params_file=str(extra))
    result, actions = params.compose_path(
        context, str(base), name="planner", package="daifuku_navigation", config_root=str(root),
    )
    try:
        assert load(result) == {"vi_planner": {
            "ros__parameters": {"actions": [3], "speed": 4},
        }}
        assert actions
    finally:
        from pathlib import Path

        Path(result).unlink()


def test_top_level_gets_exactly_one_sentinel(tmp_path):
    context = LaunchContext()
    context.launch_configurations.update(
        overrides="none", extra_params_file="", config_watch="shutdown",
    )
    actions = params.sentinel_actions(
        context, package="daifuku_robot", config_root=str(tmp_path),
        packages=["daifuku_robot"], files=[], watch_site=False,
    )
    from launch_ros.actions import Node

    assert sum(isinstance(action, Node) for action in actions) == 1
    with pytest.raises(RuntimeError):
        params.sentinel_actions(
            context, package="daifuku_robot", config_root=str(tmp_path),
        )
