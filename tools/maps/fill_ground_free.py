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
"""Fill small navigation-map ground gaps, preserving obstacles and unobserved exterior.

Run: uv run --project tools/maps python tools/maps/fill_ground_free.py IN.pgm OUT.pgm
Only unknown (205) cells inside a local closing of observed white ground are eligible.
An independent distance limit prevents large unobserved areas from becoming free.
This is an inferred navigation layer, not additional localization observations.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy import ndimage

@dataclass(frozen=True, slots=True)
class GroundFillError(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class GroundFill:
    resolution: float = 0.1
    closing_radius: float = 0.5
    support_radius: float = 0.3


def fill_ground(pixels: NDArray[np.uint8], settings: GroundFill) -> NDArray[np.uint8]:
    """Fill bounded interior gaps; do not dilate the whole observed-ground boundary."""
    dimensions = (settings.resolution, settings.closing_radius, settings.support_radius)
    if not np.isfinite(dimensions).all() or min(dimensions) <= 0:
        raise GroundFillError("resolution and both radii must be finite and positive")
    if pixels.ndim != 2 or pixels.dtype != np.uint8 or not pixels.size:
        raise GroundFillError("expected a nonempty 8-bit grayscale raster")
    free = pixels >= 254
    if not free.any():
        raise GroundFillError("no observed white ground cells")
    radius = int(np.ceil(settings.closing_radius / settings.resolution))
    dy, dx = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    disk = ((dx * settings.resolution) ** 2 + (dy * settings.resolution) ** 2
            <= settings.closing_radius ** 2 + 1e-12)
    closed = ndimage.binary_closing(free, structure=disk)
    distance = np.empty(pixels.shape, dtype=np.float64)
    ndimage.distance_transform_edt(~free, sampling=settings.resolution, distances=distance)
    added = ((pixels == 205) & closed &
             (distance <= settings.support_radius + 1e-12))
    result = pixels.copy()
    result[added] = 254
    return result


def save_image(image: Image.Image, path: Path) -> None:
    """Keep a complete preview available while Photos may be reading it."""
    temporary = path.with_name(path.stem + ".tmp" + path.suffix)
    image.save(temporary)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--resolution", type=float, default=0.1)
    parser.add_argument("--closing-radius", type=float, default=0.5)
    parser.add_argument("--support-radius", type=float, default=0.3)
    args = parser.parse_args()
    try:
        if args.source.resolve() == args.output.resolve():
            raise GroundFillError("source and output must be different files")
        if args.output.exists() and args.source.samefile(args.output):
            raise GroundFillError("source and output must be different files")
        if args.source.suffix.lower() != ".pgm" or args.output.suffix.lower() != ".pgm":
            raise GroundFillError("source and output must be .pgm files")
        with Image.open(args.source) as image:
            if image.mode != "L":
                raise GroundFillError("source must be an 8-bit grayscale PGM")
            pixels = np.array(image)
        settings = GroundFill(args.resolution, args.closing_radius, args.support_radius)
        result = fill_ground(pixels, settings)
        image = Image.fromarray(result)
        save_image(image, args.output)
        save_image(image, args.output.with_suffix(".png"))
    except (OSError, GroundFillError) as error:
        parser.error(str(error))
    report = {
        "source": str(args.source.resolve()), "output": str(args.output.resolve()),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "resolution": settings.resolution, "closing_radius_m": settings.closing_radius,
        "maximum_ground_support_distance_m": settings.support_radius,
        "added_free_cells": int(np.count_nonzero(result != pixels)),
        "free_cells_before": int(np.count_nonzero(pixels >= 254)),
        "free_cells_after": int(np.count_nonzero(result >= 254)),
        "occupied_pixels_unchanged": bool(np.array_equal(result[pixels == 0],
                                                        pixels[pixels == 0])),
    }
    args.output.with_suffix(".json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
