import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    pkg_share = get_package_share_directory("autonomous_nav")

    lidar = LaunchConfiguration("lidar")
    use_sim_time = LaunchConfiguration("use_sim_time")
    scan_filter_enabled = LaunchConfiguration("scan_filter_enabled")
    scan_filter_params_file = LaunchConfiguration("scan_filter_params_file")
    mid360_config = LaunchConfiguration("mid360_config")
    mid360_scan_params_file = LaunchConfiguration("mid360_scan_params_file")
    use_mid360_imu = LaunchConfiguration("use_mid360_imu")
    mid360_ekf_params_file = LaunchConfiguration("mid360_ekf_params_file")
    wheel_odom_topic = LaunchConfiguration("wheel_odom_topic")
    odom_topic = LaunchConfiguration("odom_topic")
    publish_lidar_tf = LaunchConfiguration("publish_lidar_tf")
    lidar_frame = LaunchConfiguration("lidar_frame")
    base_frame = LaunchConfiguration("base_frame")

    is_mid360 = PythonExpression(["'", lidar, "' == 'mid360'"])
    use_mid360_ekf = PythonExpression([
        "'", lidar, "' == 'mid360' and '", use_mid360_imu,
        "'.lower() == 'true'",
    ])
    publish_mid360_tf = PythonExpression([
        "'", lidar, "' == 'mid360' and '", publish_lidar_tf,
        "'.lower() == 'true'",
    ])

    def validate(context, *args, **kwargs):
        selected = lidar.perform(context)
        if selected not in ("2d", "mid360"):
            raise RuntimeError(
                f"Unsupported lidar: {selected}. Use lidar:=2d or lidar:=mid360."
            )

        files = []
        if scan_filter_enabled.perform(context).lower() == "true":
            files.append(
                ("scan_filter_params_file", scan_filter_params_file.perform(context))
            )
        if selected == "mid360":
            files.extend([
                ("mid360_config", mid360_config.perform(context)),
                ("mid360_scan_params_file", mid360_scan_params_file.perform(context)),
            ])
            if use_mid360_imu.perform(context).lower() == "true":
                files.append(
                    ("mid360_ekf_params_file", mid360_ekf_params_file.perform(context))
                )
        for label, path in files:
            if not os.path.isfile(path):
                raise RuntimeError(f"{label} does not exist: {path}")
        return []

    return LaunchDescription([
        DeclareLaunchArgument(
            "lidar",
            default_value="2d",
            description="LiDAR backend: 2d (external /scan_raw) or mid360.",
        ),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("scan_raw_topic", default_value="/scan_raw"),
        DeclareLaunchArgument("scan_topic", default_value="/scan"),
        DeclareLaunchArgument("scan_filter_enabled", default_value="true"),
        DeclareLaunchArgument(
            "scan_filter_params_file",
            default_value=os.path.join(pkg_share, "config", "sensors", "scan_filter.yaml"),
        ),
        DeclareLaunchArgument(
            "mid360_config",
            default_value=os.path.join(pkg_share, "config", "sensors", "MID360_config.json"),
        ),
        DeclareLaunchArgument(
            "mid360_scan_params_file",
            default_value=os.path.join(pkg_share, "config", "sensors", "mid360_scan.yaml"),
        ),
        DeclareLaunchArgument("mid360_publish_freq", default_value="10.0"),
        DeclareLaunchArgument("lidar_frame", default_value="livox_frame"),
        DeclareLaunchArgument("base_frame", default_value="base_footprint"),
        DeclareLaunchArgument("publish_lidar_tf", default_value="false"),
        DeclareLaunchArgument("lidar_x", default_value="0.0"),
        DeclareLaunchArgument("lidar_y", default_value="0.0"),
        DeclareLaunchArgument("lidar_z", default_value="0.0"),
        DeclareLaunchArgument("lidar_roll", default_value="0.0"),
        DeclareLaunchArgument("lidar_pitch", default_value="0.0"),
        DeclareLaunchArgument("lidar_yaw", default_value="0.0"),
        DeclareLaunchArgument("use_mid360_imu", default_value="true"),
        DeclareLaunchArgument(
            "mid360_ekf_params_file",
            default_value=os.path.join(pkg_share, "config", "sensors", "mid360_ekf.yaml"),
        ),
        DeclareLaunchArgument("wheel_odom_topic", default_value="/wheel/odom"),
        DeclareLaunchArgument("odom_topic", default_value="/odom"),

        OpaqueFunction(function=validate),

        Node(
            condition=IfCondition(is_mid360),
            package="livox_ros_driver2",
            executable="livox_ros_driver2_node",
            name="livox_lidar_publisher",
            output="screen",
            parameters=[{
                "xfer_format": 0,
                "multi_topic": 0,
                "data_src": 0,
                "publish_freq": ParameterValue(
                    LaunchConfiguration("mid360_publish_freq"), value_type=float
                ),
                "output_data_type": 0,
                "frame_id": lidar_frame,
                "user_config_path": mid360_config,
                "cmdline_input_bd_code": "livox0000000001",
            }],
        ),

        Node(
            condition=IfCondition(publish_mid360_tf),
            package="tf2_ros",
            executable="static_transform_publisher",
            name="mid360_static_transform",
            output="screen",
            arguments=[
                "--x", LaunchConfiguration("lidar_x"),
                "--y", LaunchConfiguration("lidar_y"),
                "--z", LaunchConfiguration("lidar_z"),
                "--roll", LaunchConfiguration("lidar_roll"),
                "--pitch", LaunchConfiguration("lidar_pitch"),
                "--yaw", LaunchConfiguration("lidar_yaw"),
                "--frame-id", base_frame,
                "--child-frame-id", lidar_frame,
            ],
        ),

        Node(
            condition=IfCondition(is_mid360),
            package="pointcloud_to_laserscan",
            executable="pointcloud_to_laserscan_node",
            name="pointcloud_to_laserscan",
            output="screen",
            parameters=[
                mid360_scan_params_file,
                {"use_sim_time": use_sim_time, "target_frame": base_frame},
            ],
            remappings=[
                ("cloud_in", "/livox/lidar"),
                # The MID360 device clock is not PTP-synced and drifts by
                # seconds per minute against the system clock, so the raw
                # scan is restamped with the receive time below before it
                # reaches the filter chain and the rest of the stack.
                ("scan", "/scan_mid360_prestamp"),
            ],
        ),

        ExecuteProcess(
            condition=IfCondition(is_mid360),
            cmd=[
                "python3",
                os.path.join(pkg_share, "scripts", "restamp_scan.py"),
                "/scan_mid360_prestamp",
                LaunchConfiguration("scan_raw_topic"),
            ],
            output="screen",
        ),

        Node(
            condition=IfCondition(scan_filter_enabled),
            package="laser_filters",
            executable="scan_to_scan_filter_chain",
            name="scan_to_scan_filter_chain",
            output="screen",
            parameters=[scan_filter_params_file, {"use_sim_time": use_sim_time}],
            remappings=[
                ("scan", LaunchConfiguration("scan_raw_topic")),
                ("scan_filtered", LaunchConfiguration("scan_topic")),
            ],
        ),
        Node(
            condition=UnlessCondition(scan_filter_enabled),
            package="topic_tools",
            executable="relay",
            name="unfiltered_scan_relay",
            output="screen",
            arguments=[
                LaunchConfiguration("scan_raw_topic"),
                LaunchConfiguration("scan_topic"),
            ],
        ),

        Node(
            condition=IfCondition(use_mid360_ekf),
            package="autonomous_nav",
            executable="prepare_mid360_imu.py",
            name="prepare_mid360_imu",
            output="screen",
            parameters=[{"use_sim_time": use_sim_time}],
            remappings=[("imu_in", "/livox/imu"), ("imu_out", "/imu/mid360")],
        ),
        Node(
            condition=IfCondition(use_mid360_ekf),
            package="robot_localization",
            executable="ekf_node",
            name="ekf_filter_node",
            output="screen",
            parameters=[
                mid360_ekf_params_file,
                {
                    "use_sim_time": use_sim_time,
                    "odom0": wheel_odom_topic,
                    "imu0": "/imu/mid360",
                    "base_link_frame": base_frame,
                },
            ],
            remappings=[("odometry/filtered", odom_topic)],
        ),
    ])
