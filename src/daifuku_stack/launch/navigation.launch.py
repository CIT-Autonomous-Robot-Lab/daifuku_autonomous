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
# **センサは立てない。** LiDAR (/scan) も EKF (/odom, odom -> base_footprint) も
# robot_bringup.launch.py の受け持ちで、そちらは docker compose up で常駐している。
# ここはその消費者に徹する。手元で単独に立てるときは先に
# `ros2 launch daifuku_bringup robot_bringup.launch.py` を通しておくこと
# (/scan が来ないと emcl2 も costmap も動かない)。
#
# 選べる組み合わせは 3 通り (localization x planner)。どれを選んでも RViz
# (use_rviz:=true のとき) は共通で、違うのはその下のスタックだけ。
#
#   localization:=amcl  + planner:=navfn -> nav2 の bringup_launch.py をそのまま
#   localization:=amcl  + planner:=vi    -> nav2 の localization_launch.py +
#                                           vi_planner の navigation_launch.py
#   localization:=emcl2 + navfn / vi     -> map_server + emcl2 を自前で立て、
#                                           その上に nav2 / vi の navigation
#
# emcl2 は nav2 のノードではないので、標準の bringup には乗らない。map_server と
# lifecycle_manager_localization をここで立てているのはそのため。
#
# その navigation を **Nav2 抜き**で組むのが nav2:=false で、これが**既定**。
# vi_planner が standalone モードで navigate_to_pose と follow_waypoints も
# 提供するので、bt_navigator / behavior_server / waypoint_follower /
# smoother_server を立てない。アクション型は nav2_msgs のままなので
# RViz も各パネルも配線は変わらない。残る Nav2 のノードは map_server
# (localization 側) と、velocity_smoother:=true なら velocity_smoother。
# lifecycle_manager_navigation は**名前は同じまま残る**が、管理下はその
# velocity_smoother 1 つだけになる (velocity_smoother:=false なら消える)。
# 何が変わるか・何を読まなくてよくなるかは docs/usage/architecture.md。
#
# パラメータの合成規則は daifuku_config_manager/params.py と config/README.md、
# バックエンドの選択規則は daifuku_stack_launch/backends.py を参照。

import os
import sys

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
    OpaqueFunction,
    SetEnvironmentVariable,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    LaunchConfiguration,
    PathJoinSubstitution,
    PythonExpression,
)
from launch_ros.actions import Node, PushRosNamespace, SetParameter, SetParametersFromFile
from launch_ros.descriptions import ParameterFile
from launch_ros.substitutions import FindPackageShare
from nav2_common.launch import RewrittenYaml

from ament_index_python.packages import get_package_share_directory

# 共通部品はこの launch ディレクトリの直下 (daifuku_stack_launch/) にある。
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from daifuku_config_manager import params  # noqa: E402
from daifuku_stack_launch import backends, nav2_params  # noqa: E402


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
    default_rviz_config = os.path.join(pkg_share, "rviz", "navigation.rviz")

    bringup_launch = os.path.join(nav2_share, "launch", "bringup_launch.py")
    navigation_launch = os.path.join(nav2_share, "launch", "navigation_launch.py")
    localization_launch = os.path.join(nav2_share, "launch", "localization_launch.py")
    # planner:=vi 用: planner_server の代わりに価値反復プランナを起動する
    # navigation_launch.py の vi 版 (vi_planner パッケージが提供。2026-08-08 の上流の
    # 整理で vi_global_planner パッケージごと消え、こちらへ移った)。**どちらの
    # local_planner でも立つノードは vi_planner 1 つ**で、local_planner:=vi なら両
    # アクション、local_planner:=nav2 なら follow: false (広域のみ) + controller_server。
    # vi_planner 未インストールでも planner:=navfn で起動できるよう、パス解決は
    # include 実行時 (条件成立時) まで遅延させる。
    vi_navigation_launch = PathJoinSubstitution(
        [FindPackageShare("vi_planner"), "launch", "navigation_launch.py"]
    )

    namespace = LaunchConfiguration("namespace")
    use_namespace = LaunchConfiguration("use_namespace")
    map_yaml = LaunchConfiguration("map")
    params_file = LaunchConfiguration("params_file")
    emcl2_params_file = LaunchConfiguration("emcl2_params_file")
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
    planner = LaunchConfiguration("planner")
    local_planner = LaunchConfiguration("local_planner")
    nav2 = LaunchConfiguration("nav2")
    use_velocity_smoother = LaunchConfiguration("velocity_smoother")

    # ------------------------------------------------------------------
    # どのスタックを立てるかの条件
    # ------------------------------------------------------------------
    use_emcl2 = PythonExpression(["'", localization, "' in ['emcl', 'emcl2']"])
    use_amcl_navfn = PythonExpression(
        ["'", localization, "' == 'amcl' and '", planner, "' == 'navfn'"]
    )
    use_amcl_vi = PythonExpression(
        ["'", localization, "' == 'amcl' and '", planner, "' == 'vi'"]
    )
    # emcl2 スタックの中で navigation をどちらにするか。
    use_navfn = PythonExpression(["'", planner, "' == 'navfn'"])
    use_vi = PythonExpression(["'", planner, "' == 'vi'"])
    effective_local_planner = backends.resolve_local_planner(planner, local_planner)
    # Nav2 のノードを立てるか ("true" / "false")。**既定は false**。
    # auto は「VI が 1 ノードで全部やれるなら立てない」で、planner:=navfn へ
    # 落とすときに要る (既定のままだと backends.validate_nav2 が弾く)。
    effective_nav2 = backends.resolve_nav2(nav2, planner, effective_local_planner)
    use_nav2 = PythonExpression(["'", effective_nav2, "' == 'true'"])
    # vi_planner 1 ノード構成 (Nav2 抜き)。
    use_standalone = PythonExpression(["'", effective_nav2, "' != 'true'"])
    # standalone で velocity_smoother を挟むか。挟むなら vi_planner の cmd_vel は
    # cmd_vel_nav へ (Nav2 構成と同じ配線)、挟まないなら cmd_vel をそのまま出す。
    # **どちらでも twist_mux から先は変わらない**。
    use_smoother = PythonExpression(
        [use_standalone, " and '", use_velocity_smoother, "'.lower() in ('true', '1')"]
    )
    # vi_planner の cmd_vel の行き先。挟むなら velocity_smoother の入力
    # (cmd_vel_nav)、挟まないなら twist_mux の入力 (cmd_vel) へ直接。
    # リマップ先を substitution にしてあるのは、Node を 2 つに割らずに済ませるため。
    vi_cmd_vel_topic = PythonExpression(
        ["'cmd_vel_nav' if ", use_smoother, " else 'cmd_vel'"]
    )

    # ------------------------------------------------------------------
    # スタックの部品
    # ------------------------------------------------------------------
    remappings = [("/tf", "tf"), ("/tf_static", "tf_static")]
    emcl2_remappings = remappings + [
        ("particlecloud", "particle_cloud"),
        ("global_localization", "reinitialize_global_localization"),
    ]

    configured_map_server_params = ParameterFile(
        RewrittenYaml(
            source_file=params_file,
            root_key=namespace,
            param_rewrites={
                "use_sim_time": use_sim_time,
                "yaml_filename": map_yaml,
            },
            convert_types=True,
        ),
        allow_substs=True,
    )

    configured_nav2_params = ParameterFile(
        RewrittenYaml(
            source_file=params_file,
            root_key=namespace,
            param_rewrites={
                "use_sim_time": use_sim_time,
                "autostart": autostart,
            },
            convert_types=True,
        ),
        allow_substs=True,
    )

    def vi_behavior_tree_params():
        """planner:=vi のときだけ BT を差し替える。

        planner:=vi では planner_server (と local_planner:=vi なら controller_server) を
        起動しないため、nav2 既定の BT が要求する compute_path_through_poses アクションと
        */clear_entirely_*_costmap サービスが存在せず、bt_navigator が on_configure で
        例外を投げて bringup 全体が止まる。nav2 1.1.20 には Iron 以降の `navigators`
        パラメータが無く through_poses を無効化できないので、木そのものを VI 用
        (behavior_trees/) に差し替える。

        これらのキーは config/stack/nav2/*.yaml に存在しないので SetParameter (グループ全体への
        注入) で足りる。逆に params_file に**ある**キーは SetParameter /
        SetParametersFromFile では上書きできない (launch_ros は global params を先に、
        ノード個別の parameters= を後に渡すため、後勝ちでノード側が勝つ)。
        extra_params_file の上書きが効かないのはこれが理由なので、あちらは
        params.compose で params_file 自体をマージして解決する。
        """
        bt_dir = PathJoinSubstitution([FindPackageShare("daifuku_stack"), "behavior_trees"])
        return GroupAction(
            # nav2:=false では bt_navigator を立てないので、差し替える木も無い。
            condition=IfCondition(PythonExpression([use_vi, " and ", use_nav2])),
            scoped=False,
            actions=[
                SetParameter(
                    "default_nav_to_pose_bt_xml",
                    PathJoinSubstitution([bt_dir, "navigate_to_pose_vi.xml"]),
                ),
                SetParameter(
                    "default_nav_through_poses_bt_xml",
                    PathJoinSubstitution([bt_dir, "nav_through_poses_stub.xml"]),
                ),
            ],
        )

    def stack_prelude():
        """自前で組むスタック (amcl+vi / emcl2) に共通する頭。

        名前空間、bond タイムアウト、VI 用 BT、use_composition:=True のときの
        コンポーネントコンテナ。以降のノードと include はこのスコープに入る。
        """
        return [
            PushRosNamespace(
                condition=IfCondition(use_namespace),
                namespace=namespace,
            ),
            SetParametersFromFile(bond_params_file),
            vi_behavior_tree_params(),
            Node(
                # nav2:=false では中身が 1 つも入らない (vi_planner は composable
                # ではない) ので、空のコンテナを立てない。
                condition=IfCondition(
                    PythonExpression(
                        ["'", use_composition, "'.lower() in ('true', '1') and ", use_nav2]
                    )
                ),
                name="nav2_container",
                package="rclcpp_components",
                executable="component_container_isolated",
                parameters=[configured_nav2_params, {"autostart": autostart}],
                arguments=["--ros-args", "--log-level", log_level],
                remappings=remappings,
                output="screen",
            ),
        ]

    def emcl2_localization_nodes():
        """emcl2 による自己位置推定一式 (nav2 の localization_launch.py の代わり)。"""
        return [
            Node(
                package="nav2_map_server",
                executable="map_server",
                name="map_server",
                output="screen",
                respawn=use_respawn,
                respawn_delay=2.0,
                parameters=[configured_map_server_params],
                arguments=["--ros-args", "--log-level", log_level],
                remappings=remappings,
            ),
            Node(
                package=LaunchConfiguration("emcl2_package"),
                executable=LaunchConfiguration("emcl2_executable"),
                name=LaunchConfiguration("emcl2_node_name"),
                output="screen",
                respawn=use_respawn,
                respawn_delay=2.0,
                parameters=[
                    emcl2_params_file,
                    {"use_sim_time": use_sim_time},
                ],
                arguments=["--ros-args", "--log-level", log_level],
                remappings=emcl2_remappings,
            ),
            # emcl2 自身はライフサイクルノードではないので、マネージャが見るのは
            # map_server だけ。
            Node(
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_localization",
                output="screen",
                arguments=["--ros-args", "--log-level", log_level],
                parameters=[
                    {"use_sim_time": use_sim_time},
                    {"autostart": autostart},
                    {"node_names": ["map_server"]},
                ],
            ),
        ]

    def navigation_include(source, pose_topic=None, condition=None):
        """navigation 側 (planner / controller / bt_navigator ...) を include する。

        source が nav2 の navigation_launch.py なら標準構成、vi 版なら
        planner_server の代わりに価値反復プランナが立つ。pose_topic は vi 版だけが
        受ける引数で、どの自己位置推定の出力を追うかを渡す。
        """
        launch_arguments = {
            "namespace": namespace,
            "use_sim_time": use_sim_time,
            "params_file": params_file,
            "autostart": autostart,
            "use_composition": use_composition,
            "use_respawn": use_respawn,
            "container_name": "nav2_container",
            "log_level": log_level,
        }
        if pose_topic is not None:
            launch_arguments["pose_topic"] = pose_topic
            launch_arguments["local_planner"] = effective_local_planner
        return IncludeLaunchDescription(
            PythonLaunchDescriptionSource(source),
            condition=condition,
            launch_arguments=launch_arguments.items(),
        )

    def standalone_navigation(pose_topic):
        """nav2:=false: vi_planner 1 ノードだけで navigation を組む。

        vi_planner の navigation_launch.py を include する代わりに
        vi_planner を直接立てる。あちらは Nav2 のスタック (bt_navigator /
        behavior_server / waypoint_follower / smoother_server / velocity_smoother
        + lifecycle_manager) を組む launch なので、Nav2 を立てないならもう
        通り道が無い。

        `standalone: true` がノード側の切り替えで、これが navigate_to_pose と
        follow_waypoints を生やす。**params_file 側でこのキーを立ててはいけない** —
        Nav2 構成のときに立っていると navigate_to_pose のサーバが bt_navigator と
        2 つになり、クライアントは先に見つけたほうへ繋ぐ (どちらに繋がったかは
        どこにも出ない)。だからここ、launch 引数と 1 対 1 の場所で渡す。

        velocity_smoother は残せるようにしてある (既定 true)。VI の cmd_vel は
        10Hz の離散な行動そのもので、Nav2 構成ではこれを通してから車輪へ送って
        いた。外すのは 1 引数だが、加減速の当たりが変わるので既定は据え置き。
        """
        smoother_nodes = [
            Node(
                condition=IfCondition(use_smoother),
                package="nav2_velocity_smoother",
                executable="velocity_smoother",
                name="velocity_smoother",
                output="screen",
                respawn=use_respawn,
                respawn_delay=2.0,
                parameters=[configured_nav2_params],
                arguments=["--ros-args", "--log-level", log_level],
                remappings=remappings
                + [("cmd_vel", "cmd_vel_nav"), ("cmd_vel_smoothed", "cmd_vel")],
            ),
            # velocity_smoother は lifecycle ノードなので、起こす者が要る。
            # **管理下はこれ 1 つだけ** (vi_planner は rclrs に lifecycle が無く、
            # 非 lifecycle ノードとしてただ動く)。bond の心拍も 1 本しか無いので、
            # Nav2 構成で起きていた「高負荷でマネージャが CRITICAL FAILURE」は
            # ここでは起こりにくい。
            Node(
                condition=IfCondition(use_smoother),
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_navigation",
                output="screen",
                arguments=["--ros-args", "--log-level", log_level],
                parameters=[
                    {"use_sim_time": use_sim_time},
                    {"autostart": autostart},
                    {"node_names": ["velocity_smoother"]},
                ],
            ),
        ]
        return GroupAction(
            condition=IfCondition(use_standalone),
            actions=[
                Node(
                    package="vi_planner",
                    executable="vi_planner",
                    name="vi_planner",
                    output="screen",
                    respawn=use_respawn,
                    respawn_delay=2.0,
                    parameters=[
                        configured_nav2_params,
                        {
                            "use_sim_time": use_sim_time,
                            "standalone": True,
                            "pose_topic": pose_topic,
                            "scan_topic": "scan",
                        },
                    ],
                    arguments=["--ros-args", "--log-level", log_level],
                    remappings=remappings + [("cmd_vel", vi_cmd_vel_topic)],
                ),
                *smoother_nodes,
            ],
        )

    # ------------------------------------------------------------------
    # スタック
    # ------------------------------------------------------------------
    # amcl + navfn: nav2 標準の bringup がそのまま使える。
    amcl_navfn_stack = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(bringup_launch),
        condition=IfCondition(use_amcl_navfn),
        launch_arguments={
            "namespace": namespace,
            "use_namespace": use_namespace,
            "slam": "False",
            "map": map_yaml,
            "use_sim_time": use_sim_time,
            "params_file": params_file,
            "autostart": autostart,
            "use_composition": use_composition,
            "use_respawn": use_respawn,
            "log_level": log_level,
        }.items(),
    )

    # amcl + vi: 標準 bringup は planner_server 込みなので、localization と
    # vi 版 navigation を個別に include する。
    amcl_vi_stack = GroupAction(
        condition=IfCondition(use_amcl_vi),
        actions=stack_prelude() + [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(localization_launch),
                launch_arguments={
                    "namespace": namespace,
                    "map": map_yaml,
                    "use_sim_time": use_sim_time,
                    "params_file": params_file,
                    "autostart": autostart,
                    "use_composition": use_composition,
                    "use_respawn": use_respawn,
                    "container_name": "nav2_container",
                }.items(),
            ),
            navigation_include(
                vi_navigation_launch,
                pose_topic="amcl_pose",
                condition=IfCondition(use_nav2),
            ),
            standalone_navigation("amcl_pose"),
        ],
    )

    # emcl2 + navfn / vi: 自己位置推定を自前で立て、その上に navigation を載せる。
    emcl2_stack = GroupAction(
        condition=IfCondition(use_emcl2),
        actions=stack_prelude() + emcl2_localization_nodes() + [
            navigation_include(navigation_launch, condition=IfCondition(use_navfn)),
            navigation_include(
                vi_navigation_launch,
                pose_topic="mcl_pose",
                condition=IfCondition(PythonExpression([use_vi, " and ", use_nav2])),
            ),
            standalone_navigation("mcl_pose"),
        ],
    )

    # CPU を /diagnostics に出す。ここ (ros2 コンテナ) に置くのは、プロセス別の
    # 内訳が同じ PID 名前空間の中しか見えないため。robot_bringup 側へ移すと
    # nav2 と VI が見えなくなる。
    system_monitor = Node(
        condition=IfCondition(use_system_monitor),
        package="daifuku_stack",
        executable="system_monitor.py",
        name="system_monitor",
        namespace=namespace,
        output="screen",
        parameters=[{"use_sim_time": use_sim_time}],
    )

    rviz = Node(
        condition=IfCondition(use_rviz),
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        namespace=namespace,
        output="screen",
        arguments=["-d", rviz_config],
        parameters=[{"use_sim_time": use_sim_time}],
    )

    # ------------------------------------------------------------------
    # 起動引数
    # ------------------------------------------------------------------
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
            description="地図の yaml (フルパス)。**空 (既定) なら overrides の "
                        "site: map: が指すもの。** 明示すると overrides の宣言と"
                        "同じものかを見る (違えば起動時にエラー)。"
                        "場所ごと変えるのは tools/site.sh。",
        ),

        # --- パラメータの合成 (daifuku_config_manager/params.py) ---
        DeclareLaunchArgument(
            "params_file",
            default_value="",
            description="nav2 パラメータを 1 ファイルで与える (空なら params_dir の "
                        "断片を合成する)。指定すると params_dir は無視される。",
        ),
        DeclareLaunchArgument(
            "params_dir",
            default_value=default_params_dir,
            description="合成する nav2 パラメータ断片のディレクトリ。"
                        "*.yaml をファイル名順に深くマージする (config/README.md)。",
        ),
        *params.declare_args(),
        params.declare_watch_arg(),
        DeclareLaunchArgument("emcl2_params_file", default_value=default_emcl2_params),
        DeclareLaunchArgument("bond_params_file", default_value=default_bond_params),

        # --- バックエンドの選択 (daifuku_stack_launch/backends.py) ---
        DeclareLaunchArgument(
            "localization",
            default_value="emcl2",
            description="Localization backend: amcl or emcl2.",
        ),
        DeclareLaunchArgument("emcl2_package", default_value="emcl2"),
        DeclareLaunchArgument("emcl2_executable", default_value="emcl2_node"),
        DeclareLaunchArgument("emcl2_node_name", default_value="emcl2"),
        DeclareLaunchArgument(
            "planner",
            default_value="vi",
            description="Global planner backend: vi (value iteration) or navfn.",
        ),
        DeclareLaunchArgument(
            "local_planner",
            default_value="auto",
            description="Local planner backend: auto (follow the global planner: "
                        "planner:=vi -> vi, otherwise nav2), vi (vi_planner — one "
                        "node serving compute_path_to_pose and follow_path from a "
                        "single value function; requires planner:=vi) or nav2 "
                        "(the same vi_planner with follow: false + controller_server/DWB, "
                        "the wiring "
                        "needed for maps that use map_scale / the compact solver).",
        ),
        DeclareLaunchArgument(
            "nav2",
            default_value="false",
            description="Bring up the Nav2 navigation nodes? false (default) runs "
                        "vi_planner standalone: it serves navigate_to_pose and "
                        "follow_waypoints itself, so bt_navigator, behavior_server, "
                        "waypoint_follower, smoother_server and "
                        "lifecycle_manager_navigation are not launched. The action "
                        "types stay nav2_msgs, so RViz and the panels are unchanged. "
                        "true brings the Nav2 stack up. auto follows the planner "
                        "(false only when planner:=vi resolves the local planner to "
                        "vi, true otherwise) — needed when falling back to "
                        "planner:=navfn, which the default would otherwise reject.",
        ),
        DeclareLaunchArgument(
            "velocity_smoother",
            default_value="true",
            description="nav2:=false only: keep nav2_velocity_smoother between "
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

        # このスタックが読む設定ファイルへ overrides を重ね、差し替える。以降の
        # 参照 (RewrittenYaml / 各 include / 下の検証) はこの合成結果を見る。
        # どの節がどのファイルへ行くかはノード名で決まる (params.py)。
        OpaqueFunction(
            function=params.compose,
            kwargs={
                "package": "daifuku_stack",
                "config_root": config_root,
                "targets": ["params_file", "emcl2_params_file", "bond_params_file"],
                # params_file だけは config/stack/nav2/*.yaml の合成が土台になる。
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
        OpaqueFunction(function=backends.validate_localization),
        OpaqueFunction(function=backends.validate_planner),
        OpaqueFunction(
            function=backends.validate_local_planner,
            kwargs={"effective_local_planner": effective_local_planner},
        ),
        OpaqueFunction(
            function=backends.validate_nav2,
            kwargs={
                "effective_nav2": effective_nav2,
                "effective_local_planner": effective_local_planner,
            },
        ),

        amcl_navfn_stack,
        amcl_vi_stack,
        emcl2_stack,

        system_monitor,
        rviz,
    ])
