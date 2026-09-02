#!/usr/bin/env python3
"""順路 (/follow_waypoints) を 1 回投げて、**点ごとに止まる時間**を測る。

probe.py の単発ゴール版に対する順路版。**先読み (waypoint_prefetch) が効くのは
こちらだけ** — vi_planner は follow_waypoints のゴールそのものから「次の点」を
知る (servers.rs の follow_waypoints_server が set_waypoints を呼ぶ)。単発ゴール
では並びが入らないので、先読みは注文を出さないまま何もしない。

出す値は点ごとの区間時間 (feedback の current_waypoint が変わった時刻の差) で、
これを nav.log の "value function solved in X.XXs" と突き合わせると、その点で
止まっていたのが solve なのか走行なのかが分かる。
"""
import argparse
import json
import math
import time

import rclpy
from nav2_msgs.action import FollowWaypoints
from geometry_msgs.msg import PoseStamped
from rclpy.action import ActionClient
from rclpy.node import Node


def yaw_quat(deg):
    r = math.radians(deg) / 2.0
    return math.sin(r), math.cos(r)


class Tour(Node):
    def __init__(self):
        super().__init__("tour_probe")
        self.client = ActionClient(self, FollowWaypoints, "follow_waypoints")
        self.marks = []          # (waypoint index, 経過秒)
        self.current = None
        self.t0 = None

    def on_feedback(self, fb):
        i = int(fb.feedback.current_waypoint)
        if i != self.current:
            self.current = i
            self.marks.append((i, round(time.time() - self.t0, 2)))
            self.get_logger().info(f"waypoint {i} started at {self.marks[-1][1]}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poses", required=True,
                    help='"x,y,yawdeg;x,y,yawdeg;..." (map 座標)')
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--server-timeout", type=float, default=180.0)
    ap.add_argument("--settle", type=float, default=10.0)
    args = ap.parse_args()

    rclpy.init()
    node = Tour()

    deadline = time.time() + args.settle
    while time.time() < deadline and rclpy.ok():
        rclpy.spin_once(node, timeout_sec=0.2)

    t = time.time()
    while time.time() - t < args.server_timeout and rclpy.ok():
        if node.client.wait_for_server(timeout_sec=1.0):
            break
        rclpy.spin_once(node, timeout_sec=0.1)
    else:
        print(json.dumps({"result": "NO_ACTION_SERVER"}))
        return 1

    goal = FollowWaypoints.Goal()
    for spec in args.poses.split(";"):
        x, y, yaw = (float(v) for v in spec.split(","))
        p = PoseStamped()
        p.header.frame_id = "map"
        p.header.stamp = node.get_clock().now().to_msg()
        p.pose.position.x = x
        p.pose.position.y = y
        p.pose.orientation.z, p.pose.orientation.w = yaw_quat(yaw)
        goal.poses.append(p)

    node.t0 = time.time()
    fut = node.client.send_goal_async(goal, feedback_callback=node.on_feedback)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=30.0)
    handle = fut.result()
    if handle is None or not handle.accepted:
        print(json.dumps({"result": "REJECTED"}))
        return 1

    res_fut = handle.get_result_async()
    end = time.time() + args.timeout
    while rclpy.ok() and time.time() < end and not res_fut.done():
        rclpy.spin_once(node, timeout_sec=0.2)

    if not res_fut.done():
        handle.cancel_goal_async()
        result = "TIMEOUT"
        missed = []
    else:
        status = res_fut.result().status
        result = {4: "SUCCEEDED", 5: "CANCELED", 6: "ABORTED"}.get(status, str(status))
        missed = list(res_fut.result().result.missed_waypoints)

    # 区間 = 点 i が始まってから次の点が始まるまで。最後の点は終了まで。
    spans = []
    for k, (i, t_start) in enumerate(node.marks):
        t_end = node.marks[k + 1][1] if k + 1 < len(node.marks) else round(time.time() - node.t0, 2)
        spans.append({"waypoint": i, "start_s": t_start, "span_s": round(t_end - t_start, 2)})

    print("TOUR_SUMMARY " + json.dumps({
        "result": result,
        "elapsed_s": round(time.time() - node.t0, 1),
        "spans": spans,
        "missed_waypoints": missed,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
