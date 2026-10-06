# ROS 2 Humble runtime for the usim VTC workflow (daifuku side only).
#
# Built by daifuku_sim.vtc.run_vtc from the **repository root** context:
#
#   <engine> build -f simulator/container/usim_vtc/Dockerfile \
#       --build-arg BASE_IMAGE=daifuku-autonomous:humble -t daifuku-usim-vtc-stack:local .
#
# BASE_IMAGE is docker/raspberrypi/Dockerfile (apt deps, rosdep of every repo in
# daifuku_autonomous.repos, robot_state_publisher/xacro, the ros2_rust toolchain
# vi_planner needs). The simulator README's daifuku-autonomous:humble-amd64
# (same Dockerfile plus a built /opt/ros_ws) also works: this layer builds its
# own overlay workspace /opt/usim_vtc_ws, sourced last by stack.sh, so a stale
# /opt/ros_ws in the base cannot shadow the current checkout.
#
# Only what the simulated workflow runs is built: daifuku_config /
# daifuku_config_manager / daifuku_stack, emcl2, vi_planner and
# raspicat_description. daifuku_bringup, raspicat_driver and livox_ros_driver2
# are not built here, and stack.sh never launches them.
ARG BASE_IMAGE=daifuku-autonomous:humble
FROM ${BASE_IMAGE}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
USER root
ARG BUILD_JOBS=4

# External sources: the exact entries daifuku_autonomous.repos declares, filtered
# to the three the workflow needs (one source of truth for their refs).
# (python -c rather than a RUN heredoc: older buildah/podman builders reject heredocs.)
COPY daifuku_autonomous.repos /tmp/daifuku_autonomous.repos
RUN mkdir -p /opt/usim_vtc_ws/src /opt/usim_vtc \
    && python3 -c "import yaml; \
wanted = {'src/emcl2_ros2', 'src/value_iteration3', 'src/raspicat_description'}; \
data = yaml.safe_load(open('/tmp/daifuku_autonomous.repos')); \
picked = {k: v for k, v in data['repositories'].items() if k in wanted}; \
assert set(picked) == wanted, 'daifuku_autonomous.repos lacks %s' % sorted(wanted - set(picked)); \
yaml.safe_dump({'repositories': picked}, open('/opt/usim_vtc/vtc_stack.repos', 'w'))"
RUN cd /opt/usim_vtc_ws && vcs import . < /opt/usim_vtc/vtc_stack.repos

COPY src/daifuku_config /opt/usim_vtc_ws/src/daifuku_config
COPY src/daifuku_config_manager /opt/usim_vtc_ws/src/daifuku_config_manager
COPY src/daifuku_stack /opt/usim_vtc_ws/src/daifuku_stack
# A Windows checkout may carry CRLF and no exec bits (core.fileMode=false).
RUN find /opt/usim_vtc_ws/src/daifuku_config /opt/usim_vtc_ws/src/daifuku_config_manager \
         /opt/usim_vtc_ws/src/daifuku_stack \
        -type f \( -name '*.py' -o -name '*.yaml' -o -name '*.xml' -o -name '*.txt' -o -name '*.cfg' -o -name 'site' \) \
        -exec sed -i 's/\r$//' {} + \
    && chmod +x /opt/usim_vtc_ws/src/daifuku_stack/src/*.py

# vi_planner (planner:=vi local_planner:=vi nav2:=false) is an ament_cargo
# package: it needs the ROS 2 Rust underlay docker/raspberrypi/Dockerfile builds at
# /opt/ros2_rust_ws (rclrs, plus nav2_msgs / tf2_msgs Rust bindings where Humble
# lacks them) and cargo + colcon-ros-cargo. docker/dev/Dockerfile has none of this,
# so it is not a valid BASE_IMAGE. Fail here, not at launch time.
RUN test -f /opt/ros2_rust_ws/install/local_setup.bash \
      || { echo "BASE_IMAGE lacks /opt/ros2_rust_ws (build it from docker/raspberrypi/Dockerfile)" >&2; exit 1; } \
    && test -d /opt/ros2_rust_ws/install/share/rclrs \
      || { echo "BASE_IMAGE /opt/ros2_rust_ws has no rclrs" >&2; exit 1; } \
    && command -v cargo >/dev/null \
      || { echo "BASE_IMAGE has no cargo on PATH" >&2; exit 1; } \
    && for msg in nav2_msgs tf2_msgs; do \
         test -d "/opt/ros/humble/share/${msg}/rust" -o -d "/opt/ros2_rust_ws/install/share/${msg}/rust" \
           || { echo "no Rust binding for ${msg} in /opt/ros or /opt/ros2_rust_ws" >&2; exit 1; }; \
       done

RUN source /opt/ros/humble/setup.bash \
    && source /opt/ros2_rust_ws/install/local_setup.bash \
    && cd /opt/usim_vtc_ws \
    && colcon build --parallel-workers "${BUILD_JOBS}" \
         --packages-select daifuku_config daifuku_config_manager daifuku_stack \
                           emcl2 raspicat_description vi_planner \
         --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && rm -rf build log \
    && test -x /opt/usim_vtc_ws/install/vi_planner/lib/vi_planner/vi_planner \
    && vcs export --exact src > /opt/usim_vtc/sources.repos \
    && chmod -R a+rX /opt/usim_vtc_ws /opt/usim_vtc

COPY simulator/container/usim_vtc/stack.sh simulator/container/usim_vtc/*.py /opt/usim_vtc/
COPY simulator/container/usim_vtc/*.yaml /opt/usim_vtc/
COPY simulator/container/usim_vtc/fastdds_udp.xml /opt/usim_vtc/
COPY simulator/src/daifuku_sim/vtc/prepare_slam_params.py /opt/usim_vtc/
COPY simulator/src/daifuku_sim/vtc/vtc_contract.py /opt/usim_vtc/
RUN sed -i 's/\r$//' /opt/usim_vtc/stack.sh /opt/usim_vtc/vtc_ros.py \
    && chmod 0755 /opt/usim_vtc/stack.sh /opt/usim_vtc/vtc_ros.py \
    && python3 -m py_compile /opt/usim_vtc/*.py \
    && source /opt/ros/humble/setup.bash \
    && PYTHONPATH="/opt/usim_vtc:${PYTHONPATH}" python3 -c 'import vtc_contract; import vtc_ros'

ENV HOME=/tmp \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
    FASTRTPS_DEFAULT_PROFILES_FILE=/opt/usim_vtc/fastdds_udp.xml \
    FASTDDS_BUILTIN_TRANSPORTS=UDPv4
WORKDIR /opt/usim_vtc_ws
