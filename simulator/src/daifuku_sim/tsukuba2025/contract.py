# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Coordinate and ordered-arrival contracts, shared with the ROS course driver."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from pathlib import Path
import time
from typing import Final, Literal, Protocol, TypedDict


MAX_WAYPOINTS: Final = 30


class CourseOptions(Protocol):
    backend: Literal["gazebo", "isaacsim"]
    lidar_sensor_type: Literal["ray", "gpu_ray"]
    world: Path
    usim_root: Path | None
    engine: Literal["docker", "podman"]
    run_dir: Path
    stack_image: str
    base_image: str
    usim_image: str
    no_build: bool
    headless: bool
    keep_open: bool
    goal_timeout: float
    startup_timeout: float
    course_timeout: float
    end_index: int
    ros_domain_id: int


class TransformJson(TypedDict):
    source_origin_m: list[float]
    source_yaw_rad: float
    course_sha256: str


class ArrivalScore(TypedDict):
    index: int
    physical_error_m: float


class CourseAcceptance(TypedDict):
    maximum_physical_goal_error_m: float


class CourseMetadata(TypedDict):
    acceptance: CourseAcceptance
    goals: list[list[float]]


class CourseScore(TypedDict):
    status: str
    total_goals: int
    end_index: int
    completed: list[ArrivalScore]
    live_truth_localization: bool
    physical_goal_error_limit_m: float


def initial_localization_ready(covariance: Sequence[float], scans_since_seed: int) -> bool:
    """Require new observations and a concentrated posterior before enabling motion."""
    return scans_since_seed >= 10 and all(
        0 <= covariance[index] <= limit
        for index, limit in ((0, 0.01), (7, 0.01), (35, 0.005))
    )


def audit_score(
    score: CourseScore, end_index: int, physical_limit: float, *,
    total_goals: int = MAX_WAYPOINTS,
) -> None:
    """A subset, skipped arrival, or relaxed physical threshold cannot become a full PASS."""
    expected_status = "PASS" if end_index == total_goals else "PARTIAL_PASS"
    if (score["status"] != expected_status or score["total_goals"] != total_goals
            or score["end_index"] != end_index):
        raise ValueError("course result does not match the requested original-course coverage")
    if score["live_truth_localization"]:
        raise ValueError("course used continuous truth localization")
    limit = min(0.6, physical_limit)
    if not 0 < score["physical_goal_error_limit_m"] <= limit:
        raise ValueError("course result relaxed its physical acceptance threshold")
    if [item["index"] for item in score["completed"]] != list(range(end_index)):
        raise ValueError("course result omitted or reordered physical arrivals")
    if any(not math.isfinite(item["physical_error_m"])
           or not 0 <= item["physical_error_m"] <= limit for item in score["completed"]):
        raise ValueError("physical arrival audit exceeded its limit")


@dataclass(frozen=True, slots=True)
class CourseTransform:
    """Rigid simulator-to-source transform; map files and goals retain source coordinates."""

    x: float
    y: float
    yaw: float

    def source_pose(self, x: float, y: float, yaw: float) -> tuple[float, float, float]:
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return (
            self.x + c*x - s*y,
            self.y + s*x + c*y,
            math.remainder(yaw + self.yaw, 2*math.pi),
        )

    def position_error(self, x: float, y: float, source_target: Sequence[float]) -> float:
        """Measure physical distance in the source frame, including nonzero native odometry."""
        return math.dist(self.source_pose(x, y, 0)[:2], source_target[:2])


class WaypointProgress:
    """Accumulate arrivals only from ordered feedback and successful group completion."""

    def __init__(
        self, count: int, timeout: float, observe: Callable[[int, str], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.count: int = count
        self.timeout: float = timeout
        self.observe: Callable[[int, str], None] = observe
        self.clock: Callable[[], float] = clock
        self.current: int = -1
        self.deadline: float = clock() + timeout

    def expired(self) -> bool:
        return self.clock() >= self.deadline

    def feedback(self, index: int) -> None:
        if self.expired():
            raise TimeoutError(f"Waypoint {max(0, self.current)} deadline exceeded")
        if index == self.current and 0 <= index < self.count:
            return
        if index != self.current + 1 or not 0 <= index < self.count:
            raise RuntimeError(f"Missing/out-of-order waypoint feedback: {self.current} -> {index}")
        if self.current >= 0:
            self.observe(self.current, "next_waypoint_started")
            self.deadline = self.clock() + self.timeout
        self.current = index

    def finish(self) -> None:
        if self.expired():
            raise TimeoutError(f"Waypoint {max(0, self.current)} deadline exceeded")
        if self.current != self.count - 1:
            raise RuntimeError(
                f"Incomplete waypoint feedback: last={self.current}, count={self.count}"
            )
        self.observe(self.current, "group_succeeded")


def regional_planner_argv(argv: list[str], namespace: str) -> list[str]:
    """Isolate both ROS action servers from stale endpoints after each VI restart."""
    result = argv.copy()
    actions = ("navigate_to_pose", "follow_waypoints")
    for index in reversed(range(1, len(result))):
        if result[index - 1] != "-r":
            continue
        source = result[index].split(":=", 1)[0].split(":")[-1]
        if any(action in source.split("/") for action in actions):
            del result[index - 1:index + 1]
    for action in actions:
        for entity in ("send_goal", "get_result", "cancel_goal", "feedback", "status"):
            result.extend([
                "-r", f"{action}/_action/{entity}:={namespace}/{action}/_action/{entity}",
            ])
    return result
