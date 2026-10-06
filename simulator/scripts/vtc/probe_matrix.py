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
"""Run bounded factory probes with identical DDS settings and explicit provenance."""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
from pathlib import Path
from typing import TypedDict


class MatrixRow(TypedDict, total=False):
    label: str
    container: str
    network: str
    world: str
    robot: str
    argv: list[str]
    env: dict[str, str]
    stdout: str
    stderr: str
    exit_code: int
    timeout: bool
    leftover: bool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--staged', type=Path, required=True, help='stage_assets.py output directory')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    staged = args.staged.resolve()
    resources = json.loads((staged / 'mounts.json').read_text())
    args.out.mkdir(parents=True, exist_ok=False)
    rows = []
    for network, world, robot in itertools.product(
        ('host', 'bridge'), ('empty', 'vtc'), ('minimal', 'actual'),
    ):
        label = f'{network}-{world}-{robot}'
        name = 'vtc-probe-87-' + label
        env = {
            'ROS_DOMAIN_ID': '87', 'RMW_IMPLEMENTATION': 'rmw_fastrtps_cpp',
            'FASTDDS_BUILTIN_TRANSPORTS': 'UDPv4', 'ROS_LOCALHOST_ONLY': '0',
            'GAZEBO_MODEL_DATABASE_URI': '',
            'GAZEBO_MODEL_PATH': '/assets/0:/assets/1:/usr/share/gazebo-11/models',
        }
        command = ['podman', 'run', '--rm', '--name', name, '--network', network,
                   '--shm-size', '512m']
        for key, value in env.items():
            command += ['--env', f'{key}={value}']
        for source, target in [
            (here, '/diagnostics'), (staged, '/staged'), (args.out.resolve(), '/output'),
            *((Path(item['source']), item['target']) for item in resources),
        ]:
            command += ['--mount', f'type=bind,source={source},target={target}']
        probe = (
            'source /opt/ros/humble/setup.bash && exec python3 '
            f'/diagnostics/factory_probe.py --network {network} '
            f'--out /output/{label}.json --world '
            + ('/staged/world.sdf' if world == 'vtc'
               else '/usr/share/gazebo-11/worlds/empty.world')
            + (' --robot /staged/robot.urdf' if robot == 'actual' else '')
        )
        command += ['usim-gazebo:local', 'bash', '-c', probe]
        row: MatrixRow = dict(
            label=label, container=name, network=network, world=world, robot=robot,
            argv=command, env=env, stdout=f'{label}.stdout.log', stderr=f'{label}.stderr.log',
        )
        rows.append(row)
        (args.out / 'matrix.json').write_text(json.dumps(rows, indent=2))
        try:
            with (args.out / row['stdout']).open('w') as stdout, (
                args.out / row['stderr']
            ).open('w') as stderr:
                result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=65)
                row['exit_code'] = result.returncode
        except subprocess.TimeoutExpired:
            row['timeout'] = True
        finally:
            inspect = subprocess.run(
                ['podman', 'container', 'exists', name], capture_output=True,
            )
            if inspect.returncode == 0:
                subprocess.run(['podman', 'rm', '-f', name], check=True, timeout=20)
            row['leftover'] = subprocess.run(
                ['podman', 'container', 'exists', name], capture_output=True,
            ).returncode == 0
        (args.out / 'matrix.json').write_text(json.dumps(rows, indent=2))
        print('PROBE_DONE', label, row, flush=True)


if __name__ == '__main__':
    main()
