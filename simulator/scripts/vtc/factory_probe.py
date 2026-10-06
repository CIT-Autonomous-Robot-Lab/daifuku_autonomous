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
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# How to run: in the native Humble image, source ROS then:
# python3 /vtc/diagnostics/factory_probe.py --out /output/probe.json
"""Bounded real Gazebo factory request, isolated from other Gazebo masters."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import time
import uuid
import sys
import threading
from pathlib import Path
from typing import TypedDict

import rclpy
from gazebo_msgs.srv import SpawnEntity


class FactoryResult(TypedDict):
    world: str
    master_uri: str
    service_ready: bool
    request_completed: bool
    success: bool
    status_message: str
    elapsed_s: float
    argv: list[str]
    network: str
    env: dict[str, str | None]
    gzserver_argv: list[str]
    gzserver_log: str
    advertised_address: str | None


def main() -> int:
    """Launch one owned server and distinguish discovery from response failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', default='/usr/share/gazebo-11/worlds/empty.world')
    parser.add_argument('--robot', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--network', required=True)
    parser.add_argument('--hold-after-spawn', action='store_true')
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    master_uri = f'http://127.0.0.1:{port}'
    os.environ['GAZEBO_MASTER_URI'] = master_uri
    request = SpawnEntity.Request()
    request.name = 'vtc_factory_' + uuid.uuid4().hex
    request.xml = args.robot.read_text(encoding='utf-8') if args.robot else (
        '<sdf version="1.6"><model name="probe"><static>true</static>'
        '<link name="body"><collision name="box"><geometry>'
        '<box><size>0.1 0.1 0.1</size></box></geometry></collision>'
        '</link></model></sdf>'
    )
    request.reference_frame = 'world'
    result: FactoryResult = {
        'world': args.world,
        'master_uri': master_uri,
        'service_ready': False,
        'request_completed': False,
        'success': False,
        'status_message': '',
        'elapsed_s': 0.0,
        'argv': sys.argv,
        'network': args.network,
        'env': {key: os.environ.get(key) for key in (
            'ROS_DOMAIN_ID', 'RMW_IMPLEMENTATION', 'FASTDDS_BUILTIN_TRANSPORTS',
            'ROS_LOCALHOST_ONLY', 'GAZEBO_IP', 'GAZEBO_MASTER_URI',
            'GAZEBO_MODEL_PATH', 'GAZEBO_MODEL_DATABASE_URI',
        )},
        'gzserver_argv': ['gzserver', '--verbose', args.world, '-s', 'libgazebo_ros_init.so',
                         '-s', 'libgazebo_ros_factory.so'],
        'gzserver_log': str(args.out.with_suffix('.gzserver.log')),
        'advertised_address': None,
    }
    started = time.monotonic()
    with args.out.with_suffix('.gzserver.log').open('w', encoding='utf-8') as log:
        with subprocess.Popen(
            ['gzserver', '--verbose', args.world, '-s', 'libgazebo_ros_init.so',
             '-s', 'libgazebo_ros_factory.so'],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        ) as server:
            rclpy.init()
            node = rclpy.create_node('vtc_factory_probe')
            try:
                client = node.create_client(SpawnEntity, '/spawn_entity')
                result['service_ready'] = client.wait_for_service(timeout_sec=20.0)
                if result['service_ready']:
                    future = client.call_async(request)
                    rclpy.spin_until_future_complete(node, future, timeout_sec=20.0)
                    result['request_completed'] = future.done()
                    response = future.result() if future.done() else None
                    if response is not None:
                        result['success'] = response.success
                        result['status_message'] = response.status_message
                if args.hold_after_spawn and result['success']:
                    stopped = threading.Event()
                    signal.signal(signal.SIGTERM, lambda *_args: stopped.set())
                    stopped.wait(timeout=60)
                result['elapsed_s'] = time.monotonic() - started
            finally:
                node.destroy_node()
                rclpy.shutdown()
                if server.poll() is None:
                    os.killpg(server.pid, signal.SIGINT)
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(server.pid, signal.SIGKILL)
                        server.wait(timeout=5)
    text = args.out.with_suffix('.gzserver.log').read_text(encoding='utf-8')
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    address = re.search(r'Publicized address:\s*([^\s]+)', text)
    result['advertised_address'] = address.group(1) if address else None
    args.out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
