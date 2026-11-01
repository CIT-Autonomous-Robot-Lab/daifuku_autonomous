# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Public entry point for original-course Gazebo runs."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
from .contract import MAX_WAYPOINTS, CourseMetadata, CourseOptions


def configure_parser(parser: argparse.ArgumentParser) -> None:
    from ..vtc.runtime import REPO, SIMULATOR

    course: CourseMetadata = json.loads(
        (REPO / "src/daifuku_maps/maps/tsukuba2025/course.json").read_text(encoding="utf-8")
    )
    total_goals = len(course["goals"])
    if not 2 <= total_goals <= MAX_WAYPOINTS:
        parser.error(f"the configured course must contain 2..{MAX_WAYPOINTS} waypoints")

    _ = parser.add_argument("--world", type=Path, default=SIMULATOR / "runs" / "tsukuba2025-world" / "world.sdf")
    _ = parser.add_argument("--backend", choices=("gazebo", "isaacsim"), default="gazebo")
    _ = parser.add_argument("--lidar-sensor-type", choices=("ray", "gpu_ray"), default="ray")
    _ = parser.add_argument("--usim-root", type=Path, help="explicit usim asset/build checkout")
    _ = parser.add_argument("--engine", choices=("docker", "podman"), default=os.environ.get("VTC_ENGINE", "docker"))
    _ = parser.add_argument("--run-dir", type=Path, default=SIMULATOR / "runs" / "vtc" / dt.datetime.now().strftime("tsukuba2025-%Y%m%d-%H%M%S-%f"))
    _ = parser.add_argument("--stack-image", default="daifuku-usim-vtc-stack:local")
    _ = parser.add_argument("--base-image", default="daifuku-autonomous:humble")
    _ = parser.add_argument("--usim-image", default="usim-gazebo:local")
    _ = parser.add_argument("--no-build", action="store_true")
    _ = parser.add_argument("--headless", action="store_true")
    _ = parser.add_argument("--keep-open", action="store_true", help="keep the successful final simulation visible until its GUI is closed")
    _ = parser.add_argument("--goal-timeout", type=float, default=1800)
    _ = parser.add_argument("--startup-timeout", type=float, default=900)
    _ = parser.add_argument("--course-timeout", type=float, default=28800)
    _ = parser.add_argument(
        "--end-index", type=int, default=total_goals,
        help="diagnostics only; a subset of the configured course cannot PASS",
    )
    _ = parser.add_argument("--ros-domain-id", type=int, default=87, choices=range(102))

    def dispatch(options: CourseOptions) -> int:
        for name, seconds in (
            ("goal_timeout", options.goal_timeout), ("course_timeout", options.course_timeout),
            ("startup_timeout", options.startup_timeout),
        ):
            if not math.isfinite(seconds) or seconds <= 0:
                parser.error(f"--{name.replace('_', '-')} must be finite and positive")
        if not 0 < options.end_index <= total_goals:
            parser.error(f"--end-index must be between 1 and {total_goals}")
        if options.keep_open and options.headless:
            parser.error("--keep-open requires the Gazebo GUI")
        if options.backend != "gazebo" and options.lidar_sensor_type != "ray":
            parser.error("--lidar-sensor-type gpu_ray requires --backend gazebo")
        from .workflow import run
        return run(options)

    parser.set_defaults(handler=dispatch)
