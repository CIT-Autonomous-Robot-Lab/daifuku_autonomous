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
知らない。navigation の params_file だけは `src/daifuku_config/stack/nav2/*.yaml` と
`src/daifuku_config/stack/vi_planner.yaml` の合成なので、
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
from daifuku_config_manager.params import config_root, load, site_meta, site_name


def fragments_resolver(context):
    """params_file の土台を解決する (params.compose の base_resolvers 用)。

    params_dir/*.yaml に src/daifuku_config/stack/vi_planner.yaml を足して、ファイル名順に
    合成する。断片はノード単位で重複しない前提 (src/daifuku_config/README.md) で、重なると
    「どちらが勝つか分からない」ので止める。

    VI and the shared Nav2 utilities receive the same composed file so overrides
    can target either node.

    params_file:= を明示したときはそれを土台にするが、**上書きを受けるノード名は
    断片のものを使う**。nav2 のノード宛の節が params_file:= を渡した途端に黙って
    消えると、探しようがないため。

    Returns:
        (中身, 上書きを受けるノード名, 出どころの表示, 常に一時ファイルを作るか)。
    """
    params_dir = value(context, "params_dir")
    vi_frag = os.path.join(config_root("daifuku_stack"), "vi_planner.yaml")
    if not os.path.isfile(vi_frag):
        # 黙って落とさない。抜けると vi_planner が宣言時の既定だけで上がり、
        # overrides の vi_planner: が「行き先の無いノード名」で落ちる。
        raise RuntimeError(f"Missing parameter fragment: {vi_frag}")
    nav2_fragments = sorted(glob.glob(os.path.join(params_dir, "*.yaml")))
    if not nav2_fragments:
        raise RuntimeError(f"No parameter fragments found in {params_dir}")
    fragments = nav2_fragments + [vi_frag]

    merged, owner = {}, {}
    for frag in fragments:
        body = load(frag)
        for node_name in body:
            if node_name in owner:
                raise RuntimeError(
                    f"Node '{node_name}' is defined in two fragments: "
                    f"{owner[node_name]} and {frag}.\n"
                    "The fragments (src/daifuku_config/stack/nav2/*.yaml + "
                    "src/daifuku_config/stack/vi_planner.yaml) must partition the nodes; put "
                    "map-specific overrides in src/daifuku_config/overrides/ instead."
                )
            owner[node_name] = frag
        merged.update(body)
    origin = f"{len(fragments)} fragments from {params_dir} (+ vi_planner.yaml)"

    explicit = value(context, "params_file")
    if not explicit:
        # 断片の合成結果は元からどこにも無いファイルなので、overrides が
        # 何も重ならなくても一時ファイルを作る。
        return merged, set(merged), origin, True
    if not os.path.isfile(explicit):
        raise RuntimeError(f"params_file does not exist: {explicit}")
    return load(explicit), set(merged), explicit, False


# site: map: に書く 2 つの役と、それぞれに対応する launch 引数。
# Navigation reads /map, and external 2D localization reads /map_loc.
MAP_ROLES = ("navigation", "localization")
_MAP_ARGS = {"navigation": "map", "localization": "map_loc"}
# site: map: に書ける 3 つめのキー。localization:=emcl3d が読む PCD の点群地図で、
# **それ以外の構成では読まないので任意** (2D の 2 枚と違って書き忘れが黙って走る
# 相手が居ない)。emcl3d を選んだのに決まらなければ resolve_map が落とす。
POINTCLOUD_KEY = "pointcloud"

_MAP_EXAMPLE = (
    "  site:\n"
    "    map:\n"
    "      navigation:   19f/map_19f.yaml   # 経路計画 (/map)\n"
    "      localization: 19f/map_19f.yaml   # 自己位置推定 (/map_loc)\n"
)


def _map_path(declared):
    """site: map: の値 (maps/ からの相対パスか絶対パス) をフルパスにする。"""
    declared = str(declared).strip()
    if os.path.isabs(declared):
        return os.path.normpath(declared)
    maps_dir = os.path.join(get_package_share_directory("daifuku_stack"), "maps")
    return os.path.normpath(os.path.join(maps_dir, declared))


def declared_maps(context):
    """overrides の `site: map:` が指す 2 枚のフルパス (節が無ければどちらも "")。

    値は maps/ からの相対パスか、絶対パス。**名前から導くのはやめた**
    (2026-08-07) — 以前は maps/<overrides の名前>.yaml という規約で、地図と
    overrides が同じ名前でなければならなかった。どの地図を読むかがファイルの
    どこにも書かれておらず、地図を差し替えるには名前ごと揃え直す必要があった。

    2026-08-25 から **1 枚ではなく役ごとに 2 枚**書く。経路計画には入ってほしく
    ない場所を手で塗り潰した地図で走らせ、自己位置推定には実測のままの地図を使う、
    というのが上流 (CIT-Autonomous-Robot-Lab/mugimaru_bringup) の運用で、1 枚しか
    持てないとどちらかを諦めることになるため。**同じ地図で走らせるときも 2 行とも
    書く** — 片方だけ書けるようにすると、書き忘れがもう一方の地図で黙って走る。
    """
    declared = site_meta(context).get("map")
    site = site_name(context) or "?"
    if declared is None:
        return {role: "" for role in MAP_ROLES}
    if not isinstance(declared, dict):
        raise RuntimeError(
            f"overrides:{site} の site: map: が 1 枚のままです: {declared}\n"
            "2026-08-25 から役ごとに 2 枚書きます。\n" + _MAP_EXAMPLE
            + "同じ地図で走らせるときも 2 行とも書いてください。"
        )
    unknown = sorted(k for k in declared if k not in MAP_ROLES + (POINTCLOUD_KEY,))
    if unknown:
        raise RuntimeError(
            f"overrides:{site} の site: map: に知らない役があります: "
            f"{', '.join(unknown)}\n"
            f"書けるのは {' / '.join(MAP_ROLES)} の 2 つと、任意の "
            f"{POINTCLOUD_KEY} (localization:=emcl3d の PCD) だけです。\n"
            + _MAP_EXAMPLE
        )
    missing = [r for r in MAP_ROLES if not str(declared.get(r) or "").strip()]
    if missing:
        raise RuntimeError(
            f"overrides:{site} の site: map: に {', '.join(missing)} がありません。\n"
            "**同じ地図で走らせるときも 2 行とも書きます** — 片方だけにすると、"
            "書き忘れがもう一方の地図で黙って走る形になります。\n" + _MAP_EXAMPLE
        )
    return {role: _map_path(declared[role]) for role in MAP_ROLES}


def resolve_map(context, *args, **kwargs):
    """map:= / map_loc:= を決めて、overrides と食い違っていないか見る (OpaqueFunction)。

    **地図は overrides が持っている** (`site: map:`)。場所が変われば地図も
    LiDAR の帯も emcl2 の調整も一緒に変わるので、そのひとまとまりを 1 ファイルに
    入れてある。人が動かす値は今どこか (src/daifuku_config/site) の 1 つだけで、
    map:= / map_loc:= の既定が空なのはそのため。

    明示されたときは overrides の宣言と同じものを指しているかを**役ごとに**見て、
    違えば止める。地図だけ差し替えて overrides を置き忘れると、別の場所の帯と
    emcl2 の調整を載せたまま走るため。

    地図が決まらないのは 2 通りで、**どちらも起動時に落とす**:
      * overrides:=none (場所を名乗っていない)
      * その overrides に site: map: が無い
    どちらも map:= を明示すれば通る。**既定の地図へ落とさない** — 落とすと、
    別の場所にいるのに 19F の地図で自己位置を推定し始める。**localization だけは
    例外**で、決まらなければ navigation と同じ地図に落ちる (2026-08-25 まで唯一の
    形なので危なくない。`map:=` だけ渡す使い方をそのまま残すため)。逆向きは無い。
    """
    declared = declared_maps(context)
    site = site_name(context)
    actions, resolved = [], {}

    for role in MAP_ROLES:
        arg = _MAP_ARGS[role]
        want = declared[role]
        explicit = value(context, arg)

        if want and not os.path.isfile(want):
            raise RuntimeError(
                f"overrides:{site or '?'} の site: map: {role}: が指す地図が"
                f"ありません: {want}\n"
                "値は daifuku_stack の maps/ からの相対パス (19f/map_19f.yaml の"
                "ように拡張子まで書く) か、絶対パスです。"
            )

        if explicit:
            if want and os.path.realpath(explicit) != os.path.realpath(want):
                raise RuntimeError(
                    f"{arg}:= と overrides:= が食い違っています。\n"
                    f"  {arg}:=            {explicit}\n"
                    f"  overrides:{site} の site: map: {role}: -> {want}\n"
                    "場所が決まれば地図も LiDAR の帯も emcl2 の調整も決まるので、"
                    "この 2 つは同じものを指していなければなりません。\n"
                    f"  ふつうは {arg}:= を渡さない (overrides が持っています)\n"
                    f"  地図のほうが正しいなら overrides 側の site: map: {role}: "
                    "を直す\n"
                    "  対にしないと分かっていてやるなら overrides:=none を添える\n"
                    "場所そのものを変えるのは ros2 param set /site_manager site <名前> "
                    "(または tools/site.sh <名前>) です。"
                )
            if not os.path.isfile(explicit):
                raise RuntimeError(
                    f"Map YAML file does not exist: {explicit}\n"
                    "Pass a real map path, for example: "
                    f"{arg}:=$PWD/src/daifuku_stack/maps/19f/map_19f.yaml"
                )
            resolved[role] = explicit
            continue

        if want:
            resolved[role] = want
            actions += [
                LogInfo(msg=f"{arg} ({role}): {want} "
                            f"(overrides:{site} の site: map: {role}: から)"),
                SetLaunchConfiguration(arg, want),
            ]
            continue

        if role == "localization" and resolved.get("navigation"):
            fallback = resolved["navigation"]
            resolved[role] = fallback
            actions += [
                LogInfo(msg=f"{arg} ({role}): {fallback} (map:= と同じ地図)"),
                SetLaunchConfiguration(arg, fallback),
            ]
            continue

        raise RuntimeError(
            "どの地図を読むか決まりません。\n"
            + (f"overrides:{site} に site: map: がありません。\n"
               if site else "overrides:=none なので、場所から地図を導けません。\n")
            + "overrides の 1 段目へ次のように書くか、map:= を明示してください。\n"
            + _MAP_EXAMPLE
        )

    # 2 枚が別物のときに立てられるのは emcl2 だけ。**黙って通すと、経路計画用に
    # 手で壁を描き足した地図で自己位置を推定することになる**。
    #   localization:=amcl … 上流の localization_launch.py が自前で map_server を
    #     立てるので、2 枚目を渡す口が無い。
    #   localization:=vi (VIOLA) … vi_planner の地図の購読が 1 つしかないので、
    #     経路計画と自己位置推定が同じ地図を見る。
    selected = value(context, "localization")
    if selected == "emcl3d":
        actions += _resolve_pointcloud(context, site)

    # emcl3d は /map_loc を読まない (PCD を直に読む) ので、2 枚が別でも害は無い。
    if os.path.realpath(resolved["navigation"]) != os.path.realpath(
            resolved["localization"]):
        if selected not in ("emcl", "emcl2", "emcl3d"):
            raise RuntimeError(
                "navigation と localization に別の地図を指しているので、"
                f"localization:={selected} では走れません。\n"
                f"  navigation:   {resolved['navigation']}\n"
                f"  localization: {resolved['localization']}\n"
                "2 枚に分けられるのは localization:=emcl2 のときだけです "
                "(amcl は上流の localization_launch.py が自前で map_server を立てる"
                "ので 2 枚目を渡す口が無く、vi は地図の購読が 1 つしかありません)。\n"
                "  localization:=emcl2 にする\n"
                "  または overrides の site: map: の 2 行を同じ地図にそろえる"
            )

    return actions


def _resolve_pointcloud(context, site):
    """localization:=emcl3d の map_pcd:= を決める (resolve_map から呼ぶ)。

    2D の地図と同じ規則: 既定は overrides の `site: map: pointcloud:`、明示したら
    それと同じものかを見る。**決まらなければ止める** — 別の場所の点群で 6DoF の
    推定を始めると、2D より派手に飛ぶ。
    """
    declared = (site_meta(context).get("map") or {}).get(POINTCLOUD_KEY)
    want = _map_path(declared) if str(declared or "").strip() else ""
    explicit = value(context, "map_pcd")

    if explicit:
        if want and os.path.realpath(explicit) != os.path.realpath(want):
            raise RuntimeError(
                "map_pcd:= と overrides:= が食い違っています。\n"
                f"  map_pcd:=          {explicit}\n"
                f"  overrides:{site} の site: map: {POINTCLOUD_KEY}: -> {want}\n"
                "ふつうは map_pcd:= を渡さない (overrides が持っています)。"
                "対にしないと分かっていてやるなら overrides:=none を添える。"
            )
        path = explicit
    elif want:
        path = want
    else:
        raise RuntimeError(
            "localization:=emcl3d が読む点群地図 (PCD) が決まりません。\n"
            + (f"overrides:{site} の site: map: に {POINTCLOUD_KEY}: がありません。\n"
               if site else "overrides:=none なので、場所から地図を導けません。\n")
            + f"overrides の site: map: に {POINTCLOUD_KEY}: 19f/map_19f.pcd のように"
            "書くか、map_pcd:= を明示してください。"
        )

    if not os.path.isfile(path):
        raise RuntimeError(
            f"PCD map does not exist: {path}\n"
            f"site: map: {POINTCLOUD_KEY}: は daifuku_stack の maps/ からの相対パス"
            "(19f/map_19f.pcd のように拡張子まで書く) か、絶対パスです。"
        )
    return [
        LogInfo(msg=f"map_pcd: {path}"
                    + ("" if explicit else f" (overrides:{site} の site: map: "
                                           f"{POINTCLOUD_KEY}: から)")),
        SetLaunchConfiguration("map_pcd", path),
    ]
