# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Add measured steep native terrain to planning maps without changing geometry.

Use the CPU USD extra with Python 3.12: uv run --project tools/maps --python 3.12 --extra usd.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
from typing import Final

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy.ndimage import binary_dilation, label
import yaml


MAXIMUM_GRADIENT: Final = 0.3


def mark_steep_terrain(
    mask: NDArray[np.bool_], triangles: NDArray[np.float64],
    origin: NDArray[np.float64], resolution: float,
    low: NDArray[np.float64], high: NDArray[np.float64],
) -> int:
    """Use the existing native-triangle centroid rule, independent of winding."""
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    centers = triangles.mean(axis=1)
    selected = (
        (np.linalg.norm(normal[:, :2], axis=1) > MAXIMUM_GRADIENT * np.abs(normal[:, 2]))
        & (centers[:, :2] >= low).all(axis=1)
        & (centers[:, :2] <= high).all(axis=1)
    )
    cells = np.floor((centers[selected, :2] - origin) / resolution).astype(np.int64)
    mask[cells[:, 1], cells[:, 0]] = True
    return int(selected.sum())


def ensure_goals_connected(
    pixels: NDArray[np.uint8], goals: NDArray[np.float64],
    origin: NDArray[np.float64], resolution: float,
) -> None:
    """Reject a planning repair that blocks or separates any unchanged target."""
    cells = np.floor((goals[:, :2] - origin) / resolution).astype(np.int64)
    components, _ = label(pixels >= 254, np.ones((3, 3), dtype=np.bool_))
    goal_components = components[cells[:, 1], cells[:, 0]]
    if np.any(goal_components == 0) or not np.all(goal_components == goal_components[0]):
        raise ValueError("Terrain hazards block or disconnect unchanged course targets")


def main() -> None:
    """Repair only a measured region and regenerate every planning crop from it."""
    from pxr import Usd, UsdGeom, UsdPhysics

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--maps", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bounds", type=float, nargs=4, required=True,
                        metavar=("X_MIN", "X_MAX", "Y_MIN", "Y_MAX"))
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    course_path = args.maps / "course.json"
    course = json.loads(course_path.read_text(encoding="utf-8"))
    base = yaml.safe_load((args.maps / "navigation.yaml").read_text(encoding="utf-8"))
    origin = np.asarray(base["origin"][:2], dtype=np.float64)
    resolution = float(base["resolution"])
    low = np.array([args.bounds[0], args.bounds[2]])
    high = np.array([args.bounds[1], args.bounds[3]])
    navigation_path = args.maps / base["image"]
    # Image.__exit__ closes the file but can retain its mmap. Release the
    # complete image before replacing a PGM on Windows.
    with closing(Image.open(navigation_path)) as source:
        pixels = np.array(source)[::-1].copy()
    extent = origin + np.array(pixels.shape[::-1]) * resolution
    if np.any(low >= high) or np.any(low < origin) or np.any(high >= extent):
        parser.error("Bounds must be ordered and inside the navigation raster")
    mask = np.zeros(pixels.shape, dtype=np.bool_)
    stage = Usd.Stage.Open(str(args.world), load=Usd.Stage.LoadNone)
    stage.SetEditTarget(stage.GetSessionLayer())
    entries = json.loads(args.index.read_text(encoding="utf-8"))
    steep_facets = 0
    affected_meshes = []
    for entry in entries:
        if ("/Terrain/" not in entry["path"]
                or np.any(np.asarray(entry["max"][:2]) < low)
                or np.any(np.asarray(entry["min"][:2]) > high)):
            continue
        stage.OverridePrim(entry["path"]).SetActive(True)
        stage.Load(entry["path"])
        cache = UsdGeom.XformCache()
        for prim in Usd.PrimRange(stage.GetPrimAtPath(entry["path"]),
                                  Usd.TraverseInstanceProxies()):
            if (not prim.IsA(UsdGeom.Mesh) or not prim.HasAPI(UsdPhysics.CollisionAPI)
                    or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False):
                continue
            mesh = UsdGeom.Mesh(prim)
            points = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
            matrix = np.asarray(cache.GetLocalToWorldTransform(prim))
            points = points @ matrix[:3, :3] + matrix[3, :3]
            if np.any(points[:, :2].max(axis=0) < low) or np.any(points[:, :2].min(axis=0) > high):
                continue
            indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
            faces, offset = [], 0
            for count in mesh.GetFaceVertexCountsAttr().Get():
                face = indices[offset:offset + count]
                faces.extend((face[0], face[i], face[i + 1]) for i in range(1, count - 1))
                offset += count
            count = mark_steep_terrain(
                mask, points[np.asarray(faces)], origin, resolution, low, high,
            )
            if count:
                affected_meshes.append({"path": str(prim.GetPath()), "steep_triangles": count})
                steep_facets += count
        stage.Unload(entry["path"])
    mask = binary_dilation(mask, np.ones((3, 3), dtype=np.bool_))
    changed = mask & (pixels >= 254)
    if not changed.any():
        raise ValueError("Measured terrain hazards do not add any planning obstacles")
    pixels[mask] = 0
    ensure_goals_connected(pixels, np.asarray(course["goals"]), origin, resolution)
    report = {
        "method": "native triangle gradient above0.3 plus one0.2m body-clearance raster cell",
        "bounds_m": [low.tolist(), high.tolist()],
        "maximum_gradient": MAXIMUM_GRADIENT,
        "clearance_cells": 1,
        "resolution_m": resolution,
        "steep_facets": steep_facets,
        "affected_meshes": affected_meshes,
        "new_obstacle_cells": int(changed.sum()),
        "source_navigation_sha256": hashlib.sha256(navigation_path.read_bytes()).hexdigest(),
        "source_world": str(args.world.resolve()),
        "source_world_sha256": hashlib.sha256(args.world.read_bytes()).hexdigest(),
        "evidence": str(args.evidence.resolve()),
        "evidence_sha256": hashlib.sha256(args.evidence.read_bytes()).hexdigest(),
        "source_geometry_modified": False,
        "old_obstacle_cells_cleared": 0,
        "official_goals_changed": 0,
        "all_goals_free_and_connected": True,
    }
    course["terrain_hazards"].setdefault("additional_regions", []).append(report)
    course["runtime_course_completed"] = False
    course["latest_run"] = {"status": "NOT_RUN"}
    args.output.mkdir(parents=True, exist_ok=True)
    raster = Image.fromarray(pixels[::-1])
    raster.save(args.output / base["image"])
    for name in ("navigation.yaml", "localization.yaml", "localization.pgm"):
        if args.maps.resolve() != args.output.resolve():
            shutil.copyfile(args.maps / name, args.output / name)
    for region in course["regions"]:
        metadata_path = Path(region["file"])
        metadata = yaml.safe_load((args.maps / metadata_path).read_text(encoding="utf-8"))
        x, y = np.rint((np.asarray(metadata["origin"][:2]) - origin) / resolution).astype(int)
        width, height = region["dimensions"]
        target = args.output / metadata_path
        target.parent.mkdir(parents=True, exist_ok=True)
        raster.crop((x, raster.height - y - height, x + width, raster.height - y)).save(
            target.with_suffix(".pgm"),
        )
        if args.maps.resolve() != args.output.resolve():
            shutil.copyfile(args.maps / metadata_path, target)
    (args.output / "terrain-hazards-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )
    (args.output / "course.json").write_text(
        json.dumps(course, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    print("TERRAIN_MAP_REPAIRED " + json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
