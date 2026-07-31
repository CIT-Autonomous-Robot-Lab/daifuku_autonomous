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

# Build CMake packages without the ros2_rust overlay.  Under BuildKit/QEMU,
# arm64 CMake can reject ROS shared libraries during the generated relative
# lookup under QEMU.  Pin builder-only export lookups to the same verified ROS
# lib directory.  The runtime image receives none of these modified CMake files.
RUN if [[ "$(dpkg --print-architecture)" == "arm64" ]]; then \
      test -f /opt/ros/${ROS_DISTRO}/lib/librcutils.so; \
      find /opt/ros/${ROS_DISTRO}/share -type f \
        -name '*.cmake' -exec \
        sed -i \
          's#set(_lib "NOTFOUND")#set(_lib "${CMAKE_CURRENT_LIST_DIR}/../../../lib/lib${_library}.so")#' \
          '{}' +; \
    fi

RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && MULTIARCH="$(dpkg-architecture -qDEB_HOST_MULTIARCH)" \
    && colcon build --merge-install \
         --parallel-workers "${BUILD_JOBS}" \
         --packages-select autonomous_nav emcl2 livox_ros_driver2 \
                          raspicat_bringup raspicat_description \
                          raspimouse raspimouse_msgs \
         --cmake-args -DROS_EDITION=ROS2 \
                      -DDISTRO_ROS=${ROS_DISTRO} \
                      "-DCMAKE_LIBRARY_PATH=/opt/ros/${ROS_DISTRO}/lib;/usr/lib/${MULTIARCH};/lib/${MULTIARCH}" \
                      "-DCMAKE_INCLUDE_PATH=/usr/include;/usr/include/eigen3;/usr/include/pcl-1.12" \
                      "-DCMAKE_CXX_FLAGS=-O2 -I/usr/include/eigen3" \
                      -DEigen3_DIR=/usr/share/eigen3/cmake \
                      -DOPENSSL_ROOT_DIR=/usr \
                      -DOPENSSL_INCLUDE_DIR=/usr/include \
                      -DOPENSSL_CRYPTO_LIBRARY=/usr/lib/${MULTIARCH}/libcrypto.so \
                      -DOPENSSL_SSL_LIBRARY=/usr/lib/${MULTIARCH}/libssl.so

RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
    && source /opt/ros2_rust_ws/install/local_setup.bash \
    && source ${ROS_WS}/install/local_setup.bash \
    && colcon build --merge-install \
         --parallel-workers "${BUILD_JOBS}" \
         --packages-select vi_global_planner vi_planner \
         --cargo-args --release \
    && install -D -m 0644 \
         src/value_iteration3/vi_ros2/vi_global_planner/launch/navigation_launch.py \
         ${ROS_WS}/install/share/vi_global_planner/launch/navigation_launch.py


# Headless runtime: no compiler, source tree, Rust toolchain, or RViz.
FROM ${ROS_IMAGE} AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=humble \
    ROS_WS=/opt/ros_ws \
    ROS_LOCALHOST_ONLY=0 \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
      libapr1 \
      ros-humble-diagnostic-updater \
      ros-humble-grid-map-msgs \
      ros-humble-joint-state-publisher \
      ros-humble-laser-filters \
      ros-humble-nav2-bringup \
      ros-humble-navigation2 \
      ros-humble-pcl-conversions \
      ros-humble-pointcloud-to-laserscan \
      ros-humble-robot-localization \
      ros-humble-robot-state-publisher \
      ros-humble-rosbag2 \
      ros-humble-slam-toolbox \
      ros-humble-teleop-twist-joy \
      ros-humble-teleop-twist-keyboard \
      ros-humble-topic-tools \
      ros-humble-urdf \
      ros-humble-xacro \
    && rm -rf /var/lib/apt/lists/*

# raspicat_bringup / raspicat_description が package.xml で宣言している依存のうち、
# 次のものは意図的に runtime へ入れていない。build 段では rosdep が入れるので
# ビルドは通り、抜いているのは実行イメージだけ。
#   * urg_node   — 本機の LiDAR は MID360。robot_bringup.launch.py は上流の
#                  raspicat.launch.py と違って urg 系を一切起動しないので到達不能。
#   * rviz2 / joint_state_publisher_gui
#                — raspicat_description の表示用 launch 専用。runtime は headless。
#   * joy_linux  — ジョイスティック teleop 用 (teleop_twist_joy は入っている)。
#                  使うならここに足すこと。

COPY --from=builder /usr/local/lib/liblivox_lidar_sdk_shared.so /usr/local/lib/
COPY --from=builder /opt/ros2_rust_ws/install /opt/ros2_rust_ws/install
COPY --from=builder /opt/ros_ws/install /opt/ros_ws/install
RUN ldconfig

COPY docker/common/entrypoint.sh /ros_entrypoint.sh
RUN chmod +x /ros_entrypoint.sh

WORKDIR ${ROS_WS}
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
