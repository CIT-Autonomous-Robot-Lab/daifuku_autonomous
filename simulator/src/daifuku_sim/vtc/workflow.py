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
"""Map the usim VTC world with daifuku, then navigate daifuku on that saved map.

Host-side orchestrator (Python 3.12, the ``simulator`` uv project). Nothing
here imports ROS. The sequence is strictly serial so the two ``/scan``
pipelines (mapping and navigation both include ``scan_pipeline.launch.py``)
never run at the same time:

    preflight  installed usim Git resources or explicit USIM_ROOT; validate supplied world
    runtime    ``<engine> info``
    world      download verified VTC sources and convert once when using automatic assets
    images     usim Gazebo image, daifuku runtime base, VTC stack image
    prepare    stack container: plain Raspicat URDF + meshes -> geometry
    mapping    usim Gazebo session + stack container (RSP + mapping.launch.py
               + survey + map_saver) -> mapping stack stopped, Gazebo kept alive
    alignment  capture measured map/odom pose and TF; convert fixed world goal
    navigation same Gazebo world/odom + a fresh stack container
               (RSP + navigation.launch.py with emcl2 and standalone VI on the
               generated map)  -> NavigateToPose result + world truth

``result.json`` in the run directory is the machine-readable verdict. Exit
codes: 0 PASS, 1 FAIL, 2 invalid input, 3 BLOCKED (container runtime absent).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import traceback
import uuid
from pathlib import Path
from typing import Callable, Literal, overload
from .contract import Mapping, Navigation, RunResult
from ..runtime import Runtime, Session
from .runtime import (EXIT_BLOCKED, EXIT_FAIL, EXIT_PASS, FASTDDS_TRANSPORTS, SIMULATOR, RMW, SCHEMA, Stop, start_gazebo_session)
from .phases import WorkflowPhases


from . import checks


# --------------------------------------------------------------------------- workflow


class Workflow(WorkflowPhases):
    def __init__(
        self,
        options: argparse.Namespace,
        runtime: Runtime,
        start_session: Callable[[Path, Path, dict[str, str]], Session] = start_gazebo_session,
        poll_interval: float = 0.5,
    ) -> None:
        super().__init__()
        self.o = options
        self.runtime = runtime
        self.start_session = start_session
        self.poll_interval = poll_interval
        self.run_dir: Path = options.run_dir.resolve()
        if self.run_dir.is_dir() and any(self.run_dir.iterdir()):
            self.run_dir /= 'vtc-' + uuid.uuid4().hex[:12]
        self.run_id = self.run_dir.name.lower().replace('_', '-') + '-' + uuid.uuid4().hex[:12]
        self.sessions: list[Session] = []
        self.containers: list[str] = []
        self.engine_used = False
        self.goal = options.goal
        self.result: RunResult = {
            'schema': SCHEMA,
            'status': None,
            'stage': None,
            'reason': None,
            'run_dir': str(self.run_dir),
            'started_at': _dt.datetime.now(_dt.timezone.utc).isoformat(),
            'config': {
                'engine': options.engine,
                'network_mode': options.network,
                'usim_root': str(options.usim_root) if options.usim_root else None,
                'usim_image': options.usim_image,
                'stack_image': options.stack_image,
                'base_image': options.base_image,
                'ros_domain_id': options.ros_domain_id,
                'rmw_implementation': RMW,
                'fastdds_builtin_transports': FASTDDS_TRANSPORTS,
                'use_sim_time': True,
                'lidar': checks.LIDAR_KWARGS,
                'goal_world': self.goal.as_dict(),
                'survey': options.survey,
                'tolerance_m': options.tolerance,
            },
            'phases': {},
        }

    @overload
    def _read_json(self, path: Path, stage: Literal['mapping']) -> Mapping: ...
    @overload
    def _read_json(self, path: Path, stage: Literal['navigation']) -> Navigation: ...
    def _read_json(self, path: Path, stage: str) -> Mapping | Navigation:
        if not path.is_file():
            raise Stop('FAIL', stage, f'{path.relative_to(self.run_dir)} was not written', EXIT_FAIL)
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, TypeError) as error:
            raise Stop('FAIL', stage, f'{path.name} is not JSON: {error}', EXIT_FAIL) from error

    def check_runtime(self) -> None:
        ok, output = self.runtime.available()
        if not ok:
            raise Stop(
                'BLOCKED', 'runtime',
                f'container engine {self.runtime.engine!r} is unavailable',
                EXIT_BLOCKED, detail=output,
            )
        self.engine_used = True
        if self.o.network == 'bridge':
            self.runtime.create_network(self._name('network'))
        self.result['network'] = self.runtime.network

    # -- driver -------------------------------------------------------------

    def cleanup(self) -> None:
        gazebo_exits = []
        for session in list(self.sessions):
            gazebo_exits.append(session.stop(timeout=90))
            self.sessions.remove(session)
        if gazebo_exits:
            self._phase('mapping')['gazebo_exit'] = gazebo_exits[0]
        if not self.engine_used:
            self.result['cleanup'] = {'engine_calls': False}
            return
        removed = []
        for name in self.containers:
            if self.runtime.names(name):
                self.runtime.remove(name)
                removed.append(name)
        self.result['cleanup'] = {
            'engine_calls': True,
            'removed_stack_containers': removed,
            'usim_cleanup_owner': 'GazeboSimulator',
            'leftover': [name for name in self.containers if name in self.runtime.names(name)],
            'gazebo_exits': gazebo_exits,
        }
        self.runtime.remove_network()
        self.result['cleanup']['network_removed'] = True
        if any(gazebo_exits):
            raise RuntimeError(f'usim Gazebo cleanup exits: {gazebo_exits}')

    def _logs(self) -> list[str]:
        """Run-relative paths of the evidence files (ROS per-node logs and meshes omitted)."""
        return [
            path.relative_to(self.run_dir).as_posix()
            for path in sorted(self.run_dir.rglob('*'))
            if path.is_file()
            and path.suffix in ('.log', '.json', '.yaml', '.pgm', '.urdf', '.txt', '.repos')
            and path.name != 'result.json'
            and 'ros_log' not in path.parts
            and 'meshes' not in path.parts
        ]

    def run(self) -> int:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        code = EXIT_FAIL
        try:
            for stage in (
                self.preflight,
                self.check_runtime,
                self.prepare_world,
                self.images,
                self.prepare,
                self.mapping,
                self.alignment,
                self.navigation,
            ):
                stage()
            raise Stop('FAIL', 'verdict', 'workflow ended without a verdict', EXIT_FAIL)
        except Stop as stop:
            self.result.update(status=stop.status, stage=stop.stage, reason=stop.reason)
            if stop.detail:
                self.result['detail'] = stop.detail
            code = stop.code
        except (OSError, RuntimeError, subprocess.SubprocessError, checks.CheckError, ValueError, KeyError, TypeError) as error:
            self.result.update(status='FAIL', stage='internal', reason=f'{type(error).__name__}: {error}')
            self.result['detail'] = traceback.format_exc()
            code = EXIT_FAIL
        finally:
            try:
                self.cleanup()
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                self.result['cleanup_error'] = f'{type(error).__name__}: {error}'
                if code == EXIT_PASS:
                    self.result.update(status='FAIL', stage='cleanup', reason=self.result['cleanup_error'])
                    code = EXIT_FAIL
            self.result['finished_at'] = _dt.datetime.now(_dt.timezone.utc).isoformat()
            self.result['exit_code'] = code
            self.result['logs'] = self._logs()
            (self.run_dir / 'result.json').write_text(json.dumps(self.result, indent=2), encoding='utf-8')
        return code
