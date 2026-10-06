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
"""Trace independent Gazebo world pose, odometry, and SLAM alignment during mapping."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TypedDict


class ObserverProvenance(TypedDict):
    phase: str
    argv: list[str]
    network: str
    container: str
    env: dict[str, str]


class TraceProvenance(TypedDict):
    workflow_argv: list[str]
    env: dict[str, str]
    observers: list[ObserverProvenance]
    workflow_exit: int | None
    observer_exits: list[int]

NATIVE = r'''
import json, math, signal, time
from pathlib import Path
import rclpy
from gazebo_msgs.srv import GetEntityState
from nav_msgs.msg import Odometry
from tf2_msgs.msg import TFMessage
from action_msgs.msg import GoalStatusArray
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
rclpy.init()
node = rclpy.create_node('vtc_independent_mapping_trace')
odom = None
alignment = None
pending = None
stopped = False
terminal = None
def pose(p):
    q = p.orientation
    return dict(x=p.position.x, y=p.position.y, z=p.position.z,
                yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z)),
                q=dict(x=q.x,y=q.y,z=q.z,w=q.w))
def on_odom(message):
    global odom
    odom = dict(pose=pose(message.pose.pose),
                stamp=message.header.stamp.sec+message.header.stamp.nanosec*1e-9)
def on_tf(message):
    global alignment
    for t in message.transforms:
        if t.header.frame_id.lstrip('/') == 'map' and t.child_frame_id.lstrip('/') == 'odom':
            q = t.transform.rotation
            alignment = dict(x=t.transform.translation.x,y=t.transform.translation.y,
                yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z)),
                stamp=t.header.stamp.sec+t.header.stamp.nanosec*1e-9)
node.create_subscription(Odometry,'/odom',on_odom,20)
node.create_subscription(TFMessage,'/tf',on_tf,100)
client = node.create_client(GetEntityState,'/gazebo/get_entity_state')
def on_status(message):
    global terminal
    complete = [item for item in message.status_list if item.status in (4,5,6)]
    if complete:
        item = complete[-1]
        terminal = dict(status=item.status,goal_id=list(item.goal_info.goal_id.uuid),
                        observed_at=time.time())
        sample()
status_qos = QoSProfile(depth=10,reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL)
node.create_subscription(GoalStatusArray,'/navigate_to_pose/_action/status',on_status,status_qos)
def shutdown(*_args):
    global stopped
    stopped = True
signal.signal(signal.SIGTERM,shutdown)
samples = 0
with Path('/dev/stdout').open('w') as stream:
    def sample():
        global pending, samples
        if odom is None or not client.service_is_ready() or pending is not None:
            return
        before = odom
        transform = alignment
        request = GetEntityState.Request(name='raspicat',reference_frame='world')
        pending = client.call_async(request)
        def complete(future):
            global pending, samples
            response = future.result()
            row = dict(odom_before=before,odom_after=odom,map_to_odom=transform,
                       received_at=time.time(),success=response.success,action_terminal=terminal)
            if response.success:
                row['world'] = pose(response.state.pose)
                row['world_stamp'] = response.header.stamp.sec+response.header.stamp.nanosec*1e-9
            stream.write(json.dumps(row)+'\n')
            stream.flush()
            samples += 1
            pending = None
        pending.add_done_callback(complete)
    node.create_timer(0.5,sample)
    deadline = time.monotonic()+600
    try:
        while not stopped and time.monotonic()<deadline:
            rclpy.spin_once(node,timeout_sec=0.2)
        print('MAPPING_TRACE_SAMPLES',samples,flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--usim-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--stack-image', default='daifuku-usim-vtc-stack:local')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    source = args.usim_root.resolve() / 'assets' / 'vtc' / 'world.sdf'
    tree = ET.parse(source)
    for uri in tree.iter('uri'):
        if uri.text and '://' not in uri.text and not Path(uri.text).is_absolute():
            uri.text = (source.parent / uri.text).resolve().as_posix()
    world_element = tree.find('world')
    assert world_element is not None
    plugin = ET.SubElement(world_element, 'plugin', name='diagnostic_world_state',
                           filename='libgazebo_ros_state.so')
    ros = ET.SubElement(plugin, 'ros')
    ET.SubElement(ros, 'namespace').text = '/gazebo'
    ET.SubElement(plugin, 'update_rate').text = '20'
    world = args.out / 'diagnostic-world.sdf'
    tree.write(world, encoding='utf-8')
    run = args.out / ('mapping-trace-' + uuid.uuid4().hex[:12])
    workflow_args = ['--usim-root', str(args.usim_root.resolve()), '--engine', 'podman',
                '--world', str(world.resolve()), '--network', 'bridge', '--no-build',
                '--stack-image', args.stack_image,
                '--base-image', 'daifuku-autonomous:humble-amd64',
                '--run-dir', str(run.resolve()), '--mapping-timeout', '240',
                '--navigation-timeout', '240']
    from daifuku_sim.vtc.run_vtc import Workflow, parse_args
    from daifuku_sim.vtc.vtc_runtime import ContainerRuntime, simulator_command
    provenance = TraceProvenance(
                      workflow_argv=[sys.executable, '-m', 'daifuku_sim.vtc.run_vtc', *workflow_args],
                      observers=[],
                      workflow_exit=None,
                      observer_exits=[],
                      env={key: value for key, value in os.environ.items()
                           if key in ('ROS_DOMAIN_ID','RMW_IMPLEMENTATION',
                                      'FASTDDS_BUILTIN_TRANSPORTS')})
    observers = []
    streams = []

    class TracedSession:
        def __init__(self, config: Path, log: Path, env: dict[str, str]):
            stream = log.open('w')
            ready = threading.Event()
            identity = []
            command = simulator_command(json.loads(config.read_text(encoding='utf-8')))
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env={**os.environ, **env, 'PYTHONUNBUFFERED':'1'})

            def record():
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    stream.write(line)
                    stream.flush()
                    if len(line.strip()) == 64 and all(c in '0123456789abcdef' for c in line.strip()):
                        identity.append(line.strip())
                    if line.strip() == 'USIM_GAZEBO_SPAWNED':
                        ready.set()
                ready.set()

            self.reader = threading.Thread(target=record, daemon=True)
            self.reader.start()
            self.stream = stream
            if not ready.wait(timeout=60) or not identity:
                self.stop(90)
                raise RuntimeError('owned simulator did not report its container ID')
            data = json.loads(config.read_text())
            native = ['podman','exec','-i',identity[0],'bash','-c',
                      'source /opt/ros/humble/setup.bash && exec python3 -']
            phase = data['phase']
            provenance['observers'].append(
                ObserverProvenance(
                    phase=phase, argv=native, network=data['network'], container=identity[0], env=env,
                ),
            )
            (args.out / 'provenance.json').write_text(json.dumps(provenance,indent=2))
            output = (args.out / f'{phase}.observer.stdout.log').open('w')
            errors = (args.out / f'{phase}.observer.stderr.log').open('w')
            streams.extend((output, errors))
            observer = subprocess.Popen(native, stdin=subprocess.PIPE, text=True,
                                        stdout=output, stderr=errors)
            assert observer.stdin is not None
            observer.stdin.write(NATIVE)
            observer.stdin.close()
            observers.append(observer)

        def poll(self):
            return self.process.poll()

        def stop(self, timeout):
            if self.process.stdin and not self.process.stdin.closed:
                try:
                    self.process.stdin.write('stop\n')
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass
            try:
                code = self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                code = self.process.wait(timeout=30)
            self.reader.join(timeout=5)
            self.stream.close()
            return code

    flow = Workflow(parse_args(workflow_args), ContainerRuntime('podman'),
                    start_session=TracedSession)
    (args.out / 'provenance.json').write_text(json.dumps(provenance,indent=2))
    try:
        provenance['workflow_exit'] = flow.run()
    finally:
        provenance['observer_exits'] = [observer.wait(timeout=20) for observer in observers]
        for stream in streams:
            stream.close()
        for output in args.out.glob('*.observer.stdout.log'):
            rows = [line for line in output.read_text().splitlines() if line.startswith('{')]
            output.with_suffix('.jsonl').write_text(''.join(line+'\n' for line in rows))
        (args.out / 'provenance.json').write_text(json.dumps(provenance,indent=2))
    print('MAPPING_TRACE_DONE',json.dumps(provenance),flush=True)


if __name__ == '__main__':
    main()
