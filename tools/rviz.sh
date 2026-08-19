#!/usr/bin/env bash
# WSL (Ubuntu 22.04 / ROS 2 Humble) から RViz だけを立てて、機体のトピックを見る。
#
#   tools/rviz.sh                  navigation.rviz
#   tools/rviz.sh mapping          mapping.rviz
#   tools/rviz.sh path/to/x.rviz   任意の設定
#
# 機体側のスタックは Pi の docker が持っているので、こちらで建てるのは RViz の
# パネルプラグイン (daifuku_waypoint_manager) 1 つだけ。初回だけ 2 分ほどかかる。
# 建て直すときは RVIZ_WS ごと消す: rm -rf ~/.cache/daifuku_rviz_ws
#
# 見えないときは docs/setup/network.md#wsl2から直接つなぐ (bridged が要る)。
# -u は付けない (ROS の setup.bash が未定義変数を読む)。
set -Eeo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WS="${RVIZ_WS:-${HOME}/.cache/daifuku_rviz_ws}"
PANEL="${REPO}/src/daifuku_waypoint_manager"

case "${1:-navigation}" in
  *.rviz) CONFIG="$1" ;;
  *) CONFIG="${REPO}/src/daifuku_stack/rviz/${1:-navigation}.rviz" ;;
esac
[[ -f "${CONFIG}" ]] || { echo "no such rviz config: ${CONFIG}" >&2; exit 2; }

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash

# パネルが無いと navigation.rviz は起動時に読めない (Panels に名指しがある)。
if [[ ! -f "${WS}/install/setup.bash" ]]; then
  echo "building ${PANEL##*/} into ${WS} (first run only)"
  mkdir -p "${WS}"
  # colcon は cwd に log/ を掘る。/mnt/c だと掘れないので ext4 側で回す。
  (cd "${WS}" && colcon --log-base "${WS}/log" build --paths "${PANEL}" \
    --build-base "${WS}/build" --install-base "${WS}/install")
fi
# shellcheck disable=SC1091
source "${WS}/install/setup.bash"

# 機体 (docker/raspberrypi/compose.common.yaml) と同じ既定。
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-90}"
export ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"

exec rviz2 -d "${CONFIG}"
