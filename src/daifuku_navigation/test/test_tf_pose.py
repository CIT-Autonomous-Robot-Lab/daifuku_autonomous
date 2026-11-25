# Copyright 2026 Keita Sekiguchi / nop
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
"""tf_pose が推定器の処理遅れを姿勢に持ち込まないことを確かめる."""

import importlib.util
from pathlib import Path

from geometry_msgs.msg import TransformStamped
import pytest
import rclpy
from rclpy.duration import Duration
from rclpy.time import Time

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'tf_pose.py'
SPEC = importlib.util.spec_from_file_location('tf_pose', SOURCE)
tf_pose = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tf_pose)


@pytest.fixture
def adapter():
    rclpy.init()
    node = tf_pose.TfPose()
    published = []
    node._publisher = type('Capture', (), {'publish': staticmethod(published.append)})
    yield node, published
    node.destroy_node()
    rclpy.shutdown()


def give(node, parent, child, stamp, x):
    edge = TransformStamped()
    edge.header.stamp = stamp.to_msg()
    edge.header.frame_id, edge.child_frame_id = parent, child
    edge.transform.translation.x = x
    edge.transform.rotation.w = 1.0
    node._buffer.set_transform(edge, 'test')


def test_pose_uses_latest_odometry_after_a_delayed_map_correction(adapter):
    node, published = adapter
    now = node.get_clock().now()
    cloud = now - Duration(seconds=0.2)
    # emcl3d は点群の時刻で map→odom を出す。その後も odom は 10 cm 進んでいる。
    give(node, 'map', 'odom', cloud, 1.0)
    give(node, 'odom', 'base_footprint', cloud, 0.0)
    give(node, 'odom', 'base_footprint', now - Duration(seconds=0.01), 0.1)
    node._publish_pose()
    pose = published[-1]
    assert pose.pose.pose.position.x == pytest.approx(1.1)
    # 鮮度は地図補正の時刻のまま。odom が進んでも古い補正を新しく見せない。
    assert Time.from_msg(pose.header.stamp) == cloud


def test_postdated_map_correction_is_aged_from_its_arrival(adapter):
    node, published = adapter
    now = node.get_clock().now()
    # AMCL は transform_tolerance だけ未来の時刻で map→odom を出し続ける。
    give(node, 'map', 'odom', now, 0.0)
    give(node, 'map', 'odom', now + Duration(seconds=1.0), 0.0)
    give(node, 'odom', 'base_footprint', now, 0.0)
    node._publish_pose()
    assert Time.from_msg(published[-1].header.stamp) <= node.get_clock().now()
