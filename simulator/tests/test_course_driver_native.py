# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Original course coordinates must serialize with real ROS message validation."""
from importlib.util import module_from_spec, spec_from_file_location
import json
import math
from pathlib import Path

import pytest

pytest.importorskip("rclpy", reason="requires native ROS message libraries")

from geometry_msgs.msg import PoseStamped
from rclpy.serialization import deserialize_message, serialize_message


def test_every_original_waypoint_serializes_with_real_ros_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: sparse course coordinates also retain the integer-input regression.
    root = Path(__file__).parents[2]
    monkeypatch.syspath_prepend(str(root / "docker/simulator"))
    spec = spec_from_file_location("course_driver", root / "docker/simulator/course_driver.py")
    assert spec is not None and spec.loader is not None
    driver = module_from_spec(spec)
    spec.loader.exec_module(driver)
    course = json.loads((root / "src/daifuku_maps/maps/tsukuba2025/course.json").read_text())
    assert 2 <= len(course["goals"]) <= 30
    mixed_goals = [
        [int(value) if float(value).is_integer() else value for value in goal]
        for goal in course["goals"]
    ]
    assert any(type(value) is int for goal in mixed_goals for value in goal[:2])
    # When: the actual driver converts and serializes every goal before starting motion.
    spawn = course["spawn"][:2] + [course["spawn_yaw"]]
    for goal in [spawn, *course["goals"], *mixed_goals]:
        pose = driver.waypoint_pose(goal)
        restored = deserialize_message(serialize_message(pose), PoseStamped)
        # Then: native validation succeeds without changing map coordinates or heading.
        assert restored.header.frame_id == "map"
        assert restored.pose.position.x == goal[0]
        assert restored.pose.position.y == goal[1]
        assert restored.pose.orientation.z == pytest.approx(math.sin(goal[2]/2))
        assert restored.pose.orientation.w == pytest.approx(math.cos(goal[2]/2))
