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
"""停止点で区切った巡回を機体の上で進める.

RViz のウェイポイントパネルは順路と停止点を latch で渡し、開始と再開を出すだけにする。
区間の切り出し、follow_waypoints の結果の受け取り、どの停止点で止まっているかは
ここが持つ。RViz の PC を外していても結果を取りこぼさず、ゲームパッドの B
(waypoint/resume) で停止点から再開できる。
"""

from action_msgs.msg import GoalStatus
from nav2_msgs.action import FollowWaypoints
from nav_msgs.msg import Path
import rclpy
from rclpy.action import ActionClient
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import Empty, Int32, Int32MultiArray, String

# 遅れて繋いだ購読者 (つなぎ直した RViz) にも今の状態を見せる。
LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
# waypoint/next の値。走行中は次に送る点が決まっていない。
FOLLOWING = -1


def route_key(path: Path) -> tuple:
    """順路の同一性. 同じ順路を受け直しても止まっている位置を捨てないために比べる."""
    return (path.header.frame_id, tuple(
        (p.pose.position.x, p.pose.position.y, p.pose.position.z,
         p.pose.orientation.x, p.pose.orientation.y, p.pose.orientation.z,
         p.pose.orientation.w)
        for p in path.poses))


class WaypointRunner(Node):
    """順路を停止点で区切り、1 区間ずつ follow_waypoints へ送る."""

    def __init__(self) -> None:
        super().__init__('waypoint_runner')
        self._route: list = []
        self._route_key: tuple | None = None
        self._stops: set[int] = set()
        # 次に送る区間の先頭。0 より大きいあいだは停止点で止まっている。
        self._next = 0
        self._segment = (0, 0)
        self._segment_key: tuple | None = None
        self._following = False
        self._feedback_index: int | None = None
        self._client = ActionClient(self, FollowWaypoints, 'follow_waypoints')
        self._status_pub = self.create_publisher(String, 'waypoint/status', LATCHED)
        self._next_pub = self.create_publisher(Int32, 'waypoint/next', LATCHED)
        self.create_subscription(Path, 'waypoint/route', self._on_route, LATCHED)
        self.create_subscription(Int32MultiArray, 'waypoint/stops', self._on_stops, LATCHED)
        self.create_subscription(Empty, 'waypoint/start', self._on_start, 10)
        self.create_subscription(Empty, 'waypoint/resume', self._on_resume, 10)
        self._report('Waiting for a route')

    def _report(self, status: str) -> None:
        self.get_logger().info(status)
        self._status_pub.publish(String(data=status))
        self._next_pub.publish(Int32(data=FOLLOWING if self._following else self._next))

    def _on_route(self, msg: Path) -> None:
        key = route_key(msg)
        # RViz の PC をつなぎ直すと同じ latch がもう一度届く。それで再開位置を捨てない。
        if key == self._route_key:
            return
        self._route = list(msg.poses)
        self._route_key = key
        self._next = 0
        self._report(f'Route received: {len(self._route)} waypoint(s)')

    def _on_stops(self, msg: Int32MultiArray) -> None:
        # 止まっている位置は動かさない。効くのは次に送る区間から。
        self._stops = set(msg.data)

    def _on_start(self, _msg: Empty) -> None:
        if self._following:
            self._report('Already following - start ignored')
            return
        self._next = 0
        self._send_segment()

    def _on_resume(self, _msg: Empty) -> None:
        if self._following:
            self._report('Already following - resume ignored')
            return
        if self._next == 0 or self._next >= len(self._route):
            self._report('Not paused at a stop waypoint - resume ignored')
            return
        self._send_segment()

    def _send_segment(self) -> None:
        if not self._route:
            self._report('Error: no route received')
            return
        if not self._client.server_is_ready():
            self._report('Error: follow_waypoints action server is unavailable')
            return
        # 停止点で区切る。止まるのは区間の終わりにゴールが SUCCEEDED で返るからで、
        # vi_planner 側には停止の概念を持たせていない。
        end = self._next
        while end + 1 < len(self._route) and end not in self._stops:
            end += 1
        self._segment = (self._next, end)
        self._segment_key = self._route_key
        goal = FollowWaypoints.Goal()
        goal.poses = self._route[self._next:end + 1]
        stamp = self.get_clock().now().to_msg()
        for pose in goal.poses:
            pose.header.stamp = stamp
        self._following = True
        self._feedback_index = None
        self._report(f'Sending waypoints {self._next}-{end}')
        self._client.send_goal_async(goal, feedback_callback=self._on_feedback) \
            .add_done_callback(self._on_goal_response)

    def _on_goal_response(self, future) -> None:
        handle = future.result()
        if not handle.accepted:
            self._following = False
            self._report('Error: FollowWaypoints goal was rejected')
            return
        handle.get_result_async().add_done_callback(self._on_result)

    def _on_feedback(self, feedback) -> None:
        # current_waypoint は区間の中での番号。点が変わったときだけ出す。
        index = self._segment[0] + feedback.feedback.current_waypoint
        if index != self._feedback_index:
            self._feedback_index = index
            self._report(f'Following waypoint {index + 1} / {len(self._route)}')

    def _on_result(self, future) -> None:
        response = future.result()
        missed = len(response.result.missed_waypoints)
        suffix = f' ({missed} waypoint(s) missed)' if missed else ''
        start, end = self._segment
        self._following = False
        # 走っているあいだに順路が差し替わったら、区間の番号は新しい順路を指さない。
        if (response.status == GoalStatus.STATUS_SUCCEEDED
                and self._segment_key == self._route_key and end + 1 < len(self._route)):
            self._next = end + 1
            self._report(f'Stopped at waypoint {end} - press Resume (B) to continue{suffix}')
            return
        self._next = 0
        text = {
            GoalStatus.STATUS_SUCCEEDED: 'Succeeded',
            GoalStatus.STATUS_CANCELED: 'Canceled',
            GoalStatus.STATUS_ABORTED: 'Failed (aborted)',
        }.get(response.status, 'Failed (unknown result)')
        self._report(f'{text} at waypoints {start}-{end}{suffix}')


def main() -> None:
    """Run the waypoint runner until launch shuts down."""
    rclpy.init()
    node = WaypointRunner()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
