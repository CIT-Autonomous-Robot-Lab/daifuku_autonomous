#!/usr/bin/env -S uv run --script
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
# dependencies = ["numpy>=1.24", "pillow>=10.0", "pyyaml>=6.0"]
# ///
# --- How to run ---
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run: uv run tools/maps/verify_map_thresholds.py <map.yaml> [...]
# 3. Or: chmod +x tools/maps/verify_map_thresholds.py && ./tools/maps/verify_map_thresholds.py [ARGS]
# ------------------
"""地図の yaml と pgm を nav2 と同じ規則で読み、未観測が未観測のままかを検算する。

`map_saver_cli` が書く `free_thresh: 0.25` は、同じ `map_saver_cli` が未観測に使う
画素 205 (p=(255-205)/255=0.196) を **free 側に落とす**。そうなると VI の
`unknown_as_obstacle` も costmap の `track_unknown_space` も、未観測セルが存在しない
ことになるので**エラーも警告も出ないまま効かない**。2026-08-09 まで `map_19f.yaml` が
これで、free セルが 105,618 のところ 518,809 として解かれていた
(`docs/usage/pi4_sim_history.md` の「1.」)。地図を取り直すたびに戻るので検算する。

    uv run tools/maps/verify_map_thresholds.py src/daifuku_stack/maps/*/*.yaml

終了コード: 0 = 全部 OK、1 = どれかで未観測が free に化けている。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

UNKNOWN_PX = 205        # map_saver が未観測に使う画素値


def check(path):
    meta = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    img = np.array(Image.open(Path(path).parent / meta["image"]))
    p = img / 255.0 if meta.get("negate", 0) else (255 - img) / 255.0
    occupied = p > meta["occupied_thresh"]
    free = p < meta["free_thresh"]
    unknown = ~occupied & ~free

    n = img.size
    print(f"{Path(path).name}: free_thresh={meta['free_thresh']} "
          f"free={free.sum()} ({100 * free.sum() / n:.1f}%) "
          f"unknown={unknown.sum()} occupied={occupied.sum()}")
    leaked = ((img == UNKNOWN_PX) & ~unknown).sum()
    if leaked:
        print(f"  NG: 未観測画素 {UNKNOWN_PX} が {leaked} セル free/occupied に化けている。"
              f" free_thresh を 0.15 へ下げること", file=sys.stderr)
    return leaked == 0


def main():
    paths = sys.argv[1:]
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2
    return 0 if all([check(p) for p in paths]) else 1


if __name__ == "__main__":
    sys.exit(main())
