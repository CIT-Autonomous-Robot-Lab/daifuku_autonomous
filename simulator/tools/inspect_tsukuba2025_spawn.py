# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["numpy==1.26.4", "usd-core==25.5.1"]
# ///
"""Inspect actual composed collision heights under the initial robot footprint.

uv run simulator/tools/inspect_tsukuba2025_spawn.py
"""
import json
import math
import xml.etree.ElementTree as ET

import numpy as np
from pxr import Usd, UsdGeom

from package_tsukuba2025 import (
    DEFAULT_COURSE, DEFAULT_STAGE, collides, mesh_arrays, meshes, resident, triangle_height,
)


def main():
    course = json.loads(DEFAULT_COURSE.read_text())
    spawn = np.asarray(course["spawn"], dtype=float)
    yaw = course["spawn_yaw"]
    c, s = math.cos(yaw), math.sin(yaw)
    footprint = np.array([
        (x, y) for x in (-.30, -.15, 0, .17) for y in (-.14, 0, .14)
    ])
    samples = footprint @ np.array([[c, s], [-s, c]]) + spawn[:2]
    low, high = samples.min(axis=0), samples.max(axis=0)
    entries = json.loads(DEFAULT_STAGE.with_name("world.index.json").read_text())
    stage = Usd.Stage.Open(str(DEFAULT_STAGE), Usd.Stage.LoadNone)
    measurements = []
    for entry in entries:
        if any(entry["max"][i] < low[i] or entry["min"][i] > high[i] for i in (0, 1)):
            continue
        with resident(stage, entry["path"]) as root:
            cache = UsdGeom.XformCache()
            for mesh in meshes(root):
                if not collides(mesh.GetPrim()):
                    continue
                points, faces = mesh_arrays(mesh, cache)
                if any(points[:, i].max() < low[i] or points[:, i].min() > high[i]
                       for i in (0, 1)):
                    continue
                heights = [triangle_height(points, faces, point) for point in samples]
                finite = [z for column in heights for z in column]
                if finite:
                    measurements.append({
                        "prim": str(mesh.GetPath()), "min_z": min(finite), "max_z": max(finite),
                        "heights": heights,
                    })
    exported = DEFAULT_COURSE.parents[4] / "simulator/runs/tsukuba2025-world"
    manifest = json.loads((exported / "manifest.json").read_text())
    ns = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
    converted = []
    measured_paths = {item["prim"] for item in measurements}
    for record in manifest["meshes"]:
        if record["source_prim"] not in measured_paths or not record["collision"]:
            continue
        document = ET.parse(exported / record["collision"])
        points = np.fromstring(
            document.findtext(".//c:float_array[@id='position-array']", "", ns), sep=" ",
        ).reshape(-1, 3)
        inputs = document.findall(".//c:triangles/c:input", ns)
        stride = 1 + max(int(node.get("offset", "0")) for node in inputs)
        faces = np.fromstring(document.findtext(".//c:triangles/c:p", "", ns), sep=" ",
                              dtype=np.int64).reshape(-1, 3, stride)[:, :, 0]
        points += record["position"]
        points[:, 2] -= manifest["transform"]["source_origin_m"][2]
        area = np.cross(points[faces[:, 1]] - points[faces[:, 0]],
                        points[faces[:, 2]] - points[faces[:, 0]])
        converted.append({
            "prim": record["source_prim"], "asset": record["collision"],
            "height_at_gazebo_origin": triangle_height(points, faces, [0, 0]),
            "up_faces": int(np.sum(area[:, 2] > 0)), "down_faces": int(np.sum(area[:, 2] < 0)),
        })
    print(json.dumps({"spawn": spawn.tolist(), "samples_xy": samples.tolist(),
                      "colliders": measurements, "exported": converted}, indent=2))


if __name__ == "__main__":
    main()
