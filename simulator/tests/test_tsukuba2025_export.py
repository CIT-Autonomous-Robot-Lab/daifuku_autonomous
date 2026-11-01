# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""合成後の形状・衝突フラグ・原座標を保ったCPU出力の検査。"""
import json
import math
import xml.etree.ElementTree as ET
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

np = pytest.importorskip('numpy')
SPEC = spec_from_file_location(
    'package_tsukuba2025',
    Path(__file__).resolve().parents[1] / 'tools/package_tsukuba2025.py',
)
assert SPEC is not None and SPEC.loader is not None
exporter = module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)
NS = {'c': 'http://www.collada.org/2005/11/COLLADASchema'}


def test_world_preserves_one_millisecond_steps_at_native_control_rate(tmp_path):
    # Given: a generated course world, independent of any native engine.
    # When: its physics settings are exported.
    exporter.write_world(tmp_path, [], [0, 0, 0])
    physics = ET.parse(tmp_path / 'world.sdf').find('./world/physics')
    # Then: the wall-clock controller cannot execute several simulated seconds per tick.
    assert physics is not None
    assert float(physics.findtext('max_step_size', '')) == 0.001
    assert float(physics.findtext('real_time_update_rate', '')) * 0.001 == 1.0


def test_source_spawn_heading_and_height_round_trip():
    origin = [298.2, -146.6, -0.8]
    source = np.array([origin, [288.2, -148.6, -0.4]])
    result = exporter.gazebo_points(source, origin, math.pi)
    assert result == pytest.approx(np.array([[0, 0, 0], [10, 2, 0.4]]))
    assert exporter.gazebo_points(result, [0, 0, 0], -math.pi) + origin == pytest.approx(
        source,
    )


def test_route_envelope_keeps_mesh_whose_center_is_outside():
    route = np.array([[0, 0, 0], [100, 0, 0]])
    assert exporter.near_route([40, 29, 0], [60, 49, 5], route, 30)
    assert not exporter.near_route([40, 70, 0], [60, 80, 5], route, 30)
    assert exporter.near_route([40, 70, 0], [60, 80, 5], route, 0)


def test_spawn_samples_actual_triangle_not_flattened_average():
    points = np.array([[0, 0, 1], [1, 0, 2], [0, 1, 3]], dtype=float)
    faces = np.array([[0, 1, 2]])
    assert exporter.triangle_height(points, faces, [.25, .25]) == pytest.approx([1.75])
    assert exporter.triangle_height(points, faces, [1, 1]) == []


def test_double_sided_visual_retains_nonzero_opposite_normals(tmp_path):
    destination = tmp_path / 'leaf.dae'
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    exporter.write_dae(destination, points, np.array([[0, 1, 2]]),
                       np.ones((3, 3)), True)
    document = ET.parse(destination)
    normal_array = document.find(".//c:float_array[@id='normal-array']", NS)
    assert normal_array is not None and normal_array.text
    normals = np.fromstring(normal_array.text, sep=' ').reshape(-1, 3)
    assert normals[:3] == pytest.approx(np.array([[0, 0, 1]] * 3))
    assert normals[3:] == pytest.approx(np.array([[0, 0, -1]] * 3))
    triangles = document.find('.//c:triangles', NS)
    assert triangles is not None and triangles.get('count') == '2'
    normal_input = triangles.find("c:input[@semantic='NORMAL']", NS)
    assert normal_input is not None and normal_input.get("offset") == "1"
    indices = np.fromstring(triangles.findtext("c:p", "", NS), sep=" ", dtype=int)
    tuples = indices.reshape(-1, 3, 2)
    assert np.array_equal(tuples[:, :, 0], tuples[:, :, 1])


def test_source_pigment_uses_a_supported_gazebo_diffuse_material(tmp_path):
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    colors = np.array([[.1, .4, .2], [.2, .5, .3], [.3, .6, .4]])
    destination = tmp_path / 'pigment.dae'
    exporter.write_dae(destination, points, np.array([[0, 1, 2]]), colors)
    document = ET.parse(destination)
    diffuse = document.find('.//c:lambert/c:diffuse/c:color', NS)
    assert diffuse is not None
    assert np.fromstring(diffuse.text, sep=' ') == pytest.approx([.2, .5, .3, 1])
    assert 'COLOR' not in {
        node.get('semantic') for node in document.findall('.//c:triangles/c:input', NS)
    }


def test_export_uses_composed_payload_and_preserves_collision_flags(tmp_path):
    pytest.importorskip('pxr')
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    asset = Usd.Stage.CreateNew(str(tmp_path / 'tile.usda'))
    UsdGeom.Xform.Define(asset, '/Tile')
    asset.SetDefaultPrim(asset.GetPrimAtPath('/Tile'))

    def triangle(path, collision=True, invisible=False):
        mesh = UsdGeom.Mesh.Define(asset, path)
        mesh.CreatePointsAttr([(0, 0, 0), (1, 0, .1), (0, 1, .2)])
        mesh.CreateFaceVertexCountsAttr([3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
        mesh.CreateDisplayColorPrimvar().Set([(0.2, 0.4, 0.6)])
        if collision:
            UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
        if invisible:
            mesh.CreateVisibilityAttr('invisible')
        return mesh

    triangle('/Tile/ground')
    triangle('/Tile/leaf', collision=False).CreateDoubleSidedAttr(True)
    triangle('/Tile/disabled')
    UsdPhysics.CollisionAPI(asset.GetPrimAtPath('/Tile/disabled')).CreateCollisionEnabledAttr(False)
    triangle('/Tile/hidden', invisible=True)
    triangle('/Tile/removed')
    asset.GetRootLayer().Save()
    source = Usd.Stage.CreateNew(str(tmp_path / 'runtime-world.usda'))
    UsdGeom.SetStageUpAxis(source, 'Z')
    UsdGeom.SetStageMetersPerUnit(source, 1)
    UsdGeom.Xform.Define(source, '/World')
    root = UsdGeom.Xform.Define(source, '/World/Terrain/tile').GetPrim()
    root.GetPayloads().AddPayload(str(tmp_path / 'tile.usda'), Sdf.Path('/Tile'))
    source.OverridePrim('/World/Terrain/tile/ground').GetAttribute('points').Set(
        [(0, 0, 1), (1, 0, 1.1), (0, 1, 1.2)],
    )
    source.GetPrimAtPath('/World/Terrain/tile/removed').SetActive(False)
    root.SetActive(False)
    source.GetRootLayer().Save()
    entries = [{'path': str(root.GetPath()), 'min': [0, 0, 0], 'max': [1, 1, 2]}]
    index_path = tmp_path / 'world.index.json'
    index_path.write_text(json.dumps(entries), encoding='utf-8')
    goals = [[.25, .25, math.pi]] * 30
    course = {'spawn': [.25, .25, 1.3], 'spawn_yaw': math.pi, 'goals': goals,
              'world_route': [[.25, .25]] * 150}
    course_path = tmp_path / 'course.json'
    course_path.write_text(json.dumps(course), encoding='utf-8')
    before = (tmp_path / 'runtime-world.usda').read_bytes()
    output = tmp_path / 'out'
    observed_routes = []
    original_near_route = exporter.near_route

    def record_route(low, high, route, margin) -> bool:
        observed_routes.append(route.copy())
        return bool(original_near_route(low, high, route, margin))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(exporter, 'near_route', record_route)
        result = exporter.export(tmp_path / 'runtime-world.usda', course_path, index_path, output)
    assert observed_routes
    for selected_route in observed_routes:
        np.testing.assert_array_equal(selected_route, course['world_route'])
    assert result['goals'] == goals
    assert result['transform']['source_origin_m'] == pytest.approx([.25, .25, 1.075])
    assert result['transform']['source_spawn_clearance_m'] == pytest.approx(.225)
    records = {Path(record['source_prim']).name: record for record in result['meshes']}
    assert set(records) == {'ground', 'leaf', 'disabled', 'hidden'}
    assert records['leaf']['collision'] is None
    assert records['disabled']['collision'] is None
    assert records['hidden']['visual'] is None
    assert records['hidden']['collision']
    assert records['leaf']['visual'].endswith('-double.dae')
    assert (tmp_path / 'runtime-world.usda').read_bytes() == before
    assert not Usd.Stage.Open(str(tmp_path / 'runtime-world.usda')).GetPrimAtPath(
        '/World/Terrain/tile',
    ).IsActive()
    document = ET.parse(output / 'world.sdf')
    assert len(document.findall('.//collision')) == 2
    assert len(document.findall('.//model')) == 1
    for uri in document.findall('.//mesh/uri'):
        assert (output / uri.text).is_file()


def test_concave_polygon_and_mirrored_face_colors_are_preserved():
    pytest.importorskip('pxr')
    from pxr import Gf, Usd, UsdGeom
    stage = Usd.Stage.CreateInMemory()
    mesh = UsdGeom.Mesh.Define(stage, '/concave')
    mesh.CreatePointsAttr([(0, 0, 0), (2, 0, 0), (1, 1, 0), (2, 2, 0), (0, 2, 0)])
    mesh.CreateFaceVertexCountsAttr([5])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3, 4])
    pigment = mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.faceVarying)
    pigment.Set([(i / 5, 0, 0) for i in range(5)])
    UsdGeom.Xformable(mesh).AddScaleOp().Set(Gf.Vec3f(-2, 3, 1))
    points, faces = exporter.mesh_arrays(mesh, UsdGeom.XformCache())
    triangle = points[faces]
    area = np.cross(triangle[:, 1] - triangle[:, 0],
                    triangle[:, 2] - triangle[:, 0])[:, 2]
    assert len(faces) == 3
    assert area.sum() / 2 == pytest.approx(18)
    expanded, _, colors = exporter.vertex_colors(mesh, points, faces)
    for point, color in zip(expanded, colors):
        original_index = np.where(np.all(points == point, axis=1))[0][0]
        assert color[0] == pytest.approx(original_index / 5)
