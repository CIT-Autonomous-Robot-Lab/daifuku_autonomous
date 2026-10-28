# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Build the complete, packaged geographic VTC without loading its high-resolution world."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Final, TypedDict, cast
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from filelock import FileLock
from usim_env_vtc.fetch_vtc_world import BLENDER_IMAGE

WORLD_DIRECTORY: Final = 'vtc-complete-v1'


class SceneModel(TypedDict):
    name: str
    visual: str | None
    collision: str | None
    pose: list[float]
    scale: list[float]


class Scene(TypedDict):
    models: list[SceneModel]
    source_origin_m: list[float]
    camera_pose: list[float]


def prepare(root: Path, engine: str) -> None:
    with FileLock(str(root) + '.lock'):
        out = root / 'assets' / WORLD_DIRECTORY
        if (out / 'world.sdf').is_file():
            return
        out.parent.mkdir(parents=True, exist_ok=True)
        print(f'Preparing complete VTC in {out}', file=sys.stderr)
        with TemporaryDirectory(prefix='.vtc-', dir=out.parent) as temporary:
            staging = Path(temporary) / 'world'
            staging.mkdir()
            with ZipFile(Path(__file__).with_name('data') / 'complete.zip') as archive:
                archive.extractall(staging)
            script = Path(__file__).with_name('convert_complete.py')
            _ = subprocess.run([
                engine, 'run', '--rm', '--network', 'none',
                '--memory', '3g', '--memory-swap', '3g', '--cpus', '2',
                '--entrypoint', '/usr/bin/blender',
                '--mount', f'type=bind,source={staging},target=/vtc',
                '--mount', f'type=bind,source={script},target=/convert.py,readonly',
                BLENDER_IMAGE, '--background', '--factory-startup', '--disable-autoexec',
                '--python-exit-code', '1', '--python', '/convert.py',
            ], check=True)
            scene = cast(Scene, json.loads((staging / 'scene.json').read_text()))
            sdf = ET.Element('sdf', version='1.6')
            world = ET.SubElement(sdf, 'world', name='vtc')
            ET.SubElement(world, 'gravity').text = '0 0 -9.81'
            physics = ET.SubElement(world, 'physics', name='default', type='ode')
            ET.SubElement(physics, 'max_step_size').text = '0.001'
            ET.SubElement(physics, 'real_time_update_rate').text = '1000'
            light = ET.SubElement(world, 'light', name='sun', type='directional')
            ET.SubElement(light, 'diffuse').text = '0.8 0.8 0.8 1'
            ET.SubElement(light, 'specular').text = '0.2 0.2 0.2 1'
            ET.SubElement(light, 'direction').text = '-0.5 -0.1 -0.9'
            visual_scene = ET.SubElement(world, 'scene')
            ET.SubElement(visual_scene, 'ambient').text = '0.6 0.6 0.6 1'
            ET.SubElement(visual_scene, 'background').text = '0.7 0.8 0.9 1'
            for record in scene['models']:
                model = ET.SubElement(world, 'model', name=record['name'])
                ET.SubElement(model, 'static').text = 'true'
                ET.SubElement(model, 'pose').text = ' '.join(map(str, record['pose']))
                link = ET.SubElement(model, 'link', name='surface')
                for role in ('visual', 'collision'):
                    if record[role] is None:
                        continue
                    node = ET.SubElement(link, role, name=role)
                    mesh = ET.SubElement(ET.SubElement(node, 'geometry'), 'mesh')
                    ET.SubElement(mesh, 'uri').text = record[role]
                    ET.SubElement(mesh, 'scale').text = ' '.join(map(str, record['scale']))
            camera = ET.SubElement(ET.SubElement(world, 'gui'), 'camera', name='overview')
            ET.SubElement(camera, 'pose').text = ' '.join(map(str, scene['camera_pose']))
            ET.indent(sdf)
            ET.ElementTree(sdf).write(staging / 'world.sdf', encoding='utf-8', xml_declaration=True)
            _ = (staging / 'provenance.json').write_text(json.dumps({
                'scene': WORLD_DIRECTORY, **scene,
            }, indent=2))
            _ = (staging / 'origin.json').write_text(json.dumps({
                'source_origin_m': scene['source_origin_m'], 'ground_z_m': 0,
            }))
            _ = staging.replace(out)
