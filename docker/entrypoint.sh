#!/usr/bin/env bash
set -e

source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"

if [[ -f /opt/ros2_rust_ws/install/local_setup.bash ]]; then
  source /opt/ros2_rust_ws/install/local_setup.bash
fi

if [[ -f "${ROS_WS:-/opt/ros_ws}/install/setup.bash" ]]; then
  source "${ROS_WS:-/opt/ros_ws}/install/setup.bash"
fi

exec "$@"
