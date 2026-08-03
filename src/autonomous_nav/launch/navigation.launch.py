import os

from ament_index_python.packages import get_package_share_directory
from ament_index_python.packages import get_package_prefix
from ament_index_python.packages import PackageNotFoundError
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
    LogInfo,
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

    # nav2 のパラメータは config/nav2/ に断片で置き、起動時に 1 つへ合成する
    # (compose_params)。分割・合成順序・上書きの規則は config/README.md。
    default_params_dir = os.path.join(pkg_share, "config", "nav2")
    overrides_dir = os.path.join(pkg_share, "config", "overrides")
    # Pi4 高負荷時の bond 4 秒タイムアウト対策 (詳細はファイル内コメント参照)。
    # nav2 の navigation_launch.py はマネージャに bond_timeout を渡せないため、
    # SetParametersFromFile でグループスコープ内の全ノードに注入する。
    bond_params = os.path.join(pkg_share, "config", "lifecycle_bond.yaml")
    default_emcl2_params = os.path.join(pkg_share, "config", "localization", "emcl2.yaml")
    default_rviz_config = os.path.join(pkg_share, "rviz", "navigation.rviz")
    default_map = os.path.join(pkg_share, "maps", "map_19f.yaml")
    bringup_launch = os.path.join(nav2_share, "launch", "bringup_launch.py")
    navigation_launch = os.path.join(nav2_share, "launch", "navigation_launch.py")
    localization_launch = os.path.join(nav2_share, "launch", "localization_launch.py")
    lidar_bringup_launch = os.path.join(pkg_share, "launch", "lidar_bringup.launch.py")
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
    params_dir_arg = LaunchConfiguration("params_dir")
    overrides = LaunchConfiguration("overrides")
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
    # planner:=vi なら vi (= vi_planner 1 ノードが両アクションを提供)、
    # それ以外は nav2 (vi_global_planner + controller_server)。
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
    # これらのキーは config/nav2/*.yaml に存在しないので SetParameter (グループ全体への
    # 注入) で足りる。逆に params_file に**ある**キーは SetParameter/
    # SetParametersFromFile では上書きできない (launch_ros は global params を先に、
    # ノード個別の parameters= を後に渡すため、後勝ちでノード側が勝つ)。
    # extra_params_file の上書きが効かないのはこれが理由なので、あちらは
    # compose_params で params_file 自体をマージして解決する。
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

    def compose_params(context, *args, **kwargs):
        """params_file を組み立てる。以降の参照 (RewrittenYaml / 各 include) 用。

        後勝ちで重ねる:
          1. params_dir/*.yaml をファイル名順に合成 (params_file:= を明示した
             場合は合成せずそのファイルを土台にする)
          2. overrides:=<名前> -> <pkg_share>/config/overrides/<名前>.yaml
             (params_dir:= を変えても override の置き場は動かない)
          3. extra_params_file:=<パス>

        SetParameter / SetParametersFromFile では params_file に既にあるキーを
        上書きできない (上のコメント参照) ため、YAML の段階で深くマージした
        一時ファイルを作る。マージは「ノード名 -> ros__parameters -> キー」の 3 段。

        emcl2 も同じ overrides / extra_params_file を受ける。emcl2 は nav2 の
        ノードではないので params_file (nav2 の合成結果) を読まず、
        emcl2_params_file がノードへ直接渡る。両者を別扱いにすると
        `overrides/<地図>.yaml` に emcl2: を書いても**エラーも警告も出さずに
        無視される**ので、ここで emcl2 用の合成結果も作って
        emcl2_params_file を差し替える。土台は emcl2_params_file:= で
        渡されたファイル (既定は config/localization/emcl2.yaml)。
        """
        import glob
        import tempfile

        import yaml

        def load(path):
            with open(path, "rb") as f:
                return yaml.safe_load(f.read().decode("utf-8")) or {}

        def overlay(base, extra):
            for node_name, node_body in extra.items():
                if not isinstance(node_body, dict):
                    base[node_name] = node_body
                    continue
                base_body = base.setdefault(node_name, {})
                for section, values in node_body.items():
                    if section == "ros__parameters" and isinstance(values, dict):
                        base_body.setdefault("ros__parameters", {}).update(values)
                    else:
                        base_body[section] = values

        explicit = params_file.perform(context)
        if explicit:
            if not os.path.isfile(explicit):
                raise RuntimeError(f"params_file does not exist: {explicit}")
            merged = load(explicit)
            origin = explicit
        else:
            params_dir = params_dir_arg.perform(context)
            fragments = sorted(glob.glob(os.path.join(params_dir, "*.yaml")))
            if not fragments:
                raise RuntimeError(f"No parameter fragments found in {params_dir}")
            merged, owner = {}, {}
            for frag in fragments:
                body = load(frag)
                for node_name in body:
                    if node_name in owner:
                        # 断片はノード単位で重複しない前提 (config/README.md)。
                        # 重なると「どちらが勝つか分からない」状態になるので止める。
                        raise RuntimeError(
                            f"Node '{node_name}' is defined in two fragments: "
                            f"{owner[node_name]} and {frag}.\n"
                            "config/nav2/*.yaml must partition the nodes; put "
                            "map-specific overrides in config/overrides/ instead."
                        )
                    owner[node_name] = frag
                merged.update(body)
            origin = f"{len(fragments)} fragments from {params_dir}"

        # 重ねる順に (表示名, パス) を 1 度だけ解決する。nav2 側と emcl2 側で
        # 別々に解決すると、片方だけ順序や欠落チェックがずれる余地ができる。
        layers = []
        for name in [n.strip() for n in overrides.perform(context).split(",") if n.strip()]:
            # ros2 launch は `overrides:=` (値が空) を malformed として弾くので、
            # 「何も重ねない」を渡す手段として none を受ける。既定が map_19f に
            # なっている以上、明示的に外す口が無いと別の地図で 19F の調整が載る。
            if name.lower() == "none":
                continue
            path = os.path.join(overrides_dir, f"{name}.yaml")
            if not os.path.isfile(path):
                available = sorted(
                    os.path.splitext(f)[0]
                    for f in os.listdir(overrides_dir)
                    if f.endswith(".yaml")
                ) if os.path.isdir(overrides_dir) else []
                raise RuntimeError(
                    f"Unknown overrides name: {name}\n"
                    f"Available: {', '.join(available) or '(none)'}\n"
                    "Use extra_params_file:=<path> for a file outside "
                    "config/overrides/."
                )
            layers.append((f"overrides:{name}", path))

        for extra in [p.strip() for p in extra_params_file.perform(context).split(",") if p.strip()]:
            if not os.path.isfile(extra):
                raise RuntimeError(f"extra_params_file does not exist: {extra}")
            layers.append((extra, extra))

        applied = []
        for label, path in layers:
            body = load(path)
            # emcl2 セクションは nav2 側の合成結果に混ぜない。読む者が居ないので
            # 害は無いが、/tmp/nav2_params_*.yaml に emcl2 が現れると
            # 「どちらが効いているのか」を追うときに紛れる。emcl2 の分は下で
            # 別に合成する。
            overlay(merged, {k: v for k, v in body.items() if k != "emcl2"})
            applied.append(label)

        out = tempfile.NamedTemporaryFile(
            mode="w", prefix="nav2_params_", suffix=".yaml", delete=False,
            encoding="utf-8",
        )
        yaml.safe_dump(merged, out, default_flow_style=False, allow_unicode=True)
        out.close()

        # emcl2 側。土台は emcl2_params_file (既定 config/localization/emcl2.yaml)、
        # 上に載せるのは同じ layers の emcl2 セクションだけ。overrides に emcl2 が
        # 一切書かれていなければ土台をそのまま使う (一時ファイルを作らない)。
        emcl2_base = emcl2_params_file.perform(context)
        emcl2_applied = []
        if os.path.isfile(emcl2_base):
            emcl2_merged = load(emcl2_base)
            for label, path in layers:
                body = load(path)
                if "emcl2" in body:
                    overlay(emcl2_merged, {"emcl2": body["emcl2"]})
                    emcl2_applied.append(label)
        else:
            # localization:=amcl では emcl2 を起動しないので、存在しなくても
            # ここでは止めない (emcl2 を選んだ場合は validate_localization が見る)。
            emcl2_merged = None

        actions = [
            LogInfo(msg=f"params: composed {origin} -> {out.name}"
                        + (f" (+ {', '.join(applied)})" if applied else "")),
            SetLaunchConfiguration("params_file", out.name),
        ]
        if emcl2_merged is not None and emcl2_applied:
            emcl2_out = tempfile.NamedTemporaryFile(
                mode="w", prefix="emcl2_params_", suffix=".yaml", delete=False,
                encoding="utf-8",
            )
            yaml.safe_dump(emcl2_merged, emcl2_out, default_flow_style=False,
                           allow_unicode=True)
            emcl2_out.close()
            actions += [
                LogInfo(msg=f"params: composed emcl2 {emcl2_base} -> {emcl2_out.name}"
                            f" (+ {', '.join(emcl2_applied)})"),
                SetLaunchConfiguration("emcl2_params_file", emcl2_out.name),
            ]
        return actions

    def validate_map_file(context, *args, **kwargs):
        map_path = map_yaml.perform(context)
        if not os.path.isfile(map_path):
            raise RuntimeError(
                f"Map YAML file does not exist: {map_path}\n"
                "Pass a real map path, for example: "
                "map:=$PWD/src/autonomous_nav/maps/map_19f.yaml"
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
                "local_planner:=nav2 (vi_global_planner + controller_server) or "
                "local_planner:=vi (vi_planner: one node, both actions)."
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
            # local_planner:=vi では vi_planner 1 ノードが compute_path_to_pose と
            # follow_path の両方を提供する (vi_global_planner は起動しない)。
            # vi_planner は map_scale もアウトオブコア経路 (compact) も持つが、
            # 密ソルバのままだと状態配列 (56 B/state) を全域ぶん**実際に**確保する。
            # overrides が map_scale を上げている = 密には解けない大きさの地図、という
            # ことなので、compact を選んでいない設定はここで止める (auto で選ばれた
            # 場合も同じ)。
            import yaml

            with open(params_file.perform(context)) as f:
                merged = yaml.safe_load(f) or {}
            vp = merged.get("vi_planner", {}).get("ros__parameters", {})
            gp = merged.get("vi_global_planner", {}).get("ros__parameters", {})
            # vi_planner セクションが無い overrides では、広域側の map_scale が
            # 「この地図は密には解けない」という同じ合図になる。
            scale = int(vp.get("map_scale", gp.get("map_scale", 1)))
            solver = str(vp.get("solver", ""))
            if scale > 1 and not solver.endswith("_compact"):
                raise RuntimeError(
                    f"local_planner:=vi with map_scale={scale} needs the out-of-core "
                    f"solver, but vi_planner.solver={solver or '(default, dense)'}.\n"
                    "map_scale > 1 means the map is too large to solve densely, and a "
                    "dense solve really allocates 56 B/state for the whole map.\n"
                    'Set vi_planner.solver: "frontier2d_sparse_compact" (plus '
                    "compact_sink_dir) in the overrides, or use local_planner:=nav2 "
                    "(vi_global_planner + controller_server)."
                )
            try:
                get_package_prefix("vi_planner")
            except PackageNotFoundError as exc:
                raise RuntimeError(
                    "vi_planner package is not available (local planner defaults to "
                    "vi when planner:=vi).\n"
                    "Import value_iteration3 (vcs import src < autonomous_bot.repos) and "
                    "build it (colcon build --packages-select vi_planner), or "
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
        DeclareLaunchArgument(
            "overrides",
            # 既定の地図 (map_19f) に対応する override を既定で載せる。地図を
            # 変えるときは overrides:=map_tsudanuma のように**置き換える** —
            # 追加ではないので、19F 用の調整は自動的に外れる。
            # 地図を渡し替えて overrides を放置すると別の地図の調整が載るので注意。
            default_value="map_19f",
            description="config/overrides/<名前>.yaml を上に重ねる (カンマ区切りで複数可)。"
                        "既定は map_19f (既定の地図に対応)。別の地図では"
                        "overrides:=map_tsudanuma のように置き換える。"
                        "何も重ねないなら overrides:=none "
                        "(ros2 launch は値が空の overrides:= を受け付けない)。",
        ),
        DeclareLaunchArgument(
            "extra_params_file",
            default_value="",
            description="overrides の後にさらに重ねる任意パスのファイル (カンマ区切りで"
                        "複数可)。config/overrides/ に置けない一時的な上書き用。",
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
        # 実機 (Raspberry Pi) は headless で、docker/raspberrypi/ のイメージにも
        # RViz は入っていない。表示は同じ ROS_DOMAIN_ID の PC 側から開く。
        DeclareLaunchArgument("use_rviz", default_value="false"),
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
        DeclareLaunchArgument(
            "lidar",
            default_value="mid360",
            description="LiDAR backend: mid360 (既定。本機の構成) または "
                        "2d (raspicat の URG を起動する)。",
        ),
        DeclareLaunchArgument(
            "lidar_driver",
            default_value="true",
            description="LiDAR の実機ドライバ (mid360: livox_ros_driver2 + "
                        "restamp_scan.py / 2d: urg_node) を起動するか。"
                        "シミュレータ (simulator) から動かすときは false にする。",
        ),
        DeclareLaunchArgument("scan_filter_enabled", default_value="true"),
        DeclareLaunchArgument(
            "scan_filter_params_file",
            default_value=os.path.join(pkg_share, "config", "sensors", "scan_filter.yaml"),
        ),
        DeclareLaunchArgument(
            "mid360_config",
            default_value=os.path.join(pkg_share, "config", "sensors", "MID360_config.json"),
        ),
        DeclareLaunchArgument("use_mid360_imu", default_value="true"),
        # 既定 true。TF が出るのは lidar:=mid360 のときだけで、URDF が配信して
        # いない base_footprint -> livox_frame を補う (詳細は lidar_bringup 側)。
        DeclareLaunchArgument("publish_lidar_tf", default_value="true"),
        DeclareLaunchArgument("lidar_x", default_value="0.0"),
        DeclareLaunchArgument("lidar_y", default_value="0.0"),
        DeclareLaunchArgument("lidar_z", default_value="0.275"),  # 実測 275 mm
        DeclareLaunchArgument("lidar_roll", default_value="0.0"),
        DeclareLaunchArgument("lidar_pitch", default_value="0.0"),
        DeclareLaunchArgument("lidar_yaw", default_value="0.0"),
        DeclareLaunchArgument("wheel_odom_topic", default_value="/wheel/odom"),
        DeclareLaunchArgument("urg_interface", default_value="serial"),
        DeclareLaunchArgument("urg_params_file", default_value=""),

        OpaqueFunction(function=compose_params),
        OpaqueFunction(function=validate_map_file),
        OpaqueFunction(function=validate_localization),
        OpaqueFunction(function=validate_planner),
        OpaqueFunction(function=validate_local_planner),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(lidar_bringup_launch),
            launch_arguments={
                "lidar": lidar,
                "lidar_driver": LaunchConfiguration("lidar_driver"),
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
                "urg_interface": LaunchConfiguration("urg_interface"),
                "urg_params_file": LaunchConfiguration("urg_params_file"),
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
