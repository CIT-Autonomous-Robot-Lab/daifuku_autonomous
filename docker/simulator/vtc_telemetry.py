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
"""JSON shapes emitted by the ROS probes."""

from typing import TypedDict

from vtc_contract import Alignments, MapStats, PoseJson


class SlamTruth(TypedDict):
    slam_map: PoseJson
    truth_world: PoseJson
    error_m: float
    yaw_error_rad: float


class ProbeResult(TypedDict, total=False):
    ok: bool
    error: str
    odom_frame: str
    odom_child_frame: str
    scan_raw_frame: str
    scan_raw_samples: int
    scan_raw_finite: int
    base_to_lidar: PoseJson
    base_footprint_dynamic_parents: list[str]
    base_footprint_static_parent: str | None
    static_edges: dict[str, str]
    publishers: dict[str, list[str]]
    nodes: list[str]
    counts: dict[str, int]
    waypoints_world: list[PoseJson]
    waypoint_arrivals: list[PoseJson]
    waypoints_completed: int
    duration_s: float
    distance_m: float
    map: MapStats
    alignment: Alignments
    slam_vs_truth: SlamTruth
    tf_edges: list[str]
    seed_map: PoseJson
    goal_map: PoseJson
    truth_start: PoseJson
    estimate_start: PoseJson
    accepted: bool
    navigation_server_state: str
    timed_out: bool
    elapsed_s: float
    action_status: int
    feedback_count: int
    truth_final: PoseJson
    estimate_final: PoseJson | None
