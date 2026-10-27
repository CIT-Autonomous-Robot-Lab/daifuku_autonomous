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
from usim_env_vtc import fetch_vtc_world

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
    """Use installed usim's verified VTC sources and pinned CPU Blender converter."""
    with FileLock(str(root) + '.lock'):
        out = root / 'assets' / 'vtc'
        if (out / 'world.sdf').is_file():
            return
        out.parent.mkdir(parents=True, exist_ok=True)
        print(f'Preparing VTC world in {out} with {engine}', file=sys.stderr)
        with TemporaryDirectory(prefix='.vtc-', dir=out.parent) as temporary:
            staging = Path(temporary) / 'world'
            staging.mkdir()
            fetch_vtc_world.fetch_assets(staging)
            _ = (staging / 'manifest.json').write_text(
                json.dumps(fetch_vtc_world.MANIFEST, indent=2), encoding='utf-8',
            )
            script = Path(fetch_vtc_world.__file__).with_name('convert_blender.py')
            _ = subprocess.run([
                engine, 'run', '--rm', '--network', 'none',
                '--entrypoint', '/usr/bin/blender',
                '--mount', f'type=bind,source={staging},target=/vtc',
                '--mount', f'type=bind,source={script},target=/convert.py,readonly',
                fetch_vtc_world.BLENDER_IMAGE,
                '--background', '--factory-startup', '--disable-autoexec',
                '--python-exit-code', '1', '--python', '/convert.py',
            ], check=True)
            meshes = json.loads((staging / 'geometry.json').read_text(encoding='utf-8'))
            origin = json.loads((staging / 'origin.json').read_text(encoding='utf-8'))
            _ = fetch_vtc_world.write_sdf(staging, meshes, origin['ground_z_m'])
            _ = (staging / 'provenance.json').write_text(json.dumps({
                'repository': fetch_vtc_world.REPOSITORY,
                'commit': fetch_vtc_world.COMMIT,
                'blender_image': fetch_vtc_world.BLENDER_IMAGE,
                **origin,
                'sources': {
                    path: {'sha256': digest, 'bytes': size}
                    for path, (digest, size) in fetch_vtc_world.MANIFEST.items()
                },
            }, indent=2) + '\n', encoding='utf-8')
            _ = staging.replace(out)
