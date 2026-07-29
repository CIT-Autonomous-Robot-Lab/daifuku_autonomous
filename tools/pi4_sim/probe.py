#!/usr/bin/env python3
"""NavigateToPose を1回投げて、どこでチェーンが切れたかを数値で出す。

実機で取った診断プローブ (plan=0 / cmd_vel_nav=0 / cmd_vel=49 → ABORTED) と
同じ指標をローカルシムでも取れるようにしたもの。加えて Pi4 4GB 再現で本命に
なるメモリを継続サンプリングする (vi_global_planner / vi_local_planner の RSS、
コンテナの memory.current / memory.events)。

  python3 probe.py --goal-x 4.28 --goal-y -2.92 --goal-yaw -24 --timeout 300
"""

import argparse
import json
import os
import re
import sys
import time

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import OccupancyGrid, Path
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy

STATUS = {
    GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
    GoalStatus.STATUS_ABORTED: "ABORTED",
    GoalStatus.STATUS_CANCELED: "CANCELED",
}

WATCH = ("vi_global_planner", "vi_local_planner", "controller_server",
         "planner_server", "bt_navigator", "emcl2", "map_server")


def yaw_quat(deg):
    import math
    r = math.radians(deg)
    return math.sin(r * 0.5), math.cos(r * 0.5)


def proc_rss_mb():
    """/proc を舐めて監視対象プロセスの RSS [MB] を返す。

    `<name>` が VmRSS、`<name>:anon` / `<name>:file` がその内訳。
    VI の compact 経路は確定出力を mmap ファイルに置くので、常駐の大半が
    file 側 (= メモリ逼迫時に回収できるページキャッシュ) になる。
    OOM killer に効くのは anon 側なので、この 2 つは分けて見ること。
    """
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().decode("utf-8", "replace").replace("\0", " ")
            name = next((w for w in WATCH if w in cmd), None)
            if name is None:
                continue
            with open(f"/proc/{pid}/status") as f:
                status = f.read()
            for key, suffix in (("VmRSS", ""), ("RssAnon", ":anon"), ("RssFile", ":file")):
                m = re.search(rf"^{key}:\s+(\d+) kB", status, re.M)
                if m:
                    k = name + suffix
                    out[k] = max(out.get(k, 0.0), int(m.group(1)) / 1024.0)
        except (OSError, StopIteration):
            continue
    return out


def cgroup_mem():
    """cgroup v2 のメモリ使用量 [MB] と OOM/throttle カウンタ。"""
    base = "/sys/fs/cgroup"
    res = {}
    try:
        with open(f"{base}/memory.current") as f:
            res["mem_mb"] = int(f.read()) / 1048576.0
        with open(f"{base}/memory.max") as f:
            v = f.read().strip()
            res["mem_max_mb"] = None if v == "max" else int(v) / 1048576.0
        with open(f"{base}/memory.events") as f:
            res["events"] = dict(
                (k, int(v)) for k, v in (ln.split() for ln in f.read().split("\n") if ln)
            )
    except OSError:
        pass
    try:
        with open(f"{base}/cpu.stat") as f:
            st = dict((k, int(v)) for k, v in (ln.split() for ln in f.read().split("\n") if ln))
        res["throttled_usec"] = st.get("throttled_usec")
        res["nr_throttled"] = st.get("nr_throttled")
    except OSError:
        pass
    return res


class Probe(Node):
    def __init__(self, args):
        super().__init__("pi4_sim_probe")
        self.args = args
        # 注意: planner:=vi では /plan に publisher がいない (vi_global_planner は
        # Path を action の Result で返すだけで、nav2 の planner_server のように
        # /plan を publish しない)。実機プローブの plan=0 はこれで説明がつくので、
        # VI が実際に解けたかは value_function / local_value_function で見る。
        self.counts = dict(
            plan=0, cmd_vel_nav=0, cmd_vel=0, mcl_pose=0, truth=0,
            value_function=0, local_value_function=0, local_window_value=0,
        )
        self.first_plan_t = None
        self.t0 = time.time()
        self.last_truth = None
        self.last_mcl = None
        self.peak_rss = {}
        self.peak_mem_mb = 0.0
        self.mem_events = {}
        self.feedback_n = 0
        self.last_feedback = None

        def bump(key):
            def cb(msg):
                self.counts[key] += 1
                if key == "plan" and self.first_plan_t is None:
                    self.first_plan_t = time.time() - self.send_t
                    self.get_logger().info(
                        f"first /plan after {self.first_plan_t:.1f}s "
                        f"({len(msg.poses)} poses)"
                    )
                if key == "truth":
                    self.last_truth = (msg.pose.position.x, msg.pose.position.y)
                if key == "mcl_pose":
                    self.last_mcl = (msg.pose.pose.position.x, msg.pose.pose.position.y)
            return cb

        self.create_subscription(Path, "/plan", bump("plan"), 10)
        self.create_subscription(Twist, "/cmd_vel_nav", bump("cmd_vel_nav"), 10)
        self.create_subscription(Twist, "/cmd_vel", bump("cmd_vel"), 10)
        self.create_subscription(
            PoseWithCovarianceStamped, "/mcl_pose", bump("mcl_pose"),
            QoSProfile(depth=10, reliability=QoSReliabilityPolicy.RELIABLE,
                       durability=QoSDurabilityPolicy.VOLATILE))
        self.create_subscription(PoseStamped, "/sim/ground_truth", bump("truth"), 10)
        # VI の solve 進捗/完了 (transient_local + reliable で publish される)。
        vf_qos = QoSProfile(
            depth=1,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        for topic in ("value_function", "local_value_function", "local_window_value"):
            self.create_subscription(OccupancyGrid, f"/{topic}", bump(topic), vf_qos)

        self.create_timer(1.0, self.sample)
        self.client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self.send_t = time.time()

    def sample(self):
        for k, v in proc_rss_mb().items():
            self.peak_rss[k] = max(self.peak_rss.get(k, 0.0), v)
        c = cgroup_mem()
        if "mem_mb" in c:
            self.peak_mem_mb = max(self.peak_mem_mb, c["mem_mb"])
        if "events" in c:
            self.mem_events = c["events"]
        self.cgroup = c

    def on_feedback(self, fb):
        self.feedback_n += 1
        f = fb.feedback
        self.last_feedback = dict(
            distance_remaining=round(float(f.distance_remaining), 2),
            number_of_recoveries=int(f.number_of_recoveries),
            navigation_time=round(f.navigation_time.sec + f.navigation_time.nanosec * 1e-9, 1),
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal-x", type=float, required=True)
    ap.add_argument("--goal-y", type=float, required=True)
    ap.add_argument("--goal-yaw", type=float, default=0.0)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--server-timeout", type=float, default=180.0)
    ap.add_argument("--settle", type=float, default=10.0, help="ゴール送信前の待機 [s]")
    args = ap.parse_args()

    rclpy.init()
    node = Probe(args)

    # 立ち上がり待ち (Pi4 では bringup 自体が数十秒かかる)。
    deadline = time.time() + args.settle
    while time.time() < deadline and rclpy.ok():
        rclpy.spin_once(node, timeout_sec=0.2)

    node.get_logger().info("waiting for navigate_to_pose action server ...")
    t = time.time()
    ok = False
    while time.time() - t < args.server_timeout and rclpy.ok():
        if node.client.wait_for_server(timeout_sec=1.0):
            ok = True
            break
        rclpy.spin_once(node, timeout_sec=0.1)
    if not ok:
        print(json.dumps({"result": "NO_ACTION_SERVER"}))
        return 1
    node.get_logger().info(f"action server up after {time.time()-t:.1f}s")

    goal = NavigateToPose.Goal()
    goal.pose.header.frame_id = "map"
    goal.pose.header.stamp = node.get_clock().now().to_msg()
    goal.pose.pose.position.x = args.goal_x
    goal.pose.pose.position.y = args.goal_y
    z, w = yaw_quat(args.goal_yaw)
    goal.pose.pose.orientation.z = z
    goal.pose.pose.orientation.w = w

    node.get_logger().info(
        f"goal stamp={goal.pose.header.stamp.sec}.{goal.pose.header.stamp.nanosec:09d} "
        f"wall={time.time():.3f}"
    )
    node.send_t = time.time()
    node.counts = dict.fromkeys(node.counts, 0)
    send_future = node.client.send_goal_async(goal, feedback_callback=node.on_feedback)
    rclpy.spin_until_future_complete(node, send_future, timeout_sec=30.0)
    handle = send_future.result()
    if handle is None or not handle.accepted:
        print(json.dumps({"result": "GOAL_REJECTED"}))
        return 1

    res_future = handle.get_result_async()
    end = time.time() + args.timeout
    while rclpy.ok() and not res_future.done() and time.time() < end:
        rclpy.spin_once(node, timeout_sec=0.2)

    if res_future.done():
        status = STATUS.get(res_future.result().status, str(res_future.result().status))
    else:
        status = "TIMEOUT"
        handle.cancel_goal_async()
        rclpy.spin_once(node, timeout_sec=2.0)

    summary = {
        "result": status,
        "elapsed_s": round(time.time() - node.send_t, 1),
        "first_plan_s": None if node.first_plan_t is None else round(node.first_plan_t, 1),
        "counts": node.counts,
        "feedback_n": node.feedback_n,
        "last_feedback": node.last_feedback,
        "mcl": node.last_mcl,
        "ground_truth": node.last_truth,
        "peak_rss_mb": {k: round(v, 1) for k, v in sorted(node.peak_rss.items())},
        "peak_cgroup_mem_mb": round(node.peak_mem_mb, 1),
        "cgroup": getattr(node, "cgroup", {}),
        "loadavg": open("/proc/loadavg").read().split()[:3],
    }
    print("PROBE_SUMMARY " + json.dumps(summary, ensure_ascii=False))
    node.destroy_node()
    rclpy.shutdown()
    return 0 if status == "SUCCEEDED" else 2


if __name__ == "__main__":
    sys.exit(main())
