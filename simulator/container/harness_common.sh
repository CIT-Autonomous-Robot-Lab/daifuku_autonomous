#!/usr/bin/env bash
# run_case.sh と nav_container.sh が共有する起動前処理。
#
# 両方から source される。set -u は使わない (ROS の setup.bash が未定義変数を
# 参照するため)。呼び元は source したあと、自分だけの起動 (fake_robot / Isaac
# 待ち / rsp) を書いて、最後に summarize_nav_logs を呼ぶ。
#
# **overlay の 1 段目はパッケージ名。** extra_params_file も overrides と同じ規則
# で読まれるので、ノード名を直に置くと「知らないパッケージ名です」で起動時に落ちる。

source_ros_overlays() {
    source /opt/ros/humble/setup.bash
    [ -f /opt/ros2_rust_ws/install/local_setup.bash ] && source /opt/ros2_rust_ws/install/local_setup.bash
    [ -f /opt/ros_ws/install/setup.bash ] && source /opt/ros_ws/install/setup.bash
}

default_nav_env() {
    PLANNER=${PLANNER:-vi}
    LOCAL_PLANNER=${LOCAL_PLANNER:-auto}
    NAV2=${NAV2:-auto}
    # **既定が emcl2 でないのは、既定の場所 (19f) がそれでは立たないため。**
    # src/daifuku_config/overrides/19f.yaml が vi_planner の localizer を
    # 'belief' (VIOLA) にしているので、localization:=emcl2 だと推定器が 2 つに
    # なり backends.validate_localization が起動時に止める。tsudanuma は
    # 逆に belief を置けない (密ソルバが要り 3.17GB で OOM) ので emcl2 に戻すこと。
    LOCALIZATION=${LOCALIZATION:-vi}
    GOAL_X=${GOAL_X:-4.28}
    GOAL_Y=${GOAL_Y:--2.92}
    GOAL_YAW_DEG=${GOAL_YAW_DEG:--24}
    SETTLE=${SETTLE:-45}
    TIMEOUT=${TIMEOUT:-300}
    CASE=${CASE:-default}
}

# 前回ケースの残骸を落とす。名前が nav2_ で始まらないノード (laser_filters など)
# が取り残されやすい。追加のパターンは引数で渡す (pi4_sim の fake_robot.py)。
cleanup_ros() {
    pkill -f '/opt/ros/humble/lib/' 2>/dev/null
    pkill -f '/opt/ros_ws/install/lib/' 2>/dev/null
    pkill -f 'ros2 launch daifuku_stack' 2>/dev/null
    pkill -f 'ros2 launch daifuku_bringup' 2>/dev/null
    local extra
    for extra in "$@"; do
        pkill -f "$extra" 2>/dev/null
    done
    sleep 2
    pkill -9 -f '/opt/ros/humble/lib/' 2>/dev/null
    pkill -9 -f '/opt/ros_ws/install/lib/' 2>/dev/null
    for extra in "$@"; do
        pkill -9 -f "$extra" 2>/dev/null
    done
    sleep 1
}

# **止めたら必ず立て直す。** ros2cli は毎回 127.0.0.1 のデーモンへ繋ぎに行き、
# 居なければ ECONNREFUSED ですぐ諦める…… のは NAT の話。.wslconfig が
# networkingMode=mirrored だと Linux の 127.0.0.1 は Windows 側にも向くので、
# 繋ぎ先が居ないと**握られたまま 2 分待つ**。`timeout 5 ros2 topic list` は
# 全部空を返し、Isaac が正しく喋っていても「トピックが見えない」で exit 4 になる。
restart_ros_daemon() {
    ros2 daemon stop >/dev/null 2>&1
    ros2 daemon start >/dev/null 2>&1
}

# SHARE / CONFIG_SHARE / MAP を決める。無い地図は exit 2。
# 呼び元は MAP_NAME を渡してよく、空なら 19f/map_19f。
resolve_map() {
    SHARE=/opt/ros_ws/install/share/daifuku_stack
    # overrides は daifuku_config の share。**maps/ を持つ daifuku_stack とは置き場が違う**
    # (2026-08-08 まで $SHARE/config/overrides/ を見ていた。設定を src/daifuku_config/ へ出して
    # daifuku_stack がその段を install しなくなった時点から、**どの地図でも overrides:=none
    # に落ちていたはず** — この修正ともども**未検証**。効かせるには一度ビルドが要る)。
    CONFIG_SHARE=/opt/ros_ws/install/share/daifuku_config
    MAP_NAME=${MAP_NAME:-19f/map_19f}
    MAP=$SHARE/maps/$MAP_NAME.yaml
    if [ ! -f "$MAP" ]; then
        echo "map not found: $MAP" >&2
        exit 2
    fi
}

setup_run_dir() {
    local root=$1
    RUN=$root/$CASE
    rm -rf "$RUN"; mkdir -p "$RUN"
    export ROS_LOG_DIR=$RUN/log
}

# 環境変数で触るキーだけの overlay を $RUN/overlay.yaml に書く。
# ここで nav2_params 相当を作り直すと src/daifuku_config/stack/nav2/*.yaml の合成を
# 素通りするので、launch と同じ extra_params_file 経路に載せる。
write_params_overlay() {
    python3 - "$RUN" <<'PY'
import os, sys, yaml
run = sys.argv[1]
overlay = {}


def put(node, key, value):
    # **1 段目はパッケージ名。** extra_params_file も overrides と同じ規則で読まれる
    # (params.compose の KNOWN_PACKAGES) ので、ノード名を直に置くと
    # 「知らないパッケージ名です」で起動時に落ちる。2026-08-25 に設定が
    # daifuku_config へ出て 1 段目がパッケージ名になったとき、ここが追随して
    # いなかった (2026-09-02 に実測で判明。VI_SOLVER などを渡すと必ず落ちていた)。
    overlay.setdefault("daifuku_stack", {}).setdefault(node, {}).setdefault(
        "ros__parameters", {}
    )[key] = value


solver = os.environ.get("VI_SOLVER", "")
pub_vf = os.environ.get("VI_PUBLISH_VF", "")
planner_freq = os.environ.get("PLANNER_EXPECTED_FREQ", "")
map_scale = os.environ.get("VI_MAP_SCALE", "")
sink_dir = os.environ.get("VI_COMPACT_SINK_DIR", "")
bt_timeout = os.environ.get("BT_SERVER_TIMEOUT", "")

# 2026-08-08 の上流の整理で vi_global_planner ノードは消え、広域だけ VI
# (local_planner:=nav2) も同じ vi_planner を follow: false で立てるようになった。
# 宛先が 1 つになったので、ここも 1 つだけに書く。
if solver:
    put("vi_planner", "solver", solver)
if pub_vf:
    # 配信の on/off だった publish_value_function は 2026-08-09 の上流の整理で
    # value_publish_interval_ms に吸収された (負 = 配信そのものを立てない)。
    put("vi_planner", "value_publish_interval_ms",
        500 if pub_vf.lower() == "true" else -1)
if map_scale:
    put("vi_planner", "map_scale", int(map_scale))
if sink_dir:
    put("vi_planner", "compact_sink_dir", sink_dir)
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
}

# overlay と EXTRA_PARAMS を params_arg に載せる。overrides は**必ず明示的に渡す**。
# launch の既定は 19f なので、渡さないと MAP_NAME を変えても 19F 用の調整が載ったまま。
# 選ぶのは**地図の入っているフォルダ名 (= 場所の名前)**。ファイル名ではない。
resolve_params_arg() {
    local extra=""
    [ -f "$RUN/overlay.yaml" ] && extra=$RUN/overlay.yaml
    [ -n "${EXTRA_PARAMS:-}" ] && extra="${extra:+$extra,}${EXTRA_PARAMS}"
    params_arg=()
    if [ -z "${OVERRIDES:-}" ]; then
        local site_name
        site_name=$(dirname "$MAP_NAME")
        if [ -f "$CONFIG_SHARE/overrides/$site_name.yaml" ]; then
            OVERRIDES=$site_name
        else
            OVERRIDES=none
        fi
    fi
    params_arg+=(overrides:="$OVERRIDES")
    [ -n "$extra" ] && params_arg+=(extra_params_file:="$extra")
}

start_load_monitor() {
    ( while :; do
        printf '%s load=%s mem=%s\n' "$(date +%T)" \
            "$(cut -d' ' -f1-3 /proc/loadavg)" \
            "$(cat /sys/fs/cgroup/memory.current 2>/dev/null)"
        sleep 1
      done ) >"$RUN/load.log" 2>&1 &
    MON_PID=$!
}

summarize_nav_logs() {
    echo "=== 実測 planner 周波数 (キャリブレーション用; 実機 Pi4 の実測は 7.6Hz) ==="
    grep -h -o 'current loop rate is [0-9.]* Hz' "$RUN/nav.log" | tail -3 || true

    echo "=== bond / lifecycle ==="
    grep -h -E 'connected with bond|Managed nodes are active|Aborting bringup|Failed to change state|bond' \
        "$RUN/nav.log" | tail -12 || true

    echo "=== KILLED (OOM 等でプロセスが落ちていないか) ==="
}
