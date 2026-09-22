#!/usr/bin/env bash
# Pi4 相当に絞ったコンテナの**中**で、nav2 側だけを 1 ケース走らせる。
#
# 同じディレクトリの run_case.sh (pi4_sim ハーネス) の Isaac 版。設計上の違いは
# 1 点で、
#   run_case.sh     : fake_robot.py (地図をレイキャストする疑似ロボット) をこの中で起動
#   nav_container.sh: ロボットとセンサは**コンテナの外** (ホストの Isaac Sim) にいる
#
# 起動前処理 (ROS overlay、cleanup、overrides、params overlay、ログ要約) は
# harness_common.sh。片方を直したら共通部を見ること。
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
#   MAP_NAME=19f/map_19f|tsudanuma/map_tsudanuma|...  share/maps/<name>.yaml
#                               OVERRIDES 未指定ならフォルダ名 (= 場所) の override を選ぶ
#   OVERRIDES= / EXTRA_PARAMS=  navigation.launch.py と同じ
#   GOAL_X/GOAL_Y/GOAL_YAW_DEG  ゴール
#   SETTLE= / TIMEOUT=          ゴール送信前の待機秒 / 打ち切り秒
#   CASE=                       ログの識別名
# set -u は使わない: ROS の setup.bash が未定義変数を参照するため。

. "$(dirname "$0")/harness_common.sh"
source_ros_overlays
default_nav_env
LIDAR=${LIDAR:-2d}
USE_SIM_TIME=${USE_SIM_TIME:-false}

cleanup_ros
restart_ros_daemon
resolve_map
setup_run_dir /tmp/simulator
write_params_overlay
resolve_params_arg

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

# **Isaac を立て直さずにケースを回すと、ロボットは前回の走行の終了位置に残る。**
# それでも下の /initialpose は START_X/Y/YAW (= スポーン姿勢) を撒くので、種と
# 実際の姿勢が食い違ったまま走り出す。エラーも警告も出ず、症状は
# 「自己位置が最初からずれている」だけになるので、ローカライザやセンサの不調に
# 見える (2026-08-20 に踏んで、測定を 1 回無駄にした)。Isaac の odom は
# スポーン姿勢が原点なので、原点から離れていたら立て直しを促す。
# run_isaac_case.sh から回すぶんには毎回 Isaac を起動するので当たらない。
if [ "${ALLOW_STALE_POSE:-0}" != "1" ]; then
    od=$(timeout 20 ros2 topic echo --once --field pose.pose.position "$expected_odom" 2>/dev/null          | awk '/^x:/{x=$2} /^y:/{y=$2} END{printf "%s %s", x, y}')
    if [ -n "$od" ] &&        [ "$(awk -v v="$od" 'BEGIN{split(v,a," "); print (sqrt(a[1]*a[1]+a[2]*a[2])>0.3)?1:0}')" = "1" ]; then
        echo "  !! Isaac の odom が原点から離れている (x y = $od)。" >&2
        echo "     ロボットが前回の走行位置に残ったままなので、下で撒く /initialpose" >&2
        echo "     ($START_X, $START_Y, $START_YAW rad) と実際の姿勢が食い違う。" >&2
        echo "     Isaac を立て直してからやり直すこと (承知の上なら ALLOW_STALE_POSE=1)。" >&2
        exit 5
    fi
    echo "  ok: odom は原点付近 (x y = ${od:-取得できず})"
fi

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
# **LiDAR ドライバは立てない。** Isaac が /livox/lidar と /livox/imu (または
# /scan_raw) を直接出すので、実機ドライバ (livox_ros_driver2 / urg_node) は要らず、
# 取り付け位置の静的 TF も上の robot_state_publisher が出している。
# 点群を /scan に変える段は 2026-08-25 から navigation.launch.py が持つので、
# lidar:= と lidar_driver:=false は下の navigation へ渡す。
#
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

# navigation は /odom の消費者に徹し、**/scan は自分で作る**
# (scan_pipeline.launch.py。lidar_driver:=false なので restamp は挟まない)。
#
# config_watch:=off で設定の見張り (config_sentinel) を立てない。ここは 1 回きりの
# 構成を OVERRIDES で渡すので追随の対象外だし (params.follows_site)、告知する
# site_manager も居ない。DDS の参加者も 1 つ増やさずに済む。**params_arg に
# 混ぜないこと** — この引数を宣言しているのは navigation だけで、上の
# odom_fusion にも渡ってしまう。
#
# 地図は 2 枚 (navigation -> /map、localization -> /map_loc) だが、ハーネスが指すのは
# MAP_NAME の 1 枚だけなので両方へ同じものを渡す。**map_loc:= を落とすと
# OVERRIDES=none のとき resolve_map が「どの地図を読むか決まりません」で止まる。**
ros2 launch daifuku_stack navigation.launch.py \
    use_rviz:=false \
    use_sim_time:="$USE_SIM_TIME" \
    config_watch:=off \
    lidar:="$LIDAR" lidar_driver:=false \
    map:="$MAP" map_loc:="$MAP" "${params_arg[@]}" \
    planner:="$PLANNER" local_planner:="$LOCAL_PLANNER" nav2:="$NAV2" \
    localization:="$LOCALIZATION" >"$RUN/nav.log" 2>&1 &
NAV_PID=$!

start_load_monitor

# **自己位置の種を撒く。** Isaac はロボットを既知の姿勢
# (run_isaac_case.sh の START_X / START_Y / START_YAW) にスポーンさせるので、
# 同じ姿勢を /initialpose へ流す。localization:=vi (VIOLA) は種が無いと
# 地図全域からの大域初期化になり、**収まらないまま「pose withheld」で
# 黙る** —— そのとき nav2 のフィードバックは distance_remaining が
# 初期値のまま動かず、機体だけがふらつくので、症状はプランナの不調に見える
# (2026-08-20 に踏んだ)。emcl2 でも同じトピックで効く。
#
# 購読側が上がるまで待てないので、15 秒後から 1Hz x 5 回まく。
if [ -n "$START_X" ] || [ -n "$START_Y" ] || [ -n "$START_YAW" ]; then
    ( sleep 15
      read -r qz qw <<EOF
$(python3 -c "import math,sys; y=float(sys.argv[1]); print(math.sin(y/2), math.cos(y/2))" "${START_YAW:-0}")
EOF
      ros2 topic pub -t 5 -r 1 /initialpose geometry_msgs/PoseWithCovarianceStamped         "{header: {frame_id: map}, pose: {pose: {position: {x: ${START_X:-0}, y: ${START_Y:-0}, z: 0.0}, orientation: {x: 0.0, y: 0.0, z: $qz, w: $qw}}}}"
    ) >"$RUN/initialpose.log" 2>&1 &
    echo "  initialpose seed: (${START_X:-0}, ${START_Y:-0}, ${START_YAW:-0} rad) -> /initialpose"
fi

# probe.py は 2 つのハーネスで共有する (ゴール投入と /plan /cmd_vel の計数)。
# 配り先の /opt/sim は run_isaac_case.sh / run_pi4_sim.ps1 の両方で共通。
python3 /opt/sim/probe.py \
    --goal-x "$GOAL_X" --goal-y "$GOAL_Y" --goal-yaw "$GOAL_YAW_DEG" \
    --settle "$SETTLE" --timeout "$TIMEOUT" 2>&1 | tee "$RUN/probe.log"
rc=${PIPESTATUS[0]}

kill $MON_PID $NAV_PID $ODOM_PID $RSP_PID 2>/dev/null
sleep 3
cleanup_ros

summarize_nav_logs
grep -h -i -E 'error|killed|terminated|exited with|abort' \
    "$RUN"/nav.log "$RUN"/odom_fusion.log 2>/dev/null | tail -25
echo "=== peak mem: $(sort -t= -k3 -n "$RUN/load.log" 2>/dev/null | tail -1)"
echo "=== CASE=$CASE done rc=$rc, logs in $RUN"
exit $rc
