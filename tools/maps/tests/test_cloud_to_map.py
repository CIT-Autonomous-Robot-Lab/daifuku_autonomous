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

"""Check point-record boundaries, height bands and ROS image orientation."""

import hashlib
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "cloud_to_map.py"
SPEC = importlib.util.spec_from_file_location("cloud_to_map", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_pcd_page_padding_is_not_an_extra_point(tmp_path: Path) -> None:
    points = np.array([[1.0, 2.0, 3.0, 4.0], [-1.0, -2.0, -3.0, 5.0]], dtype="<f4")
    payload = points.tobytes()
    pcd = tmp_path / "input.pcd"
    pcd.write_bytes(
        b"VERSION 0.7\nFIELDS x y z intensity\nSIZE 4 4 4 4\n"
        b"TYPE F F F F\nCOUNT 1 1 1 1\nWIDTH 2\nHEIGHT 1\nPOINTS 2\nDATA binary\n"
        + payload + bytes(3904)
    )
    ply = tmp_path / "input.ply"
    ply.write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement vertex 2\n"
        b"property float x\nproperty float y\nproperty float z\n"
        b"property float intensity\nend_header\n" + payload
    )
    first, second = MODULE.read_cloud(pcd), MODULE.read_cloud(ply)
    np.testing.assert_array_equal(first.points, points)
    np.testing.assert_array_equal(second.points, points)
    assert first.payload_sha256 == second.payload_sha256 == hashlib.sha256(payload).hexdigest()
    assert (first.trailing_bytes, second.trailing_bytes) == (3904, 0)
    assert first.file_sha256 == hashlib.sha256(pcd.read_bytes()).hexdigest()


def test_nonzero_trailing_records_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.pcd"
    path.write_bytes(
        b"FIELDS x y z intensity\nSIZE 4 4 4 4\nTYPE F F F F\n"
        b"COUNT 1 1 1 1\nPOINTS 1\nDATA binary\n" + bytes(16) + b"\1"
    )
    with pytest.raises(MODULE.CloudInputError):
        MODULE.read_cloud(path)


def test_local_ground_height_bands_and_top_down_rows() -> None:
    x, y = np.meshgrid(np.arange(-2.0, 2.01, 0.1), np.arange(-2.0, 2.01, 0.1))
    floor = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, -0.5),
                             np.zeros(x.size)))
    obstacles = np.array([[1.05, 0.55, 0.3, 1.0], [-1.05, -0.55, 3.0, 1.0]])
    points = np.vstack((floor, obstacles)).astype(np.float32)
    original = points.copy()
    maps = MODULE.project(points, MODULE.Settings(resolution=0.1, ground_ceiling=1.0))
    assert maps.origin == (-4.0, -4.0)
    assert maps.navigation.shape == maps.localization.shape == (80, 80)
    # These coordinates are inside cells, not on floating-point grid boundaries.
    low_column, low_row = 50, 34
    high_column, high_row = 29, 45
    assert maps.navigation[low_row, low_column] == 0
    assert maps.localization[low_row, low_column] == 0
    assert maps.navigation[high_row, high_column] != 0
    assert maps.localization[high_row, high_column] == 0
    assert maps.navigation[0, 0] == 205
    assert maps.navigation[40, 40] == 254
    np.testing.assert_array_equal(points, original)
