"""nav2 / emcl2 のパラメータ合成 (navigation.launch.py 用)。

nav2 のパラメータは config/nav2/ に断片で置き、起動時に 1 つのファイルへ
合成する。分割・合成順序・上書きの規則は config/README.md。

後勝ちで重ねる:

  1. params_dir/*.yaml をファイル名順に合成
     (params_file:= を明示した場合は合成せずそのファイルを土台にする)
  2. overrides:=<名前> -> <pkg_share>/config/overrides/<名前>.yaml
     (params_dir:= を変えても override の置き場は動かない)
  3. extra_params_file:=<パス>

SetParameter / SetParametersFromFile では params_file に既にあるキーを
上書きできない。launch_ros はグローバルのパラメータを先に、ノード個別の
parameters= を後に渡すため、後勝ちでノード側が勝つからである。そこで YAML の
段階で深くマージした一時ファイルを作る。マージは
「ノード名 -> ros__parameters -> キー」の 3 段。
"""

import glob
import os
import tempfile

import yaml
from launch.actions import LogInfo, SetLaunchConfiguration

from . import value


def _load(path):
    with open(path, "rb") as f:
        return yaml.safe_load(f.read().decode("utf-8")) or {}


def _overlay(base, extra):
    """extra を base の上に深く重ねる (ノード -> ros__parameters -> キー)。"""
    for node_name, node_body in extra.items():
        if not isinstance(node_body, dict):
            base[node_name] = node_body
            continue
        base_body = base.setdefault(node_name, {})
        for section, values in node_body.items():
            if section == "ros__parameters" and isinstance(values, dict):
                base_body.setdefault("ros__parameters", {}).update(values)
            else:
                base_body[section] = values


def _dump_temp(prefix, body):
    out = tempfile.NamedTemporaryFile(
        mode="w", prefix=prefix, suffix=".yaml", delete=False, encoding="utf-8",
    )
    yaml.safe_dump(body, out, default_flow_style=False, allow_unicode=True)
    out.close()
    return out.name


def _base_params(context):
    """土台になる nav2 パラメータを読む。

    Returns:
        (パラメータの dict, ログに出す出どころの文字列)
    """
    explicit = value(context, "params_file")
    if explicit:
        if not os.path.isfile(explicit):
            raise RuntimeError(f"params_file does not exist: {explicit}")
        return _load(explicit), explicit

    params_dir = value(context, "params_dir")
    fragments = sorted(glob.glob(os.path.join(params_dir, "*.yaml")))
    if not fragments:
        raise RuntimeError(f"No parameter fragments found in {params_dir}")

    merged, owner = {}, {}
    for frag in fragments:
        body = _load(frag)
        for node_name in body:
            if node_name in owner:
                # 断片はノード単位で重複しない前提 (config/README.md)。
                # 重なると「どちらが勝つか分からない」状態になるので止める。
                raise RuntimeError(
                    f"Node '{node_name}' is defined in two fragments: "
                    f"{owner[node_name]} and {frag}.\n"
                    "config/nav2/*.yaml must partition the nodes; put "
                    "map-specific overrides in config/overrides/ instead."
                )
            owner[node_name] = frag
        merged.update(body)
    return merged, f"{len(fragments)} fragments from {params_dir}"


def _resolve_layers(context, overrides_dir):
    """土台の上に重ねるファイルを、重ねる順に (表示名, パス) で返す。

    nav2 側と emcl2 側で別々に解決すると、片方だけ順序や欠落チェックが
    ずれる余地ができるので、ここで 1 度だけ解決する。
    """
    layers = []

    for name in [n.strip() for n in value(context, "overrides").split(",") if n.strip()]:
        # ros2 launch は `overrides:=` (値が空) を malformed として弾くので、
        # 「何も重ねない」を渡す手段として none を受ける。既定が map_19f に
        # なっている以上、明示的に外す口が無いと別の地図で 19F の調整が載る。
        if name.lower() == "none":
            continue
        path = os.path.join(overrides_dir, f"{name}.yaml")
        if not os.path.isfile(path):
            available = sorted(
                os.path.splitext(f)[0]
                for f in os.listdir(overrides_dir)
                if f.endswith(".yaml")
            ) if os.path.isdir(overrides_dir) else []
            raise RuntimeError(
                f"Unknown overrides name: {name}\n"
                f"Available: {', '.join(available) or '(none)'}\n"
                "Use extra_params_file:=<path> for a file outside "
                "config/overrides/."
            )
        layers.append((f"overrides:{name}", path))

    for extra in [p.strip() for p in value(context, "extra_params_file").split(",") if p.strip()]:
        if not os.path.isfile(extra):
            raise RuntimeError(f"extra_params_file does not exist: {extra}")
        layers.append((extra, extra))

    return layers


def _compose_emcl2(context, layers):
    """emcl2 用のパラメータを合成して emcl2_params_file を差し替える。

    emcl2 は nav2 のノードではないので params_file (nav2 の合成結果) を読まず、
    emcl2_params_file がノードへ直接渡る。両者を別扱いにすると
    `overrides/<地図>.yaml` に emcl2: を書いても**エラーも警告も出さずに
    無視される**ので、ここで emcl2 用の合成結果も作る。土台は
    emcl2_params_file:= で渡されたファイル (既定は config/localization/emcl2.yaml)、
    上に載せるのは nav2 側と同じ layers の emcl2 セクションだけ。

    overrides に emcl2 が一切書かれていなければ土台をそのまま使う
    (一時ファイルを作らない)。
    """
    base = value(context, "emcl2_params_file")
    if not os.path.isfile(base):
        # localization:=amcl では emcl2 を起動しないので、存在しなくても
        # ここでは止めない (emcl2 を選んだ場合は backends.validate_localization
        # が見る)。
        return []

    merged = _load(base)
    applied = []
    for label, path in layers:
        body = _load(path)
        if "emcl2" in body:
            _overlay(merged, {"emcl2": body["emcl2"]})
            applied.append(label)
    if not applied:
        return []

    out = _dump_temp("emcl2_params_", merged)
    return [
        LogInfo(msg=f"params: composed emcl2 {base} -> {out}"
                    f" (+ {', '.join(applied)})"),
        SetLaunchConfiguration("emcl2_params_file", out),
    ]


def compose(context, *args, overrides_dir, **kwargs):
    """params_file と emcl2_params_file を組み立てる (OpaqueFunction)。

    以降の参照 (RewrittenYaml / 各 include / emcl2 ノード) は、ここで
    差し替えた合成結果を見ることになる。合成の規則はこのモジュールの
    docstring を参照。
    """
    merged, origin = _base_params(context)
    layers = _resolve_layers(context, overrides_dir)

    applied = []
    for label, path in layers:
        body = _load(path)
        # emcl2 セクションは nav2 側の合成結果に混ぜない。読む者が居ないので
        # 害は無いが、/tmp/nav2_params_*.yaml に emcl2 が現れると
        # 「どちらが効いているのか」を追うときに紛れる。emcl2 の分は
        # _compose_emcl2 が別に合成する。
        _overlay(merged, {k: v for k, v in body.items() if k != "emcl2"})
        applied.append(label)

    out = _dump_temp("nav2_params_", merged)
    return [
        LogInfo(msg=f"params: composed {origin} -> {out}"
                    + (f" (+ {', '.join(applied)})" if applied else "")),
        SetLaunchConfiguration("params_file", out),
    ] + _compose_emcl2(context, layers)


def validate_map_file(context, *args, **kwargs):
    """map:= が実在するか見る (OpaqueFunction)。"""
    map_path = value(context, "map")
    if not os.path.isfile(map_path):
        raise RuntimeError(
            f"Map YAML file does not exist: {map_path}\n"
            "Pass a real map path, for example: "
            "map:=$PWD/src/autonomous_nav/maps/map_19f.yaml"
        )
    return []
