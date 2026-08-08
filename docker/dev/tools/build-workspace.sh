#!/usr/bin/env bash
set -eo pipefail

WS="${AUTONOMOUS_WS:-/workspaces/daifuku_autonomous}"
BUILD_JOBS="${BUILD_JOBS:-2}"

cd "${WS}"
source /opt/ros/humble/setup.bash
source /opt/raspicat2/install/setup.bash

mkdir -p src
vcs import . --skip-existing < daifuku_autonomous.repos

# livox_ros_driver2 keeps its ROS 2 manifest and launch files under alternate
# names in the upstream repository.
cp src/livox_ros_driver2/package_ROS2.xml src/livox_ros_driver2/package.xml
rm -rf src/livox_ros_driver2/launch
# Windows/Podman bind mounts do not support setting every Unix timestamp.
mkdir -p src/livox_ros_driver2/launch
while IFS= read -r -d '' launch_file; do
  target="src/livox_ros_driver2/launch/${launch_file##*/}"
  dd if="${launch_file}" of="${target}" status=none
done < <(find src/livox_ros_driver2/launch_ROS2 -maxdepth 1 -type f -print0)

# The image removes apt indexes to stay reasonably small. rosdep may still
# discover source-specific packages (for example libaprutil1-dev), so refresh
# the index before it installs them.
apt-get update
rosdep install \
  --from-paths \
    src/daifuku_stack \
    src/daifuku_rqt \
    src/emcl2_ros2 \
    src/livox_ros_driver2 \
  --ignore-src \
  --rosdistro humble \
  -r -y

# VI packages require the separate ros2_rust toolchain. Navfn is the supported
# development fallback here and matches the currently working Humble setup.
#
# daifuku_rqt and daifuku_waypoint_manager are built here but deliberately not
# in the Raspberry Pi image: they need rqt and RViz, which ros:humble-ros-base
# does not carry. The Pi's build-workspace.sh selects packages by name, so
# leaving them out of that list is all it takes.
#
# raspimouse_msgs is here without raspicat_driver on purpose: the driver is only
# ever launched (and never on this side), but joy_teleop.py imports Leds at module
# scope for the status LEDs, so leaving it out kills joy_teleop at startup.
colcon build \
  --symlink-install \
  --parallel-workers "${BUILD_JOBS}" \
  --packages-select daifuku_bringup daifuku_config daifuku_config_manager daifuku_stack \
                    daifuku_rqt daifuku_waypoint_manager emcl2 livox_ros_driver2 \
                    raspimouse_msgs \
  --cmake-args \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DROS_EDITION=ROS2 \
    -DDISTRO_ROS=humble

printf '\nBuild complete. Refresh this shell with:\n  source %s/install/setup.bash\n' "${WS}"
