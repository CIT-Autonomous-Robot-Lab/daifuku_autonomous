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

"""スキャン生成と SLAM を組み合わせる、地図作成の公開入口。"""

import os

from ament_index_python.packages import get_package_share_directory
from daifuku_config import params, value
from daifuku_perception_launch import scan
from launch import LaunchContext, LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
    OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _assemble(context: LaunchContext):
    """監視対象は合成前の入力ファイル。子 launch の一時ファイルは監視しない。"""
    slam_file = value(context, "slam_params_file")
    if not os.path.isfile(slam_file):
        raise RuntimeError(f"slam_params_file does not exist: {slam_file}")
    watched = [slam_file]
    watched.extend(
        value(context, name)
        for name in (
            "mid360_elevation_params_file",
            "mid360_scan_params_file",
            "scan_filter_params_file",
        )
        if value(context, name)
    )
    watched.extend(
        path.strip() for path in value(context, "extra_params_file").split(",")
        if path.strip()
    )
    mapping_share = get_package_share_directory("daifuku_mapping")
    perception_share = get_package_share_directory("daifuku_perception")
    names = ("namespace", "use_sim_time", "slam_params_file", "overrides", "extra_params_file")
    return [
        *params.sentinel_actions(
            context,
            package="daifuku_mapping",
            config_root=params.config_root("daifuku_mapping"),
            packages=["daifuku_mapping", "daifuku_perception"],
            files=watched,
            watch_site=False,
        ),
        scan.include_scan_pipeline(perception_share),
        GroupAction([
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    os.path.join(mapping_share, "launch", "mapping.launch.py"),
                ),
                launch_arguments={name: value(context, name) for name in names}.items(),
            ),
        ]),
    ]


def generate_launch_description():
    share = get_package_share_directory("daifuku_bringup")
    return LaunchDescription([
        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("use_rviz", default_value="false"),
        DeclareLaunchArgument(
            "rviz_config", default_value=os.path.join(share, "rviz", "mapping.rviz"),
        ),
        DeclareLaunchArgument(
            "slam_params_file",
            default_value=os.path.join(
                params.config_root("daifuku_mapping"), "slam_toolbox.yaml",
            ),
        ),
        *params.declare_args(),
        params.declare_watch_arg(),
        *scan.declare_args(),
        OpaqueFunction(function=_assemble),
        Node(
            condition=IfCondition(LaunchConfiguration("use_rviz")),
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            namespace=LaunchConfiguration("namespace"),
            output="screen",
            arguments=["-d", LaunchConfiguration("rviz_config")],
            parameters=[{"use_sim_time": LaunchConfiguration("use_sim_time")}],
        ),
    ])
