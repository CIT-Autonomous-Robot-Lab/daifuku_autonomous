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
"""VTC command arguments and host-side dispatch."""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sys
from pathlib import Path

from ..runtime import ContainerRuntime
from .contract import CheckError, Pose2D, parse_pose, parse_waypoints
from .runtime import SIMULATOR


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    def pose(text: str) -> Pose2D:
        try:
            return parse_pose(text)
        except CheckError as error:
            raise argparse.ArgumentTypeError(str(error)) from error

    def survey(text: str) -> tuple[tuple[float, float], ...]:
        try:
            return parse_waypoints(text)
        except CheckError as error:
            raise argparse.ArgumentTypeError(str(error)) from error

    env_root = os.environ.get('USIM_ROOT')
    parser.add_argument('--usim-root', type=Path, default=Path(env_root) if env_root else None,
                        help='optional asset checkout override (default: $USIM_ROOT or automatic venv assets)')
    parser.add_argument('--world', type=Path, help='SDF override (default: automatically prepared VTC world)')
    parser.add_argument('--engine', choices=('podman', 'docker'),
                        default=os.environ.get('VTC_ENGINE', 'docker'),
                        help='container engine (default: Docker; VTC_ENGINE overrides it)')
    parser.add_argument('--usim-image', default='usim-gazebo:local')
    parser.add_argument('--network', choices=('bridge', 'host'), default='bridge',
                        help='owned bridge shared by stack and simulator (default); host is opt-in')
    parser.add_argument('--base-image', default='daifuku-autonomous:humble',
                        help='runtime base built from docker/raspberrypi/Dockerfile when missing')
    parser.add_argument('--stack-image', default='daifuku-usim-vtc-stack:local')
    parser.add_argument('--no-build', action='store_true',
                        help='reuse an existing stack image instead of rebuilding it')
    parser.add_argument('--record-video', action='store_true',
                        help='enable Gazebo RGB rendering for the recorded navigation video')
    parser.add_argument('--headless', action='store_true',
                        help='run without the Gazebo GUI (default: show the GUI)')
    parser.add_argument('--run-dir', type=Path,
                        default=SIMULATOR / 'runs' / 'vtc' / _dt.datetime.now().strftime('vtc-%Y%m%d-%H%M%S-%f'))
    parser.add_argument('--ros-domain-id', type=int, default=87, choices=range(0, 102), metavar='0..101')
    parser.add_argument('--survey', type=survey,
                        default=parse_waypoints('2.5,0;2.5,2.5;0,2.5;-1.055,-0.25'),
                        help='mapping waypoints x,y;... relative to the spawn pose (metres)')
    parser.add_argument('--goal', type=pose, default=parse_pose('0,-1,-1.38'),
                        help='fixed navigation goal x,y,yaw in the Gazebo world frame')
    parser.add_argument('--tolerance', type=float, default=0.35, help='world-truth goal tolerance [m]')
    parser.add_argument('--goal-clearance', type=float, default=0.20)
    parser.add_argument('--min-known-cells', type=int, default=500)
    parser.add_argument('--align-max-translation', type=float, default=0.10)
    parser.add_argument('--align-max-yaw', type=float, default=math.radians(2.0))
    parser.add_argument('--prepare-timeout', type=float, default=600)
    parser.add_argument('--mapping-timeout', type=float, default=1800)
    parser.add_argument('--navigation-timeout', type=float, default=1200)
    parser.add_argument('--goal-timeout', type=float, default=300)


def validate_run_options(parser: argparse.ArgumentParser, options: argparse.Namespace) -> argparse.Namespace:
    for name in ('tolerance', 'goal_clearance', 'align_max_translation', 'align_max_yaw',
                 'prepare_timeout', 'mapping_timeout', 'navigation_timeout', 'goal_timeout'):
        value = getattr(options, name)
        if not math.isfinite(value) or value < 0:
            parser.error(f'--{name.replace("_", "-")} must be finite and non-negative')
    return options


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Map and navigate the VTC world.')
    add_run_arguments(parser)
    return validate_run_options(parser, parser.parse_args(argv))


def run(options: argparse.Namespace) -> int:
    from .workflow import Workflow

    workflow = Workflow(options, ContainerRuntime(options.engine))
    code = workflow.run()
    summary = {key: workflow.result.get(key) for key in ('status', 'stage', 'reason', 'run_dir', 'exit_code')}
    print(json.dumps(summary))
    detail = workflow.result.get('detail')
    if detail:
        print(detail, file=sys.stderr)
    return code


def add_video_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('input', type=Path, help='recorded telemetry JSONL')
    parser.add_argument('output', type=Path, help='H264 MP4 destination')
    parser.add_argument('--fps', type=float, default=10)
    parser.add_argument('--result', '--result-json', dest='result_json', type=Path,
                        help='independent run/result.json or flat result JSON')
    parser.add_argument('--transition', type=Path, help='map_to_odom alignment JSON')
    parser.add_argument('--trim-start', type=float, default=0, metavar='SECONDS')
    parser.add_argument('--hold-result', type=float, default=3, metavar='SECONDS')
    parser.add_argument('--map-radius', type=float, default=3, metavar='METERS',
                        help='radius of the fixed goal-centered map viewport')
    parser.add_argument('--world-error-limit', type=float, metavar='METERS',
                        help='override run tolerance (otherwise run value or 0.5)')


def run_video(options: argparse.Namespace) -> int:
    from .render_video import render

    try:
        render(options)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f'render_navigation_video: {error}', file=sys.stderr)
        return 2
    return 0


def configure_parser(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest='operation', required=True)
    run_parser = commands.add_parser('run', help='Map and navigate the VTC world')
    add_run_arguments(run_parser)

    def run_command(options: argparse.Namespace) -> int:
        return run(validate_run_options(run_parser, options))

    run_parser.set_defaults(handler=run_command)
    video_parser = commands.add_parser('video', help='Render recorded VTC telemetry')
    add_video_arguments(video_parser)
    video_parser.set_defaults(handler=run_video)


def main(argv: list[str] | None = None) -> int:
    """Keep the daifuku-vtc command as an alias for vtc run."""
    return run(parse_args(argv))


def video_main(argv: list[str] | None = None) -> int:
    """Keep the daifuku-vtc-video command as an alias for vtc video."""
    parser = argparse.ArgumentParser(description='Render recorded VTC telemetry.')
    add_video_arguments(parser)
    return run_video(parser.parse_args(argv))
