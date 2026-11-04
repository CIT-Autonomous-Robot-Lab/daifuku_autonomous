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

"""取得済み津田沼PCDをmugimaruのmap座標へ変換する。"""

import hashlib
import math
from pathlib import Path
import sys

import numpy as np


def main() -> None:
    """識別済みのASCII地図を変換し、XYZ・intensityのbinary PCDへ保存する。"""
    source, destination = map(Path, sys.argv[1:])
    checksum = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    if checksum.hexdigest() != "19fd7f0cf35e3d8a72f089e48081f572ff21d0a8fd92fcaf916f1bce8993a486":
        raise RuntimeError("対応していないPCDです。地図の版と座標整合を再確認してください。")
    if source.resolve() == destination.resolve():
        raise RuntimeError("入力と出力は別のパスを指定してください。")
    cloud = np.loadtxt(source, skiprows=11, dtype=np.float32)
    yaw = 2.87006675
    c, s = math.cos(yaw), math.sin(yaw)
    cloud[:, :2] = cloud[:, :2] @ np.array([[c, s], [-s, c]]) + [
        -33.85123089, -32.99809369,
    ]
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\nFIELDS x y z intensity\nSIZE 4 4 4 4\n"
        "TYPE F F F F\nCOUNT 1 1 1 1\n"
        f"WIDTH {len(cloud)}\nHEIGHT 1\n"
        f"VIEWPOINT -33.85123089 -32.99809369 0 {math.cos(yaw/2)} 0 0 {math.sin(yaw/2)}\n"
        f"POINTS {len(cloud)}\nDATA binary\n"
    )
    with destination.open("xb") as stream:
        stream.write(header.encode("ascii"))
        cloud.astype("<f4", copy=False).tofile(stream)
    print(f"{destination}: {len(cloud)} points; PCD -> map: x=-33.85123089, "
          "y=-32.99809369, yaw=2.87006675 rad")


if __name__ == "__main__":
    main()
