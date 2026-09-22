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

"""overrides の合成と行き先検査。launch も ROS も要らない。

params.py が起動時に使う規則そのもの。tools/checklist と単体テストが
同じ関数を呼ぶので、sed/awk で YAML を再実装しない。
"""

import difflib
import glob
import os

import yaml

# overrides の 1 段目に書けるパッケージ名と、そのパッケージの設定が
# daifuku_config のどの段に居るか。
#
# **ここに無い名前は起動時に落とす。** 各 launch は自分の名前の部分木しか見ないので、
# `daifuku_stak:` のような綴り違いを許すと、どの launch からも読まれないまま
# エラーも警告も出ずに消える。パッケージが増えたときだけここを足す。
CONFIG_DIRS = {
    "daifuku_bringup": "bringup",
    "daifuku_stack": "stack",
}
KNOWN_PACKAGES = tuple(CONFIG_DIRS)

# 1 段目に書ける、パッケージ名ではない節。**ノードのパラメータではないもの**を
# ここへ入れる (今は地図だけ)。
RESERVED_SECTIONS = ("site",)


def load(path):
    """YAML を dict として読む (空ファイルは {})。"""
    with open(path, "rb") as f:
        return yaml.safe_load(f.read().decode("utf-8")) or {}


def overlay(base, extra):
    """extra を base の上に深く重ねる。

    dict どうしは再帰、それ以外は置き換え。list を置き換えるのは意図的で、
    action_forward_m のような並びを連結してはいけない。
    """
    for key, val in extra.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            overlay(base[key], val)
        else:
            base[key] = val


def check_sections(body, label):
    """1 段目に知らない名前が無いか見る。"""
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


def select(body, label, package):
    """1 ファイルから package の部分木を取り出す (1 段目の検査つき)。"""
    check_sections(body, label)
    return body.get(package) or {}


def meta(body):
    """1 ファイルから site: 節 (ノードのパラメータではない値) を取り出す。"""
    section = body.get("site")
    return section if isinstance(section, dict) else {}


def config_files(config_root):
    """config_root の下の設定ファイル (overrides/ を除く) をパス順に。

    **開けないものは飛ばす。** `--symlink-install` の install/ に残ったリンク切れを
    読みにいくと launch ごと落ちる。

    Returns:
        (読めたファイル, リンク切れなどで飛ばしたもの)。
    """
    pattern = os.path.join(config_root, "**", "*.yaml")
    found, stale = [], []
    for path in sorted(glob.glob(pattern, recursive=True)):
        if os.path.basename(os.path.dirname(path)) == "overrides":
            continue
        (found if os.path.isfile(path) else stale).append(path)
    return found, stale


def reject_unknown_nodes(layers, hit, config_root):
    """どの設定ファイルにも無いノード名を書いていたら止める。"""
    unmatched = {}
    for label, body in layers:
        for node_name in body:
            if node_name not in hit:
                unmatched.setdefault(node_name, label)
    if not unmatched:
        return

    declared = {}
    for path in config_files(config_root)[0]:
        for node_name in load(path):
            declared.setdefault(node_name, os.path.relpath(path, config_root))

    unknown = {n: label for n, label in unmatched.items() if n not in declared}
    if not unknown:
        return

    lines = []
    for node_name, label in sorted(unknown.items()):
        near = difflib.get_close_matches(node_name, declared, n=3)
        lines.append(
            f"  {node_name} ({label})"
            + (f" -- did you mean: {', '.join(near)}?" if near else "")
        )
    raise RuntimeError(
        "These override sections have nowhere to go -- no file under "
        f"{config_root} declares such a node:\n" + "\n".join(lines) + "\n"
        f"Known nodes: {', '.join(sorted(declared))}"
    )


def fragment_owners(fragment_paths):
    """断片ファイルのノード名の持ち主。同じ名前が 2 ファイルにあれば RuntimeError。"""
    owner = {}
    for path in fragment_paths:
        for node_name in load(path):
            if node_name in owner:
                raise RuntimeError(
                    f"Node '{node_name}' is defined in two fragments: "
                    f"{owner[node_name]} and {path}."
                )
            owner[node_name] = path
    return owner


def audit_tree(config_dir):
    """src/daifuku_config を見て、起動時と同じ検査をする (ROS 不要)。

    Returns:
        問題の文字列の並び。空なら通る。
    """
    problems = []
    overrides_dir = os.path.join(config_dir, "overrides")
    for path in sorted(glob.glob(os.path.join(overrides_dir, "*.yaml"))):
        label = os.path.basename(path)
        try:
            body = load(path)
            check_sections(body, label)
        except (OSError, yaml.YAMLError, RuntimeError) as err:
            problems.append(str(err))
            continue
        for package, subdir in CONFIG_DIRS.items():
            subtree = body.get(package) or {}
            if not subtree:
                continue
            root = os.path.join(config_dir, subdir)
            try:
                reject_unknown_nodes([(label, subtree)], set(), root)
            except RuntimeError as err:
                problems.append(str(err))

    nav2 = sorted(glob.glob(os.path.join(config_dir, "stack", "nav2", "*.yaml")))
    vi = os.path.join(config_dir, "stack", "vi_planner.yaml")
    if os.path.isfile(vi):
        nav2.append(vi)
    try:
        fragment_owners(nav2)
    except (OSError, yaml.YAMLError, RuntimeError) as err:
        problems.append(str(err))
    return problems
