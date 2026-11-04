# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Check actual mesh coordinates, closed outward surfaces and SDF asset wiring."""

import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pcd_to_world import export, voxel_surface


@pytest.mark.parametrize("points,expected_triangles,low,high", [
    ([[-0.11, 0.21, 1.01], [-0.12, 0.22, 1.02]], 12,
     [-0.2, 0.2, 1.0], [-0.1, 0.3, 1.1]),
    ([[0.01, 0.01, 0.01], [0.11, 0.01, 0.01]], 20,
     [0, 0, 0], [0.2, 0.1, 0.1]),
])
def test_surface_keeps_xyz_and_removes_only_shared_faces(
    points, expected_triangles, low, high,
) -> None:
    surface = voxel_surface(np.asarray(points, dtype=np.float32), 0.1)
    triangles = surface.triangles
    assert len(triangles) == expected_triangles
    np.testing.assert_allclose(triangles.min(axis=(0, 1)), low, atol=1e-7)
    np.testing.assert_allclose(triangles.max(axis=(0, 1)), high, atol=1e-7)
    # Signed enclosed volume catches reversed winding and missing exterior faces.
    volume = np.einsum("ij,ij->i", triangles[:, 0],
                       np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6
    assert volume == pytest.approx(surface.voxels * 0.1 ** 3, rel=1e-4)


def test_export_wires_same_real_stl_into_visual_and_collision(tmp_path: Path) -> None:
    source = tmp_path / "source.pcd"
    points = np.asarray([[-1.01, 2.01, 0.31, 7]], dtype="<f4")
    source.write_bytes(
        b"FIELDS x y z intensity\nSIZE 4 4 4 4\nTYPE F F F F\n"
        b"COUNT 1 1 1 1\nWIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA binary\n"
        + points.tobytes()
    )
    original = source.read_bytes()
    output = tmp_path / "world"

    export(source, output, 0.1, -0.2)

    world = ET.parse(output / "world.sdf")
    visual = world.findtext(".//model[@name='pcd_surface']/link/visual/geometry/mesh/uri")
    collision = world.findtext(".//model[@name='pcd_surface']/link/collision/geometry/mesh/uri")
    assert visual == collision
    mesh = (output / visual).read_bytes()
    count = struct.unpack_from("<I", mesh, 80)[0]
    assert count == 12
    assert len(mesh) == 84 + 50 * count
    vertices = np.frombuffer(mesh, offset=84, dtype=[
        ("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2"),
    ])["vertices"]
    np.testing.assert_allclose(vertices.min(axis=(0, 1)), [-1.1, 2, 0.3], atol=1e-6)
    floor_pose = world.findtext(".//model[@name='support_floor']/link/collision/pose")
    assert float(floor_pose.split()[2]) == pytest.approx(-0.25)
    assert json.loads((output / "manifest.json").read_text())["source_points"] == 1
    assert source.read_bytes() == original


def test_shipped_world_has_matching_mesh_and_manifest() -> None:
    root = Path(__file__).resolve().parents[3] / "simulator/worlds/19f"
    world = ET.parse(root / "world.sdf")
    mesh_paths = [root / node.text for node in world.findall(".//mesh/uri")]
    assert len(mesh_paths) == 2
    assert mesh_paths[0] == mesh_paths[1]
    mesh = mesh_paths[0].read_bytes()
    manifest = json.loads((root / "manifest.json").read_text())
    count = struct.unpack_from("<I", mesh, 80)[0]
    assert count == manifest["triangles"]
    assert len(mesh) == 84 + 50 * count
    vertices = np.frombuffer(mesh, offset=84, dtype=[
        ("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2"),
    ])["vertices"]
    assert np.isfinite(vertices).all()
    low, high = np.asarray(manifest["source_bounds_m"])
    resolution = manifest["voxel_size_m"]
    np.testing.assert_allclose(vertices.min(axis=(0, 1)),
                               np.floor(low / resolution) * resolution, atol=1e-5)
    np.testing.assert_allclose(vertices.max(axis=(0, 1)),
                               (np.floor(high / resolution) + 1) * resolution, atol=1e-5)
