# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Original source integrity, native coordinates and explicit ROS transport."""
import hashlib
import io
import json
import math
from pathlib import Path
import sys
from types import ModuleType
import xml.etree.ElementTree as ET

import pytest

from daifuku_sim.tsukuba2025 import isaac
from daifuku_sim.vtc.contract import RobotGeometry


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, RobotGeometry]:
    stage, index = tmp_path / "original.usda", tmp_path / "world.index.json"
    stage.write_text("#usda 1.0")
    index.write_text("[]")
    package = tmp_path / "world.sdf"
    package.with_name("manifest.json").write_text(json.dumps({
        "course_sha256": "original-course",
        "source_stage": str(stage),
        "source_index": str(index),
        "transform": {
            "source_spawn_m": [298.2, -146.6, -0.6],
            "source_yaw_rad": math.pi,
            "source_index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
            "source_layers": [{
                "path": str(stage), "sha256": hashlib.sha256(stage.read_bytes()).hexdigest(),
            }],
        },
    }))
    robot: RobotGeometry = {
        "urdf": str(tmp_path / "robot.urdf"), "base_link": "base_footprint",
        "lidar_link": "livox_frame", "left_joint": "left_wheel_joint",
        "right_joint": "right_wheel_joint", "wheel_radius": 0.0762,
        "wheel_separation": 0.27918, "lidar_height_m": 0.275,
        "lidar_chain": ["base_footprint", "livox_frame"],
    }
    return package, robot


def test_configuration_preserves_original_source_and_sensor_workload(source) -> None:
    package, robot = source
    data = isaac.configuration(package, robot, "original-course", headless=True)
    fields = data["configuration"]
    assert fields["world"] == str(package.with_name("original.usda"))
    assert fields["initial_position"] == (298.2, -146.6, -0.6)
    assert fields["initial_yaw"] == math.pi
    assert fields["lidar_samples"] == 720
    assert len(fields["lidar_vertical_angles"]) == 28
    assert fields["lidar_vertical_angles"][0] == pytest.approx(math.radians(-7))
    assert fields["lidar_vertical_angles"][-1] == pytest.approx(math.radians(52))
    assert fields["lidar_hz"] == 10
    assert fields["lidar_max_range"] == 70
    assert fields["lidar_offset"] == (0.144, 0, 0.275)
    assert fields["ros"]["publish_lidar_tf"] is False
    assert fields["ros"]["points_topic"] == "/livox/lidar"
    assert data["backend"]["physics_dt"] == 0.001
    assert data["backend"]["lidar_mode"] == "rtx"


@pytest.mark.parametrize("changed", ["course", "layer", "index"])
def test_changed_source_cannot_be_silently_reused(source, changed: str) -> None:
    package, robot = source
    course = "original-course"
    if changed == "course":
        course = "different-course"
    elif changed == "layer":
        package.with_name("original.usda").write_text("#changed")
    else:
        package.with_name("world.index.json").write_text("[{}]")
    with pytest.raises(ValueError):
        isaac.configuration(package, robot, course, headless=True)


def test_transport_retains_tcp_and_container_udp_discovery(tmp_path: Path) -> None:
    server_path, client_path = isaac.transport_profiles(tmp_path, "192.168.65.254", 93)
    namespace = {"p": "http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles"}
    server, client = ET.parse(server_path), ET.parse(client_path)
    assert server.findtext(".//p:listening_ports/p:port", namespaces=namespace) == "55100"
    assert server.findtext(".//p:wan_addr", namespaces=namespace) == "192.168.65.254"
    assert server.findtext(".//p:useBuiltinTransports", namespaces=namespace) == "false"
    assert client.findtext(".//p:useBuiltinTransports", namespaces=namespace) == "true"
    assert client.find(".//p:transport_descriptors", namespace) is None
    assert client.find(".//p:userTransports", namespace) is None
    assert client.findtext(".//p:initialPeersList/p:locator/p:udpv4/p:port",
                           namespaces=namespace) == str(7400 + 250 * 93)
    assert client.findtext(".//p:initialPeersList/p:locator/p:tcpv4/p:physical_port",
                           namespaces=namespace) == "55100"
    assert client.find(".//p:metatrafficMulticastLocatorList", namespace) is None


def test_worker_keeps_stdin_cancellation_outside_the_sdk(source, monkeypatch) -> None:
    package, robot = source
    configuration = isaac.configuration(package, robot, "original-course", headless=True)
    path = package.with_name("worker.json")
    path.write_text(json.dumps(configuration))
    executed = []

    class Native:
        def __init__(self, **options):
            assert options["lidar_mode"] == "rtx"
            assert options["world_index"] == package.with_name("world.index.json")

        def run(self, config, *, stop):
            assert stop.wait(timeout=5)
            executed.append(config)

    module = ModuleType("usim_isaacsim")
    module.__dict__["IsaacSimulator"] = Native
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "argv", ["native-worker", str(path)])
    monkeypatch.setattr(sys, "stdin", io.StringIO("stop\n"))
    assert isaac.main() == 0
    assert len(executed) == 1
    assert executed[0].world == package.with_name("original.usda")
