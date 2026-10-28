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

"""Protect measured obstacles and unknown exterior while bridging ground sampling gaps."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest
from scipy import ndimage


SCRIPT = Path(__file__).resolve().parents[1] / "fill_ground_free.py"
SPEC = importlib.util.spec_from_file_location("fill_ground_free", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_only_supported_small_unknown_gaps_become_free() -> None:
    pixels = np.full((51, 51), 205, dtype=np.uint8)
    pixels[10:41, 10:41] = 254
    pixels[14, 14] = 205
    pixels[19:32, 19:32] = 205
    pixels[12:14, 12:14] = 0
    original = pixels.copy()
    result = MODULE.fill_ground(pixels, MODULE.GroundFill())
    assert result[14, 14] == 254
    assert result[25, 25] == 205
    assert result[8, 25] == 205
    assert np.all(result[pixels == 0] == 0)
    assert np.all(result[pixels == 254] == 254)
    added = result != pixels
    assert np.all(pixels[added] == 205)
    distance = np.empty(pixels.shape, dtype=np.float64)
    ndimage.distance_transform_edt(pixels != 254, sampling=0.1, distances=distance)
    assert np.all(distance[added] <= 0.3 + 1e-12)
    np.testing.assert_array_equal(pixels, original)


def test_a_wall_and_its_unobserved_far_side_are_preserved() -> None:
    pixels = np.full((15, 25), 205, dtype=np.uint8)
    pixels[2:13, 2:10] = 254
    pixels[1:14, 10] = 0
    result = MODULE.fill_ground(pixels, MODULE.GroundFill())
    np.testing.assert_array_equal(result[:, 10:], pixels[:, 10:])


@pytest.mark.parametrize("radius", [0.0, -0.1, float("nan")])
def test_invalid_support_radius_is_rejected(radius: float) -> None:
    with pytest.raises(MODULE.GroundFillError):
        MODULE.fill_ground(np.full((3, 3), 254, dtype=np.uint8),
                           MODULE.GroundFill(support_radius=radius))


def test_cli_preserves_source_and_writes_matching_previews(tmp_path: Path) -> None:
    source = tmp_path / "source.pgm"
    output = tmp_path / "navigation.pgm"
    pixels = np.full((15, 15), 205, dtype=np.uint8)
    pixels[3:12, 3:12] = 254
    pixels[7, 7] = 205
    Image.fromarray(pixels).save(source)
    original = source.read_bytes()
    subprocess.run([sys.executable, str(SCRIPT), str(source), str(output)],
                   capture_output=True, check=True, timeout=30)
    assert source.read_bytes() == original
    with Image.open(output) as pgm, Image.open(output.with_suffix(".png")) as png:
        np.testing.assert_array_equal(np.array(pgm), np.array(png))
        assert np.array(pgm)[7, 7] == 254
    report = json.loads(output.with_suffix(".json").read_text())
    assert report["added_free_cells"] == 1
    assert report["occupied_pixels_unchanged"] is True
