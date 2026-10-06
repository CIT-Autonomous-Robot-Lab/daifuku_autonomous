"""Pure, ROS-free checks for the usim VTC workflow (host side, Python >= 3.10).

Everything here is deterministic and dependency-light (stdlib + PyYAML) so the
orchestrator can fail early before any container starts, and so the verdict
logic can be unit-tested without Gazebo or ROS.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

from .vtc_map import OccupancyMap, check_goal_cell, nearest_safe_goal_cell

from .vtc_contract import (
    Alignment, BASE_FRAME, CheckError, LEFT_WHEEL_JOINT, LIDAR_FRAME,
    LIDAR as LIDAR_KWARGS, Navigation, Pose2D, PoseJson, RIGHT_WHEEL_JOINT,
    RobotGeometry, TruthResult, WorldInfo, parse_pose, parse_waypoints,
)

STATUS_SUCCEEDED = 4
STATUS_NAMES = {
    0: 'UNKNOWN',
    1: 'ACCEPTED',
    2: 'EXECUTING',
    3: 'CANCELING',
    4: 'SUCCEEDED',
    5: 'CANCELED',
    6: 'ABORTED',
}


# --------------------------------------------------------------------------- world


def validate_world(path: Path) -> WorldInfo:
    """Reject a missing or malformed SDF world before any container starts.

    Checks: regular file, XML, ``<sdf>`` root, exactly one ``<world>``, and
    every relative/``file://`` ``<uri>`` resolves to an existing file.
    ``model://`` URIs are left to Gazebo's model path.
    """
    if not path.exists():
        raise CheckError(f'VTC world does not exist: {path}')
    if not path.is_file():
        raise CheckError(f'VTC world is not a regular file: {path}')
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise CheckError(f'VTC world is not valid XML: {path}: {error}') from error
    if root.tag != 'sdf':
        raise CheckError(f'VTC world root must be <sdf>, got <{root.tag}>: {path}')
    worlds = root.findall('world')
    if len(worlds) != 1:
        raise CheckError(f'VTC world must contain exactly one <world>, found {len(worlds)}: {path}')
    missing = []
    meshes = 0
    for element in root.iter('uri'):
        value = unquote((element.text or '').strip())
        if not value or value.startswith('model://') or '://' in value.removeprefix('file://'):
            continue
        target = Path(value.removeprefix('file://'))
        if not target.is_absolute():
            target = path.parent / target
        meshes += 1
        if not target.is_file():
            missing.append(value)
    if missing:
        raise CheckError(
            f'VTC world references {len(missing)} missing file(s), first: {missing[0]}: {path}'
        )
    return {'path': str(path), 'world_name': worlds[0].get('name'), 'file_uris': meshes}


# --------------------------------------------------------------------------- robot


def _origin(joint: ET.Element) -> tuple[float, float, float]:
    origin = joint.find('origin')
    text = origin.get('xyz', '0 0 0') if origin is not None else '0 0 0'
    x, y, z = (float(value) for value in text.split())
    return x, y, z


def robot_geometry(urdf: Path) -> RobotGeometry:
    """Derive the drive geometry usim needs from the plain Raspicat URDF.

    Wheel separation is the lateral distance between the two wheel joint
    origins (both children of ``base_link``); the radius comes from the wheel
    collision cylinders, which must agree.
    """
    try:
        robot = ET.parse(urdf).getroot()
    except (OSError, ET.ParseError) as error:
        raise CheckError(f'cannot read robot URDF {urdf}: {error}') from error
    if robot.tag != 'robot':
        raise CheckError(f'{urdf}: expected <robot>, got <{robot.tag}>')
    links = {link.get('name'): link for link in robot.findall('link')}
    joints = {joint.get('name'): joint for joint in robot.findall('joint')}
    for frame in (BASE_FRAME, LIDAR_FRAME):
        if frame not in links:
            raise CheckError(f'{urdf}: link {frame!r} is missing')
    radii = []
    positions = []
    for name in (LEFT_WHEEL_JOINT, RIGHT_WHEEL_JOINT):
        joint = joints.get(name)
        if joint is None or joint.get('type') not in ('continuous', 'revolute'):
            raise CheckError(f'{urdf}: wheel joint {name!r} missing or not rotating')
        positions.append(_origin(joint))
        child = joint.find('child')
        link = links.get(child.get('link') if child is not None else None)
        cylinder = None if link is None else link.find('collision/geometry/cylinder')
        if cylinder is None:
            raise CheckError(f'{urdf}: wheel {name!r} has no collision cylinder')
        try:
            radii.append(float(cylinder.attrib['radius']))
        except (KeyError, ValueError) as error:
            raise CheckError(str(urdf), f'wheel {name!r} has no numeric radius') from error
    if not math.isclose(radii[0], radii[1], rel_tol=1e-6):
        raise CheckError(f'{urdf}: wheel radii differ: {radii}')
    separation = abs(positions[0][1] - positions[1][1])
    if not 0.05 < separation < 2.0 or not 0.01 < radii[0] < 1.0:
        raise CheckError(f'{urdf}: implausible geometry r={radii[0]} sep={separation}')

    # base_footprint -> ... -> lidar_link must be a chain of fixed joints so the
    # robot_state_publisher alone (no joint_states) can publish it.
    parent_of = {}
    for joint in robot.findall('joint'):
        parent, child = joint.find('parent'), joint.find('child')
        if parent is not None and child is not None:
            parent_of[child.get('link')] = (parent.get('link'), joint)
    chain, frame, height = [LIDAR_FRAME], LIDAR_FRAME, 0.0
    while frame != BASE_FRAME:
        if frame not in parent_of:
            raise CheckError(f'{urdf}: {LIDAR_FRAME} is not below {BASE_FRAME}')
        frame, joint = parent_of[frame]
        if frame in chain:
            raise CheckError(str(urdf), 'lidar joint chain contains a cycle')
        if joint.get('type') != 'fixed':
            raise CheckError(f'{urdf}: joint {joint.get("name")!r} on the lidar chain is not fixed')
        height += _origin(joint)[2]
        chain.append(frame)
    return {
        'urdf': str(urdf),
        'base_link': BASE_FRAME,
        'lidar_link': LIDAR_FRAME,
        'left_joint': LEFT_WHEEL_JOINT,
        'right_joint': RIGHT_WHEEL_JOINT,
        'wheel_radius': radii[0],
        'wheel_separation': separation,
        'lidar_height_m': height,
        'lidar_chain': list(reversed(chain)),
    }


# --------------------------------------------------------------------------- map


# --------------------------------------------------------------------------- frames


def wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def map_from_odom(map_to_odom: Pose2D, point: Pose2D) -> Pose2D:
    """Express an odom-frame pose in the map frame via the map->odom transform."""
    cos, sin = math.cos(map_to_odom.yaw), math.sin(map_to_odom.yaw)
    return Pose2D(
        map_to_odom.x + cos * point.x - sin * point.y,
        map_to_odom.y + sin * point.x + cos * point.y,
        wrap(map_to_odom.yaw + point.yaw),
    )


def odom_from_map(map_to_odom: Pose2D, point: Pose2D) -> Pose2D:
    """Inverse of :func:`map_from_odom`."""
    dx, dy = point.x - map_to_odom.x, point.y - map_to_odom.y
    cos, sin = math.cos(-map_to_odom.yaw), math.sin(-map_to_odom.yaw)
    return Pose2D(cos * dx - sin * dy, sin * dx + cos * dy, wrap(point.yaw - map_to_odom.yaw))


def evaluate_alignment(
    map_to_odom: Pose2D, max_translation: float, max_yaw: float
) -> Alignment:
    """Decide whether the saved map frame coincides with the Gazebo world frame.

    usim's diff drive uses world-source odometry and the robot spawns at the
    world origin, so the odom frame *is* the world frame. SLAM's final
    ``map -> odom`` is therefore the map-to-world offset. Restarting Gazebo at
    (0, 0) and seeding localization there is only justified if that offset is
    small; the exact seed used is the world origin expressed in map coordinates.
    """
    translation = math.hypot(map_to_odom.x, map_to_odom.y)
    supported = translation <= max_translation and abs(wrap(map_to_odom.yaw)) <= max_yaw
    seed = map_from_odom(map_to_odom, Pose2D(0.0, 0.0, 0.0))
    return {
        'map_to_odom': map_to_odom.as_dict(),
        'translation_m': translation,
        'yaw_rad': wrap(map_to_odom.yaw),
        'max_translation_m': max_translation,
        'max_yaw_rad': max_yaw,
        'origin_seed_supported': supported,
        'seed_map': seed.as_dict(),
    }


def pose_from(data: PoseJson) -> Pose2D:
    pose = Pose2D(float(data['x']), float(data['y']), float(data.get('yaw', 0.0)))
    if not all(math.isfinite(value) for value in (pose.x, pose.y, pose.yaw)):
        raise CheckError('pose', 'artifact coordinates must be finite')
    return pose


def navigation_verdict(
    navigation: Navigation,
    map_to_odom: Pose2D,
    goal_map: Pose2D,
    tolerance: float,
    start_tolerance: float = 0.10,
    expected_start_world: Pose2D = Pose2D(0.0, 0.0, 0.0),
) -> tuple[str, str, TruthResult]:
    """Return ``(status, reason, truth)``; PASS needs SUCCEEDED *and* world truth."""
    start = navigation.get('truth_start')
    final = navigation.get('truth_final')
    status_code = navigation.get('action_status')
    if start is None or final is None or status_code is None:
        raise CheckError('navigation', 'action status and independent world truth are required')
    truth_start = pose_from(start)
    truth_final = pose_from(final)
    goal_world = odom_from_map(map_to_odom, goal_map)
    error = math.hypot(truth_final.x - goal_world.x, truth_final.y - goal_world.y)
    moved = math.hypot(truth_final.x - truth_start.x, truth_final.y - truth_start.y)
    needed = math.hypot(goal_world.x - truth_start.x, goal_world.y - truth_start.y)
    truth: TruthResult = {
        'source': 'gazebo world-source /odom (continuous usim diff drive)',
        'start_world': truth_start.as_dict(),
        'final_world': truth_final.as_dict(),
        'goal_world': goal_world.as_dict(),
        'error_m': error,
        'tolerance_m': tolerance,
        'moved_m': moved,
        'within_tolerance': error <= tolerance,
    }
    start_offset = math.hypot(
        truth_start.x - expected_start_world.x, truth_start.y - expected_start_world.y
    )
    if start_offset > start_tolerance:
        return 'FAIL', f'navigation world pose changed across transition (offset {start_offset:.3f} m)', truth
    if status_code != STATUS_SUCCEEDED:
        name = STATUS_NAMES.get(status_code, str(status_code))
        return 'FAIL', f'navigate_to_pose finished with {name} ({status_code})', truth
    if moved < 0.5 * needed:
        return 'FAIL', f'action reported SUCCEEDED but the robot moved only {moved:.2f} m of {needed:.2f} m', truth
    if error > tolerance:
        return (
            'FAIL',
            f'action reported SUCCEEDED but world-truth goal error {error:.3f} m exceeds {tolerance} m',
            truth,
        )
    return 'PASS', f'SUCCEEDED and world-truth goal error {error:.3f} m <= {tolerance} m', truth
