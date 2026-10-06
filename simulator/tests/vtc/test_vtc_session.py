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

from pathlib import Path

import pytest
from usim.cli import build_parser
from usim.runtime_cli import configured

from daifuku_sim.vtc.vtc_checks import robot_geometry
from daifuku_sim.vtc.vtc_contract import SessionConfig
from daifuku_sim.vtc.vtc_runtime import STACK, simulation_from_session, simulator_command
from .conftest import RASPICAT_URDF, write_world


@pytest.mark.parametrize('record_video', [False, True])
def test_session_cli_preserves_geometry_lidar_dds_and_camera(
    tmp_path: Path, record_video: bool,
) -> None:
    # Given: explicit consumer resources and nondefault container settings.
    urdf = tmp_path / 'robot.urdf'
    urdf.write_text(RASPICAT_URDF, encoding='utf-8')
    config: SessionConfig = {
        'phase': 'mapping',
        'usim_root': str(tmp_path),
        'world': str(write_world(tmp_path / 'world.sdf')),
        'robot': robot_geometry(urdf),
        'engine': 'podman',
        'network': 'owned-vtc-network',
        'image': 'vtc-test-image',
        'spawn': {'x': 0.0, 'y': 0.0, 'yaw': 0.0},
        'camera_enabled': record_video,
    }
    # When: the real usim parser consumes the workflow's command.
    command = simulator_command(config)
    args = build_parser().parse_args(command[4:])
    simulation = configured(args)
    # Then: the same public configuration reaches usim with stoppable supervision.
    assert simulation == simulation_from_session(config)
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
