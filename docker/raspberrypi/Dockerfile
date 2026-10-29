# **タグではなくダイジェストで固定する。** `FROM` のタグは毎回レジストリへ引きに
# いくので (`load metadata for ...`)、上流が同じタグを焼き直した日にこの下の層が
# 全部作り直しになる — しかも「何も変えていないのに 1〜2 時間」という顔で出る。
# 上げるときは人が明示的にこの sha を書き換えること。
ARG ROS_IMAGE=ros:humble-ros-base-jammy@sha256:7bea3d9aa2483d3ca34c8e30d921b79273b0913bd7dc64bebf51d082b5d107e4

FROM ${ROS_IMAGE}

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
      ros-humble-grid-map-msgs \
      ros-humble-joint-state-publisher \
      ros-humble-joy \
      ros-humble-laser-filters \
      ros-humble-nav2-bringup \
      ros-humble-pcl-conversions \
      ros-humble-pointcloud-to-laserscan \
      ros-humble-realsense2-camera \
      ros-humble-robot-localization \
      ros-humble-robot-state-publisher \
      ros-humble-rosbag2 \
      ros-humble-slam-toolbox \
      ros-humble-teleop-twist-joy \
      ros-humble-teleop-twist-keyboard \
      ros-humble-topic-tools \
      ros-humble-twist-mux \
      ros-humble-urdf \
      ros-humble-urg-node \
      ros-humble-xacro

# **ARG は「最初に使う RUN の直前」に置くこと。** BuildKit は FROM より後の ARG を
# **使っていない RUN も含めて**それ以降の全 RUN のキャッシュキーに入れるので、上の
# apt 層 (2.85GB / Pi 4 で 6 分) より上に置くと --build-arg がそれを作り直しにいく。
ARG LIVOX_SDK2_REF=v1.3.1
# ビルドの並列数。**compose は環境変数の BUILD_JOBS をここへ渡さない** (渡すと
# OOM 避けで下げた瞬間に下の層が全部作り直しになる)。イメージ側を絞るのは明示のとき
# だけ: docker compose build --build-arg BUILD_JOBS=1
ARG BUILD_JOBS=4

# livox_ros_driver2 が要る。
RUN git clone --depth 1 --branch "${LIVOX_SDK2_REF}" \
      https://github.com/Livox-SDK/Livox-SDK2.git /tmp/Livox-SDK2 \
    && cmake -S /tmp/Livox-SDK2 -B /tmp/Livox-SDK2/build \
         -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/Livox-SDK2/build --parallel "${BUILD_JOBS}" \
    && cmake --install /tmp/Livox-SDK2/build \
    && rm -rf /tmp/Livox-SDK2

# vi_planner が使う rclrs のツールチェーン。**compose は user: "1000:1000" で回す**
# ので、/root (0700) ではなく誰でも読める /opt に置く。レジストリはキャッシュ
# マウント (イメージには入らない。up 時の CARGO_HOME はホストの build/ 側)。
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
# 中間生成物は同じ層の中で消す (層を分けて消しても実体は前の層に残る)。
RUN --mount=type=cache,target=/opt/cargo/registry \
    source /opt/ros/${ROS_DISTRO}/setup.bash \
    && colcon build --merge-install \
         --parallel-workers "${BUILD_JOBS}" \
         --packages-up-to rclrs \
    && rm -rf build log

# vi_planner が使うメッセージの Rust バインディングは Humble に無いことがある。
# 無いものだけソースから建てる (tf2_msgs は 2026-08-09 に publish_tf が入って加わった)。
RUN --mount=type=cache,target=/opt/cargo/registry \
    pkgs=""; \
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
        --parallel-workers "${BUILD_JOBS}" \
        --packages-select ${pkgs}; \
    fi \
    && rm -rf /opt/ros2_rust_ws/build /opt/ros2_rust_ws/log

# 外部パッケージのソースは **rosdep に apt 依存を解かせるためだけ**に取り、済んだら
# 捨てる (ワークスペースのビルドは up 時にコンテナ内で行う)。rosdep をここに置くのは
# 「apt が変わったときだけイメージを焼き直す」ため。
#
# 自前のソースはまとめて渡し、Pi で建てない GUI パッケージだけを除く。
# bind mount なので地図などの実体をイメージの層に残さない。
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=bind,source=daifuku_autonomous.repos,target=/tmp/repos.yaml \
    --mount=type=bind,source=src,target=/tmp/daifuku \
    source /opt/ros/${ROS_DISTRO}/setup.bash \
    && mkdir -p /tmp/vcs && cd /tmp/vcs \
    && vcs import . < /tmp/repos.yaml \
    && cp src/livox_ros_driver2/package_ROS2.xml \
          src/livox_ros_driver2/package.xml \
    && rosdep update --rosdistro=${ROS_DISTRO} \
    && apt-get update \
    && package_paths="$(colcon list --base-paths /tmp/daifuku \
         --packages-skip daifuku_rqt daifuku_waypoint_manager --paths-only)" \
    && rosdep install --from-paths /tmp/vcs/src ${package_paths} --ignore-src -r -y \
         --rosdistro=${ROS_DISTRO} \
    && cd / \
    && rm -rf /tmp/vcs

# arm64 の CMake が相対探索で ROS の共有ライブラリを見失う (QEMU 下で顕著) ことへの
# 当て木。探索先を実在を確かめた lib ディレクトリに固定する。
RUN if [[ "$(dpkg --print-architecture)" == "arm64" ]]; then \
      test -f /opt/ros/${ROS_DISTRO}/lib/librcutils.so; \
      find /opt/ros/${ROS_DISTRO}/share -type f \
        -name '*.cmake' -exec \
        sed -i \
          's#set(_lib "NOTFOUND")#set(_lib "${CMAKE_CURRENT_LIST_DIR}/../../../lib/lib${_library}.so")#' \
          '{}' +; \
    fi

ENV ROS_LOCALHOST_ONLY=0 \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp

# ソースと生成物は Compose がワークスペース全体を bind mount する。
COPY docker/common/ docker/raspberrypi/scripts/ /usr/local/lib/daifuku/
RUN ldconfig \
    && chmod +x /usr/local/lib/daifuku/*.sh \
    && ln -sf /usr/local/lib/daifuku/entrypoint.sh /ros_entrypoint.sh \
    && ln -s /usr/local/lib/daifuku/build-workspace.sh /usr/local/bin/build-workspace

WORKDIR ${ROS_WS}
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
