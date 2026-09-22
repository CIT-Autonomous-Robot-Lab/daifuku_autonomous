#!/usr/bin/env python3
# Copyright 2026 Keita Sekiguchi / nop
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Republish a LaserScan with the stamp replaced by the receive time.

The Livox MID360 has no PTP/gPTP time source on this robot, so
livox_ros_driver2 stamps frames from the device clock, which drifts by
seconds per minute against the Pi system clock. Downstream consumers
(emcl2's map->odom TF, Nav2 costmap message filters) then reject data as
"too old"/"in the future" a few minutes after startup. Until the sensor
is PTP-synced, restamping at receive time keeps every stamp on the one
clock the rest of the stack (wheel odometry, TF, Nav2) already uses.

Topics are the relative scan_in / scan_out, remapped by
scan_pipeline.launch.py -- the same shape as prepare_mid360_imu.py.
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan


class RestampScan(Node):
    def __init__(self):
        super().__init__("restamp_scan")
        # Publisher is reliable so both reliable and best-effort
        # subscribers (laser_filters, rviz) can match it.
        pub_qos = QoSProfile(depth=10)
        sub_qos = QoSProfile(depth=10)
        sub_qos.reliability = ReliabilityPolicy.BEST_EFFORT
        self._publisher = self.create_publisher(LaserScan, "scan_out", pub_qos)
        self._subscription = self.create_subscription(
            LaserScan, "scan_in", self._callback, sub_qos)

    def _callback(self, message):
        message.header.stamp = self.get_clock().now().to_msg()
        self._publisher.publish(message)


def main(args=None):
    rclpy.init(args=args)
    node = RestampScan()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
