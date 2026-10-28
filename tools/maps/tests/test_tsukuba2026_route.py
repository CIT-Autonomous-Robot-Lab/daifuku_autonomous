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

"""Recheck the closed reference using full swept bodies, not route-centre pixels."""

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image
import yaml


ROOT = Path(__file__).resolve().parents[3]
MAPS = ROOT / "src/daifuku_maps/maps/tsukuba2026"


def test_reference_loop_has_continuous_body_clearance_and_no_lateral_motion() -> None:
    # Given: the shipped route, real footprint, and selected planner grid.
    route = json.loads((MAPS / "cityhall_loop_reference.json").read_text(encoding="utf-8"))
    metadata = yaml.safe_load((MAPS / "navigation_reviewed.yaml").read_text(encoding="utf-8"))
    source = MAPS / metadata["image"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == route["map_sha256"]
    profile = yaml.safe_load((ROOT / "src/daifuku_config/sites/tsukuba2026/overrides.yaml").read_text(encoding="utf-8"))
    selected_yaml = ROOT / "src/daifuku_maps/maps" / profile["site"]["map"]["navigation"]
    selected = yaml.safe_load(selected_yaml.read_text(encoding="utf-8"))
    with Image.open(source) as image:
        original_pixels = np.flipud(np.array(image))
    with Image.open(selected_yaml.parent / selected["image"]) as image:
        selected_pixels = np.flipud(np.array(image))
    offset = (np.array(selected["origin"][:2])-metadata["origin"][:2])/metadata["resolution"]
    columns, rows = np.rint(offset).astype(int)
    np.testing.assert_allclose(offset, [columns, rows], atol=1e-9, rtol=0)
    np.testing.assert_array_equal(
        selected_pixels,
        original_pixels[rows:rows+selected_pixels.shape[0], columns:columns+selected_pixels.shape[1]],
    )
    assert selected["resolution"] == metadata["resolution"]
    metadata = selected
    scale = int(profile["daifuku_navigation"]["vi_planner"]["ros__parameters"]["map_scale"])
    config = yaml.safe_load((ROOT / "src/daifuku_config/navigation/nav2/costmaps.yaml").read_text(encoding="utf-8"))
    params = config["local_costmap"]["local_costmap"]["ros__parameters"]
    polygon = np.array(json.loads(params["footprint"]), dtype=float)
    np.testing.assert_array_equal(polygon, np.array(route["footprint_base_frame_m"]))
    padding = float(params.get("footprint_padding", .01))
    assert padding == route["padding_m"]
    clearance = float(route["extra_clearance_m"])
    assert clearance >= .03
    low, high = polygon.min(axis=0)-padding-clearance, polygon.max(axis=0)+padding+clearance
    centre, half = (low+high)/2, (high-low)/2
    radius = math.hypot(max(abs(low[0]), abs(high[0])), max(abs(low[1]), abs(high[1])))
    probability = (255-selected_pixels.astype(float))/255
    native_free: NDArray[np.bool_] = probability < float(metadata["free_thresh"])
    height, width = native_free.shape
    padded = np.pad(native_free, ((0, (-height) % scale), (0, (-width) % scale)),
                    constant_values=False)
    free: NDArray[np.bool_] = np.all(
        padded.reshape(padded.shape[0]//scale, scale, padded.shape[1]//scale, scale),
        axis=(1, 3),
    )
    resolution = float(metadata["resolution"])*scale
    ox, oy, angle = metadata["origin"]
    assert angle == 0 and metadata["negate"] == 0

    def check_rectangle(base: NDArray[np.float64], yaw: float, hx: float, hy: float) -> None:
        c, s = math.cos(yaw), math.sin(yaw)
        x, y = base+np.array([c*centre[0]-s*centre[1], s*centre[0]+c*centre[1]])
        ex, ey = hx*abs(c)+hy*abs(s), hx*abs(s)+hy*abs(c)
        c0, c1 = math.floor((x-ex-ox)/resolution), math.floor((x+ex-ox)/resolution)
        r0, r1 = math.floor((y-ey-oy)/resolution), math.floor((y+ey-oy)/resolution)
        assert c0 >= 0 and r0 >= 0 and c1 < free.shape[1] and r1 < free.shape[0]
        rows, columns = np.nonzero(~free[r0:r1+1, c0:c1+1])
        dx, dy = ox+(columns+c0+.5)*resolution-x, oy+(rows+r0+.5)*resolution-y
        # Exact separating axes for an oriented body and axis-aligned cell boxes.
        gaps = np.stack((abs(dx)-ex-resolution/2, abs(dy)-ey-resolution/2,
                         abs(c*dx+s*dy)-hx-resolution/2*(abs(c)+abs(s)),
                         abs(-s*dx+c*dy)-hy-resolution/2*(abs(c)+abs(s))), axis=0).max(axis=0)
        assert np.all(gaps > 1e-9), (base, yaw)

    # When: every complete translation and rotation is checked independently.
    primitives = route["primitives"]
    shortest_move = min(float(np.linalg.norm(np.array(item["end"][:2])-item["start"][:2]))
                        for item in primitives if item["start"][:2] != item["end"][:2])
    assert profile["daifuku_navigation"]["vi_planner"]["ros__parameters"]["goal_tolerance_xy"] < shortest_move
    previous = np.array(primitives[0]["start"], dtype=float)
    points = [previous[:2]]
    length = 0.0
    for item in primitives:
        a, b = np.array(item["start"], dtype=float), np.array(item["end"], dtype=float)
        np.testing.assert_allclose(a[:2], previous[:2], atol=1e-10, rtol=0)
        assert abs(math.remainder(a[2]-previous[2], 2*math.pi)) < 1e-10
        delta, turn = b[:2]-a[:2], math.remainder(b[2]-a[2], 2*math.pi)
        distance = float(np.linalg.norm(delta))
        assert distance > 1e-10 or abs(turn) > 1e-10
        if distance > 1e-10:
            assert abs(turn) < 1e-10
            forward = np.array([math.cos(a[2]), math.sin(a[2])])
            assert abs(delta[0]*forward[1]-delta[1]*forward[0]) < 1e-8
            # A fixed-yaw straight/reverse sweep is exactly a longer rectangle.
            check_rectangle((a[:2]+b[:2])/2, float(a[2]), float(half[0])+distance/2, float(half[1]))
        else:
            steps = max(1, math.ceil(abs(turn)/math.radians(.25)))
            motion = 2*radius*math.sin(abs(turn)/steps/4)
            for fraction in (np.arange(steps)+.5)/steps:
                # This encloses each entire angular subarc about the actual base.
                check_rectangle(a[:2], float(a[2]+fraction*turn),
                                float(half[0])+motion, float(half[1])+motion)
        length += distance
        previous = b
        points.append(b[:2])

    # Then: the certified motions close and wind around the preserved building.
    first = np.array(primitives[0]["start"], dtype=float)
    np.testing.assert_allclose(previous[:2], first[:2], atol=1e-10, rtol=0)
    assert abs(math.remainder(previous[2]-first[2], 2*math.pi)) < 1e-10
    manifest = json.loads((MAPS / "navigation_manual_edits.json").read_text(encoding="utf-8"))
    building = np.array(manifest["protected_polygons"][0]["points"], dtype=float)
    vectors = np.array(points)-building.mean(axis=0)
    a, b = vectors[:-1], vectors[1:]
    winding = float(np.arctan2(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0],
                               (a*b).sum(axis=1)).sum()/(2*math.pi))
    assert abs(abs(winding)-1) < 1e-9
    assert abs(winding-route["winding_number"]) < 1e-9
    assert abs(length-route["length_m"]) < 1e-6
