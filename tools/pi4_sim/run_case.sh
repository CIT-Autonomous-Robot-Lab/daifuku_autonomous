#!/usr/bin/env bash
# コンテナ内で 1 ケース分を通しで実行する:
#   navigation.launch.py (lidar:=2d) + fake_robot.py を起動 -> probe.py で
#   NavigateToPose を1回投げる -> ログを要約して停止。
#
# 環境変数で条件を振る (既定値は Pi 実機の現行設定に一致):
#   PLANNER=vi|navfn            planner:=
#   LOCAL_PLANNER=auto|nav2|vi  local_planner:=
#   LOCALIZATION=emcl2|amcl     localization:=
#   MAP_NAME=map|map_tsudanuma|... share/maps/<name>.yaml を使う (既定 map)
#   VI_MAP_SCALE=               vi_global_planner の map_scale (地図をプランナ内部で
#                               粗くする倍率。津田沼 (5888x4000@0.05m) は 3 で
#                               1963x1334@0.15m = 1.57 億状態)
#   VI_COMPACT_SINK_DIR=        compact 経路の確定出力を置くディレクトリ ("" = RAM)
#   SIM_UNKNOWN_AS_OBSTACLE=1   シム LiDAR が未観測セルも壁として返す
#   EXTRA_PARAMS=               navigation.launch.py の extra_params_file
#                               (地図固有の上書き。例 config/tsudanuma_overrides.yaml)
#   MAP_FREE_THRESH=            指定すると map.yaml の free_thresh を差し替えた
#                               コピーを使う (実機は 0.25 = 未観測 205 が free 扱い)
#   VI_SOLVER=                  vi_*_planner の solver パラメータ上書き
#   VI_PUBLISH_VF=true|false    publish_value_function 上書き
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
    pkill -f 'ros2 launch autonomous_nav' 2>/dev/null
    sleep 2
    pkill -9 -f '/opt/ros/humble/lib/' 2>/dev/null
    pkill -9 -f '/opt/ros_ws/install/lib/' 2>/dev/null
    pkill -9 -f 'fake_robot.py' 2>/dev/null
    sleep 1
}
cleanup_ros
ros2 daemon stop >/dev/null 2>&1

SHARE=/opt/ros_ws/install/share/autonomous_nav
RUN=/tmp/pi4_sim/$CASE
rm -rf "$RUN"; mkdir -p "$RUN"
export ROS_LOG_DIR=$RUN/log

MAP=$SHARE/maps/${MAP_NAME:-map}.yaml
PARAMS=$SHARE/config/nav2_params.yaml
if [ ! -f "$MAP" ]; then
    echo "map not found: $MAP" >&2
    exit 2
fi

python3 - "$MAP" "$PARAMS" "$RUN" "$(dirname "$0")" <<'PY'
import os, subprocess, sys, yaml
map_in, params_in, run, tools = sys.argv[1:5]

free_thresh = os.environ.get("MAP_FREE_THRESH", "")
scale = os.environ.get("MAP_SCALE", "")
if scale and int(scale) > 1:
    # 解像度を落とした地図を作る (状態数 = 面積 x theta なので scale^2 で効く)。
    out = os.path.join(run, "map.yaml")
    cmd = [sys.executable, os.path.join(tools, "downsample_map.py"),
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

solver = os.environ.get("VI_SOLVER", "")
pub_vf = os.environ.get("VI_PUBLISH_VF", "")
planner_freq = os.environ.get("PLANNER_EXPECTED_FREQ", "")
map_scale = os.environ.get("VI_MAP_SCALE", "")
sink_dir = os.environ.get("VI_COMPACT_SINK_DIR", "")
# BT の差し替え (planner:=vi 用) は navigation.launch.py 自身が behavior_trees/ を
# 指すので、ハーネス側では何もしない。
if (solver or pub_vf or planner_freq or map_scale or sink_dir
        or os.environ.get("BT_SERVER_TIMEOUT", "")):
    params = yaml.safe_load(open(params_in))
    for node in ("vi_global_planner", "vi_local_planner"):
        rp = params.get(node, {}).get("ros__parameters")
        if rp is None:
            continue
        if solver:
            rp["solver"] = solver
        if pub_vf:
            rp["publish_value_function"] = pub_vf.lower() == "true"
        # map_scale / compact_sink_dir は vi_global_planner だけが持つ。
        if node == "vi_global_planner":
            if map_scale:
                rp["map_scale"] = int(map_scale)
            if sink_dir:
                rp["compact_sink_dir"] = sink_dir
    if os.environ.get("BT_SERVER_TIMEOUT", ""):
        # bt_navigator の BtActionNode がゴール受理 ack を待つ時間 [ms]。
        # nav2 既定は 20ms で、CPU 飢餓時はこれを超えて全アクションが即失敗する。
        params["bt_navigator"]["ros__parameters"]["default_server_timeout"] = int(
            os.environ["BT_SERVER_TIMEOUT"]
        )
    if planner_freq:
        # planner_server は達成できない周波数を設定したときだけ実測値を WARN に
        # 出す。キャリブレーション (実機実測 7.6Hz) はこれを読む。
        params["planner_server"]["ros__parameters"]["expected_planner_frequency"] = float(
            planner_freq
        )
    out = os.path.join(run, "nav2_params.yaml")
    yaml.safe_dump(params, open(out, "w"))
    print(f"PARAMS_OVERRIDE {out} solver={solver or '-'} publish_vf={pub_vf or '-'}")
PY

[ -f "$RUN/map.yaml" ] && MAP=$RUN/map.yaml
[ -f "$RUN/nav2_params.yaml" ] && PARAMS=$RUN/nav2_params.yaml

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
python3 "$(dirname "$0")/fake_robot.py" --ros-args \
    -p map_yaml:="$MAP" -p unknown_as_obstacle:="$sim_unknown" \
    -p initial_x:="$START_X" -p initial_y:="$START_Y" -p initial_yaw:="$yaw_rad" \
    "${obs_arg[@]}" >"$RUN/fake_robot.log" 2>&1 &
SIM_PID=$!
sleep 3

ros2 launch autonomous_nav navigation.launch.py \
    lidar:=2d use_rviz:=false \
    map:="$MAP" params_file:="$PARAMS" \
    extra_params_file:="${EXTRA_PARAMS:-}" \
    planner:="$PLANNER" local_planner:="$LOCAL_PLANNER" \
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

kill $MON_PID $NAV_PID $SIM_PID 2>/dev/null
sleep 3
cleanup_ros

echo "=== 実測 planner 周波数 (キャリブレーション用; 実機 Pi4 の実測は 7.6Hz) ==="
grep -h -o 'current loop rate is [0-9.]* Hz' "$RUN/nav.log" | tail -3 || true

echo "=== bond / lifecycle ==="
grep -h -E 'connected with bond|Managed nodes are active|Aborting bringup|Failed to change state|bond' \
    "$RUN/nav.log" | tail -12 || true

echo "=== KILLED (OOM 等でプロセスが落ちていないか) ==="
dmesg 2>/dev/null | tail -20 | grep -i -E 'oom|killed' || echo "(dmesg unavailable in container)"
grep -h -i -E 'error|killed|terminated|exited with|abort' "$RUN"/nav.log | tail -25
echo "=== peak mem: $(sort -t= -k3 -n "$RUN/load.log" 2>/dev/null | tail -1)"
echo "=== CASE=$CASE done rc=$rc, logs in $RUN"
exit $rc
