# Copyright 2026 Keita Sekiguchi / nop
#
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
"""Start the measured GLIM map using the 2025 dual-scan hardware wiring."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from daifuku_config_manager import env_bool_default
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node, SetRemap
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    share = Path(get_package_share_directory("daifuku_stack"))
    config = Path(get_package_share_directory("daifuku_config")) / "stack/tsukuba2026"
    use_sim_time = LaunchConfiguration("use_sim_time")
    lidar_driver = LaunchConfiguration("lidar_driver")
    clock = {"use_sim_time": ParameterValue(use_sim_time, value_type=bool)}
    restamp_cloud = PythonExpression([
        "'", lidar_driver, "'.lower() == 'true' and '",
        use_sim_time, "'.lower() not in ('true', '1')",
    ])
    navigation = str(config / "navigation.yaml")
    localization = str(config / "localization.yaml")
    return LaunchDescription([
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("use_rviz", default_value="false"),
        DeclareLaunchArgument(
            "lidar_driver",
            default_value=PythonExpression([
                "'false' if '", use_sim_time, "'.lower() in ('true', '1') else '",
                env_bool_default("LIDAR_DRIVER", "true"), "'",
            ]),
            description="Use receive-time correction for real Mid-360 inputs. "
                        "Defaults to LIDAR_DRIVER for hardware, false with sim time. "
                        "The driver itself stays in robot_bringup.",
        ),
        ExecuteProcess(
            cmd=["python3", "-u", str(share / "src/localization_gravity.py")],
            output="screen",
        ),
        SetRemap(src="emcl2:scan", dst="/scan_loc"),
        Node(
            package="daifuku_stack", executable="elevation_filter.py",
            name="localization_elevation_filter", output="screen",
            parameters=[localization, clock, {
                "restamp_cloud": ParameterValue(restamp_cloud, value_type=bool),
            }],
            remappings=[("cloud_in", "/livox/lidar"),
                        ("cloud_out", "/livox/lidar_localization")],
        ),
        Node(
            package="pointcloud_to_laserscan", executable="pointcloud_to_laserscan_node",
            name="pointcloud_to_laserscan_loc", output="screen",
            parameters=[localization, clock],
            remappings=[("cloud_in", "/livox/lidar_localization"), ("scan", "/scan_loc_raw")],
        ),
        Node(
            package="laser_filters", executable="scan_to_scan_filter_chain",
            name="scan_filter_loc", output="screen", parameters=[localization, clock],
            remappings=[("scan", "/scan_loc_raw"), ("scan_filtered", "/scan_loc")],
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(share / "launch/navigation.launch.py")),
            launch_arguments={
                "use_sim_time": use_sim_time, "use_rviz": LaunchConfiguration("use_rviz"),
                "lidar": "mid360", "lidar_driver": lidar_driver, "elevation_filter": "true",
                "restamp_cloud": restamp_cloud,
                "overrides": "tsukuba2026",
                "mid360_elevation_params_file": navigation,
                "mid360_scan_params_file": navigation,
                "scan_filter_params_file": navigation,
                "localization": "emcl2", "planner": "vi", "local_planner": "vi",
                "nav2": "false", "use_system_monitor": "false", "use_respawn": "false",
            }.items(),
        ),
    ])
