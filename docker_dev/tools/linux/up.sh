#!/usr/bin/env bash
set -euo pipefail

LINUX_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(dirname -- "$(dirname -- "${LINUX_DIR}")")"
COMPOSE_FILES=(-f "${DEV_DIR}/compose.yaml")

if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  COMPOSE_FILES+=(-f "${DEV_DIR}/compose.wsl.yaml")
  # Under WSL the Windows-side static LAN is still configured by the Windows
  # tool, so this Linux script intentionally reaches into ../windows.
  WINDOWS_DIR="$(cd -- "${LINUX_DIR}/../windows" && pwd)"
  WIN_SCRIPT="$(wslpath -w "${WINDOWS_DIR}/network.ps1")"
  echo "Requesting Administrator permission to configure the Windows static robot LAN..."
  powershell.exe -NoProfile -Command \
    "Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"${WIN_SCRIPT}\" -Mode Static'"
else
  COMPOSE_FILES+=(-f "${DEV_DIR}/compose.linux.yaml")
  bash "${LINUX_DIR}/network.sh" up "${RASPICAT_ETHERNET_IF:-}"
fi

docker compose "${COMPOSE_FILES[@]}" up -d --build
docker compose "${COMPOSE_FILES[@]}" ps
