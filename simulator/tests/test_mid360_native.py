# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Opt-in real Gazebo acceptance; no ROS mocks and no timing sleeps."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import runpy

import pytest

from daifuku_sim.runtime import ContainerRuntime, start_session
from daifuku_sim.tsukuba2025.robot import mount_mid360
from daifuku_sim.tsukuba2025.workflow import course_command
from daifuku_sim.vtc.cli import parse_args
from daifuku_sim.vtc.contract import SessionConfig
from daifuku_sim.vtc.runtime import REPO, URDF_RELATIVE
from daifuku_sim.vtc.workflow import Workflow


@pytest.mark.skipif(os.environ.get("RUN_GAZEBO_MID360") != "1", reason="requires native Docker Gazebo images")
@pytest.mark.parametrize("ground_geometry", ["box", "exported_mesh"])
def test_real_gazebo_emits_three_dimensional_mid360_clouds(tmp_path: Path, ground_geometry: str) -> None:
    # Given: real collision surfaces and the actual Raspicat mounted Mid-360.
    world = tmp_path / "sensor-world.sdf"
    ground = '<box><size>30 30 0.2</size></box>'
    ground_pose = "0 0 -0.1 0 0 0"
    if ground_geometry == "exported_mesh":
        import numpy as np
        exporter = runpy.run_path(str(REPO / "simulator/tools/package_tsukuba2025.py"))
        exporter["write_dae"](
            tmp_path / "ground.dae",
            np.array([[-15, -15, 0], [15, -15, 0], [15, 15, 0], [-15, 15, 0]], dtype=float),
            np.array([[0, 1, 2], [0, 2, 3]]), np.ones((4, 3)),
        )
        ground = "<mesh><uri>ground.dae</uri></mesh>"
        ground_pose = "0 0 0 0 0 0"
    world.write_text(
        '<sdf version="1.6"><world name="mid360_acceptance"><gravity>0 0 -9.81</gravity>'
        '<model name="ground"><static>true</static><link name="surface">'
        f'<collision name="ground"><geometry>{ground}</geometry>'
        f'<pose>{ground_pose}</pose></collision></link></model>'
        '<model name="wall"><static>true</static><pose>3 0 2 0 0 0</pose><link name="surface">'
        '<collision name="wall"><geometry><box><size>0.2 10 4</size></box></geometry>'
        '</collision></link></model></world></sdf>',
    )
    options = parse_args([
        "--world", str(world), "--run-dir", str(tmp_path / "run"),
        "--usim-root", str(REPO.parent / "usim"), "--headless", "--no-build",
    ])
    workflow = Workflow(options, ContainerRuntime("docker"))
    workflow.run_dir.mkdir()
    probe = None
    try:
        workflow.preflight()
        workflow.check_runtime()
        workflow.prepare()
        mount_mid360(workflow.run_dir / URDF_RELATIVE)
        config: SessionConfig = {
            "engine": "docker", "network": workflow.runtime.network,
            "image": options.usim_image, "world": str(world),
            "robot": workflow.robot, "headless": True, "camera_enabled": False,
        }
        script = workflow.run_dir / "mid360_probe.py"
        shutil.copyfile(REPO / "docker" / "simulator" / script.name, script)
        name = workflow._name("mid360-probe")
        workflow.containers.append(name)
        command = ["docker", "run", "--rm", "--name", name, "--network", workflow.runtime.network]
        for key, value in workflow._ros_env().items():
            command += ["--env", f"{key}={value}"]
        command += [
            "--env", "PYTHONPATH=/opt/usim_vtc", "--mount",
            f"type=bind,source={workflow.run_dir},target=/output",
            "--entrypoint", "bash", options.stack_image, "-c",
            "source /opt/ros/humble/setup.bash; python3 -u /output/mid360_probe.py --out /output/mid360.json",
        ]
        # Subscribe before triggering simulation; readline is bounded by actual process termination.
        probe = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        startup_deadline = threading.Timer(60, probe.kill)
        startup_deadline.start()
        try:
            assert probe.stdout is not None
            ready = probe.stdout.readline()
            assert ready.strip() == "MID360_PROBE_READY", ready
        finally:
            startup_deadline.cancel()
        # When: the unmodified installed CLI starts real Gazebo with the 3D ray configuration.
        workflow.sessions.append(start_session(
            course_command(config), workflow.run_dir / "simulator.log", workflow._ros_env(),
        ))
        output, _ = probe.communicate(timeout=150)
        # Then: actual XYZ data, acquisition stamps and native publisher are verified.
        assert probe.returncode == 0, output
        measured = json.loads((workflow.run_dir / "mid360.json").read_text())
        print("NATIVE_MID360 " + json.dumps(measured))
        assert measured["status"] == "PASS"
        assert measured["geometry"]["frame"] == "livox_frame"
        assert measured["geometry"]["finite_points"] > 100
        assert measured["geometry"]["max_z"] - measured["geometry"]["min_z"] > 1
        assert set(("x", "y", "z", "intensity")) <= set(measured["fields"])
        assert "usim_lidar_ros" in measured["publishers"]
        assert 8 <= measured["rate_sim_hz"] <= 11
        assert measured["rate_wall_hz"] > 0
        assert measured["real_time_factor"] > 0
        assert measured["robot_xyz"] == pytest.approx([0, 0, 0], abs=0.02)
    finally:
        if probe is not None and probe.poll() is None:
            probe.kill()
            probe.communicate(timeout=30)
        workflow.cleanup()
