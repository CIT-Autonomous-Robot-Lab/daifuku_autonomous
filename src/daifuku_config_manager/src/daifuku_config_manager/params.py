"""設定ファイルへの上書き (overrides) の合成。

`config/overrides/<名前>.yaml` と `extra_params_file:=` は「**パッケージ名** ->
ノード名 -> ros__parameters -> キー」の 4 段で書く。**行き先はパッケージ名と
ノード名で決まる**。各 launch は自分のパッケージ名の部分木だけを読み、その節は、
同じノード名を宣言している設定ファイル (自分の config_root の下のどれか) の上に
後勝ちで深くマージされ、一時ファイルになって launch 引数が差し替わる。

  1. 土台 = 各 launch が渡している設定ファイル。navigation の params_file だけは
     base_resolvers 経由で params_dir/*.yaml を合成したもの
  2. overrides:=<名前> -> <このパッケージの share>/config/overrides/<名前>.yaml
  3. extra_params_file:=<パス>

**1 地図 = 1 ファイル。** 場所が決まれば LiDAR の帯 (daifuku_bringup) も emcl2 の
調整 (daifuku_stack) も決まる、という 1 つの話なので、パッケージで割らずに
1 つのファイルへ入れてある。パッケージ名の段は、その 1 ファイルをどちらの launch が
どこまで読むかを**明示するため**にある。

落ちるのは 2 通り。どちらも「書いたのに効かない」を起動時に見つけるためのもの。

  * 知らないパッケージ名 (KNOWN_PACKAGES に無い) -> **誰も読まない部分木**に
    なるので、綴り違いが黙って消えないよう止める
  * 行き先の無いノード名 -> config_root の下のどの設定ファイルにも無い

SetParameter / SetParametersFromFile では設定ファイルに既にあるキーを上書き
できない。launch_ros はグローバルのパラメータを先に、ノード個別の parameters= を
後に渡すため、後勝ちでノード側が勝つからである。そこで YAML の段階でマージした
一時ファイルを作る。

**このモジュールはどのパッケージの中身も知らない。** 土台の解決を差し替える口
(base_resolvers) と、宣言済みノード名を探す範囲 (config_root) は呼び元が渡す。
nav2 の断片合成のようなパッケージ固有の規則は、そちら側に置くこと。
"""

import difflib
import glob
import os
import tempfile

import yaml
from ament_index_python.packages import get_package_share_directory
from launch.actions import DeclareLaunchArgument, LogInfo, SetLaunchConfiguration

from . import env_default, value

# overrides の既定値。**場所が変わったら変えるもの**なので、Compose の .env 1 行で
# 配れるよう環境変数から取る。raspicat サービス (LiDAR の帯) と、人が exec で叩く
# navigation (emcl2 と vi の調整) の両方がこれを既定として読むので、片方だけ古い値で
# 走ることが無い。環境変数も無いときの map_19f は既定の地図 (maps/map_19f.yaml)。
DEFAULT_OVERRIDES = env_default("OVERRIDES", "map_19f")

# overrides ファイルの 1 段目に書けるパッケージ名。
#
# **ここに無い名前は起動時に落とす。** 各 launch は自分の名前の部分木しか見ないので、
# `daifuku_stak:` のような綴り違いを許すと、どの launch からも読まれないまま
# エラーも警告も出ずに消える (ノード名の綴り違いを _reject_unknown_nodes で
# 潰しているのと同じ理由)。パッケージが増えたときだけここを足す。
KNOWN_PACKAGES = ("daifuku_bringup", "daifuku_stack")


def overrides_dir():
    """overrides/*.yaml の置き場 (このパッケージの share)。

    daifuku_bringup と daifuku_stack のどちらからも同じものを指す。地図ごとの
    調整が 2 つのパッケージにまたがるので、どちらか一方に置くと他方がそちらへ
    依存してしまう (葉であるこのパッケージに置けば、その依存は起きない)。
    """
    return os.path.join(
        get_package_share_directory("daifuku_config_manager"), "config", "overrides"
    )


def load(path):
    """YAML を dict として読む (空ファイルは {})。"""
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


def _available_overrides():
    """overrides:= に渡せる名前を並べる。

    --show-args に出る一覧と、綴りを間違えたときのエラーに出る一覧は同じもので
    なければならない (片方だけが古いと、「一覧に無いのに通る」名前が出る)。
    """
    directory = overrides_dir()
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(directory) if f.endswith(".yaml")
    )


def declare_args():
    """overrides / extra_params_file を宣言する (どの launch も同じものを使う)。"""
    available = ", ".join(_available_overrides())
    return [
        DeclareLaunchArgument(
            "overrides",
            # 地図を変えるときは overrides:=map_tsudanuma のように**置き換える** —
            # 追加ではないので、19F 用の調整は自動的に外れる。地図を渡し替えて
            # overrides を放置すると別の地図の調整が載るので注意。
            default_value=DEFAULT_OVERRIDES,
            description=f"daifuku_config_manager の overrides/<名前>.yaml を重ねる "
                        f"({available})。カンマ区切りで複数可。行き先はパッケージ名と"
                        "ノード名で決まるので、この launch が読まない設定ファイル宛の"
                        "節は何も起きない。"
                        f"既定は {DEFAULT_OVERRIDES} (環境変数 OVERRIDES。"
                        "Compose なら .env の 1 行)。"
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


def _select(body, label, package):
    """1 ファイルから package の部分木を取り出す。

    1 段目はパッケージ名でなければならない。知らない名前は**どの launch からも
    読まれない**ので、綴り違いが黙って消える前にここで止める。
    """
    unknown = [k for k in body if k not in KNOWN_PACKAGES]
    if unknown:
        raise RuntimeError(
            f"{label}: これらは知らないパッケージ名です: {', '.join(sorted(unknown))}\n"
            f"overrides の 1 段目は {' / '.join(KNOWN_PACKAGES)} のいずれかです "
            "(2 段目がノード名)。どの launch も自分のパッケージ名の部分木しか "
            "読まないので、名前を間違えるとエラーも警告も出ないまま消えます。"
        )
    return body.get(package) or {}


def _resolve_layers(context, package):
    """土台の上に重ねるものを、重ねる順に (表示名, 中身) で返す。

    中身は package の部分木まで降りたもの。どの設定ファイルに重なるかは
    ここでは決めない (ノード名で決まる)。
    """
    layers = []
    directory = overrides_dir()

    for name in [n.strip() for n in value(context, "overrides").split(",") if n.strip()]:
        # ros2 launch は `overrides:=` (値が空) を malformed として弾くので、
        # 「何も重ねない」を渡す手段として none を受ける。既定が map_19f に
        # なっている以上、明示的に外す口が無いと別の地図で 19F の調整が載る。
        if name.lower() == "none":
            continue
        path = os.path.join(directory, f"{name}.yaml")
        if not os.path.isfile(path):
            available = _available_overrides()
            raise RuntimeError(
                f"Unknown overrides name: {name}\n"
                f"Available: {', '.join(available) or '(none)'}\n"
                "Use extra_params_file:=<path> for a file outside "
                "config/overrides/."
            )
        label = f"overrides:{name}"
        layers.append((label, _select(load(path), label, package)))

    for extra in [p.strip() for p in value(context, "extra_params_file").split(",") if p.strip()]:
        if not os.path.isfile(extra):
            raise RuntimeError(f"extra_params_file does not exist: {extra}")
        layers.append((extra, _select(load(extra), extra, package)))

    return layers


def _base(context, name, base_resolvers):
    """launch 引数 name が指す設定ファイルを読む。

    base_resolvers に name があれば、そちらへ丸ごと委ねる。ファイル 1 つでは
    済まない土台 (navigation の params_file は config/nav2/*.yaml の合成) を、
    このモジュールがパッケージ構造を知らないまま扱うための口。

    Returns:
        (中身, 上書きを受けるノード名, 出どころの表示, 常に一時ファイルを作るか)。
        読めないなら 1 つ目が None。
    """
    resolver = base_resolvers.get(name)
    if resolver is not None:
        return resolver(context)

    path = value(context, name)
    if not path or not os.path.isfile(path):
        # 選ばなかった構成のファイル (lidar:=mid360 のときの urg_params_file、
        # localization:=amcl のときの emcl2_params_file) は、ここでは止めない。
        # 実際に使う構成で存在しないなら各 launch の validate が見る。
        return None, None, None, False
    body = load(path)
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

    見るのは**呼び元のパッケージの部分木だけ** (パッケージ名の段で既に分かれて
    いる)。同じパッケージの中で、この launch が読まない設定ファイル宛の節
    (mapping での emcl2: など) は正常なので、config_root の下**全体**が
    宣言しているノード名を見る。
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
        for node_name in load(path):
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


def compose(context, *args, package, config_root, targets,
            base_resolvers=None, **kwargs):
    """targets の各 launch 引数が指す設定ファイルへ overrides を重ねる。

    OpaqueFunction として呼ぶ。以降の参照 (RewrittenYaml / 各 include /
    各ノードの parameters=) は、ここで差し替えた合成結果を見ることになる。

    Args:
        package: 呼び元のパッケージ名。overrides のこの部分木だけを読む。
        config_root: ノード名を宣言している設定ファイルを探す根 (呼び元の
            パッケージの config/)。行き先の無い節を見つけるために使う。
        targets: 上書きの対象になる launch 引数名の並び。値が空か実在しない
            ものは黙って飛ばす (選ばなかった構成のファイル)。
        base_resolvers: {launch 引数名: callable(context)} で土台の解決を
            差し替える。返す形は _base と同じ 4 つ組。
    """
    layers = _resolve_layers(context, package)
    base_resolvers = base_resolvers or {}

    actions, hit = [], set()
    for name in targets:
        base, owned, origin, always = _base(context, name, base_resolvers)
        if base is None:
            continue
        out, log, target_hit = _compose_one(name, base, owned, origin, layers, always)
        hit |= target_hit
        if out is not None:
            actions += [log, SetLaunchConfiguration(name, out)]

    _reject_unknown_nodes(layers, hit, config_root)
    return actions


def compose_path(context, path, *, name, package, config_root):
    """解決済みのパスへ overrides を重ねて (新しいパス, action の並び) を返す。

    launch 引数ではなく OpaqueFunction の中で決まるファイル (robot_bringup の
    driver:= で決まるパラメータファイル) 用。呼び元がそのパスをその場で使うので、
    SetLaunchConfiguration ではなく戻り値で渡す。
    """
    layers = _resolve_layers(context, package)
    base = load(path)
    out, log, hit = _compose_one(name, base, set(base), path, layers, False)
    _reject_unknown_nodes(layers, hit, config_root)
    return (path, []) if out is None else (out, [log])
