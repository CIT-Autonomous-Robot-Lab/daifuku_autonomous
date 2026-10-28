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


"""起動時のパラメータ合成と、共有 API の公開口。"""

import os
import tempfile

import yaml
from launch.actions import LogInfo, SetLaunchConfiguration

from . import paths, value
from .layers import (  # noqa: F401 - 公開 API
    _resolve_layers, declare_args, site_meta, site_name,
)
from .monitoring import config_digest, precheck  # noqa: F401
from .overlay import (  # noqa: F401
    CONFIG_DIRS, KNOWN_PACKAGES, RESERVED_SECTIONS, load,
    overlay as _overlay, reject_unknown_nodes as _reject_unknown_nodes,
)
from .paths import (  # noqa: F401
    DEFAULT_OVERRIDES, DEFAULT_OVERRIDES_ORIGIN, SENTINEL_RESTART_CODE,
    available_overrides, is_site, overrides_dir, overrides_path,
    read_site_file, site_file,
)
from .watch_launch import (  # noqa: F401
    _on_sentinel_exit, declare_watch_arg, follows_site, sentinel_actions,
)

config_root = paths.config_root


def _dump_temp(prefix, body):
    out = tempfile.NamedTemporaryFile(
        mode="w", prefix=prefix, suffix=".yaml", delete=False, encoding="utf-8",
    )
    yaml.safe_dump(body, out, default_flow_style=False, allow_unicode=True)
    out.close()
    return out.name


def _base(context, name, base_resolvers):
    """launch 引数 name が指す設定ファイルを読む。

    base_resolvers に name があれば、そちらへ丸ごと委ねる。ファイル 1 つでは
    済まない土台 (navigation の params_file は navigation/nav2/*.yaml +
    vi_planner.yaml の合成) を、このモジュールがパッケージ構造を知らないまま扱うための口。

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


def compose(context, *args, package, config_root, targets,
            base_resolvers=None, **kwargs):
    """targets の各 launch 引数が指す設定ファイルへ overrides を重ねる。

    OpaqueFunction として呼ぶ。以降の参照 (RewrittenYaml / 各 include /
    各ノードの parameters=) は、ここで差し替えた合成結果を見ることになる。

    Args:
        package: 呼び元のパッケージ名。overrides のこの部分木だけを読む。
        config_root: ノード名を宣言している設定ファイルを探す根 (呼び元の
            パッケージの src/daifuku_config/)。行き先の無い節を見つけるために使う。
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
