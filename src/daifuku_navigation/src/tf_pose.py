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
"""Compose the latest map correction with the latest odometry for the Rust planner."""

from geometry_msgs.msg import PoseWithCovarianceStamped
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener


class TfPose(Node):
    """Read TF without owning or republishing any transform."""

    def __init__(self) -> None:
        super().__init__('vi_tf_pose')
        self._buffer = Buffer(node=self)
        self._listener = TransformListener(self._buffer, self)
        self._publisher = self.create_publisher(PoseWithCovarianceStamped, 'vi_pose_from_tf', 1)
        self._map_update: tuple[Time, Time] | None = None
        self._timer = self.create_timer(0.05, self._publish_pose)

    def _publish_pose(self) -> None:
        try:
            map_to_odom = self._buffer.lookup_transform('map', 'odom', Time())
            # Each half at its own latest time. emcl3d stamps map→odom with the cloud
            # time (~0.2 s ago); the latest common time would drop the odometry since.
            transform = self._buffer.lookup_transform_full(
                'map', Time(), 'base_footprint', Time(), 'odom')
        except TransformException:
            return

        map_stamp = Time.from_msg(map_to_odom.header.stamp)
        if self._map_update is None or map_stamp != self._map_update[0]:
            self._map_update = (map_stamp, self.get_clock().now())
        _, map_seen = self._map_update

        msg = PoseWithCovarianceStamped()
        msg.header = transform.header
        # Freshness is the map correction's, never the odometry's. AMCL postdates
        # map→odom, so cap it at when the correction arrived.
        msg.header.stamp = min(map_stamp, map_seen, key=lambda t: t.nanoseconds).to_msg()
        translation = transform.transform.translation
        msg.pose.pose.position.x = translation.x
        msg.pose.pose.position.y = translation.y
        msg.pose.pose.position.z = translation.z
        msg.pose.pose.orientation = transform.transform.rotation
        self._publisher.publish(msg)


def main() -> None:
    """Run the pose adapter until launch shuts down."""
    rclpy.init()
    node = TfPose()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
