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
"""自己位置推定だけを起動し、VI の TF は計画側に任せる。"""

import os

from daifuku_config import params, value
from launch import LaunchContext, LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration as LC
from launch_ros.actions import Node, PushRosNamespace
from launch_ros.descriptions import ParameterValue


def localization_nodes(context: LaunchContext):
    """選択した推定器と、その推定器だけが読む地図を返す。"""
    selected = value(context, "localization")
    if selected not in ("amcl", "emcl", "emcl2", "emcl3d", "vi"):
        raise RuntimeError(f"Unsupported localization: {selected}")
    # VI は計画側の同じ 1 ノードが map->odom を持つ。
    if selected == "vi":
        return []
    sim = {"use_sim_time": ParameterValue(LC("use_sim_time"), value_type=bool)}
    tf = [("/tf", "tf"), ("/tf_static", "tf_static")]
    common = {"output": "screen", "respawn": LC("use_respawn"), "respawn_delay": 2.0,
              "arguments": ["--ros-args", "--log-level", LC("log_level")]}
    actions = []
    managed = []
    if selected != "emcl3d":
        actions.append(Node(
            package="nav2_map_server", executable="map_server", name="map_server_loc",
            parameters=[sim, {"yaml_filename": LC("map_loc")}],
            remappings=tf + [("map", "map_loc")], **common,
        ))
        managed.append("map_server_loc")
    if selected == "amcl":
        actions.append(Node(
            package="nav2_amcl", executable="amcl", name="amcl",
            parameters=[LC("amcl_params_file"), sim],
            remappings=tf + [("map", "map_loc"), ("scan", LC("localization_scan_topic"))],
            **common,
        ))
        managed.append("amcl")
    elif selected in ("emcl", "emcl2"):
        actions.append(Node(
            package=LC("emcl2_package"), executable=LC("emcl2_executable"),
            name=LC("emcl2_node_name"), parameters=[LC("emcl2_params_file"), sim],
            remappings=tf + [("map", "map_loc"), ("particlecloud", "particle_cloud"),
                             ("global_localization", "reinitialize_global_localization"),
                             ("scan", LC("localization_scan_topic"))], **common,
        ))
    else:
        actions.append(Node(
            package="emcl3d", executable="emcl3d_node", name="emcl3d",
            parameters=[LC("emcl3d_params_file"), sim, {"map_path": LC("map_pcd")}],
            remappings=tf + [("particlecloud", "particle_cloud")], **common,
        ))
    if managed:
        actions.append(Node(
            package="nav2_lifecycle_manager", executable="lifecycle_manager",
            name="lifecycle_manager_localization", output="screen",
            parameters=[sim, {"autostart": LC("autostart"), "node_names": managed}],
        ))
    return actions


def generate_launch_description():
    root = params.config_root("daifuku_localization")
    return LaunchDescription([
        DeclareLaunchArgument("localization", default_value="emcl2"),
        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("use_namespace", default_value="false"),
        DeclareLaunchArgument("map_loc", default_value=""),
        DeclareLaunchArgument("map_pcd", default_value=""),
        DeclareLaunchArgument("localization_scan_topic", default_value="scan"),
        DeclareLaunchArgument("amcl_params_file", default_value=os.path.join(root, "amcl.yaml")),
        DeclareLaunchArgument("emcl2_params_file", default_value=os.path.join(root, "emcl2.yaml")),
        DeclareLaunchArgument(
            "emcl3d_params_file", default_value=os.path.join(root, "emcl3d.yaml")),
        DeclareLaunchArgument("emcl2_package", default_value="emcl2"),
        DeclareLaunchArgument("emcl2_executable", default_value="emcl2_node"),
        DeclareLaunchArgument("emcl2_node_name", default_value="emcl2"),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("autostart", default_value="true"),
        DeclareLaunchArgument("use_respawn", default_value="false"),
        DeclareLaunchArgument("log_level", default_value="info"),
        DeclareLaunchArgument("leveling", default_value="false"),
        *params.declare_args(),
        OpaqueFunction(function=params.compose, kwargs={
            "package": "daifuku_localization", "config_root": root,
            "targets": ["amcl_params_file", "emcl2_params_file", "emcl3d_params_file"],
        }),
        GroupAction([
            PushRosNamespace(LC("namespace"), condition=IfCondition(LC("use_namespace"))),
            Node(package="daifuku_localization", executable="localization_gravity.py",
                 name="localization_gravity_publisher", condition=IfCondition(LC("leveling")),
                 output="screen", parameters=[{
                     "use_sim_time": ParameterValue(LC("use_sim_time"), value_type=bool),
                 }]),
            OpaqueFunction(function=localization_nodes),
        ]),
    ])
