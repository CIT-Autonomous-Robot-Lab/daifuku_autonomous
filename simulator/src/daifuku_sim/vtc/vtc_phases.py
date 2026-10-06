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
"""Serial mapping and navigation stages and their artifact checks."""
from __future__ import annotations
import argparse
import json
import math
import time
from pathlib import Path
from typing import Callable, Literal, Protocol, overload
from . import vtc_checks as checks
from .vtc_contract import Mapping, Navigation, Phase, RunResult, RobotGeometry
from .vtc_map import nearest_safe_seed_cell
from .vtc_runtime import (CONTAINER_PREFIX, EXIT_BLOCKED, EXIT_FAIL, EXIT_PASS, EXIT_USAGE, FASTDDS_TRANSPORTS, STACK, MAP_RELATIVE, REPO, RMW, URDF_RELATIVE, Runtime, Session, Stop)

class WorkflowPhases(Protocol):
    o: argparse.Namespace
    runtime: Runtime
    start_session: Callable[[Path, Path, dict[str, str]], Session]
    poll_interval: float
    run_dir: Path
    run_id: str
    sessions: list[Session]
    containers: list[str]
    engine_used: bool
    goal: checks.Pose2D
    result: RunResult

    def _ros_env(self) -> dict[str, str]:
        return {
            'ROS_DOMAIN_ID': str(self.o.ros_domain_id),
            'RMW_IMPLEMENTATION': RMW,
            'FASTDDS_BUILTIN_TRANSPORTS': FASTDDS_TRANSPORTS,
            'ROS_LOCALHOST_ONLY': '0',
        }
    usim_root: Path
    world: Path
    robot: RobotGeometry
    map_to_odom: checks.Pose2D
    seed: checks.Pose2D
    start_world: checks.Pose2D
    goal_map: checks.Pose2D
    planned_goal_map: checks.Pose2D
    navigation_seed_map: checks.Pose2D

    # -- helpers ------------------------------------------------------------

    def _name(self, phase: str) -> str:
        return f'{CONTAINER_PREFIX}{self.run_id}-{phase}'

    def _phase(self, phase: str) -> Phase:
        return self.result['phases'].setdefault(phase, {})

    @overload
    def _read_json(self, path: Path, stage: Literal['mapping']) -> Mapping: ...
    @overload
    def _read_json(self, path: Path, stage: Literal['navigation']) -> Navigation: ...
    def _read_json(self, path: Path, stage: str) -> Mapping | Navigation: ...

    # -- stages -------------------------------------------------------------

    def preflight(self) -> None:
        usim_root = self.o.usim_root
        if usim_root is None:
            raise Stop(
                'FAIL',
                'preflight',
                'USIM_ROOT is required: set the USIM_ROOT environment variable or pass '
                '--usim-root to an explicit usim checkout (no sibling checkout is inferred)',
                EXIT_USAGE,
            )
        usim_root = usim_root.resolve()
        if not (usim_root / 'docker' / 'Dockerfile.gazebo').is_file():
            raise Stop(
                'FAIL', 'preflight', f'USIM_ROOT is not a usim checkout: {usim_root}', EXIT_USAGE
            )
        self.usim_root = usim_root
        world = (self.o.world or usim_root / 'assets' / 'vtc' / 'world.sdf').resolve()
        try:
            self.result['world'] = checks.validate_world(world)
        except checks.CheckError as error:
            raise Stop('FAIL', 'preflight', f'malformed VTC world: {error}', EXIT_USAGE) from error
        self.world = world

    def images(self) -> None:
        logs = self.run_dir / 'images'
        logs.mkdir(parents=True, exist_ok=True)
        builds: list[tuple[str, Path, Path, str, dict[str, str]]] = []
        if not self.runtime.image_exists(self.o.usim_image):
            builds.append(
                ('usim-gazebo', self.usim_root / 'docker' / 'Dockerfile.gazebo', self.usim_root, self.o.usim_image, {})
            )
        if not self.runtime.image_exists(self.o.base_image):
            builds.append(
                ('base', REPO / 'docker' / 'raspberrypi' / 'Dockerfile', REPO, self.o.base_image, {})
            )
        if not self.o.no_build or not self.runtime.image_exists(self.o.stack_image):
            builds.append(
                (
                    'stack',
                    STACK / 'Dockerfile',
                    REPO,
                    self.o.stack_image,
                    {'BASE_IMAGE': self.o.base_image},
                )
            )
        built = []
        for label, dockerfile, context, tag, args in builds:
            log = logs / f'{label}.log'
            if self.runtime.build(dockerfile, context, tag, args, log) != 0:
                raise Stop('FAIL', 'images', f'building {tag} failed; see images/{label}.log',
                           EXIT_FAIL, detail=log.read_text(encoding='utf-8'))
            built.append(tag)
        self.result['images'] = {'built': built}

    def _container(self, phase: str, env: dict[str, str], timeout: float, session: Session | None) -> int:
        directory = self.run_dir / phase
        directory.mkdir(parents=True, exist_ok=True)
        name = self._name(phase)
        self.containers.append(name)
        self._phase(phase)['container'] = name
        handle = self.runtime.start(
            name,
            self.o.stack_image,
            {**self._ros_env(), 'HOME': '/tmp', 'PYTHONUNBUFFERED': '1', **env},
            self.run_dir,
            ['bash', '/opt/usim_vtc/stack.sh', phase],
            directory / 'stack.log',
        )
        started = time.monotonic()
        while True:
            code = handle.poll()
            if code is not None:
                self._phase(phase)['stack_exit'] = code
                return code
            if session is not None and session.poll() is not None:
                self.runtime.remove(name)
                handle.wait(timeout=60)
                raise Stop(
                    'FAIL',
                    phase,
                    f'usim Gazebo session ended early (exit {session.poll()}); see mapping/simulator.log',
                    EXIT_FAIL,
                )
            if time.monotonic() - started > timeout:
                self.runtime.remove(name)
                handle.wait(timeout=60)
                raise Stop('FAIL', phase, f'{phase} stack container exceeded {timeout:.0f} s', EXIT_FAIL)
            time.sleep(self.poll_interval)

    def prepare(self) -> None:
        code = self._container('prepare', {}, self.o.prepare_timeout, None)
        if code != 0:
            raise Stop('FAIL', 'prepare', f'URDF preparation exited {code}; see prepare/stack.log', EXIT_FAIL)
        try:
            self.robot = checks.robot_geometry(self.run_dir / URDF_RELATIVE)
        except checks.CheckError as error:
            raise Stop('FAIL', 'prepare', str(error), EXIT_FAIL) from error
        self.result['robot'] = self.robot

    def _simulated(self, phase: str, env: dict[str, str], timeout: float) -> int:
        directory = self.run_dir / phase
        directory.mkdir(parents=True, exist_ok=True)
        if not self.sessions:
            config = directory / 'session.json'
            config.write_text(
                json.dumps(
                    {
                        'phase': phase,
                        'usim_root': str(self.usim_root),
                        'engine': self.runtime.engine,
                        'network': self.runtime.network,
                        'image': self.o.usim_image,
                        'world': str(self.world),
                        'robot': self.robot,
                        'spawn': {'x': 0.0, 'y': 0.0, 'yaw': 0.0},
                        'camera_enabled': self.o.record_video,
                    },
                    indent=2,
                ),
                encoding='utf-8',
            )
            self.sessions.append(
                self.start_session(config, directory / 'simulator.log', self._ros_env())
            )
        session = self.sessions[0]
        self._phase(phase)['simulator_session'] = 'mapping/session.json'
        try:
            return self._container(phase, env, timeout, session)
        finally:
            self.runtime.remove(self._name(phase))

    def mapping(self) -> None:
        env = {
            'VTC_SURVEY': ';'.join(f'{x},{y}' for x, y in self.o.survey),
            'VTC_WORLD_GOAL': f'{self.goal.x},{self.goal.y}',
            'VTC_MIN_KNOWN_CELLS': str(self.o.min_known_cells),
        }
        code = self._simulated('mapping', env, self.o.mapping_timeout)
        mapping = self._read_json(self.run_dir / 'mapping' / 'mapping.json', 'mapping')
        self.result['mapping'] = mapping
        if code != 0 or not mapping.get('ok'):
            reason = mapping.get('error') or f'mapping stack exited {code}'
            raise Stop('FAIL', 'mapping', f'{reason}; see mapping/stack.log', EXIT_FAIL)
        alignment = mapping.get('alignment')
        if alignment is None:
            raise Stop('FAIL', 'mapping', 'mapping artifact has no map->odom alignment', EXIT_FAIL)
        self.map_to_odom = checks.pose_from(alignment['end'])
        capture = mapping.get('slam_vs_truth')
        if capture is None:
            raise Stop('FAIL', 'transition', 'mapping has no measured map/odom pose capture', EXIT_FAIL)
        self.seed = checks.pose_from(capture['slam_map'])
        self.start_world = checks.pose_from(capture['truth_world'])
        if not (self.run_dir / MAP_RELATIVE).is_file():
            raise Stop('FAIL', 'mapping', f'{MAP_RELATIVE} was not saved', EXIT_FAIL)

    def alignment(self) -> None:
        alignment = checks.evaluate_alignment(
            self.map_to_odom, self.o.align_max_translation, self.o.align_max_yaw
        )
        self.result['alignment'] = alignment
        predicted = checks.map_from_odom(self.map_to_odom, self.start_world)
        translation_error = math.hypot(predicted.x - self.seed.x, predicted.y - self.seed.y)
        yaw_error = abs(checks.wrap(predicted.yaw - self.seed.yaw))
        if translation_error > self.o.align_max_translation or yaw_error > self.o.align_max_yaw:
            raise Stop(
                'FAIL', 'transition',
                f'measured map/odom poses disagree with captured TF by '
                f'{translation_error:.3f} m / {yaw_error:.3f} rad',
                EXIT_FAIL,
            )
        self.goal_map = checks.pose_from(
            checks.map_from_odom(self.map_to_odom, self.goal).as_dict()
        )
        try:
            grid = checks.OccupancyMap(self.run_dir / MAP_RELATIVE)
            self.result['map'] = {'yaml': str(MAP_RELATIVE), **grid.counts()}
            self.planned_goal_map = checks.nearest_safe_goal_cell(
                grid, self.goal_map, self.o.goal_clearance, self.o.tolerance
            )
            self.navigation_seed_map = nearest_safe_seed_cell(
                grid,
                self.seed,
                self.planned_goal_map,
                self.o.goal_clearance,
                min(self.o.tolerance, 0.25),
            )
        except checks.CheckError as error:
            raise Stop('FAIL', 'goal', str(error), EXIT_FAIL) from error
        adjustment_m = math.hypot(
            self.planned_goal_map.x - self.goal_map.x,
            self.planned_goal_map.y - self.goal_map.y,
        )
        self.result['transition'] = {
            'mode': 'continuous-gazebo',
            'map_pose': self.seed.as_dict(),
            'navigation_seed_map': self.navigation_seed_map.as_dict(),
            'seed_adjustment_m': math.hypot(
                self.navigation_seed_map.x - self.seed.x,
                self.navigation_seed_map.y - self.seed.y,
            ),
            'odom_pose': self.start_world.as_dict(),
            'map_to_odom': self.map_to_odom.as_dict(),
            'goal_world': self.goal.as_dict(),
            'requested_goal_map': self.goal_map.as_dict(),
            'planned_goal_map': self.planned_goal_map.as_dict(),
            'goal_adjustment_m': adjustment_m,
            'translation_consistency_m': translation_error,
            'yaw_consistency_rad': yaw_error,
        }
        (self.run_dir / 'transition.json').write_text(
            json.dumps(self.result['transition'], indent=2), encoding='utf-8',
        )
        leftovers = self.runtime.names(self._name('mapping'))
        if leftovers:
            raise Stop(
                'FAIL',
                'alignment',
                f'mapping container {leftovers} still exists; refusing to start a second /scan pipeline',
                EXIT_FAIL,
            )

    def navigation(self) -> None:
        env = {
            'VTC_SEED': (
                f'{self.navigation_seed_map.x},'
                f'{self.navigation_seed_map.y},{self.navigation_seed_map.yaw}'
            ),
            'VTC_GOAL': (
                f'{self.planned_goal_map.x},{self.planned_goal_map.y},{self.planned_goal_map.yaw}'
            ),
            'VTC_GOAL_TIMEOUT': str(self.o.goal_timeout),
        }
        code = self._simulated('navigation', env, self.o.navigation_timeout)
        navigation = self._read_json(self.run_dir / 'navigation' / 'navigation.json', 'navigation')
        self.result['navigation'] = navigation
        if code != 0 or not navigation.get('ok'):
            reason = navigation.get('error') or f'navigation stack exited {code}'
            raise Stop('FAIL', 'navigation', f'{reason}; see navigation/stack.log', EXIT_FAIL)
        if not navigation.get('accepted'):
            raise Stop('FAIL', 'navigation', 'NavigateToPose was not accepted', EXIT_FAIL)
        status, reason, truth = checks.navigation_verdict(
            navigation, self.map_to_odom, self.goal_map, self.o.tolerance,
            expected_start_world=self.start_world,
        )
        self.result['truth'] = truth
        raise Stop(status, 'verdict', reason, EXIT_PASS if status == 'PASS' else EXIT_FAIL)
