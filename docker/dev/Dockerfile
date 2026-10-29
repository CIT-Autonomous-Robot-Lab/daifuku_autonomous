FROM osrf/ros:humble-desktop-full

ARG RASPICAT2_REF=500a20c16987176efe8e9c653934ea25182020b9
ARG LIVOX_SDK2_REF=v1.3.1

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=humble \
    RASPICAT2_WS=/opt/raspicat2

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
      arp-scan \
      bash-completion \
      build-essential \
      ca-certificates \
      cmake \
      curl \
      gdb \
      gdbserver \
      git \
      iproute2 \
      iputils-ping \
      joystick \
      libapr1-dev \
      libclang-dev \
      libpcl-dev \
      libssl-dev \
      nano \
      openssh-client \
      pkg-config \
      python3-colcon-common-extensions \
      python3-pip \
      python3-rosdep \
      python3-vcstool \
      ros-humble-ament-cmake-auto \
      ros-humble-diagnostic-updater \
      ros-humble-grid-map-msgs \
      ros-humble-laser-filters \
      ros-humble-nav2-bringup \
      ros-humble-pcl-conversions \
      ros-humble-pointcloud-to-laserscan \
      ros-humble-realsense2-camera \
      ros-humble-robot-localization \
      ros-humble-rosbag2 \
      ros-humble-slam-toolbox \
      ros-humble-topic-tools \
      ros-humble-twist-mux \
      sudo \
      tmux \
      xterm \
    && rm -rf /var/lib/apt/lists/*

# Standalone VI uses the same ROS 2 Rust underlay as the robot image.
ENV CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    PATH=/opt/cargo/bin:${PATH}
RUN pip3 install colcon-cargo colcon-ros-cargo \
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
# 中間生成物は同じ層の中で消す (層を分けて消しても実体は前の層に残る)。
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && colcon build --merge-install \
         --parallel-workers 2 \
         --packages-up-to rclrs \
    && rm -rf build log

# vi_planner が使うメッセージの Rust バインディングは Humble に無いことがある。
# 無いものだけソースから建てる (tf2_msgs は 2026-08-09 に publish_tf が入って加わった)。
RUN pkgs=""; \
    for spec in "nav2_msgs https://github.com/ros-navigation/navigation2.git" \
                "tf2_msgs https://github.com/ros2/geometry2.git"; do \
      set -- ${spec}; \
      if [[ ! -d "/opt/ros/${ROS_DISTRO}/share/$1/rust" ]]; then \
        git clone --depth 1 --branch "${ROS_DISTRO}" "$2" "/tmp/$1_src"; \
        cp -a "/tmp/$1_src/$1" "/opt/ros2_rust_ws/src/$1"; \
        rm -rf "/tmp/$1_src"; \
        pkgs="${pkgs} $1"; \
      fi; \
    done \
    && if [[ -n "${pkgs}" ]]; then \
      source /opt/ros/${ROS_DISTRO}/setup.bash; \
      source /opt/ros2_rust_ws/install/local_setup.bash; \
      colcon build --merge-install \
        --parallel-workers 2 \
        --packages-select ${pkgs}; \
    fi \
    && rm -rf /opt/ros2_rust_ws/build /opt/ros2_rust_ws/log

# MID360 用。マウントしたワークスペースを建てるとき livox_ros_driver2 がリンクする。
RUN git clone --depth 1 --branch "${LIVOX_SDK2_REF}" \
      https://github.com/Livox-SDK/Livox-SDK2.git /tmp/Livox-SDK2 \
    && cmake -S /tmp/Livox-SDK2 -B /tmp/Livox-SDK2/build \
         -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    && cmake --build /tmp/Livox-SDK2/build --parallel 2 \
    && cmake --install /tmp/Livox-SDK2/build \
    && rm -rf /tmp/Livox-SDK2 \
    && ldconfig

# PC 用の manifest を使う。SSH の GitHub URL を HTTPS へ直して、まっさらな
# Docker ビルドが個人の SSH 鍵に依存しないようにする。
RUN git clone https://github.com/CIT-Autonomous-Robot-Lab/raspicat2.git "${RASPICAT2_WS}" \
    && git -C "${RASPICAT2_WS}" checkout "${RASPICAT2_REF}" \
    && sed -E 's#git@github.com:([^ ]+)#https://github.com/\1#g' \
         "${RASPICAT2_WS}/raspicat-pc.repos" > /tmp/raspicat-pc.repos \
    && mkdir -p "${RASPICAT2_WS}/src" \
    && vcs import "${RASPICAT2_WS}/src" < /tmp/raspicat-pc.repos

RUN rosdep update --rosdistro=humble \
    && apt-get update \
    && rosdep install --from-paths "${RASPICAT2_WS}/src" --ignore-src \
         --rosdistro=humble -r -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR ${RASPICAT2_WS}
RUN /bin/bash -lc 'source /opt/ros/humble/setup.bash \
    && colcon build --symlink-install'

RUN printf '%s\n' \
      'source /opt/ros/humble/setup.bash' \
      'source /opt/ros2_rust_ws/install/local_setup.bash' \
      'source /opt/raspicat2/install/setup.bash' \
      >> /root/.bashrc

# ソースパッケージが rosdep 経由でだけ足すビルド依存。コンテナを作り直しても
# ワークスペースを建て直せるよう、既知の Livox の依存はイメージに残す。
RUN apt-get update && apt-get install -y --no-install-recommends \
      libaprutil1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY docker/common/ docker/dev/tools/ /usr/local/lib/daifuku/
RUN chmod +x /usr/local/lib/daifuku/*.sh \
    && ln -sf /usr/local/lib/daifuku/entrypoint.sh /ros_entrypoint.sh \
    && ln -s /usr/local/lib/daifuku/build-workspace.sh /usr/local/bin/build-autonomous

WORKDIR /workspaces/daifuku_autonomous
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
