# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["numpy==1.26.4", "usd-core==25.5.1"]
# ///
"""合成済み2025コースをCPUで出力する。USD依存はこのツール環境だけに置く。

uv run simulator/tools/package_tsukuba2025.py --out simulator/runs/tsukuba2025-gazebo

sourceXY = originXY + R(source_yaw_rad) @ gazeboXY、sourceZ = originZ + gazeboZ。
originZ は spawn XY における合成後の地形三角形から求める。course.spawn の Z は
Isaac の機体ルート高さなので地面の基準には使わない。Gazebo の base_footprint は Z=0。
residency index のルートだけを私有sessionで有効化し、その下の無効化は保持する。
既定では全順路の75m以内（センサ70m+位置誤差余裕5m）と交わり得る
メッシュを丸ごと保持する。間引きはしない。
--route-margin 0 は回廊選択を無効にして市街全体を出力する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGE = ROOT / (
    'simulator/runs/vtc/isaac-course-20261016/course-2025/'
    'frontage-model-v3/runtime-world.usda'
)
DEFAULT_COURSE = ROOT / 'src/daifuku_maps/maps/tsukuba2025/course.json'


def gazebo_points(points, origin, yaw):
    points = np.asarray(points, dtype=np.float64) - origin
    c, s = math.cos(yaw), math.sin(yaw)
    return points @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def route_distance(point, route):
    starts, ends = route[:-1, :2], route[1:, :2]
    delta = ends - starts
    lengths = np.einsum('ij,ij->i', delta, delta)
    u = np.clip(np.sum((point[:2] - starts) * delta, axis=1)
                / np.maximum(lengths, 1e-20), 0, 1)
    return float(np.linalg.norm(starts + delta * u[:, None] - point[:2], axis=1).min())


def near_route(low, high, route, margin):
    if margin == 0:
        return True
    low, high = np.asarray(low), np.asarray(high)
    # 外接円による保守判定。回廊内の面を落とさず、境界メッシュも切断しない。
    return route_distance((low + high) / 2, route) <= margin + np.linalg.norm(
        (high - low)[:2] / 2,
    )


def triangle_height(points, faces, xy):
    triangle = points[faces]
    a, b, c = triangle[:, 0], triangle[:, 1], triangle[:, 2]
    ab, ac = b[:, :2] - a[:, :2], c[:, :2] - a[:, :2]
    delta = np.asarray(xy) - a[:, :2]
    determinant = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
    valid = np.abs(determinant) > 1e-12
    u = np.zeros(len(faces))
    v = np.zeros(len(faces))
    u[valid] = (delta[valid, 0] * ac[valid, 1]
                - delta[valid, 1] * ac[valid, 0]) / determinant[valid]
    v[valid] = (ab[valid, 0] * delta[valid, 1]
                - ab[valid, 1] * delta[valid, 0]) / determinant[valid]
    valid &= (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7)
    return (a[:, 2] + u * (b[:, 2] - a[:, 2])
            + v * (c[:, 2] - a[:, 2]))[valid].tolist()


@contextmanager
def resident(stage, path):
    """usim WorldResidency と同じルート有効化を一つずつ行う。原本は保存しない。"""
    from pxr import Usd
    stage.SetEditTarget(stage.GetSessionLayer())
    prim = stage.GetPrimAtPath(path)
    if not prim:
        raise ValueError(f'Index root missing from composed stage: {path}')
    active = prim.IsActive()
    prim.SetActive(True)
    stage.Load(path, Usd.LoadWithoutDescendants)
    try:
        yield stage.GetPrimAtPath(path)
    finally:
        stage.Unload(path)
        prim.SetActive(active)


def meshes(root):
    from pxr import Usd, UsdGeom
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdGeom.Mesh):
            yield UsdGeom.Mesh(prim)
        elif prim.IsA(UsdGeom.Gprim):
            raise ValueError(f'Unsupported geometric primitive: {prim.GetPath()}')


def triangulation(mesh):
    """三角形・四角形をベクトル化し、一般多角形は射影面でear clippingする。"""
    counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=np.int32)
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int32)
    holes = set(mesh.GetHoleIndicesAttr().Get() or [])
    if np.all(counts == 3):
        keep = np.array([i not in holes for i in range(len(counts))])
        corners = np.arange(len(indices)).reshape(-1, 3)[keep]
        return indices[corners], np.arange(len(counts))[keep], corners
    vertices = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
    triangles, face_ids, corners = [], [], []
    offset = 0
    for face_id, count in enumerate(counts):
        polygon = indices[offset:offset + count]
        if face_id not in holes:
            remaining = list(range(int(count)))
            points = vertices[polygon]
            normal = np.cross(points[1] - points[0], points[2] - points[0])
            projected = np.delete(points, np.argmax(np.abs(normal)), axis=1)
            area = np.sum(projected[:, 0] * np.roll(projected[:, 1], -1)
                          - projected[:, 1] * np.roll(projected[:, 0], -1))
            direction = 1 if area >= 0 else -1

            def cross(a, b, c):
                u, v = b - a, c - a
                return u[0] * v[1] - u[1] * v[0]

            while len(remaining) > 3:
                for k, b in enumerate(remaining):
                    a, c = remaining[k - 1], remaining[(k + 1) % len(remaining)]
                    if direction * cross(projected[a], projected[b], projected[c]) <= 1e-14:
                        continue
                    if any(
                        all(direction * cross(projected[u], projected[v], projected[p])
                            >= -1e-14 for u, v in ((a, b), (b, c), (c, a)))
                        for p in remaining if p not in (a, b, c)
                    ):
                        continue
                    corner = np.array([a, b, c]) + offset
                    corners.append(corner)
                    triangles.append(indices[corner])
                    face_ids.append(face_id)
                    remaining.pop(k)
                    break
                else:
                    raise ValueError(f'Cannot triangulate source polygon: {mesh.GetPath()}')
            corner = np.array(remaining) + offset
            corners.append(corner)
            triangles.append(indices[corner])
            face_ids.append(face_id)
        offset += count
    return (np.asarray(triangles, dtype=np.int32), np.asarray(face_ids),
            np.asarray(corners, dtype=np.int32))


def mesh_arrays(mesh, cache):
    points = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
    faces, _, _ = triangulation(mesh)
    matrix = np.asarray(cache.GetLocalToWorldTransform(mesh.GetPrim()))
    points = points @ matrix[:3, :3] + matrix[3, :3]
    reverse = np.linalg.det(matrix[:3, :3]) < 0
    reverse ^= mesh.GetOrientationAttr().Get() == 'leftHanded'
    if reverse:
        faces = faces[:, ::-1].copy()
    return points, faces


def collides(prim):
    from pxr import UsdPhysics
    return (prim.HasAPI(UsdPhysics.CollisionAPI)
            and bool(UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()))


def write_dae(path, points, faces, colors, double_sided=False):
    """一メッシュ分だけ保持し、Gazebo対応の材質色と法線を直接書く。"""
    diffuse = ' '.join(map(str, [*np.mean(colors, axis=0), 1.0]))
    normals = np.zeros_like(points)
    triangles = points[faces]
    normal = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    for corner in range(3):
        np.add.at(normals, faces[:, corner], normal)
    length = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(length[:, None], 1e-20)
    if double_sided:
        faces = np.concatenate((faces, faces[:, ::-1] + len(points)))
        points = np.concatenate((points, points))
        normals = np.concatenate((normals, -normals))
        colors = np.concatenate((colors, colors))
    with path.open('w', encoding='utf-8', newline='\n') as out:
        out.write('<?xml version="1.0" encoding="utf-8"?>\n'
                  '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">'
                  '<asset><created>2026-10-28T00:00:00Z</created>'
                  '<modified>2026-10-28T00:00:00Z</modified>'
                  '<unit meter="1" name="meter"/><up_axis>Z_UP</up_axis></asset>'
                  '<library_effects><effect id="pigment"><profile_COMMON><technique sid="common">'
                  f'<lambert><diffuse><color>{diffuse}</color></diffuse></lambert>'
                  '</technique></profile_COMMON></effect></library_effects>'
                  '<library_materials><material id="material"><instance_effect url="#pigment"/>'
                  '</material></library_materials><library_geometries><geometry id="geometry">'
                  '<mesh>')
        for name, values, parameters in (
            ('position', points, ('X', 'Y', 'Z')),
            ('normal', normals, ('X', 'Y', 'Z')),
        ):
            out.write(f'<source id="{name}"><float_array id="{name}-array" count="{values.size}">')
            np.savetxt(out, values, fmt='%.9g')
            out.write(f'</float_array><technique_common><accessor source="#{name}-array" '
                      f'count="{len(values)}" stride="3">')
            for parameter in parameters:
                out.write(f'<param name="{parameter}" type="float"/>')
            out.write('</accessor></technique_common></source>')
        out.write('<vertices id="vertices"><input semantic="POSITION" source="#position"/>'
                  '</vertices>')
        out.write(f'<triangles count="{len(faces)}" material="surface">'
                  '<input semantic="VERTEX" source="#vertices" offset="0"/>'
                  '<input semantic="NORMAL" source="#normal" offset="1"/><p>')
        # Gazebo Classic consumes one tuple slot per input. Explicit offsets also
        # keep position/normal indices unambiguous for other COLLADA readers.
        np.savetxt(out, np.repeat(faces, 2, axis=1), fmt='%d')
        out.write('</p></triangles></mesh></geometry></library_geometries>'
                  '<library_visual_scenes><visual_scene id="scene"><node id="mesh">'
                  '<instance_geometry url="#geometry"><bind_material><technique_common>'
                  '<instance_material symbol="surface" target="#material"/>'
                  '</technique_common></bind_material></instance_geometry></node>'
                  '</visual_scene></library_visual_scenes>'
                  '<scene><instance_visual_scene url="#scene"/></scene></COLLADA>')


def vertex_colors(mesh, points, faces):
    """元の頂点色を保持する。非頂点色もコーナーを展開して損失なく扱う。"""
    pigment = mesh.GetDisplayColorPrimvar()
    values = pigment.ComputeFlattened()
    if values is None or not len(values):
        return points, faces, np.full_like(points, 0.7)
    colors = np.asarray(values, dtype=np.float64)
    interpolation = pigment.GetInterpolation()
    if interpolation == 'constant':
        return points, faces, np.broadcast_to(colors[0], points.shape).copy()
    if interpolation in ('vertex', 'varying'):
        return points, faces, colors
    original, face_ids, corners = triangulation(mesh)
    if interpolation == 'uniform':
        colors = np.repeat(colors[face_ids], 3, axis=0)
    elif interpolation == 'faceVarying':
        colors = colors[corners]
        if not np.array_equal(faces, original):
            colors = colors[:, ::-1]
        colors = colors.reshape(-1, 3)
    elif interpolation != 'faceVarying':
        raise ValueError(f'Unsupported color interpolation: {mesh.GetPath()}: {interpolation}')
    # この入力の地形・植生はvertex、定色モデルはconstant。異種入力も平均に崩さない。
    return points[faces].reshape(-1, 3), np.arange(faces.size).reshape(-1, 3), colors


def measure_ground(stage, entries, spawn):
    from pxr import UsdGeom
    heights = []
    for entry in entries:
        if not entry['path'].startswith('/World/Terrain/'):
            continue
        if not all(entry['min'][i] - 1e-5 <= spawn[i] <= entry['max'][i] + 1e-5
                   for i in (0, 1)):
            continue
        with resident(stage, entry['path']) as root:
            cache = UsdGeom.XformCache()
            for mesh in meshes(root):
                if collides(mesh.GetPrim()):
                    points, faces = mesh_arrays(mesh, cache)
                    heights.extend(triangle_height(points, faces, spawn[:2]))
    if not heights:
        raise ValueError('No composed collision terrain triangle at course spawn')
    return max(heights)


def write_world(out, records, origin):
    sdf = ET.Element('sdf', version='1.6')
    world = ET.SubElement(sdf, 'world', name='tsukuba2025')
    ET.SubElement(world, 'gravity').text = '0 0 -9.81'
    physics = ET.SubElement(world, 'physics', name='default', type='ode')
    ET.SubElement(physics, 'max_step_size').text = '0.001'
    # 壁時計周期の追従器と合わせ、1msの積分刻みで実時間を超えない。
    ET.SubElement(physics, 'real_time_update_rate').text = '1000'
    scene = ET.SubElement(world, 'scene')
    ET.SubElement(scene, 'ambient').text = '0.6 0.6 0.6 1'
    ET.SubElement(scene, 'background').text = '0.7 0.8 0.9 1'
    light = ET.SubElement(world, 'light', name='sun', type='directional')
    ET.SubElement(light, 'diffuse').text = '0.8 0.8 0.8 1'
    ET.SubElement(light, 'direction').text = '-0.5 -0.1 -0.9'
    groups = {}
    for record in records:
        groups.setdefault(record['root'], []).append(record)
    for number, surfaces in enumerate(groups.values()):
        model = ET.SubElement(world, 'model', name=f'surface_{number:05d}')
        ET.SubElement(model, 'static').text = 'true'
        link = ET.SubElement(model, 'link', name='surface')
        for surface_number, record in enumerate(surfaces):
            # 原本のactor/tile単位を静的linkにまとめ、葉ごとの剛体を増やさない。
            position = np.asarray(record['position']) - [0, 0, origin[2]]
            for role in ('visual', 'collision'):
                asset = record.get(role)
                if asset:
                    node = ET.SubElement(link, role, name=f'{role}_{surface_number}')
                    ET.SubElement(node, 'pose').text = ' '.join(
                        map(str, [*position, 0, 0, 0]),
                    )
                    geometry = ET.SubElement(ET.SubElement(node, 'geometry'), 'mesh')
                    ET.SubElement(geometry, 'uri').text = asset
    ET.indent(sdf)
    ET.ElementTree(sdf).write(out / 'world.sdf', encoding='utf-8', xml_declaration=True)


def export(stage_path, course_path, index_path, out, margin=75.0, replace=False):
    from pxr import Usd, UsdGeom
    course = json.loads(course_path.read_text(encoding='utf-8'))
    if not 2 <= len(course['goals']) <= 30:
        raise ValueError('Expected at most 30 configured course goals')
    spawn, yaw = course['spawn'], course['spawn_yaw']
    # Goal thinning must not shrink the physical scene's original route envelope.
    route = np.asarray(course['world_route'], dtype=np.float64)
    entries = json.loads(index_path.read_text(encoding='utf-8'))
    if (out / 'world.sdf').exists() and not replace:
        raise FileExistsError(f'Use a new output directory: {out}')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'meshes').mkdir(exist_ok=True)
    stage = Usd.Stage.Open(str(stage_path.resolve()), Usd.Stage.LoadNone)
    if UsdGeom.GetStageUpAxis(stage) != 'Z' or UsdGeom.GetStageMetersPerUnit(stage) != 1:
        raise ValueError('Expected the native metre-scale Z-up adapted course')
    ground = measure_ground(stage, entries, spawn)
    origin = [spawn[0], spawn[1], ground]
    records = []
    assets = {}
    triangles = 0
    selected = [e for e in entries if near_route(e['min'], e['max'], route, margin)]
    for number, entry in enumerate(selected):
        with resident(stage, entry['path']) as root:
            cache = UsdGeom.XformCache()
            for mesh in meshes(root):
                prim = mesh.GetPrim()
                visible = UsdGeom.Imageable(prim).ComputeVisibility() != 'invisible'
                collision = collides(prim)
                if not (visible or collision):
                    continue
                points, faces = mesh_arrays(mesh, cache)
                if not len(faces) or not near_route(points.min(axis=0), points.max(axis=0),
                                                  route, margin):
                    continue
                # XYを原点回りへ回しZは保持する。任意の非一様scale/鏡映も上で焼き込む。
                points = gazebo_points(points, [spawn[0], spawn[1], 0], yaw)
                center = (points.min(axis=0) + points.max(axis=0)) / 2
                points -= center
                points, faces, colors = vertex_colors(mesh, points, faces)
                digest = hashlib.sha256()
                for array in (points, faces, colors):
                    digest.update(np.ascontiguousarray(array).tobytes())
                sided = bool(mesh.GetDoubleSidedAttr().Get())
                key = digest.hexdigest()
                collision_asset = f'meshes/{key}.dae'
                if key not in assets:
                    write_dae(out / collision_asset, points, faces, colors)
                    assets[key] = collision_asset
                visual_asset = collision_asset
                if visible and sided:
                    visual_asset = f'meshes/{key}-double.dae'
                    if key + '-double' not in assets:
                        write_dae(out / visual_asset, points, faces, colors, True)
                        assets[key + '-double'] = visual_asset
                records.append({
                    'source_prim': str(prim.GetPath()), 'root': entry['path'],
                    'position': center.tolist(),
                    'visual': visual_asset if visible else None,
                    'collision': collision_asset if collision else None,
                    'triangles': len(faces),
                })
                triangles += len(faces)
        print(f'EXPORT_ROOT {number + 1}/{len(selected)} meshes={len(records)} '
              f'triangles={triangles}', flush=True)
    if not records:
        raise ValueError('No composed geometry exported')
    transform = {
        'schema': 'daifuku-tsukuba2025-transform/1',
        'source_origin_m': origin, 'source_yaw_rad': yaw,
        'source_spawn_m': spawn, 'source_spawn_clearance_m': spawn[2] - ground,
        'gazebo_base_footprint_spawn_m': [0, 0, 0],
        'source_from_gazebo': 'sourceXY=originXY+R(yaw)*gazeboXY; sourceZ=originZ+gazeboZ',
        'source_heading_from_gazebo': 'sourceYaw=gazeboYaw+source_yaw_rad',
        'course_sha256': hashlib.sha256(course_path.read_bytes()).hexdigest(),
        'source_stage': str(stage_path.resolve()),
        'source_index_sha256': hashlib.sha256(index_path.read_bytes()).hexdigest(),
    }
    fingerprints = []
    for layer in stage.GetLayerStack():
        if layer == stage.GetSessionLayer():
            continue
        digest = hashlib.sha256()
        with Path(layer.realPath).open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        fingerprints.append({'path': layer.realPath, 'sha256': digest.hexdigest()})
    transform['source_layers'] = fingerprints
    transform['source_stage_fingerprint'] = hashlib.sha256(
        json.dumps(fingerprints, sort_keys=True).encode(),
    ).hexdigest()
    manifest = {
        'schema': 'daifuku-tsukuba2025-world/1', 'transform': transform,
        'source_stage': str(stage_path.resolve()), 'source_index': str(index_path.resolve()),
        'course_sha256': hashlib.sha256(course_path.read_bytes()).hexdigest(),
        'goals': course['goals'], 'route_margin_m': margin,
        'sensor_reach_m': 70, 'localization_margin_m': margin - 70,
        'selection': 'whole meshes conservatively intersecting the complete route envelope',
        'topology': 'native triangles, no simplification, no synthetic support',
        'visual_color': 'per-mesh mean diffuse; Gazebo Classic does not support COLLADA vertex COLOR',
        'activation': 'indexed residency roots only; descendant active and collision flags preserved',
        'indexed_roots': len(entries), 'selected_roots': len(selected),
        'triangle_count': triangles, 'asset_count': len(assets), 'meshes': records,
        'layers': [layer.identifier for layer in stage.GetLayerStack()
                   if layer != stage.GetSessionLayer()],
    }
    for name, value in (('transform.json', transform), ('manifest.json', manifest),
                        ('course.json', course)):
        (out / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    write_world(out, records, origin)
    print(f'WORLD_READY {out / "world.sdf"} ground={ground} triangles={triangles}', flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, default=DEFAULT_STAGE)
    parser.add_argument('--course', type=Path, default=DEFAULT_COURSE)
    parser.add_argument('--index', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--route-margin', type=float, default=75.0)
    parser.add_argument('--replace', action='store_true', help='regenerate an existing derived output')
    parser.add_argument('--refresh-world', action='store_true',
                        help='rewrite SDF from existing exported geometry without re-exporting meshes')
    args = parser.parse_args()
    if not math.isfinite(args.route_margin) or args.route_margin < 0:
        parser.error('--route-margin must be finite and nonnegative')
    if args.refresh_world:
        manifest = json.loads((args.out / 'manifest.json').read_text())
        write_world(args.out, manifest['meshes'], manifest['transform']['source_origin_m'])
        print(f'WORLD_READY {args.out / "world.sdf"}', flush=True)
        return
    export(args.stage, args.course, args.index or args.stage.with_name('world.index.json'),
           args.out, args.route_margin, args.replace)


if __name__ == '__main__':
    main()
