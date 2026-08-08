#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_WORKSPACE="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

WORKSPACE="${DEFAULT_WORKSPACE}"
BUILD_JOBS="${BUILD_JOBS:-2}"
SKIP_APT=0
SKIP_IMPORT=0
SKIP_BUILD=0

usage() {
  cat <<'EOF'
Ubuntu 22.04 / ROS 2 HumbleへNav2などの共通依存を導入し、daifuku_stackと
raspicat_driver、emcl2をビルドします。他のsetup_native_*.shより先に実行してください。

Usage:
  bash tools/setup/setup_native_base.sh [options]

Options:
  --workspace PATH   ROS 2ワークスペース（既定: リポジトリルート）
  --jobs N           並列ビルド数（既定: 2、低メモリ時は1）
  --skip-apt         aptによる依存パッケージ導入を省略
  --skip-import      vcs importによる外部リポジトリ取得を省略
  --skip-build       colconビルドを省略（依存導入のみ）
  -h, --help         このヘルプを表示

Livoxドライバはtools/setup/setup_native_livox.sh、価値反復プランナは
tools/setup/setup_native_ros2_rust.shとtools/setup/setup_native_vi.shで導入します。
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
    --skip-apt)
      SKIP_APT=1
      shift
      ;;
    --skip-import)
      SKIP_IMPORT=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
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
REPOS_FILE="${WORKSPACE}/autonomous_bot.repos"

if [[ ! -f "${ROS_SETUP}" ]]; then
  echo "ROS 2 Humble was not found: ${ROS_SETUP}" >&2
  exit 1
fi

if ((SKIP_APT == 0)); then
  echo "[1/4] Installing apt dependencies"
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    python3-colcon-common-extensions \
    python3-rosdep \
    python3-vcstool \
    ros-humble-diagnostic-updater \
    ros-humble-laser-filters \
    ros-humble-nav2-bringup \
    ros-humble-joy \
    ros-humble-navigation2 \
    ros-humble-pcl-conversions \
    ros-humble-pointcloud-to-laserscan \
    ros-humble-robot-localization \
    ros-humble-rosbag2 \
    ros-humble-rviz2 \
    ros-humble-slam-toolbox \
    ros-humble-teleop-twist-joy \
    ros-humble-teleop-twist-keyboard \
    ros-humble-topic-tools \
    ros-humble-twist-mux
else
  echo "[1/4] Skipping apt dependencies"
fi

if ((SKIP_IMPORT == 0)); then
  echo "[2/4] Importing external repositories"
  if [[ ! -f "${REPOS_FILE}" ]]; then
    echo "Repos file was not found: ${REPOS_FILE}" >&2
    exit 1
  fi
  mkdir -p "${WORKSPACE}/src"
  (cd "${WORKSPACE}" && vcs import . < "${REPOS_FILE}")
else
  echo "[2/4] Skipping external repositories"
fi

echo "[3/4] Installing ROS dependencies"
# shellcheck disable=SC1090
source "${ROS_SETUP}"
if [[ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]]; then
  sudo rosdep init
fi
rosdep update --rosdistro=humble
# rclrs系の依存はrosdepが解決できないため、-rで残りの導入を継続する。
rosdep install --from-paths "${WORKSPACE}/src" --ignore-src \
  --rosdistro=humble -r -y

if ((SKIP_BUILD == 1)); then
  echo "[4/4] Skipping colcon build"
  exit 0
fi

echo "[4/4] Building daifuku_stack, daifuku_rqt, daifuku_waypoint_manager, raspicat_driver and emcl2"
cd "${WORKSPACE}"
# daifuku_rqt と daifuku_waypoint_manager はここでは建てる。ネイティブ環境は RViz と
# rqt を動かす PC 側の構成であり、rqt と RViz が無いのは docker/raspberrypi/ の
# イメージだけ。raspimouse_msgs は raspicat_driver が /leds と /switches で使うので
# 要る (公式実装と同じ型に揃えてある)。
colcon build --symlink-install \
  --parallel-workers "${BUILD_JOBS}" \
  --packages-select daifuku_bringup daifuku_config daifuku_config_manager daifuku_stack \
                    daifuku_rqt daifuku_waypoint_manager emcl2 \
                    raspicat_driver raspimouse_msgs \
  --cmake-args -DCMAKE_BUILD_TYPE=Release

cat <<EOF

Base setup completed.
Load the workspace with:
  source ${WORKSPACE}/install/setup.bash

Verify with:
  ros2 pkg prefix daifuku_stack
  ros2 pkg prefix emcl2

Next steps:
  Mid-360を使う場合   : bash tools/setup/setup_native_livox.sh
  価値反復プランナ    : bash tools/setup/setup_native_ros2_rust.sh
                        bash tools/setup/setup_native_vi.sh
EOF
