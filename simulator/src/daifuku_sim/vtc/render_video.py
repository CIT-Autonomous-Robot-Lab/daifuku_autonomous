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
"""Render recorded ROS navigation JSONL, offline, using Pillow and local ffmpeg.

Usage:
  uv run --project simulator daifuku-vtc-video \
      recording.jsonl navigation.mp4 --fps 10 \
      --transition transition.json --result result.json

The first JSONL object is metadata: type, map (width, height, resolution,
origin {x,y,yaw}, row-major ROS OccupancyGrid data), goal {x,y,yaw},
nodes [strings], planner "vi", localization "emcl2".
Each frame has type "frame", t (seconds, one monotonic clock), truth
{x,y,yaw}, estimate (same or null), scan and path ([{x,y}] in map coordinates),
cmd_vel {linear,angular}, and state (string). Optional frame evidence includes
sim_time and mcl_pose_count. Metadata truth_frame "world" requires --transition:
its map_to_odom {x,y,yaw} rigid transform is applied to truth only.
All other coordinates, including goal, remain in the map frame. Without
truth_frame, truth is assumed to be in map coordinates. Metadata may also
include pose_publishers (names of actual /mcl_pose publishers).
Optional camera is a relative image path, e.g. "camera/000000.ppm", or null.
P6 PPM rgb8 recordings are shown directly in an explicitly labelled Gazebo
camera panel. Paths resolve beside the JSONL and must stay within its folder.
Absent images are labelled unavailable; no camera imagery is generated.
An optional final result object has type "result", action_status (ROS action
status integer), and optional world_error_m (independent world-goal error).
--result accepts the existing run/result.json: navigation.action_status,
truth.error_m (or truth.goal_error_m), and truth.tolerance_m. A flat result
object is accepted too. External evidence takes precedence over JSONL.
The displayed verdict requires status 4 and error within the run tolerance,
or --world-error-limit (default 0.5 m) when no run tolerance is supplied.

Video time is relative to the first sample. At each FPS tick the most recent
recorded sample is held; poses are never interpolated. All input samples
contribute to the trace, even when the video FPS is lower than the sample rate.
The last sample is always shown for one video tick. Result evidence appears
only on that final tick. --trim-start SECONDS removes an initial wait in
recorded time without speeding up or fabricating movement; original elapsed
time stays visible. Memory holds the map, two samples and raster trace,
not the entire recording. JSON lines are capped at 64 MiB and maps at 4M cells.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


SIZE = (1600, 900)
MAX_LINE = 64 * 1024 * 1024
BG = '#0b1423'
PANEL = '#152237'
TEXT = '#ecf2fa'
MUTED = '#9bacc3'
TRUTH = '#28b89a'
ESTIMATE = '#ed8d35'
PLAN = '#407ddc'
SCAN = '#bf4988'
GOAL = '#d7b438'


def records(path):
    """Read bounded JSON objects with useful line-number errors."""
    with path.open('rb') as stream:
        line_number = 0
        while True:
            line = stream.readline(MAX_LINE + 1)
            if not line:
                return
            line_number += 1
            if len(line) > MAX_LINE:
                raise ValueError(f'{path}:{line_number}: JSON line exceeds 64 MiB')
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError('expected a JSON object')
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError(f'{path}:{line_number}: {exc}') from exc
            yield value


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('expected a finite number')
    if not math.isfinite(value):
        raise ValueError('expected a finite number')
    return value


def point(value, heading=False):
    number(value['x'])
    number(value['y'])
    if heading:
        number(value['yaw'])


def check_result(value):
    status = value['action_status']
    if type(status) is not int or status not in range(7):
        raise ValueError('action_status must be a ROS action status (0..6)')
    if value.get('world_error_m') is not None and number(value['world_error_m']) < 0:
        raise ValueError('world_error_m cannot be negative')


def json_object(path):
    with path.open('rb') as stream:
        data = stream.read(MAX_LINE + 1)
    if len(data) > MAX_LINE:
        raise ValueError(f'{path}: JSON exceeds 64 MiB')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected a JSON object')
    return value


def map_truth(row, transform) -> dict[str, Any]:
    if transform is None:
        return row
    p = row['truth']
    c, s = math.cos(transform['yaw']), math.sin(transform['yaw'])
    return dict(row, truth={
        'x': transform['x'] + c * p['x'] - s * p['y'],
        'y': transform['y'] + s * p['x'] + c * p['y'],
        'yaw': transform['yaw'] + p['yaw'],
    })


def inspect_recording(path):
    """Validate once before encoding; collect timing and terminal evidence."""
    rows = records(path)
    metadata = next(rows, None)
    if metadata is None or metadata.get('type') != 'metadata':
        raise ValueError('first line must be metadata')
    if metadata['planner'] != 'vi' or metadata['localization'] != 'emcl2':
        raise ValueError('recording must identify planner vi and localization emcl2')
    grid = metadata['map']
    width, height = grid['width'], grid['height']
    if (type(width) is not int or type(height) is not int
            or min(width, height) <= 0 or width * height > 4_000_000):
        raise ValueError('map dimensions must be positive and at most 4M cells')
    if number(grid['resolution']) <= 0:
        raise ValueError('map resolution must be positive')
    point(grid['origin'], True)
    if len(grid['data']) != width * height:
        raise ValueError('occupancy data length does not match map dimensions')
    if any(type(v) is not int or not -1 <= v <= 100 for v in grid['data']):
        raise ValueError('occupancy data must contain integers in -1..100')
    point(metadata['goal'], True)
    if (not isinstance(metadata['nodes'], list)
            or any(not isinstance(n, str) for n in metadata['nodes'])):
        raise ValueError('nodes must be a list of names')
    if metadata.get('truth_frame', 'map') not in ('world', 'map'):
        raise ValueError('truth_frame must be world or map')
    first = last = None
    count = 0
    result = None
    for row in rows:
        if row.get('type') == 'result':
            if result is not None or not count:
                raise ValueError('result must occur once, after frames')
            check_result(row)
            result = row
            continue
        if row.get('type') != 'frame' or result is not None:
            raise ValueError('expected frame records followed by optional result')
        t = number(row['t'])
        if last is not None and t < last:
            raise ValueError('frame timestamps must be nondecreasing')
        point(row['truth'], True)
        if row['estimate'] is not None:
            point(row['estimate'], True)
        for key in ('scan', 'path'):
            if not isinstance(row[key], list):
                raise ValueError(f'{key} must be a list')
            for p in row[key]:
                point(p)
        number(row['cmd_vel']['linear'])
        number(row['cmd_vel']['angular'])
        if 'sim_time' in row:
            number(row['sim_time'])
        if 'mcl_pose_count' in row:
            if type(row['mcl_pose_count']) is not int or row['mcl_pose_count'] < 0:
                raise ValueError('mcl_pose_count must be a nonnegative integer')
        if row.get('camera') is not None and not isinstance(row['camera'], str):
            raise ValueError('camera must be a relative image path or null')
        if not isinstance(row['state'], str):
            raise ValueError('state must be a string')
        if first is None:
            first = t
        last = t
        count += 1
    if first is None or last is None:
        raise ValueError('recording contains no frames')
    return metadata, first, last, count, result


def font(size):
    for name in ('DejaVuSans.ttf', 'C:/Windows/Fonts/arial.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


class View:
    """Fixed map viewport, with ROS grid origin rotation handled explicitly."""

    def __init__(self, metadata, recording_dir, map_radius):
        self.metadata = metadata
        self.recording_dir = recording_dir.resolve()
        self.camera_path = None
        self.camera_image = None
        self.fonts = {n: font(n) for n in (13, 14, 16, 18, 22, 30)}
        grid = metadata['map']
        self.origin = grid['origin']
        self.resolution = grid['resolution']
        grid_width, grid_height = grid['width'], grid['height']
        goal = metadata['goal']
        dx, dy = goal['x'] - self.origin['x'], goal['y'] - self.origin['y']
        c, s = math.cos(self.origin['yaw']), math.sin(self.origin['yaw'])
        goal_column = (c * dx + s * dy) / self.resolution
        goal_row = (-s * dx + c * dy) / self.resolution
        radius_cells = map_radius / self.resolution
        self.column = max(0, math.floor(goal_column - radius_cells))
        self.row = max(0, math.floor(goal_row - radius_cells))
        right = min(grid_width, math.ceil(goal_column + radius_cells))
        upper = min(grid_height, math.ceil(goal_row + radius_cells))
        width, height = right - self.column, upper - self.row
        if min(width, height) <= 0:
            raise ValueError('goal-centered viewport does not intersect the recorded map')
        self.scale = min(864 / width, 650 / height)
        self.left = 32 + (864 - width * self.scale) / 2
        self.top = 130 + (650 - height * self.scale) / 2
        self.height = height
        gray = bytes(155 if v < 0 else round(245 - v * 2.12)
                     for v in grid['data'])
        raster = Image.frombytes('L', (grid_width, grid_height), gray)
        raster = raster.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        raster = raster.crop((self.column, grid_height - upper, right, grid_height - self.row))
        raster = raster.resize(
            (max(1, round(width * self.scale)), max(1, round(height * self.scale))),
            Image.Resampling.NEAREST).convert('RGB')
        self.base = Image.new('RGB', SIZE, BG)
        draw = ImageDraw.Draw(self.base)
        draw.rounded_rectangle((16, 112, 912, 804), 16, fill=PANEL)
        draw.rounded_rectangle((928, 112, 1584, 804), 16, fill=PANEL)
        self.base.paste(raster, (round(self.left), round(self.top)))
        self.trace = Image.new('RGBA', (864, 650))
        self.previous = None
        self.speed = None
        self.samples = 0

    def xy(self, p):
        dx, dy = p['x'] - self.origin['x'], p['y'] - self.origin['y']
        c, s = math.cos(self.origin['yaw']), math.sin(self.origin['yaw'])
        x = (c * dx + s * dy) / self.resolution - self.column
        y = (-s * dx + c * dy) / self.resolution - self.row
        return self.left + x * self.scale, self.top + (self.height - y) * self.scale

    def accept(self, row):
        if self.previous is not None:
            a, b = self.xy(self.previous['truth']), self.xy(row['truth'])
            ImageDraw.Draw(self.trace).line(
                [(a[0] - 32, a[1] - 130), (b[0] - 32, b[1] - 130)],
                fill=TRUTH, width=3)
            dt = row['t'] - self.previous['t']
            self.speed = (math.hypot(row['truth']['x'] - self.previous['truth']['x'],
                                     row['truth']['y'] - self.previous['truth']['y']) / dt
                          if dt > 0 else None)
        self.previous = row
        self.samples += 1

    def camera(self, image, row):
        name = row.get('camera')
        if name is None:
            return False
        path = (self.recording_dir / name).resolve()
        if not path.is_relative_to(self.recording_dir) or Path(name).is_absolute():
            raise ValueError('camera path must stay within the recording folder')
        if path != self.camera_path:
            if path.stat().st_size > MAX_LINE:
                raise ValueError('camera image exceeds 64 MiB')
            with Image.open(path) as source:
                if source.width * source.height > 16_000_000:
                    raise ValueError('camera image exceeds 16M pixels')
                source.load()
                self.camera_image = source.convert('RGB')
                self.camera_image.thumbnail((616, 347), Image.Resampling.LANCZOS)
            self.camera_path = path
        camera = self.camera_image
        assert camera is not None  # The cache is populated with its path above.
        image.paste(camera, (948 + (616 - camera.width) // 2,
                             153 + (347 - camera.height) // 2))
        return True

    def pose(self, draw, p, color, radius):
        x, y = self.xy(p)
        theta = p['yaw'] - self.origin['yaw']
        draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                     outline=color, width=3)
        draw.line((x, y, x + 24 * math.cos(theta), y - 24 * math.sin(theta)),
                  fill=color, width=4)

    def render(self, row, elapsed, first, duration, result, limit):
        image = self.base.copy()
        image.paste(self.trace, (32, 130), self.trace)
        # Clip recorded points and markers to the map panel, not the evidence panel.
        overlay = Image.new('RGBA', SIZE)
        draw = ImageDraw.Draw(overlay)
        for p in row['scan']:
            x, y = self.xy(p)
            if 32 <= x <= 896 and 130 <= y <= 780:
                draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=SCAN)
        if len(row['path']) > 1:
            draw.line([self.xy(p) for p in row['path']], fill=PLAN, width=4)
        self.pose(draw, self.metadata['goal'], GOAL, 12)
        if row['estimate'] is not None:
            self.pose(draw, row['estimate'], ESTIMATE, 11)
        self.pose(draw, row['truth'], TRUTH, 8)
        image.paste(overlay.crop((32, 130, 896, 780)), (32, 130),
                    overlay.crop((32, 130, 896, 780)))
        draw = ImageDraw.Draw(image)

        def text(x, y, value, size=16, color=TEXT):
            draw.text((x, y), value, font=self.fonts[size], fill=color)

        def wrapped(x, y, value, width, max_lines, size=14, color=MUTED):
            lines = textwrap.wrap(value, width=width) or ['']
            for i, line in enumerate(lines[:max_lines]):
                if i == max_lines - 1 and len(lines) > max_lines:
                    line = line[:-3] + '...'
                text(x, y + i * (size + 5), line, size, color)

        text(24, 20, 'Value Iteration Planner + emcl2', 30)
        text(24, 66, 'Recorded ROS telemetry / usim VTC', 18, MUTED)
        text(1200, 24, f'{elapsed:6.1f} / {duration:.1f} s', 22)
        text(1200, 62, '1x recorded time | no pose interpolation', 13, MUTED)
        text(948, 125, 'Gazebo camera (actual render)', 18)
        draw.rectangle((948, 153, 1564, 500), fill='#070b12')
        if not self.camera(image, row):
            text(1000, 305, 'No camera sample at this recorded tick', 18, MUTED)
        text(948, 523, 'LIVE TELEMETRY', 18)
        wrapped(948, 554, row['state'], 33, 2, 16)
        age = max(0, first + elapsed - row['t'])
        text(948, 602, f'Sample: +{row["t"] - first:.3f}s | age {age:.3f}s', 14, MUTED)
        speed = 'n/a' if self.speed is None else f'{self.speed:.3f} m/s'
        text(948, 628, f'Truth speed (sample delta): {speed}', 14)
        text(948, 654, f'cmd linear: {row["cmd_vel"]["linear"]:+.3f} m/s', 16)
        text(948, 680, f'cmd angular: {row["cmd_vel"]["angular"]:+.3f} rad/s', 16)
        error = ('unavailable' if row['estimate'] is None else
                 f'{math.hypot(row["truth"]["x"] - row["estimate"]["x"], row["truth"]["y"] - row["estimate"]["y"]):.3f} m')
        text(948, 712, f'Truth / emcl2 separation: {error}', 14)
        distance = math.hypot(row['truth']['x'] - self.metadata['goal']['x'],
                              row['truth']['y'] - self.metadata['goal']['y'])
        text(948, 740, f'Remaining (truth, straight-line): {distance:.2f} m', 13)
        text(948, 770, f'LiDAR {len(row["scan"])} | path {len(row["path"])} | mcl {row.get("mcl_pose_count", "n/a")}', 13, MUTED)
        text(1280, 523, 'RECORDED NODE EVIDENCE', 16)
        nodes = self.metadata['nodes']
        evidence_nodes = [name for name in ('vi_planner', 'emcl2') if name in nodes]
        evidence_nodes.extend(name for name in nodes if name not in evidence_nodes)
        wrapped(1280, 554, ', '.join(evidence_nodes) or '(none recorded)',
                41, 5, 13)
        publishers = ', '.join(self.metadata.get('pose_publishers', [])) or 'not recorded'
        wrapped(1280, 652, '/mcl_pose publishers: ' + publishers, 41, 1, 13)
        text(1280, 687, 'INDEPENDENT WORLD VERDICT', 14)
        if result is None:
            text(1280, 718, 'Pending / no result evidence', 14, MUTED)
        elif result.get('world_error_m') is None:
            text(1280, 718, f'Action {result["action_status"]} | world error missing', 14, MUTED)
        else:
            passed = result['action_status'] == 4 and result['world_error_m'] <= limit
            text(1280, 718, f'{"PASS" if passed else "FAIL"} | action {result["action_status"]}',
                 18, TRUTH if passed else ESTIMATE)
            text(1280, 752, f'World error {result["world_error_m"]:.3f} m (limit {limit:g})',
                 14, MUTED)
        for x, color, label in (
                (24, TRUTH, 'Truth / recorded trace'), (280, ESTIMATE, 'emcl2 pose'),
                (460, PLAN, 'VI planned path'), (662, SCAN, 'Recorded LiDAR'),
                (879, GOAL, 'Goal')):
            draw.line((x, 836, x + 22, 836), fill=color, width=4)
            text(x + 30, 826, label, 14)
        text(1210, 825, f'Samples: {self.samples}', 14, MUTED)
        if 'sim_time' in row:
            text(1210, 849, f'Sim time: {row["sim_time"]:.2f}s', 14, MUTED)
        draw.rectangle((24, 884, 1576, 888), fill=PANEL)
        fraction = min(1, elapsed / duration) if duration else 1
        draw.rectangle((24, 884, 24 + round(1552 * fraction), 888), fill=TRUTH)
        return image


def render(args):
    metadata, first, last, count, result = inspect_recording(args.input)
    if args.result_json:
        external = json_object(args.result_json)
        if 'navigation' in external:
            truth = external['truth']
            result = {'action_status': external['navigation']['action_status'],
                      'world_error_m': truth.get('goal_error_m', truth.get('error_m'))}
            if args.world_error_limit is None:
                args.world_error_limit = truth['tolerance_m']
        else:
            result = external
        check_result(result)
    if args.world_error_limit is None:
        args.world_error_limit = 0.5
    transform = None
    if metadata.get('truth_frame', 'map') == 'world':
        if args.transition is None:
            raise ValueError('truth_frame world requires --transition to align truth to map')
        transform = json_object(args.transition)['map_to_odom']
        point(transform, True)
    if not math.isfinite(args.fps) or not 0 < args.fps <= 120:
        raise ValueError('--fps must be finite and in (0,120]')
    if not math.isfinite(args.world_error_limit) or args.world_error_limit < 0:
        raise ValueError('--world-error-limit must be finite and nonnegative')
    if (not math.isfinite(args.trim_start) or args.trim_start < 0
            or args.trim_start > last - first):
        raise ValueError('--trim-start must be within the recorded duration')
    if not math.isfinite(args.hold_result) or not 0 <= args.hold_result <= 30:
        raise ValueError('--hold-result must be in [0,30] seconds')
    if not math.isfinite(args.map_radius) or args.map_radius <= 0:
        raise ValueError('--map-radius must be finite and positive')
    if args.output.resolve() in {
            args.input.resolve(),
            args.result_json.resolve() if args.result_json else args.input.resolve(),
            args.transition.resolve() if args.transition else args.input.resolve()}:
        raise ValueError('output must not overwrite an input')
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        raise ValueError('ffmpeg is not on PATH')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    view = View(metadata, args.input.parent, args.map_radius)
    rows = (map_truth(row, transform) for row in records(args.input)
            if row['type'] == 'frame')
    current = next(rows)
    view.accept(current)
    following = next(rows, None)
    recorded_ticks = math.ceil((last - first - args.trim_start) * args.fps) + 1
    ticks = recorded_ticks + math.ceil(args.hold_result * args.fps)
    command = [
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', '1600x900',
        '-framerate', str(args.fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264',
        '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart', '-f', 'mp4', str(args.output)]
    # File-backed stderr avoids a full pipe deadlocking the frame producer.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
        assert process.stdin is not None  # subprocess.PIPE guarantees a stream.
        try:
            for tick in range(ticks):
                elapsed = min(last - first, args.trim_start + tick / args.fps)
                while following is not None and (
                        following['t'] <= first + elapsed or tick >= recorded_ticks - 1):
                    current = following
                    view.accept(current)
                    following = next(rows, None)
                image = view.render(current, elapsed, first, last - first,
                                    result if tick >= recorded_ticks - 1 else None,
                                    args.world_error_limit)
                if tick >= recorded_ticks:
                    ImageDraw.Draw(image).text(
                        (24, 92), 'Terminal recorded frame held for result inspection',
                        font=view.fonts[13], fill=MUTED)
                process.stdin.write(image.tobytes())
            process.stdin.close()
            code = process.wait(timeout=120)
            if code:
                raise RuntimeError(f'ffmpeg exited with {code}')
        except BaseException as exc:
            if process.poll() is None:
                process.kill()
            process.wait()
            errors.seek(0)
            detail = errors.read(8192).decode('utf-8', errors='replace')
            if isinstance(exc, (BrokenPipeError, RuntimeError, subprocess.TimeoutExpired)):
                raise RuntimeError(f'ffmpeg failed: {detail or str(exc)}') from exc
            raise
    print(json.dumps({'output': str(args.output), 'samples': count,
                      'video_frames': ticks, 'fps': args.fps,
                      'trim_start_s': args.trim_start,
                      'recorded_duration_s': last - first,
                      'video_duration_s': ticks / args.fps}))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
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
    args = parser.parse_args()
    try:
        render(args)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        parser.exit(2, f'render_navigation_video: {exc}\n')


if __name__ == '__main__':
    main()
