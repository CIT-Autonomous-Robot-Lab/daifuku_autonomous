ARG ROS_IMAGE=ros:humble-ros-base-jammy

# Build on the target architecture.  The official ros-base image supports both
# amd64 and arm64, unlike osrf/ros:humble-desktop-full (amd64 only).
FROM ${ROS_IMAGE} AS builder

ARG BUILD_JOBS=2
ARG LIVOX_SDK2_REF=v1.3.1
ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=humble \
    ROS_WS=/opt/ros_ws

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
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
      ros-humble-topic-tools \
    && rm -rf /var/lib/apt/lists/*

# Livox SDK2 is required by livox_ros_driver2.  Limit parallelism because a
# Raspberry Pi 4 commonly has only 4 GB of RAM.
RUN git clone --depth 1 --branch "${LIVOX_SDK2_REF}" \
      https://github.com/Livox-SDK/Livox-SDK2.git /tmp/Livox-SDK2 \
    && cmake -S /tmp/Livox-SDK2 -B /tmp/Livox-SDK2/build \
         -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/Livox-SDK2/build --parallel "${BUILD_JOBS}" \
    && cmake --install /tmp/Livox-SDK2/build \
    && rm -rf /tmp/Livox-SDK2

# vi_planner and vi_global_planner use rclrs.  Rust and all build tools
# stay in the builder stage; only their installed runtime artifacts are copied.
RUN pip3 install --no-cache-dir colcon-cargo colcon-ros-cargo \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
       | sh -s -- -y --profile minimal \
    && /root/.cargo/bin/cargo install --locked cargo-ament-build
ENV PATH=/root/.cargo/bin:${PATH}

WORKDIR /opt/ros2_rust_ws/src
RUN git clone https://github.com/ros2-rust/ros2_rust.git \
    && git -C ros2_rust checkout 2c6b92671a65a426aec9c3468c069a551329ca1b \
    && vcs import < ros2_rust/ros2_rust_humble.repos \
    && rm -rf ros2

WORKDIR /opt/ros2_rust_ws
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && colcon build --merge-install \
         --parallel-workers "${BUILD_JOBS}" \
         --packages-up-to rclrs

# Some Humble installations do not ship nav2_msgs Rust bindings.  Build the
# source fallback only on those installations.
RUN if [[ ! -d "/opt/ros/${ROS_DISTRO}/share/nav2_msgs/rust" ]]; then \
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
    fi

# Rust一式は上の段で /root 配下に入るが、compose は user: "1000:1000" で回すので
# /root (0700) の中は読めない。ツールチェーンだけ /opt へ移して誰でも読める場所に
# 置き直す。ここより上の命令は触らないこと (buildah はRUNのキャッシュキーに
# build-arg を含めるため、上を1行でも動かすと apt/Livox/ros2_rust を焼き直す)。
RUN mv /root/.cargo /opt/cargo \
    && mv /root/.rustup /opt/rustup \
    && chmod -R a+rX /opt/rustup /opt/cargo \
    && chown -R 1000:1000 /opt/cargo
ENV CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    PATH=/opt/cargo/bin:${PATH}

# ここから下のソースツリーは **rosdep に apt 依存を解決させるためだけ** に置く。
# ワークスペースのビルドは compose の up 時にコンテナ内で行うので
# (docker/raspberrypi/scripts/build-workspace.sh)、解決が済んだら src/ ごと捨てる。
#
# 狙いは「aptパッケージが変わったときだけイメージを焼き直す」こと。rosdep を
# 起動時に回すとネットワークが要るうえ apt の状態が毎回変わるので、ここに残す。
WORKDIR ${ROS_WS}
COPY autonomous_bot.repos /tmp/autonomous_bot.repos
RUN mkdir -p src \
    && vcs import . < /tmp/autonomous_bot.repos

COPY src/autonomous_nav src/autonomous_nav

RUN cp src/livox_ros_driver2/package_ROS2.xml \
       src/livox_ros_driver2/package.xml \
    && rm -rf src/livox_ros_driver2/launch \
    && cp -a src/livox_ros_driver2/launch_ROS2 \
       src/livox_ros_driver2/launch

RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && rosdep update --rosdistro=${ROS_DISTRO} \
    && apt-get update \
    && rosdep install --from-paths src --ignore-src -r -y \
         --rosdistro=${ROS_DISTRO} \
    && rm -rf /var/lib/apt/lists/*

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
RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-humble-grid-map-msgs \
      ros-humble-joint-state-publisher \
      ros-humble-robot-state-publisher \
      ros-humble-teleop-twist-joy \
      ros-humble-teleop-twist-keyboard \
      ros-humble-urdf \
      ros-humble-urg-node \
      ros-humble-xacro \
    && rm -rf /var/lib/apt/lists/*

# urg_node は lidar:=2d のときだけ使う。本機の既定は MID360 なので通常は動かない
# が、2D LiDAR に差し替えたときにイメージを焼き直さず起動できるよう入れてある
# (lidar_bringup.launch.py が raspicat_bringup の URG パラメータで立てる)。
#
# raspicat_bringup / raspicat_description が package.xml で宣言している依存のうち、
# 次のものは意図的に入れていない。rosdep はビルドのために入れるが、実行には要らない。
#   * rviz2 / joint_state_publisher_gui
#                — raspicat_description の表示用 launch 専用。この機はheadless。
#   * joy_linux  — ジョイスティック teleop 用 (teleop_twist_joy は入っている)。
#                  使うならここに足すこと。
ENV ROS_LOCALHOST_ONLY=0 \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp

RUN ldconfig

# rosdep に読ませたあとの src/ は捨てる。実体は compose が ../../src を
# ここへマウントする。ros2_rust の build/ も同様に成果物 (install/) だけ残す。
RUN rm -rf ${ROS_WS}/src ${ROS_WS}/build ${ROS_WS}/log \
           /tmp/autonomous_bot.repos \
           /opt/ros2_rust_ws/build /opt/ros2_rust_ws/log \
           /opt/rustup/toolchains/*/share/doc

# 名前付きボリュームは「イメージ側の同じパスの中身と所有者」で初期化される。
# compose は user: "1000:1000" で回すので、ここで作って chown しておかないと
# 空のボリュームが root 所有で作られ、colcon が build/install へ書けない。
RUN mkdir -p ${ROS_WS}/src ${ROS_WS}/build ${ROS_WS}/install ${ROS_WS}/log \
    && chown -R 1000:1000 ${ROS_WS}

COPY docker/common/entrypoint.sh /ros_entrypoint.sh
COPY docker/raspberrypi/scripts/build-workspace.sh /usr/local/bin/build-workspace
RUN chmod +x /ros_entrypoint.sh /usr/local/bin/build-workspace

WORKDIR ${ROS_WS}
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
