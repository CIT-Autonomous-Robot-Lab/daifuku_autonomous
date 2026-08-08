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

# SLAM Toolbox で地図を作る。
#
# **センサは立てない。** LiDAR も EKF も robot_bringup.launch.py の受け持ちで、
# docker compose up で常駐している。ここは slam_toolbox と RViz だけを足す。
#
# そのため、**地図を作る場所に合わせて LiDAR の帯 (仰角フィルタと高さ) を変えるには
# SLAM を始める前に `tools/site.sh <名前>` を通す**必要がある (config/site を書いて
# raspicat を立て直すところまでやる)。この launch へ overrides:= を渡しても効くのは
# daifuku_stack: の部分木、すなわち slam_toolbox の節だけ。

import os
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from ament_index_python.packages import get_package_share_directory

# 共通部品はこの launch ディレクトリの直下 (daifuku_stack_launch/) にある。
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from daifuku_config_manager import params  # noqa: E402


def generate_launch_description():
    pkg_share = get_package_share_directory("daifuku_stack")

    config_root = params.config_root("daifuku_stack")
    default_slam_params = os.path.join(config_root, "mapping", "slam_toolbox.yaml")
    default_rviz_config = os.path.join(pkg_share, "rviz", "mapping.rviz")

    namespace = LaunchConfiguration("namespace")
    slam_params_file = LaunchConfiguration("slam_params_file")
    rviz_config = LaunchConfiguration("rviz_config")
    use_sim_time = LaunchConfiguration("use_sim_time")
    use_rviz = LaunchConfiguration("use_rviz")

    return LaunchDescription([
        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("slam_params_file", default_value=default_slam_params),
        DeclareLaunchArgument("rviz_config", default_value=default_rviz_config),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        # 実機 (Raspberry Pi) は headless。表示は同じ ROS_DOMAIN_ID の PC 側から
        # 開く (navigation.launch.py と同じ既定)。
        DeclareLaunchArgument("use_rviz", default_value="false"),
        *params.declare_args(),
        params.declare_watch_arg(),

        # slam_params_file へ overrides を重ねる (slam_toolbox: の節を持つものだけ
        # 効く)。LiDAR 側の設定ファイルは robot_bringup.launch.py が同じ overrides で
        # 重ねるので、ここでは対象にしない。
        OpaqueFunction(
            function=params.compose,
            kwargs={
                "package": "daifuku_stack",
                "config_root": config_root,
                "targets": ["slam_params_file"],
            },
        ),

        # 起動後に設定が書き変わったら言い、追随してよければこの launch を落とす
        # (地図を作っている最中に場所を切り替えたときなど)。**上げ直す人は居ない。**
        OpaqueFunction(
            function=params.sentinel_actions,
            kwargs={"package": "daifuku_stack", "config_root": config_root},
        ),

        Node(
            package="slam_toolbox",
            executable="async_slam_toolbox_node",
            name="slam_toolbox",
            namespace=namespace,
            output="screen",
            parameters=[
                slam_params_file,
                {"use_sim_time": use_sim_time},
            ],
        ),

        Node(
            condition=IfCondition(use_rviz),
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            namespace=namespace,
            output="screen",
            arguments=["-d", rviz_config],
            parameters=[{"use_sim_time": use_sim_time}],
        ),
    ])
