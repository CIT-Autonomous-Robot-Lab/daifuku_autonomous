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
"""Copy daifuku's complete SLAM config with Gazebo truth-odometry corrections disabled."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def prepare_slam_profile(source: Path, destination: Path) -> None:
    """Retain all deployed mapping parameters, disabling only redundant pose corrections."""
    config = yaml.safe_load(source.read_text(encoding='utf-8'))
    slam = config['slam_toolbox']['ros__parameters']
    slam['use_scan_matching'] = False
    slam['do_loop_closing'] = False
    destination.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    prepare_slam_profile(args.source, args.destination)


if __name__ == '__main__':
    main()
