"""Shared vocabulary of the usim VTC workflow: frames, lidar contract, poses, typed errors."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, IntEnum, unique
from typing import Final, Literal, TypeAlias, TypedDict

Json: TypeAlias = str | int | float | bool | None | list['Json'] | dict[str, 'Json']
JsonObject: TypeAlias = dict[str, Json]
Waypoint: TypeAlias = tuple[float, float]

BASE_FRAME: Final = 'base_footprint'
LIDAR_FRAME: Final = 'lidar_link'
LEFT_WHEEL_JOINT: Final = 'left_wheel_joint'
RIGHT_WHEEL_JOINT: Final = 'right_wheel_joint'


class LidarKwargs(TypedDict):
    """Keyword arguments of usim's ``GazeboLidarConfig`` (the parallel-task contract)."""

    link_name: str
    frame_name: str
    topic: str
    update_rate: float
    horizontal_samples: int
    min_angle: float
    max_angle: float
    range_min: float
    range_max: float


LIDAR: Final[LidarKwargs] = {
    'link_name': LIDAR_FRAME,
    'frame_name': LIDAR_FRAME,
    'topic': '/scan_raw',
    'update_rate': 10.0,
    'horizontal_samples': 720,
    'min_angle': -math.pi,
    'max_angle': math.pi,
    'range_min': 0.1,
    'range_max': 10.0,
}


@unique
class GoalStatus(IntEnum):
    """``action_msgs/GoalStatus`` codes."""

    UNKNOWN = 0
    ACCEPTED = 1
    EXECUTING = 2
    CANCELING = 3
    SUCCEEDED = 4
    CANCELED = 5
    ABORTED = 6


@unique
class Status(str, Enum):
    PASS = 'PASS'
    FAIL = 'FAIL'
    BLOCKED = 'BLOCKED'


@unique
class Engine(str, Enum):
    PODMAN = 'podman'
    DOCKER = 'docker'


@dataclass(frozen=True, slots=True)
class CheckError(Exception):
    """An input artifact (world, URDF, map, run JSON, CLI value) violates its contract."""

    subject: str
    problem: str = ''

    def __str__(self) -> str:
        return f'{self.subject}: {self.problem}' if self.problem else self.subject


class PoseJson(TypedDict):
    x: float
    y: float
    yaw: float


@dataclass(frozen=True, slots=True)
class Pose2D:
    """Planar pose in metres / radians."""

    x: float
    y: float
    yaw: float = 0.0

    def as_json(self) -> PoseJson:
        return {'x': self.x, 'y': self.y, 'yaw': self.yaw}

    def as_dict(self) -> PoseJson:
        return self.as_json()

    def as_arg(self) -> str:
        """``x,y,yaw`` as consumed by the container probes."""
        return f'{self.x},{self.y},{self.yaw}'


def parse_pose(text: str) -> Pose2D:
    """Parse ``x,y[,yaw]``."""
    parts = [part.strip() for part in text.split(',')]
    if len(parts) not in {2, 3}:
        raise CheckError(text, 'expected x,y[,yaw]')
    try:
        values = [float(part) for part in parts]
    except ValueError as error:
        raise CheckError(text, 'pose values must be numbers') from error
    if not all(math.isfinite(value) for value in values):
        raise CheckError(text, 'pose values must be finite')
    return Pose2D(*values)


def parse_waypoints(text: str) -> tuple[Waypoint, ...]:
    """Parse ``x,y;x,y;...`` survey waypoints relative to the spawn pose."""
    points = tuple((pose.x, pose.y) for pose in (parse_pose(c) for c in text.split(';') if c.strip()))
    if len(points) < 2:  # noqa: PLR2004 - a survey needs a leg, i.e. two points
        raise CheckError(text, 'survey needs at least two waypoints')
    return points


def wrap(angle: float) -> float:
    """Normalize an angle to (-pi, pi]."""
    return math.atan2(math.sin(angle), math.cos(angle))


def survey_pose_arrived(current: PoseJson, target: PoseJson) -> bool:
    """Require the survey's position and requested heading before sampling SLAM."""
    return (
        math.hypot(current['x'] - target['x'], current['y'] - target['y']) < 0.12
        and abs(wrap(current['yaw'] - target['yaw'])) < 0.02
    )


def survey_targets(
    start: PoseJson, waypoints: tuple[Waypoint, ...], world_goal: PoseJson | None
) -> list[PoseJson]:
    """Keep relative survey legs, but visit the fixed world goal before returning."""
    cos, sin = math.cos(start['yaw']), math.sin(start['yaw'])
    targets: list[PoseJson] = [
        {'x': start['x'] + x * cos - y * sin,
         'y': start['y'] + x * sin + y * cos, 'yaw': 0.0}
        for x, y in waypoints
    ]
    if world_goal is not None:
        targets.insert(-1, world_goal)
    return targets


class WorldInfo(TypedDict):
    path: str
    world_name: str | None
    file_uris: int


class RobotGeometry(TypedDict):
    urdf: str
    base_link: str
    lidar_link: str
    left_joint: str
    right_joint: str
    wheel_radius: float
    wheel_separation: float
    lidar_height_m: float
    lidar_chain: list[str]


class SessionConfig(TypedDict):
    phase: str
    usim_root: str
    engine: Literal['podman', 'docker']
    network: str
    image: str
    world: str
    robot: RobotGeometry
    spawn: PoseJson
    camera_enabled: bool


class Alignment(TypedDict):
    map_to_odom: PoseJson
    translation_m: float
    yaw_rad: float
    max_translation_m: float
    max_yaw_rad: float
    origin_seed_supported: bool
    seed_map: PoseJson


class Navigation(TypedDict, total=False):
    ok: bool
    error: str
    accepted: bool
    action_status: int
    truth_start: PoseJson
    truth_final: PoseJson


class TruthResult(TypedDict):
    source: str
    start_world: PoseJson
    final_world: PoseJson
    goal_world: PoseJson
    error_m: float
    tolerance_m: float
    moved_m: float
    within_tolerance: bool


class MapStats(TypedDict):
    width: int
    height: int
    resolution: float
    origin: PoseJson
    known_cells: int
    free_cells: int
    occupied_cells: int
    updates: int


class Alignments(TypedDict):
    start: PoseJson
    end: PoseJson


class Mapping(TypedDict, total=False):
    ok: bool
    error: str
    alignment: Alignments
    slam_vs_truth: MappingPoses


class MappingPoses(TypedDict):
    slam_map: PoseJson
    truth_world: PoseJson
    error_m: float
    yaw_error_rad: float


class Transition(TypedDict):
    mode: str
    map_pose: PoseJson
    odom_pose: PoseJson
    map_to_odom: PoseJson
    goal_world: PoseJson
    requested_goal_map: PoseJson
    planned_goal_map: PoseJson
    navigation_seed_map: PoseJson
    goal_adjustment_m: float
    seed_adjustment_m: float
    translation_consistency_m: float
    yaw_consistency_rad: float


class Phase(TypedDict, total=False):
    container: str
    stack_exit: int
    gazebo_exit: int
    simulator_session: str


class RunConfig(TypedDict):
    engine: str
    network_mode: str
    usim_root: str | None
    usim_image: str
    stack_image: str
    base_image: str
    ros_domain_id: int
    rmw_implementation: str
    fastdds_builtin_transports: str
    use_sim_time: bool
    lidar: LidarKwargs
    goal_world: PoseJson
    survey: tuple[Waypoint, ...]
    tolerance_m: float


class RunResultCore(TypedDict):
    schema: str
    status: str | None
    stage: str | None
    reason: str | None
    run_dir: str
    started_at: str
    config: RunConfig
    phases: dict[str, Phase]


class RunResult(RunResultCore, total=False):
    network: str
    detail: str
    finished_at: str
    exit_code: int
    world: WorldInfo
    robot: RobotGeometry
    images: dict[str, list[str]]
    mapping: Mapping
    alignment: Alignment
    transition: Transition
    map: dict[str, str | int]
    navigation: Navigation
    truth: TruthResult
    cleanup: JsonObject
    cleanup_error: str
    logs: list[str]
