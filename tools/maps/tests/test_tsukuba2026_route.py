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

"""提供された順路の保存と、計画地図上での座標整合を検査する。"""

import hashlib
import math
from pathlib import Path

import numpy as np
from PIL import Image
import yaml


ROOT = Path(__file__).resolve().parents[3]
MAPS = ROOT / "src/daifuku_maps/maps/tsukuba2026"
WAYPOINTS = ROOT / "src/daifuku_maps/waypoints/waypoints_tsukuba2026.yaml"


def test_supplied_waypoints_are_preserved() -> None:
    # Given: the supplied route's independent fingerprint.
    # When: the installed route source is read.
    actual = hashlib.sha256(WAYPOINTS.read_bytes()).hexdigest()
    # Then: positions, stop flags, and orientations remain the supplied values.
    assert actual == "53e671c91bba362d2796fa1fe4fe8c4754e672dd539a9ab1709516b536903187"


def test_waypoint_poses_are_in_free_cells_of_the_selected_map(monkeypatch) -> None:
    # Given: the public profile, its map metadata, and the supplied route.
    profile = yaml.safe_load((ROOT / "src/daifuku_config/sites/tsukuba2026/overrides.yaml").read_text(encoding="utf-8"))
    selected = MAPS.parent / profile["site"]["map"]["navigation"]
    metadata = yaml.safe_load(selected.read_text(encoding="utf-8"))
    route = yaml.safe_load(WAYPOINTS.read_text(encoding="utf-8"))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 14466 * 8604)
    with Image.open(selected.parent / metadata["image"]) as image:
        pixels = np.asarray(image)
    assert route["frame_id"] == "map"
    assert metadata["origin"][2] == 0.0
    assert metadata["negate"] == 0
    # When: world poses are mapped to image rows (Y increases upward).
    for waypoint in route["waypoints"]:
        position, orientation = waypoint["position"], waypoint["orientation"]
        assert all(math.isfinite(value) for value in (*position.values(), *orientation.values()))
        assert abs(sum(value ** 2 for value in orientation.values()) - 1) < 0.001
        column = math.floor((position["x"] - metadata["origin"][0]) / metadata["resolution"])
        row = pixels.shape[0] - 1 - math.floor((position["y"] - metadata["origin"][1]) / metadata["resolution"])
        # Then: every supplied goal is inside the map and classified as free.
        assert 0 <= column < pixels.shape[1] and 0 <= row < pixels.shape[0], waypoint["name"]
        assert (255 - int(pixels[row, column])) / 255 < metadata["free_thresh"], waypoint["name"]
