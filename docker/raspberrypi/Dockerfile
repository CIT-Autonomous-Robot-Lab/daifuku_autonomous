ARG ROS_IMAGE=ros:humble-ros-base-jammy

# Build on the target architecture.  The official ros-base image supports both
# amd64 and arm64, unlike osrf/ros:humble-desktop-full (amd64 only).
FROM ${ROS_IMAGE} AS builder

ARG LIVOX_SDK2_REF=v1.3.1
# ビルドの並列数。**compose は環境変数の BUILD_JOBS をここへ渡さない** (`args:`
# に無い)。渡すと OOM 避けの `BUILD_JOBS=1 docker compose up -d` — つまり up 時の
# colcon だけを絞りたいとき — がこの下の層を全部作り直し、いちばん困るときに
# 1〜2 時間の焼き直しが始まるため。イメージ側を絞るのは明示のときだけ:
#
#   docker compose build --build-arg BUILD_JOBS=1
ARG BUILD_JOBS=4
ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=humble \
    ROS_WS=/opt/ros_ws

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# **apt のキャッシュは捨てずに BuildKit のキャッシュマウントへ置く。** マウントの
# 中身は層に入らないので、イメージは太らずに次回の apt が deb を取り直さない。
# `docker-clean` はイメージが持ち込む dpkg フックで、インストールのたびに
# /var/cache/apt/archives を消す。**これを外さないとマウントが黙って空になる。**
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' \
       > /etc/apt/apt.conf.d/keep-cache

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      cmake \
      curl \
      git \
      libapr1-dev \
      libclang-dev \
      libpcl-dev \
      libssl-dev \
      pkg-config \
      python3-colcon-common-extensions \
      python3-pip \
      python3-rosdep \
      python3-vcstool \
      ros-humble-ament-cmake-auto \
      ros-humble-diagnostic-updater \
      ros-humble-laser-filters \
      ros-humble-nav2-bringup \
      ros-humble-navigation2 \
      ros-humble-pcl-conversions \
      ros-humble-pointcloud-to-laserscan \
      ros-humble-robot-localization \
      ros-humble-rosbag2 \
      ros-humble-slam-toolbox \
      ros-humble-topic-tools

# Livox SDK2 is required by livox_ros_driver2.
RUN git clone --depth 1 --branch "${LIVOX_SDK2_REF}" \
      https://github.com/Livox-SDK/Livox-SDK2.git /tmp/Livox-SDK2 \
    && cmake -S /tmp/Livox-SDK2 -B /tmp/Livox-SDK2/build \
         -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/Livox-SDK2/build --parallel "${BUILD_JOBS}" \
    && cmake --install /tmp/Livox-SDK2/build \
    && rm -rf /tmp/Livox-SDK2

# vi_planner と vi_global_planner は rclrs を使う。compose は user: "1000:1000"
# で回すので、/root (0700) ではなく誰でも読める /opt にツールチェーンを置く。
# クレートのレジストリは層には残さない (キャッシュマウントなので、イメージには
# 入らないまま次回のビルドからは再利用される)。ワークスペースのビルド時は
# build-workspace.sh が CARGO_HOME をビルド用ボリュームへ向けるので無関係。
ENV CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    PATH=/opt/cargo/bin:${PATH}
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/opt/cargo/registry \
    pip3 install colcon-cargo colcon-ros-cargo \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
       | sh -s -- -y --profile minimal --no-modify-path \
    && cargo install --locked cargo-ament-build \
    && rm -rf /opt/rustup/toolchains/*/share/doc \
    && chmod -R a+rX /opt/cargo/bin /opt/rustup

WORKDIR /opt/ros2_rust_ws/src
RUN git clone https://github.com/ros2-rust/ros2_rust.git \
    && git -C ros2_rust checkout 2c6b92671a65a426aec9c3468c069a551329ca1b \
    && vcs import < ros2_rust/ros2_rust_humble.repos \
    && rm -rf ros2

WORKDIR /opt/ros2_rust_ws
# 中間生成物は同じ層の中で消す。層を分けて消しても、実体は前の層に残ったまま
# イメージのサイズに乗る。
RUN --mount=type=cache,target=/opt/cargo/registry \
    source /opt/ros/${ROS_DISTRO}/setup.bash \
    && colcon build --merge-install \
         --parallel-workers "${BUILD_JOBS}" \
         --packages-up-to rclrs \
    && rm -rf build log

# Some Humble installations do not ship nav2_msgs Rust bindings.  Build the
# source fallback only on those installations.
RUN --mount=type=cache,target=/opt/cargo/registry \
    if [[ ! -d "/opt/ros/${ROS_DISTRO}/share/nav2_msgs/rust" ]]; then \
      git clone --depth 1 --branch humble \
        https://github.com/ros-navigation/navigation2.git /tmp/navigation2; \
      cp -a /tmp/navigation2/nav2_msgs /opt/ros2_rust_ws/src/nav2_msgs; \
      rm -rf /tmp/navigation2; \
    fi \
    && if [[ -d /opt/ros2_rust_ws/src/nav2_msgs ]]; then \
      source /opt/ros/${ROS_DISTRO}/setup.bash; \
      source /opt/ros2_rust_ws/install/local_setup.bash; \
      colcon build --merge-install \
        --parallel-workers "${BUILD_JOBS}" \
        --packages-select nav2_msgs; \
    fi \
    && rm -rf /opt/ros2_rust_ws/build /opt/ros2_rust_ws/log

# 外部パッケージのソースは **rosdep に apt 依存を解決させるためだけ** に取る。
# ワークスペースのビルドは compose の up 時にコンテナ内で行うので
# (docker/raspberrypi/scripts/build-workspace.sh)、解決が済んだら捨てる。
#
# 狙いは「apt パッケージが変わったときだけイメージを焼き直す」こと。rosdep を
# 起動時に回すとネットワークが要るうえ apt の状態が毎回変わるので、ここに残す。
#
# **COPY ではなくバインドマウントで渡す。** マウントは層を作らないので中身は
# イメージに残らず (だからここで rm する必要も無い)、それでいて内容ハッシュは
# キャッシュキーに入る。**rosdep が読むのは package.xml 1 つだけ**なので、
# daifuku_stack の地図や config を直してもこの層から下は作り直されない
# (以前はツリーごと COPY していて、yaml を 1 行直すたびに rosdep と apt が
# やり直しになっていた)。
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=bind,source=autonomous_bot.repos,target=/tmp/repos.yaml \
    --mount=type=bind,source=src/daifuku_stack/package.xml,target=/tmp/stack/daifuku_stack/package.xml \
    source /opt/ros/${ROS_DISTRO}/setup.bash \
    && mkdir -p /tmp/vcs && cd /tmp/vcs \
    && vcs import . < /tmp/repos.yaml \
    && cp src/livox_ros_driver2/package_ROS2.xml \
          src/livox_ros_driver2/package.xml \
    && rosdep update --rosdistro=${ROS_DISTRO} \
    && apt-get update \
    && rosdep install --from-paths /tmp/vcs/src /tmp/stack --ignore-src -r -y \
         --rosdistro=${ROS_DISTRO} \
    && cd / \
    && rm -rf /tmp/vcs

# arm64 の CMake が、生成された相対探索で ROS の共有ライブラリを見失うこと
# (QEMU 下で顕著) への当て木。エクスポートの探索先を、実在を確認した ROS の
# lib ディレクトリに固定する。ワークスペースのビルドは up 時にこのイメージの
# 中で走るので、この修正は実行イメージにもそのまま残る。
RUN if [[ "$(dpkg --print-architecture)" == "arm64" ]]; then \
      test -f /opt/ros/${ROS_DISTRO}/lib/librcutils.so; \
      find /opt/ros/${ROS_DISTRO}/share -type f \
        -name '*.cmake' -exec \
        sed -i \
          's#set(_lib "NOTFOUND")#set(_lib "${CMAKE_CURRENT_LIST_DIR}/../../../lib/lib${_library}.so")#' \
          '{}' +; \
    fi

# 実行時にだけ要るノード。rosdep が見るのはビルド依存なので、launch から起動する
# だけのものはここで明示的に入れる。
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      ros-humble-grid-map-msgs \
      ros-humble-joint-state-publisher \
      ros-humble-joy \
      ros-humble-robot-state-publisher \
      ros-humble-teleop-twist-joy \
      ros-humble-teleop-twist-keyboard \
      ros-humble-twist-mux \
      ros-humble-urdf \
      ros-humble-urg-node \
      ros-humble-xacro

# urg_node は lidar:=2d のときだけ使う。本機の既定は MID360 なので通常は動かない
# が、2D LiDAR に差し替えたときにイメージを焼き直さず起動できるよう入れてある
# (lidar_bringup.launch.py が raspicat_bringup の URG パラメータで立てる)。
#
# raspicat_bringup / raspicat_description が package.xml で宣言している依存のうち、
# 次のものは意図的に入れていない。rosdep はビルドのために入れるが、実行には要らない。
#   * rviz2 / joint_state_publisher_gui
#                — raspicat_description の表示用 launch 専用。この機はheadless。
#   * joy_linux  — /dev/input/js* を読む旧実装。本機が使うのは SDL 版の joy
#                  (joy_node) のほうで、そちらは上の一覧に入れてある
#                  (robot_bringup.launch.py の joy:=true が立てる)。
#                  teleop_twist_joy の依存として既に入っている可能性が高いので、
#                  焼き直す前に `ros2 pkg executables joy` で確かめてよい。
ENV ROS_LOCALHOST_ONLY=0 \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp

# ワークスペースは空で用意する。src/ は compose が ../../src をここへマウントし、
# build/install/log は名前付きボリュームが受け持つ。
#
# 名前付きボリュームは「イメージ側の同じパスの中身と所有者」で初期化される。
# compose は user: "1000:1000" で回すので、ここで作って chown しておかないと
# 空のボリュームが root 所有で作られ、colcon が build/install へ書けない。
RUN ldconfig \
    && mkdir -p ${ROS_WS}/src ${ROS_WS}/build ${ROS_WS}/install ${ROS_WS}/log \
    && chown -R 1000:1000 ${ROS_WS}

COPY docker/common/entrypoint.sh /ros_entrypoint.sh
COPY docker/raspberrypi/scripts/build-workspace.sh /usr/local/bin/build-workspace
RUN chmod +x /ros_entrypoint.sh /usr/local/bin/build-workspace

WORKDIR ${ROS_WS}
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
