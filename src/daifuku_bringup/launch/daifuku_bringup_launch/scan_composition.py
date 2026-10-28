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

"""共通のスキャン生成部品へ渡す入力と、自己位置推定用の独立した接続。"""

from launch.substitutions import LaunchConfiguration

from .navigation_args import SCAN_PARAMETER_ARGUMENTS


def navigation_scan_arguments() -> dict[str, str | LaunchConfiguration]:
    """走行用スキャンは公開引数をそのまま受け取る。"""
    names = SCAN_PARAMETER_ARGUMENTS + (
        "use_sim_time", "overrides", "extra_params_file", "lidar", "lidar_driver",
        "elevation_filter", "restamp_cloud", "scan_filter_enabled",
    )
    return {name: LaunchConfiguration(name) for name in names}


def localization_scan_arguments() -> dict[str, str | LaunchConfiguration]:
    """別の帯で投影し、走行用の中間トピックや時刻補正と混ざらないようにする。"""
    profile = LaunchConfiguration("localization_scan_params_file")
    return navigation_scan_arguments() | {
        "mid360_elevation_params_file": profile,
        "mid360_scan_params_file": profile,
        "scan_filter_params_file": profile,
        "elevation_filter_name": "localization_elevation_filter",
        "projection_name": "pointcloud_to_laserscan_loc",
        "scan_filter_name": "scan_filter_loc",
        "filtered_cloud_topic": "/livox/lidar_localization",
        "scan_raw_topic": "/scan_loc_raw",
        "scan_topic": "/scan_loc",
        "scan_prestamp_topic": "/scan_loc_mid360",
        "restamp_scan": "false",
        "base_frame": "localization_gravity",
    }
