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

"""設定ファイルへの上書き (overrides) の合成。

`config/overrides/<名前>.yaml` と `extra_params_file:=` は「**パッケージ名** ->
ノード名 -> ros__parameters -> キー」の 4 段で書く。**行き先はパッケージ名と
ノード名で決まる**。各 launch は自分のパッケージ名の部分木だけを読み、その節は、
同じノード名を宣言している設定ファイル (自分の config_root の下のどれか) の上に
後勝ちで深くマージされ、一時ファイルになって launch 引数が差し替わる。

1 段目にはもう 1 つ **`site:` という予約節**が書ける (RESERVED_SECTIONS)。ノードの
パラメータではなく「その場所そのもの」に付く値の置き場で、今のところ地図
(`site: map:`) だけが入っている。**どのパッケージのものでもない**ので、パッケージ名
の段には並べない。読むのは site_meta で、意味付け (どこからの相対パスか、無いときに
どうするか) は使う側 (daifuku_stack の nav2_params.resolve_map) が決める。

  1. 土台 = 各 launch が渡している設定ファイル。navigation の params_file だけは
     base_resolvers 経由で params_dir/*.yaml を合成したもの
  2. overrides:=<名前> -> <daifuku_config の share>/overrides/<名前>.yaml
  3. extra_params_file:=<パス>

**1 地図 = 1 ファイル。** 場所が決まれば LiDAR の帯 (daifuku_bringup) も emcl2 の
調整 (daifuku_stack) も決まる、という 1 つの話なので、パッケージで割らずに
1 つのファイルへ入れてある。パッケージ名の段は、その 1 ファイルをどちらの launch が
どこまで読むかを**明示するため**にある。

**どの launch も overrides:= の既定を config/site から取る。** 場所は人が運ぶたびに
変わるので、機体側と自律移動側で別々に指定させない。navigation の map:= もこの値に
追随する (daifuku_stack の nav2_params.resolve_map) ので、切り替えで人が動かす値は
1 つだけになる。

落ちるのは 2 通り。どちらも「書いたのに効かない」を起動時に見つけるためのもの。

  * 知らないパッケージ名 (KNOWN_PACKAGES に無い) -> **誰も読まない部分木**に
    なるので、綴り違いが黙って消えないよう止める
  * 行き先の無いノード名 -> config_root の下のどの設定ファイルにも無い

SetParameter / SetParametersFromFile では設定ファイルに既にあるキーを上書き
できない。launch_ros はグローバルのパラメータを先に、ノード個別の parameters= を
後に渡すため、後勝ちでノード側が勝つからである。そこで YAML の段階でマージした
一時ファイルを作る。

**このモジュールはどのパッケージの中身も知らない。** 土台の解決を差し替える口
(base_resolvers) は呼び元が渡す。nav2 の断片合成のようなパッケージ固有の規則は、
そちら側に置くこと。宣言済みノード名を探す範囲 (config_root) だけは逆で、
**組み立てるのはここ (config_root()) 1 か所**にしてある — 呼び元が作れると
親の段を渡す事故が起き、検査が黙って広がるため。
"""

import difflib
import glob
import hashlib
import json
import os
import tempfile

import yaml
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from launch.actions import (
    DeclareLaunchArgument,
    EmitEvent,
    LogInfo,
    RegisterEventHandler,
    SetLaunchConfiguration,
)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown

from . import env_default, value

# 読めなかったときの最後の砦 (同梱の既定の地図)。
_FALLBACK_SITE = "map_19f"


def site_file():
    """今どこで走らせるかを 1 行で持つファイル (daifuku_config の share)。"""
    return os.path.join(get_package_share_directory("daifuku_config"), "site")


def read_site_file(path):
    """site ファイルの 1 行 (1 つめの空でない非コメント行) を読む。

    書き手 (site_manager、tools/site.sh) と読み手がこの規則を共有する。
    読めない・空なら "" を返す — **投げない**。落とすかどうかは呼び元が決める。
    """
    try:
        with open(path, "rb") as f:
            body = f.read().decode("utf-8")
    except OSError:
        return ""
    for raw in body.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def _read_site():
    """config/site の 1 行を読む。**読めなくても落とさない。**

    ここは params.py の import 時に走るので、投げるとワークスペース中の launch が
    全部立たなくなる。名前そのものの妥当性は後段 (_resolve_layers) が見るので、
    ここでやるのは「読めたか」だけでよい。
    """
    try:
        path = site_file()
    except PackageNotFoundError:
        return _FALLBACK_SITE
    return read_site_file(path) or _FALLBACK_SITE


# overrides の既定値。**場所が変わったら変えるもの**なので、リポジトリの中の
# 1 ファイル (config/site) から取る。機体側 (LiDAR の帯) も、人が exec で叩く
# navigation (emcl2 と vi の調整) も、navigation の map:= も同じ値を見るので、
# 片方だけ古い場所で走ることが無い。書き換えは tools/site.sh。
#
# **.env に置くのはやめた** (2026-08-07)。あちらは COMPOSE_FILE や INPUT_GID の
# ような「機体を仕立てるときに 1 度決める」値の置き場で、運ぶたびに変わる場所を
# 混ぜると忘れる。加えて環境変数はコンテナ生成時に焼かれるので、変えるのに
# `docker compose up -d` (作り直し) が要った — ファイルなら restart で足りる。
#
# 環境変数 OVERRIDES はファイルより強い。**人が書く口ではなく**、
# simulator/ が 1 回きりの構成をコンテナへ渡すためのもの (compose は渡さない)。
_SITE = _read_site()
DEFAULT_OVERRIDES = env_default("OVERRIDES", _SITE)
DEFAULT_OVERRIDES_ORIGIN = (
    "環境変数 OVERRIDES" if os.environ.get("OVERRIDES", "").strip() else "config/site"
)

# overrides ファイルの 1 段目に書けるパッケージ名と、そのパッケージの設定が
# daifuku_config のどの段に居るか。
#
# **ここに無い名前は起動時に落とす。** 各 launch は自分の名前の部分木しか見ないので、
# `daifuku_stak:` のような綴り違いを許すと、どの launch からも読まれないまま
# エラーも警告も出ずに消える (ノード名の綴り違いを _reject_unknown_nodes で
# 潰しているのと同じ理由)。パッケージが増えたときだけここを足す。
CONFIG_DIRS = {
    "daifuku_bringup": "bringup",
    "daifuku_stack": "stack",
}
KNOWN_PACKAGES = tuple(CONFIG_DIRS)

# 1 段目に書ける、パッケージ名ではない節。**ノードのパラメータではないもの**を
# ここへ入れる (今は地図だけ)。パッケージ名の段に混ぜないのは、どちらのパッケージの
# ものでもないから — 地図を読むのは daifuku_stack だが、「その場所の地図」という値は
# 場所の属性であってノードの設定ではない。
RESERVED_SECTIONS = ("site",)

# config_sentinel が「設定が変わったので立て直したい」と言うときの終了コード。
#
# **0 ではない値にしてあるのが要点。** launch を落とすのは
# OnProcessExit(...) -> EmitEvent(Shutdown) で、これが 0 で発火すると
# **ノードがバグで落ちただけでも機体が上がり直す** (restart: unless-stopped と
# 組み合わさると止まらない)。この値ちょうどのときだけ落とす。
SENTINEL_RESTART_CODE = 42


def overrides_dir():
    """overrides/*.yaml の置き場 (daifuku_config の share)。

    daifuku_bringup と daifuku_stack のどちらからも同じものを指す。地図ごとの
    調整が 2 つのパッケージにまたがるので、どちらか一方に置くと他方がそちらへ
    依存してしまう (葉である daifuku_config に置けば、その依存は起きない)。
    """
    return os.path.join(get_package_share_directory("daifuku_config"), "overrides")


def config_root(package):
    """そのパッケージの設定の根 (daifuku_config の bringup/ か stack/)。

    **設定のパスを組み立てるのはここだけ。** 呼び元が os.path.join で作れると、
    親の daifuku_config/ を渡す事故が起きる。そうすると
    _reject_unknown_nodes が両パッケージ分のノード名を認めてしまい、
    `daifuku_bringup:` の下に nav2 のノード名を書いても**通ってしまう** (誰も
    読まない部分木になるのを止める検査が、黙って効かなくなる)。
    """
    return os.path.join(get_package_share_directory("daifuku_config"), CONFIG_DIRS[package])


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


def available_overrides():
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
    available = ", ".join(available_overrides())
    return [
        DeclareLaunchArgument(
            "overrides",
            # 地図を変えるときは overrides:=map_tsudanuma のように**置き換える** —
            # 追加ではないので、19F 用の調整は自動的に外れる。navigation では
            # map:= がこの値に追随する (nav2_params.resolve_map) ので、ここだけ
            # 渡せばよい。既定を変えるのは tools/site.sh。
            default_value=DEFAULT_OVERRIDES,
            description=f"daifuku_config の overrides/<名前>.yaml を重ねる "
                        f"({available})。カンマ区切りで複数可。行き先はパッケージ名と"
                        "ノード名で決まるので、この launch が読まない設定ファイル宛の"
                        "節は何も起きない。"
                        f"既定は {DEFAULT_OVERRIDES} ({DEFAULT_OVERRIDES_ORIGIN}。"
                        "書き換えは tools/site.sh)。"
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


def site_name(context):
    """overrides:= の 1 つめ、すなわち「今どこか」。

    重ねる順の先頭を場所とみなす (2 つめ以降はその上の微調整という扱い)。
    none / 空のときは場所を名乗っていないので "" を返す — 呼び元はそこから
    地図を導いてはいけない。
    """
    for name in [n.strip() for n in value(context, "overrides").split(",") if n.strip()]:
        return "" if name.lower() == "none" else name
    return ""


def _check_sections(body, label):
    """1 段目に知らない名前が無いか見る。

    知らない名前は**どの launch からも読まれない**ので、綴り違いが黙って消える
    前にここで止める。
    """
    unknown = [
        k for k in body if k not in KNOWN_PACKAGES and k not in RESERVED_SECTIONS
    ]
    if unknown:
        raise RuntimeError(
            f"{label}: これらは知らないパッケージ名です: {', '.join(sorted(unknown))}\n"
            f"overrides の 1 段目は {' / '.join(KNOWN_PACKAGES)} "
            f"(2 段目がノード名) か、予約節の {' / '.join(RESERVED_SECTIONS)} "
            "です。どの launch も自分のパッケージ名の部分木しか読まないので、"
            "名前を間違えるとエラーも警告も出ないまま消えます。"
        )


def _select(body, label, package):
    """1 ファイルから package の部分木を取り出す (1 段目の検査つき)。"""
    _check_sections(body, label)
    return body.get(package) or {}


def _meta(body):
    """1 ファイルから site: 節 (ノードのパラメータではない値) を取り出す。"""
    section = body.get("site")
    return section if isinstance(section, dict) else {}


def site_meta(context):
    """重ねる順に site: 節をマージしたもの (後勝ち)。

    地図のように「場所そのものに付く値」の入れ物。ここでは中身を解釈しない —
    キーの意味も、相対パスの起点も、無いときにどうするかも読む側が決める
    (daifuku_stack の nav2_params.resolve_map)。
    """
    merged = {}
    for _label, body in _layer_bodies(context):
        _overlay(merged, _meta(body))
    return merged


def _layer_bodies(context):
    """重ねるファイルを、重ねる順に (表示名, ファイル全体) で返す。

    1 段目の検査 (_check_sections) はここで済ませる。部分木まで降りるのは
    _resolve_layers、site: 節を取るのは site_meta。
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
            available = available_overrides()
            raise RuntimeError(
                f"Unknown overrides name: {name}\n"
                f"Available: {', '.join(available) or '(none)'}\n"
                "Use extra_params_file:=<path> for a file outside "
                "config/overrides/."
            )
        label = f"overrides:{name}"
        body = load(path)
        _check_sections(body, label)
        layers.append((label, body))

    for extra in [p.strip() for p in value(context, "extra_params_file").split(",") if p.strip()]:
        if not os.path.isfile(extra):
            raise RuntimeError(f"extra_params_file does not exist: {extra}")
        body = load(extra)
        _check_sections(body, extra)
        layers.append((extra, body))

    return layers


def _resolve_layers(context, package):
    """土台の上に重ねるものを、重ねる順に (表示名, 中身) で返す。

    中身は package の部分木まで降りたもの。どの設定ファイルに重なるかは
    ここでは決めない (ノード名で決まる)。
    """
    return [
        (label, body.get(package) or {}) for label, body in _layer_bodies(context)
    ]


def _base(context, name, base_resolvers):
    """launch 引数 name が指す設定ファイルを読む。

    base_resolvers に name があれば、そちらへ丸ごと委ねる。ファイル 1 つでは
    済まない土台 (navigation の params_file は config/stack/nav2/*.yaml の合成) を、
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
    for path in _config_files(config_root)[0]:
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


# ──────────────────────────────────────────────────────────────────────────────
# 設定が書き変わったことを見つけるための道具 (config_sentinel / site_manager と共用)
#
# **ここは launch の中からもノードの中からも同じ答えを出さなければならない。**
# launch は起動時に 1 度呼んで指紋を sentinel へ渡し、sentinel はそれを 2 秒ごとに
# 計算し直して比べる。context を引かない (site 名と config_root だけで決まる) のは
# そのため。
# ──────────────────────────────────────────────────────────────────────────────


def overrides_path(site):
    """場所の名前から overrides のファイルパス。**実在は確かめない。**"""
    return os.path.join(overrides_dir(), f"{site}.yaml")


def is_site(name):
    """場所を名乗っている名前か (none / 空は名乗っていない)。"""
    return bool(name) and name.strip().lower() != "none"


def _config_files(config_root):
    """config_root の下の設定ファイル (overrides/ を除く) をパス順に。

    **開けないものは飛ばす。** `--symlink-install` の install/ は src/ への
    symlink なので、設定ファイルを別のパッケージへ移すと**古い symlink が
    install/ に残る** (2026-08-07 の実機: daifuku_stack の share にまだ
    当時の config/robot/joy_teleop.yaml が居た。移したのは f922a80)。glob には出るが
    開けないので、読みにいくと launch ごと落ちる。ここは「今ある設定」を数える
    ところなので、リンク切れは設定ではないと見なす。

    Returns:
        (読めたファイル, リンク切れなどで飛ばしたもの)。飛ばしたほうは呼び元が
        言うためのもので、**黙って捨てない**。
    """
    pattern = os.path.join(config_root, "**", "*.yaml")
    found, stale = [], []
    for path in sorted(glob.glob(pattern, recursive=True)):
        if os.path.basename(os.path.dirname(path)) == "overrides":
            continue
        (found if os.path.isfile(path) else stale).append(path)
    return found, stale


def config_digest(site, package, config_root):
    """「今ディスクにある、このパッケージが読む設定」の指紋。

    **中身を正規化してから取るので、コメントや並び順を直しただけでは変わらない。**
    設定を書き換えたかどうかを見るためのものなので、注釈の推敲で機体が上がり直す
    のは邪魔でしかない。

    見るのは 2 つ:

      * config_root の下の設定ファイル**全部**。この launch が実際に読むものだけに
        絞らないのは、絞るには targets と base_resolvers を渡し回すことになり、
        取りこぼしたときに**エラーも警告も出ないまま検出だけが効かなくなる**ため。
        代償は、その launch が読まない設定 (navigation から見た
        mapping/slam_toolbox.yaml) を直しても変わったと言うこと。
      * overrides/<site>.yaml のうち**このパッケージの部分木と site: 節だけ**。
        ファイルまるごとにすると、daifuku_stack: の数字を直しただけで機体が
        上がり直す。

    地図そのもの (maps/*.pgm) は見ていない。同じパスのまま作り直したときは
    人が立て直すこと。

    Raises:
        yaml.YAMLError: どれかが壊れているとき。**呼び元は握って「壊れている」
            として扱うこと** — 書きかけを読んだだけかもしれないので、これを
            立て直しの理由にしてはいけない。
    """
    payload = {
        "site": site,
        "package": package,
        "files": {
            os.path.relpath(path, config_root).replace(os.sep, "/"): load(path)
            for path in _config_files(config_root)[0]
        },
    }
    if is_site(site):
        body = load(overrides_path(site))
        payload["overrides"] = body.get(package) or {}
        payload["meta"] = _meta(body)
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def precheck(site, package, config_root):
    """その場所でこのパッケージの launch が通るかを、立てずに確かめる。

    **これが無いと、yaml の綴り違い 1 つで機体が上がり直し続ける。** sentinel が
    launch を落とし、restart: unless-stopped が上げ直し、compose が同じ綴り違いで
    投げ、また落ちる — 無人の実機でこれをやらせないための関門。site_manager が
    書く前にも同じものを通すので、`ros2 param set` は**ファイルを書く前に**断る。

    見るのは合成が起動時に落とす 2 つ (1 段目の名前、行き先の無いノード名) と、
    そもそも読めるか。daifuku_stack の断片の重複検査 (fragments_resolver) までは
    見ない — あちらはパッケージ固有の規則なので、ここからは呼べない。

    Returns:
        (通るか, 通らない理由)。通るなら理由は ""。
    """
    try:
        for path in _config_files(config_root)[0]:
            load(path)
        if not is_site(site):
            return True, ""
        path = overrides_path(site)
        if not os.path.isfile(path):
            return False, f"{path} がありません"
        label = f"overrides:{site}"
        subtree = _select(load(path), label, package)
        _reject_unknown_nodes([(label, subtree)], set(), config_root)
    except (OSError, yaml.YAMLError, RuntimeError) as err:
        return False, str(err)
    return True, ""


def follows_site(context):
    """この launch が「場所の切り替えに黙って追随してよい」ものか。

    追随するのは**人が構成を指定しなかったとき**だけ。`overrides:=` を明示した
    人にも、`OVERRIDES` を渡した simulator にも、その構成で走らせたい理由が
    あるはずで、config/site が変わったからといって勝手に落としてはいけない
    (食い違いを言うのは、追随しないときも sentinel がやる)。

    既定値ちょうどを明示したときは追随する側に入るが、それは同じ値なので害が無い。
    """
    return (
        DEFAULT_OVERRIDES_ORIGIN == "config/site"
        and value(context, "overrides").strip() == DEFAULT_OVERRIDES
        and not value(context, "extra_params_file").strip()
    )


def _on_sentinel_exit(event, context):
    """config_sentinel が終わったときに launch をどうするか。

    **落とすのは SENTINEL_RESTART_CODE ちょうどのときだけ。** 0 でも落とすように
    すると、ノードがバグで終わっただけで機体が上がり直す (restart: unless-stopped
    と組み合わさると止まらない)。それ以外の異常終了では launch は生き続けるが、
    **見張りが居なくなったことは言う** — 黙って検出だけが効かなくなるのが一番悪い。
    """
    if event.returncode == SENTINEL_RESTART_CODE:
        return [
            LogInfo(msg="config_sentinel: 設定が変わったので launch を終了します。"),
            EmitEvent(event=Shutdown(reason="configuration changed")),
        ]
    # 0 は launch 自身の停止に巻き込まれた正常終了、負値はシグナル (SIGINT/SIGTERM)。
    if event.returncode in (0, -2, -15):
        return []
    return [LogInfo(
        msg=f"config_sentinel が rc={event.returncode} で落ちました。"
            "以降、設定の書き換えは検出されません (launch はこのまま動きます)。",
    )]


def declare_watch_arg():
    """config_watch を宣言する (config_sentinel を立てる launch が共有する)。"""
    return DeclareLaunchArgument(
        "config_watch",
        default_value="shutdown",
        description="起動後に設定ファイルが書き変わったときどうするか。"
                    "shutdown (既定) = 大声で言い、追随してよい構成で・その設定でも"
                    "立ち上がることを確かめ・機体が止まっていれば launch を終了する"
                    "(機体は compose の restart で上がり直す)。"
                    "warn = 言うだけ。off = 見張りごと立てない。",
    )


def sentinel_actions(context, *args, package, config_root, action=None,
                     node_name=None, **kwargs):
    """設定の書き換えを見張るノードと、その終了を launch の停止に繋ぐ handler。

    **top-level の launch だけが呼ぶこと。** include される側 (lidar_bringup /
    odom_fusion) でも呼ぶと、1 つの launch 木に見張りが 3 つ立って、それぞれが
    勝手に launch を落としにかかる。

    Args:
        package: この launch のパッケージ名 (overrides のどの部分木を見るか)。
        config_root: このパッケージの config/ (指紋を取る範囲)。
        action: shutdown / warn / off。省略すると launch 引数 config_watch。
        node_name: 既定は config_sentinel_<パッケージ名から daifuku_ を除いたもの>。

    Returns:
        action の並び (OpaqueFunction からそのまま返せる)。
    """
    # launch_ros はここでだけ要る。module の頭で読むと、ノード側
    # (site_manager / config_sentinel が import する params) にも付いてくる。
    from launch_ros.actions import Node

    if action is None:
        action = value(context, "config_watch").strip().lower()
    if action == "off":
        return []
    if action not in ("shutdown", "warn"):
        raise RuntimeError(
            f"config_watch:={action} は未対応です (shutdown / warn / off)。"
        )

    site = site_name(context)
    name = node_name or f"config_sentinel_{package.replace('daifuku_', '')}"
    try:
        digest = config_digest(site, package, config_root)
    except (OSError, yaml.YAMLError) as err:
        # ここで読めないなら合成も通っていないはずだが、順序に依らず言っておく。
        raise RuntimeError(f"設定の指紋を取れません: {err}")

    logs = []
    stale = _config_files(config_root)[1]
    if stale:
        # 設定ファイルを別のパッケージへ移したときの残骸 (install/ の symlink は
        # ビルド時に張られ、ソースが消えてもそのまま残る)。**見張りの対象からは
        # 外れている**ので、そのぶんだけ検出も効かない。消し方は
        # `find <install> -xtype l -delete` か、install/ を消して建て直す。
        logs.append(LogInfo(msg=(
            f"config_sentinel: リンク切れの設定ファイルを飛ばしました "
            f"({len(stale)} 個): {', '.join(stale)}\n"
            "  install/ に古い symlink が残っています "
            "(設定ファイルを別のパッケージへ移した残骸)。"
        )))

    sentinel = Node(
        package="daifuku_config_manager",
        executable="config_sentinel",
        name=name,
        output="screen",
        parameters=[{
            "package": package,
            "config_root": config_root,
            "site": site,
            "follow": follows_site(context),
            "digest": digest,
            "action": action,
        }],
    )
    return logs + [
        sentinel,
        RegisterEventHandler(OnProcessExit(
            target_action=sentinel, on_exit=_on_sentinel_exit,
        )),
    ]


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
