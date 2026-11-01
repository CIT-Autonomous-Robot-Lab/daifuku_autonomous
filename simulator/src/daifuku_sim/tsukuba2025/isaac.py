# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Original USD/RTX execution and explicit Windows/Docker ROS transport."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
from importlib.util import find_spec
from ipaddress import IPv4Address
import json
import math
import os
from pathlib import Path
import sys
from threading import Event, Thread
from typing import TypedDict
import xml.etree.ElementTree as ET

from ..vtc.contract import Json, RobotGeometry
from .robot import MID360_OFFSET


class SourceLayer(TypedDict):
    path: str
    sha256: str


class SourceTransform(TypedDict):
    source_spawn_m: list[float]
    source_yaw_rad: float
    source_index_sha256: str
    source_layers: list[SourceLayer]


class SourceManifest(TypedDict):
    source_stage: str
    source_index: str
    course_sha256: str
    transform: SourceTransform


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_environment(engine: str) -> None:
    if sys.platform != "win32" or engine != "docker":
        raise ValueError("The native Isaac course transport requires Windows and Docker Desktop")
    if find_spec("isaacsim") is None:
        raise ValueError("Run the Isaac course from a Python environment containing Isaac Sim")


def configuration(
    packaged_world: Path, robot: RobotGeometry, course_sha256: str, *, headless: bool,
) -> dict[str, Json]:
    """Use the original composition, not the converted SDF or a simplified replacement."""
    from usim import Ros2Config, SimulationConfig

    manifest: SourceManifest = json.loads(
        packaged_world.with_name("manifest.json").read_text(encoding="utf-8"),
    )
    if manifest["course_sha256"] != course_sha256:
        raise ValueError("Isaac source manifest does not describe the original course")
    stage, index = Path(manifest["source_stage"]), Path(manifest["source_index"])
    if not stage.is_file() or not index.is_file():
        raise ValueError("Isaac requires the exported world's original USD and residency index")
    if _sha256(index) != manifest["transform"]["source_index_sha256"]:
        raise ValueError("Isaac source index changed after the Gazebo world was exported")
    for layer in manifest["transform"]["source_layers"]:
        if _sha256(Path(layer["path"])) != layer["sha256"]:
            raise ValueError(f"Isaac source layer changed after export: {layer['path']}")
    spawn = manifest["transform"]["source_spawn_m"]
    if len(spawn) != 3:
        raise ValueError("Isaac source spawn must contain exactly three coordinates")
    config = SimulationConfig(
        world=stage,
        robot_urdf=Path(robot["urdf"]),
        robot_name="raspicat",
        base_link=robot["base_link"],
        left_joint=robot["left_joint"],
        right_joint=robot["right_joint"],
        wheel_radius=robot["wheel_radius"],
        wheel_separation=robot["wheel_separation"],
        initial_position=(spawn[0], spawn[1], spawn[2]),
        initial_yaw=manifest["transform"]["source_yaw_rad"],
        headless=headless,
        camera_enabled=False,
        lidar_enabled=True,
        lidar_offset=MID360_OFFSET,
        lidar_hz=10,
        lidar_samples=720,
        lidar_min_range=0.1,
        lidar_max_range=70,
        lidar_vertical_angles=tuple(math.radians(-7 + 59 * index / 27) for index in range(28)),
        ros=Ros2Config(points_topic="/livox/lidar", publish_lidar_tf=False),
    )
    fields = asdict(config)
    fields.update(world=str(stage), robot_urdf=str(Path(robot["urdf"]).resolve()))
    return {
        "configuration": fields,
        "backend": {
            "world_index": str(index), "residency_radius": 80.0,
            "lidar_frame": "livox_frame", "lidar_mode": "rtx", "physics_dt": 0.001,
        },
    }


def transport_profiles(
    directory: Path, address: str, domain_id: int,
) -> tuple[Path, Path]:
    """Native uses routed TCP; Docker retains builtin large-data transports and discovery."""
    address = str(IPv4Address(address))
    paths = []
    for role in ("server", "client"):
        root = ET.Element("profiles", xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles")
        if role == "server":
            descriptors = ET.SubElement(root, "transport_descriptors")
            tcp = ET.SubElement(descriptors, "transport_descriptor")
            ET.SubElement(tcp, "transport_id").text = "isaac_tcp_server"
            ET.SubElement(tcp, "type").text = "TCPv4"
            ET.SubElement(ET.SubElement(tcp, "listening_ports"), "port").text = "55100"
            ET.SubElement(tcp, "wan_addr").text = address
        participant = ET.SubElement(
            root, "participant", profile_name="isaac_" + role, is_default_profile="true",
        )
        rtps = ET.SubElement(participant, "rtps")
        if role == "server":
            transports = ET.SubElement(rtps, "userTransports")
            ET.SubElement(transports, "transport_id").text = "isaac_tcp_server"
        ET.SubElement(rtps, "useBuiltinTransports").text = "false" if role == "server" else "true"
        if role == "client":
            peers = ET.SubElement(ET.SubElement(rtps, "builtin"), "initialPeersList")
            # Only peers are explicit: overriding multicast locators loses TCP return locators.
            multicast = ET.SubElement(ET.SubElement(peers, "locator"), "udpv4")
            ET.SubElement(multicast, "address").text = "239.255.0.1"
            ET.SubElement(multicast, "port").text = str(7400 + 250 * domain_id)
            host = ET.SubElement(ET.SubElement(peers, "locator"), "tcpv4")
            ET.SubElement(host, "address").text = address
            ET.SubElement(host, "physical_port").text = "55100"
        path = directory / f"isaac-{role}.xml"
        ET.indent(root)
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        paths.append(path)
    return paths[0], paths[1]


def main() -> int:
    """Keep stdin cancellation outside the SDK worker, whose stdin stays DEVNULL."""
    from usim import Ros2Config, SimulationConfig
    from usim_isaacsim import IsaacSimulator

    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    os.environ.pop("FASTDDS_BUILTIN_TRANSPORTS", None)
    fields = data["configuration"]
    fields["world"], fields["robot_urdf"] = Path(fields["world"]), Path(fields["robot_urdf"])
    fields["ros"] = Ros2Config(**fields["ros"])
    backend = data["backend"]
    backend["world_index"] = Path(backend["world_index"])
    stop = Event()

    def read_stop() -> None:
        while not stop.is_set():
            line = sys.stdin.readline()
            if not line or line.strip() == "stop":
                stop.set()
                return

    Thread(target=read_stop, daemon=True).start()
    IsaacSimulator(**backend).run(SimulationConfig(**fields), stop=stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
