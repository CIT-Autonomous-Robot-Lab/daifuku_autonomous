# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""The Mid-360 optical frame must agree between raycasting and robot TF."""
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from daifuku_sim.tsukuba2025.robot import mount_mid360


def test_mount_preserves_the_drive_robot_and_adds_the_mid360_optical_frame(tmp_path: Path) -> None:
    # Given: the simulator prepared its drive URDF without the Mid-360.
    urdf = tmp_path / "robot.urdf"
    urdf.write_text('<robot name="raspicat"><link name="base_footprint"/></robot>')
    # When: the course sensor is mounted.
    mount_mid360(urdf)
    # Then: both the original drive link and one correctly mounted sensor remain.
    robot = ET.parse(urdf).getroot()
    assert robot.find("link[@name='base_footprint']") is not None
    assert len(robot.findall("link[@name='livox_frame']")) == 1
    joint = robot.find("joint[@name='mid360_mount']")
    assert joint is not None
    parent, child, origin = joint.find("parent"), joint.find("child"), joint.find("origin")
    assert parent is not None and child is not None and origin is not None
    assert parent.attrib["link"] == "base_footprint"
    assert child.attrib["link"] == "livox_frame"
    assert origin.attrib["xyz"] == "0.144 0 0.275"


def test_duplicate_optical_frame_is_rejected_before_changing_the_robot(tmp_path: Path) -> None:
    # Given: an existing Mid-360 frame that must not acquire another TF parent.
    urdf = tmp_path / "robot.urdf"
    original = '<robot name="raspicat"><link name="livox_frame"/></robot>'
    urdf.write_text(original)
    # When / Then: a second mount is refused without changing the existing robot.
    with pytest.raises(ValueError):
        mount_mid360(urdf)
    assert urdf.read_text() == original
