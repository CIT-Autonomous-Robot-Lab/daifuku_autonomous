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
"""Publish a colocated gravity frame from measured odometry roll/pitch only."""

import math


def leveling_rotation(
    orientation: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Return Rx(-roll) Ry(-pitch), preserving heading without position feedback."""
    x, y, z, w = orientation
    roll = math.atan2(2 * (w*x + y*z), 1 - 2 * (x*x + y*y))
    pitch = math.asin(2 * (w*y - z*x))
    sr, cr = math.sin(roll/2), math.cos(roll/2)
    sp, cp = math.sin(pitch/2), math.cos(pitch/2)
    return -sr*cp, -cr*sp, sr*sp, cr*cp


def main() -> None:
    import rclpy
    from geometry_msgs.msg import TransformStamped
    from nav_msgs.msg import Odometry
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from tf2_msgs.msg import TFMessage

    class GravityFrame(Node):
        def __init__(self):
            super().__init__("localization_gravity_publisher")
            self.publisher = self.create_publisher(TFMessage, "/tf", 100)
            self.announced = False
            self.create_subscription(Odometry, "/odom", self.receive, qos_profile_sensor_data)

        def receive(self, message: Odometry) -> None:
            rotation = message.pose.pose.orientation
            transform = TransformStamped()
            transform.header.stamp = message.header.stamp
            transform.header.frame_id = message.child_frame_id
            transform.child_frame_id = "localization_gravity"
            # Default translation is zero. Use no global position or yaw feedback.
            (transform.transform.rotation.x, transform.transform.rotation.y,
             transform.transform.rotation.z, transform.transform.rotation.w) = leveling_rotation(
                (rotation.x, rotation.y, rotation.z, rotation.w)
            )
            output = TFMessage()
            output.transforms = [transform]
            self.publisher.publish(output)
            if not self.announced:
                self.get_logger().info("GRAVITY_FRAME_READY parent=" + message.child_frame_id)
                self.announced = True

    rclpy.init()
    node = GravityFrame()
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
