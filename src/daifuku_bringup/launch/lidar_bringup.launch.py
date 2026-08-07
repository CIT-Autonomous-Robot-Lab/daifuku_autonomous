# LiDAR まわりの起動。navigation / mapping の両方から include される。
#
# どちらの構成でも入力を /scan_raw へ集約し、角度フィルタを通した /scan を
# SLAM と Nav2 に渡す。
#
#   lidar:=2d      urg_node -> /scan_raw -> 角度フィルタ -> /scan
#   lidar:=mid360  livox_ros_driver2 -> /livox/lidar -> elevation_filter.py
#                  -> /livox/lidar_elevation -> pointcloud_to_laserscan
#                  -> /scan_mid360_prestamp -> restamp_scan.py
#                  -> /scan_raw -> 角度フィルタ -> /scan
#                  (elevation_filter:=false なら /livox/lidar が直接 2 段目へ入る)
#
# **IMU 経路 (prepare_mid360_imu.py -> ekf_node) はここには無い。**
# odom_fusion.launch.py が持ち、robot_bringup.launch.py が include する。入力の
# /livox/imu を出すのはここの livox ドライバだが、それはトピックの縁だけで、EKF を
# 本体ドライバと同じ launch に置かないと use_mid360_imu の切り替えが割れるため。

import os
import sys

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    OpaqueFunction,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from ament_index_python.packages import get_package_share_directory

# 共通部品はこの launch ディレクトリの直下 (daifuku_bringup_launch/) にある。
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from daifuku_config_manager import params  # noqa: E402
from daifuku_bringup_launch import lidar as lidar_common  # noqa: E402


def generate_launch_description():
    pkg_share = get_package_share_directory("daifuku_bringup")
    sensors_dir = os.path.join(pkg_share, "config", "sensors")
    config_root = os.path.join(pkg_share, "config")

    lidar = LaunchConfiguration("lidar")
    lidar_driver = LaunchConfiguration("lidar_driver")
    use_sim_time = LaunchConfiguration("use_sim_time")
    scan_raw_topic = LaunchConfiguration("scan_raw_topic")
    scan_topic = LaunchConfiguration("scan_topic")
    scan_filter_enabled = LaunchConfiguration("scan_filter_enabled")
    scan_filter_params_file = LaunchConfiguration("scan_filter_params_file")
    mid360_config = LaunchConfiguration("mid360_config")
    mid360_scan_params_file = LaunchConfiguration("mid360_scan_params_file")
    mid360_elevation_params_file = LaunchConfiguration("mid360_elevation_params_file")
    elevation_filter = LaunchConfiguration("elevation_filter")
    publish_lidar_tf = LaunchConfiguration("publish_lidar_tf")
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
    use_elevation_filter = PythonExpression([
        "'", lidar, "' == 'mid360' and '", elevation_filter,
        "'.lower() == 'true'",
    ])
    publish_mid360_tf = PythonExpression([
        "'", lidar, "' == 'mid360' and '", publish_lidar_tf,
        "'.lower() == 'true'",
    ])

    # ------------------------------------------------------------------
    # 起動引数
    #
    # navigation / mapping と共有するものは daifuku_bringup_launch.lidar が持つ。
    # ここで宣言するのは、親が素通ししない (このファイルの中だけで完結する) 分。
    # ------------------------------------------------------------------
    declare_args = lidar_common.declare_shared_args(pkg_share) + [
        # 親 (navigation / mapping) から素通しされる。単独起動でも同じ既定。
        *params.declare_args(),
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
        DeclareLaunchArgument("mid360_publish_freq", default_value="10.0"),
        # Mid-360 のフレーム。**robot_bringup.launch.py の urdf_lidar_frame
        # (既定 lidar_link) とは別物**で、あちらは URDF が持つ 2D LiDAR の
        # リンク名。同じ名前にすると include したときに親の値が漏れてくる。
        DeclareLaunchArgument("lidar_frame", default_value="livox_frame"),
        DeclareLaunchArgument("base_frame", default_value="base_footprint"),
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
    # Mid-360: 点群 -> (仰角で切る) -> スキャン -> (打ち直し) -> scan_raw_topic
    #
    # 仰角フィルタは pointcloud_to_laserscan の**手前**に入る。あちらが切るのは
    # 変換したあとの z だけなので、仰角は元の点が持っていた情報として先に使う。
    # ------------------------------------------------------------------
    elevation_filter_node = Node(
        condition=IfCondition(use_elevation_filter),
        package="daifuku_bringup",
        executable="elevation_filter.py",
        name="elevation_filter",
        output="screen",
        parameters=[
            mid360_elevation_params_file,
            {"use_sim_time": use_sim_time},
        ],
        remappings=[
            ("cloud_in", "/livox/lidar"),
            ("cloud_out", "/livox/lidar_elevation"),
        ],
    )

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
            # 仰角フィルタを外したときは点群を直接受ける (relay は挟まない)。
            ("cloud_in", PythonExpression([
                "'/livox/lidar_elevation' if '", elevation_filter,
                "'.lower() == 'true' else '/livox/lidar'",
            ])),
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

    restamp_scan = Node(
        condition=IfCondition(use_livox_driver),
        package="daifuku_bringup",
        executable="restamp_scan.py",
        name="restamp_scan",
        output="screen",
        parameters=[{"use_sim_time": use_sim_time}],
        remappings=[
            ("scan_in", "/scan_mid360_prestamp"),
            ("scan_out", scan_raw_topic),
        ],
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

    return LaunchDescription([
        *declare_args,

        # 選んだ構成で実際に読むファイルの存在確認と、URG パラメータの解決。
        OpaqueFunction(function=lidar_common.validate),
        # 上の解決が済んでから overrides を重ねる (urg_params_file は lidar:=2d の
        # ときだけ、validate が値を入れるまで空)。mid360_config は JSON =
        # ROS のパラメータファイルではないので対象にできない。
        OpaqueFunction(
            function=params.compose,
            kwargs={
                "package": "daifuku_bringup",
                "config_root": config_root,
                "targets": [
                    "scan_filter_params_file",
                    "mid360_scan_params_file",
                    "mid360_elevation_params_file",
                    "urg_params_file",
                ],
            },
        ),

        urg_driver,
        livox_driver,
        mid360_static_tf,
        elevation_filter_node,
        pointcloud_to_laserscan,
        restamp_scan,

        scan_filter,
        scan_relay,
    ])
