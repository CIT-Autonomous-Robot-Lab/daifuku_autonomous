# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Drive the real planner with future-dated AMCL-like TF in isolated DDS."""

from contextlib import ExitStack
import math
import os
import signal
import subprocess
import time
from collections.abc import Callable

from geometry_msgs.msg import PoseStamped, TransformStamped, Twist
from nav_msgs.msg import OccupancyGrid
import rclpy
from rclpy.duration import Duration
from rclpy.qos import DurabilityPolicy, QoSProfile
from tf2_ros import TransformBroadcaster


def stop_process(child: subprocess.Popen) -> None:
    """Stop the ros2 run process and the node it started."""
    if child.poll() is not None:
        return
    os.killpg(child.pid, signal.SIGINT)
    try:
        child.wait(timeout=4)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=4)


def main() -> None:
    """Verify wait, follow, stop and recovery without a motor or estimator."""
    if os.environ.get('ROS_LOCALHOST_ONLY') != '1' or os.environ.get('ROS_DOMAIN_ID') != '77':
        raise RuntimeError('Set ROS_DOMAIN_ID=77 and ROS_LOCALHOST_ONLY=1 for this test.')
    rclpy.init()
    node = rclpy.create_node('tf_follow_probe')
    map_pub = node.create_publisher(
        OccupancyGrid, 'map', QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
    )
    goal_pub = node.create_publisher(PoseStamped, 'goal_pose', 1)
    broadcaster = TransformBroadcaster(node)
    poses: list[PoseStamped] = []
    speeds: list[tuple[float, bool]] = []
    node.create_subscription(PoseStamped, 'viola_pose', poses.append, 10)

    def receive_cmd(msg: Twist) -> None:
        speeds.append((time.monotonic(), abs(msg.linear.x) + abs(msg.angular.z) > 1e-6))

    node.create_subscription(Twist, 'cmd_vel', receive_cmd, 10)
    grid = OccupancyGrid()
    grid.header.frame_id = 'map'
    grid.info.width = grid.info.height = 40
    grid.info.resolution = 0.25
    grid.info.origin.orientation.w = 1.0
    grid.data = [0] * (grid.info.width * grid.info.height)
    last_map_sent = 0.0

    def pump_until(condition: Callable[[], bool], send_map: bool, timeout: float = 8.0) -> None:
        nonlocal last_map_sent
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            now = node.get_clock().now()
            edges = []
            for parent, child, offset in (
                ('odom', 'base_footprint', 1.5), ('map', 'odom', 1.0),
            ):
                if parent == 'map' and not send_map:
                    continue
                edge = TransformStamped()
                # Match the shipped AMCL transform_tolerance, but never postdate odometry.
                edge.header.stamp = (now + Duration(seconds=int(parent == 'map'))).to_msg()
                edge.header.frame_id, edge.child_frame_id = parent, child
                edge.transform.translation.x = edge.transform.translation.y = offset
                edge.transform.rotation.w = 1.0
                edges.append(edge)
            if send_map:
                last_map_sent = time.monotonic()
            broadcaster.sendTransform(edges)
            rclpy.spin_once(node, timeout_sec=0.02)
            time.sleep(0.015)
        raise AssertionError(f'condition not reached: poses={len(poses)}, speeds={speeds[-6:]}')

    def moving() -> bool:
        return bool(speeds and speeds[-1][1])

    with ExitStack() as cleanup:
        cleanup.callback(rclpy.try_shutdown)
        cleanup.callback(node.destroy_node)
        for command in (
            ['ros2', 'run', 'daifuku_navigation', 'tf_pose.py'],
            ['ros2', 'run', 'vi_planner', 'vi_planner', '--ros-args',
             '-p', 'standalone:=true', '-p', 'pose_topic:=vi_pose_from_tf',
             '-p', 'external_pose_max_age_sec:=0.5', '-p', 'value_publish_interval_ms:=-1'],
        ):
            child = subprocess.Popen(command, start_new_session=True)
            cleanup.callback(stop_process, child)

        map_pub.publish(grid)
        pump_until(lambda: goal_pub.get_subscription_count() > 0, send_map=False)
        goal = PoseStamped()
        goal.header.frame_id = 'map'
        goal.pose.position.x, goal.pose.position.y = 6.0, 2.5
        goal.pose.orientation.w = 1.0
        goal_pub.publish(goal)
        pump_until(lambda: len(speeds) >= 3, send_map=False)
        assert not any(active for _, active in speeds), 'moved without a complete TF chain'

        pump_until(lambda: moving() and bool(poses), send_map=True)
        position = poses[-1].pose.position
        assert math.isclose(position.x, 2.5) and math.isclose(position.y, 2.5)

        pump_until(lambda: not moving(), send_map=False, timeout=2.0)
        elapsed = speeds[-1][0] - last_map_sent
        # Allow the adapter/plan ticks and ROS scheduling, not the 1 s AMCL tolerance.
        assert 0.0 <= elapsed < 0.85, f'planning stopped after {elapsed:.3f} s'
        first_stop = len(speeds)
        pump_until(lambda: len(speeds) > first_stop + 4, send_map=False)
        assert not any(active for _, active in speeds[first_stop:]), 'stale TF restarted motion'
        pump_until(moving, send_map=True)
        print(f'PASS: stopped {elapsed:.3f} s after the last future-dated map TF and recovered')


if __name__ == '__main__':
    main()
