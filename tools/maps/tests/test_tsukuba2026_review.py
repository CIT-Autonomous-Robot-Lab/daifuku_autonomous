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

"""提供地図の画素・座標と、VIの格子上の到着条件を検査する。"""

import hashlib
import math
from pathlib import Path

import numpy as np
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
MAPS = ROOT / "src/daifuku_maps/maps/tsukuba2026"


@pytest.mark.parametrize("name,digest", [
    ("tsukuba2026.pgm", "35d4c51ac08f2627cf293da762482a8bff3231e3e98c04b0af94bc7487ccce44"),
    ("tsukuba2026_modified.pgm", "3206507d8a33592f62d0c6c7fbde28e775bf4193dc06636dc128f7dbeb689df4"),
])
def test_provided_map_pixels_are_preserved(name: str, digest: str) -> None:
    # Given: independent fingerprints of the supplied original and edited maps.
    # When: the shipped asset is read.
    actual = hashlib.sha256((MAPS / name).read_bytes()).hexdigest()
    # Then: importing or renaming assets cannot silently change their pixels.
    assert actual == digest


def test_navigation_and_localization_keep_the_provided_world_frame() -> None:
    # Given: the selected maps and the supplied coordinate metadata.
    profile = yaml.safe_load((ROOT / "src/daifuku_config/sites/tsukuba2026/overrides.yaml").read_text(encoding="utf-8"))
    # When: the public profile's map references are resolved.
    maps = profile["site"]["map"]
    navigation = yaml.safe_load((MAPS.parent / maps["navigation"]).read_text(encoding="utf-8"))
    localization = yaml.safe_load((MAPS.parent / maps["localization"]).read_text(encoding="utf-8"))
    # Then: planning uses the edit, localization uses the original in the same frame.
    assert maps == {
        "navigation": "tsukuba2026/tsukuba2026_modified.yaml",
        "localization": "tsukuba2026/tsukuba2026.yaml",
        "pointcloud": "tsukuba2026/tsukuba2026.pcd",
    }
    assert navigation["image"] == "tsukuba2026_modified.pgm"
    assert localization["image"] == "tsukuba2026.pgm"
    assert navigation | {"image": localization["image"]} == localization
    assert localization["resolution"] == 0.05
    assert localization["origin"] == [-239.888, -81.802, 0.0]
    assert localization["negate"] == 0
    assert localization["occupied_thresh"] == 0.65
    assert localization["free_thresh"] == 0.25


def test_vi_arrival_region_supports_two_cell_transition_outcomes() -> None:
    # Given: the configured planner resolution and machine-consumed goal radius.
    metadata = yaml.safe_load((MAPS / "tsukuba2026_modified.yaml").read_text(encoding="utf-8"))
    profile = yaml.safe_load((ROOT / "src/daifuku_config/sites/tsukuba2026/overrides.yaml").read_text(encoding="utf-8"))
    parameters = profile["daifuku_navigation"]["vi_planner"]["ros__parameters"]
    resolution = metadata["resolution"] * parameters.get("map_scale", 1)
    radius = parameters["goal_margin_radius"]
    rows, columns = np.mgrid[-4:5, -4:5]
    lower = np.stack((columns, rows), axis=-1)*resolution
    upper = lower+resolution
    # When: goals fall at different subcell offsets on an obstacle-free patch.
    for x in np.linspace(0, resolution, 9):
        for y in np.linspace(0, resolution, 9):
            goal = np.array([x, y])
            final = ((np.sum((lower-goal)**2, axis=-1) < radius**2) &
                     (np.sum((upper-goal)**2, axis=-1) < radius**2))
            # Then: a complete 2x2 seed patch can absorb sampled XY outcomes.
            blocks = final[:-1, :-1] & final[1:, :-1] & final[:-1, 1:] & final[1:, 1:]
            assert np.any(blocks), (x, y, radius, resolution)


def test_vi_arrival_angles_cover_every_discrete_action_phase() -> None:
    # Given: the actual heading cells and all configured rotation actions.
    base = yaml.safe_load((ROOT / "src/daifuku_config/navigation/vi_planner.yaml").read_text(encoding="utf-8"))
    site = yaml.safe_load((ROOT / "src/daifuku_config/sites/tsukuba2026/overrides.yaml").read_text(encoding="utf-8"))
    parameters = (base["vi_planner"]["ros__parameters"] |
                  site["daifuku_navigation"]["vi_planner"]["ros__parameters"])
    count = int(parameters["theta_cell_num"])
    resolution = 360/count
    steps = [int(round(float(angle)/resolution))
             for angle in parameters["action_rotation_deg"] if angle]
    assert all(abs(step*resolution-angle) < 1e-9
               for step, angle in zip(steps, (a for a in parameters["action_rotation_deg"] if a)))
    phases = math.gcd(count, *steps)
    margin = float(parameters["goal_margin_theta"])
    lower, upper = np.arange(count)*resolution, (np.arange(count)+1)*resolution
    # When: each integer goal heading is converted to fully contained final cells.
    for goal in range(360):
        other = goal-360 if goal > 180 else goal+360
        final = (((lower >= goal-margin) & (upper <= goal+margin)) |
                 ((lower >= other-margin) & (upper <= other+margin)))
        # Then: every phase remains covered after losing either edge cell to
        # floating-point angle quantization (120deg was mapped to cell 19).
        indices = np.flatnonzero(final)
        ordered = sorted(indices, key=lambda cell: (cell*resolution-goal+180) % 360-180)
        covered = set(np.array(ordered[1:-1], dtype=int) % phases)
        assert covered == set(range(phases)), (goal, margin, covered)


def test_translation_lengths_progress_without_skipping_native_cells() -> None:
    # Given: the selected planner cell size and each nonzero translation.
    metadata = yaml.safe_load((MAPS / "tsukuba2026_modified.yaml").read_text(encoding="utf-8"))
    profile = yaml.safe_load((ROOT / "src/daifuku_config/sites/tsukuba2026/overrides.yaml").read_text(encoding="utf-8"))
    parameters = profile["daifuku_navigation"]["vi_planner"]["ros__parameters"]
    resolution = float(np.float32(metadata["resolution"]))*parameters.get("map_scale", 1)
    # When: a full subcell/heading distribution is translated.
    for displacement in parameters["action_forward_m"]:
        if displacement == 0:
            continue
        # Then: some XY axis always progresses; the nearest outcomes cannot all
        # skip the adjacent cell as a three-cell forward action does.
        nearest_sample = resolution/(2*64)
        assert math.sqrt(2)*resolution <= abs(displacement) < 2*resolution-nearest_sample
