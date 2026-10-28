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

"""Localization utilities and standalone VI navigation nodes."""

from launch.actions import GroupAction, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, PushRosNamespace, SetParametersFromFile
from launch_ros.descriptions import ParameterFile, ParameterValue
from nav2_common.launch import RewrittenYaml


def assemble(w):
    """自己位置推定と VI、system_monitor / RViz を返す。

    w は generate_launch_description が積んだ LaunchConfiguration と条件。
    ネストしたヘルパが閉包で見ていた名前を、属性として渡している。
    """
    namespace = w.namespace
    use_namespace = w.use_namespace
    map_yaml = w.map_yaml
    map_loc_yaml = w.map_loc_yaml
    params_file = w.params_file
    emcl2_params_file = w.emcl2_params_file
    emcl3d_params_file = w.emcl3d_params_file
    map_pcd = w.map_pcd
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
    use_emcl3d = w.use_emcl3d
    use_vi_loc = w.use_vi_loc
    use_own_map_server = w.use_own_map_server
    use_amcl = w.use_amcl
    use_smoother = w.use_smoother
    vi_cmd_vel_topic = w.vi_cmd_vel_topic
    own_pose_topic = w.own_pose_topic
    localization_launch = w.localization_launch

    remappings = [("/tf", "tf"), ("/tf_static", "tf_static")]
    emcl2_remappings = remappings + [
        ("particlecloud", "particle_cloud"),
        ("global_localization", "reinitialize_global_localization"),
        # Localization uses /map_loc; VI navigation uses /map.
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

    def stack_prelude():
        """Apply the namespace and lifecycle bond settings."""
        return [
            PushRosNamespace(
                condition=IfCondition(use_namespace),
                namespace=namespace,
            ),
            SetParametersFromFile(bond_params_file),
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
            # localization:=emcl3d の推定器。地図は /map_loc ではなく PCD を直に読む
            # (map_path は resolve_map が site: map: pointcloud: から決めた map_pcd)。
            # 出力の mcl_pose は emcl2 と同名同型なので、vi_planner の pose_topic は
            # そのまま。particlecloud を emcl2 と同じ名前へ振って RViz を共用する。
            Node(
                condition=IfCondition(use_emcl3d),
                package="emcl3d",
                executable="emcl3d_node",
                name="emcl3d",
                output="screen",
                respawn=use_respawn,
                respawn_delay=2.0,
                parameters=[
                    emcl3d_params_file,
                    {"use_sim_time": use_sim_time, "map_path": map_pcd},
                ],
                arguments=["--ros-args", "--log-level", log_level],
                remappings=remappings + [("particlecloud", "particle_cloud")],
            ),
            # emcl2 / emcl3d 自身はライフサイクルノードではないので、マネージャが
            # 見るのは map_server 2 つだけ。
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

    def standalone_navigation(pose_topic):
        """Start VI and optionally smooth its velocity commands."""
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
                            "follow": True,
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

    # AMCL keeps its map server and optional component container.
    amcl_stack = GroupAction(
        condition=IfCondition(use_amcl),
        actions=stack_prelude() + [
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
            standalone_navigation("amcl_pose"),
        ],
    )

    # emcl2 / emcl3d / VIOLA use our map servers.
    own_localization_stack = GroupAction(
        condition=IfCondition(use_own_map_server),
        actions=stack_prelude() + own_localization_nodes() + [
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
        amcl_stack,
        own_localization_stack,
        system_monitor,
        rviz,
    ]
