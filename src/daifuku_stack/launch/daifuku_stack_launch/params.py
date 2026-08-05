"""設定ファイルへの上書き (overrides) の合成。

`config/overrides/<名前>.yaml` と `extra_params_file:=` は「ノード名 ->
ros__parameters -> キー」の 3 段で書く。**行き先はノード名だけで決まる**。
その節は、同じノード名を宣言している設定ファイル (config/ の下のどれか) の上に
後勝ちで深くマージされ、一時ファイルになって launch 引数が差し替わる。
どの設定ファイルにも無いノード名は行き先が無いので、起動時にエラーで止まる
(黙って無視されるほうが探しにくいため)。

  1. 土台 = 各 launch が渡している設定ファイル。navigation の params_file だけは
     params_dir/*.yaml をファイル名順に合成したもの (params_file:= を明示した
     場合は合成せずそのファイル)
  2. overrides:=<名前> -> <pkg_share>/config/overrides/<名前>.yaml
  3. extra_params_file:=<パス>

同じ overrides が launch をまたいで使えるのは、行き先がノード名で決まるため。
navigation で emcl2: と vi_planner: が、mapping で slam_toolbox: が、
robot_bringup で raspicat_driver: が効く。その launch が読まない設定ファイル宛の
節は、単に何も起きない (mapping に emcl2: を渡しても害はない)。

SetParameter / SetParametersFromFile では設定ファイルに既にあるキーを上書き
できない。launch_ros はグローバルのパラメータを先に、ノード個別の parameters= を
後に渡すため、後勝ちでノード側が勝つからである。そこで YAML の段階でマージした
一時ファイルを作る。
"""

import difflib
import glob
import os
import tempfile

import yaml
from launch.actions import DeclareLaunchArgument, LogInfo, SetLaunchConfiguration

from . import value

# overrides の既定値。既定の地図 (maps/map_19f.yaml) に対応する調整を、素の起動でも
# 載せるため。4 つの launch すべてで同じ既定にしてある。
DEFAULT_OVERRIDES = "map_19f"


def _load(path):
    with open(path, "rb") as f:
        return yaml.safe_load(f.read().decode("utf-8")) or {}


def _overlay(base, extra):
    """extra を base の上に深く重ねる。

    dict どうしは再帰、それ以外は置き換え。costmaps.yaml のように
    「ノード名 -> ノード名 -> ros__parameters」と 1 段深いものがあるので、
    段数を決め打ちにしない。list を置き換えるのは意図的で、
    action_forward_m のような並びを連結してはいけない。
    """
    for key, val in extra.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            _overlay(base[key], val)
        else:
            base[key] = val


def _dump_temp(prefix, body):
    out = tempfile.NamedTemporaryFile(
        mode="w", prefix=prefix, suffix=".yaml", delete=False, encoding="utf-8",
    )
    yaml.safe_dump(body, out, default_flow_style=False, allow_unicode=True)
    out.close()
    return out.name


def _available_overrides(overrides_dir):
    """overrides:= に渡せる名前を並べる。

    --show-args に出る一覧と、綴りを間違えたときのエラーに出る一覧は同じもので
    なければならない (片方だけが古いと、「一覧に無いのに通る」名前が出る)。
    """
    if not os.path.isdir(overrides_dir):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(overrides_dir) if f.endswith(".yaml")
    )


def declare_args(overrides_dir):
    """overrides / extra_params_file を宣言する (4 つの launch が同じものを使う)。"""
    available = ", ".join(_available_overrides(overrides_dir))
    return [
        DeclareLaunchArgument(
            "overrides",
            # 地図を変えるときは overrides:=map_tsudanuma のように**置き換える** —
            # 追加ではないので、19F 用の調整は自動的に外れる。地図を渡し替えて
            # overrides を放置すると別の地図の調整が載るので注意。
            default_value=DEFAULT_OVERRIDES,
            description=f"config/overrides/<名前>.yaml を重ねる ({available})。"
                        "カンマ区切りで複数可。行き先はノード名で決まるので、"
                        "この launch が読まない設定ファイル宛の節は何も起きない。"
                        f"既定は {DEFAULT_OVERRIDES} (既定の地図に対応)。"
                        "何も重ねないなら overrides:=none "
                        "(ros2 launch は値が空の overrides:= を受け付けない)。",
        ),
        DeclareLaunchArgument(
            "extra_params_file",
            default_value="",
            description="overrides の後にさらに重ねる任意パスのファイル (カンマ区切りで"
                        "複数可)。書き方と行き先の決まりかたは overrides と同じ。",
        ),
    ]


def _resolve_layers(context, overrides_dir):
    """土台の上に重ねるものを、重ねる順に (表示名, 中身) で返す。

    どの設定ファイルに重なるかはここでは決めない (ノード名で決まる)。
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
            available = _available_overrides(overrides_dir)
            raise RuntimeError(
                f"Unknown overrides name: {name}\n"
                f"Available: {', '.join(available) or '(none)'}\n"
                "Use extra_params_file:=<path> for a file outside "
                "config/overrides/."
            )
        layers.append((f"overrides:{name}", _load(path)))

    for extra in [p.strip() for p in value(context, "extra_params_file").split(",") if p.strip()]:
        if not os.path.isfile(extra):
            raise RuntimeError(f"extra_params_file does not exist: {extra}")
        layers.append((extra, _load(extra)))

    return layers


def _nav2_fragments(context):
    """params_dir/*.yaml をファイル名順に合成する (navigation の params_file)。"""
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


def _base(context, name):
    """launch 引数 name が指す設定ファイルを読む。

    Returns:
        (中身, 上書きを受けるノード名, 出どころの表示, 常に一時ファイルを作るか)。
        読めないなら 1 つ目が None。
    """
    if name == "params_file":
        # navigation だけがこの経路に来る (params_dir を宣言しているのもそこだけ)。
        fragments, origin = _nav2_fragments(context)
        explicit = value(context, "params_file")
        if not explicit:
            # 断片の合成結果は元からどこにも無いファイルなので、overrides が
            # 何も重ならなくても一時ファイルを作る。
            return fragments, set(fragments), origin, True
        if not os.path.isfile(explicit):
            raise RuntimeError(f"params_file does not exist: {explicit}")
        # 土台を差し替えても、行き先は断片と同じノード名にする。nav2 のノード宛の
        # 節が params_file:= を渡した途端に黙って消えると、探しようがないため。
        return _load(explicit), set(fragments), explicit, False

    path = value(context, name)
    if not path or not os.path.isfile(path):
        # 選ばなかった構成のファイル (lidar:=mid360 のときの urg_params_file、
        # localization:=amcl のときの emcl2_params_file) は、ここでは止めない。
        # 実際に使う構成で存在しないなら各 launch の validate が見る。
        return None, None, None, False
    body = _load(path)
    return body, set(body), path, False


def _compose_one(name, base, owned, origin, layers, always):
    """1 つの設定ファイルに、そのファイルが受け持つノードの節だけを重ねる。

    Returns:
        (書き出したパス, ログ, 重ねたノード名の集合)。何も重ならず always でも
        なければパスとログは None (土台をそのまま使うので一時ファイルを作らない)。
    """
    applied, hit = [], set()
    for label, body in layers:
        nodes = [n for n in body if n in owned]
        if not nodes:
            continue
        _overlay(base, {n: body[n] for n in nodes})
        applied.append(f"{label} -> {', '.join(nodes)}")
        hit.update(nodes)

    if not applied and not always:
        return None, None, hit

    out = _dump_temp(f"{name}_", base)
    log = LogInfo(msg=f"params: {name}: {origin} -> {out}"
                      + (f" (+ {'; '.join(applied)})" if applied else ""))
    return out, log, hit


def _reject_unknown_nodes(layers, hit, config_root):
    """どの設定ファイルにも無いノード名を書いていたら止める。

    ROS 2 は宣言されていないキーを黙って捨てるし、行き先の無い節も黙って
    消える。「書いたのに効かない」を起動時に見つけるためのもの。

    この launch が読まない設定ファイル宛の節 (mapping での emcl2: など) は
    正常なので、config/ の下**全体**が宣言しているノード名を見る。
    """
    unmatched = {}
    for label, body in layers:
        for node_name in body:
            if node_name not in hit:
                unmatched.setdefault(node_name, label)
    if not unmatched:
        return

    declared = {}
    pattern = os.path.join(config_root, "**", "*.yaml")
    for path in sorted(glob.glob(pattern, recursive=True)):
        if os.path.basename(os.path.dirname(path)) == "overrides":
            continue
        for node_name in _load(path):
            declared.setdefault(node_name, os.path.relpath(path, config_root))

    unknown = {n: label for n, label in unmatched.items() if n not in declared}
    if not unknown:
        return

    lines = []
    for node_name, label in sorted(unknown.items()):
        near = difflib.get_close_matches(node_name, declared, n=3)
        lines.append(f"  {node_name} ({label})"
                     + (f" -- did you mean: {', '.join(near)}?" if near else ""))
    raise RuntimeError(
        "These override sections have nowhere to go -- no file under "
        f"{config_root} declares such a node:\n" + "\n".join(lines) + "\n"
        f"Known nodes: {', '.join(sorted(declared))}"
    )


def compose(context, *args, overrides_dir, targets, **kwargs):
    """targets の各 launch 引数が指す設定ファイルへ overrides を重ねる。

    OpaqueFunction として呼ぶ。以降の参照 (RewrittenYaml / 各 include /
    各ノードの parameters=) は、ここで差し替えた合成結果を見ることになる。

    Args:
        overrides_dir: config/overrides のパス。
        targets: 上書きの対象になる launch 引数名の並び。値が空か実在しない
            ものは黙って飛ばす (選ばなかった構成のファイル)。
    """
    layers = _resolve_layers(context, overrides_dir)

    actions, hit = [], set()
    for name in targets:
        base, owned, origin, always = _base(context, name)
        if base is None:
            continue
        out, log, target_hit = _compose_one(name, base, owned, origin, layers, always)
        hit |= target_hit
        if out is not None:
            actions += [log, SetLaunchConfiguration(name, out)]

    _reject_unknown_nodes(layers, hit, os.path.dirname(overrides_dir))
    return actions


def compose_path(context, path, *, name, overrides_dir):
    """解決済みのパスへ overrides を重ねて (新しいパス, action の並び) を返す。

    launch 引数ではなく OpaqueFunction の中で決まるファイル (robot_bringup の
    driver:= で決まるパラメータファイル) 用。呼び元がそのパスをその場で使うので、
    SetLaunchConfiguration ではなく戻り値で渡す。
    """
    layers = _resolve_layers(context, overrides_dir)
    base = _load(path)
    out, log, hit = _compose_one(name, base, set(base), path, layers, False)
    _reject_unknown_nodes(layers, hit, os.path.dirname(overrides_dir))
    return (path, []) if out is None else (out, [log])


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
