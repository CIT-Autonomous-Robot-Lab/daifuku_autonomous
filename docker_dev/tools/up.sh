#!/usr/bin/env bash
set -euo pipefail

TOOLS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(dirname -- "${TOOLS_DIR}")"
COMPOSE_FILES=(-f "${DEV_DIR}/compose.yaml")

if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  COMPOSE_FILES+=(-f "${DEV_DIR}/compose.wsl.yaml")
  WIN_SCRIPT="$(wslpath -w "${TOOLS_DIR}/network-windows.ps1")"
  echo "Requesting Administrator permission to enable Windows ICS/DHCP..."
  powershell.exe -NoProfile -Command \
    "Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"${WIN_SCRIPT}\" -Mode Enable'"
else
  COMPOSE_FILES+=(-f "${DEV_DIR}/compose.linux.yaml")
  bash "${TOOLS_DIR}/network-linux.sh" up "${RASPICAT_ETHERNET_IF:-}"
fi

docker compose "${COMPOSE_FILES[@]}" up -d --build
docker compose "${COMPOSE_FILES[@]}" ps
