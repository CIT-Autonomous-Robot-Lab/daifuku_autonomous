#!/usr/bin/env bash
set -Eeuo pipefail

LINUX_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(dirname -- "$(dirname -- "${LINUX_DIR}")")"
source "$(cd -- "${DEV_DIR}/../common/lib" && pwd)/compose.sh"

SERVICE="raspicat-dev"

# raspberrypi/ と違って自動起動はしない。dev のイメージはビルドが重く、
# 起動していない＝まだ up.sh を実行していない、であることがほとんど。
compose_init "${DEV_DIR}/compose.yaml"
compose_shell "${SERVICE}"
