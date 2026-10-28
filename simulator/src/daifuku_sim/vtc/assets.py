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
"""Acquire checkout resources at the installed usim Git revision, inside the venv."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, TypedDict

from filelock import FileLock
from .complete_scene import prepare

RESOURCE_BASE: Final = Path(sys.prefix) / 'share' / 'daifuku-vtc'


class GitSource(TypedDict):
    vcs: str
    commit_id: str


class DirectUrl(TypedDict, total=False):
    url: str
    vcs_info: GitSource


def default_usim_root() -> Path:
    """Fetch the installed dependency's exact commit, independently of uv's cache."""
    metadata = distribution('usim').read_text('direct_url.json')
    if metadata is None:
        raise RuntimeError('automatic assets require usim installed from a pinned Git source')
    source: DirectUrl = json.loads(metadata)
    vcs = source.get('vcs_info')
    url = source.get('url')
    if (vcs is None or url is None or vcs['vcs'] != 'git'
            or not re.fullmatch(r'[0-9a-f]{40}', vcs['commit_id'])):
        raise RuntimeError('automatic assets require usim installed from a pinned Git source')
    root = RESOURCE_BASE / vcs['commit_id']
    RESOURCE_BASE.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root) + '.lock'):
        if not root.exists():
            print(f'Fetching usim assets at {vcs["commit_id"]} into {root}', file=sys.stderr)
            with TemporaryDirectory(prefix='.usim-', dir=RESOURCE_BASE) as temporary:
                checkout = Path(temporary) / 'checkout'
                _ = subprocess.run(['git', 'init', '--quiet', str(checkout)], check=True)
                _ = subprocess.run([
                    'git', '-C', str(checkout), 'fetch', '--quiet', '--depth=1',
                    url, vcs['commit_id'],
                ], check=True)
                _ = subprocess.run([
                    'git', '-C', str(checkout), 'checkout', '--quiet', '--detach',
                    vcs['commit_id'],
                ], check=True)
                _ = checkout.replace(root)
    return root


def prepare_default_world(root: Path, engine: str) -> None:
    """Prepare the complete geographic scene rather than the terrain-only Blender subset."""
    prepare(root, engine)
