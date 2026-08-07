#!/usr/bin/env python3

"""Make the raw Mid-360 IMU message usable by robot_localization.

livox_ros_driver2 forwards what the sensor sends and nothing else, so
three things have to happen before the EKF sees it.

  * Covariance.  Every field is left at zero, which REP-145 consumers
    read as "perfectly certain".

  * Gyro bias.  The Mid-360 has a large turn-on bias -- +0.013960 rad/s
    (+0.800 deg/s) on the z axis of this unit, measured over 5001
    samples at rest (2026-08-05).  robot_localization does not estimate
    sensor bias, so whatever is left here goes straight into yaw: that
    figure is 48 deg/min of drift while standing still, which is far
    worse than the wheel odometry it is supposed to improve.  The bias
    is measured from the first still window after startup, so **the
    robot must not be moving when this node comes up**.

  * Units.  Livox reports acceleration in g, not m/s^2 (measured
    |a| = 0.997 at rest).  Nothing consumes it today -- mid360_ekf.yaml
    takes vyaw only -- but a message published as sensor_msgs/Imu is
    read as m/s^2 by everything downstream.

Topics are the relative imu_in / imu_out, remapped by
lidar_bringup.launch.py -- the same shape as restamp_scan.py.
"""

import math

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu

STANDARD_GRAVITY = 9.80665


class PrepareMid360Imu(Node):
    def __init__(self):
        super().__init__("prepare_mid360_imu")
        gyro_variance = self.declare_parameter("gyro_variance", 4.0e-4).value
        accel_variance = self.declare_parameter("accel_variance", 4.0e-2).value
        self._accel_in_g = self.declare_parameter("accel_in_g", True).value
        self._bias = list(
            self.declare_parameter("gyro_bias", [0.0, 0.0, 0.0]).value
        )
        self._estimating = self.declare_parameter(
            "estimate_gyro_bias", True
        ).value
        self._bias_samples = self.declare_parameter("bias_samples", 400).value
        self._bias_max_sd = self.declare_parameter("bias_max_sd", 0.005).value
        self._bias_max = self.declare_parameter("bias_max", 0.05).value
        self._window = []
        self._rejected = 0

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

    def _estimate_bias(self, message):
        """Adopt the mean of one still window as the gyro bias.

        A window is rejected when it is not still, so the node keeps
        looking instead of freezing a wrong offset in.  Both tests are
        needed: the standard deviation alone accepts a *steady* turn
        (its samples are as quiet as a resting sensor), and the mean
        alone rejects nothing while the robot ramps up.

        Only the z axis decides.  Judging on all three rejects a robot
        that is standing perfectly still, because the x axis of this
        sensor is four times noisier than z (sd 0.0054 vs 0.0014 rad/s
        at rest, 2026-08-05) -- and z is the only axis the EKF reads
        (two_d_mode with vyaw alone).  The bias of all three is still
        measured and subtracted; it is the *gate* that looks at z.
        """
        window = self._window
        window.append((
            message.angular_velocity.x,
            message.angular_velocity.y,
            message.angular_velocity.z,
        ))
        if len(window) < self._bias_samples:
            return

        count = len(window)
        means = [sum(s[i] for s in window) / count for i in range(3)]
        deviations = [
            math.sqrt(sum((s[i] - means[i]) ** 2 for s in window) / count)
            for i in range(3)
        ]
        window.clear()

        if deviations[2] > self._bias_max_sd or abs(means[2]) > self._bias_max:
            self._rejected += 1
            # Once per ~10 windows: at 200 Hz and 400 samples that is a
            # line every 20 s, enough to notice without drowning the log.
            if self._rejected % 10 == 1:
                self.get_logger().warn(
                    "prepare_mid360_imu: still moving, so the gyro bias is not "
                    "measured yet (%d windows rejected; the gate is z, limits "
                    "are |mean| <= %.4f and sd <= %.4f rad/s). "
                    "mean=[%+.5f, %+.5f, %+.5f] sd=[%.5f, %.5f, %.5f] rad/s. "
                    "Keep the robot still, or pass the bias in with "
                    "estimate_gyro_bias:=false and gyro_bias:=[x, y, z]."
                    % (self._rejected, self._bias_max, self._bias_max_sd,
                       means[0], means[1], means[2],
                       deviations[0], deviations[1], deviations[2])
                )
            return

        self._bias = means
        self._estimating = False
        self.get_logger().info(
            "prepare_mid360_imu: gyro bias = [%+.6f, %+.6f, %+.6f] rad/s "
            "(z = %+.3f deg/s = %.1f deg/min of yaw if left in), "
            "measured over %d samples at rest"
            % (means[0], means[1], means[2],
               math.degrees(means[2]), math.degrees(means[2]) * 60.0, count)
        )

    def _callback(self, message):
        if self._estimating:
            self._estimate_bias(message)

        message.angular_velocity.x -= self._bias[0]
        message.angular_velocity.y -= self._bias[1]
        message.angular_velocity.z -= self._bias[2]

        if self._accel_in_g:
            message.linear_acceleration.x *= STANDARD_GRAVITY
            message.linear_acceleration.y *= STANDARD_GRAVITY
            message.linear_acceleration.z *= STANDARD_GRAVITY

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
