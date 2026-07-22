#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${DOCKER_DIR}/compose.yaml"
SERVICE="ros2"

if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif sudo -n docker info >/dev/null 2>&1; then
  DOCKER=(sudo -n docker)
else
  echo "Dockerへ接続できません。Dockerが起動しているか、実行権限があるか確認してください。" >&2
  exit 1
fi

COMPOSE=("${DOCKER[@]}" compose -f "${COMPOSE_FILE}")

if [[ "$("${COMPOSE[@]}" ps --status running --quiet "${SERVICE}")" == "" ]]; then
  echo "${SERVICE} コンテナを起動します..."
  "${COMPOSE[@]}" up -d --no-build "${SERVICE}"
fi

echo "${SERVICE} コンテナに入ります。終了するには exit を実行してください。"
"${COMPOSE[@]}" exec "${SERVICE}" bash -lc \
  'source /opt/ros/humble/setup.bash && source /opt/ros_ws/install/setup.bash && exec bash -i'
