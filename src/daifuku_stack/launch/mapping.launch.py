# SLAM Toolbox で地図を作る。
#
# LiDAR まわりは lidar_bringup.launch.py に任せ (引数はすべて素通しする)、
# ここは slam_toolbox と RViz だけを足す。

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

from daifuku_stack_launch import lidar as lidar_common, params  # noqa: E402


def generate_launch_description():
    pkg_share = get_package_share_directory("daifuku_stack")

    overrides_dir = os.path.join(pkg_share, "config", "overrides")
    default_slam_params = os.path.join(pkg_share, "config", "mapping", "slam_toolbox.yaml")
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
        # LiDAR 構成の引数 (lidar / lidar_driver / scan_filter_* / mid360_* /
        # publish_lidar_tf / lidar_x..yaw / wheel_odom_topic / urg_*)。
        *lidar_common.declare_shared_args(pkg_share),
        *params.declare_args(overrides_dir),

        # slam_params_file へ overrides を重ねる (slam_toolbox: の節を持つものだけ
        # 効く)。LiDAR 側の設定ファイルは lidar_bringup.launch.py が同じ overrides で
        # 重ねるので、ここでは対象にしない。
        OpaqueFunction(
            function=params.compose,
            kwargs={
                "overrides_dir": overrides_dir,
                "targets": ["slam_params_file"],
            },
        ),

        lidar_common.include_lidar_bringup(pkg_share),

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
