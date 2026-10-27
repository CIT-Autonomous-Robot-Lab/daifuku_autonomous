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
"""Pinned checkout acquisition and atomic world preparation without external services."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from daifuku_sim.vtc import vtc_assets
from daifuku_sim.vtc.vtc_checks import validate_world


def test_installed_git_revision_is_fetched_and_reused(tmp_path, monkeypatch) -> None:
    # Given: an installed Git dependency points at an older commit, not remote HEAD.
    upstream = tmp_path / 'upstream'
    subprocess.run(['git', 'init', '--quiet', str(upstream)], check=True)
    dockerfile = upstream / 'docker' / 'Dockerfile.gazebo'
    dockerfile.parent.mkdir()
    dockerfile.write_text('FROM scratch\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(upstream), 'add', '.'], check=True)
    commit = [
        'git', '-C', str(upstream), '-c', 'user.name=VTC tests',
        '-c', 'user.email=vtc-tests@example.invalid', 'commit', '--quiet', '-m',
    ]
    subprocess.run([*commit, 'pinned'], check=True)
    revision = subprocess.check_output(
        ['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True,
    ).strip()
    dockerfile.write_text('FROM changed\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(upstream), 'add', '.'], check=True)
    subprocess.run([*commit, 'newer'], check=True)
    metadata = json.dumps({
        'url': upstream.as_uri(), 'vcs_info': {'vcs': 'git', 'commit_id': revision},
    })
    monkeypatch.setattr(
        vtc_assets, 'distribution', lambda name: SimpleNamespace(read_text=lambda path: metadata),
    )
    monkeypatch.setattr(vtc_assets, 'RESOURCE_BASE', tmp_path / 'venv' / 'share')
    # When: assets are requested twice, with the source gone on the second request.
    root = vtc_assets.default_usim_root()
    upstream.rename(tmp_path / 'offline-upstream')
    second = vtc_assets.default_usim_root()
    # Then: only the pinned files were acquired, and reuse requires no network/source.
    assert root == second == vtc_assets.RESOURCE_BASE / revision
    assert (root / 'docker' / 'Dockerfile.gazebo').read_text(encoding='utf-8') == 'FROM scratch\n'
    assert not list(vtc_assets.RESOURCE_BASE.glob('.usim-*'))


@pytest.mark.parametrize('metadata', [None, '{"url":"file:///editable","dir_info":{}}'])
def test_non_git_install_is_rejected_without_checkout(tmp_path, monkeypatch, metadata) -> None:
    # Given: a distribution has no fixed Git provenance.
    monkeypatch.setattr(
        vtc_assets, 'distribution', lambda name: SimpleNamespace(read_text=lambda path: metadata),
    )
    monkeypatch.setattr(vtc_assets, 'RESOURCE_BASE', tmp_path / 'venv')
    # When / Then: automatic mode fails clearly instead of guessing a sibling checkout.
    with pytest.raises(RuntimeError, match='pinned Git source'):
        vtc_assets.default_usim_root()
    assert not vtc_assets.RESOURCE_BASE.exists()


@pytest.mark.parametrize('engine', ['podman', 'docker'])
def test_world_conversion_publishes_complete_assets_and_reuses_them(
    tmp_path, monkeypatch, engine,
) -> None:
    # Given: the installed usim fetcher and converter supply a tiny valid world.
    root = tmp_path / 'checkout'
    root.mkdir()
    commands = []

    def fetch(out):
        (out / 'LICENSE').write_text('test license', encoding='utf-8')
        (out / 'NOTICE').write_text('test notice', encoding='utf-8')

    def convert(command, *, check):
        commands.append(command)
        mount = next(value for value in command if value.endswith(',target=/vtc'))
        out = Path(mount.removeprefix('type=bind,source=').removesuffix(',target=/vtc'))
        manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
        assert any(name.endswith('.fbx') for name in manifest)
        (out / 'geometry.json').write_text('[]', encoding='utf-8')
        (out / 'origin.json').write_text('{"ground_z_m": 0.0}', encoding='utf-8')
        assert check

    monkeypatch.setattr(vtc_assets.fetch_vtc_world, 'fetch_assets', fetch)
    monkeypatch.setattr(vtc_assets.subprocess, 'run', convert)
    # When: the world is prepared twice.
    vtc_assets.prepare_default_world(root, engine)
    vtc_assets.prepare_default_world(root, engine)
    # Then: real SDF serialization and provenance are published once, with attribution.
    out = root / 'assets' / 'vtc'
    assert validate_world(out / 'world.sdf')['world_name'] == 'vtc'
    assert (out / 'LICENSE').read_text(encoding='utf-8') == 'test license'
    assert (out / 'NOTICE').is_file()
    assert json.loads((out / 'provenance.json').read_text())['commit'] == vtc_assets.fetch_vtc_world.COMMIT
    assert len(commands) == 1
    assert commands[0][0] == engine
    assert vtc_assets.fetch_vtc_world.BLENDER_IMAGE in commands[0]
    assert not list((root / 'assets').glob('.vtc-*'))


def test_failed_conversion_never_publishes_a_world(tmp_path, monkeypatch) -> None:
    # Given: acquisition succeeds, but the real converter process reports failure.
    root = tmp_path / 'checkout'
    root.mkdir()
    monkeypatch.setattr(vtc_assets.fetch_vtc_world, 'fetch_assets', lambda out: None)

    def fail(command, *, check):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(vtc_assets.subprocess, 'run', fail)
    # When / Then: a retry cannot mistake partial geometry for a usable cache.
    with pytest.raises(subprocess.CalledProcessError):
        vtc_assets.prepare_default_world(root, 'podman')
    assert not (root / 'assets' / 'vtc').exists()
    assert not list((root / 'assets').glob('.vtc-*'))
