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
"""Prove service and message traffic across an owned Podman user bridge."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--staged', type=Path, required=True, help='stage_assets.py output directory')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    staged = args.staged.resolve()
    network = 'vtc-bridge-proof-87'
    names = ['vtc-bridge-proof-server', 'vtc-bridge-proof-client']
    resources = json.loads((staged / 'mounts.json').read_text())
    env = {
        'ROS_DOMAIN_ID': '87', 'RMW_IMPLEMENTATION': 'rmw_fastrtps_cpp',
        'FASTDDS_BUILTIN_TRANSPORTS': 'UDPv4', 'ROS_LOCALHOST_ONLY': '0',
        'GAZEBO_MODEL_DATABASE_URI': '',
        'GAZEBO_MODEL_PATH': '/assets/0:/assets/1:/usr/share/gazebo-11/models',
    }
    common = ['--network', network, '--shm-size', '512m']
    for key, value in env.items():
        common += ['--env', f'{key}={value}']
    for source, target in [
        (here, '/diagnostics'), (staged, '/staged'), (args.out.resolve(), '/output'),
        *((Path(item['source']), item['target']) for item in resources),
    ]:
        common += ['--mount', f'type=bind,source={source},target={target}']
    server = ['podman', 'run', '--rm', '--name', names[0], *common, 'usim-gazebo:local',
              'bash', '-c', 'source /opt/ros/humble/setup.bash && exec python3 '
              f'/diagnostics/factory_probe.py --network {network} --out /output/server.json '
              '--world /staged/world.sdf --robot /staged/robot.urdf --hold-after-spawn']
    client = ['podman', 'run', '--rm', '--name', names[1], *common,
              'daifuku-usim-vtc-stack:local', 'bash', '-c',
              'source /opt/ros/humble/setup.bash && exec python3 /diagnostics/bridge_client.py']
    create = ['podman', 'network', 'create', network]
    result = dict(env=env, network=network, create_argv=create, server_argv=server,
                  client_argv=client, logs=['server.stdout.log', 'server.stderr.log',
                                           'client.stdout.log', 'client.stderr.log'])
    (args.out / 'provenance.json').write_text(json.dumps(result, indent=2))
    subprocess.run(create, check=True, timeout=30)
    try:
        with (args.out / 'server.stdout.log').open('w') as stdout, (
            args.out / 'server.stderr.log'
        ).open('w') as stderr:
            process = subprocess.Popen(server, stdout=stdout, stderr=stderr)
            try:
                with (args.out / 'client.stdout.log').open('w') as out, (
                    args.out / 'client.stderr.log'
                ).open('w') as err:
                    result['client_exit'] = subprocess.run(
                        client, stdout=out, stderr=err, timeout=70,
                    ).returncode
            finally:
                for name in names:
                    if subprocess.run(['podman', 'container', 'exists', name]).returncode == 0:
                        if name == names[0]:
                            subprocess.run(['podman', 'stop', '--time', '10', name],
                                           check=True, timeout=20)
                    if subprocess.run(['podman', 'container', 'exists', name]).returncode == 0:
                        subprocess.run(['podman', 'rm', '-f', name], check=True, timeout=20)
                process.wait(timeout=20)
    finally:
        subprocess.run(['podman', 'network', 'rm', network], check=True, timeout=30)
        result['leftover'] = [
            name for name in names
            if subprocess.run(['podman', 'container', 'exists', name]).returncode == 0
        ]
        result['network_removed'] = True
        (args.out / 'provenance.json').write_text(json.dumps(result, indent=2))
    print('BRIDGE_PROOF_DONE', json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
