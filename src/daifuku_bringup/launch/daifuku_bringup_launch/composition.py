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
from daifuku_config import env_default, params, value
from daifuku_navigation_launch import nav2_params
from launch import LaunchContext, LaunchDescription
from launch.actions import (
    DeclareLaunchArgument, GroupAction, IncludeLaunchDescription, OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration as LC, PythonExpression
from launch_ros.actions import Node
from launch_ros.actions import SetParametersFromFile
from launch_ros.descriptions import ParameterValue


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


def _prepare(context: LaunchContext, *, site: str | None):
    """入力ファイルを記録してから、地図・設定・機能を順に組み立てる。"""
    validate_site(context, site=site)
    nav_root = params.config_root("daifuku_navigation")
    loc_root = params.config_root("daifuku_localization")
    nav_files = ["params_file", "bond_params_file"]
    loc_files = ["amcl_params_file", "emcl2_params_file", "emcl3d_params_file"]
    scan_files = ["mid360_elevation_params_file", "mid360_scan_params_file",
                  "scan_filter_params_file"]
    files = [value(context, name) for name in nav_files + loc_files + scan_files
             if value(context, name)]
    files += sorted(glob.glob(os.path.join(value(context, "params_dir"), "*.yaml")))
    files.append(os.path.join(nav_root, "vi_planner.yaml"))
    dual = site in ("tsukuba2025", "tsukuba2026")
    if dual:
        files.append(value(context, "localization_scan_params_file"))
    common_names = [
        "namespace", "use_namespace", "use_sim_time", "autostart",
        "use_respawn", "log_level", "overrides", "extra_params_file",
        "localization", "emcl2_package",
    ]
    common = {name: LC(name) for name in common_names}
    navigation = common | {name: LC(name) for name in [
        "map", "params_file", "params_dir", "bond_params_file", "velocity_smoother",
    ]}
    localization = common | {name: LC(name) for name in loc_files + [
        "map_loc", "map_pcd", "emcl2_executable", "emcl2_node_name", "leveling",
    ]}
    localization["localization_scan_topic"] = "/scan_loc" if dual else "/scan"
    scan = {name: LC(name) for name in scan_files + [
        "use_sim_time", "overrides", "extra_params_file", "lidar", "lidar_driver",
        "elevation_filter", "restamp_cloud", "scan_filter_enabled",
    ]}
    actions = [
        OpaqueFunction(function=params.compose, kwargs={
            "package": "daifuku_navigation", "config_root": nav_root,
            "targets": nav_files,
            "base_resolvers": {"params_file": nav2_params.fragments_resolver},
        }),
        OpaqueFunction(function=params.compose, kwargs={
            "package": "daifuku_localization", "config_root": loc_root,
            "targets": loc_files,
        }),
        OpaqueFunction(function=nav2_params.resolve_map),
        OpaqueFunction(function=params.sentinel_actions, kwargs={
            "package": "daifuku_navigation", "config_root": nav_root,
            "packages": ["daifuku_perception", "daifuku_localization", "daifuku_navigation"],
            "files": files, "watch_site": False,
        }),
        include_component("daifuku_perception", "scan_pipeline.launch.py", scan),
    ]
    if dual:
        profile = LC("localization_scan_params_file")
        actions.append(include_component("daifuku_perception", "scan_pipeline.launch.py", scan | {
            "mid360_elevation_params_file": profile, "mid360_scan_params_file": profile,
            "scan_filter_params_file": profile,
            "elevation_filter_name": "localization_elevation_filter",
            "projection_name": "pointcloud_to_laserscan_loc",
            "scan_filter_name": "scan_filter_loc",
            "filtered_cloud_topic": "/livox/lidar_localization",
            "scan_raw_topic": "/scan_loc_raw", "scan_topic": "/scan_loc",
            "scan_prestamp_topic": "/scan_loc_mid360",
            "restamp_scan": "false", "base_frame": "localization_gravity",
        }))
    actions += [
        GroupAction([
            SetParametersFromFile(LC("bond_params_file")),
            include_component("daifuku_localization", "localization.launch.py", localization),
        ]),
        include_component("daifuku_navigation", "navigation.launch.py", navigation),
        Node(package="daifuku_bringup", executable="system_monitor.py", name="system_monitor",
             condition=IfCondition(LC("use_system_monitor")), namespace=LC("namespace"),
             output="screen", parameters=[{
                 "use_sim_time": ParameterValue(LC("use_sim_time"), value_type=bool),
             }]),
        Node(package="rviz2", executable="rviz2", name="rviz2", namespace=LC("namespace"),
             condition=IfCondition(LC("use_rviz")), output="screen",
             arguments=["-d", LC("rviz_config")], parameters=[{
                 "use_sim_time": ParameterValue(LC("use_sim_time"), value_type=bool),
             }]),
    ]
    return actions


def generate_navigation(site: str | None) -> LaunchDescription:
    """指定した場所、または明示した外部地図だけを起動する。"""
    nav = params.config_root("daifuku_navigation")
    loc = params.config_root("daifuku_localization")
    perception = params.config_root("daifuku_perception")
    share = get_package_share_directory("daifuku_bringup")
    config = get_package_share_directory("daifuku_config")
    dual = site in ("tsukuba2025", "tsukuba2026")
    defaults = {
        "overrides": site or "none", "extra_params_file": "", "map": "", "map_loc": "",
        "map_pcd": "", "namespace": "", "use_namespace": "false",
        "use_sim_time": "false", "autostart": "true", "use_respawn": "false",
        "log_level": "info", "use_rviz": "false",
        "use_system_monitor": "false" if dual else "true",
        "rviz_config": os.path.join(share, "rviz", "navigation.rviz"),
        "params_file": "", "params_dir": os.path.join(nav, "nav2"),
        "bond_params_file": os.path.join(nav, "lifecycle_bond.yaml"),
        "amcl_params_file": os.path.join(loc, "amcl.yaml"),
        "emcl2_params_file": os.path.join(loc, "emcl2.yaml"),
        "emcl3d_params_file": os.path.join(loc, "emcl3d.yaml"),
        "localization": "emcl2", "emcl2_package": "emcl2",
        "emcl2_executable": "emcl2_node", "emcl2_node_name": "emcl2",
        "velocity_smoother": "true", "leveling": "true" if dual else "false",
        "lidar": "mid360" if dual else env_default("LIDAR", "mid360"),
        "lidar_driver": env_default("LIDAR_DRIVER", "true"),
        "elevation_filter": "true", "restamp_cloud": "false", "scan_filter_enabled": "true",
        "mid360_elevation_params_file": os.path.join(perception, "mid360_elevation.yaml"),
        "mid360_scan_params_file": os.path.join(perception, "mid360_scan.yaml"),
        "scan_filter_params_file": os.path.join(perception, "scan_filter.yaml"),
    }
    if dual:
        directory = os.path.join(config, "sites", site)
        for name in ("mid360_elevation_params_file", "mid360_scan_params_file",
                     "scan_filter_params_file"):
            defaults[name] = os.path.join(directory, "navigation_scan.yaml")
        defaults["localization_scan_params_file"] = os.path.join(
            directory, "localization_scan.yaml")
        defaults["lidar_driver"] = PythonExpression([
            "'false' if '", LC("use_sim_time"), "'.lower() in ('true', '1') else '",
            env_default("LIDAR_DRIVER", "true"), "'",
        ])
        defaults["restamp_cloud"] = PythonExpression([
            "'", LC("lidar_driver"), "'.lower() in ('true', '1') and '",
            LC("use_sim_time"), "'.lower() not in ('true', '1')",
        ])
    return LaunchDescription([
        *[DeclareLaunchArgument(name, default_value=default)
          for name, default in defaults.items()],
        params.declare_watch_arg(),
        OpaqueFunction(function=_prepare, kwargs={"site": site}),
    ])
