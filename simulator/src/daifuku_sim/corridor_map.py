#!/usr/bin/env python3
"""順路の回りだけを残して、占有格子地図の**自由空間を削る**。

`vi_planner` の solve 時間を決めているのは状態数ではなく**解くべき自由空間の
広さ**です。2026-09-02 の実測 (`src/daifuku_config/README.md`):

    tsudanuma           free 31% (68.2% が未観測 = 障害物扱い)  フル solve 11.83s
    tsudanuma_mugimaru  free 93% (未観測 0.05%)                 フル solve 27.95s

状態数はほぼ同じ (5654 万 / 5932 万) で 2.4 倍の差です。`map_scale` を上げても
状態数の減りを反復の増加が相殺して速くならない (scale 3 で 27.72s) ので、
**残る手は解く面積そのものを減らすこと**。上流も navigation 用の地図に壁を
描き足して同じことをしています。

このスクリプトは、順路 (waypoints の yaml) の各線分から `--radius` [m] 以内を
「回廊」として残し、**その外側の自由セルを占有へ倒します**。元から占有の
セルはそのまま (手描きの壁は消えません)。

    uv run corridor-map nav.yaml out.yaml \\
        --waypoints ../src/daifuku_stack/waypoints/waypoints_*_v1.1.yaml \\
        --radius 5.0

**この出力は順路から導かれるので、順路を変えたら作り直しが要ります。** 新しい
点が回廊の外に出ると、その点は占有セルに乗るので**ゴールが出ないだけ**で、
地図が古いとは誰も言いません。作り直しを忘れないよう、書き出す前に全点が
自由セルに落ちることを確かめ、落ちなければ**書かずに落とします**。

`--waypoints` は複数渡せます。実機の `waypoints_file` は既定が空で、どの版を
使うかが決まっていないため (v1.0 と v1.1 は 1〜4m ずれる)、**両方を渡して
和で回廊を作るのが安全**です。
"""

import argparse
import os

import numpy as np
import yaml


def read_pgm(path):
    """P5 (バイナリ PGM) を (w, h, bytes) で読む。downsample_map.py と同じ規約。"""
    with open(path, "rb") as f:
        data = f.read()
    tokens, i = [], 0
    while len(tokens) < 4:
        while data[i : i + 1].isspace():
            i += 1
        if data[i : i + 1] == b"#":
            while data[i] != 0x0A:
                i += 1
            continue
        j = i
        while not data[j : j + 1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j
    i += 1
    if tokens[0] != b"P5":
        raise SystemExit(f"only binary PGM (P5) supported: {path}")
    w, h = int(tokens[1]), int(tokens[2])
    return w, h, np.frombuffer(data[i : i + w * h], dtype=np.uint8).reshape(h, w)


def write_pgm(path, img):
    with open(path, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (img.shape[1], img.shape[0]))
        f.write(img.tobytes())


def load_waypoints(paths):
    """順路の yaml から (x, y) の並びを読む。複数ファイルはそれぞれ別の順路。"""
    tours = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        pts = [(w["position"]["x"], w["position"]["y"]) for w in doc["waypoints"]]
        if len(pts) < 2:
            raise SystemExit(f"{p}: 順路の点が 2 つ未満です")
        tours.append(pts)
    return tours


def corridor_mask(shape, meta, tours, radius_m, closed):
    """順路の線分から radius_m 以内のセルを True にした配列を返す。

    セル中心のワールド座標と線分との距離で判定する。**y は上下反転する** —
    ROS の map_server は画像の行 0 を世界の y=origin_y として読むため
    (`docs` と downsample_map.py と同じ規約)。
    """
    h, w = shape
    res = meta["resolution"]
    ox, oy = meta["origin"][0], meta["origin"][1]
    mask = np.zeros((h, w), dtype=bool)
    r2 = radius_m * radius_m
    pad = int(np.ceil(radius_m / res)) + 1

    for pts in tours:
        segs = list(zip(pts, pts[1:]))
        if closed:
            segs.append((pts[-1], pts[0]))
        for (x0, y0), (x1, y1) in segs:
            # 線分のバウンディングボックス + 半径ぶんだけを走査する
            # (地図全体で距離を取ると 250 万セル x 66 線分になる)。
            ix0 = int(np.floor((min(x0, x1) - ox) / res)) - pad
            ix1 = int(np.ceil((max(x0, x1) - ox) / res)) + pad
            iy0 = int(np.floor((min(y0, y1) - oy) / res)) - pad
            iy1 = int(np.ceil((max(y0, y1) - oy) / res)) + pad
            ix0, iy0 = max(ix0, 0), max(iy0, 0)
            ix1, iy1 = min(ix1, w - 1), min(iy1, h - 1)
            if ix0 > ix1 or iy0 > iy1:
                continue

            gx, gy = np.meshgrid(
                (np.arange(ix0, ix1 + 1) + 0.5) * res + ox,
                (np.arange(iy0, iy1 + 1) + 0.5) * res + oy,
            )
            dx, dy = x1 - x0, y1 - y0
            seg2 = dx * dx + dy * dy
            if seg2 == 0.0:
                t = np.zeros_like(gx)
            else:
                t = np.clip(((gx - x0) * dx + (gy - y0) * dy) / seg2, 0.0, 1.0)
            px, py = x0 + t * dx, y0 + t * dy
            near = (gx - px) ** 2 + (gy - py) ** 2 <= r2
            # 行 0 = y=origin_y なので、そのまま iy が y の増える向き。
            mask[iy0 : iy1 + 1, ix0 : ix1 + 1] |= near
    return mask


def classify(img, meta):
    """(occupied, free) の真偽配列。map_server と同じしきい値の読み方。"""
    p = img.astype(np.float64) / 255.0
    occ_p = p if meta.get("negate", 0) else 1.0 - p
    return occ_p >= meta["occupied_thresh"], occ_p <= meta["free_thresh"]


def main():
    ap = argparse.ArgumentParser(
        description="順路の回りだけを残して占有格子地図の自由空間を削る"
    )
    ap.add_argument("map_in", help="入力の地図 yaml")
    ap.add_argument("map_out", help="出力の地図 yaml (pgm は同じ名前で隣に書く)")
    ap.add_argument(
        "--waypoints", nargs="+", required=True,
        help="順路の yaml。複数渡すとそれぞれの回廊の和を残す",
    )
    ap.add_argument(
        "--radius", type=float, default=5.0,
        help="回廊の半径 [m] (既定 5.0 = 幅 10m)。狭めるほど solve は速くなるが、"
             "機体がここから出ると start が占有セルになり方策が引けない",
    )
    ap.add_argument(
        "--open", action="store_true",
        help="順路を閉路として扱わない (既定は最後の点から最初の点へも回廊を作る)",
    )
    args = ap.parse_args()

    with open(args.map_in, encoding="utf-8") as f:
        meta = yaml.safe_load(f)
    src_pgm = os.path.join(os.path.dirname(os.path.abspath(args.map_in)), meta["image"])
    w, h, img = read_pgm(src_pgm)
    occ, free = classify(img, meta)

    tours = load_waypoints(args.waypoints)
    mask = corridor_mask((h, w), meta, tours, args.radius, closed=not args.open)

    out = img.copy()
    # 回廊の外の「占有でないもの」を占有へ倒す。元から占有のセルは触らない。
    # 未観測も倒す (どのみち通行不可の扱いなので、値が揃うぶん読みやすい)。
    kill = (~mask) & (~occ)
    out[kill] = 0 if not meta.get("negate", 0) else 255

    out_occ, out_free = classify(out, meta)
    res = meta["resolution"]
    print(
        "corridor r=%.1fm: free %d -> %d cells (%.1f%% -> %.1f%%, %.0f -> %.0f m2)"
        % (
            args.radius,
            free.sum(), out_free.sum(),
            100.0 * free.sum() / img.size, 100.0 * out_free.sum() / img.size,
            free.sum() * res * res, out_free.sum() * res * res,
        )
    )

    # **書く前に、元の地図で自由だった点が出力でも自由であることを確かめる。**
    # ここが通らない地図は「ゴールが出ない」としてしか現れないので生成時点で止める。
    # 元から占有だった点は**この加工のせいではない**ので、告げるだけで続ける
    # (2026-09-02 の時点で mugimaru の nav 地図には 3 点そういう点がある)。
    ox, oy = meta["origin"][0], meta["origin"][1]
    lost, already = [], []
    for path, pts in zip(args.waypoints, tours):
        for k, (x, y) in enumerate(pts):
            ix, iy = int((x - ox) / res), int((y - oy) / res)
            where = f"{os.path.basename(path)}[{k}] ({x:.2f}, {y:.2f})"
            if not (0 <= ix < w and 0 <= iy < h):
                lost.append(where + " — 地図の外")
            elif not free[iy, ix]:
                already.append(where)
            elif not out_free[iy, ix]:
                lost.append(where)
    for where in already:
        print(f"WARN: 元の地図で既に自由セルではありません: {where}")
    if lost:
        raise SystemExit(
            "この加工で順路の点が自由セルでなくなりました (%d 点):\n  %s\n"
            "--radius を広げてください。" % (len(lost), "\n  ".join(lost[:10]))
        )

    out_dir = os.path.dirname(os.path.abspath(args.map_out))
    stem = os.path.splitext(os.path.basename(args.map_out))[0]
    write_pgm(os.path.join(out_dir, stem + ".pgm"), out)
    meta_out = dict(meta)
    meta_out["image"] = stem + ".pgm"
    with open(args.map_out, "w", encoding="utf-8") as f:
        yaml.safe_dump(meta_out, f, sort_keys=False, allow_unicode=True)
    print(f"wrote {args.map_out} / {stem}.pgm")


if __name__ == "__main__":
    main()
