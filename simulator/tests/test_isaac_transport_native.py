# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Map-sized DDS payloads and service replies must cross real ROS processes."""
from __future__ import annotations

from array import array
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread, Timer

import pytest

pytest.importorskip("rclpy", reason="requires native ROS message libraries")

import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.task import Future
from std_srvs.srv import SetBool

from daifuku_sim.tsukuba2025.isaac import transport_profiles

SIDE = 5000


def map_qos() -> QoSProfile:
    return QoSProfile(
        depth=1, reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
    )


def receive_map(topic: str) -> None:
    rclpy.init()
    node = rclpy.create_node("isaac_large_map_reader")
    try:
        received = Future()
        node.create_subscription(OccupancyGrid, topic, received.set_result, map_qos())
        service = node.create_client(SetBool, topic + "_reply")
        assert service.wait_for_service(timeout_sec=15)
        print("LARGE_MAP_READER_READY", flush=True)
        rclpy.spin_until_future_complete(node, received, timeout_sec=60)
        assert received.done(), "map-sized DDS sample was not received"
        message = received.result()
        assert message.info.width == message.info.height == SIDE
        assert len(message.data) == message.data.count(42) == SIDE * SIDE
        reply = service.call_async(SetBool.Request(data=True))
        rclpy.spin_until_future_complete(node, reply, timeout_sec=15)
        assert reply.done() and reply.result().success
        print(f"LARGE_MAP_RECEIVED {len(message.data)}", flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_generated_profile_carries_large_map_and_service_reply(
    tmp_path: Path,
) -> None:
    # DDS caches profiles process-wide; other ROS tests must not select this participant's profile.
    result = subprocess.run(
        [sys.executable, str(Path(__file__)), "--exchange", str(tmp_path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"LARGE_MAP_RECEIVED {SIDE * SIDE}" in result.stdout


def exchange_map(tmp_path: Path) -> None:
    _, client = transport_profiles(tmp_path, "127.0.0.1", 76)
    os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(client)
    os.environ["FASTDDS_BUILTIN_TRANSPORTS"] = "LARGE_DATA"
    os.environ["ROS_DOMAIN_ID"] = "76"
    os.environ["ROS_LOCALHOST_ONLY"] = "0"
    rclpy.init()
    node = rclpy.create_node("isaac_large_map_writer")

    def reply(request, response):
        response.success = request.data
        return response

    topic = f"/isaac_transport_map_{os.getpid()}"
    node.create_service(SetBool, topic + "_reply", reply)
    publisher = node.create_publisher(OccupancyGrid, topic, map_qos())
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    runner = Thread(target=executor.spin, daemon=True)
    runner.start()
    reader = subprocess.Popen(
        [sys.executable, str(Path(__file__)), topic],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = Timer(30, reader.kill)
        deadline.start()
        try:
            assert reader.stdout is not None
            ready = reader.stdout.readline()
            assert ready.strip() == "LARGE_MAP_READER_READY", ready + reader.stdout.read()
        finally:
            deadline.cancel()
        message = OccupancyGrid()
        message.info.width = message.info.height = SIDE
        message.info.resolution = 0.2
        message.info.origin.orientation.w = 1.0
        message.data = array("b", [42]) * (SIDE * SIDE)
        publisher.publish(message)
        output, _ = reader.communicate(timeout=90)
        assert reader.returncode == 0, output
        assert f"LARGE_MAP_RECEIVED {SIDE * SIDE}" in output
        print(output, end="", flush=True)
    finally:
        if reader.poll() is None:
            reader.kill()
            reader.communicate(timeout=10)
        assert executor.shutdown(timeout_sec=10)
        runner.join(timeout=10)
        assert not runner.is_alive()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    if len(sys.argv) == 3:
        exchange_map(Path(sys.argv[2]))
    else:
        receive_map(sys.argv[1])
