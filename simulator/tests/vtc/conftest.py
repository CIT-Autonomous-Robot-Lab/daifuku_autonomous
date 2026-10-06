"""Shared fixtures for the usim VTC workflow tests (host side, no ROS, no engine)."""

from __future__ import annotations

from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[2] / 'container' / 'usim_vtc'

RASPICAT_URDF = """<?xml version="1.0"?>
<robot name="raspicat_on_gazebo">
  <link name="base_footprint"/>
  <joint name="base_joint" type="fixed">
    <origin xyz="0 0 0.0762" rpy="0 0 0"/>
    <parent link="base_footprint"/><child link="base_link"/>
  </joint>
  <link name="base_link">
    <visual><geometry><mesh filename="package://raspicat_description/meshes/dae/body/raspicat_base.dae"/></geometry></visual>
  </link>
  <joint name="right_wheel_joint" type="continuous">
    <origin xyz="0 -0.13959 0" rpy="-1.57 0 0"/><axis xyz="0 0 1"/>
    <parent link="base_link"/><child link="right_wheel_link"/>
  </joint>
  <link name="right_wheel_link">
    <collision><geometry><cylinder radius="0.0762" length="0.0254"/></geometry></collision>
  </link>
  <joint name="left_wheel_joint" type="continuous">
    <origin xyz="0 0.13959 0" rpy="1.57 0 0"/><axis xyz="0 0 -1"/>
    <parent link="base_link"/><child link="left_wheel_link"/>
  </joint>
  <link name="left_wheel_link">
    <collision><geometry><cylinder radius="0.0762" length="0.0254"/></geometry></collision>
  </link>
  <joint name="lidar_mount_joint" type="fixed">
    <origin xyz="0.144 0 -0.0262" rpy="0 0 0"/>
    <parent link="base_link"/><child link="lidar_mount_link"/>
  </joint>
  <link name="lidar_mount_link"/>
  <joint name="lidar_frame_joint" type="fixed">
    <origin xyz="0 0 0.055" rpy="0 0 0"/>
    <parent link="lidar_mount_link"/><child link="lidar_link"/>
  </joint>
  <link name="lidar_link"/>
</robot>
"""


def write_world(path: Path, body: str | None = None) -> Path:
    """A minimal valid VTC-like SDF world with one relative mesh URI."""
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / 'meshes').mkdir(exist_ok=True)
    (path.parent / 'meshes' / 'mesh_0000.dae').write_text('<COLLADA/>', encoding='utf-8')
    path.write_text(
        body
        if body is not None
        else '<sdf version="1.6"><world name="vtc"><model name="terrain"><link name="l">'
        '<collision name="c"><geometry><mesh><uri>meshes/mesh_0000.dae</uri></mesh></geometry>'
        '</collision></link></model></world></sdf>',
        encoding='utf-8',
    )
    return path


def write_map(directory: Path, size: int = 120, resolution: float = 0.05,
              origin: tuple[float, float] = (-1.0, -1.0), wall_x: float | None = None) -> Path:
    """Saved map: free everywhere in the window, optional occupied column at ``wall_x``."""
    directory.mkdir(parents=True, exist_ok=True)
    pixels = bytearray([254] * size * size)
    if wall_x is not None:
        column = int((wall_x - origin[0]) / resolution)
        for row in range(size):
            pixels[row * size + column] = 0
    (directory / 'vtc_map.pgm').write_bytes(f'P5\n# CREATOR: test\n{size} {size}\n255\n'.encode() + bytes(pixels))
    (directory / 'vtc_map.yaml').write_text(
        'image: vtc_map.pgm\nmode: trinary\n'
        f'resolution: {resolution}\norigin: [{origin[0]}, {origin[1]}, 0]\n'
        'negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.15\n',
        encoding='utf-8',
    )
    return directory / 'vtc_map.yaml'


@pytest.fixture
def usim_root(tmp_path: Path) -> Path:
    root = tmp_path / 'usim'
    (root / 'docker').mkdir(parents=True)
    (root / 'docker' / 'Dockerfile.gazebo').write_text('FROM scratch\n', encoding='utf-8')
    write_world(root / 'assets' / 'vtc' / 'world.sdf')
    return root
