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

# 車輪オドメトリと Mid-360 の IMU を EKF で融合する。
#
#   /wheel/odom (本体ドライバ) ─┐
#                               ├─ ekf_node ─> /odom, odom -> base_footprint
#   /livox/imu ─> prepare_mid360_imu.py ─> /imu/mid360 ─┘
#
# **robot_bringup.launch.py から include される。** 本体ドライバと同じ launch に
# 居るのが要点で、`use_mid360_imu:=true` は「ドライバが /wheel/odom を出して TF を
# 止める」と「EKF が /odom と TF を出す」の両方を同時に切り替える。以前はこの 2 つが
# 別の launch (robot_bringup と lidar_bringup) に分かれていて、片方だけ true にすると
# エラーも警告も出ないまま自己位置が壊れた (2026-08-05 の実機)。同じファイルに入れた
# ことでその状態が作れなくなったので、環境変数で両者へ配る必要ももう無い。
#
# **入力の片方 (/livox/imu) は lidar_bringup.launch.py の livox ドライバが出す。**
# トピックの縁だけで、起動順の依存は無い。バイアス測定はメッセージ駆動 (静止した
# 400 サンプルが溜まるまで待つ) なので、IMU が後から来ても取りこぼさない。
#
# `use_mid360_imu:=false` では何も立たない。そのとき odom -> base_footprint と /odom
# を出すのは本体ドライバ側になる (TF の所有者は区間ごとに 1 つだけ)。

import os
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from ament_index_python.packages import get_package_share_directory

# 共通部品はこの launch ディレクトリの直下 (daifuku_bringup_launch/) にある。
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from daifuku_config_manager import env_bool_default, is_true, params, value  # noqa: E402


def validate(context, *args, **kwargs):
    """立てる構成で実際に読むファイルだけを見る (OpaqueFunction)。

    存在しないファイルを指したまま起動すると、EKF が黙って既定値 (odom0 も imu0 も
    無い状態) で上がり、**TF も /odom も出ないまま何も言わない**ので、ここで落とす。
    """
    if not is_true(context, "use_mid360_imu"):
        return []
    path = value(context, "mid360_ekf_params_file")
    if not os.path.isfile(path):
        raise RuntimeError(f"mid360_ekf_params_file does not exist: {path}")
    return []


def generate_launch_description():
    pkg_share = get_package_share_directory("daifuku_bringup")
    sensors_dir = os.path.join(pkg_share, "config", "sensors")
    config_root = os.path.join(pkg_share, "config")

    use_sim_time = LaunchConfiguration("use_sim_time")
    use_mid360_imu = LaunchConfiguration("use_mid360_imu")
    mid360_ekf_params_file = LaunchConfiguration("mid360_ekf_params_file")
    wheel_odom_topic = LaunchConfiguration("wheel_odom_topic")
    odom_topic = LaunchConfiguration("odom_topic")
    base_frame = LaunchConfiguration("base_frame")

    # Livox が出す IMU をそのまま EKF に入れられる形へ直す。ジャイロの電源投入時
    # バイアスを起動後の静止区間から測って引き、加速度を g から m/s^2 へ直す
    # (robot_localization はセンサのバイアスを推定しない)。**そのため起動時は機体を
    # 静止させておくこと。**
    prepare_imu = Node(
        condition=IfCondition(use_mid360_imu),
        package="daifuku_bringup",
        executable="prepare_mid360_imu.py",
        name="prepare_mid360_imu",
        output="screen",
        # EKF と同じファイルを渡す。節はノード名で分かれるので互いに影響せず、
        # overrides/ から両方のノードを 1 つのファイルで触れる (行き先はノード名で
        # 決まるので、どの設定ファイルにも無いノード名は起動時に弾かれる)。
        parameters=[mid360_ekf_params_file, {"use_sim_time": use_sim_time}],
        remappings=[("imu_in", "/livox/imu"), ("imu_out", "/imu/mid360")],
    )

    ekf = Node(
        condition=IfCondition(use_mid360_imu),
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
        # 親 (robot_bringup) から素通しされる。単独起動でも同じ既定。
        *params.declare_args(),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument(
            "use_mid360_imu",
            # 既定は環境変数から取る。**この launch と robot_bringup.launch.py が
            # 同じ値を見る必要はもう無い** (向こうが include するので必ず同じ値になる)
            # が、Compose の .env 1 行で切れる操作性のために残してある。
            default_value=env_bool_default("USE_MID360_IMU", "true"),
            description="Mid-360 の IMU と車輪オドメトリを EKF で融合するか。false に "
                        "すると何も立たず、/odom と odom -> base_footprint は本体 "
                        "ドライバが出す側に戻る。既定は環境変数 USE_MID360_IMU。",
        ),
        DeclareLaunchArgument(
            "mid360_ekf_params_file",
            default_value=os.path.join(sensors_dir, "mid360_ekf.yaml"),
            description="robot_localization の EKF 設定 (車輪 + Mid-360 IMU)。"
                        "prepare_mid360_imu の節も同じファイルにある。",
        ),
        DeclareLaunchArgument(
            "wheel_odom_topic",
            default_value="/wheel/odom",
            description="EKF に入れる車輪オドメトリ (本体ドライバの出力)。",
        ),
        DeclareLaunchArgument(
            "odom_topic",
            default_value="/odom",
            description="EKF の出力 (odometry/filtered の remap 先)。",
        ),
        DeclareLaunchArgument("base_frame", default_value="base_footprint"),

        OpaqueFunction(function=validate),
        OpaqueFunction(
            function=params.compose,
            kwargs={
                "package": "daifuku_bringup",
                "config_root": config_root,
                "targets": ["mid360_ekf_params_file"],
            },
        ),

        prepare_imu,
        ekf,
    ])
