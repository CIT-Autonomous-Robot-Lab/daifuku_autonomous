#!/usr/bin/env python3
"""Isaac Sim standalone: Raspberry Pi Cat を走らせ、本リポジトリの nav2 に繋ぐ。

対象バージョン: **Isaac Sim 5.x** (4.5 でも動くよう拡張名の解決を入れてある)
対象 ROS 2   : Humble

    ${ISAACSIM}/python.sh simulator/src/daifuku_sim/isaac_raspicat.py \
        --world worlds/map.usda --robot assets/raspicat.usd --lidar 2d

## なぜ OmniGraph なのか (rclpy を直接使わない理由)

Isaac Sim 5.x のバンドル Python は 3.11、ROS 2 Humble は 3.10 で、拡張モジュールの
ABI が合わない。Isaac のプロセス内で `import rclpy` は成立しないので、ROS 2 との
やりとりは Isaac 同梱の ROS 2 ブリッジ (OmniGraph ノード群) で行う。ブリッジは
自前の DDS を持つので、nav2 側とは通常どおり DDS 越しに繋がる。
= nav2 は別プロセス (別コンテナでもよい) のままでよい。

## このスクリプトが出すもの / 受けるもの

  受け  : /cmd_vel            (geometry_msgs/Twist)
  出し  : /clock              (rosgraph_msgs/Clock)          ※ --use-sim-time 時のみ
          <odom-topic>        (nav_msgs/Odometry)
          TF odom -> base_footprint
          TF (ロボットのリンク間)
          lidar:=2d     -> /scan_raw          (sensor_msgs/LaserScan)
          lidar:=mid360 -> /livox/lidar       (sensor_msgs/PointCloud2)
                           /livox/imu         (sensor_msgs/Imu)

## TF の所有者 (ここを曖昧にすると必ず溶かす)

TF ツリーは 2 つの区間に分かれ、**それぞれ所有者を 1 つだけ**にする:

  map  -> odom            : emcl2 (または amcl)
  odom -> base_footprint  : --publish-odom-tf true なら Isaac、
                            mid360 + EKF 構成なら ekf_node
  base_footprint -> ...   : **robot_state_publisher** (既定)
    -> base_link            --publish-link-tf true にすると Isaac が出すが、
    -> lidar_link           そのときは nav 側で robot_state_publisher を
    -> livox_frame          起動しないこと

既定を robot_state_publisher にしてあるのは実機と同じ配置だから。二重に出すと
同じ transform が別ソースから流れ、tf2 がどちらを採るかで自己位置だけが静かに
壊れる (トピックは全部出ているように見える)。

`simulator/container/nav_container.sh` は URDF から robot_state_publisher を起動し、
起動前に `base_footprint -> <lidar frame>` が引けることを確認してから nav2 を
上げる。ここが無いと laser_filters と emcl2 が「原因の分からない沈黙」で失敗する。

odom のトピック名を lidar モードから推測しないこと。**明示的に --odom-topic で
渡す**。理由は launch 側の配線:

  * lidar:=2d           : nav2 は /odom を直接使う            -> --odom-topic /odom
  * lidar:=mid360 + EKF : robot_localization の ekf_node が
                          /wheel/odom と /imu/mid360 を融合して
                          /odom を出す (odometry/filtered を
                          /odom に remap)                     -> --odom-topic /wheel/odom

ここを取り違えると odom -> base_footprint の TF が二重に出る (または出ない) が、
どちらも「なんとなく動いているように見えて自己位置だけ壊れる」ので気づきにくい。

## 実時間との関係 (ここがこのハーネスの成立条件)

Pi4 相当への減速は pi4_sim ハーネスと同じく **cgroup の CPU quota** で行う。
quota は**実時間**基準である一方、`--use-sim-time` を付けると nav2 の締め切りは
**シム時間**基準になる。したがって RTF (real-time factor) が 1.0 を割ると、
1 シム秒あたりに nav2 が実行できる仕事量が増え、**Pi4 が実際より速く見える**。

そのため:
  * 既定は `--use-sim-time` を**付けない** (Isaac をウォールクロックで自由走行させる)。
    nav2 の時計と cgroup quota が同じ時計になるので、構造的に嘘がつけない。
    navigation.launch.py の use_sim_time 既定も false で、既存ハーネスもこの経路。
  * `--use-sim-time` を使う場合は再現性と引き換えに RTF の監視が必須になる。
    --rtf-report に毎秒の RTF を書き出すので、run_isaac_case.sh がこれを判定に使う。
"""

import argparse
import json
import math
import os
import sys

# 引数は SimulationApp を起動する **前** に解釈する。SimulationApp を作った時点で
# Kit が sys.argv を触りにいくため、後から argparse すると取りこぼす。

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", required=True,
                    help="map_to_usd.py が作ったワールド USD (.usda/.usd)")
    ap.add_argument("--robot",
                    help="ロボットの USD。--urdf を使う場合は省略可")
    ap.add_argument("--urdf",
                    help="ロボットの URDF。指定するとその場で USD に変換して読み込む "
                         "(raspicat.urdf は gazebo プラグインを含まない素の URDF を使うこと)")
    ap.add_argument("--robot-prim", default="/World/Raspicat",
                    help="ロボットを置くプリムパス (既定: /World/Raspicat)")

    ap.add_argument("--lidar", default="2d", choices=("2d", "mid360"),
                    help="LiDAR 構成 (既定: 2d)")
    ap.add_argument("--lidar-config",
                    help="RTX LiDAR のプロファイル JSON。省略時は configs/ の "
                         "raspicat_2d_lidar.json / livox_mid360.json を使う")
    ap.add_argument("--lidar-profile",
                    help="Isaac Sim 同梱のプロファイル名を直接使う "
                         "(例: Example_Rotary)。--lidar-config より優先する。"
                         "configs/*.json のスキーマが手元の Isaac と合わないときの逃げ道")
    ap.add_argument("--lidar-prim-path", default="/base_link/lidar_rtx",
                    help="ロボット配下に作る RTX LiDAR のパス (--robot-prim からの相対)")
    ap.add_argument("--lidar-xyz", default="0.144,0.0,0.0289",
                    help="base_link から見た LiDAR の位置 [m]。"
                         "既定は raspicat_description の lidar_mount + lidar_sensor 相当")
    ap.add_argument("--lidar-rpy", default="0,0,0", help="LiDAR の姿勢 [rad]")

    ap.add_argument("--odom-topic", default="/odom",
                    help="オドメトリのトピック。lidar:=2d なら /odom、"
                         "lidar:=mid360 + EKF なら /wheel/odom (既定: /odom)")
    ap.add_argument("--publish-odom-tf", default="true", choices=("true", "false"),
                    help="odom -> base_footprint の TF を Isaac 側で出すか。"
                         "EKF を使う構成では ekf_node が出すので false にすること")
    ap.add_argument("--publish-link-tf", default="false", choices=("true", "false"),
                    help="ロボットのリンク間 TF (base_footprint -> base_link -> "
                         "lidar_link ...) を Isaac 側で出すか。"
                         "**既定は false**: 実機と同じく robot_state_publisher に"
                         "任せる。両方から出すと同じ transform が二重に流れ、"
                         "tf2 がどちらを採るかで自己位置だけが静かに壊れる。"
                         "詳細は下の「TF の所有者」を参照")
    ap.add_argument("--base-frame", default="base_footprint")
    ap.add_argument("--odom-frame", default="odom")
    ap.add_argument("--lidar-frame", default="",
                    help="LiDAR の frame_id。既定は lidar:=2d なら lidar_link、"
                         "mid360 なら livox_frame")

    # ▲ この 2 値は**実機ではなく、読み込んだ URDF の車輪**に合わせること。
    #   DifferentialController は (v, w) を車輪の角速度に換算するだけで、実際に
    #   地面を蹴るのは URDF が定義した物理の車輪である。両者がずれると sim の
    #   移動量が指令とずれる。既定は raspicat_description の素の URDF の値。
    #
    #   実機 (src/daifuku_config/bringup/robot/raspicat.yaml) は 2026-08-03 の実測で
    #   wheel_diameter 0.2 / wheel_tread 0.35、つまり半径 0.1 / トレッド 0.35 で
    #   あって、この既定とは違う。**sim は寸法的に実機ではない**。合わせたい
    #   なら URDF 側の車輪も直したうえで、この 2 値を 0.1 / 0.35 にすること。
    ap.add_argument("--wheel-radius", type=float, default=0.0762,
                    help="車輪半径 [m]。**URDF の車輪に合わせる値**で、実機の値では"
                         "ない (既定は raspicat_description の 0.1524 の半分)")
    ap.add_argument("--wheel-base", type=float, default=0.27918,
                    help="トレッド [m]。同じく URDF 基準 "
                         "(既定は raspicat_description の wheel_tread)")
    ap.add_argument("--left-wheel-joint", default="left_wheel_joint")
    ap.add_argument("--right-wheel-joint", default="right_wheel_joint")
    ap.add_argument("--max-linear", type=float, default=0.5, help="[m/s]")
    ap.add_argument("--max-angular", type=float, default=1.5, help="[rad/s]")

    ap.add_argument("-x", type=float, default=0.0, help="スポーン位置 x [m]")
    ap.add_argument("-y", type=float, default=0.0, help="スポーン位置 y [m]")
    ap.add_argument("-z", type=float, default=0.05, help="スポーン位置 z [m]")
    ap.add_argument("--yaw", type=float, default=0.0, help="スポーン姿勢 yaw [rad]")

    ap.add_argument("--physics-dt", type=float, default=1.0 / 200.0,
                    help="物理ステップ [s] (既定: 1/200)")
    ap.add_argument("--render-dt", type=float, default=1.0 / 30.0,
                    help="描画/センサ更新ステップ [s] (既定: 1/30)")
    ap.add_argument("--use-sim-time", action="store_true",
                    help="/clock を出し、シム時間で駆動する。"
                         "**RTF の監視が必須になる** (ファイル冒頭の説明を読むこと)")
    ap.add_argument("--headless", action="store_true", help="GUI を出さない")
    ap.add_argument("--renderer", default="RayTracedLighting",
                    choices=("RayTracedLighting", "PathTracing"))
    ap.add_argument("--rtf-report", default="",
                    help="毎秒の RTF を JSON Lines で書き出すパス")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="この秒数だけ回して終了する (0 = 無限)")
    return ap.parse_args()


ARGS = parse_args()

# Kit の起動。これ以降でしか omni.* / isaacsim.* は import できない。

from isaacsim import SimulationApp  # noqa: E402  (SimulationApp より前に import 禁止)

simulation_app = SimulationApp({
    "headless": ARGS.headless,
    "renderer": ARGS.renderer,
})

import omni.graph.core as og  # noqa: E402
import omni.usd  # noqa: E402
from isaacsim.core.api import SimulationContext  # noqa: E402
from isaacsim.core.utils.extensions import enable_extension  # noqa: E402
from pxr import Gf, UsdGeom  # noqa: E402


# 拡張名の解決
#
# Isaac Sim 4.x と 5.x で OmniGraph のノード型名の名前空間が変わった:
#     4.x: omni.isaac.ros2_bridge.ROS2PublishClock
#     5.x: isaacsim.ros2.bridge.ROS2PublishClock
# 5.x を対象にしつつ、レジストリを見て 4.x にも落とせるようにしておく。
# 6.0 では ROS 2 ブリッジと core の名前空間は据え置き。動いたのは RTX センサ
# (isaacsim.sensors.rtx -> isaacsim.sensors.experimental.rtx) で、そちらは
# OmniGraph ではなく Python API なので _make_rtx_lidar() が別に面倒を見る。

_NS_CANDIDATES = {
    "ros2": ("isaacsim.ros2.bridge", "omni.isaac.ros2_bridge"),
    "core": ("isaacsim.core.nodes", "omni.isaac.core_nodes"),
    "wheeled": ("isaacsim.robot.wheeled_robots", "omni.isaac.wheeled_robots"),
    "rtx_sensor": ("isaacsim.sensors.experimental.rtx",
                   "isaacsim.sensors.rtx", "omni.isaac.sensor"),
}
_NS_RESOLVED = {}


def _registered_types():
    try:
        return set(og.GraphRegistry().get_node_types())
    except Exception:
        # 古い API 名。取れない場合は空集合を返して既定 (5.x) にフォールバックする。
        return set()


_REGISTRY = _registered_types()


def node_type(group, name):
    """`group` の名前空間を解決して `<ns>.<name>` を返す。"""
    if group not in _NS_RESOLVED:
        chosen = _NS_CANDIDATES[group][0]
        for ns in _NS_CANDIDATES[group]:
            if not _REGISTRY or any(t.startswith(ns + ".") for t in _REGISTRY):
                chosen = ns
                break
        _NS_RESOLVED[group] = chosen
        print(f"[isaac_raspicat] namespace {group} -> {chosen}")
    return f"{_NS_RESOLVED[group]}.{name}"


def enable_ros2_bridge():
    for ns in _NS_CANDIDATES["ros2"]:
        try:
            enable_extension(ns)
            print(f"[isaac_raspicat] enabled extension {ns}")
            return ns
        except Exception as exc:  # pragma: no cover - 環境依存
            print(f"[isaac_raspicat] extension {ns} unavailable: {exc}")
    raise SystemExit(
        "ROS 2 bridge 拡張を有効化できなかった。Isaac Sim の ROS 2 ブリッジが"
        "インストールされているか、ROS_DISTRO / RMW の設定を確認すること。"
    )


# ステージの組み立て

def load_world(path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise SystemExit(f"world USD not found: {path}")
    omni.usd.get_context().open_stage(path)
    stage = omni.usd.get_context().get_stage()
    print(f"[isaac_raspicat] world: {path}")
    return stage


def import_urdf(urdf_path, dest_prim):
    """URDF をその場で USD に取り込む。

    raspicat_description の URDF は `gazebo_plugin:=false` で吐いたもの
    (= <gazebo> タグを含まない) を使うこと。Gazebo のプラグインタグは Isaac では
    無視されるだけだが、差動駆動やセンサはこのスクリプトが OmniGraph で作り直す
    ので、URDF 側に残っていると「どちらが効いているのか」が分からなくなる。
    """
    # モジュールパスは 5.x と 4.x で違う。OmniGraph のノード型名と同様に両対応する。
    _urdf = None
    for mod in ("isaacsim.asset.importer.urdf",      # 5.x
                "omni.importer.urdf",                # 4.x
                "omni.isaac.urdf"):                  # さらに古い
        try:
            _urdf = __import__(mod, fromlist=["_urdf"])._urdf
            print(f"[isaac_raspicat] urdf importer: {mod}")
            break
        except Exception:
            continue
    if _urdf is None:
        raise SystemExit(
            "URDF importer 拡張が見つからない。--robot に変換済みの USD を渡すか、"
            "Isaac Sim の URDF Importer 拡張を有効化すること。"
        )

    urdf_path = os.path.abspath(urdf_path)
    if not os.path.isfile(urdf_path):
        raise SystemExit(f"URDF not found: {urdf_path}")

    cfg = _urdf.ImportConfig()
    # 固定ジョイントを畳まない。畳むと base_link / lidar_mount_link といった
    # リンクのプリムが消え、--lidar-prim-path の取り付け先 (既定 /base_link/...) が
    # 無くなる。物理的には畳んだほうが速いが、「LiDAR がどこに付いているか
    # 分からない」状態のほうが害が大きい。高さが 2cm ずれても 2m の壁を撃つ限り
    # もっともらしいスキャンが返るので、間違いに気づけない。
    cfg.merge_fixed_joints = False
    cfg.fix_base = False                # 移動ロボットなので base は固定しない
    cfg.make_default_prim = False
    cfg.self_collision = False
    cfg.create_physics_scene = False    # ワールド側の PhysicsScene を使う
    cfg.distance_scale = 1.0            # URDF も USD も meters

    interface = _urdf.acquire_urdf_interface()
    parsed = interface.parse_urdf(os.path.dirname(urdf_path),
                                  os.path.basename(urdf_path), cfg)
    prim = interface.import_robot(os.path.dirname(urdf_path),
                                  os.path.basename(urdf_path), parsed, cfg, dest_prim)
    print(f"[isaac_raspicat] imported URDF {urdf_path} -> {prim}")
    return prim


def add_robot(stage, args):
    if args.urdf:
        prim_path = import_urdf(args.urdf, args.robot_prim)
    elif args.robot:
        robot_usd = os.path.abspath(args.robot)
        if not os.path.isfile(robot_usd):
            raise SystemExit(f"robot USD not found: {robot_usd}")
        prim_path = args.robot_prim
        prim = stage.DefinePrim(prim_path, "Xform")
        prim.GetReferences().AddReference(robot_usd)
        print(f"[isaac_raspicat] robot: {robot_usd} -> {prim_path}")
    else:
        raise SystemExit("--robot か --urdf のどちらかを指定すること")

    # スポーン位置。地図座標系 (map) と一致させる。emcl2 の初期姿勢もここに合わせる。
    xform = UsdGeom.Xformable(stage.GetPrimAtPath(prim_path))
    xform.ClearXformOpOrder()
    xform.AddTranslateOp().Set(Gf.Vec3d(args.x, args.y, args.z))
    xform.AddRotateXYZOp().Set(Gf.Vec3d(0.0, 0.0, math.degrees(args.yaw)))
    return prim_path


def add_rtx_lidar(stage, args, robot_prim):
    """RTX LiDAR をロボット配下に作る。

    プロファイル JSON がセンサの走査パターン (FOV / 分解能 / 回転数 / 到達距離) を
    決める。2D は raspicat_description の Gazebo 設定 (min_range 0.1 / max_range 30) に
    寄せ、mid360 は非回転走査の**近似**にしてある (configs/livox_mid360.json の
    コメント参照)。
    """
    if args.lidar_profile:
        # 同梱プロファイルをそのまま使う。configs/*.json のスキーマが手元の
        # Isaac のバージョンと合わなかった場合の確実な逃げ道。
        profile = args.lidar_profile
        print(f"[isaac_raspicat] using bundled lidar profile: {profile}")
    else:
        cfg_path = args.lidar_config or os.path.join(
            HERE, "configs",
            "raspicat_2d_lidar.json" if args.lidar == "2d" else "livox_mid360.json",
        )
        if not os.path.isfile(cfg_path):
            raise SystemExit(
                f"lidar config not found: {cfg_path}\n"
                "--lidar-profile Example_Rotary のように同梱プロファイル名を"
                "指定して回避できる。"
            )

        # Isaac は「プロファイル名」で JSON を探す。configs/ を検索パスに足す。
        import carb
        settings = carb.settings.get_settings()
        key = "/app/sensors/nv/lidar/profileBaseFolder"
        folders = list(settings.get(key) or [])
        cfg_dir = os.path.dirname(cfg_path)
        if cfg_dir not in folders:
            folders.append(cfg_dir)
            settings.set(key, folders)
        profile = os.path.splitext(os.path.basename(cfg_path))[0]
    xyz = [float(v) for v in args.lidar_xyz.split(",")]
    rpy = [float(v) for v in args.lidar_rpy.split(",")]

    lidar_path = robot_prim + args.lidar_prim_path
    lidar = _make_rtx_lidar(lidar_path, profile, xyz, _rpy_to_quat(rpy))
    print(f"[isaac_raspicat] RTX lidar: {lidar_path} profile={profile}")
    return lidar, lidar_path


# RTX LiDAR の API は 6.0 で作り直された:
#     5.x : isaacsim.sensors.rtx.LidarRtx(prim_path=..., config_file_name=...)
#     6.0 : isaacsim.sensors.experimental.rtx.Lidar.create(..., config=...)
# 6.0 で旧モジュールが shim として残るのかは NVIDIA のドキュメントに明記が無い。
# そこで「どちらが在るか」を実行時に見て、引数名は **シグネチャを調べて詰める**。
# 引数名を決め打ちすると、どちらのバージョンでも TypeError で落ちるだけになり、
# 「LiDAR が作れない」以上のことが分からない。
_LIDAR_ALIASES = {
    "prim_path":   ("prim_path", "path"),
    "name":        ("name",),
    "profile":     ("config_file_name", "config"),      # 5.x -> 6.0 で改名
    "translation": ("translation", "position"),
    "orientation": ("orientation",),
}


def _make_rtx_lidar(lidar_path, profile, xyz, quat):
    import inspect

    factory = None
    for mod_name, attr, maker in (
        ("isaacsim.sensors.experimental.rtx", "Lidar", "create"),   # 6.0+
        ("isaacsim.sensors.rtx", "LidarRtx", None),                 # 4.x / 5.x
    ):
        try:
            cls = getattr(__import__(mod_name, fromlist=[attr]), attr)
        except (ImportError, AttributeError):
            continue
        factory = getattr(cls, maker) if maker else cls
        print(f"[isaac_raspicat] rtx lidar api -> {mod_name}.{attr}"
              + (f".{maker}" if maker else ""))
        break
    if factory is None:
        raise SystemExit(
            "RTX LiDAR の API が見つからない。isaacsim.sensors.experimental.rtx "
            "(6.0+) と isaacsim.sensors.rtx (4.x/5.x) のどちらも import できない。"
        )

    values = {
        "prim_path": lidar_path,
        "name": "raspicat_lidar",
        "profile": profile,
        "translation": xyz,
        "orientation": quat,
    }
    try:
        accepted = set(inspect.signature(factory).parameters)
    except (TypeError, ValueError):
        accepted = None

    kwargs = {}
    for key, names in _LIDAR_ALIASES.items():
        for n in names:
            if accepted is None or n in accepted:
                kwargs[n] = values[key]
                break
        else:
            print(f"[isaac_raspicat] warn: この Isaac は lidar の {key} を"
                  f" {names} のどれでも受け取らない。省略する。")
    print(f"[isaac_raspicat] rtx lidar kwargs: {sorted(kwargs)}")
    return factory(**kwargs)


def _rpy_to_quat(rpy):
    r, p, y = rpy
    cr, sr = math.cos(r / 2), math.sin(r / 2)
    cp, sp = math.cos(p / 2), math.sin(p / 2)
    cy, sy = math.cos(y / 2), math.sin(y / 2)
    return [
        cr * cp * cy + sr * sp * sy,  # w
        sr * cp * cy - cr * sp * sy,  # x
        cr * sp * cy + sr * cp * sy,  # y
        cr * cp * sy - sr * sp * cy,  # z
    ]


# ROS 2 ブリッジのグラフ

def build_ros_graph(args, robot_prim, lidar_path):
    """/cmd_vel 購読 -> 差動駆動 -> odom/TF/scan 出版までを 1 つのグラフに組む。"""
    lidar_frame = args.lidar_frame or (
        "lidar_link" if args.lidar == "2d" else "livox_frame"
    )
    publish_odom_tf = args.publish_odom_tf == "true"
    publish_link_tf = args.publish_link_tf == "true"

    nodes = [
        ("OnTick", node_type("core", "OnPlaybackTick")),
        ("Context", node_type("ros2", "ROS2Context")),
        ("SimTime", node_type("core", "IsaacReadSimulationTime")),

        # /cmd_vel -> 車輪速度
        ("SubTwist", node_type("ros2", "ROS2SubscribeTwist")),
        ("DiffDrive", node_type("wheeled", "DifferentialController")),
        ("Articulation", node_type("core", "IsaacArticulationController")),

        # オドメトリ
        ("ComputeOdom", node_type("core", "IsaacComputeOdometry")),
        ("PubOdom", node_type("ros2", "ROS2PublishOdometry")),
    ]
    # Isaac Sim 6.0 で ROS2PublishTransformTree は「プリムを自分で解決する」のを
    # やめ、IsaacComputeTransformTree が計算した配列を受け取る形になった。
    # 旧来の直接プリム入力は 6.0 でも deprecated として残るとされているが、
    # 「残るかどうか」ではなく**レジストリに新ノードが在るか**で分岐する。
    # バージョン番号で分岐すると、shim が外れた版で静かに TF が止まる。
    split_tf = publish_link_tf and any(
        t.endswith(".IsaacComputeTransformTree") for t in _REGISTRY
    )
    if publish_link_tf:
        if split_tf:
            nodes.append(("ComputeTF", node_type("core", "IsaacComputeTransformTree")))
            print("[isaac_raspicat] link TF: IsaacComputeTransformTree 経由 (6.0+ 形式)")
        else:
            print("[isaac_raspicat] link TF: ROS2PublishTransformTree に直接プリム "
                  "(5.x 以前の形式)")
        nodes.append(("PubTF", node_type("ros2", "ROS2PublishTransformTree")))
    if publish_odom_tf:
        nodes.append(("PubOdomTF", node_type("ros2", "ROS2PublishRawTransformTree")))
    if args.use_sim_time:
        nodes.append(("PubClock", node_type("ros2", "ROS2PublishClock")))

    connections = [
        ("OnTick.outputs:tick", "SubTwist.inputs:execIn"),
        ("OnTick.outputs:tick", "DiffDrive.inputs:execIn"),
        ("OnTick.outputs:tick", "Articulation.inputs:execIn"),
        ("OnTick.outputs:tick", "ComputeOdom.inputs:execIn"),
        ("OnTick.outputs:tick", "PubOdom.inputs:execIn"),

        ("Context.outputs:context", "SubTwist.inputs:context"),
        ("Context.outputs:context", "PubOdom.inputs:context"),

        ("SimTime.outputs:simulationTime", "PubOdom.inputs:timeStamp"),

        ("SubTwist.outputs:linearVelocity", "DiffDrive.inputs:linearVelocity"),
        ("SubTwist.outputs:angularVelocity", "DiffDrive.inputs:angularVelocity"),
        ("DiffDrive.outputs:velocityCommand", "Articulation.inputs:velocityCommand"),

        ("ComputeOdom.outputs:linearVelocity", "PubOdom.inputs:linearVelocity"),
        ("ComputeOdom.outputs:angularVelocity", "PubOdom.inputs:angularVelocity"),
        ("ComputeOdom.outputs:position", "PubOdom.inputs:position"),
        ("ComputeOdom.outputs:orientation", "PubOdom.inputs:orientation"),
    ]
    if publish_link_tf:
        connections += [
            ("Context.outputs:context", "PubTF.inputs:context"),
            ("SimTime.outputs:simulationTime", "PubTF.inputs:timeStamp"),
        ]
        if split_tf:
            connections += [
                ("OnTick.outputs:tick", "ComputeTF.inputs:execIn"),
                ("ComputeTF.outputs:execOut", "PubTF.inputs:execIn"),
                ("ComputeTF.outputs:parentFrames", "PubTF.inputs:parentFrames"),
                ("ComputeTF.outputs:childFrames", "PubTF.inputs:childFrames"),
                ("ComputeTF.outputs:translations", "PubTF.inputs:translations"),
                ("ComputeTF.outputs:orientations", "PubTF.inputs:orientations"),
            ]
        else:
            connections.append(("OnTick.outputs:tick", "PubTF.inputs:execIn"))
    if publish_odom_tf:
        connections += [
            ("OnTick.outputs:tick", "PubOdomTF.inputs:execIn"),
            ("Context.outputs:context", "PubOdomTF.inputs:context"),
            ("SimTime.outputs:simulationTime", "PubOdomTF.inputs:timeStamp"),
            ("ComputeOdom.outputs:position", "PubOdomTF.inputs:translation"),
            ("ComputeOdom.outputs:orientation", "PubOdomTF.inputs:rotation"),
        ]
    if args.use_sim_time:
        connections += [
            ("OnTick.outputs:tick", "PubClock.inputs:execIn"),
            ("Context.outputs:context", "PubClock.inputs:context"),
            ("SimTime.outputs:simulationTime", "PubClock.inputs:timeStamp"),
        ]

    values = [
        ("Context.inputs:useDomainIDEnvVar", True),

        ("SubTwist.inputs:topicName", "cmd_vel"),

        ("DiffDrive.inputs:wheelRadius", args.wheel_radius),
        ("DiffDrive.inputs:wheelDistance", args.wheel_base),
        ("DiffDrive.inputs:maxLinearSpeed", args.max_linear),
        ("DiffDrive.inputs:maxAngularSpeed", args.max_angular),

        ("Articulation.inputs:targetPrim", [robot_prim]),
        ("Articulation.inputs:jointNames",
         [args.left_wheel_joint, args.right_wheel_joint]),

        ("ComputeOdom.inputs:chassisPrim", [robot_prim]),

        ("PubOdom.inputs:topicName", args.odom_topic.lstrip("/")),
        ("PubOdom.inputs:odomFrameId", args.odom_frame),
        ("PubOdom.inputs:chassisFrameId", args.base_frame),
    ]
    if publish_link_tf:
        # --publish-link-tf true を選んだ場合のみ。このとき nav 側で
        # robot_state_publisher を**起動しないこと** (二重配信になる)。
        tf_src = "ComputeTF" if split_tf else "PubTF"
        values += [
            (f"{tf_src}.inputs:parentPrim", [robot_prim]),
            (f"{tf_src}.inputs:targetPrims", [robot_prim]),
        ]
    if publish_odom_tf:
        values += [
            ("PubOdomTF.inputs:parentFrameId", args.odom_frame),
            ("PubOdomTF.inputs:childFrameId", args.base_frame),
        ]
    if args.use_sim_time:
        values.append(("PubClock.inputs:topicName", "clock"))

    try:
        (graph, _, _, _) = og.Controller.edit(
            {"graph_path": "/World/ROS2Bridge", "evaluator_name": "execution"},
            {
                og.Controller.Keys.CREATE_NODES: nodes,
                og.Controller.Keys.CONNECT: connections,
                og.Controller.Keys.SET_VALUES: values,
            },
        )
    except Exception:
        if split_tf:
            # 6.0 の移行ガイドは IsaacComputeTransformTree の**出力**名しか
            # 書いておらず、ROS2PublishTransformTree 側の入力名がこれと同じか
            # までは確認できていない。属性名が違うとここで落ちるので、
            # 「4 つの名前を推測した」ことを言わずにスタックトレースだけ出すと
            # 原因がグラフ全体に見えてしまう。
            print("[isaac_raspicat] グラフ構築に失敗した。link TF の 6.0 経路で"
                  " 使っている以下の**入力属性名は推測**である:", file=sys.stderr)
            for name in ("parentFrames", "childFrames", "translations", "orientations"):
                print(f"    ROS2PublishTransformTree.inputs:{name}", file=sys.stderr)
            print("  6.0 移行ガイドに載っているのは IsaacComputeTransformTree の"
                  " 出力名だけで、", file=sys.stderr)
            print("  publisher 側の入力名が同じかは未確認。属性名の不一致が疑わしい。",
                  file=sys.stderr)
            print("  回避: --publish-link-tf false (既定) にして"
                  " robot_state_publisher にリンク間 TF を任せる。", file=sys.stderr)
        raise
    print("[isaac_raspicat] ROS 2 graph built at /World/ROS2Bridge")

    build_lidar_graph(args, lidar_path, lidar_frame)
    return graph


def build_lidar_graph(args, lidar_path, lidar_frame):
    """RTX LiDAR の出版だけ別グラフにする。

    lidar:=2d     -> /scan_raw  (LaserScan)。lidar_bringup.launch.py の
                     scan_to_scan_filter_chain がこれを受けて /scan にする。
    lidar:=mid360 -> /livox/lidar (PointCloud2)。実機の livox_ros_driver2 は
                     xfer_format:=0 = PointCloud2 なので、ドライバの出力と同型。
                     pointcloud_to_laserscan がそのまま食える。
    """
    helper = node_type("ros2", "ROS2RtxLidarHelper")
    is_2d = args.lidar == "2d"

    nodes = [
        ("OnTick", node_type("core", "OnPlaybackTick")),
        ("Context", node_type("ros2", "ROS2Context")),
        ("RenderProduct", node_type("rtx_sensor", "IsaacCreateRenderProduct")),
        ("PubLidar", helper),
    ]
    connections = [
        ("OnTick.outputs:tick", "RenderProduct.inputs:execIn"),
        ("RenderProduct.outputs:execOut", "PubLidar.inputs:execIn"),
        ("RenderProduct.outputs:renderProductPath", "PubLidar.inputs:renderProductPath"),
        ("Context.outputs:context", "PubLidar.inputs:context"),
    ]
    values = [
        ("Context.inputs:useDomainIDEnvVar", True),
        ("RenderProduct.inputs:cameraPrim", [lidar_path]),
        ("PubLidar.inputs:frameId", lidar_frame),
        ("PubLidar.inputs:topicName", "scan_raw" if is_2d else "livox/lidar"),
        ("PubLidar.inputs:type", "laser_scan" if is_2d else "point_cloud"),
    ]

    og.Controller.edit(
        {"graph_path": "/World/ROS2Lidar", "evaluator_name": "execution"},
        {
            og.Controller.Keys.CREATE_NODES: nodes,
            og.Controller.Keys.CONNECT: connections,
            og.Controller.Keys.SET_VALUES: values,
        },
    )
    print(f"[isaac_raspicat] lidar graph -> "
          f"{'/scan_raw (LaserScan)' if is_2d else '/livox/lidar (PointCloud2)'}")

    if not is_2d:
        build_imu_graph(args)


def build_imu_graph(args):
    """MID360 の IMU を /livox/imu に出す。

    lidar_bringup.launch.py は /livox/imu -> prepare_mid360_imu.py -> /imu/mid360 ->
    ekf_node と繋いでいるので、実機と同じ経路がそのまま通る。
    """
    imu_prim = args.robot_prim + "/base_link/imu_sensor"
    nodes = [
        ("OnTick", node_type("core", "OnPlaybackTick")),
        ("Context", node_type("ros2", "ROS2Context")),
        ("SimTime", node_type("core", "IsaacReadSimulationTime")),
        ("ReadIMU", node_type("rtx_sensor", "IsaacReadIMU")),
        ("PubIMU", node_type("ros2", "ROS2PublishImu")),
    ]
    connections = [
        ("OnTick.outputs:tick", "ReadIMU.inputs:execIn"),
        ("ReadIMU.outputs:execOut", "PubIMU.inputs:execIn"),
        ("Context.outputs:context", "PubIMU.inputs:context"),
        ("SimTime.outputs:simulationTime", "PubIMU.inputs:timeStamp"),
        ("ReadIMU.outputs:linAcc", "PubIMU.inputs:linearAcceleration"),
        ("ReadIMU.outputs:angVel", "PubIMU.inputs:angularVelocity"),
        ("ReadIMU.outputs:orientation", "PubIMU.inputs:orientation"),
    ]
    values = [
        ("Context.inputs:useDomainIDEnvVar", True),
        ("ReadIMU.inputs:imuPrim", [imu_prim]),
        ("PubIMU.inputs:topicName", "livox/imu"),
        ("PubIMU.inputs:frameId", args.lidar_frame or "livox_frame"),
        ("PubIMU.inputs:publishLinearAcceleration", True),
        ("PubIMU.inputs:publishAngularVelocity", True),
        ("PubIMU.inputs:publishOrientation", True),
    ]
    og.Controller.edit(
        {"graph_path": "/World/ROS2Imu", "evaluator_name": "execution"},
        {
            og.Controller.Keys.CREATE_NODES: nodes,
            og.Controller.Keys.CONNECT: connections,
            og.Controller.Keys.SET_VALUES: values,
        },
    )
    print("[isaac_raspicat] imu graph -> /livox/imu")


# 実行ループ + RTF の計測

class RtfMeter:
    """1 秒ごとに RTF を測って JSON Lines で落とす。

    RTF < 1 は「GPU が追いついていない」だけでなく、**Pi4 減速の測定そのものを
    無効にする**。--use-sim-time を使うと nav2 の締め切りはシム時間基準になるが、
    cgroup の CPU quota は実時間基準のままなので、シム時間が遅れるほど nav2 は
    1 シム秒あたり多く仕事ができ、Pi4 が実際より速く見える。run_isaac_case.sh は
    このファイルを読んで、RTF が閾値を割った実行を**無効**として扱う。
    """

    def __init__(self, path, use_sim_time):
        self.path = path
        self.use_sim_time = use_sim_time
        self.fh = open(path, "w", encoding="utf-8", buffering=1) if path else None
        self.samples = []
        self._last_wall = None
        self._last_sim = None

    def tick(self, wall_now, sim_now):
        if self._last_wall is None:
            self._last_wall, self._last_sim = wall_now, sim_now
            return
        dw = wall_now - self._last_wall
        if dw < 1.0:
            return
        ds = sim_now - self._last_sim
        rtf = ds / dw if dw > 0 else 0.0
        self.samples.append(rtf)
        self._last_wall, self._last_sim = wall_now, sim_now
        if self.fh:
            self.fh.write(json.dumps({
                "wall": round(wall_now, 3),
                "sim": round(sim_now, 3),
                "rtf": round(rtf, 4),
            }) + "\n")

    def summary(self):
        if not self.samples:
            return {"n": 0}
        s = sorted(self.samples)
        return {
            "n": len(s),
            "min": round(s[0], 4),
            "mean": round(sum(s) / len(s), 4),
            "p05": round(s[max(0, int(0.05 * len(s)) - 1)], 4),
            "below_0.95": sum(1 for v in s if v < 0.95),
            "use_sim_time": self.use_sim_time,
        }

    def close(self):
        if self.fh:
            self.fh.write(json.dumps({"summary": self.summary()}) + "\n")
            self.fh.close()


def main():
    args = ARGS
    enable_ros2_bridge()

    stage = load_world(args.world)
    robot_prim = add_robot(stage, args)
    _, lidar_path = add_rtx_lidar(stage, args, robot_prim)

    sim = SimulationContext(
        physics_dt=args.physics_dt,
        rendering_dt=args.render_dt,
        stage_units_in_meters=1.0,
    )

    build_ros_graph(args, robot_prim, lidar_path)

    sim.initialize_physics()
    sim.play()

    import time
    meter = RtfMeter(args.rtf_report, args.use_sim_time)
    t0 = time.monotonic()
    print(f"[isaac_raspicat] running "
          f"(physics_dt={args.physics_dt:.5f}s render_dt={args.render_dt:.5f}s "
          f"use_sim_time={args.use_sim_time})")

    try:
        while simulation_app.is_running():
            sim.step(render=True)
            wall = time.monotonic() - t0
            meter.tick(wall, sim.current_time)
            if args.duration and wall >= args.duration:
                break
    except KeyboardInterrupt:
        print("[isaac_raspicat] interrupted")
    finally:
        meter.close()
        print(f"[isaac_raspicat] RTF summary: {json.dumps(meter.summary())}")
        sim.stop()
        simulation_app.close()


if __name__ == "__main__":
    main()
