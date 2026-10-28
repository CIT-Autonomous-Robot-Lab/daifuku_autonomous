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

"""SLAM 部品は入力スキャンを消費するだけで、機体や監視を重ねない。"""

from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions
from launch_ros.actions import Node


def test_mapping_component_only_starts_slam():
    # Given: the actual component, evaluated without starting any ROS process.
    source = Path(__file__).resolve().parents[1] / "launch" / "mapping.launch.py"
    context = LaunchContext()
    description = PythonLaunchDescriptionSource(str(source)).get_launch_description(context)
    # When: declarations and parameter composition run with their defaults.
    for action in description.entities:
        if isinstance(action, (DeclareLaunchArgument, OpaqueFunction)):
            for generated in action.execute(context) or []:
                generated.execute(context)
    nodes = [action for action in description.entities if isinstance(action, Node)]
    # Then: SLAM is the sole process; drivers, scan publishers and watchers are absent.
    assert [
        (
            perform_substitutions(
                context, normalize_to_list_of_substitutions(node.node_package),
            ),
            perform_substitutions(
                context, normalize_to_list_of_substitutions(node.node_executable),
            ),
        )
        for node in nodes
    ] == [("slam_toolbox", "async_slam_toolbox_node")]
