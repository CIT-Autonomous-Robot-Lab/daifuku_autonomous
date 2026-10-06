#!/usr/bin/env bash
# One phase of the usim VTC workflow inside the daifuku stack container.
#
#   bash /opt/usim_vtc/stack.sh prepare|mapping|navigation
#
# run_vtc.py starts one container per phase on the simulator's owned bridge and the run
# directory mounted at /output. **Only external simulation inputs come from
# outside** (usim Gazebo: /clock, /odom + odom -> base_footprint, /scan_raw,
# /motor_power). Never started here: robot_bringup.launch.py, raspicat_driver
# (GPIO/I2C), Livox, URG, EKF/odom_fusion. TF ownership:
#   odom -> base_footprint           usim Gazebo diff drive (exclusively)
#   base_footprint -> ... lidar_link robot_state_publisher below (fixed joints)
#   map -> odom                      slam_toolbox (mapping) / emcl2 (navigation)
# Mapping and navigation live in separate containers that never overlap, so
# the two scan_pipeline.launch.py instances never run concurrently.
#
# Exit codes: 0 ok, 2 usage, 10 inputs, 11 survey, 12 map save, 13 navigate,
# 14 prepare.
# set -u is not used: ROS setup scripts reference unset variables.
set -o pipefail

# Source the chain explicitly (do not rely on the image entrypoint): ROS, then the
# ROS 2 Rust underlay vi_planner was built against, then the workflow overlay last
# so it wins over a stale base /opt/ros_ws. ros2_rust is a --merge-install
# workspace, hence local_setup.bash (same as docker/common/entrypoint.sh).
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
if [[ ! -f /opt/ros2_rust_ws/install/local_setup.bash ]]; then
    echo "!! /opt/ros2_rust_ws underlay missing: vi_planner (planner:=vi) cannot run" >&2
    exit 2
fi
# shellcheck disable=SC1091
source /opt/ros2_rust_ws/install/local_setup.bash
# shellcheck disable=SC1091
source /opt/usim_vtc_ws/install/setup.bash

phase=${1:-}
OUT=/output
HERE=/opt/usim_vtc
URDF=$OUT/robot/raspicat_description/urdf/raspicat_plain.urdf
MAP=$OUT/mapping/vtc_map.yaml

# Launch arguments shared by both stack launches: 2D lidar fed by the simulator,
# no site overrides, no config sentinel, simulated clock.
COMMON_ARGS=(
    use_sim_time:=true
    lidar:=2d
    lidar_driver:=false
    overrides:=none
    config_watch:=off
    use_rviz:=false
    "scan_filter_params_file:=$HERE/scan_filter_gazebo.yaml"
)
MAPPING_ARGS=("${COMMON_ARGS[@]}")
NAVIGATION_ARGS=(
    "${COMMON_ARGS[@]}"
    use_system_monitor:=false
    localization:=emcl2
    planner:=vi
    local_planner:=vi
    nav2:=false
    "extra_params_file:=$HERE/navigation_gazebo.yaml"
    "map:=$MAP"
    "map_loc:=$MAP"
)

groups=()

start_group() {
    # $1 = log file, rest = command. setsid makes the child a process-group
    # leader so the whole ros2 launch tree can be signalled at once.
    local log=$1
    shift
    setsid "$@" >"$log" 2>&1 &
    groups+=("$!")
}

stop_groups() {
    local pgid waited=0
    for pgid in "${groups[@]}"; do
        # Humble's rclpy/rclcpp handle SIGINT, not SIGTERM.
        kill -INT -- "-$pgid" 2>/dev/null
    done
    while ((waited < 20)); do
        local alive=0
        for pgid in "${groups[@]}"; do
            kill -0 -- "-$pgid" 2>/dev/null && alive=1
        done
        ((alive)) || break
        sleep 0.5
        waited=$((waited + 1))
    done
    for pgid in "${groups[@]}"; do
        kill -KILL -- "-$pgid" 2>/dev/null
    done
    groups=()
}

trap stop_groups EXIT
trap 'exit 130' INT TERM

phase_dir() {
    DIR=$OUT/$1
    mkdir -p "$DIR"
    export ROS_LOG_DIR=$DIR/ros_log
    echo "=== phase=$1 ROS_DOMAIN_ID=$ROS_DOMAIN_ID RMW=$RMW_IMPLEMENTATION FASTDDS_BUILTIN_TRANSPORTS=$FASTDDS_BUILTIN_TRANSPORTS"
}

start_rsp() {
    if [[ ! -f $URDF ]]; then
        echo "!! prepared URDF missing: $URDF" >&2
        exit 10
    fi
    start_group "$DIR/rsp.log" ros2 run robot_state_publisher robot_state_publisher \
        --ros-args -p use_sim_time:=true -p robot_description:="$(cat "$URDF")"
}

check_inputs() {
    python3 "$HERE/vtc_ros.py" inputs --out "$DIR/inputs.json" "$@" || {
        echo "!! simulator inputs / TF contract not satisfied (see $DIR/inputs.json)" >&2
        exit 10
    }
}

case $phase in
prepare)
    phase_dir robot
    desc=$(ros2 pkg prefix raspicat_description)/share/raspicat_description || exit 14
    target=$OUT/robot/raspicat_description
    rm -rf "$target"
    mkdir -p "$target/urdf"
    cp "$desc/package.xml" "$target/package.xml" || exit 14
    cp -r "$desc/meshes" "$target/meshes" || exit 14
    # RSP consumes this description as-is; usim strips plugins in its simulator copy.
    xacro "$desc/urdf/raspicat.urdf.xacro" \
        gazebo_plugin:=false camera_gazebo_plugin:=false imu_gazebo_plugin:=false \
        lidar_frame:=lidar_link >"$target/urdf/raspicat_plain.urdf" || exit 14
    cp "$HERE/sources.repos" "$OUT/robot/sources.repos" 2>/dev/null
    echo "prepared $target/urdf/raspicat_plain.urdf"
    ;;
mapping)
    phase_dir mapping
    slam_defaults=$(ros2 pkg prefix daifuku_config)/share/daifuku_config/stack/mapping/slam_toolbox.yaml
    python3 "$HERE/prepare_slam_params.py" "$slam_defaults" "$DIR/slam_toolbox_gazebo.yaml" || exit 15
    MAPPING_ARGS+=("slam_params_file:=$DIR/slam_toolbox_gazebo.yaml")
    start_rsp
    check_inputs --require-idle-scan
    start_group "$DIR/launch.log" ros2 launch daifuku_stack mapping.launch.py "${MAPPING_ARGS[@]}"
    python3 "$HERE/vtc_ros.py" survey --out "$DIR/mapping.json" \
        --waypoints "${VTC_SURVEY:?VTC_SURVEY is required}" \
        --world-goal "${VTC_WORLD_GOAL:?VTC_WORLD_GOAL is required}" \
        --min-known-cells "${VTC_MIN_KNOWN_CELLS:-500}" || exit 11
    # free 0.15 keeps unobserved pixels (205) unknown; map_saver's default 0.25
    # would turn them free (see tools/maps/verify_map_thresholds.py).
    ros2 run nav2_map_server map_saver_cli -f "$DIR/vtc_map" --fmt pgm \
        --free 0.15 --occ 0.65 \
        --ros-args -p use_sim_time:=true -p save_map_timeout:=20.0 \
        >"$DIR/map_saver.log" 2>&1 || exit 12
    [[ -f $DIR/vtc_map.yaml && -f $DIR/vtc_map.pgm ]] || exit 12
    ;;
navigation)
    phase_dir navigation
    if [[ ! -f $MAP ]]; then
        echo "!! generated map missing: $MAP" >&2
        exit 13
    fi
    for pkg in vi_planner emcl2 daifuku_stack; do
        prefix=$(ros2 pkg prefix "$pkg" 2>/dev/null) || {
            echo "!! package $pkg not found in the sourced workspaces" >&2
            exit 13
        }
        echo "package $pkg -> $prefix"
    done
    start_rsp
    check_inputs --require-idle-scan
    start_group "$DIR/launch.log" ros2 launch daifuku_stack navigation.launch.py "${NAVIGATION_ARGS[@]}"
    python3 "$HERE/vtc_ros.py" navigate --out "$DIR/navigation.json" \
        --seed="${VTC_SEED:?VTC_SEED is required}" \
        --goal="${VTC_GOAL:?VTC_GOAL is required}" \
        --goal-timeout "${VTC_GOAL_TIMEOUT:-300}" || exit 13
    ;;
*)
    echo "usage: stack.sh prepare|mapping|navigation" >&2
    exit 2
    ;;
esac
