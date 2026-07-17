FROM osrf/ros:humble-desktop-full

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=humble \
    ROS_WS=/opt/ros_ws

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        python3-colcon-common-extensions \
        python3-rosdep \
        python3-vcstool \
        ros-humble-navigation2 \
        ros-humble-nav2-bringup \
        ros-humble-slam-toolbox \
    && rm -rf /var/lib/apt/lists/*

WORKDIR ${ROS_WS}

COPY autonomous_bot.repos /tmp/autonomous_bot.repos
RUN mkdir -p src \
    && vcs import . < /tmp/autonomous_bot.repos

COPY src/autonomous_nav src/autonomous_nav

RUN . /opt/ros/${ROS_DISTRO}/setup.sh \
    && rosdep update --rosdistro=${ROS_DISTRO} \
    && apt-get update \
    && rosdep install --from-paths src --ignore-src -r -y --rosdistro=${ROS_DISTRO} \
    && rm -rf /var/lib/apt/lists/* \
    && colcon build --symlink-install

COPY docker/entrypoint.sh /ros_entrypoint.sh
RUN chmod +x /ros_entrypoint.sh

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["bash"]
