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

"""ウェイポイント YAML の読み込み (ROS に依存しない)。

書式は daifuku_waypoint_manager パネルが保存するものと同じ
(frame_id + waypoints[].position/orientation)。パネルは RViz プラグインで
実機には載らないので、joy_teleop は同じ書式を読むだけの実装を持つ。

**受け入れる書式はパネルの readYamlFile (waypoint_manager_panel.cpp) と
そろえてある。** 片方だけが通す形にすると、手で書いた順路が「実機では走るのに
パネルでは開けない」(あるいはその逆) になる。決まりは 3 つ:

  * frame_id は必須。既定を持たせると、書き忘れた順路が黙って map 上の
    座標として走る (座標系を取り違えると全点が地図の外に出る)
  * position.z は省略可 (0.0)。接地して走る機体なので、無くても意味が決まる
  * 有限でない値と、長さが 0 のクォータニオンは弾く。NaN のまま
    FollowWaypoints へ投げると Nav2 の側で黙って落ちる

ここを直したらパネル側も直すこと。検算は simulator/tests/verify_waypoints.py。
"""

import math

import yaml


def load_waypoint_document(path):
    """ウェイポイントの YAML を (frame_id, 点の並び) で返す。

    各点は ((x, y, z), (qx, qy, qz, qw))。ROS の型は持たないので、
    開発ホストでも rclpy 無しで検算できる。

    Raises:
        ValueError: 書式が違うとき。1 点でも欠けていれば読み込みごと失敗させる
            (黙って飛ばすと、経路の途中が抜けた巡回が静かに走ってしまう)。
        OSError, yaml.YAMLError: ファイルが読めない・YAML として壊れているとき。
    """
    # encoding を明示する。同梱の waypoints_tsudanuma_v1.0.yaml は冒頭に日本語の
    # 注記を持っていて、ロケールが C の環境 (実機のコンテナは LANG を持たない)
    # では既定の encoding が ASCII になり、**読み込みごと失敗する**。
    # daifuku_config_manager/params.py が同じ理由で明示しているのと同じ。
    with open(path, encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise ValueError("top level is not a mapping")

    frame_id = document.get("frame_id")
    if not frame_id or not isinstance(frame_id, str):
        raise ValueError("missing or invalid frame_id")
    entries = document.get("waypoints")
    if not entries:
        raise ValueError("no waypoints")
    if not isinstance(entries, list):
        raise ValueError("waypoints is not a sequence")

    poses = []
    for index, entry in enumerate(entries):
        try:
            position = entry["position"]
            orientation = entry["orientation"]
            xyz = (
                float(position["x"]),
                float(position["y"]),
                float(position.get("z", 0.0)),
            )
            quat = (
                float(orientation["x"]),
                float(orientation["y"]),
                float(orientation["z"]),
                float(orientation["w"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("waypoint %d: %s" % (index, exc)) from exc
        _validate_pose(index, xyz, quat)
        poses.append((xyz, quat))
    return frame_id, poses


def _validate_pose(index, xyz, quat):
    """有限でない座標と、長さが 0 のクォータニオンを弾く (パネルと同じ判定)。"""
    if not all(math.isfinite(v) for v in xyz):
        raise ValueError("waypoint %d: position is not finite" % index)
    norm_squared = quat[0] ** 2 + quat[1] ** 2 + quat[2] ** 2 + quat[3] ** 2
    if not math.isfinite(norm_squared) or norm_squared < 1e-12:
        raise ValueError(
            "waypoint %d: orientation is not a usable quaternion" % index
        )
