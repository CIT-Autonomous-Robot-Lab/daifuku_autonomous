# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["numpy==1.26.4"]
# ///
"""書き出した完全メッシュからSDFを再構成する。USDやDockerは読み込まない。

uv run simulator/tools/convert_tsukuba2025.py simulator/runs/tsukuba2025-world
"""
from __future__ import annotations

import argparse
import json
import runpy
from pathlib import Path

write_world = runpy.run_path(
    str(Path(__file__).with_name('package_tsukuba2025.py')),
)['write_world']


def convert(out):
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema'] != 'daifuku-tsukuba2025-world/1':
        raise ValueError('Unsupported course world manifest')
    roots = {entry['path'] for entry in json.loads(
        Path(manifest['source_index']).read_text(encoding='utf-8'),
    )}
    for record in manifest['meshes']:
        if 'root' not in record:
            path = record['source_prim']
            while path and path not in roots:
                path = path.rpartition('/')[0]
            if not path:
                raise ValueError(f'No residency root for {record["source_prim"]}')
            record['root'] = path
        for role in ('visual', 'collision'):
            if record[role] and not (out / record[role]).is_file():
                raise FileNotFoundError(out / record[role])
    write_world(out, manifest['meshes'], manifest['transform']['source_origin_m'])
    assets = {record[role] for record in manifest['meshes'] for role in ('visual', 'collision')
              if record[role]}
    print(json.dumps({
        'world': str(out / 'world.sdf'), 'models': len({r['root'] for r in manifest['meshes']}),
        'visuals': sum(bool(r['visual']) for r in manifest['meshes']),
        'collisions': sum(bool(r['collision']) for r in manifest['meshes']),
        'triangles': manifest['triangle_count'], 'assets': len(assets),
        'asset_bytes': sum((out / asset).stat().st_size for asset in assets),
        'transform': manifest['transform'],
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    convert(parser.parse_args().directory)


if __name__ == '__main__':
    main()
