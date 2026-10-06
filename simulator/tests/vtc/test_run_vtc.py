"""Orchestration: early failure, strict phase order, verdicts and cleanup (fake engine/simulator)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from daifuku_sim.vtc import run_vtc
from .conftest import RASPICAT_URDF, write_map, write_world

from .vtc_test_support import (ALL_OK, mapping_ok, navigation_result, result_of, workflow)

# --------------------------------------------------------------------------- early failures


@pytest.mark.parametrize(
    'body',
    ['<sdf><world name="a"/><world name="b"/></sdf>', 'not xml at all', '<sdf><world><uri>gone.dae</uri></world></sdf>'],
)
def test_malformed_world_fails_before_any_engine_call(tmp_path, usim_root, body) -> None:
    write_world(usim_root / 'assets' / 'vtc' / 'world.sdf', body)
    flow, runtime, timeline, _ = workflow(tmp_path, usim_root, ALL_OK)
    assert flow.run() == run_vtc.EXIT_USAGE
    assert runtime.calls == [] and timeline == []
    result = result_of(tmp_path)
    assert (result['status'], result['stage']) == ('FAIL', 'preflight')
    assert 'malformed VTC world' in result['reason'] and 'world.sdf' in result['reason']
    assert result['cleanup'] == {'engine_calls': False}


def test_missing_world_path_is_named(tmp_path, usim_root) -> None:
    missing = tmp_path / 'daifuku-missing-vtc-world.sdf'
    flow, runtime, _, _ = workflow(tmp_path, usim_root, ALL_OK, extra=('--world', str(missing)))
    assert flow.run() == run_vtc.EXIT_USAGE
    assert runtime.calls == []
    assert str(missing) in result_of(tmp_path)['reason']


def test_usim_root_is_required(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv('USIM_ROOT', raising=False)
    flow, runtime, _, _ = workflow(tmp_path, None, ALL_OK)
    assert flow.run() == run_vtc.EXIT_USAGE
    assert runtime.calls == []
    assert 'USIM_ROOT is required' in result_of(tmp_path)['reason']


def test_invalid_asset_checkout_fails_before_engine_calls(tmp_path) -> None:
    flow, runtime, _, _ = workflow(tmp_path, tmp_path, ALL_OK)
    assert flow.run() == run_vtc.EXIT_USAGE
    assert runtime.calls == []
    result = result_of(tmp_path)
    assert result['status'] == 'FAIL' and result['stage'] == 'preflight'


def test_unavailable_engine_is_blocked_with_exact_output(tmp_path, usim_root) -> None:
    output = 'Cannot connect to Podman. Please verify your connection to the Linux system'
    flow, runtime, timeline, _ = workflow(tmp_path, usim_root, ALL_OK, available=(False, output))
    assert flow.run() == run_vtc.EXIT_BLOCKED
    assert runtime.calls == [('available',)] and timeline == []
    result = result_of(tmp_path)
    assert (result['status'], result['stage'], result['detail']) == ('BLOCKED', 'runtime', output)


def test_asset_checkout_is_not_used_to_import_usim(tmp_path, usim_root) -> None:
    # Given: resource checkout sources are unusable; packages are installed separately.
    source = usim_root / 'src' / 'usim'
    source.mkdir(parents=True)
    (source / '__init__.py').write_text('raise RuntimeError("asset checkout is not a package")')
    # When: the workflow uses the checkout only for assets and build context.
    flow, _, _, _ = workflow(tmp_path, usim_root, ALL_OK)
    # Then: package import and the workflow succeed without modifying that checkout.
    assert flow.run() == run_vtc.EXIT_PASS
    assert not list(usim_root.rglob('__pycache__'))

# --------------------------------------------------------------------------- full sequence


def test_happy_path_is_serial_and_passes_on_world_truth(tmp_path, usim_root) -> None:
    flow, runtime, timeline, start = workflow(tmp_path, usim_root, ALL_OK)
    assert flow.run() == run_vtc.EXIT_PASS
    assert timeline == [
        ('container-start', 'prepare'),
        ('container-exit', 'prepare'),
        ('gazebo-start', 'mapping'),
        ('container-start', 'mapping'),
        ('container-exit', 'mapping'),
        ('container-start', 'navigation'),
        ('container-exit', 'navigation'),
        ('gazebo-stop', 'mapping'),
    ]
    env = runtime.started_env
    seed = run_vtc.checks.parse_pose(env['navigation']['VTC_SEED'])
    assert seed.as_dict() == pytest.approx(result_of(tmp_path)['transition']['navigation_seed_map'])
    assert run_vtc.checks.parse_pose(env['navigation']['VTC_GOAL']).as_dict() == pytest.approx({
        'x': 0.025,
        'y': -0.975,
        'yaw': -1.378,
    })
    assert env['mapping']['VTC_SURVEY'] == '2.5,0.0;2.5,2.5;0.0,2.5;-1.055,-0.25'
    assert env['mapping']['VTC_WORLD_GOAL'] == '0.0,-1.0'
    for phase_env in env.values():
        assert phase_env['ROS_DOMAIN_ID'] == '87'
        assert phase_env['RMW_IMPLEMENTATION'] == 'rmw_fastrtps_cpp'
        assert phase_env['FASTDDS_BUILTIN_TRANSPORTS'] == 'UDPv4'
    result = result_of(tmp_path)
    assert (result['status'], result['stage']) == ('PASS', 'verdict')
    assert result['truth']['within_tolerance'] is True
    assert result['truth']['goal_world']['x'] == pytest.approx(0.0)
    assert result['truth']['goal_world']['y'] == pytest.approx(-1.0)
    assert result['alignment']['origin_seed_supported'] is True
    assert result['robot']['wheel_separation'] == pytest.approx(0.27918)
    assert result['cleanup']['leftover'] == []
    assert ('build', 'daifuku-usim-vtc-stack:local', {'BASE_IMAGE': 'daifuku-autonomous:humble'}) in runtime.calls
    assert 'mapping/vtc_map.yaml' in result['logs']
    network = flow._name('network')
    assert ('create_network', network) in runtime.calls
    assert start.networks == [network]
    assert len(start.sessions) == 1
    assert result['transition']['mode'] == 'continuous-gazebo'
    assert runtime.calls[-1] == ('remove_network', network)
    assert result['cleanup']['network_removed'] is True


def test_host_network_is_explicit_and_not_owned(tmp_path, usim_root) -> None:
    flow, runtime, _, start = workflow(
        tmp_path, usim_root, ALL_OK, extra=('--network', 'host'),
    )
    assert flow.run() == run_vtc.EXIT_PASS
    assert not any(call[0] == 'create_network' for call in runtime.calls)
    assert start.networks == ['host']


@pytest.mark.parametrize('record', [False, True])
def test_gazebo_camera_is_enabled_only_for_video(tmp_path, usim_root, record) -> None:
    # Given: the regular workflow or an explicitly requested camera recording.
    extra = ('--record-video',) if record else ()
    flow, _, _, _ = workflow(tmp_path, usim_root, ALL_OK, extra=extra)
    # When: the workflow starts the actual simulator session configuration.
    assert flow.run() == run_vtc.EXIT_PASS
    session = json.loads((tmp_path / 'run' / 'mapping' / 'session.json').read_text())
    # Then: only video recording pays the rendering cost.
    assert session['camera_enabled'] is record


def test_container_runtime_passes_shared_network_to_stack(tmp_path, monkeypatch) -> None:
    import subprocess
    from daifuku_sim.vtc.vtc_runtime import ContainerRuntime

    commands = []

    class Finished:
        def wait(self, timeout=None):
            return 0

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    def popen(command, **kwargs):
        commands.append(command)
        return Finished()

    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    runtime = ContainerRuntime('podman')
    runtime.create_network('owned-vtc-network')
    handle = runtime.start('owned-stack', 'stack-image', {}, tmp_path, ['true'],
                           tmp_path / 'stack.log')
    assert handle.wait() == 0
    assert commands[0] == ['podman', 'network', 'create', '--driver', 'bridge', 'owned-vtc-network']
    assert commands[1][commands[1].index('--network') + 1] == 'owned-vtc-network'
    runtime.remove_network()
    assert commands[-1] == ['podman', 'network', 'rm', 'owned-vtc-network']


def test_usim_port_passes_explicit_network(tmp_path, monkeypatch) -> None:
    import subprocess

    from usim_gazebo import GazeboSimulator
    from usim.robot import MobileRobot, render_robot
    from usim.simulation import SimulationConfig

    world, robot = tmp_path / 'world.sdf', tmp_path / 'robot.urdf'
    world.write_text('<sdf version="1.6"><world name="test"/></sdf>')
    robot.write_text(render_robot(MobileRobot()))
    commands = []

    class Finished:
        def wait(self, timeout=None):
            return 0

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='')

    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(subprocess, 'Popen', lambda *_args: Finished())
    GazeboSimulator(engine='podman', network='owned-vtc-network').run(
        SimulationConfig(world, robot, headless=True),
    )
    create = commands[0]
    assert create[:2] == ['podman', 'create']
    assert create[create.index('--network') + 1] == 'owned-vtc-network'


def test_aborted_action_fails_even_near_goal(tmp_path, usim_root) -> None:
    phases = {**ALL_OK, 'navigation': navigation_result(status=6)}
    flow, _, _, _ = workflow(tmp_path, usim_root, phases)
    assert flow.run() == run_vtc.EXIT_FAIL
    result = result_of(tmp_path)
    assert (result['status'], result['stage']) == ('FAIL', 'verdict')
    assert 'ABORTED' in result['reason']


def test_succeeded_but_off_target_in_world_fails(tmp_path, usim_root) -> None:
    phases = {**ALL_OK, 'navigation': navigation_result(final=(1.6, 1.25))}
    flow, _, _, _ = workflow(tmp_path, usim_root, phases)
    assert flow.run() == run_vtc.EXIT_FAIL
    assert 'world-truth goal error' in result_of(tmp_path)['reason']


# --------------------------------------------------------------------------- cleanup


def test_mapping_failure_stops_gazebo_and_never_navigates(tmp_path, usim_root) -> None:
    def mapping_fails(mount: Path, env: dict) -> int:
        (mount / 'mapping').mkdir(parents=True, exist_ok=True)
        (mount / 'mapping' / 'mapping.json').write_text(
            json.dumps({'ok': False, 'error': 'SLAM map has only 12 known cells (< 500)'}), encoding='utf-8'
        )
        return 11

    flow, runtime, timeline, start = workflow(tmp_path, usim_root, {**ALL_OK, 'mapping': mapping_fails})
    assert flow.run() == run_vtc.EXIT_FAIL
    assert ('gazebo-stop', 'mapping') in timeline
    assert not any(phase == 'navigation' for _, phase in timeline)
    assert all(session.stopped for session in start.sessions)
    result = result_of(tmp_path)
    assert result['stage'] == 'mapping' and '12 known cells' in result['reason']
    assert result['cleanup']['leftover'] == [] and runtime.live == set()


def test_gazebo_dying_mid_phase_removes_the_stack_container(tmp_path, usim_root) -> None:
    hangs = {**ALL_OK, 'mapping': lambda mount, env: None}
    flow, runtime, timeline, _ = workflow(tmp_path, usim_root, hangs, die_in='mapping')
    assert flow.run() == run_vtc.EXIT_FAIL
    result = result_of(tmp_path)
    assert result['stage'] == 'mapping' and 'ended early' in result['reason']
    name = flow._name('mapping')
    assert ('remove', name) in runtime.calls and name not in runtime.live
    assert ('gazebo-stop', 'mapping') in timeline
    assert not any(phase == 'navigation' for _, phase in timeline)


def test_cleanup_preserves_parallel_users_gazebo_containers(tmp_path, usim_root) -> None:
    # Given: a workflow whose mapping phase overlaps another user's Gazebo start.
    flow, runtime, _, _ = workflow(tmp_path, usim_root, ALL_OK)
    runtime.live.update({'isaacsim_pi4', 'pi4sim', 'usim-gazebo-existing'})
    original = runtime.phases['mapping']

    def mapping_with_parallel_user(mount: Path, env: dict) -> int:
        runtime.live.add('usim-gazebo-parallel-user')
        return original(mount, env)

    runtime.phases = {**runtime.phases, 'mapping': mapping_with_parallel_user}
    # When: the workflow completes and cleans up.
    assert flow.run() == run_vtc.EXIT_PASS
    # Then: cleanup has preserved every container it does not own.
    assert runtime.live == {
        'isaacsim_pi4', 'pi4sim', 'usim-gazebo-existing', 'usim-gazebo-parallel-user'
    }


def test_stack_timeout_is_enforced_and_cleaned(tmp_path, usim_root) -> None:
    hangs = {**ALL_OK, 'mapping': lambda mount, env: None}
    flow, runtime, _, _ = workflow(tmp_path, usim_root, hangs, extra=('--mapping-timeout', '0'))
    assert flow.run() == run_vtc.EXIT_FAIL
    result = result_of(tmp_path)
    assert 'exceeded' in result['reason'] and runtime.live == set()


def test_large_offset_keeps_origin_restart_rejected_but_reuses_world(tmp_path, usim_root) -> None:
    phases = {**ALL_OK, 'mapping': mapping_ok(alignment=(0.4, 0.0, 0.0))}
    flow, _, timeline, _ = workflow(tmp_path, usim_root, phases)
    assert flow.run() == run_vtc.EXIT_PASS
    result = result_of(tmp_path)
    assert result['alignment']['origin_seed_supported'] is False
    assert result['transition']['mode'] == 'continuous-gazebo'
    assert ('container-start', 'navigation') in timeline
    assert [phase for action, phase in timeline if action == 'gazebo-start'] == ['mapping']


def test_continuous_transition_uses_current_pose_and_fixed_world_goal(tmp_path, usim_root) -> None:
    end_world = (0.3, 0.2, -1.2)
    phases = {
        **ALL_OK,
        'mapping': mapping_ok((0.2, -0.3, 0.4), end_world),
        'navigation': navigation_result(start=end_world),
    }
    flow, runtime, _, start = workflow(tmp_path, usim_root, phases)
    assert flow.run() == run_vtc.EXIT_PASS
    result = result_of(tmp_path)
    assert result['transition']['odom_pose'] == dict(zip(('x', 'y', 'yaw'), end_world))
    assert result['transition']['translation_consistency_m'] < 1e-10
    assert result['transition']['yaw_consistency_rad'] < 1e-10
    assert result['truth']['goal_world'] == pytest.approx({'x': 0.0, 'y': -1.0, 'yaw': -1.38})
    assert result['truth']['start_world']['x'] == pytest.approx(0.3)
    assert len(start.sessions) == 1
    seed = run_vtc.checks.parse_pose(runtime.started_env['navigation']['VTC_SEED'])
    assert seed.as_dict() == pytest.approx(result['transition']['navigation_seed_map'])


def test_inconsistent_transition_capture_blocks_navigation(tmp_path, usim_root) -> None:
    original = mapping_ok()

    def inconsistent(mount, env):
        code = original(mount, env)
        path = mount / 'mapping' / 'mapping.json'
        data = json.loads(path.read_text())
        data['slam_vs_truth']['slam_map']['x'] += 0.2
        path.write_text(json.dumps(data))
        return code

    flow, _, timeline, _ = workflow(tmp_path, usim_root, {**ALL_OK, 'mapping': inconsistent})
    assert flow.run() == run_vtc.EXIT_FAIL
    assert result_of(tmp_path)['stage'] == 'transition'
    assert not any(phase == 'navigation' for _, phase in timeline)


def test_goal_off_the_mapped_free_space_fails_before_navigation(tmp_path, usim_root) -> None:
    flow, _, timeline, _ = workflow(tmp_path, usim_root, ALL_OK, extra=('--goal', '9,9,0'))
    assert flow.run() == run_vtc.EXIT_FAIL
    result = result_of(tmp_path)
    assert result['stage'] == 'goal' and 'no safe known-free map cell' in result['reason']
    assert not any(phase == 'navigation' for _, phase in timeline)


def test_default_alignment_limits() -> None:
    parsed = run_vtc.parse_args(['--usim-root', '.'])
    assert parsed.align_max_translation == 0.10
    assert parsed.align_max_yaw == pytest.approx(math.radians(2.0))
    assert parsed.engine in ('podman', 'docker')
    assert parsed.goal == run_vtc.checks.Pose2D(0.0, -1.0, -1.38)
    assert parsed.survey[-1] == (-1.055, -0.25)


def test_transition_seed_snaps_before_navigation_when_cell_is_occupied(tmp_path, usim_root) -> None:
    mapping = mapping_ok()

    def seed_overlaps_wall(mount: Path, env: dict) -> int:
        code = mapping(mount, env)
        yaml_path = write_map(mount / 'mapping')
        map_bytes = yaml_path.with_suffix('.pgm').read_bytes()
        header = b'P5\n# CREATOR: test\n120 120\n255\n'
        assert map_bytes.startswith(header)
        pixels = bytearray(map_bytes[len(header):])
        pixels[(120 - 1 - 19) * 120 + 20] = 0
        yaml_path.with_suffix('.pgm').write_bytes(header + pixels)
        return code

    phases = {**ALL_OK, 'mapping': seed_overlaps_wall}
    flow, _, timeline, _ = workflow(tmp_path, usim_root, phases)
    assert flow.run() == run_vtc.EXIT_PASS
    transition = result_of(tmp_path)['transition']
    assert transition['seed_adjustment_m'] > 0
    assert transition['seed_adjustment_m'] <= 0.25
    assert transition['navigation_seed_map'] != transition['map_pose']
    assert any(phase == 'navigation' for _, phase in timeline)


@pytest.mark.parametrize('option', ['--tolerance', '--goal-timeout', '--mapping-timeout'])
@pytest.mark.parametrize('value', ['nan', 'inf', '-1'])
def test_acceptance_bounds_reject_invalid_cli_values(option: str, value: str) -> None:
    # Given: a non-finite or negative acceptance bound.
    # When / Then: the CLI rejects it rather than allowing an unbounded run.
    with pytest.raises(SystemExit) as error:
        run_vtc.parse_args([option, value])
    assert error.value.code == run_vtc.EXIT_USAGE


def test_existing_run_data_is_preserved(tmp_path, usim_root) -> None:
    # Given: an explicitly selected output directory already contains user data.
    existing = tmp_path / 'run'
    existing.mkdir()
    previous = existing / 'result.json'
    previous.write_text('previous user data', encoding='utf-8')
    # When: a new workflow uses that directory.
    flow, _, _, _ = workflow(tmp_path, usim_root, ALL_OK)
    assert flow.run() == run_vtc.EXIT_PASS
    # Then: it writes into a fresh child without overwriting the previous result.
    assert previous.read_text(encoding='utf-8') == 'previous user data'
    assert flow.run_dir.parent == existing
    assert json.loads((flow.run_dir / 'result.json').read_text(encoding='utf-8'))['status'] == 'PASS'
