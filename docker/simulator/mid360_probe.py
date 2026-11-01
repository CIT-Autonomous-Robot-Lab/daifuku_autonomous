# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Native ROS acceptance and throughput probe for actual nonplanar Mid-360 returns."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2
from nav_msgs.msg import Odometry

from course_driver import cloud_geometry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=6)
    args = parser.parse_args()
    if args.samples < 2:
        parser.error("samples must be at least two")
    rclpy.init()
    node = Node("mid360_acceptance_probe")
    messages: list[PointCloud2] = []
    wall_stamps: list[float] = []
    odometry: list[Odometry] = []

    def receive(message: PointCloud2) -> None:
        messages.append(message)
        wall_stamps.append(time.monotonic())

    node.create_subscription(PointCloud2, "/livox/lidar", receive, qos_profile_sensor_data)
    node.create_subscription(Odometry, "/odom", odometry.append, 20)
    print("MID360_PROBE_READY", flush=True)
    deadline = time.monotonic() + 120
    try:
        while len(messages) < args.samples or not odometry:
            if time.monotonic() >= deadline:
                raise TimeoutError("Mid-360 PointCloud2 messages did not arrive")
            rclpy.spin_once(node, timeout_sec=min(1, deadline - time.monotonic()))
        stamps = [message.header.stamp.sec + message.header.stamp.nanosec*1e-9 for message in messages]
        if not all(after > before for before, after in zip(stamps, stamps[1:])):
            raise RuntimeError("Mid-360 acquisition stamps did not advance")
        geometry = cloud_geometry(messages[-1])
        result = {
            "status": "PASS", "geometry": geometry, "messages": len(messages),
            "rate_sim_hz": (len(stamps) - 1)/(stamps[-1] - stamps[0]),
            "rate_wall_hz": (len(stamps) - 1)/(wall_stamps[-1] - wall_stamps[0]),
            "real_time_factor": (stamps[-1] - stamps[0])/(wall_stamps[-1] - wall_stamps[0]),
            "fields": [field.name for field in messages[-1].fields],
            "publishers": [info.node_name for info in node.get_publishers_info_by_topic("/livox/lidar")],
            "robot_xyz": [
                odometry[-1].pose.pose.position.x, odometry[-1].pose.pose.position.y,
                odometry[-1].pose.pose.position.z,
            ],
        }
        args.out.write_text(json.dumps(result, indent=2))
        print("MID360_PROBE_RESULT " + json.dumps(result), flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
