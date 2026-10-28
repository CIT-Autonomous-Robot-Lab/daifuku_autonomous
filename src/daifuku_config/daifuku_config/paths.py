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

"""機能別設定と、明示した場所の設定のパス。"""

import os

from .overlay import CONFIG_DIRS

DEFAULT_OVERRIDES = "none"
DEFAULT_OVERRIDES_ORIGIN = "明示した構成のみ"
SENTINEL_RESTART_CODE = 42


def _share():
    from ament_index_python.packages import get_package_share_directory

    return get_package_share_directory("daifuku_config")


def site_file():
    """次回起動用の場所名を持つファイル。実行中の構成には使わない。"""
    return os.path.join(_share(), "site")


def read_site_file(path):
    """読めない・空なら空文字列を返す。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def overrides_dir():
    """場所別設定の親ディレクトリ。"""
    return os.path.join(_share(), "sites")


def overrides_path(site):
    """場所名から overrides.yaml のパスを引く。"""
    if not site or site in (".", "..") or "/" in site or "\\" in site:
        raise RuntimeError(f"場所の名前になっていません: {site!r}")
    return os.path.join(overrides_dir(), site, "overrides.yaml")


def available_overrides():
    """overrides.yaml を持つ場所だけを並べる。"""
    directory = overrides_dir()
    if not os.path.isdir(directory):
        return []
    return sorted(name for name in os.listdir(directory)
                  if os.path.isfile(os.path.join(directory, name, "overrides.yaml")))


def config_root(package):
    """パッケージ名から機能別設定の根を引く。"""
    return os.path.join(_share(), CONFIG_DIRS[package])


def is_site(name):
    """none / 空は場所を選んでいない。"""
    return bool(name) and name.strip().lower() != "none"
