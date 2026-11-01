# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Keep native terrain hazards local and preserve connected unchanged targets."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest
import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "add_terrain_hazards.py"
SPEC = importlib.util.spec_from_file_location("add_terrain_hazards", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_only_steep_native_faces_inside_the_measured_bounds_are_marked() -> None:
    # Given
    triangles = np.array([
        [[1, 1, 0], [2, 1, 0.4], [1, 2, 0]],
        [[3, 1, 0], [3, 2, 0], [4, 1, 0.4]],
        [[1, 3, 0], [2, 3, 0.2], [1, 4, 0]],
        [[8, 8, 0], [9, 8, 1], [8, 9, 0]],
    ], dtype=np.float64)
    mask = np.zeros((12, 12), dtype=np.bool_)
    # When
    count = MODULE.mark_steep_terrain(
        mask, triangles, np.array([-1., -1.]), 1., np.array([0., 0.]), np.array([5., 5.]),
    )
    # Then
    assert count == 2
    assert np.argwhere(mask).tolist() == [[2, 2], [2, 4]]


@pytest.mark.parametrize("blocked", [False, True])
def test_repair_cannot_disconnect_or_cover_an_unchanged_goal(blocked: bool) -> None:
    # Given
    pixels = np.full((5, 9), 255, dtype=np.uint8)
    goals = np.array([[1., 2., 0.], [7., 2., 0.]])
    if blocked:
        pixels[2, 7] = 0
    else:
        pixels[:, 4] = 0
    # When / Then
    with pytest.raises(ValueError):
        MODULE.ensure_goals_connected(pixels, goals, np.zeros(2), 1.)


def test_connected_targets_remain_acceptable_with_a_local_hazard() -> None:
    # Given
    pixels = np.full((5, 9), 254, dtype=np.uint8)
    pixels[2, 4] = 0
    goals = np.array([[1., 2., 0.], [7., 2., 0.]])
    # When / Then
    MODULE.ensure_goals_connected(pixels, goals, np.zeros(2), 1.)


def test_cli_can_replace_its_input_raster_after_releasing_the_image(tmp_path: Path) -> None:
    # Given
    Usd = pytest.importorskip("pxr.Usd")
    UsdGeom = pytest.importorskip("pxr.UsdGeom")
    UsdPhysics = pytest.importorskip("pxr.UsdPhysics")
    world = tmp_path / "world.usda"
    stage = Usd.Stage.CreateNew(str(world))
    UsdGeom.Xform.Define(stage, "/World/Terrain/Tile")
    mesh = UsdGeom.Mesh.Define(stage, "/World/Terrain/Tile/Ground")
    mesh.GetPointsAttr().Set([(2., 2., 0.), (3., 2., .5), (2., 3., 0.)])
    mesh.GetFaceVertexCountsAttr().Set([3])
    mesh.GetFaceVertexIndicesAttr().Set([0, 1, 2])
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    stage.GetRootLayer().Save()
    original_world = world.read_bytes()
    index = tmp_path / "index.json"
    index.write_text(json.dumps([{"path": "/World/Terrain/Tile",
                                  "min": [2, 2, 0], "max": [3, 3, .5]}]))
    maps = tmp_path / "maps"
    (maps / "regions").mkdir(parents=True)
    pixels = np.full((10, 10), 255, dtype=np.uint8)
    for name in ("navigation.pgm", "localization.pgm", "regions/region-000.pgm"):
        Image.fromarray(pixels).save(maps / name)
    metadata = {"resolution": 1., "origin": [0., 0., 0.], "negate": 0,
                "occupied_thresh": .65, "free_thresh": .196}
    for name, image in (("navigation.yaml", "navigation.pgm"),
                        ("localization.yaml", "localization.pgm"),
                        ("regions/region-000.yaml", "region-000.pgm")):
        (maps / name).write_text(yaml.safe_dump({**metadata, "image": image}))
    course = {"goals": [[.5, .5, 0.], [8.5, 8.5, 0.]], "terrain_hazards": {},
              "regions": [{"file": "regions/region-000.yaml", "dimensions": [10, 10]}]}
    (maps / "course.json").write_text(json.dumps(course))
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    # When
    subprocess.run(
        [sys.executable, str(SCRIPT), "--world", str(world), "--index", str(index),
         "--maps", str(maps), "--output", str(maps), "--bounds", "1", "4", "1", "4",
         "--evidence", str(evidence)],
        check=True, capture_output=True, timeout=30,
    )
    # Then
    actual = np.array(Image.open(maps / "navigation.pgm"))
    assert np.count_nonzero(actual == 0) == 9
    np.testing.assert_array_equal(
        actual, np.array(Image.open(maps / "regions/region-000.pgm")),
    )
    assert json.loads((maps / "course.json").read_text())["goals"] == course["goals"]
    assert world.read_bytes() == original_world
