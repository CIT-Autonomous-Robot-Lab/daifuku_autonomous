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
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# How to run: uv run --project simulator --no-sync python
# simulator/scripts/vtc/stage_assets.py <session.json> <new-output-dir>
"""Retain the actual usim-injected URDF/SDF and mount contract for factory probes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypedDict

from daifuku_sim.vtc.vtc_runtime import simulation_from_session
from daifuku_sim.vtc.vtc_contract import LIDAR, SessionConfig


class ResourceMount(TypedDict):
    source: str
    target: str


def main() -> None:
    """Stage precisely the configuration of a preserved workflow attempt."""
    config: SessionConfig = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    simulation = simulation_from_session(config)
    from usim_gazebo import GazeboLidarConfig
    from usim_gazebo.assets import GazeboAssetConfig, prepare_assets
    output = Path(sys.argv[2])
    mounts = prepare_assets(
        simulation, output, GazeboAssetConfig('/vtc_factory_cmd', GazeboLidarConfig(**LIDAR)),
    )
    resources: list[ResourceMount] = [
        {'source': str(source), 'target': target} for source, target in mounts
    ]
    (output / 'mounts.json').write_text(json.dumps(resources, indent=2), encoding='utf-8')
    print(json.dumps(resources), flush=True)


if __name__ == '__main__':
    main()
