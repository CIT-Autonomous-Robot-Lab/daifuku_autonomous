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

"""Protect the reviewed map's explicit edit bounds and separate localization layer."""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from scipy import ndimage
import yaml


ROOT = Path(__file__).resolve().parents[3]
MAPS = ROOT / "src/daifuku_stack/maps/tsukuba2026"


@dataclass(frozen=True, slots=True)
class Stroke:
    points: tuple[tuple[float, float], ...]
    radius_m: float


def raster(path: Path) -> NDArray[np.uint8]:
    with Image.open(path) as image:
        assert image.mode == "L"
        return np.array(image, dtype=np.uint8)


def in_strokes(points: NDArray[np.float64], strokes: tuple[Stroke, ...],
               resolution: float) -> NDArray[np.bool_]:
    """Independently check metric distances, not Pillow's drawing coordinates."""
    inside = np.zeros(len(points), dtype=np.bool_)
    for stroke in strokes:
        vertices = np.array(stroke.points)
        limit = stroke.radius_m + math.sqrt(2)*resolution
        for start, end in zip(vertices, vertices[1:]):
            direction = end-start
            fraction = np.clip((points-start) @ direction / (direction @ direction), 0, 1)
            closest = start+fraction[:, None]*direction
            inside |= np.sum((points-closest)**2, axis=1) <= limit**2
    return inside


def test_reviewed_pixels_follow_the_explicit_edit_mask() -> None:
    # Given: the preserved source and the two declared manual actions.
    manifest = json.loads((MAPS / "navigation_manual_edits.json").read_text())
    source = raster(MAPS / manifest["source_image"])
    mask = raster(MAPS / "navigation_reviewed_edit_mask.png")
    expected = source.copy()
    expected[mask > 0] = 254
    # When: the shipped navigation raster is decoded.
    actual = raster(MAPS / "navigation_reviewed.pgm")
    # Then: only declared occupied/unknown cells were replaced, never a no-op.
    assert set(np.unique(mask)) == {0, 1, 2}
    assert np.all(source[mask == 1] == 0)
    assert np.all(source[mask == 2] == 205)
    np.testing.assert_array_equal(actual, expected)


def test_fixed_regions_and_localization_remain_unchanged() -> None:
    # Given: protected fixed geometry and the pre-review localization fingerprint.
    manifest = json.loads((MAPS / "navigation_manual_edits.json").read_text())
    source = raster(MAPS / manifest["source_image"])
    protected = raster(MAPS / "navigation_reviewed_protected_mask.png") > 0
    # When: the delivered navigation/localization assets are read.
    actual = raster(MAPS / "navigation_reviewed.pgm")
    localization = (MAPS / "localization_denoised.pgm").read_bytes()
    # Then: fixed pixels and the entire localization image are preserved.
    np.testing.assert_array_equal(actual[protected], source[protected])
    assert hashlib.sha256(localization).hexdigest() == manifest["localization_preserved_sha256"]


def test_edit_coordinates_stay_inside_the_hand_reviewed_strokes() -> None:
    # Given: strokes expressed in world XY and independently decoded changed cells.
    manifest = json.loads((MAPS / "navigation_manual_edits.json").read_text())
    mask = raster(MAPS / "navigation_reviewed_edit_mask.png")
    resolution = manifest["resolution"]
    ox, oy = manifest["origin"][:2]
    # When: image row centres are transformed using the ROS map convention.
    for value, field in ((1, "clear_transient_traces"), (2, "restore_pavement")):
        rows, columns = np.nonzero(mask == value)
        points = np.column_stack((ox+(columns+.5)*resolution,
                                  oy+(mask.shape[0]-rows-.5)*resolution))
        strokes = tuple(
            Stroke(tuple((float(p[0]), float(p[1])) for p in item["points"]),
                   float(item["width_m"])/2) for item in manifest[field]
        )
        # Then: no flipped, shifted, or globally applied edit can pass.
        assert np.all(in_strokes(points, strokes, resolution))


def test_pavement_interpolation_requires_ground_support() -> None:
    # Given: actual low-surface support, with occupied source cells excluded.
    manifest = json.loads((MAPS / "navigation_manual_edits.json").read_text())
    source = raster(MAPS / manifest["source_image"])
    support = raster(MAPS / manifest["ground_support_image"]) > 0
    mask = raster(MAPS / "navigation_reviewed_edit_mask.png")
    # When: support distances are measured in the unchanged native grid.
    distance = np.empty(source.shape, dtype=np.float64)
    ndimage.distance_transform_edt(~support, sampling=manifest["resolution"], distances=distance)
    # Then: neither unsupported floor nor an occupied support seed is accepted.
    assert not np.any(support & (source == 0))
    assert np.all(distance[mask == 1] <= manifest["maximum_source_ground_distance_m"]+1e-9)
    assert np.all(distance[mask == 2] <= manifest["maximum_pavement_support_distance_m"]+1e-9)


def test_preview_frame_and_profile_match_the_reviewed_map() -> None:
    # Given: the original coordinate frame and selected two-map profile.
    source = yaml.safe_load((MAPS / "navigation_connected.yaml").read_text())
    actual = yaml.safe_load((MAPS / "navigation_reviewed.yaml").read_text())
    profile = yaml.safe_load((ROOT / "src/daifuku_config/overrides/tsukuba2026.yaml").read_text())
    # When: the viewer preview and machine-consumed metadata are decoded.
    pgm = raster(MAPS / actual["image"])
    png = raster(MAPS / "navigation_reviewed.png")
    # Then: viewing cannot disguise geometry changes or switch localization.
    np.testing.assert_array_equal(pgm, png)
    for field in ("resolution", "origin", "negate", "occupied_thresh", "free_thresh"):
        assert actual[field] == source[field]
    assert profile["site"]["map"] == {
        "navigation": "tsukuba2026/navigation_cityhall.yaml",
        "localization": "tsukuba2026/localization_denoised.yaml",
    }


def test_cityhall_crop_preserves_pixels_and_glim_world_coordinates() -> None:
    # Given: a native-resolution full map and the bounded planning map.
    original = yaml.safe_load((MAPS / "navigation_reviewed.yaml").read_text())
    selected = yaml.safe_load((MAPS / "navigation_cityhall.yaml").read_text())
    source = np.flipud(raster(MAPS / original["image"]))
    actual = np.flipud(raster(MAPS / selected["image"]))
    # When: the new origin is transformed into original map cell offsets.
    offset = (np.array(selected["origin"][:2])-original["origin"][:2])/original["resolution"]
    columns, rows = np.rint(offset).astype(int)
    np.testing.assert_allclose(offset, [columns, rows], atol=1e-9, rtol=0)
    # Then: cropping changes extent, not any world-coordinate cell or threshold.
    np.testing.assert_array_equal(actual, source[rows:rows+actual.shape[0],
                                                columns:columns+actual.shape[1]])
    np.testing.assert_array_equal(np.flipud(actual), raster(MAPS / "navigation_cityhall.png"))
    for field in ("resolution", "negate", "occupied_thresh", "free_thresh"):
        assert selected[field] == original[field]


def test_vi_arrival_region_supports_two_cell_transition_outcomes() -> None:
    # Given: the configured planner resolution and machine-consumed goal radius.
    metadata = yaml.safe_load((MAPS / "navigation_reviewed.yaml").read_text())
    profile = yaml.safe_load((ROOT / "src/daifuku_config/overrides/tsukuba2026.yaml").read_text())
    parameters = profile["daifuku_stack"]["vi_planner"]["ros__parameters"]
    resolution = metadata["resolution"] * parameters["map_scale"]
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
    base = yaml.safe_load((ROOT / "src/daifuku_config/stack/vi_planner.yaml").read_text(encoding="utf-8"))
    site = yaml.safe_load((ROOT / "src/daifuku_config/overrides/tsukuba2026.yaml").read_text())
    parameters = (base["vi_planner"]["ros__parameters"] |
                  site["daifuku_stack"]["vi_planner"]["ros__parameters"])
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
    metadata = yaml.safe_load((MAPS / "navigation_cityhall.yaml").read_text())
    profile = yaml.safe_load((ROOT / "src/daifuku_config/overrides/tsukuba2026.yaml").read_text())
    parameters = profile["daifuku_stack"]["vi_planner"]["ros__parameters"]
    resolution = float(np.float32(metadata["resolution"]))*parameters["map_scale"]
    # When: a full subcell/heading distribution is translated.
    for displacement in parameters["action_forward_m"]:
        if displacement == 0:
            continue
        # Then: some XY axis always progresses; the nearest outcomes cannot all
        # skip the adjacent cell as a three-cell forward action does.
        nearest_sample = resolution/(2*64)
        assert math.sqrt(2)*resolution <= abs(displacement) < 2*resolution-nearest_sample
