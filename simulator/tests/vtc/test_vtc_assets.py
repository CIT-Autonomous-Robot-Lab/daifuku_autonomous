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
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from daifuku_sim.vtc import assets
from daifuku_sim.vtc.checks import validate_world
from daifuku_sim.vtc.complete_scene import WORLD_DIRECTORY
from daifuku_sim.vtc import complete_scene


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
        assets, 'distribution', lambda name: SimpleNamespace(read_text=lambda path: metadata),
    )
    monkeypatch.setattr(assets, 'RESOURCE_BASE', tmp_path / 'venv' / 'share')
    # When: assets are requested twice, with the source gone on the second request.
    root = assets.default_usim_root()
    upstream.rename(tmp_path / 'offline-upstream')
    second = assets.default_usim_root()
    # Then: only the pinned files were acquired, and reuse requires no network/source.
    assert root == second == assets.RESOURCE_BASE / revision
    assert (root / 'docker' / 'Dockerfile.gazebo').read_text(encoding='utf-8') == 'FROM scratch\n'
    assert not list(assets.RESOURCE_BASE.glob('.usim-*'))


@pytest.mark.parametrize('metadata', [None, '{"url":"file:///editable","dir_info":{}}'])
def test_non_git_install_is_rejected_without_checkout(tmp_path, monkeypatch, metadata) -> None:
    # Given: a distribution has no fixed Git provenance.
    monkeypatch.setattr(
        assets, 'distribution', lambda name: SimpleNamespace(read_text=lambda path: metadata),
    )
    monkeypatch.setattr(assets, 'RESOURCE_BASE', tmp_path / 'venv')
    # When / Then: automatic mode fails clearly instead of guessing a sibling checkout.
    with pytest.raises(RuntimeError, match='pinned Git source'):
        assets.default_usim_root()
    assert not assets.RESOURCE_BASE.exists()


@pytest.mark.parametrize('engine', ['podman', 'docker'])
def test_world_conversion_publishes_complete_assets_and_reuses_them(
    tmp_path, monkeypatch, engine,
) -> None:
    # Given: an old terrain-only cache exists, and converters supply the full scene.
    root = tmp_path / 'checkout'
    root.mkdir()
    old_world = root / 'assets' / 'vtc' / 'world.sdf'
    old_world.parent.mkdir(parents=True)
    old_world.write_text('old terrain only', encoding='utf-8')
    commands = []

    def convert(command, *, check):
        commands.append(command)
        mount = next(value for value in command if value.endswith(',target=/vtc'))
        out = Path(mount.removeprefix('type=bind,source=').removesuffix(',target=/vtc'))
        manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
        assert len(manifest['actors']) == 1039
        assert len({actor['name'] for actor in manifest['actors']}) == 1039
        (out / 'meshes').mkdir()
        for name in ('collision', 'appearance'):
            (out / 'meshes' / f'{name}.dae').write_text('<COLLADA/>', encoding='utf-8')
        (out / 'scene.json').write_text(json.dumps({
            'models': [
                {'name': 'collision', 'visual': None, 'collision': 'meshes/collision.dae',
                 'pose': [1, 2, 3, 0, 0, 0], 'scale': [1, 2, 3]},
                {'name': 'appearance', 'visual': 'meshes/appearance.dae', 'collision': None,
                 'pose': [0]*6, 'scale': [1, 2, 3]},
            ], 'camera_pose': [10,-20,20,0,0.6,2], 'source_origin_m': manifest['origin'],
        }), encoding='utf-8')
        assert check

    monkeypatch.setattr(complete_scene.subprocess, 'run', convert)
    # When: the world is prepared twice.
    assets.prepare_default_world(root, engine)
    assets.prepare_default_world(root, engine)
    # Then: real SDF serialization and provenance are published once, with attribution.
    out = root / 'assets' / WORLD_DIRECTORY
    assert validate_world(out / 'world.sdf')['world_name'] == 'vtc'
    world = ET.parse(out / 'world.sdf')
    assert world.find("world/model[@name='collision']/link/visual") is None
    assert world.find("world/model[@name='collision']/link/collision") is not None
    assert world.find("world/model[@name='appearance']/link/visual") is not None
    assert world.find("world/model[@name='appearance']/link/collision") is None
    assert {node.text for node in world.findall('.//mesh/scale')} == {'1 2 3'}
    pose = world.find("world/model[@name='collision']/pose")
    assert pose is not None
    assert pose.text == '1 2 3 0 0 0'
    assert (out / 'LICENSE-MIT').is_file()
    assert (out / 'NOTICE').is_file()
    assert json.loads((out / 'provenance.json').read_text())['scene'] == WORLD_DIRECTORY
    assert old_world.read_text(encoding='utf-8') == 'old terrain only'
    assert len(commands) == 1
    assert commands[0][0] == engine
    assert complete_scene.BLENDER_IMAGE in commands[0]
    assert not list((root / 'assets').glob('.vtc-*'))


def test_failed_conversion_never_publishes_a_world(tmp_path, monkeypatch) -> None:
    # Given: acquisition succeeds, but the real converter process reports failure.
    root = tmp_path / 'checkout'
    root.mkdir()

    def fail(command, *, check):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(complete_scene.subprocess, 'run', fail)
    # When / Then: a retry cannot mistake partial geometry for a usable cache.
    with pytest.raises(subprocess.CalledProcessError):
        assets.prepare_default_world(root, 'podman')
    assert not (root / 'assets' / WORLD_DIRECTORY).exists()
    assert not list((root / 'assets').glob('.vtc-*'))


def test_complete_bundle_preserves_trees_poles_and_all_actor_references() -> None:
    with ZipFile(Path(complete_scene.__file__).with_name('data') / 'complete.zip') as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert len(manifest['actors']) == 1039
        assert len(manifest['terrain']) == 64
        assert sum('Bush' in actor['source'] for actor in manifest['actors']) == 331
        assert sum('Cylinder' in actor['source'] for actor in manifest['actors']) == 79
        assert all(actor['model'] in manifest['models'] for actor in manifest['actors'])
        for model in manifest['models'].values():
            assert 'models/' + model['visual'] in archive.namelist()
            if model['collision']:
                assert 'models/' + model['collision'] in archive.namelist()
        inverted = next(a for a in manifest['actors'] if a['name'].endswith('_E1254_Shape_TriPyramid2'))
        assert inverted['matrix'][2][2] == 0.75
        assert inverted['model'].endswith('_mirror_z')
