#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_WORKSPACE="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

WORKSPACE="${DEFAULT_WORKSPACE}"
BUILD_JOBS="${BUILD_JOBS:-2}"
LIVOX_SDK2_REF="${LIVOX_SDK2_REF:-v1.3.1}"
LIVOX_DRIVER_REF="${LIVOX_DRIVER_REF:-1.2.6}"
SKIP_APT=0

usage() {
  cat <<'EOF'
Raspberry Pi / Ubuntu 22.04へLivox SDK2とlivox_ros_driver2を導入します。

Usage:
  bash scripts/setup_livox_native.sh [options]

Options:
  --workspace PATH   ROS 2ワークスペース（既定: リポジトリルート）
  --jobs N          並列ビルド数（既定: 2、低メモリ時は1）
  --sdk-ref REF      Livox SDK2のタグ/コミット（既定: v1.3.1）
  --driver-ref REF   ROSドライバのタグ/コミット（既定: 1.2.6）
  --skip-apt         aptによる依存パッケージ導入を省略
  -h, --help         このヘルプを表示
EOF
}

while (($#)); do
  case "$1" in
    --workspace)
      WORKSPACE="${2:?--workspace requires a path}"
      shift 2
      ;;
    --jobs)
      BUILD_JOBS="${2:?--jobs requires a number}"
      shift 2
      ;;
    --sdk-ref)
      LIVOX_SDK2_REF="${2:?--sdk-ref requires a ref}"
      shift 2
      ;;
    --driver-ref)
      LIVOX_DRIVER_REF="${2:?--driver-ref requires a ref}"
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

WORKSPACE="$(cd -- "${WORKSPACE}" && pwd)"
ROS_SETUP="/opt/ros/humble/setup.bash"
DRIVER_DIR="${WORKSPACE}/src/livox_ros_driver2"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS 2 Humble was not found: ${ROS_SETUP}" >&2
  exit 1
fi

if ((SKIP_APT == 0)); then
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    libapr1-dev \
    libpcl-dev \
    libssl-dev \
    python3-colcon-common-extensions \
    python3-rosdep \
    ros-humble-ament-cmake-auto \
    ros-humble-pcl-conversions \
    ros-humble-rosbag2
fi

SDK_SOURCE="$(mktemp -d -t livox-sdk2.XXXXXX)"
cleanup() {
  case "${SDK_SOURCE}" in
    /tmp/livox-sdk2.*) rm -rf -- "${SDK_SOURCE}" ;;
    *) echo "Refusing to remove unexpected temporary path: ${SDK_SOURCE}" >&2 ;;
  esac
}
trap cleanup EXIT

echo "[1/4] Building Livox SDK2 ${LIVOX_SDK2_REF}"
git clone --depth 1 --branch "${LIVOX_SDK2_REF}" \
  https://github.com/Livox-SDK/Livox-SDK2.git "${SDK_SOURCE}"
cmake -S "${SDK_SOURCE}" -B "${SDK_SOURCE}/build" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "${SDK_SOURCE}/build" --parallel "${BUILD_JOBS}"
sudo cmake --install "${SDK_SOURCE}/build"
sudo ldconfig

echo "[2/4] Preparing livox_ros_driver2"
mkdir -p "${WORKSPACE}/src"
if [[ ! -d "${DRIVER_DIR}/.git" ]]; then
  if [[ -e "${DRIVER_DIR}" ]]; then
    echo "Existing non-Git path cannot be replaced: ${DRIVER_DIR}" >&2
    exit 1
  fi
  git clone --branch "${LIVOX_DRIVER_REF}" \
    https://github.com/Livox-SDK/livox_ros_driver2.git "${DRIVER_DIR}"
else
  echo "Using existing checkout: ${DRIVER_DIR}"
fi

cp "${DRIVER_DIR}/package_ROS2.xml" "${DRIVER_DIR}/package.xml"
mkdir -p "${DRIVER_DIR}/launch"
cp -a "${DRIVER_DIR}/launch_ROS2/." "${DRIVER_DIR}/launch/"

echo "[3/4] Installing ROS dependencies"
# shellcheck disable=SC1090
source "${ROS_SETUP}"
if [[ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]]; then
  sudo rosdep init
fi
rosdep update --rosdistro=humble
rosdep install --from-paths "${DRIVER_DIR}" --ignore-src \
  --rosdistro=humble -r -y

echo "[4/4] Building livox_ros_driver2"
cd "${WORKSPACE}"
colcon build --symlink-install \
  --parallel-workers "${BUILD_JOBS}" \
  --packages-select livox_ros_driver2 \
  --cmake-args -DCMAKE_BUILD_TYPE=Release \
               -DROS_EDITION=ROS2 \
               -DDISTRO_ROS=humble

cat <<EOF

Livox setup completed.
Load the workspace with:
  source ${WORKSPACE}/install/setup.bash

Verify with:
  ros2 pkg prefix livox_ros_driver2
EOF
