# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Build a Gazebo world from measured XYZ voxels, without projecting onto a 2D map."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import numpy as np
from numpy.typing import NDArray

from cloud_to_map import CloudInputError, read_cloud


@dataclass(frozen=True, slots=True)
class Surface:
    triangles: NDArray[np.float32]
    voxels: int


def voxel_surface(points: NDArray[np.float32], resolution: float) -> Surface:
    """Keep every observed voxel, removing only faces shared by adjacent voxels."""
    xyz = points[np.isfinite(points[:, :3]).all(axis=1), :3]
    if not len(xyz):
        raise CloudInputError(Path("<cloud>"), "no finite XYZ records")
    cells = np.unique(np.floor(xyz / resolution).astype(np.int64), axis=0)
    occupied = {tuple(cell) for cell in cells}
    faces = (
        ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
        ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
        ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
        ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
        ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
        ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
    )
    triangles = []
    for direction, corners in faces:
        neighbours = cells + direction
        exposed = np.array([tuple(cell) not in occupied for cell in neighbours])
        quads = (cells[exposed, None, :] + np.asarray(corners)[None, :, :]) * resolution
        triangles.append(quads[:, ((0, 1, 2), (0, 2, 3)), :].reshape(-1, 3, 3))
    return Surface(np.concatenate(triangles).astype(np.float32), len(cells))


def write_stl(path: Path, triangles: NDArray[np.float32]) -> None:
    """Write metre-scale triangles with outward normals as binary STL."""
    records = np.zeros(len(triangles), dtype=[
        ("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2"),
    ])
    normals = np.cross(triangles[:, 1] - triangles[:, 0],
                       triangles[:, 2] - triangles[:, 0])
    records["normal"] = normals / np.linalg.norm(normals, axis=1)[:, None]
    records["vertices"] = triangles
    with path.open("wb") as stream:
        stream.write(b"PCD observed voxel surface; metres; Z up".ljust(80, b"\0"))
        stream.write(struct.pack("<I", len(triangles)))
        records.tofile(stream)


def write_world(path: Path, low: NDArray[np.float32], high: NDArray[np.float32],
                floor_z: float) -> None:
    """Use the same measured mesh for rendering and static collision."""
    sdf = ET.Element("sdf", version="1.6")
    world = ET.SubElement(sdf, "world", name="19f")
    ET.SubElement(world, "gravity").text = "0 0 -9.81"
    physics = ET.SubElement(world, "physics", name="default", type="ode")
    ET.SubElement(physics, "max_step_size").text = "0.001"
    ET.SubElement(physics, "real_time_update_rate").text = "1000"
    scene = ET.SubElement(world, "scene")
    ET.SubElement(scene, "ambient").text = "0.65 0.65 0.65 1"
    ET.SubElement(scene, "background").text = "0.75 0.8 0.85 1"
    light = ET.SubElement(world, "light", name="sun", type="directional")
    ET.SubElement(light, "diffuse").text = "0.8 0.8 0.8 1"
    ET.SubElement(light, "direction").text = "-0.4 0.2 -1"
    gui = ET.SubElement(world, "gui")
    camera = ET.SubElement(gui, "camera", name="overview")
    ET.SubElement(camera, "pose").text = "38 -45 35 0 0.65 2.3"
    for name in ("pcd_surface", "support_floor"):
        model = ET.SubElement(world, "model", name=name)
        ET.SubElement(model, "static").text = "true"
        link = ET.SubElement(model, "link", name="surface")
        for role in ("visual", "collision"):
            node = ET.SubElement(link, role, name=role)
            geometry = ET.SubElement(node, "geometry")
            if name == "pcd_surface":
                mesh = ET.SubElement(geometry, "mesh")
                ET.SubElement(mesh, "uri").text = "meshes/19f.stl"
            else:
                center = (low + high) / 2
                ET.SubElement(node, "pose").text = (
                    f"{center[0]} {center[1]} {floor_z - 0.05} 0 0 0"
                )
                box = ET.SubElement(geometry, "box")
                ET.SubElement(box, "size").text = (
                    f"{high[0] - low[0] + 2} {high[1] - low[1] + 2} 0.1"
                )
            if role == "visual":
                material = ET.SubElement(node, "material")
                color = "0.65 0.72 0.8 1" if name == "pcd_surface" else "0.3 0.3 0.32 1"
                ET.SubElement(material, "ambient").text = color
                ET.SubElement(material, "diffuse").text = color
    ET.indent(sdf)
    ET.ElementTree(sdf).write(path, encoding="utf-8", xml_declaration=True)


def export(source: Path, output: Path, resolution: float, floor_z: float) -> None:
    """Record source provenance and refuse to replace an existing world directory."""
    if not np.isfinite([resolution, floor_z]).all() or resolution <= 0:
        raise CloudInputError(source, "voxel size must be positive; floor height must be finite")
    cloud = read_cloud(source)
    surface = voxel_surface(cloud.points, resolution)
    xyz = cloud.points[np.isfinite(cloud.points[:, :3]).all(axis=1), :3]
    output.mkdir(parents=True, exist_ok=False)
    (output / "meshes").mkdir()
    write_stl(output / "meshes/19f.stl", surface.triangles)
    write_world(output / "world.sdf", xyz.min(axis=0), xyz.max(axis=0), floor_z)
    report = {
        "schema": "daifuku-pcd-world/1",
        "source": str(source.resolve()),
        "source_sha256": cloud.file_sha256,
        "source_points": len(cloud.points),
        "finite_points": len(xyz),
        "source_bounds_m": [xyz.min(axis=0).tolist(), xyz.max(axis=0).tolist()],
        "voxel_size_m": resolution,
        "occupied_voxels": surface.voxels,
        "triangles": len(surface.triangles),
        "coordinates": "source XYZ in metres, Z up; no rotation, translation or rescaling",
        "reconstruction": "occupied voxels; no denoising, height extrusion or hole filling",
        "support_floor": {
            "top_z_m": floor_z,
            "inferred": True,
            "reason": "sparse measured floor cannot continuously support robot wheels",
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--voxel-size", type=float, default=0.1)
    parser.add_argument("--floor-z", type=float, default=0.0)
    args = parser.parse_args()
    try:
        export(args.source, args.output, args.voxel_size, args.floor_z)
    except (CloudInputError, FileExistsError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
