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

"""Check cloud timestamps before downstream TF projection with real ROS messages."""

import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest
import rclpy
from rclpy.time import Time
from sensor_msgs.msg import PointCloud2, PointField


@pytest.mark.parametrize("restamp_cloud", [None, False, True])
def test_cloud_uses_receive_time_only_when_requested(
    restamp_cloud: bool | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    specification = importlib.util.spec_from_file_location(
        "tested_elevation", Path(__file__).resolve().parents[1] / "src/elevation_filter.py",
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    arguments = []
    if restamp_cloud is not None:
        arguments = ["--ros-args", "-p", f"restamp_cloud:={str(restamp_cloud).lower()}"]
    rclpy.init(args=arguments)
    node = module.ElevationFilter()
    captured: list[PointCloud2] = []
    monkeypatch.setattr(node, "_publisher", SimpleNamespace(publish=captured.append))
    monkeypatch.setattr(
        node, "get_clock", lambda: SimpleNamespace(now=lambda: Time(seconds=42)),
    )
    try:
        cloud = PointCloud2()
        cloud.header.frame_id = "livox_frame"
        cloud.header.stamp.sec = 123
        cloud.height, cloud.width = 1, 1
        cloud.point_step = cloud.row_step = 12
        cloud.fields = [
            PointField(name=name, offset=offset, datatype=PointField.FLOAT32, count=1)
            for name, offset in (("x", 0), ("y", 4), ("z", 8))
        ]
        cloud.data = struct.pack("<fff", 1.0, 0.0, 0.5)
        node._callback(cloud)
        assert len(captured) == 1
        assert captured[0].header.stamp.sec == (42 if restamp_cloud else 123)
        assert captured[0].header.frame_id == "livox_frame"
        assert struct.unpack("<fff", captured[0].data) == (1.0, 0.0, 0.5)
    finally:
        node.destroy_node()
        rclpy.shutdown()
