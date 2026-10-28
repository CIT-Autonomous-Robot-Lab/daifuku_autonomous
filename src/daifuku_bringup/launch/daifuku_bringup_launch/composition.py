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
"""場所を固定する入口と、機能ごとの隔離された include。"""

import glob
import os

from ament_index_python.packages import get_package_share_directory
from daifuku_config import params, value
from daifuku_navigation_launch import nav2_params
from launch import LaunchContext, LaunchDescription
from launch.actions import (
    GroupAction, IncludeLaunchDescription, OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.actions import SetParametersFromFile
from launch_ros.descriptions import ParameterValue

from .navigation_args import (
    LOCALIZATION_PARAMETER_ARGUMENTS,
    NAVIGATION_PARAMETER_ARGUMENTS,
    SCAN_PARAMETER_ARGUMENTS,
    declare_navigation_arguments,
    uses_separate_localization_scan,
)
from .scan_composition import localization_scan_arguments, navigation_scan_arguments


def include_component(package, launch_file, arguments):
    """include の引数と remap を他の機能へ漏らさない。"""
    return GroupAction([
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(
                get_package_share_directory(package), "launch", launch_file,
            )),
            launch_arguments=arguments.items(),
        ),
    ])


def validate_site(context: LaunchContext, *, site: str | None):
    """場所別入口で別の場所の override を混ぜたら停止する。"""
    selected = value(context, "overrides").strip()
    if site is not None and selected != site:
        raise RuntimeError(
            f"This launch fixes site={site}; overrides:={selected} is not allowed."
        )
    if site is None and selected.lower() != "none":
        raise RuntimeError("navigation.vtc.launch.py requires overrides:=none.")
    # extra_params_file でも地図を別サイトへ差し替えさせない。
    if site is not None:
        metadata = params.site_meta(context)
        fixed = LaunchContext()
        fixed.launch_configurations.update(context.launch_configurations)
        fixed.launch_configurations["extra_params_file"] = ""
        if metadata != params.site_meta(fixed):
            raise RuntimeError("extra_params_file must not change fixed-site metadata.")
    return []


def _source_parameter_files(
    context: LaunchContext, navigation_root: str, *, separate_localization_scan: bool,
) -> list[str]:
    """合成が引数を書き換える前に、監視する元ファイルを確定する。"""
    names = (
        NAVIGATION_PARAMETER_ARGUMENTS + LOCALIZATION_PARAMETER_ARGUMENTS
        + SCAN_PARAMETER_ARGUMENTS
    )
    files = [value(context, name) for name in names
             if value(context, name)]
    files += sorted(glob.glob(os.path.join(value(context, "params_dir"), "*.yaml")))
    files.append(os.path.join(navigation_root, "vi_planner.yaml"))
    if separate_localization_scan:
        files.append(value(context, "localization_scan_params_file"))
    return files


def _assemble_navigation(context: LaunchContext, *, site: str | None):
    """場所の検査、設定合成、地図解決、監視、機能接続の順に実行する。"""
    validate_site(context, site=site)
    separate_localization_scan = uses_separate_localization_scan(site)
    navigation_root = params.config_root("daifuku_navigation")
    localization_root = params.config_root("daifuku_localization")
    source_files = _source_parameter_files(
        context, navigation_root, separate_localization_scan=separate_localization_scan,
    )

    common_names = [
        "namespace", "use_namespace", "use_sim_time", "autostart",
        "use_respawn", "log_level", "overrides", "extra_params_file",
        "localization", "emcl2_package",
    ]
    common: dict[str, str | LaunchConfiguration] = {
        name: LaunchConfiguration(name) for name in common_names
    }
    navigation = common | {name: LaunchConfiguration(name) for name in [
        "map", "params_file", "params_dir", "bond_params_file", "velocity_smoother",
    ]}
    localization_names = LOCALIZATION_PARAMETER_ARGUMENTS + (
        "map_loc", "map_pcd", "emcl2_executable", "emcl2_node_name", "leveling",
    )
    localization = common | {
        name: LaunchConfiguration(name) for name in localization_names
    }
    localization["localization_scan_topic"] = (
        "/scan_loc" if separate_localization_scan else "/scan"
    )
    scan_pipelines = [
        include_component(
            "daifuku_perception", "scan_pipeline.launch.py", navigation_scan_arguments(),
        ),
    ]
    if separate_localization_scan:
        scan_pipelines.append(include_component(
            "daifuku_perception", "scan_pipeline.launch.py", localization_scan_arguments(),
        ))

    # 合成をここで呼ばず Action として並べ、後続の解決と include に更新済み引数を渡す。
    return [
        OpaqueFunction(function=params.compose, kwargs={
            "package": "daifuku_navigation", "config_root": navigation_root,
            "targets": list(NAVIGATION_PARAMETER_ARGUMENTS),
            "base_resolvers": {"params_file": nav2_params.fragments_resolver},
        }),
        OpaqueFunction(function=params.compose, kwargs={
            "package": "daifuku_localization", "config_root": localization_root,
            "targets": list(LOCALIZATION_PARAMETER_ARGUMENTS),
        }),
        OpaqueFunction(function=nav2_params.resolve_map),
        OpaqueFunction(function=params.sentinel_actions, kwargs={
            "package": "daifuku_navigation", "config_root": navigation_root,
            "packages": ["daifuku_perception", "daifuku_localization", "daifuku_navigation"],
            "files": source_files, "watch_site": False,
        }),
        *scan_pipelines,
        GroupAction([
            SetParametersFromFile(LaunchConfiguration("bond_params_file")),
            include_component("daifuku_localization", "localization.launch.py", localization),
        ]),
        include_component("daifuku_navigation", "navigation.launch.py", navigation),
        Node(package="daifuku_bringup", executable="system_monitor.py", name="system_monitor",
             condition=IfCondition(LaunchConfiguration("use_system_monitor")),
             namespace=LaunchConfiguration("namespace"),
             output="screen", parameters=[{
                 "use_sim_time": ParameterValue(
                     LaunchConfiguration("use_sim_time"), value_type=bool,
                 ),
             }]),
        Node(package="rviz2", executable="rviz2", name="rviz2",
             namespace=LaunchConfiguration("namespace"),
             condition=IfCondition(LaunchConfiguration("use_rviz")), output="screen",
             arguments=["-d", LaunchConfiguration("rviz_config")], parameters=[{
                 "use_sim_time": ParameterValue(
                     LaunchConfiguration("use_sim_time"), value_type=bool,
                 ),
             }]),
    ]


def generate_navigation(site: str | None) -> LaunchDescription:
    """指定した場所、または明示した外部地図だけを起動する。"""
    return LaunchDescription([
        *declare_navigation_arguments(site),
        params.declare_watch_arg(),
        OpaqueFunction(function=_assemble_navigation, kwargs={"site": site}),
    ])
