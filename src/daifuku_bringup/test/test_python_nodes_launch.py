# Copyright 2026 Keita Sekiguchi / nop
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

"""インストール済みラッパーから実ノードを起動し、ROS の応答まで確認する。"""

import unittest

from launch import LaunchDescription
from launch.actions import SetEnvironmentVariable
from launch_ros.actions import Node
import launch_testing
import launch_testing.actions
import launch_testing.asserts
import pytest
import rclpy
from rcl_interfaces.srv import GetParameters


NODES = (
    ("daifuku_perception", "elevation_filter.py", "qa_elevation"),
    ("daifuku_perception", "restamp_scan.py", "qa_restamp"),
    ("daifuku_localization", "localization_gravity.py", "qa_gravity"),
    ("daifuku_bringup", "system_monitor.py", "qa_monitor"),
    ("daifuku_robot", "prepare_mid360_imu.py", "qa_imu"),
)


@pytest.mark.launch_test
def generate_test_description():
    processes = [
        Node(package=package, executable=executable, name=name, output="screen",
             parameters=[{"use_sim_time": True}])
        for package, executable, name in NODES
    ]
    return LaunchDescription([
        SetEnvironmentVariable("ROS_DOMAIN_ID", "77"),
        SetEnvironmentVariable("ROS_LOCALHOST_ONLY", "1"),
        *processes,
        launch_testing.actions.ReadyToTest(),
    ]), {"processes": processes}


class TestInstalledPythonNodes(unittest.TestCase):

    def test_each_node_answers_its_parameter_service(self):
        # Given: the real executables, isolated from any physical robot.
        rclpy.init()
        node = rclpy.create_node("installed_python_nodes_test")
        try:
            for _package, _executable, name in NODES:
                client = node.create_client(GetParameters, f"/{name}/get_parameters")
                self.assertTrue(client.wait_for_service(timeout_sec=15), name)
                request = GetParameters.Request()
                request.names = ["use_sim_time"]
                # When: a request reaches the running node, not a source-file import.
                future = client.call_async(request)
                rclpy.spin_until_future_complete(node, future, timeout_sec=15)
                self.assertTrue(future.done(), name)
                # Then: the installed wrapper preserved the launch parameter.
                self.assertTrue(future.result().values[0].bool_value, name)
                node.destroy_client(client)
        finally:
            node.destroy_node()
            rclpy.shutdown()


@launch_testing.post_shutdown_test()
class TestPythonNodesExit(unittest.TestCase):

    def test_all_nodes_exit_cleanly(self, proc_info, processes):
        for process in processes:
            launch_testing.asserts.assertExitCodes(proc_info, [0, -2, -15], process=process)
