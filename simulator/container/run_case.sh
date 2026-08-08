#!/usr/bin/env bash
# コンテナ内で 1 ケース分を通しで実行する:
#   navigation.launch.py (lidar:=2d) + fake_robot.py を起動 -> probe.py で
#   NavigateToPose を1回投げる -> ログを要約して停止。
#
# 環境変数で条件を振る (既定値は Pi 実機の現行設定に一致):
#   PLANNER=vi|navfn            planner:=
#   LOCAL_PLANNER=auto|nav2|vi  local_planner:=
#   NAV2=auto|true|false        nav2:= (既定 auto)。**launch の既定 false のまま
#                               渡すと PLANNER=navfn / LOCAL_PLANNER=nav2 が
#                               起動時にエラーで止まる**ので、ここは auto にして
#                               プランナに追従させる。BT 込みで測りたいときだけ
#                               NAV2=true を明示する (実機の既定は false)
#   LOCALIZATION=emcl2|amcl     localization:=
#   MAP_NAME=map_19f|map_tsudanuma|... share/maps/<name>.yaml を使う (既定 map_19f)。
#                               OVERRIDES 未指定なら同名の override を自動で選ぶ
#   VI_MAP_SCALE=               vi_global_planner の map_scale (地図をプランナ内部で
#                               粗くする倍率。津田沼 (5888x4000@0.05m) は 3 で
#                               1963x1334@0.15m = 1.57 億状態)
#   VI_COMPACT_SINK_DIR=        compact 経路の確定出力を置くディレクトリ ("" = RAM)
#   SIM_UNKNOWN_AS_OBSTACLE=1   シム LiDAR が未観測セルも壁として返す
#   OVERRIDES=                  navigation.launch.py の overrides
#                               (config/overrides/<名前>.yaml。例 map_tsudanuma)
#   EXTRA_PARAMS=               navigation.launch.py の extra_params_file
#                               (config/overrides/ に無い任意パスの上書き)
#   MAP_FREE_THRESH=            指定すると map.yaml の free_thresh を差し替えた
#                               コピーを使う (実機は 0.25 = 未観測 205 が free 扱い)
#   VI_SOLVER=                  vi_*_planner の solver パラメータ上書き
#   VI_PUBLISH_VF=true|false    publish_value_function 上書き
#   INITIALPOSE_MAX_TRIES=      /initialpose の再送上限 (fake_robot 既定 8)。
#                               広域地図 + 低 CPU では emcl2 が最初のループを回すまで
#                               100 秒以上かかることがあり、既定の 8 回 x 5 秒では
#                               「受理される前に打ち切る」→ map フレームが生えない。
#   INITIALPOSE_DELAY=          その再送間隔 [s] (fake_robot 既定 5.0)
#   START_X/START_Y/START_YAW_DEG   シムのスポーン位置 (既定は実機プローブ時の自己位置)
#   GOAL_X/GOAL_Y/GOAL_YAW_DEG      ゴール (既定は実機プローブと同じ)
#   SETTLE=                     ゴール送信前の待機秒 (bringup 完了待ち)
#   TIMEOUT=                    ゴールの打ち切り秒
#   EXTRA_OBSTACLES="x,y,r;..." 地図に無い障害物
#   CASE=                       ログ/結果の識別名
# set -u は使わない: ROS の setup.bash が未定義変数を参照するため。

source /opt/ros/humble/setup.bash
[ -f /opt/ros2_rust_ws/install/local_setup.bash ] && source /opt/ros2_rust_ws/install/local_setup.bash
[ -f /opt/ros_ws/install/setup.bash ] && source /opt/ros_ws/install/setup.bash

PLANNER=${PLANNER:-vi}
LOCAL_PLANNER=${LOCAL_PLANNER:-auto}
NAV2=${NAV2:-auto}
LOCALIZATION=${LOCALIZATION:-emcl2}
START_X=${START_X:--1.27}
START_Y=${START_Y:--0.63}
START_YAW_DEG=${START_YAW_DEG:-0}
GOAL_X=${GOAL_X:-4.28}
GOAL_Y=${GOAL_Y:--2.92}
GOAL_YAW_DEG=${GOAL_YAW_DEG:--24}
SETTLE=${SETTLE:-45}
TIMEOUT=${TIMEOUT:-300}
CASE=${CASE:-default}
EXTRA_OBSTACLES=${EXTRA_OBSTACLES:-}

# 前回ケースの残骸を必ず落とす。実機でも「docker exec 残骸」が graph を汚して
# 診断を狂わせたので、ここは徹底する (laser_filters のように名前が nav2_ で
# 始まらないノードが取り残されやすい)。
cleanup_ros() {
    pkill -f '/opt/ros/humble/lib/' 2>/dev/null
    pkill -f '/opt/ros_ws/install/lib/' 2>/dev/null
    pkill -f 'fake_robot.py' 2>/dev/null
    pkill -f 'ros2 launch daifuku_stack' 2>/dev/null
    pkill -f 'ros2 launch daifuku_bringup' 2>/dev/null
    sleep 2
    pkill -9 -f '/opt/ros/humble/lib/' 2>/dev/null
    pkill -9 -f '/opt/ros_ws/install/lib/' 2>/dev/null
    pkill -9 -f 'fake_robot.py' 2>/dev/null
    sleep 1
}
cleanup_ros
ros2 daemon stop >/dev/null 2>&1

SHARE=/opt/ros_ws/install/share/daifuku_stack
RUN=/tmp/pi4_sim/$CASE
rm -rf "$RUN"; mkdir -p "$RUN"
export ROS_LOG_DIR=$RUN/log

MAP_NAME=${MAP_NAME:-map_19f}       # 既定は 19F の地図
MAP=$SHARE/maps/$MAP_NAME.yaml
if [ ! -f "$MAP" ]; then
    echo "map not found: $MAP" >&2
    exit 2
fi

# 第 3 引数はこのスクリプトのあるディレクトリ (= /opt/sim)。downsample_map.py も
# fake_robot.py / probe.py と一緒にそこへ配られている。
python3 - "$MAP" "$RUN" "$(dirname "$0")" <<'PY'
import os, subprocess, sys, yaml
map_in, run, here = sys.argv[1:4]

free_thresh = os.environ.get("MAP_FREE_THRESH", "")
scale = os.environ.get("MAP_SCALE", "")
if scale and int(scale) > 1:
    # 解像度を落とした地図を作る (状態数 = 面積 x theta なので scale^2 で効く)。
    out = os.path.join(run, "map.yaml")
    cmd = [sys.executable, os.path.join(here, "downsample_map.py"),
           map_in, out, "--scale", scale]
    if free_thresh:
        cmd += ["--free-thresh", free_thresh]
    subprocess.run(cmd, check=True)
    print(f"MAP_OVERRIDE {out} scale={scale}")
elif free_thresh:
    meta = yaml.safe_load(open(map_in))
    # image: は元の地図ディレクトリを指すよう絶対パス化する
    meta["image"] = os.path.join(os.path.dirname(os.path.abspath(map_in)), meta["image"])
    meta["free_thresh"] = float(free_thresh)
    out = os.path.join(run, "map.yaml")
    yaml.safe_dump(meta, open(out, "w"))
    print(f"MAP_OVERRIDE {out} free_thresh={free_thresh}")

# パラメータの上書きは launch と同じ経路 (extra_params_file) に載せる。ここで
# nav2_params 相当を作り直すと config/stack/nav2/*.yaml の合成を素通りしてしまうので、
# 環境変数で触るキーだけの overlay を書く。
# BT の差し替え (planner:=vi 用) は navigation.launch.py 自身が behavior_trees/ を
# 指すので、ハーネス側では何もしない。
overlay = {}


def put(node, key, value):
    overlay.setdefault(node, {}).setdefault("ros__parameters", {})[key] = value


solver = os.environ.get("VI_SOLVER", "")
pub_vf = os.environ.get("VI_PUBLISH_VF", "")
planner_freq = os.environ.get("PLANNER_EXPECTED_FREQ", "")
map_scale = os.environ.get("VI_MAP_SCALE", "")
sink_dir = os.environ.get("VI_COMPACT_SINK_DIR", "")
bt_timeout = os.environ.get("BT_SERVER_TIMEOUT", "")

for node in ("vi_planner", "vi_global_planner"):
    if solver:
        put(node, "solver", solver)
    if pub_vf:
        put(node, "publish_value_function", pub_vf.lower() == "true")
# map_scale / compact_sink_dir は vi_global_planner だけが持つ。
if map_scale:
    put("vi_global_planner", "map_scale", int(map_scale))
if sink_dir:
    put("vi_global_planner", "compact_sink_dir", sink_dir)
if bt_timeout:
    # bt_navigator の BtActionNode がゴール受理 ack を待つ時間 [ms]。
    # nav2 既定は 20ms で、CPU 飢餓時はこれを超えて全アクションが即失敗する。
    put("bt_navigator", "default_server_timeout", int(bt_timeout))
if planner_freq:
    # planner_server は達成できない周波数を設定したときだけ実測値を WARN に
    # 出す。キャリブレーション (実機実測 7.6Hz) はこれを読む。
    put("planner_server", "expected_planner_frequency", float(planner_freq))

if overlay:
    out = os.path.join(run, "overlay.yaml")
    yaml.safe_dump(overlay, open(out, "w"))
    print(f"PARAMS_OVERLAY {out} solver={solver or '-'} publish_vf={pub_vf or '-'}")
PY

[ -f "$RUN/map.yaml" ] && MAP=$RUN/map.yaml
# overlay と EXTRA_PARAMS は params.compose が後勝ちで重ねる (カンマ区切り)。
# ros2 launch は `arg:=` (値が空) を malformed として弾くので、値があるときだけ渡す。
EXTRA=""
[ -f "$RUN/overlay.yaml" ] && EXTRA=$RUN/overlay.yaml
[ -n "${EXTRA_PARAMS:-}" ] && EXTRA="${EXTRA:+$EXTRA,}${EXTRA_PARAMS}"
params_arg=()
# overrides は**必ず明示的に渡す**。launch の既定は map_19f なので、渡さないと
# MAP_NAME を変えても 19F 用の調整 (emcl2 のリセット閾値など) が載ったままになる。
# 地図名と同名の override があればそれを、無ければ none (= 何も重ねない)。
if [ -z "${OVERRIDES:-}" ]; then
    if [ -f "$SHARE/config/overrides/$MAP_NAME.yaml" ]; then
        OVERRIDES=$MAP_NAME
    else
        OVERRIDES=none
    fi
fi
params_arg+=(overrides:="$OVERRIDES")
[ -n "$EXTRA" ] && params_arg+=(extra_params_file:="$EXTRA")

obs_arg=()
if [ -n "$EXTRA_OBSTACLES" ]; then
    flat=$(echo "$EXTRA_OBSTACLES" | tr ';' ',' | sed 's/,$//')
    obs_arg=(-p "extra_obstacles:=[$flat]")
fi

echo "=== CASE=$CASE planner=$PLANNER local=$LOCAL_PLANNER loc=$LOCALIZATION map=$MAP"
echo "=== start=($START_X,$START_Y,${START_YAW_DEG}deg) goal=($GOAL_X,$GOAL_Y,${GOAL_YAW_DEG}deg)"
nproc_v=$(nproc)
echo "=== nproc=$nproc_v mem.max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null) \
cpu.max=$(cat /sys/fs/cgroup/cpu.max 2>/dev/null)"

# fake_robot を先に上げる (map_server より先に /scan_raw と odom TF を出しておくと
# コストマップの立ち上がりが実機に近い)。
yaw_rad=$(python3 -c "import math;print(math.radians($START_YAW_DEG))")
sim_unknown=false
[ "${SIM_UNKNOWN_AS_OBSTACLE:-0}" = "1" ] && sim_unknown=true
init_arg=()
[ -n "${INITIALPOSE_MAX_TRIES:-}" ] && \
    init_arg+=(-p "initialpose_max_tries:=$INITIALPOSE_MAX_TRIES")
[ -n "${INITIALPOSE_DELAY:-}" ] && \
    init_arg+=(-p "initialpose_delay:=$INITIALPOSE_DELAY")

python3 "$(dirname "$0")/fake_robot.py" --ros-args \
    -p map_yaml:="$MAP" -p unknown_as_obstacle:="$sim_unknown" \
    -p initial_x:="$START_X" -p initial_y:="$START_Y" -p initial_yaw:="$yaw_rad" \
    "${init_arg[@]}" "${obs_arg[@]}" >"$RUN/fake_robot.log" 2>&1 &
SIM_PID=$!
sleep 3

# 角度フィルタ (/scan_raw -> /scan)。**実機ではこれも robot_bringup.launch.py が
# 立てる**ので、navigation.launch.py からは出ていった。
# lidar_driver:=false: /scan_raw は fake_robot.py が出すので、lidar:=2d の
# 実機ドライバ (urg_node) は立てない。
# odom_fusion は立てない (2D LiDAR に IMU は無く、odom -> base_footprint は
# fake_robot.py が出す)。
ros2 launch daifuku_bringup lidar_bringup.launch.py \
    lidar:=2d lidar_driver:=false "${params_arg[@]}" \
    >"$RUN/lidar.log" 2>&1 &
LIDAR_PID=$!

# config_watch:=off で設定の見張り (config_sentinel) を立てない。ここは 1 回きりの
# 構成を OVERRIDES で渡すので追随の対象外だし (params.follows_site)、告知する
# site_manager も居ない。**params_arg に混ぜないこと** — この引数を宣言している
# のは navigation だけで、上の lidar_bringup にも渡ってしまう。
ros2 launch daifuku_stack navigation.launch.py \
    use_rviz:=false \
    config_watch:=off \
    map:="$MAP" "${params_arg[@]}" \
    planner:="$PLANNER" local_planner:="$LOCAL_PLANNER" nav2:="$NAV2" \
    localization:="$LOCALIZATION" >"$RUN/nav.log" 2>&1 &
NAV_PID=$!

# 立ち上がり中の負荷とメモリを1秒毎に記録する (Pi4 4GB では OOM がここで出る)。
( while :; do
    printf '%s load=%s mem=%s\n' "$(date +%T)" \
        "$(cut -d' ' -f1-3 /proc/loadavg)" \
        "$(cat /sys/fs/cgroup/memory.current 2>/dev/null)"
    sleep 1
  done ) >"$RUN/load.log" 2>&1 &
MON_PID=$!

python3 "$(dirname "$0")/probe.py" \
    --goal-x "$GOAL_X" --goal-y "$GOAL_Y" --goal-yaw "$GOAL_YAW_DEG" \
    --settle "$SETTLE" --timeout "$TIMEOUT" 2>&1 | tee "$RUN/probe.log"
rc=${PIPESTATUS[0]}

kill $MON_PID $NAV_PID $LIDAR_PID $SIM_PID 2>/dev/null
sleep 3
cleanup_ros

echo "=== 実測 planner 周波数 (キャリブレーション用; 実機 Pi4 の実測は 7.6Hz) ==="
grep -h -o 'current loop rate is [0-9.]* Hz' "$RUN/nav.log" | tail -3 || true

echo "=== bond / lifecycle ==="
grep -h -E 'connected with bond|Managed nodes are active|Aborting bringup|Failed to change state|bond' \
    "$RUN/nav.log" | tail -12 || true

echo "=== KILLED (OOM 等でプロセスが落ちていないか) ==="
dmesg 2>/dev/null | tail -20 | grep -i -E 'oom|killed' || echo "(dmesg unavailable in container)"
grep -h -i -E 'error|killed|terminated|exited with|abort' \
    "$RUN"/nav.log "$RUN"/lidar.log 2>/dev/null | tail -25
echo "=== peak mem: $(sort -t= -k3 -n "$RUN/load.log" 2>/dev/null | tail -1)"
echo "=== CASE=$CASE done rc=$rc, logs in $RUN"
exit $rc
