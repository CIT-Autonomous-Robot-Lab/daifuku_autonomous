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
"""実際の launch action から自己位置推定の所有者と入力を確認する。"""

import importlib.util
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchContext
from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions
from launch_ros.actions import Node
import pytest


@pytest.fixture
def component():
    source = Path(get_package_share_directory("daifuku_localization")) / (
        "launch/localization.launch.py"
    )
    specification = importlib.util.spec_from_file_location("localization_component", source)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_vi_localization_adds_no_second_estimator(component):
    # Given: VI navigation already owns map->odom.
    context = LaunchContext()
    context.launch_configurations["localization"] = "vi"
    # When: the localization component selects its nodes.
    nodes = component.localization_nodes(context)
    # Then: it neither duplicates VI nor introduces another estimator/map server.
    assert nodes == []


@pytest.mark.parametrize("backend,estimator", [
    ("amcl", "nav2_amcl"), ("emcl2", "emcl2"), ("emcl3d", "emcl3d"),
])
def test_selected_estimator_reads_localization_scan(component, backend, estimator, tmp_path):
    # Given: distinct planning and localization scans and an empty estimator config.
    configuration = tmp_path / "parameters.yaml"
    configuration.write_text("{}\n")
    context = LaunchContext()
    context.launch_configurations.update({
        "localization": backend, "use_sim_time": "true", "use_respawn": "false",
        "log_level": "info", "autostart": "true", "map_loc": "/localization.yaml",
        "map_pcd": "/localization.pcd", "emcl2_package": "emcl2",
        "emcl2_executable": "emcl2_node", "emcl2_node_name": "emcl2",
        "localization_scan_topic": "/scan_loc",
        **{name: str(configuration) for name in (
            "amcl_params_file", "emcl2_params_file", "emcl3d_params_file",
        )},
    })
    # When: substitutions are resolved without starting any hardware process.
    nodes = component.localization_nodes(context)
    selected = next(node for node in nodes if perform_substitutions(
        context, normalize_to_list_of_substitutions(node.node_package),
    ) == estimator)
    selected._perform_substitutions(context)
    # Then: only the chosen estimator is present; 2D estimators use the dedicated scan.
    assert all(isinstance(node, Node) and node.node_package != "vi_planner" for node in nodes)
    if backend != "emcl3d":
        assert ("scan", "/scan_loc") in selected.expanded_remapping_rules
        assert ("map", "map_loc") in selected.expanded_remapping_rules
