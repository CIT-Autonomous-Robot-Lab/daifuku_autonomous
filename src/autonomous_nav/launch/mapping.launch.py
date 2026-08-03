import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    pkg_share = get_package_share_directory("autonomous_nav")

    default_slam_params = os.path.join(pkg_share, "config", "mapping", "slam_toolbox.yaml")
    default_rviz_config = os.path.join(pkg_share, "rviz", "mapping.rviz")
    lidar_bringup_launch = os.path.join(pkg_share, "launch", "lidar_bringup.launch.py")

    namespace = LaunchConfiguration("namespace")
    slam_params_file = LaunchConfiguration("slam_params_file")
    rviz_config = LaunchConfiguration("rviz_config")
    use_sim_time = LaunchConfiguration("use_sim_time")
    use_rviz = LaunchConfiguration("use_rviz")
    lidar = LaunchConfiguration("lidar")
    scan_filter_enabled = LaunchConfiguration("scan_filter_enabled")
    scan_filter_params_file = LaunchConfiguration("scan_filter_params_file")
    mid360_config = LaunchConfiguration("mid360_config")
    use_mid360_imu = LaunchConfiguration("use_mid360_imu")

    return LaunchDescription([
        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("slam_params_file", default_value=default_slam_params),
        DeclareLaunchArgument("rviz_config", default_value=default_rviz_config),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        # 実機 (Raspberry Pi) は headless。表示は同じ ROS_DOMAIN_ID の PC 側から
        # 開く (navigation.launch.py と同じ既定)。
        DeclareLaunchArgument("use_rviz", default_value="false"),
        DeclareLaunchArgument(
            "lidar",
            default_value="mid360",
            description="LiDAR backend: mid360 (既定。本機の構成) または "
                        "2d (raspicat の URG を起動する)。",
        ),
        DeclareLaunchArgument("scan_filter_enabled", default_value="true"),
        DeclareLaunchArgument(
            "scan_filter_params_file",
            default_value=os.path.join(pkg_share, "config", "sensors", "scan_filter.yaml"),
        ),
        DeclareLaunchArgument(
            "mid360_config",
            default_value=os.path.join(pkg_share, "config", "sensors", "MID360_config.json"),
        ),
        DeclareLaunchArgument("use_mid360_imu", default_value="true"),
        # 既定 true。TF が出るのは lidar:=mid360 のときだけで、URDF が配信して
        # いない base_footprint -> livox_frame を補う (詳細は lidar_bringup 側)。
        DeclareLaunchArgument("publish_lidar_tf", default_value="true"),
        DeclareLaunchArgument("lidar_x", default_value="0.0"),
        DeclareLaunchArgument("lidar_y", default_value="0.0"),
        DeclareLaunchArgument("lidar_z", default_value="0.275"),  # 実測 275 mm
        DeclareLaunchArgument("lidar_roll", default_value="0.0"),
        DeclareLaunchArgument("lidar_pitch", default_value="0.0"),
        DeclareLaunchArgument("lidar_yaw", default_value="0.0"),
        DeclareLaunchArgument("wheel_odom_topic", default_value="/wheel/odom"),
        DeclareLaunchArgument(
            "lidar_driver",
            default_value="true",
            description="LiDAR の実機ドライバ (mid360: livox_ros_driver2 + "
                        "restamp_scan.py / 2d: urg_node) を起動するか。"
                        "シミュレータやバッグから地図を作るときは false にする。",
        ),
        DeclareLaunchArgument("urg_interface", default_value="serial"),
        DeclareLaunchArgument("urg_params_file", default_value=""),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(lidar_bringup_launch),
            launch_arguments={
                "lidar": lidar,
                "lidar_driver": LaunchConfiguration("lidar_driver"),
                "use_sim_time": use_sim_time,
                "scan_filter_enabled": scan_filter_enabled,
                "scan_filter_params_file": scan_filter_params_file,
                "mid360_config": mid360_config,
                "use_mid360_imu": use_mid360_imu,
                "publish_lidar_tf": LaunchConfiguration("publish_lidar_tf"),
                "lidar_x": LaunchConfiguration("lidar_x"),
                "lidar_y": LaunchConfiguration("lidar_y"),
                "lidar_z": LaunchConfiguration("lidar_z"),
                "lidar_roll": LaunchConfiguration("lidar_roll"),
                "lidar_pitch": LaunchConfiguration("lidar_pitch"),
                "lidar_yaw": LaunchConfiguration("lidar_yaw"),
                "wheel_odom_topic": LaunchConfiguration("wheel_odom_topic"),
                "urg_interface": LaunchConfiguration("urg_interface"),
                "urg_params_file": LaunchConfiguration("urg_params_file"),
            }.items(),
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
