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
"""Bounded cross-container factory service and simulator message probe."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import rclpy
from std_srvs.srv import Empty
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import LaserScan
from rclpy.qos import qos_profile_sensor_data


def main() -> int:
    rclpy.init()
    node = rclpy.create_node('vtc_bridge_client')
    counts = {'clock': 0, 'odom': 0, 'scan_raw': 0}

    def count(key):
        def callback(_message):
            counts[key] += 1
        return callback

    for kind, topic, key in (
        (Clock, '/clock', 'clock'), (Odometry, '/odom', 'odom'),
        (LaserScan, '/scan_raw', 'scan_raw'),
    ):
        node.create_subscription(kind, topic, count(key), qos_profile_sensor_data)
    client = node.create_client(Empty, '/unpause_physics')
    result = {
        'argv': sys.argv,
        'env': {key: os.environ.get(key) for key in (
            'ROS_DOMAIN_ID', 'RMW_IMPLEMENTATION', 'FASTDDS_BUILTIN_TRANSPORTS',
            'ROS_LOCALHOST_ONLY',
        )},
        'service_ready': client.wait_for_service(timeout_sec=20),
        'request_completed': False, 'success': False, 'counts': counts,
    }
    try:
        if result['service_ready']:
            future = client.call_async(Empty.Request())
            rclpy.spin_until_future_complete(node, future, timeout_sec=20)
            result['request_completed'] = future.done()
            if future.done():
                result['success'] = future.result() is not None
        deadline = time.monotonic() + 20
        while not all(value >= 2 for value in counts.values()) and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
        result['ok'] = result['success'] and all(value >= 2 for value in counts.values())
        Path('/output/client.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        return 0 if result['ok'] else 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
