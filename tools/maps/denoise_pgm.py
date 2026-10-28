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
"""Remove small occupied 8-connected components from an 8-bit ROS PGM map.

Run: uv run --project tools/maps python tools/maps/denoise_pgm.py INPUT.pgm OUTPUT.pgm
Removed cells become unknown (205), never free. No image rows are flipped.
OUTPUT.png contains identical pixels; OUTPUT.json records hashes and counts.
Occupancy assumes ROS negate=0: (255 - pixel) / 255 > occupied_threshold.
"""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy import ndimage


def save_image(image: Image.Image, path: Path) -> None:
    """Replace complete rasters without truncating files open in image viewers."""
    temporary = path.with_name(path.stem + ".tmp" + path.suffix)
    image.save(temporary)
    temporary.replace(path)


def denoise(
    pixels: NDArray[np.uint8],
    min_component_cells: int = 3,
    occupied_threshold: float = 0.65,
) -> tuple[NDArray[np.uint8], int, int, int]:
    """Return a copy, total components, removed components, and removed cells."""
    if (isinstance(min_component_cells, bool)
            or not isinstance(min_component_cells, int) or min_component_cells < 1):
        raise ValueError("min-component-cells must be a positive integer")
    if not np.isfinite(occupied_threshold) or not 0 < occupied_threshold < 1:
        raise ValueError("occupied-threshold must be finite and strictly between 0 and 1")
    if pixels.ndim != 2 or pixels.dtype != np.uint8 or not pixels.size:
        raise ValueError("expected a nonempty 8-bit grayscale raster")
    occupied = (255.0 - pixels) / 255.0 > occupied_threshold
    labels, component_count = ndimage.label(occupied, structure=np.ones((3, 3), dtype=bool))
    sizes = np.bincount(labels.ravel())
    small = sizes < min_component_cells
    small[0] = False
    removed = small[labels]
    result = pixels.copy()
    result[removed] = 205
    return (result, int(component_count), int(np.count_nonzero(small)),
            int(np.count_nonzero(removed)))


def denoise_file(
    source: Path,
    output: Path,
    min_component_cells: int = 3,
    occupied_threshold: float = 0.65,
) -> dict[str, str | int | float]:
    """Write a PGM, matching PNG, and JSON without overwriting the source."""
    if source.suffix.lower() != ".pgm" or output.suffix.lower() != ".pgm":
        raise ValueError("source and output must have .pgm extensions")
    destinations = (output, output.with_suffix(".png"), output.with_suffix(".json"))
    for destination in destinations:
        if (source.resolve() == destination.resolve()
                or (destination.exists() and source.samefile(destination))):
            raise ValueError("source and output files must be different")
    payload = source.read_bytes()
    with Image.open(BytesIO(payload)) as image:
        if image.format != "PPM" or image.mode != "L":
            raise ValueError("source must be an 8-bit grayscale PGM")
        pixels = np.array(image)
    result, components, removed_components, removed_cells = denoise(
        pixels, min_component_cells, occupied_threshold,
    )
    image = Image.fromarray(result)
    save_image(image, output)
    save_image(image, destinations[1])
    report: dict[str, str | int | float] = {
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "width": result.shape[1],
        "height": result.shape[0],
        "occupied_threshold": occupied_threshold,
        "min_component_cells": min_component_cells,
        "connectivity": 8,
        "replacement_value": 205,
        "component_count": components,
        "removed_component_count": removed_components,
        "removed_cell_count": removed_cells,
    }
    destinations[2].write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--min-component-cells", type=int, default=3)
    parser.add_argument("--occupied-threshold", type=float, default=0.65)
    args = parser.parse_args()
    try:
        report = denoise_file(
            args.source, args.output, args.min_component_cells, args.occupied_threshold,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
