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
"""usim strips plugins in its copy while the original RSP description survives."""

from dataclasses import replace
import xml.etree.ElementTree as ET
from pathlib import Path

from usim.simulation import SimulationConfig
from usim_gazebo.assets import GazeboAssetConfig, prepare_assets
from .conftest import RASPICAT_URDF, write_world
from daifuku_sim.vtc.vtc_checks import robot_geometry


def test_preparation_removes_plugins_without_changing_robot_geometry(tmp_path: Path) -> None:
    # Given: upstream still emits a caster plugin and supplies collision properties.
    urdf = tmp_path / 'robot.urdf'
    (tmp_path / 'body.dae').write_text('<COLLADA/>', encoding='utf-8')
    urdf.write_text(
        RASPICAT_URDF.replace(
            'package://raspicat_description/meshes/dae/body/raspicat_base.dae', 'body.dae',
        ).replace(
            '</robot>', '<gazebo reference="base_link"><mu1>0.8</mu1>'
            '<plugin name="caster" filename="libgazebo_ros_joint_state_publisher.so"/>'
            '</gazebo></robot>',
        ), encoding='utf-8',
    )
    original = urdf.read_bytes()
    staging = tmp_path / 'staged'
    staging.mkdir()
    # When: usim prepares its simulator-owned copy from the same RSP description.
    configuration = SimulationConfig(
        world=write_world(tmp_path / 'world.sdf'),
        robot_urdf=urdf,
        base_link='base_footprint',
        left_joint='left_wheel_joint',
        right_joint='right_wheel_joint',
        camera_enabled=False,
        ros=None,
    )
    assets = GazeboAssetConfig('/private_cmd')
    mounts = prepare_assets(configuration, staging, assets)
    # Preserve the previous consumer-side stripping pipeline as an independent oracle.
    legacy_urdf = tmp_path / 'previous-input.urdf'
    previous = ET.parse(urdf)
    for parent in previous.iter():
        for plugin in parent.findall('plugin'):
            parent.remove(plugin)
    previous.write(legacy_urdf, encoding='utf-8', xml_declaration=True)
    previous_staging = tmp_path / 'previous-staged'
    previous_mounts = prepare_assets(
        replace(configuration, robot_urdf=legacy_urdf), previous_staging, assets,
    )
    # Then: no competing plugins reach Gazebo, and RSP's geometry stays unchanged.
    robot = ET.parse(staging / 'robot.urdf').getroot()
    assert robot.findall('.//plugin') == []
    assert robot.findtext('gazebo/mu1') == '0.8'
    assert urdf.read_bytes() == original
    assert robot_geometry(urdf)['wheel_separation'] == 0.27918
    assert mounts == previous_mounts
    assert (staging / 'robot.urdf').read_bytes() == (previous_staging / 'robot.urdf').read_bytes()
    assert (staging / 'world.sdf').read_bytes() == (previous_staging / 'world.sdf').read_bytes()
