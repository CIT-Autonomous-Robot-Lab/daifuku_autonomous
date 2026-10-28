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

"""選択した機能の設定と、明示入力だけの指紋・起動前検査。ROS 不要。"""

import hashlib
import json
import os

import yaml

from . import paths
from .overlay import (
    CONFIG_DIRS, KNOWN_PACKAGES, check_sections, config_files, load, meta,
    reject_unknown_nodes,
)


def selected_roots(package, config_root, packages=None):
    """呼び元の根を起点に、選んだ機能の根だけを返す。"""
    selected = tuple(dict.fromkeys(packages if packages is not None else [package]))
    if not selected:
        raise RuntimeError("監視する packages は空にできません")
    roots = {}
    for name in selected:
        if name not in CONFIG_DIRS:
            raise RuntimeError(f"知らない設定パッケージです: {name}")
        roots[name] = (config_root if name == package else
                       os.path.join(os.path.dirname(config_root), CONFIG_DIRS[name]))
    return roots


def _names(site):
    return [name.strip() for name in site.split(",") if paths.is_site(name.strip())]


def _is_overlay(body):
    return any(key == "site" or key in KNOWN_PACKAGES or key.startswith("daifuku_")
               for key in body)


def _selected_overlay(body, selected):
    """無関係な機能の値は指紋へ入れない。"""
    return {
        "packages": {name: body.get(name) or {} for name in selected},
        "meta": meta(body),
        "unknown": sorted(key for key in body
                          if key not in KNOWN_PACKAGES and key != "site"),
    }


def config_digest(site, package, config_root, *, packages=None, files=None):
    """コメントや順序で変わらず、選んだ構成の値で変わる指紋。"""
    roots = selected_roots(package, config_root, packages)
    payload = {"roots": {}, "layers": {}, "files": {}}
    for name, root in roots.items():
        payload["roots"][name] = {
            os.path.relpath(path, root).replace(os.sep, "/"): load(path)
            for path in config_files(root)[0]
        }
    for name in _names(site):
        payload["layers"][name] = _selected_overlay(load(paths.overrides_path(name)), roots)
    for path in sorted(set(files or [])):
        body = load(path)
        payload["files"][path] = (_selected_overlay(body, roots)
                                  if _is_overlay(body) else body)
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def precheck(site, package, config_root, *, packages=None, files=None):
    """選んだ入力だけを検査し、壊れた設定での再起動を防ぐ。"""
    try:
        roots = selected_roots(package, config_root, packages)
        for root in roots.values():
            for path in config_files(root)[0]:
                load(path)
        layers = [(paths.overrides_path(name), True) for name in _names(site)]
        layers += [(path, False) for path in files or []]
        for path, required_overlay in layers:
            body = load(path)
            if required_overlay or _is_overlay(body):
                check_sections(body, path)
                for name, root in roots.items():
                    reject_unknown_nodes([(path, body.get(name) or {})], set(), root)
    except (OSError, yaml.YAMLError, RuntimeError) as err:
        return False, str(err)
    return True, ""
