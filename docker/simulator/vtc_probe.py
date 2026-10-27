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
"""ROS subscriptions, TF lookups, bounded readiness, and motor ownership."""
from __future__ import annotations

import math
import time
from typing import Callable

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped, Quaternion, Twist
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import LaserScan
from std_srvs.srv import SetBool
from tf2_msgs.msg import TFMessage
from tf2_ros import Buffer, TransformException, TransformListener
from vtc_contract import MapStats, PoseJson, parse_pose as parse_contract_pose
from vtc_telemetry import ProbeResult

BASE = 'base_footprint'
LIDAR = 'lidar_link'


class ProbeError(RuntimeError):
    """A probe precondition failed; the message goes into the JSON as ``error``."""

    def __init__(self, problem: str, result: ProbeResult | None = None) -> None:
        super().__init__(problem)
        self.result: ProbeResult = {} if result is None else result


def yaw_of(q: Quaternion) -> float:
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def pose(x: float, y: float, yaw: float) -> PoseJson:
    return {'x': float(x), 'y': float(y), 'yaw': float(yaw)}


def parse_pose(text: str) -> tuple[float, float, float]:
    value = parse_contract_pose(text)
    return value.x, value.y, value.yaw


class Probe(Node):
    def __init__(self) -> None:
        super().__init__(
            'vtc_probe',
            parameter_overrides=[Parameter('use_sim_time', Parameter.Type.BOOL, True)],
        )
        latched = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.counts = {'clock': 0, 'odom': 0, 'scan_raw': 0, 'scan': 0, 'map': 0, 'cmd_vel_nonzero': 0}
        self.odom: Odometry | None = None
        self.scan_raw: LaserScan | None = None
        self.map: OccupancyGrid | None = None
        self.tf_edges: dict[tuple[str, str], int] = {}
        self.static_edges: dict[str, str] = {}
        self.create_subscription(Clock, '/clock', lambda _m: self._count('clock'), qos_profile_sensor_data)
        self.create_subscription(Odometry, '/odom', self._on_odom, 20)
        self.create_subscription(LaserScan, '/scan_raw', self._on_scan_raw, qos_profile_sensor_data)
        self.create_subscription(LaserScan, '/scan', lambda _m: self._count('scan'), qos_profile_sensor_data)
        self.create_subscription(OccupancyGrid, '/map', self._on_map, latched)
        self.create_subscription(TFMessage, '/tf', self._on_tf, 100)
        self.create_subscription(TFMessage, '/tf_static', self._on_tf_static, latched)
        self.create_subscription(Twist, '/cmd_vel', self._on_cmd_vel, 20)
        self.cmd_vel = self.create_publisher(Twist, '/cmd_vel', 10)
        self.initial_pose = self.create_publisher(PoseWithCovarianceStamped, '/initialpose', 10)
        self.motor = self.create_client(SetBool, '/motor_power')
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.motor_on = False

    # -- callbacks ------------------------------------------------------------

    def _count(self, key: str) -> None:
        self.counts[key] += 1

    def _on_odom(self, message: Odometry) -> None:
        self.odom = message
        self.counts['odom'] += 1

    def _on_scan_raw(self, message: LaserScan) -> None:
        self.scan_raw = message
        self.counts['scan_raw'] += 1

    def _on_map(self, message: OccupancyGrid) -> None:
        self.map = message
        self.counts['map'] += 1

    def _on_tf(self, message: TFMessage) -> None:
        for transform in message.transforms:
            key = (transform.header.frame_id.lstrip('/'), transform.child_frame_id.lstrip('/'))
            self.tf_edges[key] = self.tf_edges.get(key, 0) + 1

    def _on_tf_static(self, message: TFMessage) -> None:
        for transform in message.transforms:
            self.static_edges[transform.child_frame_id.lstrip('/')] = transform.header.frame_id.lstrip('/')

    def _on_cmd_vel(self, message: Twist) -> None:
        if abs(message.linear.x) > 1e-4 or abs(message.angular.z) > 1e-4:
            self.counts['cmd_vel_nonzero'] += 1

    # -- helpers --------------------------------------------------------------

    def spin_until(self, ready: Callable[[], bool], timeout: float, what: str) -> None:
        deadline = time.monotonic() + timeout
        while not ready():
            if not rclpy.ok():
                raise ProbeError(f'ROS shut down while waiting for {what}')
            if time.monotonic() > deadline:
                raise ProbeError(f'timed out after {timeout:.0f} s waiting for {what}')
            rclpy.spin_once(self, timeout_sec=0.1)

    def spin_for(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.05)

    def truth(self) -> PoseJson:
        if self.odom is None:
            raise ProbeError('no /odom received')
        p = self.odom.pose.pose
        return pose(p.position.x, p.position.y, yaw_of(p.orientation))

    def lookup(self, parent: str, child: str) -> PoseJson | None:
        try:
            t = self.tf_buffer.lookup_transform(parent, child, Time())
        except TransformException:
            return None
        return pose(t.transform.translation.x, t.transform.translation.y, yaw_of(t.transform.rotation))

    def wait_lookup(self, parent: str, child: str, timeout: float) -> PoseJson:
        found: list[PoseJson] = []

        def ready() -> bool:
            value = self.lookup(parent, child)
            if value is not None:
                found.append(value)
            return bool(found)

        self.spin_until(ready, timeout, f'TF {parent} -> {child}')
        return found[-1]

    def set_motor(self, enabled: bool) -> None:
        if not self.motor.wait_for_service(timeout_sec=15.0):
            raise ProbeError('/motor_power service did not appear')
        future = self.motor.call_async(SetBool.Request(data=enabled))
        self.spin_until(future.done, 15.0, '/motor_power response')
        response = future.result()
        if response is None or not response.success:
            raise ProbeError(f'/motor_power {enabled} was refused')
        self.motor_on = enabled

    def stop_robot(self) -> None:
        self.cmd_vel.publish(Twist())
        if self.motor_on:
            try:
                self.set_motor(False)
            except ProbeError as error:
                self.get_logger().error(f'motor shutdown failed: {error}')

    def map_stats(self) -> MapStats:
        if self.map is None:
            raise ProbeError('no /map received')
        data = self.map.data
        info = self.map.info
        return {
            'width': info.width,
            'height': info.height,
            'resolution': info.resolution,
            'origin': pose(info.origin.position.x, info.origin.position.y, yaw_of(info.origin.orientation)),
            'known_cells': sum(1 for v in data if v >= 0),
            'free_cells': sum(1 for v in data if 0 <= v < 25),
            'occupied_cells': sum(1 for v in data if v >= 65),
            'updates': self.counts['map'],
        }
