# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Run every original regional waypoint and score physical truth without feeding it back."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import time
from typing import TypedDict

from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_msgs.action import FollowWaypoints
from nav2_msgs.srv import LoadMap
from nav_msgs.msg import Odometry
import rclpy
from rclpy.action import ActionClient
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan, PointCloud2

from course_contract import (
    MAX_WAYPOINTS, CourseTransform, WaypointProgress, initial_localization_ready, regional_planner_argv,
)
from course_process import planner_command, start_planner, stop_planner
from vtc_probe import Probe, ProbeError


class CloudGeometry(TypedDict):
    frame: str
    width: int
    height: int
    finite_points: int
    min_z: float
    max_z: float


class Arrival(TypedDict):
    index: int
    region: int
    goal: list[float]
    truth: tuple[float, float, float]
    physical_error_m: float
    arrival_evidence: str
    simulator_position_m: list[float]
    simulator_orientation_xyzw: list[float]


class Group(TypedDict):
    start: int
    end: int
    region: int
    action_status: int
    missed_waypoints: list[int]


class CourseReport(TypedDict, total=False):
    status: str
    total_goals: int
    end_index: int
    completed: list[Arrival]
    groups: list[Group]
    live_truth_localization: bool
    physical_goal_error_limit_m: float
    planned_length_m: float
    mid360: CloudGeometry
    error: str
    current_index: int
    driven_distance_m: float
    wall_seconds: float


class PhysicsSample(TypedDict):
    clock: float
    wall: float
    xyz: list[float]
    quaternion: list[float]
    linear: list[float]
    angular: list[float]


def waypoint_pose(goal: list[float]) -> PoseStamped:
    """Convert JSON numbers to the strict ROS floating-point message fields."""
    target = PoseStamped()
    target.header.frame_id = "map"
    target.pose.position.x, target.pose.position.y = (float(value) for value in goal[:2])
    target.pose.orientation.z = math.sin(goal[2]/2)
    target.pose.orientation.w = math.cos(goal[2]/2)
    return target


def cloud_geometry(message: PointCloud2) -> CloudGeometry:
    """Inspect actual XYZ payload once, not a planar scan republished under a new type."""
    offsets = {field.name: field.offset for field in message.fields}
    if not all(name in offsets for name in ("x", "y", "z")):
        raise ProbeError("Mid-360 cloud has no XYZ fields")
    types = {field.name: field.datatype for field in message.fields}
    if any(types[name] != 7 for name in ("x", "y", "z")):
        raise ProbeError("Mid-360 XYZ fields must use FLOAT32")
    unpack = struct.Struct(">f" if message.is_bigendian else "<f").unpack_from
    heights = []
    for row in range(message.height):
        for column in range(message.width):
            offset = row*message.row_step + column*message.point_step
            xyz = [unpack(message.data, offset + offsets[name])[0] for name in ("x", "y", "z")]
            if all(math.isfinite(value) for value in xyz):
                heights.append(xyz[2])
    if not heights or max(heights) - min(heights) < 0.1:
        raise ProbeError("Mid-360 cloud has insufficient finite three-dimensional geometry")
    return {
        "frame": message.header.frame_id, "width": message.width, "height": message.height,
        "finite_points": len(heights), "min_z": min(heights), "max_z": max(heights),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--transform", type=Path, required=True)
    parser.add_argument("--goal-timeout", type=float, default=1800)
    parser.add_argument("--startup-timeout", type=float, default=900)
    parser.add_argument("--end-index", type=int)
    args = parser.parse_args()
    maps = Path(get_package_share_directory("daifuku_maps")) / "maps" / "tsukuba2025"
    course_path = maps / "course.json"
    course = json.loads(course_path.read_text())
    transform_data = json.loads(args.transform.read_text())
    transform = CourseTransform(
        *transform_data["source_origin_m"][:2], transform_data["source_yaw_rad"],
    )
    if course["course_year"] != 2025 or not 2 <= len(course["goals"]) <= MAX_WAYPOINTS:
        parser.error(f"expected a Tsukuba2025 course with at most {MAX_WAYPOINTS} waypoints")
    if args.end_index is None:
        args.end_index = len(course["goals"])
    if not 0 < args.end_index <= len(course["goals"]):
        parser.error("end-index must be within the original course")
    targets = [waypoint_pose(goal) for goal in course["goals"]]
    source_spawn = [float(value) for value in course["spawn"][:2]] + [float(course["spawn_yaw"])]
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "course.json").write_bytes(course_path.read_bytes())
    report: CourseReport = {
        "status": "FAIL", "total_goals": len(course["goals"]), "end_index": args.end_index,
        "completed": [], "groups": [], "live_truth_localization": False,
        "physical_goal_error_limit_m": min(0.6, course["acceptance"]["maximum_physical_goal_error_m"]),
        "planned_length_m": course["length_m"],
    }
    rclpy.init()
    probe = Probe()
    physics: list[PhysicsSample] = []
    physics_recording = True

    def record_physics(message: Odometry) -> None:
        p, q, v = message.pose.pose.position, message.pose.pose.orientation, message.twist.twist
        if physics_recording:
            physics.append({
                "clock": message.header.stamp.sec + message.header.stamp.nanosec*1e-9,
                "wall": time.monotonic(), "xyz": [p.x, p.y, p.z],
                "quaternion": [q.x, q.y, q.z, q.w],
                "linear": [v.linear.x, v.linear.y, v.linear.z],
                "angular": [v.angular.x, v.angular.y, v.angular.z],
            })
            if len(physics) > 2000:
                del physics[0]

    probe.create_subscription(Odometry, "/odom", record_physics, 50)
    cloud: list[PointCloud2 | None] = [None]
    localization: list[PoseWithCovarianceStamped | None] = [None]
    local_scans = [0]
    probe.create_subscription(
        PoseWithCovarianceStamped, "/mcl_pose",
        lambda message: localization.__setitem__(0, message), 10,
    )
    probe.create_subscription(
        PointCloud2, "/livox/lidar", lambda message: cloud.__setitem__(0, message),
        qos_profile_sensor_data,
    )
    probe.create_subscription(
        LaserScan, "/scan_loc",
        lambda _: local_scans.__setitem__(0, local_scans[0] + 1), qos_profile_sensor_data,
    )
    action = None
    active = None
    worker = None
    timer = None
    started = time.monotonic()
    distance = 0.0
    previous: tuple[float, float] | None = None
    current_index = 0
    fault: str | None = None
    try:
        probe.spin_until(
            lambda: cloud[0] is not None and local_scans[0] > 0
            and probe.counts["scan"] > 0 and probe.odom is not None and probe.map is not None,
            args.startup_timeout, "Mid-360 point cloud, dual scans, odometry and navigation map",
        )
        first_cloud = cloud[0]
        if first_cloud is None:
            raise ProbeError("Mid-360 readiness has no matching cloud")
        report["mid360"] = cloud_geometry(first_cloud)
        print("COURSE_INPUTS_READY " + json.dumps(report["mid360"]), flush=True)
        def settled() -> bool:
            if len(physics) < 2:
                return False
            last = physics[-1]
            window = [item for item in physics if item["clock"] >= last["clock"] - 0.5]
            return (
                last["clock"] - window[0]["clock"] >= 0.4
                and max(math.dist(item["xyz"], last["xyz"]) for item in window) < 0.01
                and max(abs(value) for value in last["linear"] + last["angular"]) < 0.02
            )
        probe.spin_until(settled, args.startup_timeout, "physical settling before localization")
        physics_recording = False
        if transform.position_error(*physics[-1]["xyz"][:2], source_spawn) > 0.3:
            raise ProbeError("Unpowered robot moved away from the intended source spawn")
        print("COURSE_PHYSICS_SETTLED " + json.dumps(physics[-1]), flush=True)
        seed = source_spawn
        probe.spin_until(
            lambda: probe.initial_pose.get_subscription_count() > 0, 60, "emcl2 seed subscriber",
        )
        initial = PoseWithCovarianceStamped()
        initial.header.frame_id = "map"
        initial.header.stamp = probe.get_clock().now().to_msg()
        initial.pose.pose = waypoint_pose(seed).pose
        initial.pose.covariance[0] = initial.pose.covariance[7] = 0.01
        initial.pose.covariance[35] = math.radians(20)**2
        seed_scans = local_scans[0]
        probe.initial_pose.publish(initial)

        def converged() -> bool:
            posterior = localization[0]
            estimate = probe.lookup("map", "base_footprint")
            return (
                posterior is not None and estimate is not None
                and initial_localization_ready(
                    posterior.pose.covariance, local_scans[0] - seed_scans,
                )
                and math.dist((estimate["x"], estimate["y"]), seed[:2]) < 0.3
            )

        probe.spin_until(
            converged, 120, "emcl2 observed initial convergence",
        )
        print("COURSE_LOCALIZATION_SETTLED " + json.dumps({
            "estimate": probe.lookup("map", "base_footprint"),
            "scans_since_seed": local_scans[0] - seed_scans,
        }), flush=True)
        pid, argv = planner_command(args.output)
        loader = probe.create_client(LoadMap, "/map_server/load_map")
        if not loader.wait_for_service(timeout_sec=30):
            raise ProbeError("navigation map loading service did not appear")
        probe.set_motor(True)
        with (args.output / "trajectory.jsonl").open("w") as trajectory:
            next_progress = [0.0]

            def sample() -> None:
                nonlocal previous, distance, fault
                estimate = probe.lookup("map", "base_footprint")
                if estimate is None:
                    return
                raw = probe.truth()
                truth = transform.source_pose(raw["x"], raw["y"], raw["yaw"])
                if previous is not None:
                    distance += math.dist(previous, truth[:2])
                previous = truth[:2]
                error = math.dist(truth[:2], (estimate["x"], estimate["y"]))
                trajectory.write(json.dumps({
                    "clock": probe.get_clock().now().nanoseconds*1e-9, "goal": current_index,
                    "truth": truth, "estimate": estimate, "localization_error_m": error,
                    "driven_distance_m": distance,
                }) + "\n")
                trajectory.flush()
                if error > min(1.0, course["acceptance"]["maximum_live_localization_error_m"]):
                    fault = f"Independent localization error {error:.3f} m"
                now = time.monotonic()
                if now >= next_progress[0]:
                    print(f"COURSE_PROGRESS goal={current_index}/{len(targets)} distance={distance:.1f}m "
                          f"localization={error:.3f}m", flush=True)
                    next_progress[0] = now + 20

            timer = probe.create_timer(0.5, sample)
            while current_index < args.end_index:
                region = course["goal_regions"][current_index]
                batch_start, batch_end = current_index, current_index + 1
                while (batch_end < args.end_index and course["goal_regions"][batch_end] == region):
                    batch_end += 1
                loaded = loader.call_async(LoadMap.Request(
                    map_url=str(maps / "regions" / f"region-{region:03}.yaml"),
                ))
                probe.spin_until(loaded.done, 60, "regional navigation map")
                if loaded.result().result != LoadMap.Response.RESULT_SUCCESS:
                    raise ProbeError(f"Regional map {region} failed to load")
                stop_planner(pid)
                if worker is not None:
                    worker.wait(timeout=10)
                namespace = f"/course_vi_{region}_{batch_start}"
                worker = start_planner(
                    regional_planner_argv(argv, namespace),
                    args.output / f"vi-region-{batch_start:03}.log",
                )
                pid = worker.pid
                if action is not None:
                    action.destroy()
                action = ActionClient(probe, FollowWaypoints, namespace + "/follow_waypoints")
                if not action.wait_for_server(timeout_sec=120):
                    raise ProbeError(f"Regional VI {region} did not start")
                print(f"COURSE_REGION_READY region={region} start={batch_start}", flush=True)

                def observe(index: int, evidence: str) -> None:
                    goal_index = batch_start + index
                    goal = course["goals"][goal_index]
                    raw = probe.truth()
                    assert probe.odom is not None  # truth() requires this same received message.
                    simulator_pose = probe.odom.pose.pose
                    truth = transform.source_pose(raw["x"], raw["y"], raw["yaw"])
                    error = transform.position_error(raw["x"], raw["y"], goal)
                    estimate = probe.lookup("map", "base_footprint")
                    if estimate is None:
                        raise ProbeError(f"Missing localization at goal {goal_index}")
                    localization_error = math.dist(truth[:2], (estimate["x"], estimate["y"]))
                    if localization_error > min(
                        1.0, course["acceptance"]["maximum_live_localization_error_m"],
                    ):
                        raise ProbeError(
                            f"Independent localization error {localization_error:.3f} m",
                        )
                    arrival: Arrival = {
                        "index": goal_index, "region": region, "goal": goal, "truth": truth,
                        "physical_error_m": error, "arrival_evidence": evidence,
                        "simulator_position_m": [
                            simulator_pose.position.x, simulator_pose.position.y,
                            simulator_pose.position.z,
                        ],
                        "simulator_orientation_xyzw": [
                            simulator_pose.orientation.x, simulator_pose.orientation.y,
                            simulator_pose.orientation.z, simulator_pose.orientation.w,
                        ],
                    }
                    if error > report["physical_goal_error_limit_m"]:
                        raise ProbeError(f"Goal {goal_index} physical error {error:.3f} m")
                    report["completed"].append(arrival)
                    (args.output / "checkpoint.json").write_text(json.dumps(report, indent=2))
                    print("COURSE_GOAL " + json.dumps(arrival), flush=True)

                progress = WaypointProgress(batch_end - batch_start, args.goal_timeout, observe)

                def feedback(message) -> None:
                    nonlocal current_index, fault
                    if fault is not None:
                        return
                    try:
                        progress.feedback(message.feedback.current_waypoint)
                        current_index = batch_start + progress.current
                    except (RuntimeError, TimeoutError) as error:
                        fault = str(error)

                request = FollowWaypoints.Goal()
                for target in targets[batch_start:batch_end]:
                    target.header.stamp = probe.get_clock().now().to_msg()
                    request.poses.append(target)
                sent = action.send_goal_async(request, feedback_callback=feedback)
                probe.spin_until(sent.done, 30, "course group acceptance")
                active = sent.result()
                if not active.accepted:
                    raise ProbeError(f"Group {batch_start}:{batch_end} rejected")
                outcome = active.get_result_async()
                probe.spin_until(
                    lambda: outcome.done() or fault is not None or progress.expired(),
                    args.goal_timeout*(batch_end - batch_start) + 30, "course group result",
                )
                if fault is not None:
                    raise ProbeError(fault)
                if progress.expired():
                    raise ProbeError(f"Course goal {current_index} deadline exceeded")
                result = outcome.result()
                group: Group = {
                    "start": batch_start, "end": batch_end, "region": region,
                    "action_status": result.status, "missed_waypoints": list(result.result.missed_waypoints),
                }
                report["groups"].append(group)
                if result.status != 4 or result.result.missed_waypoints:
                    raise ProbeError(f"Course group failed: {group}")
                progress.finish()
                active = None
                current_index = batch_end
            report["status"] = "PASS" if args.end_index == len(targets) else "PARTIAL_PASS"
    except (ProbeError, RuntimeError, TimeoutError, AssertionError, KeyboardInterrupt) as error:
        report["error"] = str(error) or "interrupted"
        print("COURSE_FAILED " + report["error"], flush=True)
    finally:
        if timer is not None:
            probe.destroy_timer(timer)
        probe.stop_robot()
        if active is not None and active.accepted:
            cancel = active.cancel_goal_async()
            probe.spin_until(cancel.done, 15, "cancel failed course group")
        if worker is not None and worker.poll() is None:
            stop_planner(worker.pid)
            worker.wait(timeout=10)
        wall_seconds = time.monotonic() - started
        report.update(
            current_index=current_index, driven_distance_m=distance,
            wall_seconds=wall_seconds,
        )
        (args.output / "result.json").write_text(json.dumps(report, indent=2))
        (args.output / "startup-physics.json").write_text(json.dumps(physics, indent=2))
        print("COURSE_RESULT " + json.dumps({
            "status": report["status"], "completed": len(report["completed"]),
            "driven_distance_m": distance, "wall_seconds": wall_seconds,
        }), flush=True)
        probe.destroy_node()
        rclpy.shutdown()
    return 0 if report["status"] in ("PASS", "PARTIAL_PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
