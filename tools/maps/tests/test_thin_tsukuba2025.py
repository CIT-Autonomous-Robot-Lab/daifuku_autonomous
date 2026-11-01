# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Keep route order and source map pixels when reducing waypoint count."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "thin_tsukuba2025.py"
SPEC = importlib.util.spec_from_file_location("thin_tsukuba2025", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_straight_route_distributes_the_budget() -> None:
    # Given
    points = np.column_stack((np.arange(101), np.zeros(101))).astype(np.float64)
    arc = np.arange(101, dtype=np.float64)
    # When
    indices = MODULE.select_indices(points, arc, {0, 100}, 5, 100.0)
    # Then
    assert indices == [0, 25, 50, 75, 100]


def test_turn_is_preserved() -> None:
    # Given
    points = np.array([[0, 0], [5, 0], [10, 0], [10, 5], [10, 10]], dtype=np.float64)
    arc = np.array([0, 5, 10, 15, 20], dtype=np.float64)
    # When
    indices = MODULE.select_indices(points, arc, {0, 4}, 3, 100.0)
    # Then
    assert indices == [0, 2, 4]


def test_revisited_section_boundary_is_not_deduplicated_by_position() -> None:
    # Given
    loop = np.array([[0, 0], [5, 0], [5, 5], [0, 0], [-5, 0]], dtype=np.float64)
    loop_arc = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(loop, axis=0), axis=1))]
    # When
    selected = MODULE.select_indices(loop, loop_arc, {0, 2, 3, 4}, 4, 100.0)
    # Then
    assert selected == [0, 2, 3, 4]


def test_cli_updates_both_formats_and_preserves_source_pixels(tmp_path: Path) -> None:
    # Given
    output = tmp_path / "maps/tsukuba2025"
    output.mkdir(parents=True)
    route = np.column_stack((np.arange(100), np.zeros(100))).astype(np.float64)
    np.save(tmp_path / "dense.npy", route)
    pixels = np.full((256, 512), 254, dtype=np.uint8)
    pixels[17:21, 41:55] = 0
    Image.fromarray(pixels).save(output / "navigation.pgm")
    original = (output / "navigation.pgm").read_bytes()
    source_origin = [-100, -128, 0]
    base = {"image": "navigation.pgm", "resolution": 1.0, "origin": source_origin,
            "negate": 0, "occupied_thresh": 0.65, "free_thresh": 0.196}
    (output / "navigation.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    original_goals = np.column_stack((route, np.zeros(100))).tolist()
    course = {"goals": original_goals,
              "waypoint_route_indices": list(range(100)), "control_route_indices": [0, 99],
              "mandatory_landmarks": {"start": {"goal_index": 0}, "finish": {"goal_index": 99}},
              "sections": [{"points": [[0, 0], [99, 0]]}],
              "rolling_map_span_m": 120.0, "length_m": 99.0}
    source = tmp_path / "original.json"
    source.write_text(json.dumps(course), encoding="utf-8")
    # When
    subprocess.run([sys.executable, str(SCRIPT), str(source), str(tmp_path / "dense.npy"),
                    str(output)], capture_output=True, check=True, timeout=30)
    # Then
    result = json.loads((output / "course.json").read_text(encoding="utf-8"))
    manager = yaml.safe_load((tmp_path / "waypoints/waypoints_tsukuba2025_v1.0.yaml").read_text())
    assert len(result["goals"]) == len(manager["waypoints"]) == 30
    assert result["goals"][0] == original_goals[0]
    assert result["goals"][-1] == original_goals[-1]
    assert result["runtime_course_completed"] is False
    assert result["length_m"] == course["length_m"]
    assert result["world_route"] == route.tolist()
    assert (output / "navigation.pgm").read_bytes() == original
    for index, goal in enumerate(result["goals"]):
        waypoint = manager["waypoints"][index]
        assert [waypoint["position"]["x"], waypoint["position"]["y"]] == goal[:2]
        region = result["regions"][result["goal_regions"][index]]
        region_yaml = yaml.safe_load((output / region["file"]).read_text())
        assert region_yaml["origin"][:2] == region["origin"]
        with Image.open(output / region["file"].replace(".yaml", ".pgm")) as crop:
            assert list(crop.size) == region["dimensions"]
            x = round(region["origin"][0] - source_origin[0])
            y = round(region["origin"][1] - source_origin[1])
            expected = Image.fromarray(pixels).crop(
                (x, len(pixels) - y - crop.height, x + crop.width, len(pixels) - y),
            )
            np.testing.assert_array_equal(np.array(crop), np.array(expected))
