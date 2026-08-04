#!/usr/bin/env python3
"""ROS 占有格子地図 (map.yaml + .pgm) から Isaac Sim 用の USD ワールドを作る。

Gazebo 版 (rt-net/raspicat_sim) のワールドは `empty.world` / `iscas_museum.world` /
`turtlebot3_house.world` のいずれも数百バイトしかなく、実体は外部 Gazebo モデル DB
への `include` 参照でしかない。USD 化しようとすると元モデルの調達から始まる。

そこで**このリポジトリが既に持っている地図そのもの**を押し出してワールドにする。
利点は依存が無いことだけではない:

  * 地図とシミュレータ環境が定義上ずれない。実機で起きた emcl2 の alpha 崩壊は
    「有効ビームの 28% が地図の壁を貫通する」= 地図と環境の不一致が原因だった。
    地図から環境を作れば、その不一致を**意図的に入れたときだけ**再現される。
  * `simulator/container/fake_robot.py` (地図をレイキャストする疑似 LiDAR) と同じ地図・
    同じしきい値解釈なので、Isaac 版と既存ハーネスの結果を直接比較できる。

占有セルの判定は fake_robot.py の `load_map()` と**同じ式**にしてある:

    p = v / 255            (negate = 1)
    p = (255 - v) / 255    (negate = 0, 既定)
    occupied  = p >  occupied_thresh
    unknown   = p >= free_thresh  かつ occupied でない

`--unknown wall` は fake_robot.py の `unknown_as_obstacle` / run_case.sh の
`SIM_UNKNOWN_AS_OBSTACLE=1` に対応する。既定 (`free`) では未観測セルは素通しに
なる。map_19f.yaml の `free_thresh: 0.25` では未観測画素 (205, p=0.196) が free 側に
落ちるため、既定でも「地図の 74.66% が未観測」という事実はワールドに現れない。
これは意図した挙動で、既存ハーネスの既定と揃えてある (simulator/docs/pi4_sim.md)。

出力は `pxr` (usd-core) を使わない**手書きの .usda テキスト**。Isaac Sim の
Python でしか読めない形式を避けることで、GPU の無い開発機でも生成・検査できる。

使い方:

    python3 map_to_usd.py src/daifuku_stack/maps/map_19f.yaml -o worlds/map.usda
    python3 map_to_usd.py .../map.yaml -o w.usda --unknown wall   # 未観測も壁
    python3 map_to_usd.py .../turtlebot3.yaml -o w.usda --wall-height 1.0

大きな地図 (map_tsudanuma: 5888x4000) はプリム数が数万に達してステージの読み込みが
重い。先に `uv run downsample-map` で粗くしてから渡すこと:

    uv run --project simulator downsample-map maps/map_tsudanuma.yaml /tmp/ts.yaml --scale 4
    uv run --project simulator map-to-usd /tmp/ts.yaml -o worlds/tsudanuma.usda
"""

import argparse
import os
import sys

import numpy as np
import yaml
from PIL import Image

# 壁の高さの既定値。
#
# 「センサのスライスに合わせて薄く作る」は罠。mid360 経路は
# config/sensors/mid360_scan.yaml が base_footprint 基準で min_height 0.30 /
# max_height 0.50 を切り出し、2D 経路の LiDAR は URDF 上 base_link (0.0762) +
# lidar_mount + 0.055 のあたりに来る。どちらか片方に合わせると、もう片方が
# 空スキャンになって「自己位置推定が壊れた」ように見える。床から 2m 立てて
# 両方まとめて覆う。
DEFAULT_WALL_HEIGHT = 2.0

# 床板の厚み [m]。物理的な意味は無く、衝突形状を持たせるためだけのもの。
FLOOR_THICKNESS = 0.1


def load_occupancy(map_yaml, unknown):
    """map.yaml を読み、占有セルの真偽値配列と地図メタデータを返す。

    返す配列の添字は `occ[row][col]` で、row 0 は **画像の一番上** = ROS 座標系の
    y が最大の側。ROS 座標への変換は cell_to_world() が行う。
    """
    with open(map_yaml, "rb") as f:
        meta = yaml.safe_load(f.read().decode("utf-8"))

    image_path = meta["image"]
    if not os.path.isabs(image_path):
        image_path = os.path.join(os.path.dirname(os.path.abspath(map_yaml)), image_path)
    if not os.path.isfile(image_path):
        raise SystemExit(f"map image not found: {image_path}")

    img = np.array(Image.open(image_path).convert("L")).astype(np.float32)

    # fake_robot.py load_map() と同じ確率変換。
    if int(meta.get("negate", 0)):
        p = img / 255.0
    else:
        p = (255.0 - img) / 255.0

    occupied_thresh = float(meta.get("occupied_thresh", 0.65))
    free_thresh = float(meta.get("free_thresh", 0.196))

    occ = p > occupied_thresh
    if unknown == "wall":
        occ |= p >= free_thresh
    elif unknown not in ("free", "wall"):
        raise SystemExit(f"unsupported --unknown: {unknown}")

    return occ, meta


def merge_rectangles(occ):
    """占有セルを軸平行な矩形に貪欲にまとめる。

    そのまま 1 セル 1 プリムにすると map_19f.pgm だけで 9146 プリムになり、
    map_tsudanuma では 17 万を超えてステージが開かなくなる。矩形にまとめると
    壁は細長い 1 枚に潰れるので、実測で 1 桁減る。

    走査は「未使用の占有セルを見つけたら右へ伸ばせるだけ伸ばし、その幅を保った
    まま下へ伸ばせるだけ伸ばす」。最適な被覆ではないが、軸に沿った壁でできた
    屋内地図では十分で、かつ O(セル数) で済む。

    返り値は (row0, col0, height, width) のリスト (セル単位、終端は排他的でない)。
    """
    used = np.zeros_like(occ, dtype=bool)
    rows, cols = occ.shape
    rects = []

    for r in range(rows):
        c = 0
        while c < cols:
            if not occ[r, c] or used[r, c]:
                c += 1
                continue

            # 右へ伸ばす
            w = 1
            while c + w < cols and occ[r, c + w] and not used[r, c + w]:
                w += 1

            # 幅 w を保ったまま下へ伸ばす
            h = 1
            while r + h < rows:
                row_span = occ[r + h, c:c + w]
                used_span = used[r + h, c:c + w]
                if not row_span.all() or used_span.any():
                    break
                h += 1

            used[r:r + h, c:c + w] = True
            rects.append((r, c, h, w))
            c += w

    return rects


def cell_to_world(rect, meta, rows):
    """セル矩形を ROS 地図座標系 (x 右 / y 上, 原点は origin) の箱に変換する。

    画像の row は y の**降順**なので上下が反転する。ここを間違えると地図と
    ワールドが y 軸で鏡像になり、自己位置推定だけが静かにおかしくなる。
    """
    r0, c0, h, w = rect
    res = float(meta["resolution"])
    ox, oy = float(meta["origin"][0]), float(meta["origin"][1])

    # x: 列 c のセルは [ox + c*res, ox + (c+1)*res]
    x_min = ox + c0 * res
    x_max = ox + (c0 + w) * res

    # y: 行 r のセルは上端が y = oy + (rows - r) * res
    y_max = oy + (rows - r0) * res
    y_min = oy + (rows - (r0 + h)) * res

    return x_min, y_min, x_max, y_max


def usda_cube(name, center, size, color, collision=True):
    """UsdGeom.Cube を 1 つ書き出す。

    `size = 1` にして extent を +-0.5 にしてあるので、xformOp:scale の値が
    そのまま辺の長さ [m] になる。UsdPhysics は Cube を PhysX の box shape に
    落とすので、非一様スケールでも衝突形状は正しく付く。
    """
    api = ' (\n        prepend apiSchemas = ["PhysicsCollisionAPI"]\n    )' if collision else ""
    cx, cy, cz = center
    sx, sy, sz = size
    r, g, b = color
    return f"""
    def Cube "{name}"{api}
    {{
        double size = 1
        float3[] extent = [(-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)]
        color3f[] primvars:displayColor = [({r}, {g}, {b})]
        double3 xformOp:translate = ({cx:.6f}, {cy:.6f}, {cz:.6f})
        double3 xformOp:scale = ({sx:.6f}, {sy:.6f}, {sz:.6f})
        uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
    }}
"""


def build_usda(rects, meta, rows, cols, wall_height, floor_margin, source):
    res = float(meta["resolution"])
    ox, oy = float(meta["origin"][0]), float(meta["origin"][1])

    # 床は地図の外周より少し広げる。ロボットが地図の縁に立ったときに落下しない
    # ようにするためで、ナビゲーション上の意味は無い。
    fx_min = ox - floor_margin
    fy_min = oy - floor_margin
    fx_max = ox + cols * res + floor_margin
    fy_max = oy + rows * res + floor_margin

    body = []
    body.append(
        usda_cube(
            "Floor",
            ((fx_min + fx_max) / 2.0, (fy_min + fy_max) / 2.0, -FLOOR_THICKNESS / 2.0),
            (fx_max - fx_min, fy_max - fy_min, FLOOR_THICKNESS),
            (0.35, 0.35, 0.38),
        )
    )

    walls = []
    for i, rect in enumerate(rects):
        x_min, y_min, x_max, y_max = cell_to_world(rect, meta, rows)
        walls.append(
            usda_cube(
                f"Wall_{i:06d}",
                ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0, wall_height / 2.0),
                (x_max - x_min, y_max - y_min, wall_height),
                (0.72, 0.70, 0.66),
            )
        )

    walls_block = "".join("    " + line if line.strip() else line
                          for line in "".join(walls).splitlines(keepends=True))

    return f'''#usda 1.0
(
    doc = "Generated by simulator/src/daifuku_sim/map_to_usd.py from {os.path.basename(source)}"
    metersPerUnit = 1
    upAxis = "Z"
    defaultPrim = "World"
)

# 地図メタデータ (再生成と検算のために残す)
#   resolution      = {res}
#   origin          = [{ox}, {oy}]
#   size (cells)    = {cols} x {rows}
#   occupied rects  = {len(rects)}
#   wall height     = {wall_height} m

def Xform "World"
{{
    def PhysicsScene "PhysicsScene"
    {{
        vector3f physics:gravityDirection = (0, 0, -1)
        float physics:gravityMagnitude = 9.81
    }}

    def DistantLight "SunLight"
    {{
        float inputs:angle = 1
        float inputs:intensity = 3000
        double3 xformOp:rotateXYZ = (315, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:rotateXYZ"]
    }}

    def Xform "Environment"
    {{
{"".join("    " + l if l.strip() else l for l in "".join(body).splitlines(keepends=True))}
    def Xform "Walls"
    {{
{walls_block}
    }}
    }}
}}
'''


def main():
    ap = argparse.ArgumentParser(
        description="ROS 占有格子地図から Isaac Sim 用の USD ワールドを生成する",
    )
    ap.add_argument("map_yaml", help="入力の map.yaml")
    ap.add_argument("-o", "--output", required=True, help="出力の .usda")
    ap.add_argument(
        "--unknown",
        default="free",
        choices=("free", "wall"),
        help="未観測セル (free_thresh 以上 occupied_thresh 以下) の扱い。"
             "fake_robot.py の unknown_as_obstacle と同じ意味 (既定: free)",
    )
    ap.add_argument(
        "--wall-height", type=float, default=DEFAULT_WALL_HEIGHT,
        help=f"壁の高さ [m] (既定: {DEFAULT_WALL_HEIGHT})。"
             "mid360 の切り出し高さ 0.30-0.50m と 2D LiDAR の ~0.14m を"
             "両方覆える値にすること",
    )
    ap.add_argument(
        "--floor-margin", type=float, default=1.0,
        help="床を地図外周からどれだけ広げるか [m] (既定: 1.0)",
    )
    ap.add_argument(
        "--max-prims", type=int, default=50000,
        help="矩形数がこれを超えたら中断する (既定: 50000)。"
             "超える場合は downsample-map で地図を粗くしてから渡す",
    )
    args = ap.parse_args()

    occ, meta = load_occupancy(args.map_yaml, args.unknown)
    rows, cols = occ.shape
    n_occupied = int(occ.sum())

    rects = merge_rectangles(occ)

    print(f"map        : {args.map_yaml}", file=sys.stderr)
    print(f"cells      : {cols} x {rows} @ {meta['resolution']} m", file=sys.stderr)
    print(f"occupied   : {n_occupied} cells ({100.0 * n_occupied / occ.size:.2f}%)"
          f"  [--unknown {args.unknown}]", file=sys.stderr)
    print(f"rectangles : {len(rects)} "
          f"({n_occupied / max(len(rects), 1):.1f} cells/rect)", file=sys.stderr)

    if len(rects) > args.max_prims:
        raise SystemExit(
            f"矩形数 {len(rects)} が --max-prims {args.max_prims} を超えた。\n"
            "  uv run --project simulator downsample-map <in.yaml> <out.yaml> --scale N\n"
            "で地図を粗くしてから渡すか、--max-prims を上げること。"
        )

    usda = build_usda(
        rects, meta, rows, cols, args.wall_height, args.floor_margin, args.map_yaml
    )

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(usda)

    print(f"wrote      : {args.output} "
          f"({os.path.getsize(args.output) / 1024.0:.0f} KiB)", file=sys.stderr)


if __name__ == "__main__":
    main()
