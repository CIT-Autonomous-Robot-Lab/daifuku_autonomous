#!/usr/bin/env python3
"""地図の yaml と pgm を nav2 と同じ規則で読み、未観測が未観測のままかを検算する。

`map_saver_cli` が書く `free_thresh: 0.25` は、同じ `map_saver_cli` が未観測に使う
画素 205 (p=(255-205)/255=0.196) を **free 側に落とす**。そうなると VI の
`unknown_as_obstacle` も costmap の `track_unknown_space` も、未観測セルが存在しない
ことになるので**エラーも警告も出ないまま効かない**。2026-08-09 まで `map_19f.yaml` が
これで、free セルが 105,618 のところ 518,809 として解かれていた
(`simulator/docs/pi4_sim.md` の「1.」)。地図を取り直すたびに戻るので検算する。

    cd simulator
    uv run python tests/verify_map_thresholds.py ../src/daifuku_stack/maps/*.yaml

終了コード: 0 = 全部 OK、1 = どれかで未観測が free に化けている。
"""

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
