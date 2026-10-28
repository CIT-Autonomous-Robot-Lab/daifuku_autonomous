# Copyright 2026 Keita Sekiguchi / nop
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["numpy==1.26.4", "Pillow==11.3.0", "usd-core==25.5.1"]
# ///
# Run: uv run simulator/tools/package_vtc_complete.py --source <authored-world-dir> --out <archive>
"""Package the complete local VTC in bounded tiles, without modifying its originals."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from PIL import Image
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt


@dataclass(frozen=True, slots=True)
class Model:
    visual: str
    collision: str | None


class TerrainTile(TypedDict):
    data: str
    texture: str
    min: list[float]
    max: list[float]


def model(source: Path, destination: Path, mirror: bool = False) -> Model:
    """Retain geometry, bake display pigments, and keep authored collision flags."""
    layer = Sdf.Layer.CreateAnonymous()
    layer.TransferContent(Sdf.Layer.FindOrOpen(str(source.resolve())))
    stage = Usd.Stage.Open(layer)
    if mirror:
        root = UsdGeom.Xformable(stage.GetDefaultPrim())
        root.MakeMatrixXform().Set(Gf.Matrix4d().SetScale(Gf.Vec3d(1, 1, -1)))
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Gprim):
            continue
        pigment = UsdGeom.Gprim(prim).GetDisplayColorPrimvar().Get()
        if pigment is None or not len(pigment):
            continue
        color = np.asarray(pigment).mean(axis=0)
        material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        if not material:
            material = UsdShade.Material.Define(stage, str(prim.GetPath()) + '/ColorMaterial')
            shader = UsdShade.Shader.Define(stage, str(material.GetPath()) + '/Surface')
            shader.CreateIdAttr('UsdPreviewSurface')
            material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)
        else:
            shader, _, _ = material.ComputeSurfaceSource()
        value = shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f)
        value.DisconnectSource()
        value.Set(Gf.Vec3f(*map(float, color)))
    # Gazebo Classic's COLLADA path does not carry USD's two-sided leaf flag.
    # Expand the reverse faces, retaining the same positions and pigments.
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        if not mesh.GetDoubleSidedAttr().Get():
            continue
        faces = np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3)
        normals = np.asarray(mesh.GetNormalsAttr().Get()).reshape(-1, 3, 3)
        mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray([3] * (len(faces) * 2)))
        mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(
            np.concatenate((faces, faces[:, ::-1])).astype(np.int32).ravel(),
        ))
        mesh.GetNormalsAttr().Set(Vt.Vec3fArray.FromNumpy(
            np.concatenate((normals, -normals[:, ::-1])).reshape(-1, 3).astype(np.float32),
        ))
        mesh.GetDoubleSidedAttr().Set(False)
    physical = Sdf.Layer.CreateAnonymous()
    physical.TransferContent(layer)
    collision = Usd.Stage.Open(physical)
    solids = 0
    for prim in [p for p in collision.Traverse() if p.IsA(UsdGeom.Gprim)]:
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
            solids += 1
        else:
            collision.RemovePrim(prim.GetPath())
    for prim in [p for p in stage.Traverse() if p.IsA(UsdGeom.Gprim)]:
        if UsdGeom.Imageable(prim).ComputeVisibility() == 'invisible':
            stage.RemovePrim(prim.GetPath())
    destination.parent.mkdir(parents=True, exist_ok=True)
    layer.Export(str(destination))
    collider = destination.with_name(destination.stem + '_collision.usdc')
    if solids:
        physical.Export(str(collider))
    return Model(destination.name, collider.name if solids else None)


def terrain(source: Path, archive: ZipFile) -> tuple[list[TerrainTile], float]:
    """Visit one original tile at a time; keep source heights at approximately 0.4 m."""
    index = json.loads((source / 'world.index.json').read_text())
    entries = []
    spawn_z = None
    sample = np.r_[np.arange(0, 127, 4), 126]
    for number, entry in enumerate(e for e in index if e['path'].startswith('/World/Terrain/')):
        stage = Usd.Stage.Open(str((source / entry['asset']).resolve()))
        transforms = UsdGeom.XformCache()
        lattices = []
        atlas = Image.new('RGB', (512, 512))
        low, high = np.array(entry['min']), np.array(entry['max'])
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(prim)
            points = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
            matrix = np.array(transforms.GetLocalToWorldTransform(prim))
            world = (points @ matrix[:3, :3] + matrix[3, :3]).reshape(127, 127, 3)
            lattice = world[sample[:, None], sample].astype(np.float32)
            lattices.append(lattice)
            if world[..., 0].min() <= 265 <= world[..., 0].max() and world[..., 1].min() <= -200 <= world[..., 1].max():
                col = int(np.clip(np.searchsorted(lattice[0, :, 0], 265) - 1, 0, 31))
                row = int(np.clip(np.searchsorted(-lattice[:, 0, 1], 200) - 1, 0, 31))
                a, b, c, d = lattice[row, col], lattice[row, col+1], lattice[row+1, col], lattice[row+1, col+1]
                u, v = (265-a[0])/(b[0]-a[0]), (-200-a[1])/(c[1]-a[1])
                spawn_z = float(a[2] + u*(b[2]-a[2]) + v*(c[2]-a[2]) if u+v <= 1 else d[2] + (1-u)*(c[2]-d[2]) + (1-v)*(b[2]-d[2]))
            colors = np.clip(np.asarray(mesh.GetDisplayColorPrimvar().Get()).reshape(127, 127, 3), 0, 1)
            srgb = np.where(colors <= 0.0031308, colors * 12.92, 1.055 * colors ** (1/2.4) - 0.055)
            patch = Image.fromarray(np.rint(srgb * 255).astype(np.uint8))
            x0 = round((world[..., 0].min()-low[0])/(high[0]-low[0])*511)
            x1 = round((world[..., 0].max()-low[0])/(high[0]-low[0])*511)
            y0 = round((high[1]-world[..., 1].max())/(high[1]-low[1])*511)
            y1 = round((high[1]-world[..., 1].min())/(high[1]-low[1])*511)
            atlas.paste(patch.resize((x1-x0+1, y1-y0+1), Image.Resampling.LANCZOS), (x0, y0))
        data = io.BytesIO()
        np.savez(data, points=np.stack(lattices))
        name = f'terrain/tile_{number:02}'
        archive.writestr(name + '.npz', data.getvalue())
        image = io.BytesIO()
        atlas.save(image, format='PNG')
        archive.writestr(name + '.png', image.getvalue())
        entries.append({'data': name + '.npz', 'texture': name + '.png', 'min': entry['min'], 'max': entry['max']})
        print(f'PACK_TERRAIN {number+1}/64', flush=True)
        del stage, transforms, lattices
    if spawn_z is None:
        raise RuntimeError('The original VTC terrain does not contain the selected spawn')
    return entries, spawn_z


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    report = json.loads((source / 'authorship.json').read_text())
    index = json.loads((source / 'world.index.json').read_text())
    world = Usd.Stage.Open(str(source / 'world.usda'), Usd.Stage.LoadNone)
    transforms = UsdGeom.XformCache()
    actors = []
    mirrored = set()
    for item in report['placements']:
        matrix = np.array(transforms.GetLocalToWorldTransform(world.GetPrimAtPath(item['path'])))
        asset = Path(item['authoredModel']).stem
        if np.linalg.det(matrix[:3, :3]) < 0:
            matrix[2, :3] *= -1
            mirrored.add(asset)
            asset += '_mirror_z'
        actors.append({'name': item['path'].removeprefix('/World/').replace('/', '_'), 'model': asset, 'matrix': matrix.tolist(), 'source': item['sourceModel']})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temporary, ZipFile(args.out, 'w', ZIP_DEFLATED) as archive:
        temporary = Path(temporary)
        models = {}
        for original in sorted({Path(p['authoredModel']).stem for p in report['placements']}):
            src = source / 'models' / (original + '.usdc')
            expected = report['hashes']['models/' + src.name]
            if hashlib.sha256(src.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f'Original model hash changed: {src}')
            for reflected in (False, True) if original in mirrored else (False,):
                name = original + ('_mirror_z' if reflected else '')
                result = model(src, temporary / (name + '.usdc'), reflected)
                archive.write(temporary / result.visual, 'models/' + result.visual)
                if result.collision:
                    archive.write(temporary / result.collision, 'models/' + result.collision)
                models[name] = asdict(result)
        extras = []
        for number, entry in enumerate(e for e in index if e['asset'].startswith('posts_') or e['asset'] == 'bsp.usdc'):
            result = model(source / entry['asset'], temporary / f'extra_{number}.usdc')
            archive.write(temporary / result.visual, 'models/' + result.visual)
            if result.collision:
                archive.write(temporary / result.collision, 'models/' + result.collision)
            extras.append(asdict(result))
        tiles, spawn_z = terrain(source, archive)
        manifest = {
            'schema': 'daifuku-vtc-complete/1', 'origin': [265, -200, spawn_z],
            'actors': actors, 'models': models, 'extras': extras, 'terrain': tiles,
            'terrain_lod_m': 0.4, 'source_placements': report['placementCount'],
            'source_revision': 'c503a8827cb1134a4931fd93cb1af093df5ba35f',
            'model_artwork': 'Original procedural usim recipes; source geometry/placements retained separately.',
        }
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.write(source / 'NOTICE', 'NOTICE')
        archive.write(source / 'LICENSE-MIT', 'LICENSE-MIT')
        archive.write(source / 'LICENSE-APACHE2', 'LICENSE-APACHE2')
    print(f'COMPLETE_BUNDLE actors={len(actors)} models={len(models)} tiles={len(tiles)} bytes={args.out.stat().st_size}', flush=True)


if __name__ == '__main__':
    main()
