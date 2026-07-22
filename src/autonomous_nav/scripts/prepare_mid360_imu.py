#!/usr/bin/env python3

"""Attach usable covariance metadata to the raw Mid-360 IMU message."""

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu


class PrepareMid360Imu(Node):
    def __init__(self):
        super().__init__("prepare_mid360_imu")
        gyro_variance = self.declare_parameter("gyro_variance", 4.0e-4).value
        accel_variance = self.declare_parameter("accel_variance", 4.0e-2).value

        self._gyro_covariance = [
            gyro_variance, 0.0, 0.0,
            0.0, gyro_variance, 0.0,
            0.0, 0.0, gyro_variance,
        ]
        self._accel_covariance = [
            accel_variance, 0.0, 0.0,
            0.0, accel_variance, 0.0,
            0.0, 0.0, accel_variance,
        ]
        self._publisher = self.create_publisher(Imu, "imu_out", qos_profile_sensor_data)
        self._subscription = self.create_subscription(
            Imu, "imu_in", self._callback, qos_profile_sensor_data
        )

    def _callback(self, message):
        # Mid-360 supplies acceleration and angular velocity, not a fused
        # orientation. REP-145 uses -1 in element zero for unavailable data.
        message.orientation_covariance = [-1.0, 0.0, 0.0,
                                          0.0, 0.0, 0.0,
                                          0.0, 0.0, 0.0]
        message.angular_velocity_covariance = self._gyro_covariance
        message.linear_acceleration_covariance = self._accel_covariance
        self._publisher.publish(message)


def main(args=None):
    rclpy.init(args=args)
    node = PrepareMid360Imu()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
