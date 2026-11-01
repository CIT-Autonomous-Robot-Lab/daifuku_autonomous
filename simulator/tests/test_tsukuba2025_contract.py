# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Pure checks for the original course's coordinate and action contracts."""
import math
import json
from pathlib import Path

import pytest
import yaml

from daifuku_sim.tsukuba2025.contract import (
    CourseScore, CourseTransform, WaypointProgress, audit_score, initial_localization_ready,
    regional_planner_argv,
)


@pytest.mark.parametrize(
    ("scans", "x_var", "y_var", "yaw_var", "ready"),
    [
        (0, 0, 0, 0, False),
        (9, 0.001, 0.001, 0.001, False),
        (10, 0.01, 0.01, 0.005, True),
        (10, 0.001, 0.001, math.radians(20)**2, False),
        (10, 0.02, 0.001, 0.001, False),
        (10, 0.001, 0.02, 0.001, False),
        (10, 0.001, 0.001, float("nan"), False),
    ],
)
def test_initial_localization_requires_observations_and_a_concentrated_posterior(
    scans: int, x_var: float, y_var: float, yaw_var: float, ready: bool,
) -> None:
    # Given: the actual posterior fields and scans received after the initial seed.
    covariance = [0.0] * 36
    covariance[0], covariance[7], covariance[35] = x_var, y_var, yaw_var
    # When / Then: nominal-pose proximity alone cannot release the motion gate.
    assert initial_localization_ready(covariance, scans) is ready


def test_source_coordinates_retain_original_course_start_and_heading() -> None:
    # Given: the course-centered world has its initial robot at Gazebo origin.
    transform = CourseTransform(298.2, -146.6, math.pi)
    # When: world truth is converted back into the unchanged source map.
    start = transform.source_pose(0, 0, 0)
    ahead = transform.source_pose(10, 2, 0.1)
    # Then: original map coordinates and heading are recovered.
    assert start == pytest.approx((298.2, -146.6, math.pi))
    assert ahead == pytest.approx((288.2, -148.6, -math.pi + 0.1))


def test_original_start_retains_xy_goal_cells_on_the_navigation_grid() -> None:
    # Given: the original start, actual regional resolution and configured goal disk.
    root = Path(__file__).resolve().parents[2]
    site = yaml.safe_load(
        (root / "src/daifuku_config/sites/tsukuba2025/overrides.yaml").read_text(encoding="utf-8")
    )
    course = json.loads((root / "src/daifuku_maps/maps/tsukuba2025/course.json").read_text())
    margin = site["daifuku_navigation"]["vi_planner"]["ros__parameters"]["goal_margin_radius"]
    region = yaml.safe_load(
        (root / "src/daifuku_maps/maps/tsukuba2025/regions/region-000.yaml").read_text()
    )
    resolution, (ox, oy, _) = region["resolution"], region["origin"]
    goal = course["goals"][0][:2]
    ix, iy = (math.floor((value - origin) / resolution) for value, origin in zip(goal, (ox, oy)))
    # When: the compact solver requires both opposite cell corners inside that disk.
    anchors = [
        (ox + (ix + dx) * resolution, oy + (iy + dy) * resolution)
        for dx in (-1, 0, 1) for dy in (-1, 0, 1)
    ]
    # Then: at least one XY cell can be terminal without skipping the start waypoint.
    assert any(
        math.dist((x, y), goal) < margin
        and math.dist((x + resolution, y + resolution), goal) < margin
        for x, y in anchors
    )


@pytest.mark.parametrize("native", [False, True])
def test_spawn_guard_measures_source_position_in_both_simulator_frames(native: bool) -> None:
    transform = CourseTransform(0, 0, 0) if native else CourseTransform(298.2, -146.6, math.pi)
    simulator_xy = (298.2, -146.6) if native else (0, 0)
    spawn = [298.2, -146.6]
    assert transform.position_error(*simulator_xy, spawn) == pytest.approx(0)
    assert transform.position_error(simulator_xy[0] + 0.31, simulator_xy[1], spawn) > 0.3


def test_ordered_feedback_and_success_observe_every_arrival() -> None:
    # Given: a deterministic clock and a three-goal regional group.
    now = [100.0]
    arrivals = []
    progress = WaypointProgress(
        3, 10, lambda index, evidence: arrivals.append((index, evidence)),
        clock=lambda: now[0],
    )
    # When: ordered feedback transitions and group success arrive.
    for index in range(3):
        progress.feedback(index)
        now[0] += 9
    progress.finish()
    # Then: each physical arrival is checked exactly once.
    assert arrivals == [
        (0, "next_waypoint_started"), (1, "next_waypoint_started"), (2, "group_succeeded"),
    ]


@pytest.mark.parametrize("index", [-1, 1, 3])
def test_missing_initial_feedback_cannot_pass(index: int) -> None:
    # Given: no waypoint has started.
    progress = WaypointProgress(3, 10, lambda *_: pytest.fail("false arrival"), clock=lambda: 0)
    # When / Then: feedback that skips the first waypoint is rejected.
    with pytest.raises(RuntimeError):
        progress.feedback(index)


def test_repeated_feedback_does_not_extend_the_deadline() -> None:
    # Given: a goal has started with a bounded deadline.
    now = [0.0]
    progress = WaypointProgress(2, 10, lambda *_: None, clock=lambda: now[0])
    progress.feedback(0)
    # When: the same feedback repeats until the deadline.
    now[0] = 9
    progress.feedback(0)
    now[0] = 10
    # Then: the next transition still fails rather than extending the timeout.
    with pytest.raises(TimeoutError):
        progress.feedback(1)


def test_group_success_cannot_hide_a_missing_final_feedback() -> None:
    # Given: only the first of two goals has started.
    progress = WaypointProgress(2, 10, lambda *_: pytest.fail("false arrival"), clock=lambda: 0)
    progress.feedback(0)
    # When / Then: even a success result cannot invent the missing final arrival.
    with pytest.raises(RuntimeError):
        progress.finish()


def test_new_region_replaces_both_actions_without_changing_sensor_remaps() -> None:
    # Given: a VI process with old action endpoints and its sensor configuration.
    original = [
        "/opt/vi_planner", "--ros-args", "--params-file", "/tmp/params.yaml",
        "-r", "scan:=/scan", "-r", "navigate_to_pose:=/old",
        "-r", "vi_planner:/follow_waypoints/_action/status:=/old/status",
    ]
    # When: successive regions replace the action endpoints.
    first = regional_planner_argv(original, "/region_one")
    second = regional_planner_argv(first, "/region_two")
    # Then: ten unique action entities are isolated, and sensor remapping is retained.
    assert original[-1] == "vi_planner:/follow_waypoints/_action/status:=/old/status"
    assert "scan:=/scan" in second
    assert len(first) == len(second)
    assert not any("/region_one" in argument for argument in second)
    for action in ("navigate_to_pose", "follow_waypoints"):
        for entity in ("send_goal", "get_result", "cancel_goal", "feedback", "status"):
            assert f"{action}/_action/{entity}:=/region_two/{action}/_action/{entity}" in second


def valid_score() -> CourseScore:
    return {
        "status": "PASS", "total_goals": 30, "end_index": 30,
        "completed": [{"index": index, "physical_error_m": 0.2} for index in range(30)],
        "live_truth_localization": False, "physical_goal_error_limit_m": 0.6,
    }


def test_complete_original_course_passes_independent_arrival_audit() -> None:
    # Given: every original goal has independent physical evidence.
    score = valid_score()
    # When / Then: the full-course audit accepts the exact ordered coverage.
    audit_score(score, 30, 0.6)


@pytest.mark.parametrize("failure", ["partial", "skipped", "truth", "relaxed", "nan", "outside"])
def test_false_full_course_results_are_rejected(failure: str) -> None:
    # Given: a result that violates one independent course invariant.
    score = valid_score()
    match failure:
        case "partial":
            score["status"] = "PARTIAL_PASS"
        case "skipped":
            score["completed"].pop(15)
        case "truth":
            score["live_truth_localization"] = True
        case "relaxed":
            score["physical_goal_error_limit_m"] = 1.0
        case "nan":
            score["completed"][15]["physical_error_m"] = math.nan
        case "outside":
            score["completed"][15]["physical_error_m"] = 0.61
        case _:
            pytest.fail("unknown test case")
    # When / Then: it cannot be reported as a verified original-course PASS.
    with pytest.raises(ValueError):
        audit_score(score, 30, 0.6)


def test_full_coverage_uses_the_authoritative_configured_count() -> None:
    # Given: a configured route with fewer than the maximum thirty targets.
    score = valid_score()
    score.update(total_goals=25, end_index=25, completed=score["completed"][:25])
    # When / Then: all configured targets constitute full coverage.
    audit_score(score, 25, 0.6, total_goals=25)


def test_shorter_claim_cannot_replace_the_authoritative_configured_count() -> None:
    # Given: a shorter run claims PASS although thirty targets were configured.
    score = valid_score()
    score.update(total_goals=25, end_index=25, completed=score["completed"][:25])
    # When / Then: its own shorter total cannot establish full-course completion.
    with pytest.raises(ValueError):
        audit_score(score, 25, 0.6, total_goals=30)
