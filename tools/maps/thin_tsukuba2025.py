# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Generate sparse 2025 waypoints and planning crops covering each full route leg.

uv run --project tools/maps python tools/maps/thin_tsukuba2025.py \
    ORIGINAL_COURSE_JSON DENSE_ROUTE_NPY OUTPUT_MAP_DIRECTORY
Dependencies live in tools/maps/pyproject.toml. Full maps and dense route stay intact.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Final

import numpy as np
from numpy.typing import NDArray
from PIL import Image
import yaml


MAXIMUM: Final = 30
MARGIN: Final = 10.0


def select_indices(
    points: NDArray[np.float64],
    arc: NDArray[np.float64],
    required: set[int],
    maximum: int,
    preferred_length: float,
) -> list[int]:
    """Keep required visits ordered and allocate remaining points to bends and long legs."""
    selected = set(required)
    while len(selected) < min(maximum, len(points)):
        best_score = -1.0
        best_index = -1
        ordered = sorted(selected)
        for first, last in zip(ordered, ordered[1:]):
            if last - first < 2:
                continue
            middle = points[first + 1:last, :2]
            direction = points[last, :2] - points[first, :2]
            squared = float(direction @ direction)
            offset = middle - points[first, :2]
            projection = np.zeros(len(middle))
            if squared > 0:
                projection = np.clip(offset @ direction / squared, 0.0, 1.0)
            errors = np.linalg.norm(offset - projection[:, None] * direction, axis=1)
            candidate = first + 1 + int(np.argmax(errors))
            shape_score = float(errors.max()) / MARGIN
            length_score = float(arc[last] - arc[first]) / preferred_length
            if length_score > shape_score:
                midpoint = (arc[first] + arc[last]) / 2
                candidate = first + 1 + int(np.argmin(np.abs(arc[first + 1:last] - midpoint)))
            score = max(shape_score, length_score)
            if score > best_score:
                best_score, best_index = score, candidate
        assert best_index >= 0
        selected.add(best_index)
    return sorted(selected)


def main() -> None:
    """Select at most thirty targets and align JSON, manager YAML and planning regions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("course", type=Path)
    parser.add_argument("dense_route", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--maximum", type=int, default=MAXIMUM)
    args = parser.parse_args()
    if not 2 <= args.maximum <= MAXIMUM:
        parser.error(f"--maximum must be between 2 and {MAXIMUM}")
    course = json.loads(args.course.read_text(encoding="utf-8"))
    goals = np.asarray(course["goals"], dtype=np.float64)
    route = np.load(args.dense_route, allow_pickle=False)
    dense_indices = np.asarray(course["waypoint_route_indices"], dtype=np.int64)
    if not np.allclose(route[dense_indices], goals[:, :2], rtol=0.0, atol=1e-8):
        parser.error("Dense route and original waypoint coordinates do not match")
    dense_arc = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(route, axis=0), axis=1))]
    arc = dense_arc[dense_indices]
    required = {0, len(goals) - 1}
    required.update(point["goal_index"] for point in course["mandatory_landmarks"].values())
    control = 0
    route_indices = course["waypoint_route_indices"]
    for section in course["sections"]:
        control += len(section["points"]) - 1
        required.add(route_indices.index(course["control_route_indices"][control]))
    if len(required) > args.maximum:
        parser.error(f"{len(required)} section/landmark endpoints exceed the waypoint budget")
    nominal_span = float(course["rolling_map_span_m"])
    selected = select_indices(goals, arc, required, args.maximum, nominal_span - 2 * MARGIN)
    retained = goals[selected].tolist()

    base = yaml.safe_load((args.output / "navigation.yaml").read_text(encoding="utf-8"))
    resolution = float(base["resolution"])
    origin = np.asarray(base["origin"][:2], dtype=np.float64)
    regions = []
    assigned = []
    first = 0
    with Image.open(args.output / base["image"]) as raster:
        while first < len(selected):
            previous = selected[max(0, first - 1)]
            last = first
            segment = route[dense_indices[previous]:dense_indices[selected[last]] + 1]
            allowance = np.maximum(nominal_span, np.ptp(segment, axis=0) + 2 * MARGIN)
            while last + 1 < len(selected):
                extended = route[dense_indices[previous]:dense_indices[selected[last + 1]] + 1]
                if np.any(np.ptp(extended, axis=0) + 2 * MARGIN > allowance + 1e-8):
                    break
                last += 1
                segment = extended
            extent = np.maximum(nominal_span, np.ptp(segment, axis=0) + 2 * MARGIN)
            center = (segment.min(axis=0) + segment.max(axis=0)) / 2
            low = np.floor((center - extent / 2 - origin) / resolution).astype(np.int64)
            high = np.ceil((center + extent / 2 - origin) / resolution).astype(np.int64)
            name = f"region-{len(regions):03}"
            folder = args.output / "regions"
            folder.mkdir(parents=True, exist_ok=True)
            crop = raster.crop((int(low[0]), raster.height - int(high[1]),
                                int(high[0]), raster.height - int(low[1])))
            crop.save(folder / f"{name}.pgm")
            region_origin = (origin + low * resolution).tolist()
            (folder / f"{name}.yaml").write_text(yaml.safe_dump({
                **base, "image": f"{name}.pgm", "origin": [*region_origin, 0.0],
            }, sort_keys=False), encoding="utf-8")
            regions.append({
                "file": f"regions/{name}.yaml", "origin": region_origin,
                "dimensions": list(crop.size),
                "center": (origin + (low + high) * resolution / 2).tolist(),
            })
            assigned.extend([len(regions) - 1] * (last - first + 1))
            first = last + 1

    remap = {old: new for new, old in enumerate(selected)}
    for landmark in course["mandatory_landmarks"].values():
        landmark["goal_index"] = remap[landmark["goal_index"]]
    intervals = np.diff(arc[selected])
    course.update(
        goals=retained, waypoint_route_indices=dense_indices[selected].tolist(),
        world_route=course.get("world_route", goals[:, :2].tolist()),
        regions=regions, goal_regions=assigned, runtime_course_completed=False,
        latest_run={"status": "NOT_RUN"},
        waypoint_spacing={
            "maximum_waypoints": args.maximum,
            "minimum_arc_m": float(intervals.min()),
            "median_arc_m": float(np.median(intervals)),
            "maximum_arc_m": float(intervals.max()),
        },
        waypoint_thinning={
            "source_count": len(goals), "source_goal_indices": selected,
            "required_source_goal_indices": sorted(required),
            "dense_route_unchanged": True, "sections_unchanged": True,
        },
    )
    (args.output / "course.json").write_text(
        json.dumps(course, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    waypoints = args.output.parents[1] / "waypoints/waypoints_tsukuba2025_v1.0.yaml"
    waypoints.parent.mkdir(parents=True, exist_ok=True)
    waypoints.write_text(yaml.safe_dump({
        "frame_id": "map",
        "waypoints": [
            {"name": f"waypoint_{index}",
             "position": {"x": x, "y": y, "z": 0.0},
             "orientation": {"x": 0.0, "y": 0.0,
                             "z": math.sin(yaw / 2), "w": math.cos(yaw / 2)}}
            for index, (x, y, yaw) in enumerate(retained)
        ],
    }, sort_keys=False), encoding="utf-8")
    print(json.dumps({"waypoints": len(retained), "regions": len(regions),
                      "length_m": course["length_m"], "spacing": course["waypoint_spacing"]}))


if __name__ == "__main__":
    main()
