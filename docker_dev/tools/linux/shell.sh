#!/usr/bin/env bash
set -euo pipefail

LINUX_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(dirname -- "$(dirname -- "${LINUX_DIR}")")"
exec docker compose -f "${DEV_DIR}/compose.yaml" exec raspicat-dev /ros_entrypoint.sh bash
