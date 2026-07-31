#!/usr/bin/env bash
# raspberrypi/ と dev/ の両イメージで共有するentrypoint。
#
# オーバーレイの集合はイメージごとに違うが、互いに素なので「あれば読む」で
# まとめられる。存在しないものは黙って飛ばすため、どちらのイメージでも
# 従来と同じ順序・同じ結果になる。
#
#   raspberrypi/: ros2_rust_ws -> ROS_WS (/opt/ros_ws)
#   dev/        : RASPICAT2_WS (/opt/raspicat2) -> AUTONOMOUS_WS (マウントした作業ツリー)
set -e

source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"

# ros2_rust だけ local_setup.bash。--merge-install したオーバーレイなので、
# 親のsetup.bashを再帰的に読み直させない。
if [[ -f /opt/ros2_rust_ws/install/local_setup.bash ]]; then
  source /opt/ros2_rust_ws/install/local_setup.bash
fi

for ws in \
  "${RASPICAT2_WS:-/opt/raspicat2}" \
  "${ROS_WS:-/opt/ros_ws}" \
  "${AUTONOMOUS_WS:-/workspaces/daifuku_autonomous}"
do
  if [[ -f "${ws}/install/setup.bash" ]]; then
    source "${ws}/install/setup.bash"
  fi
done

exec "$@"
