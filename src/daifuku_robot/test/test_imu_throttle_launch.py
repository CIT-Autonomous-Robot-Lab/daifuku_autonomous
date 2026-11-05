# Copyright 2026 Keita Sekiguchi / nop
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

"""実ノードのセンサーQoS、間引き周期、IMUメッセージの保持を確認する。"""

import time
import unittest

from launch import LaunchDescription
from launch.actions import SetEnvironmentVariable
from launch_ros.actions import Node
import launch_testing
import launch_testing.actions
import launch_testing.asserts
import pytest
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Imu


@pytest.mark.launch_test
def generate_test_description():
    process = Node(
        package="daifuku_robot", executable="imu_throttle",
        name="qa_imu_throttle", output="screen",
        remappings=[("imu_in", "/qa_imu_in"), ("imu_out", "/qa_imu_out")],
    )
    return LaunchDescription([
        SetEnvironmentVariable("ROS_DOMAIN_ID", "77"),
        SetEnvironmentVariable("ROS_LOCALHOST_ONLY", "1"),
        process,
        launch_testing.actions.ReadyToTest(),
    ]), {"process": process}


class TestImuThrottle(unittest.TestCase):

    def test_sensor_qos_throttles_without_changing_samples(self):
        rclpy.init()
        node = rclpy.create_node("imu_throttle_test")
        received = []
        sent = {}
        publisher = node.create_publisher(Imu, "/qa_imu_in", QoSProfile(depth=10))
        subscription = node.create_subscription(
            Imu, "/qa_imu_out", received.append, qos_profile_sensor_data,
        )
        try:
            deadline = time.monotonic() + 10
            while publisher.get_subscription_count() == 0 or (
                node.count_publishers("/qa_imu_out") == 0
            ):
                self.assertLess(time.monotonic(), deadline, "throttle discovery timed out")
                rclpy.spin_once(node, timeout_sec=0.01)

            # The input must remain BEST_EFFORT even when its source is RELIABLE.
            inputs = node.get_subscriptions_info_by_topic("/qa_imu_in")
            self.assertEqual(len(inputs), 1)
            self.assertEqual(inputs[0].qos_profile.reliability, ReliabilityPolicy.BEST_EFFORT)

            def publish():
                message = Imu()
                message.header.frame_id = "livox_frame"
                message.header.stamp.sec = 123
                message.header.stamp.nanosec = len(sent)
                message.angular_velocity.z = len(sent) * 0.01
                sent[message.header.stamp.nanosec] = message
                publisher.publish(message)

            timer = node.create_timer(0.005, publish)
            end = time.monotonic() + 2
            while time.monotonic() < end:
                rclpy.spin_once(node, timeout_sec=0.01)
            node.destroy_timer(timer)

            # At 200 Hz input, a healthy 50 Hz gate emits roughly 80–100 samples.
            self.assertGreaterEqual(len(received), 60)
            self.assertLessEqual(len(received), 101)
            for message in received:
                self.assertEqual(message, sent[message.header.stamp.nanosec])
        finally:
            node.destroy_subscription(subscription)
            node.destroy_node()
            rclpy.shutdown()


@launch_testing.post_shutdown_test()
class TestImuThrottleExit(unittest.TestCase):

    def test_clean_exit(self, proc_info, process):
        launch_testing.asserts.assertExitCodes(proc_info, [0, -2, -15], process=process)
