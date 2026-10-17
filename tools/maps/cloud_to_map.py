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
"""Project binary GLIM XYZI exports; preserve unobserved space and source coordinates.

Run: uv run --project tools/maps python tools/maps/cloud_to_map.py SOURCE --output DIR
Dependencies are declared in tools/maps/pyproject.toml. Source clouds are read-only.
PGM and PNG are generated together; map metadata is recorded in conversion.json.
No sensor trajectory is available, so free cells require observed ground support.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import time
from typing import assert_never

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy import ndimage


@dataclass(frozen=True, slots=True)
class CloudInputError(Exception):
    path: Path
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: {self.detail}"


class Format(Enum):
    PCD = ".pcd"
    PLY = ".ply"


@dataclass(frozen=True, slots=True)
class Cloud:
    path: Path
    points: NDArray[np.float32]
    file_sha256: str
    payload_sha256: str
    trailing_bytes: int


@dataclass(frozen=True, slots=True)
class Settings:
    resolution: float
    ground_ceiling: float
    ground_cell: float = 0.5
    ground_tolerance: float = 0.15
    obstacle_min: float = 0.275
    navigation_max: float = 2.5
    localization_max: float = 5.5
    padding: float = 2.0
    close_ground_radius: float = 0.2


@dataclass(frozen=True, slots=True)
class Maps:
    navigation: NDArray[np.uint8]
    localization: NDArray[np.uint8]
    origin: tuple[float, float]
    ground_cells: int
    finite_points: int
    ground_z_range: tuple[float, float]


def read_cloud(path: Path) -> Cloud:
    """Read exactly the declared XYZI records, not PCL's trailing page padding."""
    try:
        kind = Format(path.suffix.lower())
    except ValueError as error:
        raise CloudInputError(path, "expected a binary .pcd or .ply export") from error
    with path.open("rb") as stream:
        lines = []
        terminator = b"DATA binary" if kind is Format.PCD else b"end_header"
        while True:
            line = stream.readline()
            if not line:
                raise CloudInputError(path, "missing binary header terminator")
            lines.append(line.decode("ascii").strip())
            if line.strip() == terminator:
                break
            if line.startswith(b"DATA ") and line.strip() != b"DATA binary":
                raise CloudInputError(path, "only uncompressed binary PCD is supported")
        payload = stream.read()
    match kind:
        case Format.PCD:
            header = {
                items[0]: items[1:]
                for line in lines if (items := line.split()) and not line.startswith("#")
            }
            valid = (header["FIELDS"] == ["x", "y", "z", "intensity"]
                     and header["SIZE"] == ["4"] * 4 and header["TYPE"] == ["F"] * 4
                     and header["COUNT"] == ["1"] * 4)
            count = int(header["POINTS"][0])
        case Format.PLY:
            valid = ("format binary_little_endian 1.0" in lines and
                     [line for line in lines if line.startswith("property ")] ==
                     [f"property float {name}" for name in ("x", "y", "z", "intensity")])
            count = int(next(line.split()[-1] for line in lines
                             if line.startswith("element vertex ")))
        case unreachable:
            assert_never(unreachable)
    if not valid or count <= 0 or len(payload) < count * 16:
        raise CloudInputError(path, "require nonempty float32 XYZI binary records")
    if any(payload[count * 16:]):
        raise CloudInputError(path, "nonzero bytes after the declared point records")
    points = np.frombuffer(payload, dtype="<f4", count=count * 4).reshape(count, 4)
    return Cloud(path, points, hashlib.sha256(path.read_bytes()).hexdigest(),
                 hashlib.sha256(payload[:count * 16]).hexdigest(), len(payload) - count * 16)


def project(points: NDArray[np.float32], settings: Settings) -> Maps:
    """Estimate a local lower terrain envelope before projecting height bands."""
    xyz = points[np.isfinite(points[:, :3]).all(axis=1), :3]
    if not len(xyz):
        raise CloudInputError(Path("<cloud>"), "no finite XYZ records")
    resolution = settings.resolution
    minimum = xyz[:, :2].min(axis=0).astype(np.float64)
    maximum = xyz[:, :2].max(axis=0).astype(np.float64)
    origin = np.floor((minimum - settings.padding) / resolution) * resolution
    upper = np.ceil((maximum + settings.padding) / resolution) * resolution
    width, height = np.rint((upper - origin) / resolution).astype(int)
    coarse_width, coarse_height = np.ceil((upper - origin) / settings.ground_cell).astype(int)
    coarse_xy = np.floor((xyz[:, :2] - origin) / settings.ground_cell).astype(np.int32)
    coarse_index = coarse_xy[:, 1] * coarse_width + coarse_xy[:, 0]
    candidates = np.flatnonzero(xyz[:, 2] <= settings.ground_ceiling)
    order = np.lexsort((xyz[candidates, 2], coarse_index[candidates]))
    ordered = candidates[order]
    cells, starts, counts = np.unique(coarse_index[ordered], return_index=True, return_counts=True)
    supported = counts >= 3
    cells, starts, counts = cells[supported], starts[supported], counts[supported]
    if not len(cells):
        raise CloudInputError(Path("<cloud>"), "no supported terrain below ground ceiling")
    terrain = np.full(coarse_width * coarse_height, np.nan, dtype=np.float32)
    terrain[cells] = xyz[ordered[starts + np.floor(0.1 * (counts - 1)).astype(int)], 2]
    terrain = terrain.reshape(coarse_height, coarse_width)
    nearest = ndimage.distance_transform_edt(~np.isfinite(terrain), return_distances=False,
                                           return_indices=True)
    terrain = ndimage.grey_opening(terrain[tuple(nearest)], size=(5, 5))
    coordinates = ((xyz[:, :2] - origin) / settings.ground_cell - 0.5).T[::-1]
    ground = ndimage.map_coordinates(terrain, coordinates, order=1, mode="nearest")
    relative_height = xyz[:, 2] - ground
    xy = np.floor((xyz[:, :2] - origin) / resolution).astype(np.int32)
    index = xy[:, 1] * width + xy[:, 0]
    observed_ground = ((np.abs(relative_height) <= settings.ground_tolerance) &
                       (xyz[:, 2] <= settings.ground_ceiling))
    free = np.bincount(index[observed_ground], minlength=width * height).reshape(height, width) > 0
    radius = int(round(settings.close_ground_radius / resolution))
    dy, dx = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    free = ndimage.binary_closing(free, structure=(dx * dx + dy * dy <= radius * radius))
    rasters = []
    for maximum in (settings.navigation_max, settings.localization_max):
        obstacle = (relative_height >= settings.obstacle_min) & (relative_height <= maximum)
        occupied = np.bincount(index[obstacle], minlength=width * height).reshape(height, width) > 0
        raster = np.full((height, width), 205, dtype=np.uint8)
        raster[free] = 254
        raster[occupied] = 0
        rasters.append(np.flipud(raster).copy())
    return Maps(rasters[0], rasters[1], (float(origin[0]), float(origin[1])), len(cells),
                len(xyz), (float(terrain.min()), float(terrain.max())))


def save_image(image: Image.Image, path: Path) -> None:
    """Replace a complete raster atomically while image viewers may be reading."""
    temporary = path.with_name(path.stem + ".tmp" + path.suffix)
    image.save(temporary)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--equivalent", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=float, default=0.1)
    parser.add_argument("--ground-ceiling", type=float, required=True)
    args = parser.parse_args()
    if not np.isfinite([args.resolution, args.ground_ceiling]).all() or args.resolution <= 0:
        parser.error("resolution must be positive; ground ceiling must be finite")
    started = time.perf_counter()
    try:
        source = read_cloud(args.source)
        copies = [source]
        if args.equivalent is not None:
            other = read_cloud(args.equivalent)
            if other.payload_sha256 != source.payload_sha256:
                raise CloudInputError(args.equivalent, "records differ from the primary source")
            copies.append(other)
        settings = Settings(args.resolution, args.ground_ceiling)
        maps = project(source.points, settings)
    except CloudInputError as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=True)
    map_statistics: dict[str, dict[str, int | str]] = {}
    report = {
        "sources": [
            {"path": str(cloud.path.resolve()), "points": len(cloud.points),
             "sha256": cloud.file_sha256, "payload_sha256": cloud.payload_sha256,
             "trailing_zero_bytes_ignored": cloud.trailing_bytes}
            for cloud in copies
        ],
        "resolution": settings.resolution, "origin": [*maps.origin, 0.0],
        "width": maps.navigation.shape[1], "height": maps.navigation.shape[0],
        "finite_points": maps.finite_points, "ground_ceiling": settings.ground_ceiling,
        "ground_cell": settings.ground_cell, "ground_tolerance": settings.ground_tolerance,
        "ground_percentile": 10, "ground_opening_cells": 5,
        "ground_z_range": maps.ground_z_range, "supported_ground_cells": maps.ground_cells,
        "ground_closing_radius": settings.close_ground_radius,
        "minimum_obstacle_height": settings.obstacle_min,
        "maximum_navigation_height": settings.navigation_max,
        "maximum_localization_height": settings.localization_max,
        "ray_traced_free_space": False, "maps": map_statistics,
    }
    for name, raster in (("navigation", maps.navigation), ("localization", maps.localization)):
        image = Image.fromarray(raster)
        save_image(image, args.output / f"{name}.pgm")
        save_image(image, args.output / f"{name}.png")
        free_count = int(np.count_nonzero(raster == 254))
        labels, count = ndimage.label(raster == 254)
        sizes = np.bincount(labels.ravel())[1:]
        map_statistics[name] = {
            "free": free_count, "occupied": int(np.count_nonzero(raster == 0)),
            "unknown": int(np.count_nonzero(raster == 205)), "free_components": int(count),
            "largest_free_component": int(sizes.max()) if len(sizes) else 0,
            "pgm_sha256": hashlib.sha256((args.output / f"{name}.pgm").read_bytes()).hexdigest(),
        }
    report["elapsed_seconds"] = time.perf_counter() - started
    (args.output / "conversion.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
