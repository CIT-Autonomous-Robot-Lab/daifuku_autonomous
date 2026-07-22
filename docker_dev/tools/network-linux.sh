#!/usr/bin/env bash
set -euo pipefail

PROFILE_NAME="${RASPICAT_PROFILE:-raspicat-docker-dev}"
ACTION="${1:-up}"
REQUESTED_NIC="${2:-${RASPICAT_ETHERNET_IF:-}}"

if [[ "$(id -u)" -ne 0 ]]; then
  exec sudo --preserve-env=RASPICAT_PROFILE,RASPICAT_ETHERNET_IF "$0" "$@"
fi

if ! command -v nmcli >/dev/null; then
  echo "NetworkManager (nmcli) is required on the Linux host." >&2
  exit 1
fi

detect_nic() {
  local candidates
  candidates="$(nmcli -t -f DEVICE,TYPE,STATE device status | awk -F: '$2=="ethernet" && $1!="" {print $1}')"
  if [[ -z "${candidates}" ]]; then
    echo "No Ethernet interface was found. Set RASPICAT_ETHERNET_IF explicitly." >&2
    exit 1
  fi
  if [[ "$(wc -w <<<"${candidates}")" -ne 1 ]]; then
    echo "Multiple Ethernet interfaces found: ${candidates//$'\n'/ }. Set RASPICAT_ETHERNET_IF." >&2
    exit 1
  fi
  printf '%s\n' "${candidates}"
}

NIC="${REQUESTED_NIC:-$(detect_nic)}"

case "${ACTION}" in
  up)
    nmcli connection show "${PROFILE_NAME}" >/dev/null 2>&1 \
      && nmcli connection delete "${PROFILE_NAME}" >/dev/null
    nmcli connection add type ethernet con-name "${PROFILE_NAME}" ifname "${NIC}" \
      ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled
    nmcli connection up "${PROFILE_NAME}" ifname "${NIC}"
    echo "DHCP/NAT is active on ${NIC}; host=10.42.0.1, clients=10.42.0.0/24."
    ;;
  down)
    nmcli connection show "${PROFILE_NAME}" >/dev/null 2>&1 \
      && nmcli connection delete "${PROFILE_NAME}"
    ;;
  *)
    echo "Usage: $0 up|down [ethernet-interface]" >&2
    exit 2
    ;;
esac
