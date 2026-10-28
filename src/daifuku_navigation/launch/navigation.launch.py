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
"""計画用地図と VI だけを起動する内部コンポーネント。"""

import os

from daifuku_config import params
from daifuku_navigation_launch import backends, nav2_params
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration as LC, PythonExpression
from launch_ros.actions import Node, PushRosNamespace, SetParametersFromFile
from launch_ros.descriptions import ParameterFile, ParameterValue
from nav2_common.launch import RewrittenYaml


def generate_launch_description():
    root = params.config_root("daifuku_navigation")
    sim = LC("use_sim_time")
    namespace = LC("namespace")
    vi_localization = PythonExpression(["'", LC("localization"), "' == 'vi'"])
    smooth = LC("velocity_smoother")
    configured = ParameterFile(RewrittenYaml(
        source_file=LC("params_file"), root_key=namespace,
        param_rewrites={"use_sim_time": sim, "autostart": LC("autostart"),
                        "yaml_filename": LC("map")}, convert_types=True,
    ), allow_substs=True)
    tf = [("/tf", "tf"), ("/tf_static", "tf_static")]
    common = {
        "output": "screen", "respawn": LC("use_respawn"), "respawn_delay": 2.0,
        "arguments": ["--ros-args", "--log-level", LC("log_level")],
    }
    return LaunchDescription([
        DeclareLaunchArgument("map"),
        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("use_namespace", default_value="false"),
        DeclareLaunchArgument("params_file", default_value=""),
        DeclareLaunchArgument("params_dir", default_value=os.path.join(root, "nav2")),
        DeclareLaunchArgument("bond_params_file",
                              default_value=os.path.join(root, "lifecycle_bond.yaml")),
        *params.declare_args(),
        DeclareLaunchArgument("localization", default_value="emcl2"),
        DeclareLaunchArgument("emcl2_package", default_value="emcl2"),
        DeclareLaunchArgument("velocity_smoother", default_value="true"),
        DeclareLaunchArgument("scan_topic", default_value="scan"),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("autostart", default_value="true"),
        DeclareLaunchArgument("use_respawn", default_value="false"),
        DeclareLaunchArgument("log_level", default_value="info"),
        OpaqueFunction(function=params.compose, kwargs={
            "package": "daifuku_navigation", "config_root": root,
            "targets": ["params_file", "bond_params_file"],
            "base_resolvers": {"params_file": nav2_params.fragments_resolver},
        }),
        OpaqueFunction(function=backends.validate_localization),
        OpaqueFunction(function=backends.validate_vi_package),
        GroupAction([
            PushRosNamespace(namespace, condition=IfCondition(LC("use_namespace"))),
            SetParametersFromFile(LC("bond_params_file")),
            Node(package="nav2_map_server", executable="map_server", name="map_server",
                 parameters=[configured], remappings=tf, **common),
            Node(package="nav2_lifecycle_manager", executable="lifecycle_manager",
                 name="lifecycle_manager_map", output="screen",
                 parameters=[{"use_sim_time": sim, "autostart": LC("autostart"),
                              "node_names": ["map_server"]}]),
            Node(package="vi_planner", executable="vi_planner", name="vi_planner",
                 parameters=[configured, {
                     "use_sim_time": sim, "standalone": True, "follow": True,
                     "publish_tf": ParameterValue(vi_localization, value_type=bool),
                     "pose_topic": PythonExpression([
                         "'initialpose' if ", vi_localization,
                         " else ('amcl_pose' if '", LC("localization"),
                         "' == 'amcl' else 'mcl_pose')",
                     ]),
                     "scan_topic": LC("scan_topic"),
                 }], remappings=tf + [("cmd_vel", PythonExpression([
                     "'cmd_vel_nav' if '", smooth,
                     "'.lower() in ('true', '1') else 'cmd_vel'",
                 ]))], **common),
            Node(package="nav2_velocity_smoother", executable="velocity_smoother",
                 name="velocity_smoother", condition=IfCondition(smooth),
                 parameters=[configured], remappings=tf + [
                     ("cmd_vel", "cmd_vel_nav"), ("cmd_vel_smoothed", "cmd_vel"),
                 ], **common),
            Node(package="nav2_lifecycle_manager", executable="lifecycle_manager",
                 name="lifecycle_manager_navigation", condition=IfCondition(smooth),
                 output="screen", parameters=[{
                     "use_sim_time": sim, "autostart": LC("autostart"),
                     "node_names": ["velocity_smoother"],
                 }]),
        ]),
    ])
