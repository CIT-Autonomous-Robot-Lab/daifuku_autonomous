#!/usr/bin/env bash
set -eo pipefail

WS="${AUTONOMOUS_WS:-/workspaces/daifuku_autonomous}"
BUILD_JOBS="${BUILD_JOBS:-2}"

cd "${WS}"
source /opt/ros/humble/setup.bash
source /opt/raspicat2/install/setup.bash

mkdir -p src
vcs import . --skip-existing < autonomous_bot.repos

# livox_ros_driver2 keeps its ROS 2 manifest and launch files under alternate
# names in the upstream repository.
cp src/livox_ros_driver2/package_ROS2.xml src/livox_ros_driver2/package.xml
rm -rf src/livox_ros_driver2/launch
cp -a src/livox_ros_driver2/launch_ROS2 src/livox_ros_driver2/launch

# The image removes apt indexes to stay reasonably small. rosdep may still
# discover source-specific packages (for example libaprutil1-dev), so refresh
# the index before it installs them.
apt-get update
rosdep install \
  --from-paths \
    src/autonomous_nav \
    src/emcl2_ros2 \
    src/livox_ros_driver2 \
  --ignore-src \
  --rosdistro humble \
  -r -y

# VI packages require the separate ros2_rust toolchain. Navfn is the supported
# development fallback here and matches the currently working Humble setup.
colcon build \
  --symlink-install \
  --parallel-workers "${BUILD_JOBS}" \
  --packages-select autonomous_nav emcl2 livox_ros_driver2 \
  --cmake-args \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DROS_EDITION=ROS2 \
    -DDISTRO_ROS=humble

printf '\nBuild complete. Refresh this shell with:\n  source %s/install/setup.bash\n' "${WS}"
