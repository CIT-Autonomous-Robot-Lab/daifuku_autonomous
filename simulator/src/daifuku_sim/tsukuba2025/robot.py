# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""The course robot's Mid-360 mounting, separate from the planar survey robot."""
from pathlib import Path
import xml.etree.ElementTree as ET

MID360_OFFSET = (0.144, 0.0, 0.275)


def mount_mid360(urdf: Path) -> None:
    """Add the sensor optical frame at the previously measured course mounting pose."""
    tree = ET.parse(urdf)
    robot = tree.getroot()
    if robot.find("link[@name='livox_frame']") is not None:
        raise ValueError("The prepared robot already contains livox_frame")
    link = ET.SubElement(robot, "link", name="livox_frame")
    inertial = ET.SubElement(link, "inertial")
    _ = ET.SubElement(inertial, "mass", value="0.265")
    _ = ET.SubElement(inertial, "inertia", ixx="0.00015", ixy="0", ixz="0",
                  iyy="0.00015", iyz="0", izz="0.00019")
    visual = ET.SubElement(link, "visual")
    _ = ET.SubElement(ET.SubElement(visual, "geometry"), "box", size="0.065 0.065 0.060")
    material = ET.SubElement(visual, "material", name="mid360_case")
    _ = ET.SubElement(material, "color", rgba="0.2 0.22 0.25 1")
    joint = ET.SubElement(robot, "joint", name="mid360_mount", type="fixed")
    _ = ET.SubElement(joint, "parent", link="base_footprint")
    _ = ET.SubElement(joint, "child", link="livox_frame")
    _ = ET.SubElement(
        joint, "origin", xyz=" ".join(f"{value:g}" for value in MID360_OFFSET), rpy="0 0 0",
    )
    ET.indent(tree)
    tree.write(urdf, encoding="utf-8", xml_declaration=True)
