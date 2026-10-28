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

"""navigation の公開引数と、場所ごとに異なる既定値。"""

import os

from ament_index_python.packages import get_package_share_directory
from daifuku_config import env_default, params
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PythonExpression


NAVIGATION_PARAMETER_ARGUMENTS = ("params_file", "bond_params_file")
LOCALIZATION_PARAMETER_ARGUMENTS = (
    "amcl_params_file", "emcl2_params_file", "emcl3d_params_file",
)
SCAN_PARAMETER_ARGUMENTS = (
    "mid360_elevation_params_file", "mid360_scan_params_file", "scan_filter_params_file",
)


def uses_separate_localization_scan(site: str | None) -> bool:
    """走行用と自己位置推定用でスキャンを分ける場所。"""
    return site in ("tsukuba2025", "tsukuba2026")


def declare_navigation_arguments(site: str | None) -> list[DeclareLaunchArgument]:
    """参照先の引数を先に宣言し、時刻・ドライバの依存は実行時に評価する。"""
    nav = params.config_root("daifuku_navigation")
    loc = params.config_root("daifuku_localization")
    perception = params.config_root("daifuku_perception")
    share = get_package_share_directory("daifuku_bringup")
    config = get_package_share_directory("daifuku_config")
    separate_localization_scan = uses_separate_localization_scan(site)

    defaults: dict[str, str | PythonExpression] = {
        "overrides": site or "none",
        "extra_params_file": "",
        "map": "",
        "map_loc": "",
        "map_pcd": "",
        "namespace": "",
        "use_namespace": "false",
        "use_sim_time": "false",
        "autostart": "true",
        "use_respawn": "false",
        "log_level": "info",
        "use_rviz": "false",
        "use_system_monitor": "false" if separate_localization_scan else "true",
        "rviz_config": os.path.join(share, "rviz", "navigation.rviz"),

        "params_file": "",
        "params_dir": os.path.join(nav, "nav2"),
        "bond_params_file": os.path.join(nav, "lifecycle_bond.yaml"),
        "amcl_params_file": os.path.join(loc, "amcl.yaml"),
        "emcl2_params_file": os.path.join(loc, "emcl2.yaml"),
        "emcl3d_params_file": os.path.join(loc, "emcl3d.yaml"),
        "localization": "emcl2",
        "emcl2_package": "emcl2",
        "emcl2_executable": "emcl2_node",
        "emcl2_node_name": "emcl2",
        "velocity_smoother": "true",
        "leveling": "true" if separate_localization_scan else "false",

        "lidar": "mid360" if separate_localization_scan else env_default("LIDAR", "mid360"),
        "lidar_driver": env_default("LIDAR_DRIVER", "true"),
        "elevation_filter": "true",
        "restamp_cloud": "false",
        "scan_filter_enabled": "true",
        "mid360_elevation_params_file": os.path.join(perception, "mid360_elevation.yaml"),
        "mid360_scan_params_file": os.path.join(perception, "mid360_scan.yaml"),
        "scan_filter_params_file": os.path.join(perception, "scan_filter.yaml"),
    }
    if separate_localization_scan and site is not None:
        directory = os.path.join(config, "sites", site)
        for name in SCAN_PARAMETER_ARGUMENTS:
            defaults[name] = os.path.join(directory, "navigation_scan.yaml")
        defaults["localization_scan_params_file"] = os.path.join(
            directory, "localization_scan.yaml",
        )
        defaults["lidar_driver"] = PythonExpression([
            "'false' if '", LaunchConfiguration("use_sim_time"),
            "'.lower() in ('true', '1') else '", env_default("LIDAR_DRIVER", "true"), "'",
        ])
        defaults["restamp_cloud"] = PythonExpression([
            "'", LaunchConfiguration("lidar_driver"), "'.lower() in ('true', '1') and '",
            LaunchConfiguration("use_sim_time"), "'.lower() not in ('true', '1')",
        ])
    return [
        DeclareLaunchArgument(name, default_value=default)
        for name, default in defaults.items()
    ]
