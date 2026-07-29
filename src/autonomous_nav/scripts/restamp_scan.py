#!/usr/bin/env python3
"""Republish a LaserScan with the stamp replaced by the receive time.

The Livox MID360 has no PTP/gPTP time source on this robot, so
livox_ros_driver2 stamps frames from the device clock, which drifts by
seconds per minute against the Pi system clock. Downstream consumers
(emcl2's map->odom TF, Nav2 costmap message filters) then reject data as
"too old"/"in the future" a few minutes after startup. Until the sensor
is PTP-synced, restamping at receive time keeps every stamp on the one
clock the rest of the stack (wheel odometry, TF, Nav2) already uses.

Usage: restamp_scan.py <input_topic> <output_topic>
"""
import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan


class RestampScan(Node):
    def __init__(self, in_topic, out_topic):
        super().__init__("restamp_scan")
        # Publisher is reliable so both reliable and best-effort
        # subscribers (laser_filters, rviz) can match it.
        pub_qos = QoSProfile(depth=10)
        sub_qos = QoSProfile(depth=10)
        sub_qos.reliability = ReliabilityPolicy.BEST_EFFORT
        self.pub_ = self.create_publisher(LaserScan, out_topic, pub_qos)
        self.sub_ = self.create_subscription(
            LaserScan, in_topic, self.callback, sub_qos)

    def callback(self, msg):
        msg.header.stamp = self.get_clock().now().to_msg()
        self.pub_.publish(msg)


def main():
    args = rclpy.utilities.remove_ros_args(sys.argv)
    if len(args) != 3:
        print("usage: restamp_scan.py <input_topic> <output_topic>",
              file=sys.stderr)
        return 1
    rclpy.init(args=sys.argv)
    node = RestampScan(args[1], args[2])
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
