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

"""スキャン変換を組み込む launch の共有部品。

宣言と include の引数表を共有する。実機ドライバは起動しない。
複数の経路は中間トピックとノード名を分け、include の引数は GroupAction で隔離する。
"""

import os

from ament_index_python.packages import get_package_share_directory

from daifuku_config import env_default, is_true, params, value

from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def _arg_specs():
    """(名前, 既定値, 説明) の並び。説明が None の引数は説明を付けない。

    **ここに載せたものは親 (navigation / mapping) の launch 引数にもなる。**
    scan_pipeline.launch.py の中だけで完結するもの (scan_raw_topic / scan_topic /
    base_frame) は向こうに置いたままにしてある — 親の文脈へ積むと、後ろに並ぶ
    上流の include が自分の既定を入れられなくなる名前を無用に増やすため
    (AGENTS.md の IncludeLaunchDescription の項)。
    """
    sensors = params.config_root("daifuku_perception")
    return [
        ("lidar", env_default("LIDAR", "mid360"),
         "LiDAR backend: mid360 (既定。本機の構成) または 2d (raspicat の URG)。"
         "**機体側の lidar:= と揃えること。** 既定は環境変数 LIDAR。"),
        ("lidar_driver", env_default("LIDAR_DRIVER", "true"),
         "機体側で実機ドライバが動いているか。false にすると mid360 では "
         "restamp_scan を挟まず (シミュレータのクロックには打ち直す理由が無い)、"
         "2d では /scan_raw を外部が出す前提になる。既定は環境変数 LIDAR_DRIVER。"),

        # 既定 true でも既定の設定は 0 度 = 搭載高の水平面から上、で、これは
        # 断片が持つ min_height: 0.275 と同じ切り方。切る角度を実際に狭めるのは
        # overrides/ の側。
        ("elevation_filter", "true",
         "点群を仰角で切るか (勾配の床を落とす。lidar:=mid360 のときだけ効く)。"),
        ("restamp_cloud", "false",
         "仰角フィルタで点群の時刻を受信時刻へ直すか。TF 投影より前に効く。"
         "つくばの実機構成で有効。シミュレータ・バッグでは false。"),
        ("mid360_elevation_params_file", os.path.join(sensors, "mid360_elevation.yaml"),
         "仰角フィルタの設定 (点群を pointcloud_to_laserscan へ渡す前に切る)。"),
        ("mid360_scan_params_file", os.path.join(sensors, "mid360_scan.yaml"),
         "pointcloud_to_laserscan の設定 (点群からスキャンへの変換)。"),

        ("scan_filter_enabled", "true",
         "コネクタのある後方を落とす角度フィルタ (laser_filters) を通すか。"),
        ("scan_filter_params_file", os.path.join(sensors, "scan_filter.yaml"),
         "角度フィルタの設定ファイル。"),
    ]


def declare_args():
    """スキャン変換の引数を宣言する。navigation / mapping が同じものを使う。"""
    declarations = []
    for name, default, description in _arg_specs():
        kwargs = {"default_value": default}
        if description is not None:
            kwargs["description"] = description
        declarations.append(DeclareLaunchArgument(name, **kwargs))
    return declarations


def include_scan_pipeline(pkg_share=None, *, launch_arguments=None):
    """親 (navigation / mapping) から scan_pipeline.launch.py を include する。

    **GroupAction で囲むこと。** launch_arguments は親と同じ文脈に積まれるので、
    囲まないと後ろに並ぶ include (上流の navigation_launch.py など) の
    DeclareLaunchArgument が既定を入れられなくなる。base_frame と use_sim_time が
    その候補で、何がどう壊れるかは AGENTS.md (IncludeLaunchDescription の項)。

    素通しの一覧を人手で書くと引数を足したときに入れ忘れるので、引数表から
    そのまま作る。overrides / extra_params_file も素通しして、子が読む設定
    (mid360_scan / mid360_elevation / scan_filter) を親と同じ overrides で
    上書きできるようにする。
    """
    names = [name for name, _, _ in _arg_specs()]
    names += ["use_sim_time", "overrides", "extra_params_file"]
    if pkg_share is None:
        pkg_share = get_package_share_directory("daifuku_perception")
    arguments = {name: LaunchConfiguration(name) for name in names}
    arguments.update(launch_arguments or {})
    return GroupAction([
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(pkg_share, "launch", "scan_pipeline.launch.py")
            ),
            launch_arguments=arguments.items(),
        ),
    ])


def validate(context, *args, **kwargs):
    """scan_pipeline.launch.py の起動前チェック (OpaqueFunction)。

    選んだ構成で実際に読むファイルだけを見る。存在しないファイルを指したまま
    起動すると、ノードが黙って既定値で上がったり後段だけが止まったりして
    原因が分かりにくいので、ここで落とす。
    """
    selected = value(context, "lidar")
    if selected not in ("2d", "mid360"):
        raise RuntimeError(
            f"Unsupported lidar: {selected}. Use lidar:=2d or lidar:=mid360."
        )

    files = []
    if is_true(context, "scan_filter_enabled"):
        files.append(("scan_filter_params_file", value(context, "scan_filter_params_file")))
    if selected == "mid360":
        files.append(("mid360_scan_params_file", value(context, "mid360_scan_params_file")))
        if is_true(context, "elevation_filter"):
            files.append((
                "mid360_elevation_params_file",
                value(context, "mid360_elevation_params_file"),
            ))

    for label, path in files:
        if not os.path.isfile(path):
            raise RuntimeError(f"{label} does not exist: {path}")
    return []
