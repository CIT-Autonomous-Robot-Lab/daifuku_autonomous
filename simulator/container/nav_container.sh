#!/usr/bin/env bash
# Pi4 相当に絞ったコンテナの**中**で、nav2 側だけを 1 ケース走らせる。
#
# 同じディレクトリの run_case.sh (pi4_sim ハーネス) の Isaac 版。設計上の違いは
# 1 点で、
#   run_case.sh     : fake_robot.py (地図をレイキャストする疑似ロボット) をこの中で起動
#   nav_container.sh: ロボットとセンサは**コンテナの外** (ホストの Isaac Sim) にいる
#
# ただし 2 本は**機械的に同期されていない**。書き出しは共通だったが以降それぞれに
# 手が入っており、現状 diff は 300 行を超える。片方を直したらもう片方も見ること
# (共通部の括り出しは未着手)。
#
# したがってここは「nav2 を起動してゴールを 1 回投げる」だけを行う。CPU/メモリの
# 制約はコンテナに掛かっているので、Isaac 側 (GPU を使う) は制約を受けない。
# これは意図した分離である: 実機でも Pi4 が担うのは nav2 であって、環境の物理は
# 現実世界が「無限の計算能力で」回している。
#
# 環境変数 (既定値は実機の現行設定に一致):
#   PLANNER=vi|navfn            planner:=
#   LOCAL_PLANNER=auto|nav2|vi  local_planner:=
#   NAV2=auto|true|false        nav2:= (既定 auto)。**launch の既定 false のまま
#                               渡すと PLANNER=navfn / LOCAL_PLANNER=nav2 が
#                               起動時にエラーで止まる**ので、ここは auto にして
#                               プランナに追従させる。BT 込みで測りたいときだけ
#                               NAV2=true を明示する (実機の既定は false)
#   LOCALIZATION=emcl2|amcl     localization:=
#   LIDAR=2d|mid360             lidar:= (Isaac 側の --lidar と必ず揃えること)
#   USE_SIM_TIME=true|false     use_sim_time:= (既定 false。true にすると
#                               RTF ゲートが厳格になる。run_isaac_case.sh 参照)
#   MAP_NAME=map_19f|map_tsudanuma|... share/maps/<name>.yaml (既定 map_19f)。
#                               OVERRIDES 未指定なら同名の override を自動で選ぶ
#   OVERRIDES= / EXTRA_PARAMS=  navigation.launch.py と同じ
#   GOAL_X/GOAL_Y/GOAL_YAW_DEG  ゴール
#   SETTLE= / TIMEOUT=          ゴール送信前の待機秒 / 打ち切り秒
#   CASE=                       ログの識別名
# set -u は使わない: ROS の setup.bash が未定義変数を参照するため。

source /opt/ros/humble/setup.bash
[ -f /opt/ros2_rust_ws/install/local_setup.bash ] && source /opt/ros2_rust_ws/install/local_setup.bash
[ -f /opt/ros_ws/install/setup.bash ] && source /opt/ros_ws/install/setup.bash

PLANNER=${PLANNER:-vi}
LOCAL_PLANNER=${LOCAL_PLANNER:-auto}
NAV2=${NAV2:-auto}
LOCALIZATION=${LOCALIZATION:-emcl2}
LIDAR=${LIDAR:-2d}
USE_SIM_TIME=${USE_SIM_TIME:-false}
GOAL_X=${GOAL_X:-4.28}
GOAL_Y=${GOAL_Y:--2.92}
GOAL_YAW_DEG=${GOAL_YAW_DEG:--24}
SETTLE=${SETTLE:-45}
TIMEOUT=${TIMEOUT:-300}
CASE=${CASE:-default}

# 前回ケースの残骸を必ず落とす (run_case.sh と同じ理由: 残ったノードが
# graph を汚して診断を狂わせる。laser_filters のように名前が nav2_ で始まらない
# ノードが取り残されやすい)。
cleanup_ros() {
    pkill -f '/opt/ros/humble/lib/' 2>/dev/null
    pkill -f '/opt/ros_ws/install/lib/' 2>/dev/null
    pkill -f 'ros2 launch daifuku_stack' 2>/dev/null
    pkill -f 'ros2 launch daifuku_bringup' 2>/dev/null
    sleep 2
    pkill -9 -f '/opt/ros/humble/lib/' 2>/dev/null
    pkill -9 -f '/opt/ros_ws/install/lib/' 2>/dev/null
    sleep 1
}
cleanup_ros
ros2 daemon stop >/dev/null 2>&1

SHARE=/opt/ros_ws/install/share/daifuku_stack
RUN=/tmp/simulator/$CASE
rm -rf "$RUN"; mkdir -p "$RUN"
export ROS_LOG_DIR=$RUN/log

MAP_NAME=${MAP_NAME:-map_19f}       # 既定は 19F の地図
MAP=$SHARE/maps/$MAP_NAME.yaml
if [ ! -f "$MAP" ]; then
    echo "map not found: $MAP" >&2
    exit 2
fi

# パラメータの上書きは launch と同じ経路 (extra_params_file) に載せる。
# ここで nav2_params 相当を作り直すと config/stack/nav2/*.yaml の合成を素通りするので、
# 環境変数で触るキーだけの overlay を書く (run_case.sh と同じ方式)。
python3 - "$RUN" <<'PY'
import os, sys, yaml
run = sys.argv[1]
overlay = {}


def put(node, key, value):
    overlay.setdefault(node, {}).setdefault("ros__parameters", {})[key] = value


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
    put("vi_planner", "publish_value_function", pub_vf.lower() == "true")
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
    print(f"PARAMS_OVERLAY {out}")
PY

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

echo "=== CASE=$CASE planner=$PLANNER local=$LOCAL_PLANNER loc=$LOCALIZATION"
echo "=== lidar=$LIDAR use_sim_time=$USE_SIM_TIME map=$MAP"
echo "=== goal=($GOAL_X,$GOAL_Y,${GOAL_YAW_DEG}deg)"
echo "=== nproc=$(nproc) mem.max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null) \
cpu.max=$(cat /sys/fs/cgroup/cpu.max 2>/dev/null)"

# Isaac が本当に喋っているか先に確かめる。ここで落としておかないと、
# 「nav2 が動かない」のか「シムが繋がっていない」のかの切り分けに時間を溶かす。
echo "=== Isaac 側のトピック待ち (30s) ==="
expected_scan=/scan_raw
[ "$LIDAR" = "mid360" ] && expected_scan=/livox/lidar
expected_odom=/odom
[ "$LIDAR" = "mid360" ] && expected_odom=/wheel/odom

for i in $(seq 1 30); do
    topics=$(timeout 5 ros2 topic list 2>/dev/null)
    if echo "$topics" | grep -qx "$expected_scan" && \
       echo "$topics" | grep -qx "$expected_odom"; then
        echo "  ok: $expected_scan and $expected_odom are present (after ${i}s)"
        break
    fi
    if [ "$i" = "30" ]; then
        echo "  !! $expected_scan / $expected_odom が見えない。" >&2
        echo "     Isaac 側が起動しているか、ROS_DOMAIN_ID が一致しているか、" >&2
        echo "     コンテナが --network host --ipc host で起動しているかを確認する。" >&2
        echo "     見えているトピック:" >&2
        echo "$topics" | sed 's/^/       /' >&2
        exit 4
    fi
    sleep 1
done

# --- リンク間 TF の所有者 ---------------------------------------------------
#
# TF ツリーは区間ごとに所有者を 1 つだけにする:
#   map  -> odom            : emcl2
#   odom -> base_footprint  : Isaac (2d) / ekf_node (mid360 + EKF)
#   base_footprint -> ...   : **ここで起動する robot_state_publisher**
#
# 実機と同じ配置にしてある (robot_bringup.launch.py も rsp を上げている)。
# Isaac 側は --publish-link-tf false が既定なのでリンク間 TF を出さない。
# 両方から出すと同じ transform が別ソースから流れ、tf2 がどちらを採るかで
# **自己位置だけが静かに壊れる** (トピックは全部出ているように見える)。
if [ "${PUBLISH_LINK_TF:-rsp}" = "rsp" ]; then
    URDF=${URDF:-/tmp/raspicat_plain.urdf}
    if [ ! -f "$URDF" ]; then
        if ! ros2 pkg prefix raspicat_description >/dev/null 2>&1; then
            echo "!! raspicat_description が無く、URDF ($URDF) も置かれていない。" >&2
            echo "   ホスト側で xacro 展開したものを URDF= で渡すか、" >&2
            echo "   Isaac 側を --publish-link-tf true にして" >&2
            echo "   PUBLISH_LINK_TF=isaac を指定すること。" >&2
            exit 5
        fi
        DESC=$(ros2 pkg prefix raspicat_description)/share/raspicat_description
        xacro "$DESC/urdf/raspicat.urdf.xacro" \
            gazebo_plugin:=false camera_gazebo_plugin:=false \
            imu_gazebo_plugin:=false > "$URDF" || exit 5
        echo "  generated URDF: $URDF"
    fi
    ros2 run robot_state_publisher robot_state_publisher \
        --ros-args -p use_sim_time:="$USE_SIM_TIME" \
        -p robot_description:="$(cat "$URDF")" \
        >"$RUN/rsp.log" 2>&1 &
    RSP_PID=$!
    echo "  robot_state_publisher pid=$RSP_PID"
else
    RSP_PID=""
    echo "  リンク間 TF は Isaac 側 (--publish-link-tf true) が出す想定"
fi

# TF チェーンが実際に引けるまで待つ。ここを確認せずに nav2 を上げると、
# laser_filters と emcl2 が「原因の分からない沈黙」で失敗する。
lidar_frame=lidar_link
[ "$LIDAR" = "mid360" ] && lidar_frame=livox_frame
echo "=== TF チェーン待ち (base_footprint -> $lidar_frame, 30s) ==="
tf_ok=0
for i in $(seq 1 30); do
    if timeout 5 ros2 run tf2_ros tf2_echo base_footprint "$lidar_frame" \
            >"$RUN/tf_echo.log" 2>&1; then
        tf_ok=1
    fi
    if grep -q 'Translation' "$RUN/tf_echo.log" 2>/dev/null; then
        echo "  ok: base_footprint -> $lidar_frame (after ${i}s)"
        grep -m2 -E 'Translation|Rotation' "$RUN/tf_echo.log" | sed 's/^/    /'
        tf_ok=1
        break
    fi
    tf_ok=0
    sleep 1
done
if [ "$tf_ok" != "1" ]; then
    echo "  !! base_footprint -> $lidar_frame が引けない。" >&2
    echo "     リンク間 TF の所有者が居ないか、frame 名が違う。" >&2
    timeout 5 ros2 topic echo --once /tf_static >>"$RUN/tf_echo.log" 2>&1
    tail -30 "$RUN/tf_echo.log" | sed 's/^/       /' >&2
    exit 6
fi

# --- センサとオドメトリ融合 -------------------------------------------------
#
# **実機ではこの 2 つは docker compose up で常駐している robot_bringup.launch.py の
# 一部**で、navigation.launch.py はセンサを一切立てない。ここは実機の raspicat
# サービスに相当する分を自前で上げる (robot_state_publisher を上で上げているのと
# 同じ理由)。robot_bringup.launch.py そのものを使わないのは、駆動ドライバ
# (実機の GPIO を掴む) まで立てようとしてしまうため。
#
# lidar_driver:=false が要点。実機ドライバ (livox_ros_driver2 / urg_node) と
# restamp_scan.py を起動せず、Isaac が出す /livox/lidar と /livox/imu (または
# /scan_raw) をそのまま使う。
#
# publish_lidar_tf:=false も必須。launch の既定は true (実機の URDF は
# livox_frame を出さないため) だが、こちらは上で robot_state_publisher が
# base_footprint -> $lidar_frame を出しており、二重配信になる。
ros2 launch daifuku_bringup lidar_bringup.launch.py \
    lidar:="$LIDAR" lidar_driver:=false publish_lidar_tf:=false \
    use_sim_time:="$USE_SIM_TIME" "${params_arg[@]}" \
    >"$RUN/lidar.log" 2>&1 &
LIDAR_PID=$!
echo "  lidar_bringup pid=$LIDAR_PID"

# use_mid360_imu:=true は lidar:=mid360 では必須。Isaac は上の ODOM_TOPIC=/wheel/odom
# と PUBLISH_ODOM_TF=false で EKF に譲る側に回っているので、これが立たないと
# odom -> base_footprint を誰も出さない。launch の既定も true だが、そちらは環境変数
# USE_MID360_IMU 次第で変わる (実機は Compose が配る) ので、ここでは明示しておく。
# lidar:=2d には IMU が無いので立てない (そのときは Isaac が odom -> base_footprint
# を出す側に回る)。
ODOM_PID=""
if [ "$LIDAR" = "mid360" ]; then
    ros2 launch daifuku_bringup odom_fusion.launch.py \
        use_mid360_imu:=true use_sim_time:="$USE_SIM_TIME" "${params_arg[@]}" \
        >"$RUN/odom_fusion.log" 2>&1 &
    ODOM_PID=$!
    echo "  odom_fusion pid=$ODOM_PID"
fi

# navigation は /scan と /odom の消費者に徹する (センサ関係の引数はもう無い)。
#
# config_watch:=off で設定の見張り (config_sentinel) を立てない。ここは 1 回きりの
# 構成を OVERRIDES で渡すので追随の対象外だし (params.follows_site)、告知する
# site_manager も居ない。DDS の参加者も 1 つ増やさずに済む。**params_arg に
# 混ぜないこと** — この引数を宣言しているのは navigation だけで、上の
# lidar_bringup / odom_fusion にも渡ってしまう。
ros2 launch daifuku_stack navigation.launch.py \
    use_rviz:=false \
    use_sim_time:="$USE_SIM_TIME" \
    config_watch:=off \
    map:="$MAP" "${params_arg[@]}" \
    planner:="$PLANNER" local_planner:="$LOCAL_PLANNER" nav2:="$NAV2" \
    localization:="$LOCALIZATION" >"$RUN/nav.log" 2>&1 &
NAV_PID=$!

( while :; do
    printf '%s load=%s mem=%s\n' "$(date +%T)" \
        "$(cut -d' ' -f1-3 /proc/loadavg)" \
        "$(cat /sys/fs/cgroup/memory.current 2>/dev/null)"
    sleep 1
  done ) >"$RUN/load.log" 2>&1 &
MON_PID=$!

# probe.py は 2 つのハーネスで共有する (ゴール投入と /plan /cmd_vel の計数)。
# 配り先の /opt/sim は run_isaac_case.sh / run_pi4_sim.ps1 の両方で共通。
python3 /opt/sim/probe.py \
    --goal-x "$GOAL_X" --goal-y "$GOAL_Y" --goal-yaw "$GOAL_YAW_DEG" \
    --settle "$SETTLE" --timeout "$TIMEOUT" 2>&1 | tee "$RUN/probe.log"
rc=${PIPESTATUS[0]}

kill $MON_PID $NAV_PID $ODOM_PID $LIDAR_PID $RSP_PID 2>/dev/null
sleep 3
cleanup_ros

echo "=== 実測 planner 周波数 (キャリブレーション用; 実機 Pi4 の実測は 7.6Hz) ==="
grep -h -o 'current loop rate is [0-9.]* Hz' "$RUN/nav.log" | tail -3 || true

echo "=== bond / lifecycle ==="
grep -h -E 'connected with bond|Managed nodes are active|Aborting bringup|Failed to change state|bond' \
    "$RUN/nav.log" | tail -12 || true

echo "=== KILLED (OOM 等でプロセスが落ちていないか) ==="
grep -h -i -E 'error|killed|terminated|exited with|abort' \
    "$RUN"/nav.log "$RUN"/lidar.log "$RUN"/odom_fusion.log 2>/dev/null | tail -25
echo "=== peak mem: $(sort -t= -k3 -n "$RUN/load.log" 2>/dev/null | tail -1)"
echo "=== CASE=$CASE done rc=$rc, logs in $RUN"
exit $rc
