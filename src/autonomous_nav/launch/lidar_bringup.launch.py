# LiDAR まわりの起動。navigation / mapping の両方から include される。
#
# どちらの構成でも入力を /scan_raw へ集約し、角度フィルタを通した /scan を
# SLAM と Nav2 に渡す。
#
#   lidar:=2d      urg_node -> /scan_raw -> 角度フィルタ -> /scan
#   lidar:=mid360  livox_ros_driver2 -> /livox/lidar -> pointcloud_to_laserscan
#                  -> /scan_mid360_prestamp -> restamp_scan.py
#                  -> /scan_raw -> 角度フィルタ -> /scan
#
# lidar:=mid360 では加えて IMU 経路 (prepare_mid360_imu.py -> ekf_node) が立ち、
# 車輪オドメトリと融合した /odom と odom -> base_footprint TF を配信する。

import os
import sys

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    OpaqueFunction,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from ament_index_python.packages import get_package_share_directory

# 共通部品はこの launch ディレクトリの直下 (autonomous_nav_launch/) にある。
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from autonomous_nav_launch import lidar as lidar_common  # noqa: E402


def generate_launch_description():
    pkg_share = get_package_share_directory("autonomous_nav")
    sensors_dir = os.path.join(pkg_share, "config", "sensors")

    lidar = LaunchConfiguration("lidar")
    lidar_driver = LaunchConfiguration("lidar_driver")
    use_sim_time = LaunchConfiguration("use_sim_time")
    scan_raw_topic = LaunchConfiguration("scan_raw_topic")
    scan_topic = LaunchConfiguration("scan_topic")
    scan_filter_enabled = LaunchConfiguration("scan_filter_enabled")
    scan_filter_params_file = LaunchConfiguration("scan_filter_params_file")
    mid360_config = LaunchConfiguration("mid360_config")
    mid360_scan_params_file = LaunchConfiguration("mid360_scan_params_file")
    mid360_ekf_params_file = LaunchConfiguration("mid360_ekf_params_file")
    use_mid360_imu = LaunchConfiguration("use_mid360_imu")
    publish_lidar_tf = LaunchConfiguration("publish_lidar_tf")
    wheel_odom_topic = LaunchConfiguration("wheel_odom_topic")
    odom_topic = LaunchConfiguration("odom_topic")
    lidar_frame = LaunchConfiguration("lidar_frame")
    base_frame = LaunchConfiguration("base_frame")
    urg_params_file = LaunchConfiguration("urg_params_file")

    # ------------------------------------------------------------------
    # どのノードを立てるかの条件
    #
    # lidar_driver:=false は「LiDAR の生データを外部が出す」構成。
    # シミュレータ (simulator) は /livox/lidar を PointCloud2 で直接出すので
    # livox_ros_driver2 を立てない。実機の driver は xfer_format:=0 = PointCloud2 な
    # ので、ドライバの出力とシムの出力は同型で、下流 (pointcloud_to_laserscan 以降)
    # は一切変わらない。
    #
    # restamp_scan.py も同時に外す。あれは MID360 の**デバイス時計が PTP 同期されず
    # 毎分数秒ドリフトする**ことへの対処であって、シムには存在しない問題である。
    # とくに use_sim_time:=true では「受信時刻で押し直す」動作がシム時間と噛み合わず
    # 積極的に有害になる。
    # ------------------------------------------------------------------
    is_mid360 = PythonExpression(["'", lidar, "' == 'mid360'"])
    use_livox_driver = PythonExpression([
        "'", lidar, "' == 'mid360' and '", lidar_driver, "'.lower() == 'true'",
    ])
    # lidar:=2d は raspicat の URG (Hokuyo) ドライバを立てる。lidar_driver:=false
    # (シミュレータ) では /scan_raw を外部が出すので起動しない。
    use_urg_driver = PythonExpression([
        "'", lidar, "' == '2d' and '", lidar_driver, "'.lower() == 'true'",
    ])
    use_mid360_ekf = PythonExpression([
        "'", lidar, "' == 'mid360' and '", use_mid360_imu,
        "'.lower() == 'true'",
    ])
    publish_mid360_tf = PythonExpression([
        "'", lidar, "' == 'mid360' and '", publish_lidar_tf,
        "'.lower() == 'true'",
    ])

    # ------------------------------------------------------------------
    # 起動引数
    #
    # navigation / mapping と共有するものは autonomous_nav_launch.lidar が持つ。
    # ここで宣言するのは、親が素通ししない (このファイルの中だけで完結する) 分。
    # ------------------------------------------------------------------
    declare_args = lidar_common.declare_shared_args(pkg_share) + [
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument(
            "scan_raw_topic",
            default_value="/scan_raw",
            description="どちらの LiDAR でも生スキャンを集約するトピック。",
        ),
        DeclareLaunchArgument(
            "scan_topic",
            default_value="/scan",
            description="角度フィルタ後のトピック。SLAM と Nav2 の入力。",
        ),
        DeclareLaunchArgument(
            "mid360_scan_params_file",
            default_value=os.path.join(sensors_dir, "mid360_scan.yaml"),
            description="pointcloud_to_laserscan の設定 (点群からスキャンへの変換)。",
        ),
        DeclareLaunchArgument(
            "mid360_ekf_params_file",
            default_value=os.path.join(sensors_dir, "mid360_ekf.yaml"),
            description="robot_localization の EKF 設定 (車輪 + Mid-360 IMU)。",
        ),
        DeclareLaunchArgument("mid360_publish_freq", default_value="10.0"),
        DeclareLaunchArgument("lidar_frame", default_value="livox_frame"),
        DeclareLaunchArgument("base_frame", default_value="base_footprint"),
        DeclareLaunchArgument("odom_topic", default_value="/odom"),
    ]

    # ------------------------------------------------------------------
    # LiDAR ドライバ
    # ------------------------------------------------------------------
    # 上流 raspicat_bringup/launch/urg.launch.py と同じ urg_node_driver +
    # config/urg_<urg_interface>.param.yaml だが、include ではノードに remapping を
    # 足せないため自前で立てている。urg_node は `scan` に出すので、ここでこの
    # ファイルの入力 (scan_raw_topic) へ寄せる。ノード名はパラメータ YAML の
    # キー (urg_node) に合わせること。
    urg_driver = Node(
        condition=IfCondition(use_urg_driver),
        package="urg_node",
        executable="urg_node_driver",
        name="urg_node",
        output="screen",
        parameters=[urg_params_file, {"use_sim_time": use_sim_time}],
        remappings=[("scan", scan_raw_topic)],
    )

    livox_driver = Node(
        condition=IfCondition(use_livox_driver),
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
    )

    mid360_static_tf = Node(
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
    )

    # ------------------------------------------------------------------
    # Mid-360: 点群 -> スキャン -> (打ち直し) -> scan_raw_topic
    # ------------------------------------------------------------------
    pointcloud_to_laserscan = Node(
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
            #
            # lidar_driver:=false (シム) にはそのドリフトが無いので restamp を
            # 挟まず、ここから直接 scan_raw_topic に出す。
            ("scan", PythonExpression([
                "'/scan_mid360_prestamp' if '", lidar_driver,
                "'.lower() == 'true' else '", scan_raw_topic, "'",
            ])),
        ],
    )

    restamp_scan = ExecuteProcess(
        condition=IfCondition(use_livox_driver),
        cmd=[
            "python3",
            os.path.join(pkg_share, "src", "restamp_scan.py"),
            "/scan_mid360_prestamp",
            scan_raw_topic,
        ],
        output="screen",
    )

    # ------------------------------------------------------------------
    # 角度フィルタ: scan_raw_topic -> scan_topic
    # 無効にした場合も下流が /scan を見られるよう relay で素通しする。
    # ------------------------------------------------------------------
    scan_filter = Node(
        condition=IfCondition(scan_filter_enabled),
        package="laser_filters",
        executable="scan_to_scan_filter_chain",
        name="scan_to_scan_filter_chain",
        output="screen",
        parameters=[scan_filter_params_file, {"use_sim_time": use_sim_time}],
        remappings=[
            ("scan", scan_raw_topic),
            ("scan_filtered", scan_topic),
        ],
    )
    scan_relay = Node(
        condition=UnlessCondition(scan_filter_enabled),
        package="topic_tools",
        executable="relay",
        name="unfiltered_scan_relay",
        output="screen",
        arguments=[scan_raw_topic, scan_topic],
    )

    # ------------------------------------------------------------------
    # Mid-360 IMU + 車輪オドメトリの融合 (use_mid360_imu:=true)
    # 車輪ノード側の odom -> base_footprint TF は止めておくこと (二重配信)。
    # ------------------------------------------------------------------
    prepare_imu = Node(
        condition=IfCondition(use_mid360_ekf),
        package="autonomous_nav",
        executable="prepare_mid360_imu.py",
        name="prepare_mid360_imu",
        output="screen",
        parameters=[{"use_sim_time": use_sim_time}],
        remappings=[("imu_in", "/livox/imu"), ("imu_out", "/imu/mid360")],
    )
    ekf = Node(
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
    )

    return LaunchDescription([
        *declare_args,

        # 選んだ構成で実際に読むファイルの存在確認と、URG パラメータの解決。
        OpaqueFunction(function=lidar_common.validate),

        urg_driver,
        livox_driver,
        mid360_static_tf,
        pointcloud_to_laserscan,
        restamp_scan,

        scan_filter,
        scan_relay,

        prepare_imu,
        ekf,
    ])
