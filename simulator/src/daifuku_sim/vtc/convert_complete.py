# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Blender-only conversion: shared prototypes and one bounded terrain tile at a time."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import bmesh
import bpy
from mathutils import Matrix, Vector
import numpy as np

OUT = Path('/vtc')
NS = '{http://www.collada.org/2005/11/COLLADASchema}'
ET.register_namespace('', NS[1:-1])


def export(path: Path) -> None:
    objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    for obj in objects:
        transform = obj.matrix_world.copy()
        obj.data.transform(transform)
        if transform.determinant() < 0:
            mesh = bmesh.new()
            mesh.from_mesh(obj.data)
            bmesh.ops.reverse_faces(mesh, faces=list(mesh.faces))
            mesh.to_mesh(obj.data)
            mesh.free()
        obj.parent = None
        obj.matrix_world = Matrix.Identity(4)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if len(objects) > 1:
        bpy.ops.object.join()
    bpy.ops.wm.collada_export(
        filepath=str(path), selected=True, triangulate=True,
        apply_modifiers=False, use_texture_copies=False,
    )
    document = ET.parse(path)
    textures = OUT / 'meshes' / 'textures'
    textures.mkdir(exist_ok=True)
    for image in document.getroot().iter(NS + 'image'):
        value = image.find(NS + 'init_from')
        if value is None or value.text is None:
            raise ValueError('COLLADA image has no source')
        filename = Path(value.text).name
        shutil.copyfile(OUT / 'terrain' / filename, textures / filename)
        value.text = 'textures/' + filename
    document.write(path, encoding='utf-8', xml_declaration=True)


def prototype(filename: str) -> str:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.usd_import(filepath=str(OUT / 'models' / filename))
    destination = OUT / 'meshes' / (Path(filename).stem + '.dae')
    export(destination)
    return 'meshes/' + destination.name


def main() -> None:
    source = json.loads((OUT / 'manifest.json').read_text())
    (OUT / 'meshes').mkdir()
    models = {}
    for name, item in source['models'].items():
        models[name] = {
            'visual': prototype(item['visual']),
            'collision': prototype(item['collision']) if item['collision'] else None,
        }
        print(f'CONVERT_MODEL {name}', flush=True)
    origin = np.array(source['origin'])
    records = []
    for actor in source['actors']:
        matrix = Matrix(actor['matrix']).transposed()
        location, rotation, scale = matrix.decompose()
        location -= Vector(origin)
        records.append({
            'name': actor['name'], **models[actor['model']],
            'pose': [*location, *rotation.to_euler('XYZ')], 'scale': list(scale),
        })
    for number, item in enumerate(source['extras']):
        records.append({
            'name': f'extra_{number}', 'visual': prototype(item['visual']),
            'collision': prototype(item['collision']) if item['collision'] else None,
            'pose': [*map(float, -origin), 0, 0, 0], 'scale': [1, 1, 1],
        })
    triangle_count = 0
    for number, tile in enumerate(source['terrain']):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        with np.load(OUT / tile['data']) as data:
            points = data['points']
        distance = [
            max(tile['min'][axis]-origin[axis], 0, origin[axis]-tile['max'][axis])
            for axis in range(2)
        ]
        if math.hypot(*distance) > 50:
            points = points[:, ::2, ::2]
        count, width = points.shape[0], points.shape[1]
        cells = np.arange((width-1)**2)
        a = (cells // (width-1))*width + cells % (width-1)
        faces = np.stack((a, a+width, a+1, a+1, a+width, a+width+1), axis=1).reshape(-1, 3)
        faces = np.concatenate([faces + index*width*width for index in range(count)])
        world_points = points.reshape(-1, 3)
        mesh = bpy.data.meshes.new(f'terrain_{number}')
        mesh.from_pydata((world_points-origin).tolist(), [], faces.tolist())
        obj = bpy.data.objects.new(f'terrain_{number}', mesh)
        bpy.context.scene.collection.objects.link(obj)
        uv = (world_points[:, :2]-np.array(tile['min'])[:2])/(np.array(tile['max'])[:2]-np.array(tile['min'])[:2])
        mesh.uv_layers.new().data.foreach_set('uv', uv[faces.ravel()].astype(np.float32).ravel())
        for polygon in mesh.polygons:
            polygon.use_smooth = True
        material = bpy.data.materials.new(f'ground_{number}')
        material.use_nodes = True
        image = material.node_tree.nodes.new('ShaderNodeTexImage')
        image.image = bpy.data.images.load(str(OUT / tile['texture']))
        shader = material.node_tree.nodes.get('Principled BSDF')
        material.node_tree.links.new(image.outputs['Color'], shader.inputs['Base Color'])
        shader.inputs['Roughness'].default_value = 0.85
        mesh.materials.append(material)
        filename = f'meshes/terrain_{number:02}.dae'
        export(OUT / filename)
        records.append({
            'name': f'terrain_{number:02}', 'visual': filename, 'collision': filename,
            'pose': [0]*6, 'scale': [1]*3,
        })
        triangle_count += len(faces)
        print(f'CONVERT_TERRAIN {number+1}/{len(source["terrain"])}', flush=True)
    (OUT / 'scene.json').write_text(json.dumps({
        'models': records, 'source_origin_m': source['origin'],
        'source_placements': len(source['actors']), 'terrain_triangles': triangle_count,
        'terrain_lod_m': source['terrain_lod_m'],
        'distant_terrain_lod_m': 0.8,
        'camera_pose': [-35, -70, 40, 0, 0.30, 0.55],
    }))
    print(f'COMPLETE_SCENE models={len(records)} triangles={triangle_count}', flush=True)


if __name__ == '__main__':
    main()
