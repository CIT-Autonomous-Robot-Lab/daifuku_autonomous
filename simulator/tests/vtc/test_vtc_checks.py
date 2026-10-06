"""Pure checks: world validation, Raspicat geometry, map cells, frames and the verdict."""

from __future__ import annotations

import copy
import math
import re
import shlex
from pathlib import Path

import pytest
import yaml

from daifuku_sim.vtc import vtc_checks as checks
from daifuku_sim.vtc.vtc_contract import survey_pose_arrived, survey_targets
from daifuku_sim.vtc.vtc_map import nearest_safe_seed_cell
from .conftest import RASPICAT_URDF, STACK, write_map, write_world
from daifuku_sim.vtc.prepare_slam_params import prepare_slam_profile


@pytest.mark.parametrize(
    ('position', 'yaw', 'arrived'),
    [
        ((0.0, 0.0), 0.0, True),
        ((0.0, 0.0), -1.603, False),
        ((0.0, 0.0), 0.021, False),
        ((0.0, 0.0), 0.019, True),
        ((0.121, 0.0), 0.0, False),
        ((0.119, 0.0), 0.0, True),
        ((0.0, 0.0), 2.0 * math.pi - 0.01, True),
    ],
)
def test_survey_arrival_requires_position_and_wrapped_heading(position, yaw, arrived) -> None:
    target = {'x': 2.5, 'y': 1.25, 'yaw': 0.0}
    current = {'x': target['x'] + position[0], 'y': target['y'] + position[1], 'yaw': yaw}
    assert survey_pose_arrived(current, target) is arrived


def test_goal_survey_stop_is_world_fixed_and_precedes_return() -> None:
    start = {'x': 1.0, 'y': 2.0, 'yaw': math.pi / 2}
    goal = {'x': 2.5, 'y': 1.25, 'yaw': 0.0}
    targets = survey_targets(start, ((1.0, 0.0), (0.0, 0.0)), goal)
    assert targets[0] == pytest.approx({'x': 1.0, 'y': 3.0, 'yaw': 0.0})
    assert targets[1] == goal
    assert targets[2] == {'x': 1.0, 'y': 2.0, 'yaw': 0.0}


# --------------------------------------------------------------------------- world


def test_valid_world_is_accepted(tmp_path: Path) -> None:
    world = write_world(tmp_path / 'vtc' / 'world.sdf')
    info = checks.validate_world(world)
    assert info['world_name'] == 'vtc'
    assert info['file_uris'] == 1


@pytest.mark.parametrize(
    ('body', 'message'),
    [
        ('<sdf><world name="a"/>', 'not valid XML'),
        ('<robot/>', 'root must be <sdf>'),
        ('<sdf version="1.6"/>', 'exactly one <world>, found 0'),
        ('<sdf><world name="a"/><world name="b"/></sdf>', 'exactly one <world>, found 2'),
        (
            '<sdf><world name="a"><uri>meshes/absent.dae</uri><uri>model://sun</uri></world></sdf>',
            'missing file',
        ),
    ],
)
def test_malformed_world_is_rejected(tmp_path: Path, body: str, message: str) -> None:
    world = write_world(tmp_path / 'vtc' / 'world.sdf', body)
    with pytest.raises(checks.CheckError, match=re.escape(message)):
        checks.validate_world(world)


def test_missing_and_directory_world_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(checks.CheckError, match='does not exist'):
        checks.validate_world(tmp_path / 'nope.sdf')
    with pytest.raises(checks.CheckError, match='not a regular file'):
        checks.validate_world(tmp_path)


# --------------------------------------------------------------------------- robot


def test_raspicat_geometry_from_plain_urdf(tmp_path: Path) -> None:
    urdf = tmp_path / 'raspicat_plain.urdf'
    urdf.write_text(RASPICAT_URDF, encoding='utf-8')
    geometry = checks.robot_geometry(urdf)
    assert geometry['base_link'] == 'base_footprint'
    assert geometry['wheel_radius'] == pytest.approx(0.0762)
    assert geometry['wheel_separation'] == pytest.approx(0.27918)
    assert geometry['lidar_chain'] == ['base_footprint', 'base_link', 'lidar_mount_link', 'lidar_link']
    assert geometry['lidar_height_m'] == pytest.approx(0.0762 - 0.0262 + 0.055)


@pytest.mark.parametrize(
    ('old', 'new', 'message'),
    [
        ('<link name="lidar_link"/>', '', "link 'lidar_link' is missing"),
        ('name="lidar_frame_joint" type="fixed"', 'name="lidar_frame_joint" type="revolute"', 'not fixed'),
        ('radius="0.0762" length="0.0254"/></geometry></collision>\n  </link>\n  <joint name="left',
         'radius="0.05" length="0.0254"/></geometry></collision>\n  </link>\n  <joint name="left', 'radii differ'),
    ],
)
def test_bad_urdf_is_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    assert old in RASPICAT_URDF
    urdf = tmp_path / 'bad.urdf'
    urdf.write_text(RASPICAT_URDF.replace(old, new, 1), encoding='utf-8')
    with pytest.raises(checks.CheckError, match=re.escape(message)):
        checks.robot_geometry(urdf)


# --------------------------------------------------------------------------- map


def test_occupancy_map_cells_and_goal_clearance(tmp_path: Path) -> None:
    grid = checks.OccupancyMap(write_map(tmp_path, wall_x=3.0))
    assert grid.state(1.0, 1.0) == 'free'
    assert grid.state(3.01, 1.0) == 'occupied'
    assert grid.state(-5.0, 0.0) == 'outside'
    assert grid.counts()['occupied'] == 120
    checks.check_goal_cell(grid, checks.Pose2D(2.5, 1.25), 0.2)
    with pytest.raises(checks.CheckError, match='occupied cell within'):
        checks.check_goal_cell(grid, checks.Pose2D(2.9, 1.25), 0.2)
    with pytest.raises(checks.CheckError, match='is occupied'):
        checks.check_goal_cell(grid, checks.Pose2D(3.01, 1.25), 0.2)


def test_goal_resolves_to_nearest_safe_free_cell_within_tolerance(tmp_path: Path) -> None:
    grid = checks.OccupancyMap(write_map(tmp_path, wall_x=3.0))
    exact = checks.Pose2D(2.9, 1.25, 0.4)
    planned = checks.nearest_safe_goal_cell(grid, exact, 0.2, 0.35)
    assert planned.x < exact.x
    assert math.hypot(planned.x - exact.x, planned.y - exact.y) <= 0.35
    assert planned.yaw == exact.yaw
    checks.check_goal_cell(grid, planned, 0.2)


def test_goal_resolution_fails_when_no_safe_free_cell_is_close_enough(tmp_path: Path) -> None:
    grid = checks.OccupancyMap(write_map(tmp_path, size=120, wall_x=2.5))
    with pytest.raises(checks.CheckError, match='no safe known-free map cell'):
        checks.nearest_safe_goal_cell(grid, checks.Pose2D(2.5, 1.25), 0.2, 0.1)


def test_seed_snaps_to_nearest_safe_cell_connected_to_goal(tmp_path: Path) -> None:
    grid = checks.OccupancyMap(write_map(tmp_path, wall_x=3.0))
    seed = checks.Pose2D(2.9, 1.25, 0.2)
    planned = nearest_safe_seed_cell(grid, seed, checks.Pose2D(1.5, 1.25), 0.2, 0.35)
    assert planned.x < seed.x
    assert math.hypot(planned.x - seed.x, planned.y - seed.y) <= 0.35
    assert planned.yaw == seed.yaw
    checks.check_goal_cell(grid, planned, 0.2)


def test_seed_snapping_rejects_a_disconnected_free_component(tmp_path: Path) -> None:
    grid = checks.OccupancyMap(write_map(tmp_path, wall_x=3.0))
    with pytest.raises(checks.CheckError, match='no connected safe map cell'):
        nearest_safe_seed_cell(
            grid, checks.Pose2D(2.5, 1.25), checks.Pose2D(4.0, 1.25), 0.2, 0.35
        )


def test_gazebo_slam_profile_preserves_daifuku_mapping_parameters(tmp_path: Path) -> None:
    source = STACK.parents[2] / 'src' / 'daifuku_config' / 'stack' / 'mapping' / 'slam_toolbox.yaml'
    destination = tmp_path / 'slam_toolbox_gazebo.yaml'
    prepare_slam_profile(source, destination)
    expected = yaml.safe_load(source.read_text(encoding='utf-8'))
    expected['slam_toolbox']['ros__parameters']['use_scan_matching'] = False
    expected['slam_toolbox']['ros__parameters']['do_loop_closing'] = False
    assert yaml.safe_load(destination.read_text(encoding='utf-8')) == expected


def test_unknown_pixels_stay_unknown(tmp_path: Path) -> None:
    yaml_path = write_map(tmp_path)
    pgm = tmp_path / 'vtc_map.pgm'
    data = bytearray(pgm.read_bytes())
    data[-1] = 205  # last byte = bottom image row (row_up 0), last column
    pgm.write_bytes(bytes(data))
    grid = checks.OccupancyMap(yaml_path)
    assert grid.state(-1.0 + 119.5 * 0.05, -1.0 + 0.5 * 0.05) == 'unknown'


@pytest.mark.parametrize('header', [b'', b'P5\n', b'P5\n# unfinished comment'])
def test_truncated_map_header_is_rejected(tmp_path: Path, header: bytes) -> None:
    # Given: a saved map whose PGM header ended before the required tokens.
    yaml_path = write_map(tmp_path)
    (tmp_path / 'vtc_map.pgm').write_bytes(header)
    # When / Then: parsing rejects the artifact instead of hanging forever.
    with pytest.raises(checks.CheckError, match='truncated PGM header'):
        checks.OccupancyMap(yaml_path)


# --------------------------------------------------------------------------- frames & verdict


def test_frame_round_trip() -> None:
    map_to_odom = checks.Pose2D(0.4, -0.2, math.radians(30))
    point = checks.Pose2D(1.0, 2.0, 0.5)
    back = checks.odom_from_map(map_to_odom, checks.map_from_odom(map_to_odom, point))
    assert (back.x, back.y, back.yaw) == pytest.approx((point.x, point.y, point.yaw))


def test_alignment_gates_the_origin_seed() -> None:
    good = checks.evaluate_alignment(checks.Pose2D(0.03, -0.02, 0.01), 0.10, math.radians(2))
    assert good['origin_seed_supported'] is True
    assert good['seed_map'] == pytest.approx({'x': 0.03, 'y': -0.02, 'yaw': 0.01})
    assert checks.evaluate_alignment(checks.Pose2D(0.3, 0, 0), 0.10, 0.035)['origin_seed_supported'] is False
    assert checks.evaluate_alignment(checks.Pose2D(0, 0, 0.1), 0.10, 0.035)['origin_seed_supported'] is False


def _navigation(status: int, final: tuple[float, float], start: tuple[float, float] = (0.0, 0.0)) -> dict:
    return {
        'action_status': status,
        'truth_start': {'x': start[0], 'y': start[1], 'yaw': 0.0},
        'truth_final': {'x': final[0], 'y': final[1], 'yaw': 1.5},
    }


def test_verdict_pass_requires_success_and_world_truth() -> None:
    identity = checks.Pose2D(0.0, 0.0, 0.0)
    goal = checks.Pose2D(2.5, 1.25, 1.57)
    status, reason, truth = checks.navigation_verdict(_navigation(4, (2.45, 1.30)), identity, goal, 0.35)
    assert status == 'PASS', reason
    assert truth['within_tolerance'] is True
    assert checks.navigation_verdict(_navigation(6, (2.45, 1.30)), identity, goal, 0.35)[1].endswith('ABORTED (6)')
    status, reason, _ = checks.navigation_verdict(_navigation(4, (1.5, 1.25)), identity, goal, 0.35)
    assert status == 'FAIL' and 'world-truth goal error' in reason
    status, reason, _ = checks.navigation_verdict(_navigation(4, (0.1, 0.0)), identity, goal, 0.35)
    assert status == 'FAIL' and 'moved only' in reason
    status, reason, _ = checks.navigation_verdict(_navigation(4, (2.5, 1.25), (0.5, 0.0)), identity, goal, 0.35)
    assert status == 'FAIL'


def test_world_goal_verdict_accepts_continuous_start_and_rejects_reset() -> None:
    transform = checks.Pose2D(0.4, -0.3, math.pi / 2)
    fixed_goal = checks.Pose2D(2.5, 1.25, 1.57)
    map_goal = checks.map_from_odom(transform, fixed_goal)
    start = checks.Pose2D(0.3, 0.2, -1.2)
    status, _, truth = checks.navigation_verdict(
        _navigation(4, (2.5, 1.25), (0.3, 0.2)), transform, map_goal, 0.35,
        expected_start_world=start,
    )
    assert status == 'PASS'
    assert truth['goal_world'] == pytest.approx(fixed_goal.as_dict())
    assert checks.navigation_verdict(
        _navigation(4, (2.5, 1.25)), transform, map_goal, 0.35,
        expected_start_world=start,
    )[0] == 'FAIL'


def test_verdict_uses_map_to_odom_for_the_world_goal() -> None:
    map_to_odom = checks.Pose2D(0.08, 0.0, 0.0)
    goal = checks.Pose2D(2.5, 1.25, 0.0)
    # The goal in world (odom) coordinates is 8 cm behind the map goal.
    status, _, truth = checks.navigation_verdict(_navigation(4, (2.42, 1.25)), map_to_odom, goal, 0.01)
    assert status == 'PASS'
    assert truth['goal_world']['x'] == pytest.approx(2.42)


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
def test_verdict_rejects_nonfinite_world_truth(value: float) -> None:
    # Given: an action reports success but its independent truth is non-finite.
    navigation = _navigation(4, (value, 1.25))
    # When / Then: it cannot be classified PASS by NaN comparison behavior.
    with pytest.raises(checks.CheckError, match='finite'):
        checks.navigation_verdict(navigation, checks.Pose2D(0, 0), checks.Pose2D(2.5, 1.25), 0.35)


# --------------------------------------------------------------------------- contracts


def test_lidar_contract_is_exact() -> None:
    assert checks.LIDAR_KWARGS == {
        'link_name': 'lidar_link',
        'frame_name': 'lidar_link',
        'topic': '/scan_raw',
        'update_rate': 10.0,
        'horizontal_samples': 720,
        'min_angle': -math.pi,
        'max_angle': math.pi,
        'range_min': 0.1,
        'range_max': 10.0,
    }


def _bash_array(script: str, name: str) -> list[str]:
    body = re.search(rf'^{name}=\((.*?)\)$', script, re.S | re.M).group(1)
    return shlex.split(body, comments=True)


def test_stack_launch_arguments() -> None:
    script = (STACK / 'stack.sh').read_text(encoding='utf-8')
    common = _bash_array(script, 'COMMON_ARGS')
    assert common == [
        'use_sim_time:=true',
        'lidar:=2d',
        'lidar_driver:=false',
        'overrides:=none',
        'config_watch:=off',
        'use_rviz:=false',
        'scan_filter_params_file:=$HERE/scan_filter_gazebo.yaml',
    ]
    assert _bash_array(script, 'MAPPING_ARGS') == ['${COMMON_ARGS[@]}']
    assert 'prepare_slam_params.py' in script
    assert 'MAPPING_ARGS+=("slam_params_file:=$DIR/slam_toolbox_gazebo.yaml")' in script
    navigation = _bash_array(script, 'NAVIGATION_ARGS')
    for arg in ('localization:=emcl2', 'planner:=vi', 'local_planner:=vi', 'nav2:=false',
                'map:=$MAP', 'map_loc:=$MAP', '${COMMON_ARGS[@]}',
                'extra_params_file:=$HERE/navigation_gazebo.yaml'):
        assert arg in navigation
    assert 'ros2 launch daifuku_stack mapping.launch.py "${MAPPING_ARGS[@]}"' in script
    assert 'ros2 launch daifuku_stack navigation.launch.py "${NAVIGATION_ARGS[@]}"' in script
    assert '--seed="${VTC_SEED:?VTC_SEED is required}"' in script
    assert '--goal="${VTC_GOAL:?VTC_GOAL is required}"' in script
    assert '--wait-nav2' not in script


def test_vi_planner_rust_underlay_is_required_and_sourced() -> None:
    underlay = '/opt/ros2_rust_ws/install/local_setup.bash'
    dockerfile = (STACK / 'Dockerfile').read_text(encoding='utf-8')
    assert f'test -f {underlay}' in dockerfile
    assert 'test -x /opt/usim_vtc_ws/install/vi_planner/lib/vi_planner/vi_planner' in dockerfile
    build = dockerfile.index('colcon build')
    assert dockerfile.rindex(f'source {underlay}', 0, build) < build
    script = (STACK / 'stack.sh').read_text(encoding='utf-8')
    order = [script.index(f'source {path}') for path in (
        '/opt/ros/humble/setup.bash', underlay, '/opt/usim_vtc_ws/install/setup.bash')]
    assert order == sorted(order)
    assert order[-1] < script.index('case $phase in')


def test_stack_never_starts_hardware_bringup() -> None:
    script = (STACK / 'stack.sh').read_text(encoding='utf-8')
    code = '\n'.join(line for line in script.splitlines() if not line.lstrip().startswith('#'))
    for forbidden in ('daifuku_bringup', 'robot_bringup', 'odom_fusion', 'lidar_bringup',
                      'raspicat_driver', 'raspimouse', 'livox', 'urg_node', 'ekf'):
        assert forbidden not in code, forbidden
    dockerfile = (STACK / 'Dockerfile').read_text(encoding='utf-8')
    build = re.search(r'--packages-select (.*?)--cmake-args', dockerfile, re.S).group(1).split()
    assert sorted(w for w in build if w != '\\') == sorted(
        ['daifuku_config', 'daifuku_config_manager', 'daifuku_stack', 'emcl2', 'raspicat_description', 'vi_planner']
    )
