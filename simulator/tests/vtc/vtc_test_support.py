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
"""In-memory container runtime and simulator fixtures for orchestration tests."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from daifuku_sim.vtc import run_vtc
from .conftest import RASPICAT_URDF, write_map, write_world

class FakeHandle:
    def __init__(self, runtime: 'FakeRuntime', name: str, code: int | None) -> None:
        self.runtime, self.name, self.code = runtime, name, code

    def poll(self) -> int | None:
        if self.name not in self.runtime.live:
            return 137 if self.code is None else self.code
        if self.code is None:
            return None
        self.runtime.live.discard(self.name)  # --rm
        self.runtime.timeline.append(('container-exit', self.name.rsplit('-', 1)[1]))
        return self.code

    def wait(self, timeout: float | None = None) -> int:
        code = self.poll()
        assert code is not None
        return code


class FakeRuntime:
    """Records every engine call; phases are scripted callables writing into /output."""

    engine = 'podman'
    network = 'host'

    def __init__(self, timeline: list, phases: dict, available: tuple[bool, str] = (True, '')) -> None:
        self.timeline, self.phases, self._available = timeline, phases, available
        self.calls: list[tuple] = []
        self.live: set[str] = set()
        self.started_env: dict[str, dict] = {}

    def available(self):
        self.calls.append(('available',))
        return self._available

    def create_network(self, name):
        self.calls.append(('create_network', name))
        self.network = name

    def remove_network(self):
        self.calls.append(('remove_network', self.network))
        self.network = 'host'

    def image_exists(self, tag):
        self.calls.append(('image_exists', tag))
        return True

    def build(self, dockerfile, context, tag, build_args, log):
        self.calls.append(('build', tag, dict(build_args)))
        return 0

    def start(self, name, image, env, mount, command, log):
        phase = command[-1]
        self.calls.append(('start', name, phase))
        assert not any(n.endswith(('-mapping', '-navigation')) for n in self.live), 'stack containers overlap'
        self.live.add(name)
        self.started_env[phase] = env
        self.timeline.append(('container-start', phase))
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('fake\n', encoding='utf-8')
        code = self.phases[phase](Path(mount), env)
        return FakeHandle(self, name, code)

    def remove(self, name):
        self.calls.append(('remove', name))
        self.live.discard(name)

    def names(self, prefix):
        self.calls.append(('names', prefix))
        return sorted(n for n in self.live if n.startswith(prefix))


class FakeSession:
    def __init__(self, timeline: list, phase: str, dies: bool) -> None:
        self.timeline, self.phase, self.dies, self.stopped = timeline, phase, dies, False

    def poll(self):
        return 1 if self.dies or self.stopped else None

    def stop(self, timeout):
        self.timeline.append(('gazebo-stop', self.phase))
        self.stopped = True
        return 0


class SessionFactory:
    """Tracks mutable session ownership across sequential phases."""
    def __init__(self, timeline: list, die_in: str | None = None) -> None:
        self.timeline = timeline
        self.die_in = die_in
        self.sessions: list[FakeSession] = []
        self.networks: list[str] = []

    def __call__(self, config: Path, log: Path, env: dict[str, str]) -> FakeSession:
        data = json.loads(config.read_text(encoding="utf-8"))
        phase = data["phase"]
        self.networks.append(data['network'])
        assert all(s.stopped for s in self.sessions), "Gazebo sessions overlap"
        assert env["RMW_IMPLEMENTATION"] == "rmw_fastrtps_cpp"
        assert env["FASTDDS_BUILTIN_TRANSPORTS"] == "UDPv4"
        self.timeline.append(("gazebo-start", phase))
        session = FakeSession(self.timeline, phase, self.die_in == phase)
        self.sessions.append(session)
        return session


def prepare_ok(mount: Path, env: dict) -> int:
    urdf = mount / 'robot' / 'raspicat_description' / 'urdf' / 'raspicat_plain.urdf'
    urdf.parent.mkdir(parents=True, exist_ok=True)
    urdf.write_text(RASPICAT_URDF, encoding='utf-8')
    return 0


def mapping_ok(alignment: tuple[float, float, float] = (0.02, -0.01, 0.002),
               end_world: tuple[float, float, float] = (0.0, 0.0, 0.0)):
    def run(mount: Path, env: dict) -> int:
        write_map(mount / 'mapping')
        (mount / 'mapping' / 'mapping.json').write_text(
            json.dumps(
                {
                    'ok': True,
                    'map': {'known_cells': 9000},
                    'alignment': {
                        'start': {'x': 0.0, 'y': 0.0, 'yaw': 0.0},
                        'end': dict(zip(('x', 'y', 'yaw'), alignment)),
                    },
                    'slam_vs_truth': {
                        'slam_map': {
                            'x': alignment[0] + math.cos(alignment[2]) * end_world[0]
                                 - math.sin(alignment[2]) * end_world[1],
                            'y': alignment[1] + math.sin(alignment[2]) * end_world[0]
                                 + math.cos(alignment[2]) * end_world[1],
                            'yaw': math.atan2(math.sin(alignment[2] + end_world[2]),
                                              math.cos(alignment[2] + end_world[2])),
                        },
                        'truth_world': dict(zip(('x', 'y', 'yaw'), end_world)),
                        'error_m': 0.0,
                        'yaw_error_rad': 0.0,
                    },
                }
            ),
            encoding='utf-8',
        )
        return 0

    return run


def navigation_result(status: int = 4, final: tuple[float, float] = (0.0, -1.0),
                      start: tuple[float, float, float] = (0.0, 0.0, 0.0)):
    def run(mount: Path, env: dict) -> int:
        (mount / 'navigation').mkdir(parents=True, exist_ok=True)
        (mount / 'navigation' / 'navigation.json').write_text(
            json.dumps(
                {
                    'ok': True,
                    'accepted': True,
                    'action_status': status,
                    'truth_start': dict(zip(('x', 'y', 'yaw'), start)),
                    'truth_final': {'x': final[0], 'y': final[1], 'yaw': 1.5},
                }
            ),
            encoding='utf-8',
        )
        return 0

    return run


def options(tmp_path: Path, usim_root: Path | None, *extra: str):
    args = ['--run-dir', str(tmp_path / 'run'), *extra]
    if usim_root is not None:
        args += ['--usim-root', str(usim_root)]
    return run_vtc.parse_args(args)


def workflow(tmp_path, usim_root, phases, *, die_in=None, available=(True, ''), extra=()):
    timeline: list = []
    runtime = FakeRuntime(timeline, phases, available)
    start = SessionFactory(timeline, die_in)
    flow = run_vtc.Workflow(
        options(tmp_path, usim_root, *extra),
        runtime,
        start_session=start,
        poll_interval=0,
    )
    return flow, runtime, timeline, start


def result_of(tmp_path: Path) -> dict:
    return json.loads((tmp_path / 'run' / 'result.json').read_text(encoding='utf-8'))


ALL_OK = {'prepare': prepare_ok, 'mapping': mapping_ok(), 'navigation': navigation_result()}
