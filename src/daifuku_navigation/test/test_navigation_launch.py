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
"""実際の launch action で VI の所有と場所固定を確認する。"""

import importlib.util
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from daifuku_config import params
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, GroupAction
from launch.utilities import perform_substitutions
from launch_ros.actions import Node
import pytest
import yaml


@pytest.mark.parametrize("explicit", [False, True])
def test_fragment_resolver_preserves_explicit_base_and_fragment_ownership(
    tmp_path, monkeypatch, explicit,
):
    from daifuku_navigation_launch import nav2_params

    root = tmp_path / "navigation"
    root.mkdir()
    (root / "vi_planner.yaml").write_text("vi_planner: {}\n", encoding="utf-8")
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "smoother.yaml").write_text("velocity_smoother: {}\n", encoding="utf-8")
    base = tmp_path / "explicit.yaml"
    base.write_text("custom_node: {}\n", encoding="utf-8")
    monkeypatch.setattr(nav2_params, "config_root", lambda package: str(root))
    context = LaunchContext()
    context.launch_configurations.update({
        "params_dir": str(custom), "params_file": str(base) if explicit else "",
    })

    body, owned, _origin, always = nav2_params.fragments_resolver(context)

    assert body == ({"custom_node": {}} if explicit else {
        "velocity_smoother": {}, "vi_planner": {},
    })
    assert owned == {"velocity_smoother", "vi_planner"}
    assert always is not explicit


def _load(source):
    specification = importlib.util.spec_from_file_location(source.stem, source)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.mark.parametrize("backend,publish_tf,pose_topic", [
    ("vi", True, "initialpose"), ("emcl2", False, "mcl_pose"),
    ("amcl", False, "amcl_pose"),
])
def test_navigation_has_one_vi_with_selected_tf_owner(
    monkeypatch, tmp_path, backend, publish_tf, pose_topic,
):
    # Given: the planner reads a real parameter file without an estimator process.
    root = Path(get_package_share_directory("daifuku_navigation"))
    module = _load(root / "launch/navigation.launch.py")
    monkeypatch.setattr(params, "config_root", lambda package: str(tmp_path))
    source = tmp_path / "parameters.yaml"
    source.write_text("vi_planner:\n  ros__parameters:\n    localizer: external\n")
    context = LaunchContext()
    context.launch_configurations.update({
        "localization": backend, "params_file": str(source), "map": "/planning.yaml",
    })
    description = module.generate_launch_description()
    for action in description.entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    group = next(action for action in description.entities if isinstance(action, GroupAction))
    nodes = [action for action in group.get_sub_entities() if isinstance(action, Node)]
    # When: the one VI node resolves its parameters, without starting a process.
    planners = [node for node in nodes if node.node_package == "vi_planner"]
    assert len(planners) == 1
    planner = planners[0]
    planner._perform_substitutions(context)
    context.extend_locals({"ros_specific_arguments": {
        "name": "__node:=vi_planner", "ns": "__ns:=/",
    }})
    arguments = [perform_substitutions(context, arg) for arg in planner.cmd[1:]]
    files = [arguments[index + 1] for index, arg in enumerate(arguments)
             if arg == "--params-file"]
    # Then: launch owns TF and seed/pose routing, overriding config as one unit.
    document = yaml.safe_load(Path(files[-1]).read_text())
    assert len(document) == 1
    parameters = next(iter(document.values()))["ros__parameters"]
    assert parameters["publish_tf"] is publish_tf
    assert parameters["pose_topic"] == pose_topic
    assert parameters["standalone"] is True
    assert parameters["follow"] is True
