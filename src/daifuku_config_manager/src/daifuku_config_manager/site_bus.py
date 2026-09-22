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

"""場所の告知 (/daifuku/site) のトピック名と QoS。

site_manager が出して config_sentinel が読む。**絶対名にしてある** —
namespace:= を付けた構成でも、機体側と自律移動側が同じ 1 本を見なければ
意味が無い。立ち上がりが前後しても取りこぼさないよう latch する。
"""

import json

from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)

SITE_TOPIC = "/daifuku/site"

LATCHED = QoSProfile(
    depth=1,
    history=QoSHistoryPolicy.KEEP_LAST,
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
)


def encode_site(site, path):
    """site_manager が流す JSON。キー順を固定して指紋と見比べやすくする。"""
    return json.dumps(
        {"site": site, "file": path},
        ensure_ascii=False, sort_keys=True,
    )


def decode_site(data):
    """告知の JSON から場所の名前を取る。壊れていれば None。"""
    try:
        body = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    site = body.get("site")
    return site if isinstance(site, str) else None
