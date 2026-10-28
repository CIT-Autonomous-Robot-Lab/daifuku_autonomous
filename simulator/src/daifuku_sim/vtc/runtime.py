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
"""VTC resource paths, verdict codes and Gazebo CLI configuration."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from ..runtime import Session, start_session
from .contract import LIDAR, SessionConfig

if TYPE_CHECKING:
    from ..wslg import WslgDisplay

HERE = Path(__file__).resolve().parent
SIMULATOR = HERE.parents[2]
REPO = SIMULATOR.parent
STACK = REPO / 'docker' / 'simulator'
SCHEMA = 'daifuku-usim-vtc/1'
EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_BLOCKED = 0, 1, 2, 3
CONTAINER_PREFIX = 'daifuku-usim-vtc-'
RMW = 'rmw_fastrtps_cpp'
FASTDDS_TRANSPORTS = 'UDPv4'
URDF_RELATIVE = Path('robot') / 'raspicat_description' / 'urdf' / 'raspicat_plain.urdf'
MAP_RELATIVE = Path('mapping') / 'vtc_map.yaml'

class Stop(Exception):
    """End the workflow with a classified status."""

    def __init__(self, status: str, stage: str, reason: str, code: int, detail: str = '') -> None:
        super().__init__(reason)
        self.status, self.stage, self.reason, self.code, self.detail = (
            status,
            stage,
            reason,
            code,
            detail,
        )


def start_gazebo_session(config: Path, log: Path, env: dict[str, str]) -> Session:
    """Run the installed usim CLI; usim owns simulator startup and cancellation."""
    data: SessionConfig = json.loads(config.read_text(encoding='utf-8'))
    if (sys.platform == 'win32' and data['engine'] == 'docker' and not data['headless']
            and not env.get('DISPLAY', os.environ.get('DISPLAY'))):
        from ..wslg import WslgDisplay

        display = WslgDisplay(data['image'], data['network'])
        try:
            session = start_session(simulator_command(data), log, {**env, 'DISPLAY': display.display})
        except OSError:
            display.close()
            raise
        return _DisplaySession(session, display)
    return start_session(simulator_command(data), log, env)


class _DisplaySession:
    """Stop the simulator before releasing its owned display connection."""

    def __init__(self, session: Session, display: WslgDisplay) -> None:
        self.session, self.display = session, display

    def poll(self) -> int | None:
        return self.session.poll()

    def stop(self, timeout: float) -> int:
        try:
            return self.session.stop(timeout)
        finally:
            self.display.close()


def simulator_command(config: SessionConfig) -> list[str]:
    """Pass measured robot geometry and VTC settings to the installed usim CLI."""
    robot = config['robot']
    command = [sys.executable, '-B', '-m', 'usim.cli', 'simulate',
               '--backend', 'gazebo', '--stop-on-stdin']
    if config['headless']:
        command.append('--headless')
    values = {
        'world': config['world'],
        'robot-urdf': robot['urdf'],
        'robot-name': 'raspicat',
        'base-link': robot['base_link'],
        'left-joint': robot['left_joint'],
        'right-joint': robot['right_joint'],
        'wheel-radius': robot['wheel_radius'],
        'wheel-separation': robot['wheel_separation'],
        'camera-width': 640,
        'camera-height': 360,
        'engine': config['engine'],
        'image': config['image'],
        'network': config['network'],
        'fastdds-profile': STACK / 'fastdds_udp.xml',
    }
    for flag, value in values.items():
        command += ['--' + flag, str(value)]
    for name, value in LIDAR.items():
        flag = 'link' if name == 'link_name' else 'frame' if name == 'frame_name' else name
        # Equals keeps negative angles from being mistaken for CLI options.
        command.append(f'--lidar-{flag.replace("_", "-")}={value}')
    command += ['--camera-offset', '0.2', '0.0', '0.4']
    if not config['camera_enabled']:
        command.append('--physics-only')
    return command
