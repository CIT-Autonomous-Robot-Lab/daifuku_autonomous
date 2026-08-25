#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${SCRIPT_DIR}/../docker/raspberrypi" && pwd)"
source "$(cd -- "${DOCKER_DIR}/../common/lib" && pwd)/compose.sh"

SERVICE="ros2"

# ros2 サービスは本体ドライバに依存しないので、入口 (compose.rt.yaml /
# compose.original.yaml) ではなく共通のほうを直接渡す。project 名が同じなので、
# どちらの入口で up したコンテナでも同じものが見える。
compose_init "${DOCKER_DIR}/compose.common.yaml"
compose_ensure_running "${SERVICE}"
# このイメージの .bashrc は素のままなので、-i を付けても entrypoint が積んだ
# オーバーレイは壊れない。従来どおり対話シェルとして開く。
compose_shell "${SERVICE}" -i
