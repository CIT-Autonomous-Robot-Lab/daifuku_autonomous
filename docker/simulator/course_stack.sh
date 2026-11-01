#!/usr/bin/env bash
# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
# Dedicated 2025 composition: no hardware drivers, mapping, or truth localization.
set -eo pipefail
source /opt/ros/humble/setup.bash
source /opt/ros2_rust_ws/install/local_setup.bash
source /opt/usim_vtc_ws/install/setup.bash
OUT=/output/course
mkdir -p "$OUT"
export ROS_LOG_DIR="$OUT/ros_log"
if [[ "${COURSE_BACKEND:-gazebo}" == "isaacsim" ]]; then
    export FASTDDS_BUILTIN_TRANSPORTS=LARGE_DATA
fi
pids=()
cleanup() {
    for pid in "${pids[@]}"; do kill -INT -- "-$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT TERM
urdf=/output/robot/raspicat_description/urdf/raspicat_plain.urdf
setsid ros2 run robot_state_publisher robot_state_publisher \
    --ros-args -p use_sim_time:=true -p robot_description:="$(cat "$urdf")" \
    >"$OUT/rsp.log" 2>&1 &
pids+=("$!")
setsid ros2 launch daifuku_bringup navigation.tsukuba2025.launch.py \
    use_sim_time:=true lidar:=mid360 lidar_driver:=false \
    use_rviz:=false config_watch:=off use_respawn:=false \
    >"$OUT/navigation.log" 2>&1 &
pids+=("$!")
coverage=()
if [[ -n "${COURSE_END_INDEX:-}" ]]; then
    coverage=(--end-index "${COURSE_END_INDEX}")
fi
python3 -u /opt/usim_vtc/course_driver.py --output "$OUT" \
    --transform /output/transform.json \
    --startup-timeout "${COURSE_STARTUP_TIMEOUT:-900}" \
    --goal-timeout "${COURSE_GOAL_TIMEOUT:-1800}" \
    "${coverage[@]}"
