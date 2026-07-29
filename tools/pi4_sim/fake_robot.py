#!/usr/bin/env python3
"""Minimal差動二輪ロボット + 2D LiDAR シミュレータ (Pi4 再現ハーネス用)。

実機 (Raspberry Pi Cat + MID360) の代わりに、Nav2 スタックが必要とする最小限の
入出力だけを提供する:

  subscribe : /cmd_vel                (velocity_smoother の cmd_vel_smoothed)
  publish   : /odom                   (nav_msgs/Odometry, odom -> base_footprint)
              TF odom -> base_footprint
              /scan_raw               (LaserScan, frame_id=base_footprint)
              /sim/ground_truth       (PoseStamped, map 座標の真値; 評価用)

`lidar:=2d` で navigation.launch.py を起動すると laser_filters が
/scan_raw -> /scan を担うので、実機と同じフィルタ経路がそのまま通る。
scan の frame_id を base_footprint にしてあるのは、実機で
pointcloud_to_laserscan の target_frame が base_footprint だから
(= 追加の TF なしで済む)。

意図的に実機に寄せている点:
  * odom は真値からドリフトさせる (前進スケール誤差 + 旋回バイアス + ノイズ)。
    ドリフトが無いと emcl2 が完璧に収束してしまい「ローカルでは動く」だけの
    テストになる。map -> odom が育つので TF チェーンも実機同様になる。
  * スキャンに測距ノイズと、地図に無い障害物を混ぜられる。
  * use_sim_time は使わない (false のまま)。再現したいのは CPU 飢餓による
    実時間の破綻なので、/clock で時間を止めては意味がない。

再現できない点 (承知の上):
  * emcl2 の alpha 崩壊 (有効ビームの28%が地図の壁を貫通する問題) は
    「地図と実環境の不一致」が原因で、同じ地図をレイキャストする本シムでは
    原理的に起きない。
  * 実機側の robot_state_publisher / livox ドライバ / p2l / restamp の
    CPU 負荷は含まれない。これはコンテナの CPU quota を「Pi4 全体」ではなく
    「Pi4 のうち nav2 が実際に使えた分」に絞ることで代用する (run_pi4_sim.sh)。
"""

import math
import os
import sys

import numpy as np
import rclpy
import yaml
from geometry_msgs.msg import (
    PoseStamped,
    PoseWithCovarianceStamped,
    Quaternion,
    TransformStamped,
    Twist,
)
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import LaserScan
from tf2_ros import TransformBroadcaster


def yaw_to_quat(yaw):
    q = Quaternion()
    q.z = math.sin(yaw * 0.5)
    q.w = math.cos(yaw * 0.5)
    return q


def load_map(yaml_path, unknown_as_obstacle=False):
    """map_server と同じ規約で PGM を読み、占有ブール配列を返す。

    戻り値の occ[iy, ix] は iy=0 が地図の下端 (world y = origin_y) になるよう
    上下反転済み。

    `unknown_as_obstacle` は「未観測 (free_thresh 以上 occupied_thresh 以下)」も
    障害物として扱う。map_tsudanuma のように境界のほとんどが未観測 (205) で、
    真っ黒な占有セルが 0.4% しかない地図では、これを false にすると LiDAR が
    ほぼ何も返さない (逆に true にすると emcl2 の尤度場に無い点を返すことになる)。
    """
    with open(yaml_path) as f:
        meta = yaml.safe_load(f)
    img = os.path.join(os.path.dirname(os.path.abspath(yaml_path)), meta["image"])
    with open(img, "rb") as f:
        data = f.read()

    tokens, i = [], 0
    while len(tokens) < 4:
        while i < len(data) and data[i : i + 1].isspace():
            i += 1
        if data[i : i + 1] == b"#":
            while i < len(data) and data[i] != 0x0A:
                i += 1
            continue
        j = i
        while j < len(data) and not data[j : j + 1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j
    i += 1
    if tokens[0] != b"P5":
        raise RuntimeError(f"only binary PGM (P5) is supported: {img}")
    w, h = int(tokens[1]), int(tokens[2])
    px = np.frombuffer(data[i : i + w * h], dtype=np.uint8).reshape(h, w)

    p = (255.0 - px) / 255.0
    if int(meta.get("negate", 0)):
        p = 1.0 - p
    occ = p > float(meta.get("occupied_thresh", 0.65))
    if unknown_as_obstacle:
        occ |= p >= float(meta.get("free_thresh", 0.196))
    occ = np.flipud(occ)  # PGM は上端が最大 y
    return occ, float(meta["resolution"]), float(meta["origin"][0]), float(meta["origin"][1])


class FakeRobot(Node):
    def __init__(self):
        super().__init__("fake_robot")

        p = self.declare_parameter
        p("map_yaml", "")
        # 未観測セルも壁として扱うか (map_tsudanuma のような「境界が未観測」の地図用)。
        p("unknown_as_obstacle", False)
        p("initial_x", 0.0)
        p("initial_y", 0.0)
        p("initial_yaw", 0.0)
        p("odom_hz", 50.0)
        p("scan_hz", 10.0)
        # pointcloud_to_laserscan (config/sensors/mid360_scan.yaml) と同じ視野・分解能。
        p("angle_min", -math.pi)
        p("angle_max", math.pi)
        p("angle_increment", 0.008726646)
        p("range_min", 0.23)
        p("range_max", 10.0)
        p("range_noise_std", 0.02)
        p("dropout_rate", 0.02)
        # odom ドリフト: 前進スケール誤差・旋回スケール誤差・移動量比例ノイズ。
        p("odom_fw_scale", 1.02)
        p("odom_rot_scale", 0.99)
        p("odom_noise_fw", 0.01)
        p("odom_noise_rot", 0.01)
        # 地図に無い障害物 [x1, y1, r1, x2, y2, r2, ...]
        p("extra_obstacles", [])
        p("cmd_vel_timeout", 0.5)
        p("publish_initialpose", True)
        p("initialpose_delay", 5.0)
        # 自己位置推定が受け取るまで再送する回数と、受理とみなす距離 [m]。
        p("initialpose_max_tries", 8)
        p("initialpose_accept_dist", 2.0)
        # 初期姿勢を真値からずらして publish する (実機で RViz 手動合わせを
        # する際の誤差相当)。
        p("initialpose_error_xy", 0.0)
        p("initialpose_error_yaw", 0.0)

        g = lambda n: self.get_parameter(n).value  # noqa: E731

        map_yaml = g("map_yaml")
        if not map_yaml:
            raise RuntimeError("map_yaml parameter is required")
        self.occ, self.res, self.ox, self.oy = load_map(
            map_yaml, bool(g("unknown_as_obstacle"))
        )
        self.mh, self.mw = self.occ.shape

        self.x = float(g("initial_x"))
        self.y = float(g("initial_y"))
        self.yaw = float(g("initial_yaw"))
        # odom 系の推定姿勢 (ドリフト込み)。起動時は原点。
        self.ox_odom = 0.0
        self.oy_odom = 0.0
        self.oyaw = 0.0

        self.v = 0.0
        self.w = 0.0
        self.last_cmd = self.get_clock().now()
        self.cmd_timeout = float(g("cmd_vel_timeout"))

        self.fw_scale = float(g("odom_fw_scale"))
        self.rot_scale = float(g("odom_rot_scale"))
        self.noise_fw = float(g("odom_noise_fw"))
        self.noise_rot = float(g("odom_noise_rot"))

        self.range_min = float(g("range_min"))
        self.range_max = float(g("range_max"))
        self.noise_std = float(g("range_noise_std"))
        self.dropout = float(g("dropout_rate"))
        a0, a1, inc = float(g("angle_min")), float(g("angle_max")), float(g("angle_increment"))
        self.angles = np.arange(a0, a1, inc, dtype=np.float64)
        self.angle_min, self.angle_inc = a0, inc
        # レイのサンプル間隔は地図解像度の半分 (壁の見落としを避ける)。
        self.samples = np.arange(self.range_min, self.range_max, self.res * 0.5)
        self.cos_a = np.cos(self.angles)[:, None]
        self.sin_a = np.sin(self.angles)[:, None]

        obs = list(g("extra_obstacles") or [])
        self.obstacles = [tuple(obs[i : i + 3]) for i in range(0, len(obs) - 2, 3)]

        self.rng = np.random.default_rng(0)

        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.scan_pub = self.create_publisher(LaserScan, "/scan_raw", 10)
        self.truth_pub = self.create_publisher(PoseStamped, "/sim/ground_truth", 10)
        self.tf_bc = TransformBroadcaster(self)
        self.create_subscription(Twist, "/cmd_vel", self.on_cmd, 10)

        odom_dt = 1.0 / float(g("odom_hz"))
        self.odom_dt = odom_dt
        self.create_timer(odom_dt, self.step)
        self.create_timer(1.0 / float(g("scan_hz")), self.publish_scan)

        if bool(g("publish_initialpose")):
            qos = QoSProfile(
                depth=1,
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            )
            self.init_pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", qos)
            self.init_err = (
                float(g("initialpose_error_xy")),
                float(g("initialpose_error_yaw")),
            )
            self.create_timer(float(g("initialpose_delay")), self.publish_initialpose)
            self.init_sent = False
            self.init_tries = 0
            self.init_max_tries = int(g("initialpose_max_tries"))
            self.init_accept_dist = float(g("initialpose_accept_dist"))
            for topic in ("/mcl_pose", "/amcl_pose"):
                self.create_subscription(
                    PoseWithCovarianceStamped, topic, self.on_mcl_pose, 10
                )

        self.get_logger().info(
            f"fake_robot: map {self.mw}x{self.mh} res={self.res} "
            f"origin=({self.ox},{self.oy}) start=({self.x:.2f},{self.y:.2f},"
            f"{math.degrees(self.yaw):.0f}deg) rays={len(self.angles)} "
            f"obstacles={self.obstacles}"
        )

    # ------------------------------------------------------------------ motion
    def on_cmd(self, msg):
        self.v = msg.linear.x
        self.w = msg.angular.z
        self.last_cmd = self.get_clock().now()

    def step(self):
        now = self.get_clock().now()
        if (now - self.last_cmd).nanoseconds * 1e-9 > self.cmd_timeout:
            self.v = self.w = 0.0

        dt = self.odom_dt
        d_fw = self.v * dt
        d_rot = self.w * dt

        # 真値の更新 (壁にめり込むときは前進をキャンセルする)。
        nx = self.x + d_fw * math.cos(self.yaw + d_rot * 0.5)
        ny = self.y + d_fw * math.sin(self.yaw + d_rot * 0.5)
        if not self.is_occupied(nx, ny):
            self.x, self.y = nx, ny
        self.yaw = math.atan2(math.sin(self.yaw + d_rot), math.cos(self.yaw + d_rot))

        # odom 系はドリフトさせる (スケール誤差 + 移動量比例ノイズ)。
        o_fw = d_fw * self.fw_scale
        o_rot = d_rot * self.rot_scale
        if d_fw or d_rot:
            o_fw += self.rng.normal(0.0, self.noise_fw * abs(d_fw))
            o_rot += self.rng.normal(0.0, self.noise_rot * (abs(d_rot) + abs(d_fw)))
        self.ox_odom += o_fw * math.cos(self.oyaw + o_rot * 0.5)
        self.oy_odom += o_fw * math.sin(self.oyaw + o_rot * 0.5)
        self.oyaw = math.atan2(math.sin(self.oyaw + o_rot), math.cos(self.oyaw + o_rot))

        stamp = now.to_msg()
        t = TransformStamped()
        t.header.stamp = stamp
        t.header.frame_id = "odom"
        t.child_frame_id = "base_footprint"
        t.transform.translation.x = self.ox_odom
        t.transform.translation.y = self.oy_odom
        t.transform.rotation = yaw_to_quat(self.oyaw)
        self.tf_bc.sendTransform(t)

        od = Odometry()
        od.header.stamp = stamp
        od.header.frame_id = "odom"
        od.child_frame_id = "base_footprint"
        od.pose.pose.position.x = self.ox_odom
        od.pose.pose.position.y = self.oy_odom
        od.pose.pose.orientation = yaw_to_quat(self.oyaw)
        od.twist.twist.linear.x = self.v
        od.twist.twist.angular.z = self.w
        self.odom_pub.publish(od)

        gt = PoseStamped()
        gt.header.stamp = stamp
        gt.header.frame_id = "map"
        gt.pose.position.x = self.x
        gt.pose.position.y = self.y
        gt.pose.orientation = yaw_to_quat(self.yaw)
        self.truth_pub.publish(gt)

    # ------------------------------------------------------------------ sensor
    def is_occupied(self, x, y):
        ix = int((x - self.ox) / self.res)
        iy = int((y - self.oy) / self.res)
        if not (0 <= ix < self.mw and 0 <= iy < self.mh):
            return True
        return bool(self.occ[iy, ix])

    def publish_scan(self):
        # 全レイ x 全サンプル点をまとめて地図に当てる (numpy 一括)。
        xs = self.x + self.samples[None, :] * np.cos(self.yaw + self.angles)[:, None]
        ys = self.y + self.samples[None, :] * np.sin(self.yaw + self.angles)[:, None]
        ix = ((xs - self.ox) / self.res).astype(np.int32)
        iy = ((ys - self.oy) / self.res).astype(np.int32)
        inside = (ix >= 0) & (ix < self.mw) & (iy >= 0) & (iy < self.mh)
        hit = np.zeros(ix.shape, dtype=bool)
        hit[inside] = self.occ[iy[inside], ix[inside]]
        for cx, cy, r in self.obstacles:
            hit |= (xs - cx) ** 2 + (ys - cy) ** 2 <= r * r

        any_hit = hit.any(axis=1)
        first = hit.argmax(axis=1)
        ranges = np.where(any_hit, self.samples[first], np.inf)

        finite = np.isfinite(ranges)
        if self.noise_std > 0.0:
            ranges[finite] += self.rng.normal(0.0, self.noise_std, finite.sum())
        if self.dropout > 0.0:
            ranges[self.rng.random(ranges.shape) < self.dropout] = np.inf

        msg = LaserScan()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "base_footprint"
        msg.angle_min = float(self.angle_min)
        msg.angle_increment = float(self.angle_inc)
        msg.angle_max = float(self.angle_min + self.angle_inc * (len(ranges) - 1))
        msg.time_increment = 0.0
        msg.scan_time = 0.1
        msg.range_min = float(self.range_min)
        msg.range_max = float(self.range_max)
        msg.ranges = [float(r) for r in ranges]
        self.scan_pub.publish(msg)

    def publish_initialpose(self):
        # 1 回だけだと取りこぼす: emcl2 は地図とスキャンが揃うまで MCL を初期化せず、
        # /initialpose の購読は volatile なので、それ以前に流した 1 発は捨てられる。
        # map_tsudanuma (23.5MB) では地図の受信自体が遅く、実際に取りこぼして
        # emcl2 が初期姿勢 (0,0) のまま動き続けた。自己位置が実際に近傍へ来るまで
        # (または上限回数まで) 送り直す。ロボットはゴール送信までは動かないので、
        # この間の再送は「初期姿勢の再提示」であって位置の与え直しにはならない。
        if self.init_sent or self.init_tries >= self.init_max_tries:
            return
        self.init_tries += 1
        exy, eyaw = self.init_err
        msg = PoseWithCovarianceStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"
        msg.pose.pose.position.x = self.x + exy
        msg.pose.pose.position.y = self.y + exy
        msg.pose.pose.orientation = yaw_to_quat(self.yaw + eyaw)
        msg.pose.covariance[0] = 0.25
        msg.pose.covariance[7] = 0.25
        msg.pose.covariance[35] = 0.07
        self.init_pub.publish(msg)
        self.get_logger().info(
            f"published /initialpose ({self.init_tries}/{self.init_max_tries})"
        )

    def on_mcl_pose(self, msg):
        """自己位置が初期姿勢の近傍に来たら /initialpose の再送を止める。"""
        d = math.hypot(
            msg.pose.pose.position.x - self.x, msg.pose.pose.position.y - self.y
        )
        if d <= self.init_accept_dist:
            if not self.init_sent:
                self.get_logger().info(f"localization accepted initialpose (d={d:.2f}m)")
            self.init_sent = True


def main():
    rclpy.init()
    node = FakeRobot()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
