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

# 点群 (または生スキャン) を /scan に変える段。navigation / mapping の両方から
# include される。**単独で立てる意味はある** (シミュレータやバッグの再生)。
#
#   lidar:=2d      (機体の urg_node が出す) /scan_raw -> 角度フィルタ -> /scan
#   lidar:=mid360  (機体の livox ドライバが出す) /livox/lidar -> elevation_filter.py
#                  -> /livox/lidar_elevation -> pointcloud_to_laserscan
#                  -> /scan_mid360_prestamp -> restamp_scan.py
#                  -> /scan_raw -> 角度フィルタ -> /scan
#                  (elevation_filter:=false なら /livox/lidar が直接 2 段目へ入る)
#
# **ここが daifuku_stack に居るのは 2026-08-25 から。** 場所ごとに変わる値 (帯と
# 仰角) を持つのはこの段だけなので、機体側 (docker compose で常駐する
# robot_bringup.launch.py) から出して、人が立てる側へ移した。おかげで機体は
# src/daifuku_config/site を読まなくてよくなり、地図を変えても
# `docker compose restart raspicat` が要らない。経緯は
# launch/daifuku_stack_launch/scan.py の docstring。
#
# **/scan を出すのはこの段だけ。** navigation と mapping の両方が include するので、
# 2 つを同時に立てると publisher が 2 つになる (どちらも同じ設定・同じ入力なので
# 値は同じだが、レートが倍に見える)。**同時には立てないこと** — もともと自律移動と
# 地図作成は排他で、mapping 側は emcl2 と衝突する map->odom も持っている。

import os
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node

# 共通部品はこの launch ディレクトリの直下 (daifuku_stack_launch/) にある。
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from daifuku_config_manager import params  # noqa: E402
from daifuku_stack_launch import scan as scan_common  # noqa: E402


def generate_launch_description():
    config_root = params.config_root("daifuku_stack")

    lidar = LaunchConfiguration("lidar")
    lidar_driver = LaunchConfiguration("lidar_driver")
    use_sim_time = LaunchConfiguration("use_sim_time")
    scan_raw_topic = LaunchConfiguration("scan_raw_topic")
    scan_topic = LaunchConfiguration("scan_topic")
    base_frame = LaunchConfiguration("base_frame")
    scan_filter_enabled = LaunchConfiguration("scan_filter_enabled")
    scan_filter_params_file = LaunchConfiguration("scan_filter_params_file")
    mid360_scan_params_file = LaunchConfiguration("mid360_scan_params_file")
    mid360_elevation_params_file = LaunchConfiguration("mid360_elevation_params_file")
    elevation_filter = LaunchConfiguration("elevation_filter")

    is_mid360 = PythonExpression(["'", lidar, "' == 'mid360'"])
    use_elevation_filter = PythonExpression([
        "'", lidar, "' == 'mid360' and '", elevation_filter, "'.lower() == 'true'",
    ])
    # restamp_scan.py は MID360 の**デバイス時計が PTP 同期されず毎分数秒ドリフト
    # する**ことへの対処。lidar_driver:=false (シミュレータ・バッグ) には存在しない
    # 問題で、とくに use_sim_time:=true では「受信時刻で押し直す」動作がシム時間と
    # 噛み合わず積極的に有害になる。
    use_restamp = PythonExpression([
        "'", lidar, "' == 'mid360' and '", lidar_driver, "'.lower() == 'true'",
    ])

    # 仰角フィルタは pointcloud_to_laserscan の**手前**に入る。あちらが切るのは
    # 変換したあとの z だけなので、仰角は元の点が持っていた情報として先に使う。
    elevation_filter_node = Node(
        condition=IfCondition(use_elevation_filter),
        package="daifuku_stack",
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
            # restamp を挟まないときはここから直接 scan_raw_topic に出す。
            ("scan", PythonExpression([
                "'/scan_mid360_prestamp' if '", lidar_driver,
                "'.lower() == 'true' else '", scan_raw_topic, "'",
            ])),
        ],
    )

    restamp_scan = Node(
        condition=IfCondition(use_restamp),
        package="daifuku_stack",
        executable="restamp_scan.py",
        name="restamp_scan",
        output="screen",
        parameters=[{"use_sim_time": use_sim_time}],
        remappings=[
            ("scan_in", "/scan_mid360_prestamp"),
            ("scan_out", scan_raw_topic),
        ],
    )

    # 角度フィルタ: scan_raw_topic -> scan_topic
    # 無効にした場合も下流が /scan を見られるよう relay で素通しする。
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
        # 引数は launch/daifuku_stack_launch/scan.py の表が持つ (親も同じものを
        # 宣言して素通しする)。ここで足すのは親も子もそれぞれの意味で持つ分。
        *scan_common.declare_args(),
        *params.declare_args(),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        # ここだけで完結する引数 (親は素通ししない)。
        DeclareLaunchArgument(
            "scan_raw_topic",
            default_value="/scan_raw",
            description="どちらの LiDAR でも生スキャンを集約するトピック。"
                        "**lidar:=2d では機体側 (lidar_bringup.launch.py) の同名の "
                        "引数と揃えること** (あちらの出口)。",
        ),
        DeclareLaunchArgument(
            "scan_topic",
            default_value="/scan",
            description="角度フィルタ後のトピック。SLAM と Nav2 の入力。",
        ),
        DeclareLaunchArgument(
            "base_frame",
            default_value="base_footprint",
            description="pointcloud_to_laserscan が点群を落とし込むフレーム。",
        ),

        # 選んだ構成で実際に読むファイルの存在確認。
        OpaqueFunction(function=scan_common.validate),
        # 読む設定ファイルへ overrides を重ねる (地図ごとの帯と仰角がここに来る)。
        # **config_sentinel はここでは立てない** — include される側で立てると
        # 1 つの launch 木に見張りが 2 つ並ぶ (AGENTS.md)。親が立てている見張りが
        # src/daifuku_config/stack/ 全体を見ているので、この段の設定も入っている。
        OpaqueFunction(
            function=params.compose,
            kwargs={
                "package": "daifuku_stack",
                "config_root": config_root,
                "targets": [
                    "scan_filter_params_file",
                    "mid360_scan_params_file",
                    "mid360_elevation_params_file",
                ],
            },
        ),

        elevation_filter_node,
        pointcloud_to_laserscan,
        restamp_scan,

        scan_filter,
        scan_relay,
    ])
