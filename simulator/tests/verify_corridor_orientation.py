#!/usr/bin/env python3
"""`corridor_map` の回廊が**上下反転していない**ことを検算する。

ROS の `map_server` は画像の行 0 を世界の y の**最大**として読む
(`msg.data[MAP_IDX(w, height - y - 1, x)]`)。`corridor_mask` はワールド座標で
組んでから返す直前に反転しているが、**ここを間違えても回廊は同じ形のまま
上下が入れ替わるだけ**なので、地図は見た目にもっともらしく、順路の点が壁に
乗るという形でしか現れない。おまけに生成側の検算が同じ添字で書かれていると
自分では気づけない (2026-09-02 の `tsudanuma-challenge_nav_corridor` が
これで、66 点中 22 点が壁の中だった)。

    cd simulator
    uv run python tests/verify_corridor_orientation.py

終了コード: 0 = OK、1 = 反転している。
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from daifuku_sim.corridor_map import corridor_mask  # noqa: E402

# 10x10 セル / 1m 角、原点 (0, 0) = 画像の**左下**。
META = {"resolution": 1.0, "origin": [0.0, 0.0, 0.0]}


def main():
    # y=8.5 (上のほう) を通る水平の順路。半径 0.4m なので 1 行しか当たらない。
    mask = corridor_mask((10, 10), META, [[(2.5, 8.5), (7.5, 8.5)]], 0.4, closed=False)
    rows = sorted(set(np.nonzero(mask)[0].tolist()))
    # y=8.5 は下から 9 行目 = 画像の行 10-1-8 = 1。反転を忘れると行 8 に出る。
    assert rows == [1], f"回廊が画像の行 {rows} に出ました (期待 [1] = 上下反転なし)"
    print("OK: corridor_mask の行の向きは map_server と同じ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
