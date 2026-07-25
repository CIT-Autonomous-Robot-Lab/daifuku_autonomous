#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_WORKSPACE="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

WORKSPACE="${DEFAULT_WORKSPACE}"
RUST_WORKSPACE="${ROS2_RUST_WS:-${HOME}/ros2_rust_ws}"
BUILD_JOBS="${BUILD_JOBS:-2}"
CARGO_PROFILE_ARG="--release"

usage() {
  cat <<'EOF'
価値反復プランナ（vi_global_planner: 広域 / vi_local_planner: 狭域）を
ビルドします。事前にscripts/setup_native_ros2_rust.shを実行してください。

Usage:
  bash scripts/setup_native_vi.sh [options]

Options:
  --workspace PATH      ROS 2ワークスペース（既定: リポジトリルート）
  --ros2-rust-ws PATH   ros2_rustワークスペース（既定: ~/ros2_rust_ws）
  --jobs N              並列ビルド数（既定: 2、低メモリ時は1）
  --debug               cargoのreleaseプロファイルを使わずビルド
  -h, --help            このヘルプを表示

vi_nodeとvi_interfacesはros2_rustのDocker環境向けのため、ここではビルド
対象にしません。
EOF
}

while (($#)); do
  case "$1" in
    --workspace)
      WORKSPACE="${2:?--workspace requires a path}"
      shift 2
      ;;
    --ros2-rust-ws)
      RUST_WORKSPACE="${2:?--ros2-rust-ws requires a path}"
      shift 2
      ;;
    --jobs)
      BUILD_JOBS="${2:?--jobs requires a number}"
      shift 2
      ;;
    --debug)
      CARGO_PROFILE_ARG=""
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "${BUILD_JOBS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "--jobs must be a positive integer" >&2
  exit 2
fi

WORKSPACE="$(cd -- "${WORKSPACE}" && pwd)"
ROS_SETUP="/opt/ros/humble/setup.bash"
VI_DIR="${WORKSPACE}/src/value_iteration3"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS 2 Humble was not found: ${ROS_SETUP}" >&2
  exit 1
fi

if [[ ! -d "${VI_DIR}" ]]; then
  echo "value_iteration3 was not found: ${VI_DIR}" >&2
  echo "Run scripts/setup_native_base.sh first (vcs import)." >&2
  exit 1
fi

RUST_OVERLAY="${RUST_WORKSPACE}/install/local_setup.bash"
if [[ ! -f "${RUST_OVERLAY}" ]]; then
  echo "ros2_rust overlay was not found: ${RUST_OVERLAY}" >&2
  echo "Run scripts/setup_native_ros2_rust.sh first." >&2
  exit 1
fi

# rustupとcolcon拡張はログインシェルのPATHに無い場合があるため補う。
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
if ! command -v cargo-ament-build >/dev/null 2>&1; then
  echo "cargo-ament-build was not found in PATH" >&2
  echo "Run scripts/setup_native_ros2_rust.sh first." >&2
  exit 1
fi

echo "[1/2] Building vi_global_planner and vi_local_planner"
# shellcheck disable=SC1090
source "${ROS_SETUP}"
# shellcheck disable=SC1090
source "${RUST_OVERLAY}"
if [[ -f "${WORKSPACE}/install/local_setup.bash" ]]; then
  # shellcheck disable=SC1090
  source "${WORKSPACE}/install/local_setup.bash"
fi

cd "${WORKSPACE}"
COLCON_ARGS=(
  --symlink-install
  --parallel-workers "${BUILD_JOBS}"
  --packages-select vi_global_planner vi_local_planner
)
if [[ -n "${CARGO_PROFILE_ARG}" ]]; then
  COLCON_ARGS+=(--cargo-args "${CARGO_PROFILE_ARG}")
fi
colcon build "${COLCON_ARGS[@]}"

echo "[2/2] Verifying vi_global_planner launch files"
# cargo-ament-buildがpackage.xmlの<install>launch</install>を取りこぼす環境が
# あるため、navigation_launch.pyが無い場合だけ補完する。
LAUNCH_SRC="${VI_DIR}/vi_ros2/vi_global_planner/launch/navigation_launch.py"
LAUNCH_DST="${WORKSPACE}/install/vi_global_planner/share/vi_global_planner/launch/navigation_launch.py"
if [[ -e "${LAUNCH_DST}" ]]; then
  echo "Already installed: ${LAUNCH_DST}"
elif [[ -f "${LAUNCH_SRC}" ]]; then
  install -D -m 0644 "${LAUNCH_SRC}" "${LAUNCH_DST}"
  echo "Installed: ${LAUNCH_DST}"
else
  echo "Launch source was not found: ${LAUNCH_SRC}" >&2
fi

cat <<EOF

Value iteration planner setup completed.
Load the environment with:
  source /opt/ros/humble/setup.bash
  source ${RUST_WORKSPACE}/install/local_setup.bash
  source ${WORKSPACE}/install/setup.bash

Verify with:
  ros2 pkg prefix vi_global_planner
  ros2 pkg prefix vi_local_planner
EOF
