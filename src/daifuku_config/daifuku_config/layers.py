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


"""明示した override と追加ファイルの読み込み。"""

import os

from launch.actions import DeclareLaunchArgument

from . import value
from .overlay import check_sections as _check_sections, load, meta as _meta, overlay as _overlay
from .paths import DEFAULT_OVERRIDES, available_overrides, overrides_path


def declare_args(default_overrides=DEFAULT_OVERRIDES):
    """再利用部品には場所を暗黙注入しない。場所別入口だけが既定を指定する。"""
    return [
        DeclareLaunchArgument(
            "overrides", default_value=default_overrides,
            description="sites/<名前>/overrides.yaml。カンマ区切り可。無効化は none。"
                        f"選べる場所: {', '.join(available_overrides())}",
        ),
        DeclareLaunchArgument(
            "extra_params_file", default_value="",
            description="最後に重ねるパッケージ別 override ファイル。カンマ区切り可。",
        ),
    ]


def site_name(context):
    """overrides:= の 1 つめ、すなわち「今どこか」。

    重ねる順の先頭を場所とみなす (2 つめ以降はその上の微調整という扱い)。
    none / 空のときは場所を名乗っていないので "" を返す — 呼び元はそこから
    地図を導いてはいけない。
    """
    for name in [n.strip() for n in value(context, "overrides").split(",") if n.strip()]:
        return "" if name.lower() == "none" else name
    return ""


def site_meta(context):
    """重ねる順に site: 節をマージしたもの (後勝ち)。

    地図のように「場所そのものに付く値」の入れ物。ここでは中身を解釈しない —
    キーの意味も、相対パスの起点も、無いときにどうするかも読む側が決める
    (daifuku_navigation の nav2_params.resolve_map)。
    """
    merged = {}
    for _label, body in _layer_bodies(context):
        _overlay(merged, _meta(body))
    return merged


def _layer_bodies(context):
    """明示した順番で override ファイルを読み、最上位を検査する。"""
    layers = []
    for name in [n.strip() for n in value(context, "overrides").split(",") if n.strip()]:
        if name.lower() == "none":
            continue
        path = overrides_path(name)
        if not os.path.isfile(path):
            raise RuntimeError(
                f"Unknown overrides name: {name}\n"
                f"Available: {', '.join(available_overrides()) or '(none)'}"
            )
        body = load(path)
        _check_sections(body, path)
        layers.append((f"overrides:{name}", body))
    for path in [p.strip() for p in value(context, "extra_params_file").split(",") if p.strip()]:
        body = load(path)
        _check_sections(body, path)
        layers.append((path, body))
    return layers


def _resolve_layers(context, package):
    """土台の上に重ねるものを、重ねる順に (表示名, 中身) で返す。

    中身は package の部分木まで降りたもの。どの設定ファイルに重なるかは
    ここでは決めない (ノード名で決まる)。
    """
    return [
        (label, body.get(package) or {}) for label, body in _layer_bodies(context)
    ]
