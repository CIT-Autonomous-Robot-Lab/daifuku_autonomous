#!/usr/bin/env bash
set -Eeuo pipefail

RUST_WORKSPACE="${ROS2_RUST_WS:-${HOME}/ros2_rust_ws}"
BUILD_JOBS="${BUILD_JOBS:-2}"
# Dockerfileと同じrclrsのコミットに固定する。
ROS2_RUST_REF="${ROS2_RUST_REF:-2c6b92671a65a426aec9c3468c069a551329ca1b}"
SKIP_APT=0

usage() {
  cat <<'EOF'
価値反復プランナ（vi_global_planner / vi_local_planner）が必要とする
Rust toolchainとros2_rust（rclrs）ワークスペースを構築します。

Usage:
  bash scripts/setup_native_ros2_rust.sh [options]

Options:
  --ros2-rust-ws PATH   ros2_rustワークスペース（既定: ~/ros2_rust_ws）
  --jobs N              並列ビルド数（既定: 2、低メモリ時は1）
  --ros2-rust-ref REF   ros2_rustのコミット（既定: 2c6b926）
  --skip-apt            aptによる依存パッケージ導入を省略
  -h, --help            このヘルプを表示

ビルド後はscripts/setup_native_vi.shで価値反復パッケージをビルドします。
EOF
}

while (($#)); do
  case "$1" in
    --ros2-rust-ws)
      RUST_WORKSPACE="${2:?--ros2-rust-ws requires a path}"
      shift 2
      ;;
    --jobs)
      BUILD_JOBS="${2:?--jobs requires a number}"
      shift 2
      ;;
    --ros2-rust-ref)
      ROS2_RUST_REF="${2:?--ros2-rust-ref requires a ref}"
      shift 2
      ;;
    --skip-apt)
      SKIP_APT=1
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

ROS_SETUP="/opt/ros/humble/setup.bash"
ROS2_RUST_DIR="${RUST_WORKSPACE}/src/ros2_rust"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS 2 Humble was not found: ${ROS_SETUP}" >&2
  exit 1
fi

if ((SKIP_APT == 0)); then
  echo "[1/5] Installing apt dependencies"
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    git \
    libclang-dev \
    pkg-config \
    python3-colcon-common-extensions \
    python3-pip \
    python3-vcstool
else
  echo "[1/5] Skipping apt dependencies"
fi

echo "[2/5] Installing Rust toolchain and colcon extensions"
# colcon拡張はユーザーサイトへ導入し、システムのpython環境を汚さない。
pip3 install --user --no-cache-dir colcon-cargo colcon-ros-cargo
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
if ! command -v rustup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --no-modify-path
fi
if [[ ! -x "${HOME}/.cargo/bin/cargo" ]]; then
  echo "cargo was not found under ${HOME}/.cargo/bin" >&2
  exit 1
fi
cargo install --locked cargo-ament-build

echo "[3/5] Preparing ros2_rust workspace at ${RUST_WORKSPACE}"
mkdir -p "${RUST_WORKSPACE}/src"
RUST_WORKSPACE="$(cd -- "${RUST_WORKSPACE}" && pwd)"
ROS2_RUST_DIR="${RUST_WORKSPACE}/src/ros2_rust"
if [[ ! -d "${ROS2_RUST_DIR}/.git" ]]; then
  if [[ -e "${ROS2_RUST_DIR}" ]]; then
    echo "Existing non-Git path cannot be replaced: ${ROS2_RUST_DIR}" >&2
    exit 1
  fi
  git clone https://github.com/ros2-rust/ros2_rust.git "${ROS2_RUST_DIR}"
else
  echo "Using existing checkout: ${ROS2_RUST_DIR}"
  git -C "${ROS2_RUST_DIR}" fetch --all --tags
fi
git -C "${ROS2_RUST_DIR}" checkout "${ROS2_RUST_REF}"

(cd "${RUST_WORKSPACE}/src" \
  && vcs import < "${ROS2_RUST_DIR}/ros2_rust_humble.repos")
# rosインターフェース一式は/opt/ros/humbleの物を使うため、ソースからは外す。
rm -rf -- "${RUST_WORKSPACE}/src/ros2"

echo "[4/5] Building rclrs"
# shellcheck disable=SC1090
source "${ROS_SETUP}"
cd "${RUST_WORKSPACE}"
colcon build --merge-install \
  --parallel-workers "${BUILD_JOBS}" \
  --packages-up-to rclrs

echo "[5/5] Checking nav2_msgs Rust bindings"
if [[ ! -d /opt/ros/humble/share/nav2_msgs/rust ]]; then
  echo "nav2_msgs Rust bindings are missing; building from source"
  if [[ ! -d "${RUST_WORKSPACE}/src/nav2_msgs" ]]; then
    NAV2_SOURCE="$(mktemp -d -t navigation2.XXXXXX)"
    cleanup() {
      case "${NAV2_SOURCE}" in
        /tmp/navigation2.*) rm -rf -- "${NAV2_SOURCE}" ;;
        *) echo "Refusing to remove unexpected temporary path: ${NAV2_SOURCE}" >&2 ;;
      esac
    }
    trap cleanup EXIT
    git clone --depth 1 --branch humble \
      https://github.com/ros-navigation/navigation2.git "${NAV2_SOURCE}"
    cp -a "${NAV2_SOURCE}/nav2_msgs" "${RUST_WORKSPACE}/src/nav2_msgs"
  fi
  # shellcheck disable=SC1091
  source "${RUST_WORKSPACE}/install/local_setup.bash"
  colcon build --merge-install \
    --parallel-workers "${BUILD_JOBS}" \
    --packages-select nav2_msgs
else
  echo "nav2_msgs Rust bindings are already installed"
fi

cat <<EOF

ros2_rust setup completed.
Load the overlay with:
  source ${RUST_WORKSPACE}/install/local_setup.bash

Rust toolchainはこのスクリプトの実行中だけPATHへ追加しています。
恒久的に使うには次を~/.bashrcへ追記してください。
  export PATH="\${HOME}/.local/bin:\${HOME}/.cargo/bin:\${PATH}"

colcon拡張はpip3 --userで導入しています。colconが起動しなくなった場合は
apt版のcolcon-coreがユーザーサイトの物に隠れています。次で戻せます。
  pip3 uninstall colcon-core

Next step:
  bash scripts/setup_native_vi.sh --ros2-rust-ws ${RUST_WORKSPACE}
EOF
