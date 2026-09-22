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

# LiDAR ドライバの起動。robot_bringup.launch.py が include する。
#
# **生データを出すところまで。** どちらの構成でもここが出すのはセンサそのままの
# 点群 (mid360) か生スキャン (2d) で、それを /scan に変える段は
# daifuku_stack の scan_pipeline.launch.py に居る (2026-08-25 に出した。
# 場所ごとに変わる値を持つのはあちらだけなので、docker compose で常駐するこちらが
# src/daifuku_config/site を読まなくて済むようにした)。
#
#   lidar:=2d      urg_node -> /scan_raw
#   lidar:=mid360  livox_ros_driver2 -> /livox/lidar, /livox/imu
#
# **lidar:= と lidar_driver:= はあちらと揃えること。** 食い違うと /scan が空に
# なるだけでエラーも警告も出ない。既定は環境変数 (LIDAR / LIDAR_DRIVER) から取り、
# Compose が .env の 1 行を raspicat と ros2 の両サービスへ配る。
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
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

# 共通部品はこの launch ディレクトリの直下 (daifuku_bringup_launch/) にある。
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from daifuku_config_manager import params  # noqa: E402
from daifuku_bringup_launch import lidar as lidar_common  # noqa: E402


def generate_launch_description():
    config_root = params.config_root("daifuku_bringup")

    lidar = LaunchConfiguration("lidar")
    lidar_driver = LaunchConfiguration("lidar_driver")
    use_sim_time = LaunchConfiguration("use_sim_time")
    scan_raw_topic = LaunchConfiguration("scan_raw_topic")
    mid360_config = LaunchConfiguration("mid360_config")
    publish_lidar_tf = LaunchConfiguration("publish_lidar_tf")
    lidar_frame = LaunchConfiguration("lidar_frame")
    base_frame = LaunchConfiguration("base_frame")
    urg_params_file = LaunchConfiguration("urg_params_file")

    # どのノードを立てるかの条件
    #
    # lidar_driver:=false は「LiDAR の生データを外部が出す」構成。
    # シミュレータ (simulator) は /livox/lidar を PointCloud2 で直接出すので
    # livox_ros_driver2 を立てない。実機の driver は xfer_format:=0 = PointCloud2 な
    # ので、ドライバの出力とシムの出力は同型で、下流 (scan_pipeline.launch.py) は
    # 一切変わらない。
    use_livox_driver = PythonExpression([
        "'", lidar, "' == 'mid360' and '", lidar_driver, "'.lower() == 'true'",
    ])
    # lidar:=2d は raspicat の URG (Hokuyo) ドライバを立てる。lidar_driver:=false
    # (シミュレータ) では /scan_raw を外部が出すので起動しない。
    use_urg_driver = PythonExpression([
        "'", lidar, "' == '2d' and '", lidar_driver, "'.lower() == 'true'",
    ])
    publish_mid360_tf = PythonExpression([
        "'", lidar, "' == 'mid360' and '", publish_lidar_tf,
        "'.lower() == 'true'",
    ])

    # 起動引数
    #
    # robot_bringup.launch.py と共有するものは daifuku_bringup_launch.lidar が
    # 持つ。ここで宣言するのは、親が素通ししない (このファイルの中だけで完結する) 分。
    declare_args = lidar_common.declare_shared_args() + [
        # 親 (robot_bringup) から素通しされる。単独起動でも同じ既定。
        *params.declare_args(),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument(
            "scan_raw_topic",
            default_value="/scan_raw",
            description="lidar:=2d で urg_node が出すトピック。"
                        "**daifuku_stack の scan_pipeline.launch.py の同名の "
                        "引数と揃えること** (あちらの入口)。",
        ),
        DeclareLaunchArgument("mid360_publish_freq", default_value="10.0"),
        # Mid-360 のフレーム。**robot_bringup.launch.py の urdf_lidar_frame
        # (既定 lidar_link) とは別物**で、あちらは URDF が持つ 2D LiDAR の
        # リンク名。同じ名前にすると include したときに親の値が漏れてくる。
        DeclareLaunchArgument("lidar_frame", default_value="livox_frame"),
        DeclareLaunchArgument("base_frame", default_value="base_footprint"),
    ]

    # LiDAR ドライバ
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
                "targets": ["urg_params_file"],
            },
        ),

        urg_driver,
        livox_driver,
        mid360_static_tf,
    ])
