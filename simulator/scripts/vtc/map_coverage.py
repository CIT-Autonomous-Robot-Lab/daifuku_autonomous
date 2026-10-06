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
"""Inspect exact goal coverage without changing the saved map or planning a new goal."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from daifuku_sim.vtc.vtc_checks import OccupancyMap, check_goal_cell, odom_from_map, pose_from
from daifuku_sim.vtc.vtc_contract import CheckError, Pose2D


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = json.loads((args.run_dir / 'result.json').read_text())
    transition = result['transition']
    goal = pose_from(transition['goal_map'])
    transform = pose_from(transition['map_to_odom'])
    grid = OccupancyMap(args.run_dir / 'mapping' / 'vtc_map.yaml')
    tolerance = result['config']['tolerance_m']
    clearance = 0.20
    cos, sin = math.cos(grid.origin.yaw), math.sin(grid.origin.yaw)
    dx, dy = goal.x-grid.origin.x, goal.y-grid.origin.y
    column = math.floor((cos*dx+sin*dy)/grid.resolution)
    row = math.floor((-sin*dx+cos*dy)/grid.resolution)

    def point(c, r):
        x, y = (c+0.5)*grid.resolution, (r+0.5)*grid.resolution
        return Pose2D(grid.origin.x+cos*x-sin*y, grid.origin.y+sin*x+cos*y, goal.yaw)

    counts = {}
    for c in range(column-5, column+6):
        for r in range(row-5, row+6):
            p = point(c, r)
            state = grid.state(p.x, p.y)
            counts[state] = counts.get(state, 0)+1
    candidates = []
    steps = math.ceil(tolerance/grid.resolution)+1
    for c in range(column-steps, column+steps+1):
        for r in range(row-steps, row+steps+1):
            p = point(c, r)
            distance = math.hypot(p.x-goal.x, p.y-goal.y)
            if distance > tolerance:
                continue
            try:
                check_goal_cell(grid, p, clearance)
            except CheckError:
                continue
            candidates.append(dict(map=p.as_dict(), world=odom_from_map(transform,p).as_dict(),
                                   distance_m=distance, column=c, row=r))
    candidates.sort(key=lambda item: (item['distance_m'], item['column'], item['row']))
    summary = dict(requested_goal_map=goal.as_dict(), exact_state=grid.state(goal.x,goal.y),
                   seed_state=grid.state(transition['map_pose']['x'],transition['map_pose']['y']),
                   five_cell_radius_neighborhood=counts, clearance_m=clearance,
                   tolerance_m=tolerance, safe_free_candidates=candidates)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix('.json').write_text(json.dumps(summary,indent=2))
    image = Image.frombytes('L',(grid.width,grid.height),grid.pixels).convert('RGB')
    draw = ImageDraw.Draw(image)
    for candidate in candidates:
        x, y = candidate['column'], grid.height-1-candidate['row']
        draw.rectangle((x,y,x,y),fill='green')
    y = grid.height-1-row
    draw.line((column-2,y,column+2,y),fill='red')
    draw.line((column,y-2,column,y+2),fill='red')
    image.crop((column-12,y-12,column+13,y+13)).resize(
        (500,500),Image.Resampling.NEAREST,
    ).save(args.out.with_suffix('.png'))
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
