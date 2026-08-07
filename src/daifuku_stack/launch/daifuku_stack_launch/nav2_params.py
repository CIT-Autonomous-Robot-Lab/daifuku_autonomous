"""daifuku_stack だけが持つ、nav2 まわりの設定の解決。

`daifuku_config_manager.params` は「launch 引数 1 つ = 設定ファイル 1 つ」しか
知らない。navigation の params_file だけは `config/nav2/*.yaml` の合成なので、
土台の作り方をここに置いて `base_resolvers` 経由で渡す。あちらにパッケージ構造を
持ち込むと、機体側 (daifuku_bringup) にも nav2 の知識が付いてくるため。
"""

import glob
import os

from daifuku_config_manager import value
from daifuku_config_manager.params import load


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


def validate_map_file(context, *args, **kwargs):
    """map:= が実在するか見る (OpaqueFunction)。"""
    map_path = value(context, "map")
    if not os.path.isfile(map_path):
        raise RuntimeError(
            f"Map YAML file does not exist: {map_path}\n"
            "Pass a real map path, for example: "
            "map:=$PWD/src/daifuku_stack/maps/map_19f.yaml"
        )
    return []
