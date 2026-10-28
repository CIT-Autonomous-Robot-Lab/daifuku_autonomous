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
"""Deterministic raster and CLI checks for the independent PGM denoiser."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "denoise_pgm.py"
SPEC = importlib.util.spec_from_file_location("denoise_pgm", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sample() -> tuple[np.ndarray, np.ndarray]:
    pixels = np.full((9, 13), 254, dtype=np.uint8)
    pixels[0, :5] = [205, 255, 253, 100, 90]
    pixels[2, 1] = 0
    pixels[2, 5:7] = 0
    pixels[5, 1:6] = [0, 20, 40, 60, 80]
    pixels[5, 9] = pixels[6, 10] = pixels[7, 11] = 0
    expected = pixels.copy()
    expected[2, 1] = 205
    expected[2, 5:7] = 205
    return pixels, expected


def test_removes_only_small_components_and_keeps_diagonal_wall() -> None:
    pixels, expected = sample()
    original = pixels.copy()
    result, components, removed_components, removed_cells = MODULE.denoise(pixels)
    np.testing.assert_array_equal(result, expected)
    np.testing.assert_array_equal(pixels, original)
    assert (components, removed_components, removed_cells) == (4, 2, 3)
    assert not np.shares_memory(result, pixels)


def test_threshold_is_strict_and_configurable() -> None:
    pixels = np.array([[102, 255, 101, 255, 205, 254]], dtype=np.uint8)
    result, count, removed, cells = MODULE.denoise(pixels, occupied_threshold=0.6)
    np.testing.assert_array_equal(result, [[102, 255, 205, 255, 205, 254]])
    assert (count, removed, cells) == (1, 1, 1)
    unchanged, count, removed, cells = MODULE.denoise(pixels, occupied_threshold=0.8)
    np.testing.assert_array_equal(unchanged, pixels)
    assert (count, removed, cells) == (0, 0, 0)


def test_minimum_one_keeps_every_component() -> None:
    pixels, _ = sample()
    result, count, removed, cells = MODULE.denoise(pixels, min_component_cells=1)
    np.testing.assert_array_equal(result, pixels)
    assert (count, removed, cells) == (4, 0, 0)


def test_minimum_nine_removes_eight_cells_but_keeps_nine() -> None:
    pixels = np.full((7, 13), 205, dtype=np.uint8)
    pixels[1, 1:9] = 0
    pixels[4, 1:10] = 0
    result, count, removed, cells = MODULE.denoise(pixels, min_component_cells=9)
    np.testing.assert_array_equal(result[1, 1:9], np.full(8, 205, dtype=np.uint8))
    np.testing.assert_array_equal(result[4, 1:10], np.zeros(9, dtype=np.uint8))
    assert (count, removed, cells) == (2, 1, 8)


def test_cli_outputs_hashes_and_unchanged_source(tmp_path: Path) -> None:
    pixels, expected = sample()
    source = tmp_path / "source.pgm"
    output = tmp_path / "clean.pgm"
    Image.fromarray(pixels).save(source)
    before = source.read_bytes()
    process = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(output),
         "--min-component-cells", "3"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert source.read_bytes() == before
    for path in (output, output.with_suffix(".png")):
        with Image.open(path) as image:
            assert image.mode == "L"
            assert image.size == (13, 9)
            np.testing.assert_array_equal(np.array(image), expected)
    report = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert json.loads(process.stdout) == report
    assert report["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert report["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert (report["component_count"], report["removed_component_count"],
            report["removed_cell_count"]) == (4, 2, 3)
    assert report["occupied_threshold"] == 0.65


@pytest.mark.parametrize(
    "option,value",
    [("--occupied-threshold", value) for value in ("0", "1", "-0.1", "1.1", "nan", "inf")]
    + [("--min-component-cells", value) for value in ("0", "-1", "1.5")],
)
def test_cli_rejects_invalid_settings(tmp_path: Path, option: str, value: str) -> None:
    source = tmp_path / "source.pgm"
    output = tmp_path / "clean.pgm"
    Image.fromarray(np.array([[0, 205, 254]], dtype=np.uint8)).save(source)
    before = source.read_bytes()
    process = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(output), option, value],
        capture_output=True, check=False, timeout=30,
    )
    assert process.returncode == 2
    assert source.read_bytes() == before
    assert not output.exists()
    assert not output.with_suffix(".png").exists()
    assert not output.with_suffix(".json").exists()


def test_cli_refuses_in_place_edits(tmp_path: Path) -> None:
    source = tmp_path / "source.pgm"
    Image.fromarray(np.array([[0, 205, 254]], dtype=np.uint8)).save(source)
    before = source.read_bytes()
    process = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(source)],
        capture_output=True, check=False, timeout=30,
    )
    assert process.returncode == 2
    assert source.read_bytes() == before
    assert not source.with_suffix(".png").exists()
    assert not source.with_suffix(".json").exists()
