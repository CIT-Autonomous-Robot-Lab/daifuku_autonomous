"""Record actual ROS navigation data for an explicitly labelled telemetry video."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import TextIO

from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from nav_msgs.msg import Path as RosPath
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, LaserScan

from vtc_contract import PoseJson
from vtc_probe import BASE, LIDAR, Probe, pose, yaw_of


class NavigationRecorder:
    """Subscriptions and a ROS timer owned by the enclosing recording file."""

    def __init__(self, probe: Probe, stream: TextIO, goal: PoseJson, directory: Path) -> None:
        self.probe = probe
        self.stream = stream
        self.goal = goal
        self.began = time.monotonic()
        self.state = 'localizing'
        self.estimate: PoseJson | None = None
        self.scan: LaserScan | None = None
        self.path: RosPath | None = None
        self.linear = 0.0
        self.angular = 0.0
        self.metadata_written = False
        self.mcl_pose_count = 0
        self.path_topics: set[str] = set()
        self.directory = directory
        self.camera: Image | None = None
        self.camera_count = 0
        self.saved_camera_count = 0
        self.camera_file: str | None = None
        probe.create_subscription(PoseWithCovarianceStamped, '/mcl_pose', self.on_pose, 20)
        probe.create_subscription(LaserScan, '/scan', self.on_scan, qos_profile_sensor_data)
        probe.create_subscription(Twist, '/cmd_vel', self.on_velocity, 20)
        probe.create_subscription(Image, '/camera/color/image_raw', self.on_camera, qos_profile_sensor_data)
        self.timer = probe.create_timer(0.1, self.frame)

    def on_pose(self, message: PoseWithCovarianceStamped) -> None:
        value = message.pose.pose
        self.estimate = pose(value.position.x, value.position.y, yaw_of(value.orientation))
        self.mcl_pose_count += 1

    def on_scan(self, message: LaserScan) -> None:
        self.scan = message

    def on_velocity(self, message: Twist) -> None:
        self.linear, self.angular = message.linear.x, message.angular.z

    def on_path(self, message: RosPath) -> None:
        self.path = message

    def on_camera(self, message: Image) -> None:
        self.camera = message
        self.camera_count += 1

    def frame(self) -> None:
        probe = self.probe
        grid = probe.map
        if grid is None or probe.odom is None:
            return
        if not self.metadata_written:
            nodes = sorted(probe.get_node_names())
            if not {'vi_planner', 'emcl2'}.issubset(nodes):
                return
            origin = grid.info.origin
            self.stream.write(json.dumps({
                'type': 'metadata',
                'map': {
                    'width': grid.info.width, 'height': grid.info.height,
                    'resolution': grid.info.resolution,
                    'origin': pose(origin.position.x, origin.position.y, yaw_of(origin.orientation)),
                    'data': list(grid.data),
                },
                'goal': self.goal, 'nodes': nodes,
                'planner': 'vi', 'localization': 'emcl2',
                'truth_frame': 'world',
                'pose_publishers': [
                    info.node_name for info in probe.get_publishers_info_by_topic('/mcl_pose')
                ],
            }) + '\n')
            self.metadata_written = True
        for topic, types in probe.get_topic_names_and_types():
            if 'nav_msgs/msg/Path' in types and topic not in self.path_topics:
                probe.create_subscription(RosPath, topic, self.on_path, 10)
                self.path_topics.add(topic)
        points: list[PoseJson] = []
        scan = self.scan
        lidar = probe.lookup('map', LIDAR)
        if scan is not None and lidar is not None:
            for index, distance in enumerate(scan.ranges):
                if math.isfinite(distance) and scan.range_min <= distance <= scan.range_max:
                    angle = lidar['yaw'] + scan.angle_min + index * scan.angle_increment
                    points.append(pose(
                        lidar['x'] + distance * math.cos(angle),
                        lidar['y'] + distance * math.sin(angle), 0.0,
                    ))
        path = [] if self.path is None else [
            pose(item.pose.position.x, item.pose.position.y, yaw_of(item.pose.orientation))
            for item in self.path.poses
        ]
        camera = self.camera
        if camera is not None and self.camera_count != self.saved_camera_count:
            if camera.encoding != 'rgb8':
                raise ValueError(f'Gazebo RGB recording requires rgb8, got {camera.encoding}')
            self.camera_file = f'camera/{self.camera_count:06d}.ppm'
            target = self.directory / self.camera_file
            target.parent.mkdir(exist_ok=True)
            with target.open('wb') as output:
                output.write(f'P6\n{camera.width} {camera.height}\n255\n'.encode('ascii'))
                for row in range(camera.height):
                    start = row * camera.step
                    output.write(bytes(camera.data[start:start + camera.width * 3]))
            self.saved_camera_count = self.camera_count
        self.stream.write(json.dumps({
            'type': 'frame', 't': time.monotonic() - self.began,
            'sim_time': probe.get_clock().now().nanoseconds / 1e9,
            'truth': probe.truth(), 'estimate': self.estimate,
            'scan': points, 'path': path,
            'cmd_vel': {'linear': self.linear, 'angular': self.angular},
            'state': self.state, 'mcl_pose_count': self.mcl_pose_count,
            'camera': self.camera_file,
        }) + '\n')
        self.stream.flush()

    def finish(self, action_status: int) -> None:
        self.state = 'SUCCEEDED' if action_status == 4 else f'action status {action_status}'
        self.frame()
        self.stream.write(json.dumps({'type': 'result', 'action_status': action_status}) + '\n')
        self.timer.cancel()
