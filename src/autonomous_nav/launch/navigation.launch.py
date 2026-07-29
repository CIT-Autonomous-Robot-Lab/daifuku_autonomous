import os

from ament_index_python.packages import get_package_share_directory
from ament_index_python.packages import get_package_prefix
from ament_index_python.packages import PackageNotFoundError
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
    OpaqueFunction,
    SetEnvironmentVariable,
    SetLaunchConfiguration,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    LaunchConfiguration,
    PathJoinSubstitution,
    PythonExpression,
)
from launch_ros.actions import Node
from launch_ros.actions import PushRosNamespace
from launch_ros.actions import SetParameter
from launch_ros.actions import SetParametersFromFile
from launch_ros.descriptions import ParameterFile
from launch_ros.substitutions import FindPackageShare
from nav2_common.launch import RewrittenYaml


def generate_launch_description():
    pkg_share = get_package_share_directory("autonomous_nav")
    nav2_share = get_package_share_directory("nav2_bringup")

    default_params = os.path.join(pkg_share, "config", "nav2_params.yaml")
    # Pi4 高負荷時の bond 4 秒タイムアウト対策 (詳細はファイル内コメント参照)。
    # nav2 の navigation_launch.py はマネージャに bond_timeout を渡せないため、
    # SetParametersFromFile でグループスコープ内の全ノードに注入する。
    bond_params = os.path.join(pkg_share, "config", "lifecycle_bond_params.yaml")
    default_emcl2_params = os.path.join(pkg_share, "config", "emcl2_params.yaml")
    default_rviz_config = os.path.join(pkg_share, "rviz", "nav2_default.rviz")
    default_map = os.path.join(pkg_share, "maps", "map.yaml")
    bringup_launch = os.path.join(nav2_share, "launch", "bringup_launch.py")
    navigation_launch = os.path.join(nav2_share, "launch", "navigation_launch.py")
    localization_launch = os.path.join(nav2_share, "launch", "localization_launch.py")
    lidar_bringup_launch = os.path.join(pkg_share, "launch", "lidar_bringup.launch.py")
    # planner:=vi 用: planner_server の代わりに vi_global_planner を起動する
    # navigation_launch.py の vi 版 (vi_global_planner パッケージが提供)。
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
    rviz_config = LaunchConfiguration("rviz_config")
    use_sim_time = LaunchConfiguration("use_sim_time")
    autostart = LaunchConfiguration("autostart")
    use_composition = LaunchConfiguration("use_composition")
    use_respawn = LaunchConfiguration("use_respawn")
    log_level = LaunchConfiguration("log_level")
    use_rviz = LaunchConfiguration("use_rviz")
    localization = LaunchConfiguration("localization")
    emcl2_package = LaunchConfiguration("emcl2_package")
    emcl2_executable = LaunchConfiguration("emcl2_executable")
    emcl2_node_name = LaunchConfiguration("emcl2_node_name")
    planner = LaunchConfiguration("planner")
    local_planner = LaunchConfiguration("local_planner")
    lidar = LaunchConfiguration("lidar")
    scan_filter_enabled = LaunchConfiguration("scan_filter_enabled")
    scan_filter_params_file = LaunchConfiguration("scan_filter_params_file")
    mid360_config = LaunchConfiguration("mid360_config")
    use_mid360_imu = LaunchConfiguration("use_mid360_imu")

    # local_planner:=auto (デフォルト) はグローバルプランナに連動する:
    # planner:=vi なら vi_local_planner、それ以外は nav2 (controller_server)。
    effective_local_planner = PythonExpression([
        "'", local_planner, "' if '", local_planner, "' != 'auto' else "
        "('vi' if '", planner, "' == 'vi' else 'nav2')"
    ])

    extra_params_file = LaunchConfiguration("extra_params_file")

    use_amcl = PythonExpression(["'", localization, "' == 'amcl'"])
    use_emcl2 = PythonExpression(["'", localization, "' in ['emcl', 'emcl2']"])
    use_navfn = PythonExpression(["'", planner, "' == 'navfn'"])
    use_vi = PythonExpression(["'", planner, "' == 'vi'"])
    use_amcl_navfn = PythonExpression(
        ["'", localization, "' == 'amcl' and '", planner, "' == 'navfn'"]
    )
    use_amcl_vi = PythonExpression(
        ["'", localization, "' == 'amcl' and '", planner, "' == 'vi'"]
    )

    # planner:=vi では planner_server (と local_planner:=vi なら controller_server) を
    # 起動しないため、nav2 既定の BT が要求する compute_path_through_poses アクションと
    # */clear_entirely_*_costmap サービスが存在せず、bt_navigator が on_configure で
    # 例外を投げて bringup 全体が止まる。nav2 1.1.20 には Iron 以降の `navigators`
    # パラメータが無く through_poses を無効化できないので、木そのものを VI 用
    # (behavior_trees/) に差し替える。
    #
    # これらのキーは nav2_params.yaml に存在しないので SetParameter (グループ全体への
    # 注入) で足りる。逆に params_file に**ある**キーは SetParameter/
    # SetParametersFromFile では上書きできない (launch_ros は global params を先に、
    # ノード個別の parameters= を後に渡すため、後勝ちでノード側が勝つ)。
    # extra_params_file の上書きが効かないのはこれが理由なので、あちらは
    # merge_extra_params で params_file 自体をマージして解決する。
    vi_bt_dir = PathJoinSubstitution([FindPackageShare("autonomous_nav"), "behavior_trees"])
    vi_bt_params = GroupAction(
        condition=IfCondition(use_vi),
        scoped=False,
        actions=[
            SetParameter(
                "default_nav_to_pose_bt_xml",
                PathJoinSubstitution([vi_bt_dir, "navigate_to_pose_vi.xml"]),
            ),
            SetParameter(
                "default_nav_through_poses_bt_xml",
                PathJoinSubstitution([vi_bt_dir, "nav_through_poses_stub.xml"]),
            ),
        ],
    )
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

    def merge_extra_params(context, *args, **kwargs):
        """extra_params_file を params_file にマージし、params_file を差し替える。

        SetParametersFromFile では params_file に既にあるキーを上書きできない
        (上のコメント参照) ため、YAML の段階で深くマージした一時ファイルを作り、
        以降の params_file 参照 (RewrittenYaml / 各 include) をそちらへ向ける。
        マージは「ノード名 -> ros__parameters -> キー」の 3 段で、後者 (extra) が勝つ。
        """
        extra = extra_params_file.perform(context)
        if not extra:
            return []
        if not os.path.isfile(extra):
            raise RuntimeError(f"extra_params_file does not exist: {extra}")

        import tempfile

        import yaml

        base_path = params_file.perform(context)
        with open(base_path) as f:
            merged = yaml.safe_load(f) or {}
        with open(extra) as f:
            overlay = yaml.safe_load(f) or {}

        for node_name, node_body in overlay.items():
            if not isinstance(node_body, dict):
                merged[node_name] = node_body
                continue
            base_body = merged.setdefault(node_name, {})
            for section, values in node_body.items():
                if section == "ros__parameters" and isinstance(values, dict):
                    base_body.setdefault("ros__parameters", {}).update(values)
                else:
                    base_body[section] = values

        out = tempfile.NamedTemporaryFile(
            mode="w", prefix="nav2_params_merged_", suffix=".yaml", delete=False
        )
        yaml.safe_dump(merged, out, default_flow_style=False)
        out.close()
        return [SetLaunchConfiguration("params_file", out.name)]

    def validate_map_file(context, *args, **kwargs):
        map_path = map_yaml.perform(context)
        if not os.path.isfile(map_path):
            raise RuntimeError(
                f"Map YAML file does not exist: {map_path}\n"
                "Pass a real map path, for example: "
                "map:=$PWD/src/autonomous_nav/maps/map.yaml"
            )
        return []

    def validate_localization(context, *args, **kwargs):
        selected = localization.perform(context)
        if selected not in ("amcl", "emcl", "emcl2"):
            raise RuntimeError(
                f"Unsupported localization: {selected}\n"
                "Use localization:=amcl or localization:=emcl2."
            )

        if selected in ("emcl", "emcl2"):
            package_name = emcl2_package.perform(context)
            try:
                get_package_prefix(package_name)
            except PackageNotFoundError as exc:
                raise RuntimeError(
                    f"EMCL2 package is not available: {package_name}\n"
                    "Clone/build the emcl2_ros2 repository in this workspace or source an "
                    "underlay that provides it before launching with localization:=emcl2."
                ) from exc
        return []

    def validate_planner(context, *args, **kwargs):
        selected = planner.perform(context)
        if selected not in ("vi", "navfn"):
            raise RuntimeError(
                f"Unsupported planner: {selected}\n"
                "Use planner:=vi (value iteration, vi_global_planner) or planner:=navfn."
            )
        if selected == "vi":
            try:
                get_package_prefix("vi_global_planner")
            except PackageNotFoundError as exc:
                raise RuntimeError(
                    "vi_global_planner package is not available.\n"
                    "Import value_iteration3 (vcs import src < autonomous_bot.repos) and "
                    "build it (colcon build --packages-select vi_global_planner) before launching "
                    "with planner:=vi, or fall back to planner:=navfn."
                ) from exc
        return []

    def validate_local_planner(context, *args, **kwargs):
        selected = local_planner.perform(context)
        if selected not in ("auto", "nav2", "vi"):
            raise RuntimeError(
                f"Unsupported local_planner: {selected}\n"
                "Use local_planner:=auto (follow the global planner), "
                "local_planner:=nav2 (controller_server) or local_planner:=vi "
                "(vi_local_planner)."
            )
        if selected == "vi" and planner.perform(context) != "vi":
            # local_planner:=vi は vi 版 navigation_launch.py 経由でのみ効く
            # (planner:=navfn の標準 navigation_launch.py は local_planner を
            # 知らない)。
            raise RuntimeError(
                "local_planner:=vi requires planner:=vi (it is wired through "
                "vi_global_planner's navigation_launch.py)."
            )
        if effective_local_planner.perform(context) == "vi":
            # vi_local_planner はアウトオブコア経路 (compact) も map_scale も持たず、
            # 地図全体を密に解き直す。vi_global_planner 側が map_scale を上げている
            # = 密には解けない大きさの地図、ということなので、そのまま起動させると
            # 状態配列の確保だけで数十 GB を要求して落ちる。auto で選ばれた場合も
            # 同じなので、ここで明示的に止める。
            import yaml

            with open(params_file.perform(context)) as f:
                merged = yaml.safe_load(f) or {}
            scale = (
                merged.get("vi_global_planner", {})
                .get("ros__parameters", {})
                .get("map_scale", 1)
            )
            if int(scale) > 1:
                raise RuntimeError(
                    f"local_planner:=vi cannot be used with vi_global_planner.map_scale="
                    f"{scale}.\n"
                    "vi_local_planner has no out-of-core path and no map_scale, so it "
                    "would allocate the full-resolution state array for this map.\n"
                    "Use local_planner:=nav2."
                )
            try:
                get_package_prefix("vi_local_planner")
            except PackageNotFoundError as exc:
                raise RuntimeError(
                    "vi_local_planner package is not available (local planner "
                    "defaults to vi when planner:=vi).\n"
                    "Import value_iteration3 (vcs import src < autonomous_bot.repos) and "
                    "build it (colcon build --packages-select vi_local_planner), or "
                    "fall back to local_planner:=nav2."
                ) from exc
        return []

    return LaunchDescription([
        SetEnvironmentVariable("RCUTILS_LOGGING_BUFFERED_STREAM", "1"),

        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("use_namespace", default_value="false"),
        DeclareLaunchArgument(
            "map",
            default_value=default_map,
            description="Full path to the map yaml file.",
        ),
        DeclareLaunchArgument("params_file", default_value=default_params),
        DeclareLaunchArgument(
            "extra_params_file",
            default_value="",
            description="params_file の上に重ねる追加パラメータファイル (空で無効)。"
                        "地図固有の設定を params_file 全体を複製せずに与えるためのもの。"
                        "例: extra_params_file:=<share>/config/tsudanuma_overrides.yaml",
        ),
        DeclareLaunchArgument("emcl2_params_file", default_value=default_emcl2_params),
        DeclareLaunchArgument("rviz_config", default_value=default_rviz_config),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("autostart", default_value="true"),
        # Pi4では全nav2ノードを1プロセスに合成すると、参加者のエンドポイント数が
        # 巨大化して新規参加者からディスカバリ不能になり、CPU飢餓でbond心拍も
        # 途絶して lifecycle manager が CRITICAL FAILURE→自動シャットダウンする
        # 事象が頻発した (2026-07-24)。プロセス分離で参加者を小さく保つ。
        DeclareLaunchArgument("use_composition", default_value="False"),
        DeclareLaunchArgument("use_respawn", default_value="False"),
        DeclareLaunchArgument("log_level", default_value="info"),
        DeclareLaunchArgument("use_rviz", default_value="true"),
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
                        "planner:=vi -> vi, otherwise nav2), nav2 "
                        "(controller_server/DWB) or vi (vi_local_planner; requires "
                        "planner:=vi).",
        ),
        DeclareLaunchArgument("lidar", default_value="2d"),
        DeclareLaunchArgument("scan_filter_enabled", default_value="true"),
        DeclareLaunchArgument(
            "scan_filter_params_file",
            default_value=os.path.join(pkg_share, "config", "scan_filter.yaml"),
        ),
        DeclareLaunchArgument(
            "mid360_config",
            default_value=os.path.join(pkg_share, "config", "MID360_config.json"),
        ),
        DeclareLaunchArgument("use_mid360_imu", default_value="true"),
        DeclareLaunchArgument("publish_lidar_tf", default_value="false"),
        DeclareLaunchArgument("lidar_x", default_value="0.0"),
        DeclareLaunchArgument("lidar_y", default_value="0.0"),
        DeclareLaunchArgument("lidar_z", default_value="0.0"),
        DeclareLaunchArgument("lidar_roll", default_value="0.0"),
        DeclareLaunchArgument("lidar_pitch", default_value="0.0"),
        DeclareLaunchArgument("lidar_yaw", default_value="0.0"),
        DeclareLaunchArgument("wheel_odom_topic", default_value="/wheel/odom"),

        OpaqueFunction(function=merge_extra_params),
        OpaqueFunction(function=validate_map_file),
        OpaqueFunction(function=validate_localization),
        OpaqueFunction(function=validate_planner),
        OpaqueFunction(function=validate_local_planner),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(lidar_bringup_launch),
            launch_arguments={
                "lidar": lidar,
                "use_sim_time": use_sim_time,
                "scan_filter_enabled": scan_filter_enabled,
                "scan_filter_params_file": scan_filter_params_file,
                "mid360_config": mid360_config,
                "use_mid360_imu": use_mid360_imu,
                "publish_lidar_tf": LaunchConfiguration("publish_lidar_tf"),
                "lidar_x": LaunchConfiguration("lidar_x"),
                "lidar_y": LaunchConfiguration("lidar_y"),
                "lidar_z": LaunchConfiguration("lidar_z"),
                "lidar_roll": LaunchConfiguration("lidar_roll"),
                "lidar_pitch": LaunchConfiguration("lidar_pitch"),
                "lidar_yaw": LaunchConfiguration("lidar_yaw"),
                "wheel_odom_topic": LaunchConfiguration("wheel_odom_topic"),
            }.items(),
        ),

        IncludeLaunchDescription(
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
        ),

        # amcl + vi: 標準 bringup は planner_server 込みなので、localization と
        # vi 版 navigation を個別に include する。
        GroupAction(
            condition=IfCondition(use_amcl_vi),
            actions=[
                PushRosNamespace(
                    condition=IfCondition(use_namespace),
                    namespace=namespace,
                ),
                SetParametersFromFile(bond_params),
                vi_bt_params,
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
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(vi_navigation_launch),
                    launch_arguments={
                        "namespace": namespace,
                        "use_sim_time": use_sim_time,
                        "params_file": params_file,
                        "autostart": autostart,
                        "use_composition": use_composition,
                        "use_respawn": use_respawn,
                        "container_name": "nav2_container",
                        "log_level": log_level,
                        "pose_topic": "amcl_pose",
                        "local_planner": effective_local_planner,
                    }.items(),
                ),
            ],
        ),

        GroupAction(
            condition=IfCondition(use_emcl2),
            actions=[
                PushRosNamespace(
                    condition=IfCondition(use_namespace),
                    namespace=namespace,
                ),
                SetParametersFromFile(bond_params),
                vi_bt_params,
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
                    package=emcl2_package,
                    executable=emcl2_executable,
                    name=emcl2_node_name,
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
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(navigation_launch),
                    condition=IfCondition(use_navfn),
                    launch_arguments={
                        "namespace": namespace,
                        "use_sim_time": use_sim_time,
                        "params_file": params_file,
                        "autostart": autostart,
                        "use_composition": use_composition,
                        "use_respawn": use_respawn,
                        "container_name": "nav2_container",
                        "log_level": log_level,
                    }.items(),
                ),
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(vi_navigation_launch),
                    condition=IfCondition(use_vi),
                    launch_arguments={
                        "namespace": namespace,
                        "use_sim_time": use_sim_time,
                        "params_file": params_file,
                        "autostart": autostart,
                        "use_composition": use_composition,
                        "use_respawn": use_respawn,
                        "container_name": "nav2_container",
                        "log_level": log_level,
                        "pose_topic": "mcl_pose",
                        "local_planner": effective_local_planner,
                    }.items(),
                ),
            ],
        ),

        Node(
            condition=IfCondition(use_rviz),
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            namespace=namespace,
            output="screen",
            arguments=["-d", rviz_config],
            parameters=[{"use_sim_time": use_sim_time}],
        ),
    ])
