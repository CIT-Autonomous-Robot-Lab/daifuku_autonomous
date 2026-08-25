#!/usr/bin/env bash
# RTX マシン (Linux) 側のオーケストレータ。1 ケースを通しで実行する:
#
#   1. 地図 -> ワールド USD を生成 (無ければ)
#   2. Isaac Sim を起動 (GPU、制限なし)
#   3. Pi4 相当に絞ったコンテナで nav2 を起動しゴールを 1 回投げる
#   4. Isaac を止め、RTF を判定して結果を出す
#
# ## 同一ホストで回すこと
#
# Isaac と nav2 コンテナは**同じマシン**に置く。Isaac をクラウド、nav2 を手元に
# 分けると DDS がネットワーク越しになり、まさに測りたい「Pi4 の遅さ」に
# ネットワーク遅延が混ざって区別できなくなる。
#
# ## 速度をそろえる仕組み
#
#   nav2 側 : cgroup の CPU quota で Pi4 相当に絞る (pi4_sim ハーネスと同じ方式・同じ値)
#   Isaac側 : 絞らない。実機でも「環境の物理」は Pi4 の外で回っている
#
# quota は**実時間**基準なので、RTF が 1.0 を割ると測定が歪む。判定は rtf_gate.py が
# 行う (詳細はあのファイルの冒頭)。
#
# 使い方:
#   export ISAACSIM=$HOME/isaacsim              # python.sh のあるディレクトリ
#   bash simulator/scripts/run_isaac_case.sh baseline
#   LIDAR=mid360 bash simulator/scripts/run_isaac_case.sh mid360_run
#   USE_SIM_TIME=true bash simulator/scripts/run_isaac_case.sh simtime_run
set -o pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PROJ=$REPO/simulator                    # uv プロジェクト
PKG=$PROJ/src/daifuku_sim

# map_to_usd は numpy / pyyaml / Pillow を要る。ホストにそれが入っている保証は
# ないので uv 経由で呼ぶ (uv.lock で固定された環境が使われる)。
# isaac_raspicat.py だけはこの venv を使わない — Isaac は Kit 同梱の Python で
# 動き、外の site-packages を見ないため。あちらは python.sh にファイルパスを渡す。
if ! command -v uv >/dev/null 2>&1; then
    echo "uv が見つからない。simulator/ は uv プロジェクトになっている。" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 2
fi
UVRUN=(uv run --project "$PROJ")

CASE=${1:-${CASE:-baseline}}

# --- Isaac 側 ---------------------------------------------------------------
ISAAC_RUNTIME=${ISAAC_RUNTIME:-binary}   # binary = $ISAACSIM/python.sh / pip = uv --extra isaac
ISAACSIM=${ISAACSIM:-$HOME/isaacsim}
LIDAR=${LIDAR:-2d}
MAP_NAME=${MAP_NAME:-19f/map_19f}   # nav_container.sh / launch の既定と揃えること
UNKNOWN=${UNKNOWN:-free}            # map_to_usd.py の --unknown
WALL_HEIGHT=${WALL_HEIGHT:-2.0}
ROBOT_USD=${ROBOT_USD:-}
ROBOT_URDF=${ROBOT_URDF:-}
START_X=${START_X:--1.27}           # pi4_sim ハーネスと同じ既定 (実機プローブ時の自己位置)
START_Y=${START_Y:--0.63}
START_YAW=${START_YAW:-0}
HEADLESS=${HEADLESS:-1}
USE_SIM_TIME=${USE_SIM_TIME:-false}
RENDER_DT=${RENDER_DT:-0.0333333}
PHYSICS_DT=${PHYSICS_DT:-0.005}

# --- nav2 コンテナ側 (値は scripts/run_pi4_sim.ps1 と一致させる) -------------
IMAGE=${IMAGE:-daifuku-autonomous:humble-amd64}
CONTAINER=${CONTAINER:-isaacsim_pi4}
QUOTA=${QUOTA:-6000}                # period 10000us に対し 0.6 コア
PERIOD=${PERIOD:-10000}
CPUSET=${CPUSET:-0-3}
MEMORY=${MEMORY:-3g}
DOMAIN_ID=${DOMAIN_ID:-91}
NO_LIMITS=${NO_LIMITS:-0}
ENGINE=${ENGINE:-podman}

# --- RTF ゲート -------------------------------------------------------------
MIN_RTF=${MIN_RTF:-0.95}
MAX_BELOW_FRAC=${MAX_BELOW_FRAC:-0.05}

RUN=${RUN:-/tmp/simulator_host/$CASE}
rm -rf "$RUN"; mkdir -p "$RUN"

echo "=== [1/4] ワールド USD を生成 ==="
# ワールドと map_server は**同じ地図**から作る。このハーネスの設計上の利点は
# 「地図と環境が定義上ずれない」ことなので、ここが食い違うと利点が消えるどころか、
# 「ずれているのに、ずれていないつもりで測る」という最悪の状態になる。
#
# 粗い地図で回したい場合は `uv run downsample-map` の出力を maps/ に置いて
# MAP_NAME で指す (WORLD_MAP_YAML だけ差し替えるのは意図的なずれの注入なので、
# 明示的に指定したときだけ許す)。
MAP_YAML=$REPO/src/daifuku_stack/maps/$MAP_NAME.yaml
if [ ! -f "$MAP_YAML" ]; then
    echo "map not found: $MAP_YAML" >&2
    echo "  MAP_NAME は src/daifuku_stack/maps/<name>.yaml を指す。" >&2
    echo "  downsample した地図を使うなら、その出力を maps/ に置いてから指すこと。" >&2
    exit 2
fi

WORLD_MAP_YAML=${WORLD_MAP_YAML:-$MAP_YAML}
if [ "$WORLD_MAP_YAML" != "$MAP_YAML" ]; then
    echo "  !! 注意: ワールドと map_server で違う地図を使う"
    echo "     world      : $WORLD_MAP_YAML"
    echo "     map_server : $MAP_YAML"
    echo "     これは**意図的な地図と環境の不一致**の注入である。"
    echo "     emcl2 の alpha 崩壊を再現したいのでなければ、指定を外すこと。"
fi

WORLD=$RUN/world.usda
"${UVRUN[@]}" map-to-usd "$WORLD_MAP_YAML" -o "$WORLD" \
    --unknown "$UNKNOWN" --wall-height "$WALL_HEIGHT" || exit $?

echo
echo "=== [2/4] Isaac Sim を起動 ==="
# Isaac Sim の入れ方は 2 通りあり、どちらも同じ isaac_raspicat.py を動かせる。
#   binary : 配布バイナリ。Kit 同梱の Python を python.sh 経由で使う。
#            この venv の site-packages は**見ない**。
#   pip    : `uv sync --extra isaac` で venv に入れた isaacsim。venv の python で動く。
# 既定を binary にしてあるのは、pip 版が 5.5 GiB あり、RTX マシンに既にバイナリ版が
# 入っていることが多いため。
case "$ISAAC_RUNTIME" in
    binary)
        if [ ! -x "$ISAACSIM/python.sh" ]; then
            echo "Isaac Sim が見つからない: $ISAACSIM/python.sh" >&2
            echo "  export ISAACSIM=<isaac-sim のインストール先> を設定するか、" >&2
            echo "  pip 版を使うなら ISAAC_RUNTIME=pip にすること" >&2
            echo "  (事前に: cd simulator && uv sync --extra isaac)。" >&2
            exit 2
        fi
        ISAAC_CMD=("$ISAACSIM/python.sh")
        ;;
    pip)
        # import できるかを先に確かめる。--extra isaac を忘れていると Kit の起動
        # 途中まで進んでから落ちるので、原因がプロファイルや GPU に見えてしまう。
        #
        # --no-sync が要る。付けないと `uv run --extra isaac` は**その場で同期を
        # 始め**、確認のつもりが 20 GiB 超のダウンロードになる (実際に踏んだ)。
        # 起動側にも付けて、ケース実行の途中で勝手に環境が変わらないようにする。
        if ! "${UVRUN[@]}" --extra isaac --no-sync python -c "import isaacsim" 2>/dev/null; then
            echo "pip 版 Isaac Sim が入っていない (または venv が Python 3.12 でない)。" >&2
            echo "  cd $PROJ && uv sync --extra isaac" >&2
            echo "  isaacsim 6.0.1 は cp312 のみ。venv が 3.12 でないと" >&2
            echo "  マーカ不一致で**何も入らないまま成功する**。確認:" >&2
            echo "    uv run --no-sync python -c 'import sys; print(sys.version)'" >&2
            exit 2
        fi
        # **内蔵 ROS 2 を使うので、起動前に環境変数を立てる。** pip 版は
        # isaacsim.ros2.core/<distro>/lib に Humble 一式を同梱していて、
        # ROS_DISTRO / RMW_IMPLEMENTATION と共有ライブラリの探索パスが
        # 揃っていないと `ROS2 Bridge startup failed` で**ブリッジだけが死ぬ**。
        # Kit 自体は上がりトピックも出ないので、原因がグラフや DDS に見える。
        # ホストに ROS 2 を source 済みならそちらが優先される (上書きしない)。
        ros2_lib=$("${UVRUN[@]}" --extra isaac --no-sync python -c             "import isaacsim, os; print(os.path.join(os.path.dirname(isaacsim.__file__), 'exts', 'isaacsim.ros2.core', 'humble', 'lib'))"             2>/dev/null) || ros2_lib=""
        export ROS_DISTRO=${ROS_DISTRO:-humble}
        export RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}
        if [ -n "$ros2_lib" ] && [ -d "$ros2_lib" ]; then
            export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$ros2_lib"
            export PATH="$PATH:$ros2_lib"     # Windows (Git Bash) はこちらを見る
            echo "  ros2 libs: $ros2_lib"
        else
            echo "  warn: 内蔵 ROS 2 の lib が見つからない。ホスト側の ROS 2 に頼る。" >&2
        fi
        ISAAC_CMD=("${UVRUN[@]}" --extra isaac --no-sync python)
        ;;
    *)
        echo "ISAAC_RUNTIME は binary か pip: $ISAAC_RUNTIME" >&2
        exit 2
        ;;
esac
echo "  runtime: $ISAAC_RUNTIME"

robot_arg=()
if [ -n "$ROBOT_USD" ]; then
    robot_arg=(--robot "$ROBOT_USD")
elif [ -n "$ROBOT_URDF" ]; then
    robot_arg=(--urdf "$ROBOT_URDF")
else
    echo "ROBOT_USD か ROBOT_URDF のどちらかを指定すること。" >&2
    echo "  URDF は raspicat_description のものを gazebo_plugin:=false で" >&2
    echo "  xacro 展開したものを使う (README の手順 2 を参照)。" >&2
    exit 2
fi

# odom のトピックは lidar モードから推測させない。launch 側の配線に合わせて渡す:
#   2d     : nav2 が /odom を直接使う
#   mid360 : ekf_node が /wheel/odom と /imu/mid360 を融合して /odom を出すので、
#            Isaac は /wheel/odom を出し、odom->base_footprint の TF は ekf に譲る
if [ "$LIDAR" = "mid360" ]; then
    ODOM_TOPIC=/wheel/odom
    PUBLISH_ODOM_TF=false
else
    ODOM_TOPIC=/odom
    PUBLISH_ODOM_TF=true
fi

sim_time_arg=()
[ "$USE_SIM_TIME" = "true" ] && sim_time_arg=(--use-sim-time)
headless_arg=()
[ "$HEADLESS" = "1" ] && headless_arg=(--headless)

RTF_REPORT=$RUN/rtf.jsonl
ROS_DOMAIN_ID=$DOMAIN_ID "${ISAAC_CMD[@]}" "$PKG/isaac_raspicat.py" \
    --world "$WORLD" "${robot_arg[@]}" \
    --lidar "$LIDAR" \
    --odom-topic "$ODOM_TOPIC" --publish-odom-tf "$PUBLISH_ODOM_TF" \
    -x "$START_X" -y "$START_Y" --yaw "$START_YAW" \
    --physics-dt "$PHYSICS_DT" --render-dt "$RENDER_DT" \
    --rtf-report "$RTF_REPORT" \
    "${sim_time_arg[@]}" "${headless_arg[@]}" >"$RUN/isaac.log" 2>&1 &
ISAAC_PID=$!
echo "  isaac pid=$ISAAC_PID log=$RUN/isaac.log"

cleanup() {
    kill "$ISAAC_PID" 2>/dev/null
    sleep 5
    kill -9 "$ISAAC_PID" 2>/dev/null
}
trap cleanup EXIT

echo
echo "=== [3/4] Pi4 相当コンテナで nav2 を実行 ==="
if ! $ENGINE ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    limits=()
    if [ "$NO_LIMITS" != "1" ]; then
        limits=(--cpuset-cpus "$CPUSET" --cpu-period "$PERIOD" --cpu-quota "$QUOTA"
                --memory "$MEMORY" --memory-swap "$MEMORY")
    fi
    echo "  creating container $CONTAINER (${limits[*]:-no limits})"
    # --network host / --ipc host は Isaac (ホスト側プロセス) と DDS で繋ぐため。
    # ipc を共有しないと Fast DDS の共有メモリトランスポートが通らず、
    # ディスカバリだけ成功して**データが流れない**という分かりにくい形で失敗する。
    # **--shm-size は付けないこと。** podman 6 は --ipc host と併用すると
    # `cannot set shmsize when running in the {host } IPC Namespace` で起動を拒む
    # (ipc を共有した時点で /dev/shm はホストのものなので、そもそも意味がない)。
    $ENGINE run -d --name "$CONTAINER" \
        "${limits[@]}" \
        --network host --ipc host \
        -e ROS_DOMAIN_ID="$DOMAIN_ID" \
        -e ROS_LOCALHOST_ONLY=0 \
        -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
        -e HOME=/tmp -e ROS_HOME=/tmp/ros -e ROS_LOG_DIR=/tmp/ros/log \
        --entrypoint /bin/bash \
        "$IMAGE" -lc "sleep infinity" >/dev/null || exit $?
fi

SHARE=/opt/ros_ws/install/share/daifuku_stack
# コンテナ内で走るものは simulator/container/ にまとまっている。probe.py は
# pi4_sim ハーネスと共有 (ゴール投入と計数のロジックは同じ)。配り先の /opt/sim も
# run_pi4_sim.ps1 と共通で、nav_container.sh がそこから probe.py を呼ぶ。
$ENGINE exec "$CONTAINER" bash -lc "mkdir -p /opt/sim"
$ENGINE cp "$PROJ/container/probe.py" "$CONTAINER:/opt/sim/probe.py"
$ENGINE cp "$PROJ/container/nav_container.sh" "$CONTAINER:/opt/sim/nav_container.sh"
# robot_state_publisher には Isaac が読み込んだのと**同じ** URDF を使わせる。
# 別々に生成すると、リンクのオフセットが食い違っても誰も気づけない。
if [ -n "$ROBOT_URDF" ]; then
    $ENGINE cp "$ROBOT_URDF" "$CONTAINER:${CONTAINER_URDF:-/tmp/raspicat_plain.urdf}"
fi
# 編集中の launch / config をコンテナへ反映する (pi4_sim 側と同じ理由で bind mount しない)。
$ENGINE exec "$CONTAINER" bash -lc "rm -rf $SHARE/config $SHARE/scripts"
for d in behavior_trees config launch maps rviz src; do
    [ -d "$REPO/src/daifuku_stack/$d" ] && \
        $ENGINE cp "$REPO/src/daifuku_stack/$d" "$CONTAINER:$SHARE/"
done
$ENGINE exec "$CONTAINER" bash -lc \
    'for f in /opt/sim/*.py /opt/sim/*.sh; do [ -e "$f" ] && sed -i "s/\r$//" "$f"; done
     chmod +x /opt/sim/*.sh'

# 呼び出し側が Fast DDS のプロファイルを指しているなら、**同じものを**コンテナへも
# 配って両側で使う。Windows + WSL (networkingMode=mirrored) で回すときは
# container/fastdds_mirrored.xml がこれに当たり、片側だけに掛けても繋がらない。
# Linux ホストで同一マシンに置く分には不要 (未設定なら何もしない)。
dds_env=()
if [ -n "$FASTRTPS_DEFAULT_PROFILES_FILE" ]; then
    $ENGINE exec "$CONTAINER" bash -lc "mkdir -p /etc/fastdds"
    $ENGINE cp "$FASTRTPS_DEFAULT_PROFILES_FILE" "$CONTAINER:/etc/fastdds/profiles.xml"
    dds_env=(-e FASTRTPS_DEFAULT_PROFILES_FILE=/etc/fastdds/profiles.xml)
    echo "  dds profile: $FASTRTPS_DEFAULT_PROFILES_FILE -> /etc/fastdds/profiles.xml"
fi

$ENGINE exec \
    "${dds_env[@]}" \
    -e CASE="$CASE" \
    -e LIDAR="$LIDAR" \
    -e USE_SIM_TIME="$USE_SIM_TIME" \
    -e MAP_NAME="$MAP_NAME" \
    -e PLANNER="${PLANNER:-vi}" \
    -e LOCAL_PLANNER="${LOCAL_PLANNER:-auto}" \
    -e NAV2="${NAV2:-auto}" \
    -e LOCALIZATION="${LOCALIZATION:-vi}" \
    -e OVERRIDES="${OVERRIDES:-}" \
    -e EXTRA_PARAMS="${EXTRA_PARAMS:-}" \
    -e VI_SOLVER="${VI_SOLVER:-}" \
    -e VI_PUBLISH_VF="${VI_PUBLISH_VF:-}" \
    -e VI_MAP_SCALE="${VI_MAP_SCALE:-}" \
    -e VI_COMPACT_SINK_DIR="${VI_COMPACT_SINK_DIR:-}" \
    -e BT_SERVER_TIMEOUT="${BT_SERVER_TIMEOUT:-}" \
    -e PLANNER_EXPECTED_FREQ="${PLANNER_EXPECTED_FREQ:-}" \
    -e PUBLISH_LINK_TF="${PUBLISH_LINK_TF:-rsp}" \
    -e URDF="${CONTAINER_URDF:-/tmp/raspicat_plain.urdf}" \
    -e GOAL_X="${GOAL_X:-4.28}" -e GOAL_Y="${GOAL_Y:--2.92}" \
    -e GOAL_YAW_DEG="${GOAL_YAW_DEG:--24}" \
    -e START_X="$START_X" -e START_Y="$START_Y" -e START_YAW="$START_YAW" \
    -e SETTLE="${SETTLE:-45}" -e TIMEOUT="${TIMEOUT:-300}" \
    "$CONTAINER" bash /opt/sim/nav_container.sh 2>&1 | tee "$RUN/nav_side.log"
nav_rc=${PIPESTATUS[0]}

echo
echo "=== [4/4] RTF 判定 ==="
kill "$ISAAC_PID" 2>/dev/null
sleep 5

gate_arg=()
[ "$USE_SIM_TIME" = "true" ] && gate_arg=(--use-sim-time)
"${UVRUN[@]}" rtf-gate "$RTF_REPORT" \
    --min-rtf "$MIN_RTF" --max-below-frac "$MAX_BELOW_FRAC" "${gate_arg[@]}"
gate_rc=$?

echo
echo "=============================================================="
echo " CASE      : $CASE"
echo " lidar     : $LIDAR   use_sim_time: $USE_SIM_TIME"
echo " nav2 側   : rc=$nav_rc   (0 = ゴール到達)"
echo " RTF ゲート: rc=$gate_rc  (0 = 計測成立, 3 = 計測無効)"
echo " ログ      : $RUN"
echo "=============================================================="

# RTF ゲートが落ちた実行は、nav2 が成功していても結果を信用してはいけない。
[ "$gate_rc" = "3" ] && exit 3
exit "$nav_rc"
