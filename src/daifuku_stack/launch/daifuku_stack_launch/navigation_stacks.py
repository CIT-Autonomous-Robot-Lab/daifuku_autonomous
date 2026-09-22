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

"""navigation.launch.py が組む localization / planner スタック。

引数の宣言と合成・検証は向こうに残し、ここは「どのノードを並べるか」だけを持つ。
amcl+navfn は上流 bringup をそのまま include するので stack_prelude (bond 注入)
を通らない。揃えると上流の引数既定を壊す。
"""

from launch.actions import GroupAction, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node, PushRosNamespace, SetParameter, SetParametersFromFile
from launch_ros.descriptions import ParameterFile, ParameterValue
from launch_ros.substitutions import FindPackageShare
from nav2_common.launch import RewrittenYaml


def assemble(w):
    """3 系統のスタックと system_monitor / RViz を返す。

    w は generate_launch_description が積んだ LaunchConfiguration と条件。
    ネストしたヘルパが閉包で見ていた名前を、属性として渡している。
    """
    namespace = w.namespace
    use_namespace = w.use_namespace
    map_yaml = w.map_yaml
    map_loc_yaml = w.map_loc_yaml
    params_file = w.params_file
    emcl2_params_file = w.emcl2_params_file
    bond_params_file = w.bond_params_file
    rviz_config = w.rviz_config
    use_sim_time = w.use_sim_time
    autostart = w.autostart
    use_composition = w.use_composition
    use_respawn = w.use_respawn
    log_level = w.log_level
    use_rviz = w.use_rviz
    use_system_monitor = w.use_system_monitor
    use_emcl2 = w.use_emcl2
    use_vi_loc = w.use_vi_loc
    use_own_map_server = w.use_own_map_server
    use_amcl_navfn = w.use_amcl_navfn
    use_amcl_vi = w.use_amcl_vi
    use_navfn = w.use_navfn
    use_vi = w.use_vi
    effective_local_planner = w.effective_local_planner
    use_nav2 = w.use_nav2
    use_standalone = w.use_standalone
    use_smoother = w.use_smoother
    vi_cmd_vel_topic = w.vi_cmd_vel_topic
    own_pose_topic = w.own_pose_topic
    bringup_launch = w.bringup_launch
    navigation_launch = w.navigation_launch
    localization_launch = w.localization_launch
    vi_navigation_launch = w.vi_navigation_launch

    remappings = [("/tf", "tf"), ("/tf_static", "tf_static")]
    emcl2_remappings = remappings + [
        ("particlecloud", "particle_cloud"),
        ("global_localization", "reinitialize_global_localization"),
        # **自己位置推定だけが /map_loc を読む。** emcl2 は相対名 "map" を購読する
        # (emcl2_node.cpp) ので、こちらで振り替えられる。/map のほうは経路計画用の
        # 地図で、上流の vi 版 navigation_launch.py と global_costmap が決め打ちで
        # 見にいく先 (どちらもこちらから remap できない)。
        ("map", "map_loc"),
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

        これらのキーは合成される断片のどこにも無いので SetParameter (グループ全体への
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

    def own_localization_nodes():
        """自前の自己位置推定一式 (nav2 の localization_launch.py の代わり)。

        map_server と lifecycle_manager は localization:=emcl2 と
        localization:=vi で共通。**emcl2 のノードだけが emcl2 側**で、vi では
        推定そのものを vi_planner が持つ (standalone_navigation を参照)。

        **map_server は 2 つ立つ。** 経路計画用 (`map_server` -> /map) と自己位置
        推定用 (`map_server_loc` -> /map_loc)。同じ地図を指していても両方立てる —
        止めるなら「2 枚が同じか」で配線を変えることになり、購読側の remap 先まで
        条件付きになる。地図 1 枚ぶんの常駐 (津田沼で 23.5MB) で済むほうを取った。
        """
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
                # 自己位置推定用の 1 枚。**設定ファイルの節を持たない** —
                # map_server が読むのは use_sim_time と yaml_filename だけで、
                # 後者は site: map: localization: から来る。断片に
                # map_server_loc: を足すと RewrittenYaml が yaml_filename を
                # キー名で振り替えるので、2 つが同じ地図になってしまう。
                package="nav2_map_server",
                executable="map_server",
                name="map_server_loc",
                output="screen",
                respawn=use_respawn,
                respawn_delay=2.0,
                parameters=[{
                    "use_sim_time": use_sim_time,
                    "yaml_filename": map_loc_yaml,
                }],
                arguments=["--ros-args", "--log-level", log_level],
                remappings=remappings + [("map", "map_loc")],
            ),
            Node(
                condition=IfCondition(use_emcl2),
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
            # map_server 2 つだけ。
            Node(
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_localization",
                output="screen",
                arguments=["--ros-args", "--log-level", log_level],
                parameters=[
                    {"use_sim_time": use_sim_time},
                    {"autostart": autostart},
                    {"node_names": ["map_server", "map_server_loc"]},
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
                            # map->odom を出すのは localization:=vi のときだけ。
                            # **これも config には書かないこと** — emcl2 構成で
                            # 真になっていると出し手が 2 人になり、TF の約束
                            # (区間ごとに所有者は 1 つ) が破れて自己位置だけが
                            # 静かに壊れる。渡すのは launch と 1 対 1 のここ。
                            "publish_tf": ParameterValue(use_vi_loc, value_type=bool),
                        },
                    ],
                    arguments=["--ros-args", "--log-level", log_level],
                    remappings=remappings + [("cmd_vel", vi_cmd_vel_topic)],
                ),
                *smoother_nodes,
            ],
        )

    # スタック
    # amcl + navfn: nav2 標準の bringup がそのまま使える。
    # **amcl の 2 つは地図が 1 枚**。上流の localization_launch.py が自前で
    # map_server を立てるので map_loc:= を渡す口が無く、渡しているのは経路計画側の
    # 地図 (map:=) のほう。site: map: の 2 行が別の地図を指しているときは
    # nav2_params.resolve_map が起動時に落とすので、ここへは同じ地図しか来ない。
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

    # emcl2 / vi + navfn / vi: 自己位置推定を自前で立て、その上に navigation を
    # 載せる。localization:=vi では emcl2 のノードだけが抜け、推定は下の
    # standalone_navigation が立てる vi_planner が持つ (nav2:=true との併用は
    # backends.validate_localization が弾くので、間の 2 つの include には来ない)。
    own_localization_stack = GroupAction(
        condition=IfCondition(use_own_map_server),
        actions=stack_prelude() + own_localization_nodes() + [
            navigation_include(navigation_launch, condition=IfCondition(use_navfn)),
            navigation_include(
                vi_navigation_launch,
                pose_topic="mcl_pose",
                condition=IfCondition(PythonExpression([use_vi, " and ", use_nav2])),
            ),
            standalone_navigation(own_pose_topic),
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

    return [
        amcl_navfn_stack,
        amcl_vi_stack,
        own_localization_stack,
        system_monitor,
        rviz,
    ]
