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
# 2. Run: uv run tools/maps/verify_usda.py <map.yaml> <world.usda> free
# 3. Or: chmod +x tools/maps/verify_usda.py && ./tools/maps/verify_usda.py [ARGS]
# ------------------
"""map_to_usd が吐いた .usda を地図グリッドへ焼き戻し、元の占有と一致するか検算する。

確かめているのは主に **画像行と世界座標の y 反転** で、ここが逆でも USD は正しく
生成されたように見え、Isaac 上でも壁は立つ。壊れるのは「地図と環境が一致している」
という前提だけなので、目視では絶対に気づけない。

    uv run tools/maps/verify_usda.py src/daifuku_stack/maps/19f/map_19f.yaml /tmp/world.usda free

終了コード: 0 = 一致、1 = 不一致。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.maps.map_to_usd import load_occupancy  # noqa: E402


def main():
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    map_yaml, usda_path, unknown = sys.argv[1], sys.argv[2], sys.argv[3]

    occ, meta = load_occupancy(map_yaml, unknown)
    rows, _ = occ.shape
    res = float(meta["resolution"])
    ox, oy = float(meta["origin"][0]), float(meta["origin"][1])

    txt = open(usda_path, encoding="utf-8").read()
    # 4 スペース決め打ちの正規表現は使わない。壁は Walls Xform の中で深くネスト
    # しているので、インデントを当てにすると 1 個しか拾えない。
    chunks = txt.split('def Cube "Wall_')[1:]
    print("walls parsed:", len(chunks))

    recon = np.zeros_like(occ, dtype=bool)
    for b in chunks:
        t = re.search(r"xformOp:translate = \(([-\d.]+), ([-\d.]+), ([-\d.]+)\)", b)
        s = re.search(r"xformOp:scale = \(([-\d.]+), ([-\d.]+), ([-\d.]+)\)", b)
        if not t or not s:
            print("*** could not parse a Wall prim", file=sys.stderr)
            return 1
        cx, cy, cz = map(float, t.groups())
        sx, sy, sz = map(float, s.groups())
        c0 = int(round((cx - sx / 2 - ox) / res))
        c1 = int(round((cx + sx / 2 - ox) / res))
        # 画像の行は y が**降順**。ここを取り違えても壁の数と総面積は合うので、
        # 上下反転した環境で「一致している」と読んでしまう。
        r1 = int(round(rows - (cy - sy / 2 - oy) / res))
        r0 = int(round(rows - (cy + sy / 2 - oy) / res))
        recon[r0:r1, c0:c1] = True
        assert abs(cz - sz / 2) < 1e-9, "wall must sit on the floor"

    print("original occupied :", int(occ.sum()))
    print("reconstructed     :", int(recon.sum()))
    print("missing (in map, not in USD):", int((occ & ~recon).sum()))
    print("extra   (in USD, not in map):", int((~occ & recon).sum()))

    if np.array_equal(occ, recon):
        print("MATCH")
        return 0
    print("*** MISMATCH ***", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
