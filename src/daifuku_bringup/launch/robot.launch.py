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
"""場所を選ばず機体だけを起動する公開入口。"""

import os
import sys

from daifuku_config import params, value
from launch import LaunchContext, LaunchDescription
from launch.actions import OpaqueFunction

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from daifuku_bringup_launch.composition import include_component  # noqa: E402


def _robot(context: LaunchContext):
    """機体の入力だけを見張り、機能 launch に見張りを重ねない。"""
    if value(context, "overrides").strip().lower() != "none":
        raise RuntimeError("robot.launch.py requires overrides:=none; the robot has no site.")
    root = params.config_root("daifuku_robot")
    names = [
        "params_file", "twist_mux_params_file", "joy_teleop_params_file",
        "urg_params_file", "mid360_ekf_params_file", "mid360_config",
    ]
    files = [context.launch_configurations[name] for name in names
             if context.launch_configurations.get(name)]
    if not context.launch_configurations.get("mid360_config"):
        files.append(os.path.join(root, "sensors", "MID360_config.json"))
    return [
        *params.sentinel_actions(
            context, package="daifuku_robot", config_root=root,
            packages=["daifuku_robot"], files=files, watch_site=False,
        ),
        include_component("daifuku_robot", "robot_bringup.launch.py", {}),
    ]


def generate_launch_description():
    return LaunchDescription([
        *params.declare_args(default_overrides="none"),
        params.declare_watch_arg(),
        OpaqueFunction(function=_robot),
    ])
