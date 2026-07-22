#!/usr/bin/env bash
set -e

source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"
if [[ -f "${RASPICAT2_WS:-/opt/raspicat2}/install/setup.bash" ]]; then
  source "${RASPICAT2_WS:-/opt/raspicat2}/install/setup.bash"
fi
if [[ -f /workspaces/daifuku_autonomous/install/setup.bash ]]; then
  source /workspaces/daifuku_autonomous/install/setup.bash
fi

exec "$@"
