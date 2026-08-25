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

"""LiDAR ドライバの共通部品。

lidar_bringup.launch.py と、それを include する robot_bringup.launch.py が同じ
引数を宣言する。以前は 3 つの launch (navigation / mapping / lidar_bringup) に
同じ既定値を手で書いていて、mapping にだけ lidar_driver が無い、lidar_z の実測値が
片方だけ古い、といったずれが実際に起きた。引数表 (_shared_arg_specs) をここに 1 つ
だけ置き、宣言も親から子への素通しもそこから機械的に作る。

lidar_bringup.launch.py だけが使う引数 (scan_raw_topic / lidar_frame など、
親が触らないもの) は向こうに置いたままにしてある。

**ここが持つのはセンサを回す分だけ** (ドライバと、その取り付け位置の静的 TF)。
点群を /scan に変える段は 2026-08-25 に daifuku_stack へ出た
(launch/scan_pipeline.launch.py。経緯は向こうの daifuku_stack_launch/scan.py)。
そちらだけが場所ごとに変わる値を持っていたので、機体は場所を知らなくてよくなった。
裏返しに **lidar:= と lidar_driver:= は 2 つの launch にまたがる**ので、既定を
環境変数 (LIDAR / LIDAR_DRIVER) から取って Compose に配らせている。
"""

import os

from ament_index_python.packages import (
    PackageNotFoundError,
    get_package_share_directory,
)
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    SetLaunchConfiguration,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

from daifuku_config_manager import env_default, is_true, params, value


def _shared_arg_specs():
    """(名前, 既定値, 説明) の並び。説明が None の引数は説明を付けない。"""
    sensors = os.path.join(params.config_root("daifuku_bringup"), "sensors")
    return [
        # **daifuku_stack の scan_pipeline.launch.py と同じ値にすること。**
        # 食い違うとエラーも警告も出ないまま /scan が空になるので、既定は
        # 両方とも環境変数から取る (Compose が .env の 1 行を両サービスへ配る)。
        ("lidar", env_default("LIDAR", "mid360"),
         "LiDAR backend: mid360 (既定。本機の構成) または "
         "2d (raspicat の URG を起動する)。既定は環境変数 LIDAR。"),
        ("lidar_driver", env_default("LIDAR_DRIVER", "true"),
         "LiDAR の実機ドライバ (mid360: livox_ros_driver2 / 2d: urg_node) を "
         "起動するか。false にすると /livox/lidar と /livox/imu (mid360) あるいは "
         "/scan_raw (2d) を外部 (シミュレータやバッグ) が出す前提になる。"
         "既定は環境変数 LIDAR_DRIVER。"),

        ("mid360_config", os.path.join(sensors, "MID360_config.json"),
         "livox_ros_driver2 の設定 (Mid-360 本体とホストの IP)。"),

        # 既定 true は mid360 構成の都合。URDF は base_footprint -> lidar_link
        # (2D LiDAR のフレーム) しか配信しておらず、Mid-360 の livox_frame は
        # 誰も出さない。実際に TF を出すのは lidar:=mid360 のときだけで、
        # lidar:=2d では publish_lidar_tf:=true でも何も出ない
        # (lidar_bringup.launch.py の publish_mid360_tf)。
        # URDF から livox_frame を配信するようにしたら false にすること
        # (同じ TF を二重に配信してはいけない)。
        ("publish_lidar_tf", "true",
         "base_footprint -> livox_frame を static_transform_publisher で配信するか "
         "(lidar:=mid360 のときだけ効く)。"),
        ("lidar_x", "0.0", None),
        ("lidar_y", "0.0", None),
        # 実測 275 mm (2026-08-03)。base_footprint (接地面) から Mid-360 まで。
        ("lidar_z", "0.275", None),
        ("lidar_roll", "0.0", None),
        ("lidar_pitch", "0.0", None),
        ("lidar_yaw", "0.0", None),

        ("urg_interface", "serial",
         "lidar:=2d のときの URG の接続方式: serial または ethernet。"
         "既定のパラメータファイル名を決める。"),
        ("urg_params_file", "",
         "URG のパラメータファイル。空なら raspicat_bringup の "
         "config/urg_<urg_interface>.param.yaml を使う。"),
    ]


def declare_shared_args():
    """LiDAR ドライバの共通引数を宣言する。2 つの launch ファイルが同じものを使う。"""
    declarations = []
    for name, default, description in _shared_arg_specs():
        kwargs = {"default_value": default}
        if description is not None:
            kwargs["description"] = description
        declarations.append(DeclareLaunchArgument(name, **kwargs))
    return declarations


def include_lidar_bringup(pkg_share):
    """親 (robot_bringup) から lidar_bringup.launch.py を include する。

    共通引数はすべて素通しする。素通しの一覧を人手で書くと引数を足したときに
    入れ忘れるので、引数表からそのまま作る。use_sim_time は共通引数の表には無い
    (親も子もそれぞれの意味で宣言している) ので明示的に足す。

    overrides / extra_params_file も素通しする。親と同じ overrides で、子が読む
    設定ファイル (urg) も上書きできるようにするため。表に入れずここで足して
    いるのは、親が params.declare_args で先に宣言しているから (二重宣言になる)。
    """
    names = [name for name, _, _ in _shared_arg_specs()]
    names += ["use_sim_time", "overrides", "extra_params_file"]
    return IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_share, "launch", "lidar_bringup.launch.py")
        ),
        launch_arguments=[(name, LaunchConfiguration(name)) for name in names],
    )


def _resolve_urg_params(context):
    """lidar:=2d の URG パラメータファイルを決める。

    既定は上流 raspicat_bringup の config/urg_<urg_interface>.param.yaml。
    raspicat_bringup が無い環境でも lidar:=mid360 は起動できるよう、share
    ディレクトリの解決は 2d を選んだときまで遅らせてある (import 時ではなく
    ここで呼ぶ)。

    Returns:
        (パス, 追加する action の並び)
    """
    path = value(context, "urg_params_file")
    if path:
        return path, []

    interface = value(context, "urg_interface")
    if interface not in ("serial", "ethernet"):
        raise RuntimeError(
            f"Unsupported urg_interface: {interface}. "
            "Use urg_interface:=serial or urg_interface:=ethernet."
        )
    try:
        urg_share = get_package_share_directory("raspicat_bringup")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "raspicat_bringup package is not available, so the default "
            "URG parameters cannot be located.\n"
            "Import raspicat_ros (vcs import src < daifuku_autonomous.repos) "
            "and build it, or pass urg_params_file:=<path> explicitly."
        ) from exc

    path = os.path.join(urg_share, "config", f"urg_{interface}.param.yaml")
    # 解決した先を Node の parameters= から読めるようにする。
    return path, [SetLaunchConfiguration("urg_params_file", path)]


def validate(context, *args, **kwargs):
    """lidar_bringup.launch.py の起動前チェック (OpaqueFunction)。

    選んだ構成で実際に読むファイルだけを見る。存在しないファイルを指したまま
    起動すると、ノードが黙って既定値で上がったり後段だけが止まったりして
    原因が分かりにくいので、ここで落とす。
    """
    selected = value(context, "lidar")
    if selected not in ("2d", "mid360"):
        raise RuntimeError(
            f"Unsupported lidar: {selected}. Use lidar:=2d or lidar:=mid360."
        )

    driver = is_true(context, "lidar_driver")
    actions = []
    files = []

    if selected == "2d" and driver:
        path, extra_actions = _resolve_urg_params(context)
        actions += extra_actions
        files.append(("urg_params_file", path))

    # MID360_config.json は livox_ros_driver2 のためだけのもの。
    # lidar_driver:=false (シム) では driver を立てないので要求しない。
    if selected == "mid360" and driver:
        files.append(("mid360_config", value(context, "mid360_config")))

    for label, path in files:
        if not os.path.isfile(path):
            raise RuntimeError(f"{label} does not exist: {path}")
    return actions
