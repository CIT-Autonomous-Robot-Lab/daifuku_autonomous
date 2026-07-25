#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_WORKSPACE="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

WORKSPACE="${DEFAULT_WORKSPACE}"
RUST_WORKSPACE="${ROS2_RUST_WS:-${HOME}/ros2_rust_ws}"
BUILD_JOBS="${BUILD_JOBS:-2}"
SKIP_APT=0
WITH_LIVOX=1
WITH_VI=1

usage() {
  cat <<'EOF'
Ubuntu 22.04 / ROS 2 Humbleのネイティブ環境を一括で構築します。
各コンポーネントのsetup_native_*.shを順に呼び出します。

  1. setup_native_base.sh        共通依存 + autonomous_nav / emcl2
  2. setup_native_livox.sh       Livox SDK2 + livox_ros_driver2（Mid-360）
  3. setup_native_ros2_rust.sh   Rust toolchain + ros2_rust（rclrs）
  4. setup_native_vi.sh          vi_global_planner / vi_local_planner

Usage:
  bash scripts/setup_native.sh [options]

Options:
  --workspace PATH      ROS 2ワークスペース（既定: リポジトリルート）
  --ros2-rust-ws PATH   ros2_rustワークスペース（既定: ~/ros2_rust_ws）
  --jobs N              並列ビルド数（既定: 2、低メモリ時は1）
  --skip-apt            aptによる依存パッケージ導入を省略
  --no-livox            Livox関連の導入を省略（2D LiDARのみの構成）
  --no-vi               価値反復プランナの導入を省略（NavFnのみの構成）
  -h, --help            このヘルプを表示

個別に導入し直す場合は各setup_native_*.shを直接実行してください。
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
    --skip-apt)
      SKIP_APT=1
      shift
      ;;
    --no-livox)
      WITH_LIVOX=0
      shift
      ;;
    --no-vi)
      WITH_VI=0
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

COMMON_ARGS=(--workspace "${WORKSPACE}" --jobs "${BUILD_JOBS}")
APT_ARGS=()
if ((SKIP_APT == 1)); then
  APT_ARGS=(--skip-apt)
fi

echo "=== [1] base ==="
bash "${SCRIPT_DIR}/setup_native_base.sh" "${COMMON_ARGS[@]}" "${APT_ARGS[@]}"

if ((WITH_LIVOX == 1)); then
  echo "=== [2] livox ==="
  bash "${SCRIPT_DIR}/setup_native_livox.sh" "${COMMON_ARGS[@]}" "${APT_ARGS[@]}"
else
  echo "=== [2] livox (skipped) ==="
fi

if ((WITH_VI == 1)); then
  echo "=== [3] ros2_rust ==="
  bash "${SCRIPT_DIR}/setup_native_ros2_rust.sh" \
    --ros2-rust-ws "${RUST_WORKSPACE}" \
    --jobs "${BUILD_JOBS}" "${APT_ARGS[@]}"

  echo "=== [4] value iteration planner ==="
  bash "${SCRIPT_DIR}/setup_native_vi.sh" "${COMMON_ARGS[@]}" \
    --ros2-rust-ws "${RUST_WORKSPACE}"
else
  echo "=== [3-4] value iteration planner (skipped) ==="
fi

cat <<EOF

Native setup completed.
Load the environment with:
  source /opt/ros/humble/setup.bash
EOF
if ((WITH_VI == 1)); then
  cat <<EOF
  source ${RUST_WORKSPACE}/install/local_setup.bash
EOF
fi
cat <<EOF
  source ${WORKSPACE}/install/setup.bash

Continue with docs/setup/network.md and docs/setup/lidar.md.
EOF
