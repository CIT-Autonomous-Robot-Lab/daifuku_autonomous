#!/usr/bin/env bash
set -eo pipefail

WS="${AUTONOMOUS_WS:-/workspaces/daifuku_autonomous}"
BUILD_JOBS="${BUILD_JOBS:-2}"

cd "${WS}"
source /opt/ros/humble/setup.bash
source /opt/raspicat2/install/setup.bash

mkdir -p src
vcs import . --skip-existing < daifuku_autonomous.repos

# livox_ros_driver2 は ROS 2 用の manifest と launch を上流で別名に置いている。
cp src/livox_ros_driver2/package_ROS2.xml src/livox_ros_driver2/package.xml
rm -rf src/livox_ros_driver2/launch
# Windows/Podman のバインドマウントは一部の Unix タイムスタンプを設定できない。
mkdir -p src/livox_ros_driver2/launch
while IFS= read -r -d '' launch_file; do
  target="src/livox_ros_driver2/launch/${launch_file##*/}"
  dd if="${launch_file}" of="${target}" status=none
done < <(find src/livox_ros_driver2/launch_ROS2 -maxdepth 1 -type f -print0)

# イメージは apt の索引を消してある。rosdep がソース固有のパッケージ
# (libaprutil1-dev など) を見つけることがあるので、入れる前に索引を取り直す。
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

# VI のパッケージは ros2_rust のツールチェーンが要るのでここでは建てない
# (開発ホストの経路計画は navfn)。
#
# **daifuku_rqt と daifuku_waypoint_manager は Pi では建てない** — rqt と RViz が
# ros:humble-ros-base に無いため。あちらは名前で選ぶので一覧から外すだけでよい。
#
# **raspimouse_msgs は raspicat_driver 抜きで要る** — joy_teleop.py が状態 LED の
# ために Leds をモジュール先頭で import するので、無いと起動時に落ちる。
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
