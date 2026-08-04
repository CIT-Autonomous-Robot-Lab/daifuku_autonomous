# 保存済みの地図で自律移動する。
#
# 選べる組み合わせは 3 通り (localization x planner)。どれを選んでも
# lidar_bringup.launch.py と RViz (use_rviz:=true のとき) は共通で、違うのは
# その下のスタックだけ。
#
#   localization:=amcl  + planner:=navfn -> nav2 の bringup_launch.py をそのまま
#   localization:=amcl  + planner:=vi    -> nav2 の localization_launch.py +
#                                           vi_global_planner の navigation_launch.py
#   localization:=emcl2 + navfn / vi     -> map_server + emcl2 を自前で立て、
#                                           その上に nav2 / vi の navigation
#
# emcl2 は nav2 のノードではないので、標準の bringup には乗らない。map_server と
# lifecycle_manager_localization をここで立てているのはそのため。
#
# パラメータの合成規則は daifuku_stack_launch/params.py と config/README.md、
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

from daifuku_stack_launch import backends, params  # noqa: E402
from daifuku_stack_launch import lidar as lidar_common  # noqa: E402


def generate_launch_description():
    pkg_share = get_package_share_directory("daifuku_stack")
    nav2_share = get_package_share_directory("nav2_bringup")

    default_params_dir = os.path.join(pkg_share, "config", "nav2")
    overrides_dir = os.path.join(pkg_share, "config", "overrides")
    # Pi4 高負荷時の bond 4 秒タイムアウト対策 (詳細はファイル内コメント参照)。
    # nav2 の navigation_launch.py はマネージャに bond_timeout を渡せないため、
    # SetParametersFromFile でグループスコープ内の全ノードに注入する。
    default_bond_params = os.path.join(pkg_share, "config", "lifecycle_bond.yaml")
    default_emcl2_params = os.path.join(pkg_share, "config", "localization", "emcl2.yaml")
    default_rviz_config = os.path.join(pkg_share, "rviz", "navigation.rviz")
    default_map = os.path.join(pkg_share, "maps", "map_19f.yaml")

    bringup_launch = os.path.join(nav2_share, "launch", "bringup_launch.py")
    navigation_launch = os.path.join(nav2_share, "launch", "navigation_launch.py")
    localization_launch = os.path.join(nav2_share, "launch", "localization_launch.py")
    # planner:=vi 用: planner_server の代わりに価値反復プランナを起動する
    # navigation_launch.py の vi 版 (vi_global_planner パッケージが提供)。
    # local_planner:=vi なら vi_planner 1 ノード (両アクション)、local_planner:=nav2 なら
    # vi_global_planner + controller_server を立てる (排他)。
    # vi_global_planner 未インストールでも planner:=navfn で起動できるよう、パス解決は
    # include 実行時 (条件成立時) まで遅延させる。
    vi_navigation_launch = PathJoinSubstitution(
        [FindPackageShare("vi_global_planner"), "launch", "navigation_launch.py"]
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

        これらのキーは config/nav2/*.yaml に存在しないので SetParameter (グループ全体への
        注入) で足りる。逆に params_file に**ある**キーは SetParameter /
        SetParametersFromFile では上書きできない (launch_ros は global params を先に、
        ノード個別の parameters= を後に渡すため、後勝ちでノード側が勝つ)。
        extra_params_file の上書きが効かないのはこれが理由なので、あちらは
        params.compose で params_file 自体をマージして解決する。
        """
        bt_dir = PathJoinSubstitution([FindPackageShare("daifuku_stack"), "behavior_trees"])
        return GroupAction(
            condition=IfCondition(use_vi),
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
                condition=IfCondition(use_composition),
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
            navigation_include(vi_navigation_launch, pose_topic="amcl_pose"),
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
                condition=IfCondition(use_vi),
            ),
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
            default_value=default_map,
            description="Full path to the map yaml file.",
        ),

        # --- パラメータの合成 (daifuku_stack_launch/params.py) ---
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
        *params.declare_args(overrides_dir),
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
                        "(vi_global_planner + controller_server/DWB, the wiring "
                        "needed for maps that use map_scale / the compact solver).",
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

        # --- LiDAR (daifuku_stack_launch/lidar.py。lidar_bringup と共通) ---
        *lidar_common.declare_shared_args(pkg_share),
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
                "overrides_dir": overrides_dir,
                "targets": ["params_file", "emcl2_params_file", "bond_params_file"],
            },
        ),
        OpaqueFunction(function=params.validate_map_file),
        OpaqueFunction(function=backends.validate_localization),
        OpaqueFunction(function=backends.validate_planner),
        OpaqueFunction(
            function=backends.validate_local_planner,
            kwargs={"effective_local_planner": effective_local_planner},
        ),

        lidar_common.include_lidar_bringup(pkg_share),

        amcl_navfn_stack,
        amcl_vi_stack,
        emcl2_stack,

        system_monitor,
        rviz,
    ])
