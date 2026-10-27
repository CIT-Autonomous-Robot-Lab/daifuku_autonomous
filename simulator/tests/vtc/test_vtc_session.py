# Copyright 2026 Keita Sekiguchi / nop
#
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
"""The workflow invokes the installed usim CLI with its measured configuration."""

import json
from pathlib import Path

import pytest
from usim.cli import build_parser
from usim.runtime_cli import configured

from daifuku_sim.vtc.vtc_checks import robot_geometry
from daifuku_sim.vtc.vtc_contract import SessionConfig
from daifuku_sim.vtc.run_vtc import parse_args
from daifuku_sim.vtc.vtc_runtime import STACK, simulator_command
from daifuku_sim.vtc import vtc_runtime as runtime
from daifuku_sim import wslg
from .conftest import RASPICAT_URDF, write_world


@pytest.mark.parametrize('record_video', [False, True])
@pytest.mark.parametrize('headless', [False, True])
def test_session_cli_preserves_geometry_lidar_dds_and_camera(
    tmp_path: Path, record_video: bool, headless: bool,
) -> None:
    # Given: explicit consumer resources and nondefault container settings.
    urdf = tmp_path / 'robot.urdf'
    urdf.write_text(RASPICAT_URDF, encoding='utf-8')
    config: SessionConfig = {
        'world': str(write_world(tmp_path / 'world.sdf')),
        'robot': robot_geometry(urdf),
        'engine': 'podman',
        'network': 'owned-vtc-network',
        'image': 'vtc-test-image',
        'camera_enabled': record_video,
        'headless': parse_args(['--headless'] if headless else []).headless,
    }
    # When: the real usim parser consumes the workflow's command.
    command = simulator_command(config)
    args = build_parser().parse_args(command[4:])
    simulation = configured(args)
    # Then: the same public configuration reaches usim with stoppable supervision.
    robot = config['robot']
    assert simulation.world == Path(config['world'])
    assert simulation.robot_urdf == urdf
    assert (
        simulation.base_link, simulation.left_joint, simulation.right_joint,
        simulation.wheel_radius, simulation.wheel_separation,
    ) == (
        robot['base_link'], robot['left_joint'], robot['right_joint'],
        robot['wheel_radius'], robot['wheel_separation'],
    )
    assert simulation.robot_name == 'raspicat'
    assert simulation.headless is headless
    assert simulation.max_seconds == 0.0
    assert simulation.camera_enabled is record_video
    assert (simulation.camera_width, simulation.camera_height) == (640, 360)
    assert simulation.camera_offset == (0.20, 0.0, 0.40)
    assert (args.backend, args.engine, args.image, args.network) == (
        'gazebo', 'podman', 'vtc-test-image', 'owned-vtc-network',
    )
    assert args.stop_on_stdin is True
    assert args.fastdds_profile == STACK / 'fastdds_udp.xml'
    assert (args.lidar_link, args.lidar_frame, args.lidar_topic) == (
        'lidar_link', 'lidar_link', '/scan_raw',
    )
    assert args.lidar_horizontal_samples == 720
    assert args.lidar_update_rate == 10.0
    assert args.lidar_min_angle == -3.141592653589793
    assert args.lidar_max_angle == 3.141592653589793
    assert (args.lidar_range_min, args.lidar_range_max) == (0.1, 10.0)


@pytest.mark.parametrize(('platform', 'headless', 'display', 'automatic'), [
    ('win32', False, '', True),
    ('win32', True, '', False),
    ('win32', False, 'external:2', False),
    ('linux', False, '', False),
])
def test_session_owns_wslg_only_for_windows_gui_without_display(
    tmp_path, monkeypatch, platform, headless, display, automatic,
) -> None:
    # Given: an external simulator process and an observable display lifetime.
    events = []
    environments = []

    class Child:
        def poll(self):
            return None

        def stop(self, timeout):
            events.append('simulator stopped')
            return 0

    class Display:
        display = 'owned-display:0'

        def __init__(self, image, network):
            events.append((image, network))

        def close(self):
            events.append('display closed')

    def start(command, log, env):
        environments.append(env)
        return Child()

    monkeypatch.setattr(runtime.sys, 'platform', platform)
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.setattr(runtime, '_start_session', start)
    monkeypatch.setattr(wslg, 'WslgDisplay', Display)
    urdf = tmp_path / 'robot.urdf'
    urdf.write_text(RASPICAT_URDF, encoding='utf-8')
    config = {
        'world': str(write_world(tmp_path / 'world.sdf')),
        'robot': robot_geometry(urdf),
        'engine': 'docker', 'network': 'owned-network', 'image': 'gazebo-image',
        'camera_enabled': False, 'headless': headless,
    }
    path = tmp_path / 'session.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    env = {'DISPLAY': display} if display else {}
    # When: the simulator starts and its owner subsequently stops it.
    session = runtime.start_gazebo_session(path, tmp_path / 'simulator.log', env)
    assert session.stop(timeout=5) == 0
    # Then: only automatic WSLg adds a display, which outlives the simulator.
    assert environments == ([{'DISPLAY': 'owned-display:0'}] if automatic else [env])
    assert events == (
        [('gazebo-image', 'owned-network'), 'simulator stopped', 'display closed']
        if automatic else ['simulator stopped']
    )
