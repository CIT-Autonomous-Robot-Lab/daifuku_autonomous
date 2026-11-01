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
import copy
import math
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest
import rclpy
from rclpy.time import Time
from sensor_msgs.msg import PointCloud2, PointField
from geometry_msgs.msg import TransformStamped
from tf2_ros import Buffer


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


@pytest.mark.parametrize("deferred,superseded", [(False, False), (True, False), (True, True)])
def test_elevation_uses_acquisition_rotation_without_translating_points(
    deferred: bool, superseded: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    specification = importlib.util.spec_from_file_location(
        "tested_tilted_elevation", Path(__file__).resolve().parents[1] / "src/elevation_filter.py",
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    rclpy.init(args=[
        "--ros-args", "-p", "elevation_frame:=gravity", "-p", "min_elevation_deg:=10.0",
    ])
    node = module.ElevationFilter()
    captured: list[PointCloud2] = []
    monkeypatch.setattr(node, "_publisher", SimpleNamespace(publish=captured.append))
    buffer = Buffer()
    monkeypatch.setattr(node, "_tf_buffer", buffer, raising=False)
    transform = TransformStamped()
    transform.header.frame_id = "gravity"
    transform.child_frame_id = "livox_frame"
    transform.header.stamp.sec = 123
    transform.transform.translation.x = 100.0
    transform.transform.translation.z = 100.0
    transform.transform.rotation.y = math.sin(math.radians(10))
    transform.transform.rotation.w = math.cos(math.radians(10))
    newer = copy.deepcopy(transform)
    newer.header.stamp.sec = 124
    if not superseded:
        newer.transform.rotation.y = -newer.transform.rotation.y
    cloud = PointCloud2()
    cloud.header.frame_id = "livox_frame"
    cloud.header.stamp.sec = 123
    cloud.height, cloud.width = 1, 3
    cloud.point_step, cloud.row_step = 12, 36
    cloud.fields = [
        PointField(name=name, offset=offset, datatype=PointField.FLOAT32, count=1)
        for name, offset in (("x", 0), ("y", 4), ("z", 8))
    ]
    records = [
        struct.pack("<fff", 10.0, 0.0, 0.0),
        struct.pack("<fff", 10.0, 0.0, 10 * math.tan(math.radians(20))),
        struct.pack("<fff", 10.0, 0.0, 10 * math.tan(math.radians(40))),
    ]
    cloud.data = b"".join(records)
    try:
        if not deferred:
            buffer.set_transform(transform, "test")
            buffer.set_transform(newer, "test")
        node._callback(cloud)
        if deferred:
            assert captured == []
            if superseded:
                replacement = copy.deepcopy(cloud)
                replacement.header.stamp.sec = 124
                node._callback(replacement)
            buffer.set_transform(transform, "test")
            if superseded:
                assert captured == []
                buffer.set_transform(newer, "test")
        assert len(captured) == 1
        assert captured[0].header.frame_id == "livox_frame"
        assert captured[0].header.stamp.sec == (124 if superseded else 123)
        assert captured[0].width == 1
        assert bytes(captured[0].data) == records[2]
    finally:
        node.destroy_node()
        rclpy.shutdown()
