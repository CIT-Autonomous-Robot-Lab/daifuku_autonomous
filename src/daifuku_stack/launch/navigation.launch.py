# Copyright 2026 Keita Sekiguchi / nop
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

# 保存済みの地図で自律移動する。
#
# **センサのドライバは立てない。** LiDAR の生データ (/livox/lidar か /scan_raw) も
# EKF (/odom, odom -> base_footprint) も robot_bringup.launch.py の受け持ちで、
# そちらは docker compose up で常駐している。手元で単独に立てるときは先に
# `ros2 launch daifuku_bringup robot_bringup.launch.py` を通しておくこと。
#
# **/scan を作る段だけはこちらが持つ** (scan_pipeline.launch.py を include する)。
# 帯と仰角は場所ごとに変わる値で、それを持つのがこの段だけだったため。機体側に
# 置いていた 2026-08-25 までは、常駐している機体が src/daifuku_config/site を読む
# ことになっていて、地図を変えるたびに `docker compose restart raspicat` が要った。
#
# Navigation is always provided by standalone vi_planner. Localization may use
# AMCL, emcl2, emcl3d, or VIOLA; validate_localization checks the VI config.
#
# パラメータの合成規則は daifuku_config_manager/params.py と src/daifuku_config/README.md、
# バックエンドの選択規則は daifuku_stack_launch/backends.py、ノードの並べは
# daifuku_stack_launch/navigation_stacks.py を参照。

import os
import sys
from types import SimpleNamespace

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    OpaqueFunction,
    SetEnvironmentVariable,
)
from launch.substitutions import (
    LaunchConfiguration,
    PythonExpression,
)

from ament_index_python.packages import get_package_share_directory

# 共通部品はこの launch ディレクトリの直下 (daifuku_stack_launch/) にある。
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from daifuku_config_manager import params  # noqa: E402
from daifuku_stack_launch import backends, nav2_params, navigation_stacks, scan  # noqa: E402


def generate_launch_description():
    pkg_share = get_package_share_directory("daifuku_stack")
    nav2_share = get_package_share_directory("nav2_bringup")

    config_root = params.config_root("daifuku_stack")
    default_params_dir = os.path.join(config_root, "nav2")
    # Pi4 高負荷時の bond 4 秒タイムアウト対策 (詳細はファイル内コメント参照)。
    # nav2 の navigation_launch.py はマネージャに bond_timeout を渡せないため、
    # SetParametersFromFile でグループスコープ内の全ノードに注入する。
    default_bond_params = os.path.join(config_root, "lifecycle_bond.yaml")
    default_emcl2_params = os.path.join(config_root, "localization", "emcl2.yaml")
    default_emcl3d_params = os.path.join(config_root, "localization", "emcl3d.yaml")
    default_rviz_config = os.path.join(pkg_share, "rviz", "navigation.rviz")

    localization_launch = os.path.join(nav2_share, "launch", "localization_launch.py")

    namespace = LaunchConfiguration("namespace")
    use_namespace = LaunchConfiguration("use_namespace")
    # **地図は 2 枚**。map:= が経路計画用 (/map)、map_loc:= が自己位置推定用
    # (/map_loc)。どちらも overrides の site: map: から来る (nav2_params.resolve_map)。
    map_yaml = LaunchConfiguration("map")
    map_loc_yaml = LaunchConfiguration("map_loc")
    params_file = LaunchConfiguration("params_file")
    emcl2_params_file = LaunchConfiguration("emcl2_params_file")
    emcl3d_params_file = LaunchConfiguration("emcl3d_params_file")
    map_pcd = LaunchConfiguration("map_pcd")
    bond_params_file = LaunchConfiguration("bond_params_file")
    rviz_config = LaunchConfiguration("rviz_config")
    use_sim_time = LaunchConfiguration("use_sim_time")
    autostart = LaunchConfiguration("autostart")
    use_composition = LaunchConfiguration("use_composition")
    use_respawn = LaunchConfiguration("use_respawn")
    log_level = LaunchConfiguration("log_level")
    use_rviz = LaunchConfiguration("use_rviz")
    use_system_monitor = LaunchConfiguration("use_system_monitor")
    localization = LaunchConfiguration("localization")
    use_velocity_smoother = LaunchConfiguration("velocity_smoother")

    # どのスタックを立てるかの条件
    use_emcl2 = PythonExpression(["'", localization, "' in ['emcl', 'emcl2']"])
    # 自己位置推定も vi_planner に持たせる (VIOLA)。emcl2 を立てないだけで、
    # map_server から下は emcl2 構成と同じものを使う。
    use_vi_loc = PythonExpression(["'", localization, "' == 'vi'"])
    # emcl2 と vi は「map_server を自前で立てる」側で同じ。違うのは emcl2 の
    # ノードを立てるかと、vi_planner へ渡す pose_topic / publish_tf だけ。
    # emcl3d も同じ側。map_server_loc は立つが誰も購読しない (emcl3d は PCD を直に読む)。
    use_emcl3d = PythonExpression(["'", localization, "' == 'emcl3d'"])
    use_own_map_server = PythonExpression(
        [use_emcl2, " or ", use_emcl3d, " or ", use_vi_loc]
    )
    use_amcl = PythonExpression(["'", localization, "' == 'amcl'"])
    use_smoother = PythonExpression(
        ["'", use_velocity_smoother, "'.lower() in ('true', '1')"]
    )
    # vi_planner の cmd_vel の行き先。挟むなら velocity_smoother の入力
    # (cmd_vel_nav)、挟まないなら twist_mux の入力 (cmd_vel) へ直接。
    # リマップ先を substitution にしてあるのは、Node を 2 つに割らずに済ませるため。
    vi_cmd_vel_topic = PythonExpression(
        ["'cmd_vel_nav' if ", use_smoother, " else 'cmd_vel'"]
    )
    # localization:=vi では pose_topic の意味が変わる。内蔵推定器にとってあれは
    # 「自己位置の連続入力」ではなく**手動シード**なので、mcl_pose (emcl2 の 20Hz
    # 出力) に向けたままだと毎メッセージで belief が張り直され、**エラーも警告も
    # 出ないまま素通しと変わらなくなる**。RViz の 2D Pose Estimate が出す
    # initialpose に向ける。
    own_pose_topic = PythonExpression(
        ["'initialpose' if ", use_vi_loc, " else 'mcl_pose'"]
    )

    stack_actions = navigation_stacks.assemble(SimpleNamespace(
        namespace=namespace,
        use_namespace=use_namespace,
        map_yaml=map_yaml,
        map_loc_yaml=map_loc_yaml,
        params_file=params_file,
        emcl2_params_file=emcl2_params_file,
        emcl3d_params_file=emcl3d_params_file,
        map_pcd=map_pcd,
        bond_params_file=bond_params_file,
        rviz_config=rviz_config,
        use_sim_time=use_sim_time,
        autostart=autostart,
        use_composition=use_composition,
        use_respawn=use_respawn,
        log_level=log_level,
        use_rviz=use_rviz,
        use_system_monitor=use_system_monitor,
        use_emcl2=use_emcl2,
        use_emcl3d=use_emcl3d,
        use_vi_loc=use_vi_loc,
        use_own_map_server=use_own_map_server,
        use_amcl=use_amcl,
        use_smoother=use_smoother,
        vi_cmd_vel_topic=vi_cmd_vel_topic,
        own_pose_topic=own_pose_topic,
        localization_launch=localization_launch,
    ))

    # 起動引数
    declare_args = [
        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("use_namespace", default_value="false"),
        DeclareLaunchArgument(
            "map",
            # **既定は空 = overrides の site: map: を読む** (nav2_params.resolve_map)。
            # 地図と overrides を別々に渡させると、片方だけ差し替えて別の場所の
            # 帯と emcl2 の調整を載せたまま走れてしまう。明示したときは overrides の
            # 宣言と同じものを指しているかを見て、違えば起動時に止める。
            default_value="",
            description="**経路計画**に使う地図の yaml (フルパス)。/map で配信し、"
                        "vi_planner が読む。**空 (既定) なら "
                        "overrides の site: map: navigation: が指すもの。** 明示"
                        "すると overrides の宣言と同じものかを見る (違えば起動時に"
                        "エラー)。場所ごと変えるのは ros2 param set /site_manager site "
                        "<名前> か tools/site.sh。",
        ),
        DeclareLaunchArgument(
            "map_loc",
            # 決まらないときだけは map:= に落ちる (resolve_map)。**逆向きは無い** —
            # 経路計画の地図を落とすと、別の場所の地図で走り出しかねない。
            default_value="",
            description="**自己位置推定**に使う地図の yaml (フルパス)。/map_loc で"
                        "配信し、emcl2 が読む。**空 (既定) なら overrides の "
                        "site: map: localization: が指すもの**で、それも無ければ "
                        "map:= と同じ地図。2 枚を別にできるのは "
                        "localization:=emcl2 のときだけ。",
        ),

        # --- パラメータの合成 (daifuku_config_manager/params.py) ---
        DeclareLaunchArgument(
            "params_file",
            default_value="",
            description="nav2 パラメータを 1 ファイルで与える (空なら params_dir の "
                        "断片と src/daifuku_config/stack/vi_planner.yaml を合成する)。"
                        "指定時も params_dir の断片を上書き先のノード名の検査に使う。",
        ),
        DeclareLaunchArgument(
            "params_dir",
            default_value=default_params_dir,
            description="合成する nav2 パラメータ断片のディレクトリ。"
                        "*.yaml に src/daifuku_config/stack/vi_planner.yaml (Nav2 のノードでは "
                        "ないので nav2/ の外に居る) を足し、ファイル名順に深く"
                        "マージする (src/daifuku_config/README.md)。",
        ),
        *params.declare_args(),
        params.declare_watch_arg(),
        DeclareLaunchArgument("emcl2_params_file", default_value=default_emcl2_params),
        DeclareLaunchArgument("emcl3d_params_file", default_value=default_emcl3d_params),
        DeclareLaunchArgument(
            "map_pcd",
            # map:= と同じく既定は空 = overrides の site: map: pointcloud: を読む
            # (nav2_params.resolve_map)。
            default_value="",
            description="localization:=emcl3d が読む点群地図 (PCD、フルパス)。"
                        "**空 (既定) なら overrides の site: map: pointcloud: が指すもの。"
                        "** 明示すると overrides の宣言と同じものかを見る。"
                        "他の localization では読まない。",
        ),
        DeclareLaunchArgument("bond_params_file", default_value=default_bond_params),

        # --- バックエンドの選択 (daifuku_stack_launch/backends.py) ---
        DeclareLaunchArgument(
            "localization",
            default_value="emcl2",
            description="Localization backend: amcl, emcl2, emcl3d or vi. emcl3d "
                        "replaces emcl2 with 6DoF MCL against the PCD map "
                        "(map_pcd / site: map: pointcloud:) using /livox/lidar. "
                        "vi drops emcl2 and "
                        "lets vi_planner localize itself (map_server stays): it "
                        "publishes map->odom and takes pose_topic as a manual seed "
                        "(initialpose), not as a pose input. **Which estimator** is "
                        "config's business — set localizer: in "
                        "src/daifuku_config/stack/vi_planner.yaml (or an overrides file) to "
                        "adaptive / grid / belief / viterbi; leaving it external here "
                        "stops the launch.",
        ),
        DeclareLaunchArgument("emcl2_package", default_value="emcl2"),
        DeclareLaunchArgument("emcl2_executable", default_value="emcl2_node"),
        DeclareLaunchArgument("emcl2_node_name", default_value="emcl2"),
        DeclareLaunchArgument(
            "velocity_smoother",
            default_value="true",
            description="Keep nav2_velocity_smoother between "
                        "vi_planner and twist_mux (cmd_vel_nav -> cmd_vel). false "
                        "publishes cmd_vel directly, which removes the last "
                        "lifecycle-managed node from the navigation side.",
        ),

        # --- nav2 共通 ---
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("autostart", default_value="true"),
        # Pi4では全nav2ノードを1プロセスに合成すると、参加者のエンドポイント数が
        # 巨大化して新規参加者からディスカバリ不能になり、CPU飢餓でbond心拍も
        # 途絶して lifecycle manager が CRITICAL FAILURE→自動シャットダウンする
        # 事象が頻発した (2026-07-24)。プロセス分離で参加者を小さく保つ。
        DeclareLaunchArgument("use_composition", default_value="False"),
        DeclareLaunchArgument("use_respawn", default_value="False"),
        DeclareLaunchArgument("log_level", default_value="info"),

        # --- RViz ---
        DeclareLaunchArgument("rviz_config", default_value=default_rviz_config),
        # 実機 (Raspberry Pi) は headless で、docker/raspberrypi/ のイメージにも
        # RViz は入っていない。表示は同じ ROS_DOMAIN_ID の PC 側から開く。
        DeclareLaunchArgument("use_rviz", default_value="false"),

        # --- 監視 ---
        # 1Hz で /proc を読むだけなので CPU は無視できるが、DDS 参加者は 1 つ
        # 増える。ディスカバリが不安定なときはここを false にして切り分ける。
        DeclareLaunchArgument("use_system_monitor", default_value="true"),

    ]

    return LaunchDescription([
        SetEnvironmentVariable("RCUTILS_LOGGING_BUFFERED_STREAM", "1"),

        *declare_args,
        # 点群 (または生スキャン) を /scan に変える段の引数。実体は
        # scan_pipeline.launch.py で、この launch が include する
        # (daifuku_stack_launch/scan.py)。
        *scan.declare_args(),

        # このスタックが読む設定ファイルへ overrides を重ね、差し替える。以降の
        # 参照 (RewrittenYaml / 各 include / 下の検証) はこの合成結果を見る。
        # どの節がどのファイルへ行くかはノード名で決まる (params.py)。
        OpaqueFunction(
            function=params.compose,
            kwargs={
                "package": "daifuku_stack",
                "config_root": config_root,
                "targets": [
                    "params_file", "emcl2_params_file", "emcl3d_params_file",
                    "bond_params_file",
                ],
                # params_file だけは src/daifuku_config/stack/nav2/*.yaml と
                # src/daifuku_config/stack/vi_planner.yaml の合成が土台になる。
                "base_resolvers": {"params_file": nav2_params.fragments_resolver},
            },
        ),
        # map:= を overrides から導く (明示されていれば食い違いを見る)。**compose の
        # 後**に置くこと: overrides:= を読むのはどちらも同じだが、ここで失敗させる
        # なら先に overrides の名前そのものを検査させたほうがエラーが分かりやすい。
        OpaqueFunction(function=nav2_params.resolve_map),
        # 起動後に設定が書き変わったら言い、追随してよければこの launch を落とす。
        # **こちらは誰も上げ直さない** (人が docker compose exec で立てているので)。
        # 落ちたら立て直すこと — 古い設定のまま走り続けるよりはよい。
        OpaqueFunction(
            function=params.sentinel_actions,
            kwargs={"package": "daifuku_stack", "config_root": config_root},
        ),
        # **compose の後**に置くこと。localizer は合成後の params_file から読む。
        OpaqueFunction(function=backends.validate_localization),
        OpaqueFunction(function=backends.validate_vi_package),

        # /scan を作る。**機体側 (robot_bringup) が出すのはセンサそのままの点群まで**
        # で、帯と仰角は場所で変わるのでこちら側に居る。mapping.launch.py も同じ段を
        # 立てるので、**2 つを同時に立てないこと** (/scan の publisher が 2 つになる)。
        scan.include_scan_pipeline(pkg_share),

        *stack_actions,
    ])
