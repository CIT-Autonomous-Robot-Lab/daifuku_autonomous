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
"""waypoint_runner が停止点で止まり、パネル無しの再開要求で続きを走ることを確かめる."""

import importlib.util
from pathlib import Path
import time

from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import FollowWaypoints
from nav_msgs.msg import Path as Route
import pytest
import rclpy
from rclpy.action import ActionServer
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.task import Future
from std_msgs.msg import Empty, Int32, Int32MultiArray, String

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'waypoint_runner.py'
SPEC = importlib.util.spec_from_file_location('waypoint_runner', SOURCE)
runner_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner_module)
LATCHED = runner_module.LATCHED


def route(xs):
    msg = Route()
    msg.header.frame_id = 'map'
    for x in xs:
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.pose.position.x = float(x)
        pose.pose.orientation.w = 1.0
        msg.poses.append(pose)
    return msg


class Harness(Node):
    """vi_planner の follow_waypoints と、パネル・ゲームパッドの出すトピックの代役."""

    def __init__(self):
        super().__init__('waypoint_runner_harness')
        self.goals = []
        self.hold = False
        self.release = Future()
        self.status = ''
        self.next = None
        ActionServer(self, FollowWaypoints, 'follow_waypoints', self._execute)
        self.route_pub = self.create_publisher(Route, 'waypoint/route', LATCHED)
        self.stops_pub = self.create_publisher(Int32MultiArray, 'waypoint/stops', LATCHED)
        self.start_pub = self.create_publisher(Empty, 'waypoint/start', 10)
        self.resume_pub = self.create_publisher(Empty, 'waypoint/resume', 10)
        self.create_subscription(String, 'waypoint/status', self._on_status, LATCHED)
        self.create_subscription(Int32, 'waypoint/next', self._on_next, LATCHED)

    async def _execute(self, goal_handle):
        self.goals.append([p.pose.position.x for p in goal_handle.request.poses])
        if self.hold:
            await self.release
        goal_handle.succeed()
        return FollowWaypoints.Result()

    def _on_status(self, msg):
        self.status = msg.data

    def _on_next(self, msg):
        self.next = msg.data


@pytest.fixture
def system():
    rclpy.init()
    runner = runner_module.WaypointRunner()
    harness = Harness()
    executor = SingleThreadedExecutor()
    executor.add_node(runner)
    executor.add_node(harness)

    def spin_until(predicate, what):
        deadline = time.monotonic() + 10.0
        while not predicate():
            assert time.monotonic() < deadline, f'timed out waiting for {what}'
            executor.spin_once(timeout_sec=0.05)

    for publisher in (harness.route_pub, harness.stops_pub,
                      harness.start_pub, harness.resume_pub):
        spin_until(lambda p=publisher: p.get_subscription_count() > 0, 'runner subscriptions')
    spin_until(runner._client.server_is_ready, 'follow_waypoints server')
    harness.stops_pub.publish(Int32MultiArray(data=[1]))
    harness.route_pub.publish(route([0, 1, 2, 3]))
    spin_until(lambda: runner._stops == {1}, 'stop flags')
    spin_until(lambda: harness.status == 'Route received: 4 waypoint(s)', 'route')
    yield runner, harness, spin_until
    executor.shutdown()
    runner.destroy_node()
    harness.destroy_node()
    rclpy.shutdown()


def stopped_at_first_stop(harness, spin_until):
    harness.start_pub.publish(Empty())
    # status と next は別トピックなので、両方そろうまで待つ。
    spin_until(lambda: harness.next == 2 and harness.status.startswith('Stopped at waypoint 1'),
               'pause at the stop waypoint')
    assert harness.goals == [[0.0, 1.0]]


def test_stop_waypoint_pauses_and_resume_alone_continues(system):
    _, harness, spin_until = system
    stopped_at_first_stop(harness, spin_until)
    # パネルを通さない再開 (ゲームパッドの B と同じ経路) だけで続きを走る。
    harness.resume_pub.publish(Empty())
    spin_until(lambda: harness.next == 0 and harness.status.startswith('Succeeded'),
               'second segment')
    assert harness.goals == [[0.0, 1.0], [2.0, 3.0]]


def test_same_route_again_keeps_the_pause_but_a_new_route_drops_it(system):
    runner, harness, spin_until = system
    stopped_at_first_stop(harness, spin_until)
    # RViz の PC をつなぎ直すと同じ latch がもう一度届く。
    runner._on_route(route([0, 1, 2, 3]))
    harness.resume_pub.publish(Empty())
    spin_until(lambda: len(harness.goals) == 2, 'resumed segment')
    assert harness.goals[1] == [2.0, 3.0]
    spin_until(lambda: harness.next == 0, 'tour end')

    harness.start_pub.publish(Empty())
    spin_until(lambda: harness.next == 2, 'pause again')
    runner._on_route(route([5, 6, 7]))
    spin_until(lambda: harness.next == 0, 'pause dropped by the new route')
    harness.resume_pub.publish(Empty())
    spin_until(lambda: 'resume ignored' in harness.status, 'rejected resume')
    assert len(harness.goals) == 3


def test_resume_while_following_is_ignored(system):
    _, harness, spin_until = system
    harness.hold = True
    harness.start_pub.publish(Empty())
    spin_until(lambda: harness.goals and harness.next == -1, 'segment in flight')
    harness.resume_pub.publish(Empty())
    spin_until(lambda: harness.status == 'Already following - resume ignored', 'ignored')
    harness.release.set_result(None)
    spin_until(lambda: harness.next == 2, 'pause after release')
    assert harness.goals == [[0.0, 1.0]]
