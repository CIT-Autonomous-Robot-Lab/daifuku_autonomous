FROM osrf/ros:humble-desktop-full

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=humble \
    ROS_WS=/opt/ros_ws

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        build-essential \
        pkg-config \
        cmake \
        libclang-dev \
        libssl-dev \
        python3-pip \
        python3-colcon-common-extensions \
        python3-rosdep \
        python3-vcstool \
        ros-humble-navigation2 \
        ros-humble-nav2-bringup \
        ros-humble-diagnostic-updater \
        ros-humble-laser-filters \
        ros-humble-pointcloud-to-laserscan \
        ros-humble-robot-localization \
        ros-humble-slam-toolbox \
        ros-humble-topic-tools \
    && rm -rf /var/lib/apt/lists/*

# Mid-360 driver dependency. Keep SDK and ROS driver versions fixed so Docker
# rebuilds do not silently change the sensor protocol implementation.
ARG LIVOX_SDK2_REF=v1.3.1
RUN git clone --depth 1 --branch "${LIVOX_SDK2_REF}" \
      https://github.com/Livox-SDK/Livox-SDK2.git /tmp/Livox-SDK2 && \
    cmake -S /tmp/Livox-SDK2 -B /tmp/Livox-SDK2/build \
      -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /tmp/Livox-SDK2/build --parallel && \
    cmake --install /tmp/Livox-SDK2/build && \
    ldconfig && \
    rm -rf /tmp/Livox-SDK2

# ── vi_global_planner (value_iteration3, rclrs 製 Rust ノード) のビルド環境 ──────────
# value_iteration3/vi_ros2/docker/Dockerfile と同じ構成:
#   Rust toolchain + colcon-cargo/colcon-ros-cargo + cargo-ament-build、
#   rclrs (ros2_rust @ 2c6b926) と nav2_msgs の Rust バインディングを
#   /opt/ros2_rust_ws にビルドしておく。
RUN pip3 install --no-cache-dir colcon-cargo colcon-ros-cargo

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH=/root/.cargo/bin:$PATH

RUN cargo install --locked cargo-ament-build

WORKDIR /opt/ros2_rust_ws/src
# rclrs は vi_global_planner/vi_node が書かれている API のコミットに固定する。
# ベースイメージが共通インターフェースパッケージの Rust バインディング
# (share/<pkg>/rust) を同梱しているため、重複ビルドになる ros2 ディレクトリは
# 削除する (詳細は value_iteration3/vi_ros2/docker/Dockerfile のコメント参照)。
RUN git clone https://github.com/ros2-rust/ros2_rust.git && \
    git -C ros2_rust checkout 2c6b92671a65a426aec9c3468c069a551329ca1b && \
    vcs import < ros2_rust/ros2_rust_humble.repos && \
    rm -rf ros2

WORKDIR /opt/ros2_rust_ws
RUN . /opt/ros/${ROS_DISTRO}/setup.sh && \
    colcon build --merge-install --packages-up-to rclrs

# Older Humble binaries did not include nav2_msgs Rust bindings.  Current ones
# do, so only build the source fallback when the installed package lacks them;
# exposing both copies makes ros-env generate the nav2_msgs module twice.
RUN if [ ! -d "/opt/ros/${ROS_DISTRO}/share/nav2_msgs/rust" ]; then \
      git clone --depth 1 --branch humble \
        https://github.com/ros-navigation/navigation2.git /tmp/navigation2 && \
      cp -a /tmp/navigation2/nav2_msgs /opt/ros2_rust_ws/src/nav2_msgs && \
      rm -rf /tmp/navigation2; \
    fi
RUN if [ -d /opt/ros2_rust_ws/src/nav2_msgs ]; then \
      . /opt/ros/${ROS_DISTRO}/setup.sh && \
      . /opt/ros2_rust_ws/install/local_setup.sh && \
      colcon build --merge-install --packages-select nav2_msgs; \
    fi

# ── メインワークスペース ──────────────────────────────────────────────────────
WORKDIR ${ROS_WS}

COPY autonomous_bot.repos /tmp/autonomous_bot.repos
RUN mkdir -p src \
    && vcs import . < /tmp/autonomous_bot.repos

# livox_ros_driver2 keeps ROS 1/ROS 2 manifests side-by-side. Prepare its ROS 2
# package without invoking upstream build.sh, which deletes workspace outputs.
RUN cp src/livox_ros_driver2/package_ROS2.xml \
       src/livox_ros_driver2/package.xml && \
    cp -a src/livox_ros_driver2/launch_ROS2 \
       src/livox_ros_driver2/launch

COPY src/autonomous_nav src/autonomous_nav

# rosdep は rclrs 系のキーを解決できないため -r で続行する。
# colcon は使用パッケージのみ選択ビルドする (value_iteration3 の vi_node /
# vi_interfaces は Vi.action 用ノードでナビゲーションには不要)。
# vi_local_planner は local_planner:=vi 用の controller_server 置き換えノード。
RUN . /opt/ros/${ROS_DISTRO}/setup.sh \
    && rosdep update --rosdistro=${ROS_DISTRO} \
    && apt-get update \
    && rosdep install --from-paths src --ignore-src -r -y --rosdistro=${ROS_DISTRO} \
    && rm -rf /var/lib/apt/lists/*
RUN . /opt/ros/${ROS_DISTRO}/setup.sh \
    && . /opt/ros2_rust_ws/install/local_setup.sh \
    && colcon build --symlink-install \
         --packages-select autonomous_nav emcl2 vi_global_planner vi_local_planner \
         livox_ros_driver2 \
         --cmake-args -DROS_EDITION=ROS2 -DDISTRO_ROS=${ROS_DISTRO} \
         --cargo-args --release

COPY docker/entrypoint.sh /ros_entrypoint.sh
RUN chmod +x /ros_entrypoint.sh

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
