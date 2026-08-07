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
知らない。navigation の params_file だけは `config/nav2/*.yaml` の合成なので、
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
from daifuku_config_manager.params import load, site_name


def fragments_resolver(context):
    """params_file の土台を解決する (params.compose の base_resolvers 用)。

    params_dir/*.yaml をファイル名順に合成する。断片はノード単位で重複しない前提
    (config/README.md) で、重なると「どちらが勝つか分からない」ので止める。

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
                    "config/nav2/*.yaml must partition the nodes; put "
                    "map-specific overrides in config/overrides/ instead."
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


def resolve_map(context, *args, **kwargs):
    """map:= を決めて、overrides と食い違っていないか見る (OpaqueFunction)。

    **地図と overrides は同じ名前で対にする。** 場所が変われば地図も LiDAR の帯も
    emcl2 の調整も一緒に変わるので、人が動かす値は 1 つ (config/site、書き換えは
    tools/site.sh) にしてある。map:= の既定が空なのはそのためで、空なら
    maps/<overrides の 1 つめ>.yaml を採る。

    明示されたときは名前が overrides と一致しているかを見て、違えば止める。
    **ここは今までエラーも警告も出なかった** — 地図だけ差し替えて overrides を
    置き忘れると、別の場所の帯と emcl2 の調整を載せたまま走る。

    導けないとき:
      * overrides:=none        場所を名乗っていないので、既定の地図へ落とす
      * 同名の地図が無い        overrides が場所ではない (将来の調整用など)。
                               map:= を明示させる
    """
    maps_dir = os.path.join(get_package_share_directory("daifuku_stack"), "maps")
    site = site_name(context)
    explicit = value(context, "map")

    if explicit:
        name = os.path.splitext(os.path.basename(explicit))[0]
        if site and name != site:
            raise RuntimeError(
                f"map:= と overrides:= が食い違っています (地図 {name} / 調整 {site})。\n"
                "場所が決まれば地図も LiDAR の帯も emcl2 の調整も決まるので、"
                "この 2 つは同じ名前で対にしてください。\n"
                f"  ふつうは map:= を渡さない (overrides の {site} から導きます)\n"
                f"  地図のほうが正しいなら overrides:={name}\n"
                "  対にしないと分かっていてやるなら overrides:=none\n"
                "既定そのものを変えるのは tools/site.sh です。"
            )
        if not os.path.isfile(explicit):
            raise RuntimeError(
                f"Map YAML file does not exist: {explicit}\n"
                "Pass a real map path, for example: "
                "map:=$PWD/src/daifuku_stack/maps/map_19f.yaml"
            )
        return []

    if not site:
        fallback = os.path.join(maps_dir, "map_19f.yaml")
        return [
            LogInfo(msg=f"map: {fallback} (overrides が場所を指していないので既定)"),
            SetLaunchConfiguration("map", fallback),
        ]

    derived = os.path.join(maps_dir, f"{site}.yaml")
    if not os.path.isfile(derived):
        raise RuntimeError(
            f"overrides:={site} から地図を導けません ({derived} が無い)。\n"
            "地図と overrides は同じ名前で対にする決まりです。場所ではない "
            "overrides を重ねているなら、map:= を明示してください。"
        )
    return [
        LogInfo(msg=f"map: {derived} (overrides:={site} から)"),
        SetLaunchConfiguration("map", derived),
    ]
