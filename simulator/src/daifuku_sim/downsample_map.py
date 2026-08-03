#!/usr/bin/env python3
"""占有格子地図を整数倍でダウンサンプルする (障害物優先の保守的プーリング)。

vi_planner / vi_global_planner は地図全体 × theta_cell_num の状態を密に
確保する (915x577x60 = 3168万状態 = 56B/state で 1.65GB)。theta_cell_num は
vi_core の N_THETA とのコンパイル時一致チェックがあるため実行時に減らせない。
残る現実的な手段が解像度で、0.05 -> 0.10 m にすると状態数は 1/4 になる。

プーリング規約 (vi_bench の bench_map --scale と同じ):
  ブロック内に障害物が1つでもあれば障害物、なければ未観測 > free の順で優先。
  つまり通れる場所が増える方向には決して倒れない。

  uv run downsample-map in.yaml out.yaml --scale 2 [--free-thresh 0.15]

ホスト側 (uv) とコンテナ内 (run_case.sh の MAP_SCALE 経路) の両方から呼ばれる。
後者は `/opt/sim/downsample_map.py` に 1 ファイルだけ配って `python3` で直接叩くので、
**このモジュールにパッケージ内 import を足さないこと。**
"""

import argparse
import os

import numpy as np
import yaml


def read_pgm(path):
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
    return w, h, bytearray(data[i : i + w * h])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src_yaml")
    ap.add_argument("dst_yaml")
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument(
        "--free-thresh",
        type=float,
        default=None,
        help="出力 yaml の free_thresh。未指定なら入力のまま",
    )
    args = ap.parse_args()

    meta = yaml.safe_load(open(args.src_yaml))
    src_img = os.path.join(os.path.dirname(os.path.abspath(args.src_yaml)), meta["image"])
    w, h, px = read_pgm(src_img)
    s = args.scale
    ow, oh = (w + s - 1) // s, (h + s - 1) // s

    occ_th = float(meta.get("occupied_thresh", 0.65))
    free_th = float(meta.get("free_thresh", 0.196))
    if args.free_thresh is not None:
        free_th = args.free_thresh

    # kind: 0=free, 1=unknown, 2=obstacle。ブロック内の最大値を採る
    # (障害物 > 未観測 > free の保守的プーリング)。
    a = np.frombuffer(bytes(px), dtype=np.uint8).reshape(h, w).astype(np.float32)
    p = (255.0 - a) / 255.0
    if int(meta.get("negate", 0)):
        p = 1.0 - p
    kind = np.where(p > occ_th, 2, np.where(p < free_th, 0, 1)).astype(np.uint8)
    # 端数は未観測 (1) で埋める。free を増やす方向には倒さない。
    pad = np.full((oh * s, ow * s), 1, dtype=np.uint8)
    pad[:h, :w] = kind
    pooled = pad.reshape(oh, s, ow, s).max(axis=(1, 3))
    out = np.choose(pooled, [np.uint8(254), np.uint8(205), np.uint8(0)]).tobytes()

    dst_img = os.path.splitext(os.path.basename(args.dst_yaml))[0] + ".pgm"
    dst_path = os.path.join(os.path.dirname(os.path.abspath(args.dst_yaml)), dst_img)
    with open(dst_path, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (ow, oh))
        f.write(bytes(out))

    meta_out = dict(meta)
    meta_out["image"] = dst_img
    meta_out["resolution"] = float(meta["resolution"]) * s
    meta_out["free_thresh"] = free_th
    # origin は左下基準なので、切り上げ分だけ地図が右上に伸びるだけで原点は不変。
    # origin はフロー形式で書く (map_server 以外に vi_bench の簡易 YAML
    # パーサも読むため。ブロック形式のリストは弾かれる)。
    origin = meta_out.pop("origin")
    rest = {k: v for k, v in meta_out.items() if k not in ("image", "resolution")}
    with open(args.dst_yaml, "w") as f:
        f.write(f"image: {meta_out['image']}\n")
        f.write(f"resolution: {meta_out['resolution']}\n")
        f.write(f"origin: [{origin[0]}, {origin[1]}, {origin[2]}]\n")
        for k, v in rest.items():
            f.write(f"{k}: {v}\n")

    n_free = int((pooled == 0).sum())
    n_occ = int((pooled == 2).sum())
    print(
        f"{w}x{h} -> {ow}x{oh} (scale {s}, {meta_out['resolution']:.3f} m/cell) "
        f"free={n_free} occ={n_occ} unknown={ow*oh-n_free-n_occ} "
        f"states@60theta={ow*oh*60:,} dense@56B={ow*oh*60*56/2**30:.2f}GB"
    )
    print(f"wrote {args.dst_yaml} / {dst_path}")


if __name__ == "__main__":
    main()
