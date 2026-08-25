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

"""スキャン変換 (点群 -> /scan) の共通部品。

navigation.launch.py と mapping.launch.py が同じ引数を宣言し、同じ
scan_pipeline.launch.py を include する。引数表 (_arg_specs) をここに 1 つだけ
置き、宣言も親から子への素通しもそこから機械的に作る (daifuku_bringup 側の
lidar.py と同じ作り)。

**この段が daifuku_bringup から出てきたのは 2026-08-25。** 場所ごとに変わる値
(帯と仰角) を持っているのはこの段だけで、それが docker compose で常駐する機体側に
居たせいで、機体が起動時に src/daifuku_config/site を読まねばならなかった。読み手を
消費者側 (人が立てる navigation / mapping) へ移すと、機体は場所を知らなくてよく
なる。設定 (src/daifuku_config/stack/sensors/) とノードの実体
(src/daifuku_stack/src/) も一緒に移してあるのは、config_sentinel の指紋が
「そのパッケージの config_root 1 つ」で決まるため — 読み手と設定が別パッケージに
分かれると、直しても気づかない側と、読まないのに落ちる側が同時にできる。

**lidar:= と lidar_driver:= は機体側 (lidar_bringup.launch.py) と揃っていること。**
食い違うと、エラーも警告も出ないまま /scan が空になる (mid360 のドライバが出す
点群を 2d の経路が拾わない、など)。だから既定は両方とも環境変数から取る
(LIDAR / LIDAR_DRIVER)。Compose が .env の 1 行を raspicat と ros2 の両サービスへ
配るので、実機では人が 2 か所へ書かなくてよい。
"""

import os

from daifuku_config_manager import env_default, is_true, params, value

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
    sensors = os.path.join(params.config_root("daifuku_stack"), "sensors")
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


def include_scan_pipeline(pkg_share):
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
    return GroupAction([
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(pkg_share, "launch", "scan_pipeline.launch.py")
            ),
            launch_arguments=[(name, LaunchConfiguration(name)) for name in names],
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
