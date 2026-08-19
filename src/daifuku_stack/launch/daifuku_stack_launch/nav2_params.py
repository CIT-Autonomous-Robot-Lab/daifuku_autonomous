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

"""daifuku_stack だけが持つ、nav2 まわりの設定の解決。

`daifuku_config_manager.params` は「launch 引数 1 つ = 設定ファイル 1 つ」しか
知らない。navigation の params_file だけは `configs/stack/nav2/*.yaml` の合成なので、
土台の作り方をここに置いて `base_resolvers` 経由で渡す。あちらにパッケージ構造を
持ち込むと、機体側 (daifuku_bringup) にも nav2 の知識が付いてくるため。

`maps/` を持つのも daifuku_stack だけなので、overrides から地図を導く規則
(`resolve_map`) もここにある。機体側は地図を知らないし、知る必要もない。
"""

import glob
import os

from ament_index_python.packages import get_package_share_directory
from launch.actions import LogInfo, SetLaunchConfiguration

from daifuku_config_manager import value
from daifuku_config_manager.params import load, site_meta, site_name


def fragments_resolver(context):
    """params_file の土台を解決する (params.compose の base_resolvers 用)。

    params_dir/*.yaml をファイル名順に合成する。断片はノード単位で重複しない前提
    (configs/README.md) で、重なると「どちらが勝つか分からない」ので止める。

    params_file:= を明示したときはそれを土台にするが、**上書きを受けるノード名は
    断片のものを使う**。nav2 のノード宛の節が params_file:= を渡した途端に黙って
    消えると、探しようがないため。

    Returns:
        (中身, 上書きを受けるノード名, 出どころの表示, 常に一時ファイルを作るか)。
    """
    params_dir = value(context, "params_dir")
    fragments = sorted(glob.glob(os.path.join(params_dir, "*.yaml")))
    if not fragments:
        raise RuntimeError(f"No parameter fragments found in {params_dir}")

    merged, owner = {}, {}
    for frag in fragments:
        body = load(frag)
        for node_name in body:
            if node_name in owner:
                raise RuntimeError(
                    f"Node '{node_name}' is defined in two fragments: "
                    f"{owner[node_name]} and {frag}.\n"
                    "configs/stack/nav2/*.yaml must partition the nodes; put "
                    "map-specific overrides in configs/overrides/ instead."
                )
            owner[node_name] = frag
        merged.update(body)
    origin = f"{len(fragments)} fragments from {params_dir}"

    explicit = value(context, "params_file")
    if not explicit:
        # 断片の合成結果は元からどこにも無いファイルなので、overrides が
        # 何も重ならなくても一時ファイルを作る。
        return merged, set(merged), origin, True
    if not os.path.isfile(explicit):
        raise RuntimeError(f"params_file does not exist: {explicit}")
    return load(explicit), set(merged), explicit, False


def declared_map(context):
    """overrides の `site: map:` が指す地図のフルパス ("" なら書かれていない)。

    値は maps/ からの相対パスか、絶対パス。**名前から導くのはやめた**
    (2026-08-07) — 以前は maps/<overrides の名前>.yaml という規約で、地図と
    overrides が同じ名前でなければならなかった。どの地図を読むかがファイルの
    どこにも書かれておらず、地図を差し替えるには名前ごと揃え直す必要があった。
    """
    declared = str(site_meta(context).get("map") or "").strip()
    if not declared:
        return ""
    if os.path.isabs(declared):
        return os.path.normpath(declared)
    maps_dir = os.path.join(get_package_share_directory("daifuku_stack"), "maps")
    return os.path.normpath(os.path.join(maps_dir, declared))


def resolve_map(context, *args, **kwargs):
    """map:= を決めて、overrides と食い違っていないか見る (OpaqueFunction)。

    **地図は overrides が持っている** (`site: map:`)。場所が変われば地図も
    LiDAR の帯も emcl2 の調整も一緒に変わるので、そのひとまとまりを 1 ファイルに
    入れてある。人が動かす値は今どこか (configs/site) の 1 つだけで、map:= の既定が
    空なのはそのため。

    明示されたときは overrides の宣言と同じものを指しているかを見て、違えば止める。
    地図だけ差し替えて overrides を置き忘れると、別の場所の帯と emcl2 の調整を
    載せたまま走るため。

    地図が決まらないのは 2 通りで、**どちらも起動時に落とす**:
      * overrides:=none (場所を名乗っていない)
      * その overrides に site: map: が無い
    どちらも map:= を明示すれば通る。**既定の地図へ落とさない** — 落とすと、
    別の場所にいるのに 19F の地図で自己位置を推定し始める。
    """
    declared = declared_map(context)
    site = site_name(context)
    explicit = value(context, "map")

    if declared and not os.path.isfile(declared):
        raise RuntimeError(
            f"overrides:{site or '?'} の site: map: が指す地図がありません: {declared}\n"
            "値は daifuku_stack の maps/ からの相対パス (map_19f.yaml のように"
            "拡張子まで書く) か、絶対パスです。"
        )

    if explicit:
        if declared and os.path.realpath(explicit) != os.path.realpath(declared):
            raise RuntimeError(
                f"map:= と overrides:= が食い違っています。\n"
                f"  map:=            {explicit}\n"
                f"  overrides:{site} の site: map: -> {declared}\n"
                "場所が決まれば地図も LiDAR の帯も emcl2 の調整も決まるので、"
                "この 2 つは同じものを指していなければなりません。\n"
                "  ふつうは map:= を渡さない (overrides が持っています)\n"
                "  地図のほうが正しいなら overrides 側の site: map: を直す\n"
                "  対にしないと分かっていてやるなら overrides:=none を添える\n"
                "場所そのものを変えるのは tools/site.sh です。"
            )
        if not os.path.isfile(explicit):
            raise RuntimeError(
                f"Map YAML file does not exist: {explicit}\n"
                "Pass a real map path, for example: "
                "map:=$PWD/src/daifuku_stack/maps/map_19f.yaml"
            )
        return []

    if not declared:
        raise RuntimeError(
            "どの地図を読むか決まりません。\n"
            + (f"overrides:{site} に site: map: がありません。\n"
               if site else "overrides:=none なので、場所から地図を導けません。\n")
            + "overrides の 1 段目へ次のように書くか、map:= を明示してください。\n"
              "  site:\n"
              "    map: map_19f.yaml   # daifuku_stack の maps/ からの相対パス"
        )

    return [
        LogInfo(msg=f"map: {declared} (overrides:{site} の site: map: から)"),
        SetLaunchConfiguration("map", declared),
    ]
