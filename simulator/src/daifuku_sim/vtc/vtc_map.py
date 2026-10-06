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
"""Saved PGM occupancy map and goal clearance checks."""
from __future__ import annotations
import math
from collections import deque
from pathlib import Path
import yaml
from .vtc_contract import CheckError, Pose2D

def _read_pgm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    tokens: list[bytes] = []
    index = 0
    while len(tokens) < 4:
        while index < len(data) and data[index : index + 1].isspace():
            index += 1
        if index >= len(data):
            raise CheckError(str(path), 'truncated PGM header')
        if data[index : index + 1] == b'#':
            while index < len(data) and data[index : index + 1] not in (b'\n', b'\r'):
                index += 1
            continue
        start = index
        while index < len(data) and not data[index : index + 1].isspace():
            index += 1
        tokens.append(data[start:index])
    if tokens[0] != b'P5' or int(tokens[3]) != 255:
        raise CheckError(f'{path}: only 8-bit binary PGM (P5) maps are supported')
    width, height = int(tokens[1]), int(tokens[2])
    if width <= 0 or height <= 0:
        raise CheckError(str(path), 'PGM dimensions must be positive')
    delimiter = 2 if data[index:index + 2] == b'\r\n' else 1
    pixels = data[index + delimiter : index + delimiter + width * height]
    if len(pixels) != width * height:
        raise CheckError(f'{path}: truncated PGM')
    return width, height, pixels


class OccupancyMap:
    """map_server-compatible reading of a saved ``.yaml`` + ``.pgm`` map."""

    def __init__(self, yaml_path: Path) -> None:
        try:
            meta = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
        except (OSError, yaml.YAMLError) as error:
            raise CheckError(f'cannot read map {yaml_path}: {error}') from error
        image = Path(meta['image'])
        if not image.is_absolute():
            image = yaml_path.parent / image
        self.resolution = float(meta['resolution'])
        self.origin = Pose2D(*[float(value) for value in meta['origin'][:3]])
        self.negate = int(meta.get('negate', 0))
        self.occupied_thresh = float(meta['occupied_thresh'])
        self.free_thresh = float(meta['free_thresh'])
        self.width, self.height, self.pixels = _read_pgm(image)

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        dx, dy = x - self.origin.x, y - self.origin.y
        cos, sin = math.cos(-self.origin.yaw), math.sin(-self.origin.yaw)
        return (
            math.floor((cos * dx - sin * dy) / self.resolution),
            math.floor((sin * dx + cos * dy) / self.resolution),
        )

    def _cell_pose(self, column: int, row: int, yaw: float) -> Pose2D:
        local_x = (column + 0.5) * self.resolution
        local_y = (row + 0.5) * self.resolution
        cos, sin = math.cos(self.origin.yaw), math.sin(self.origin.yaw)
        return Pose2D(
            self.origin.x + cos * local_x - sin * local_y,
            self.origin.y + sin * local_x + cos * local_y,
            yaw,
        )

    def _cell_state(self, column: int, row: int) -> str:
        if not (0 <= column < self.width and 0 <= row < self.height):
            return 'outside'
        value = self.pixels[(self.height - 1 - row) * self.width + column]
        occupancy = value / 255.0 if self.negate else (255 - value) / 255.0
        if occupancy > self.occupied_thresh:
            return 'occupied'
        if occupancy < self.free_thresh:
            return 'free'
        return 'unknown'

    def state(self, x: float, y: float) -> str:
        """Return ``free`` / ``occupied`` / ``unknown`` / ``outside`` at a map point."""
        return self._cell_state(*self._cell(x, y))

    def counts(self) -> dict[str, int]:
        result = {'free': 0, 'occupied': 0, 'unknown': 0}
        for value in self.pixels:
            occupancy = value / 255.0 if self.negate else (255 - value) / 255.0
            if occupancy > self.occupied_thresh:
                result['occupied'] += 1
            elif occupancy < self.free_thresh:
                result['free'] += 1
            else:
                result['unknown'] += 1
        return result


def check_goal_cell(grid: OccupancyMap, goal: Pose2D, clearance: float) -> None:
    """The goal must be known-free and no occupied cell may lie within ``clearance``."""
    state = grid.state(goal.x, goal.y)
    if state != 'free':
        raise CheckError(f'goal ({goal.x:.2f}, {goal.y:.2f}) is {state} in the generated map')
    steps = max(1, math.ceil(clearance / grid.resolution))
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            dx, dy = i * grid.resolution, j * grid.resolution
            if math.hypot(dx, dy) <= clearance and grid.state(goal.x + dx, goal.y + dy) == 'occupied':
                raise CheckError(
                    f'goal ({goal.x:.2f}, {goal.y:.2f}) has an occupied cell within {clearance} m'
                )


def nearest_safe_goal_cell(
    grid: OccupancyMap, goal: Pose2D, clearance: float, max_adjustment: float
) -> Pose2D:
    """Resolve an exact requested goal to the closest safe free cell within its tolerance."""
    try:
        check_goal_cell(grid, goal, clearance)
        return goal
    except CheckError:
        pass

    column, row = grid._cell(goal.x, goal.y)
    steps = math.ceil(max_adjustment / grid.resolution) + 1
    candidates: list[tuple[float, Pose2D]] = []
    for cell_x in range(column - steps, column + steps + 1):
        for cell_y in range(row - steps, row + steps + 1):
            candidate = grid._cell_pose(cell_x, cell_y, goal.yaw)
            distance = math.hypot(candidate.x - goal.x, candidate.y - goal.y)
            if distance > max_adjustment:
                continue
            try:
                check_goal_cell(grid, candidate, clearance)
            except CheckError:
                continue
            candidates.append((distance, candidate))
    if not candidates:
        raise CheckError(
            f'no safe known-free map cell within {max_adjustment:.2f} m of '
            f'goal ({goal.x:.2f}, {goal.y:.2f})'
        )
    candidates.sort(key=lambda entry: (entry[0], entry[1].y, entry[1].x))
    return candidates[0][1]


def nearest_safe_seed_cell(
    grid: OccupancyMap,
    seed: Pose2D,
    goal: Pose2D,
    clearance: float,
    max_adjustment: float,
) -> Pose2D:
    """Snap an unsafe measured seed to a nearby safe cell connected to the goal."""
    safe_goal = nearest_safe_goal_cell(grid, goal, clearance, max_adjustment)

    def safe_cell(column: int, row: int) -> bool:
        if grid._cell_state(column, row) != 'free':
            return False
        center = grid._cell_pose(column, row, 0.0)
        try:
            check_goal_cell(grid, center, clearance)
        except CheckError:
            return False
        return True

    goal_column, goal_row = grid._cell(safe_goal.x, safe_goal.y)
    if not safe_cell(goal_column, goal_row):
        raise CheckError('safe goal cell', 'map indexing did not resolve a free cell')

    connected = {(goal_column, goal_row)}
    pending = deque([(goal_column, goal_row)])
    while pending:
        column, row = pending.popleft()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                neighbor = (column + dx, row + dy)
                if neighbor in connected or not safe_cell(*neighbor):
                    continue
                if dx and dy and (
                    not safe_cell(column + dx, row) or not safe_cell(column, row + dy)
                ):
                    continue
                connected.add(neighbor)
                pending.append(neighbor)

    seed_column, seed_row = grid._cell(seed.x, seed.y)
    steps = math.ceil(max_adjustment / grid.resolution) + 1
    candidates: list[tuple[float, Pose2D]] = []
    for column in range(seed_column - steps, seed_column + steps + 1):
        for row in range(seed_row - steps, seed_row + steps + 1):
            if (column, row) not in connected:
                continue
            candidate = grid._cell_pose(column, row, seed.yaw)
            distance = math.hypot(candidate.x - seed.x, candidate.y - seed.y)
            if distance <= max_adjustment:
                candidates.append((distance, candidate))
    if not candidates:
        raise CheckError(
            f'seed ({seed.x:.2f}, {seed.y:.2f})',
            f'no connected safe map cell within {max_adjustment:.2f} m of measured seed',
        )
    candidates.sort(key=lambda entry: (entry[0], entry[1].y, entry[1].x))
    return candidates[0][1]
