#!/usr/bin/env bash
# IMU は無効。カラー・深度画像を 640x480 / 30 fps で起動する。
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

exec docker run --rm -it --init --name realsense \
  --network host --ipc host \
  --user 1000:1000 --group-add 44 \
  --device-cgroup-rule='c *:* rwm' \
  -v /dev:/dev \
  -v "${ROOT_DIR}/docker/raspberrypi/fastdds_udp_whitelist.xml:/etc/fastdds/udp_whitelist.xml:ro" \
  -e HOME=/tmp \
  -e ROS_DOMAIN_ID=90 \
  -e FASTRTPS_DEFAULT_PROFILES_FILE=/etc/fastdds/udp_whitelist.xml \
  daifuku-autonomous:humble \
  ros2 launch realsense2_camera rs_launch.py \
    camera_name:=d435i \
    enable_gyro:=false enable_accel:=false \
    enable_color:=true enable_depth:=true \
    depth_module.depth_profile:=640x480x30 \
    rgb_camera.color_profile:=640x480x30 \
    "$@"
