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

"""スキャンを受け取って地図を作る。機体・スキャン生成・監視は呼び元が所有する。"""

import os

from daifuku_config import params
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    config_root = params.config_root("daifuku_mapping")
    return LaunchDescription([
        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument(
            "slam_params_file",
            default_value=os.path.join(config_root, "slam_toolbox.yaml"),
        ),
        *params.declare_args(),
        OpaqueFunction(
            function=params.compose,
            kwargs={
                "package": "daifuku_mapping",
                "config_root": config_root,
                "targets": ["slam_params_file"],
            },
        ),
        Node(
            package="slam_toolbox",
            executable="async_slam_toolbox_node",
            name="slam_toolbox",
            namespace=LaunchConfiguration("namespace"),
            output="screen",
            parameters=[
                LaunchConfiguration("slam_params_file"),
                {"use_sim_time": LaunchConfiguration("use_sim_time")},
            ],
        ),
    ])
