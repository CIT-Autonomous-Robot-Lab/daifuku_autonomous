#!/usr/bin/env python3
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

"""Drop PointCloud2 points outside an elevation-angle band around the sensor.

pointcloud_to_laserscan cuts on absolute height, which cannot reject a
sloped floor: a floor rising at angle a crosses any fixed min_height at
some range, so every upward grade eventually enters the band and becomes
a wall that is not in the 2D map. An elevation cut rejects it outright --
a surface whose slope stays below the cut angle never reaches the band at
any range, because both floor and ceiling approach 0 degrees as range
grows.

Seen from the projection downstream, this is min_height made proportional
to range: a point is kept when

    z >= tan(min_elevation) * hypot(x, y)

which in base_footprint is z_base >= lidar_z + range * tan(min_elevation).
Near the robot the effective floor is the sensor height, so low obstacles
come back into the costmap; far away it rises past what a fixed band
could use.

The angle is measured from the sensor origin in the cloud's own frame, so
the cloud must arrive before any transform (i.e. livox_frame, upstream of
pointcloud_to_laserscan). This holds only while lidar_roll and lidar_pitch
are 0; tilt the sensor and the band tilts with it.

Topics are the relative cloud_in / cloud_out, remapped by
lidar_bringup.launch.py -- the same shape as restamp_scan.py.
"""

import array
import math

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2

# Beyond this the tangent is large enough that the cut can never reject a
# point, so treat it as "no limit" instead of multiplying by ~1e15.
_UNBOUNDED_DEG = 89.9


class ElevationFilter(Node):
    def __init__(self):
        super().__init__("elevation_filter")
        # Declared wide open so this node is a pass-through until a
        # parameter file narrows it. src/daifuku_config/bringup/sensors/mid360_elevation.yaml
        # holds the real values.
        min_deg = self.declare_parameter("min_elevation_deg", -90.0).value
        max_deg = self.declare_parameter("max_elevation_deg", 90.0).value
        if min_deg > max_deg:
            raise RuntimeError(
                "min_elevation_deg (%g) is above max_elevation_deg (%g), "
                "which empties every scan." % (min_deg, max_deg)
            )
        self._min_slope = (
            None if min_deg <= -_UNBOUNDED_DEG else math.tan(math.radians(min_deg))
        )
        self._max_slope = (
            None if max_deg >= _UNBOUNDED_DEG else math.tan(math.radians(max_deg))
        )
        self.get_logger().info(
            "keeping elevations %.2f..%.2f deg "
            "(effective floor: %.2fm at 10m, %.2fm at 30m above the sensor)"
            % (
                min_deg,
                max_deg,
                10.0 * (self._min_slope or 0.0),
                30.0 * (self._min_slope or 0.0),
            )
        )

        # Cached per (fields, point_step) so the dtype is built once, not
        # per cloud. The Mid-360 never changes its layout at runtime, but
        # a bag or the simulator may feed a different one.
        self._dtype = None
        self._dtype_key = None
        self._warned_bigendian = False

        pub_qos = QoSProfile(depth=5)
        sub_qos = QoSProfile(depth=5)
        sub_qos.reliability = ReliabilityPolicy.BEST_EFFORT
        self._publisher = self.create_publisher(PointCloud2, "cloud_out", pub_qos)
        self._subscription = self.create_subscription(
            PointCloud2, "cloud_in", self._callback, sub_qos)

    def _point_dtype(self, message):
        """Structured dtype covering one point, padding included.

        Built from the message's own fields rather than a hard-coded Livox
        layout, so the simulator's cloud and a recorded bag work too.
        itemsize is point_step: the trailing padding has to be carried
        through or the republished cloud stops parsing.
        """
        key = (
            tuple((f.name, f.offset, f.datatype, f.count) for f in message.fields),
            message.point_step,
        )
        if key == self._dtype_key:
            return self._dtype

        numpy_type = {
            1: np.int8, 2: np.uint8, 3: np.int16, 4: np.uint16,
            5: np.int32, 6: np.uint32, 7: np.float32, 8: np.float64,
        }
        names, formats, offsets = [], [], []
        for field in message.fields:
            if field.datatype not in numpy_type:
                raise RuntimeError(
                    "unsupported PointField datatype %d on field %r"
                    % (field.datatype, field.name)
                )
            names.append(field.name)
            formats.append(
                numpy_type[field.datatype] if field.count == 1
                else (numpy_type[field.datatype], field.count)
            )
            offsets.append(field.offset)

        for required in ("x", "y", "z"):
            if required not in names:
                raise RuntimeError(
                    "cloud has no %r field, so elevation cannot be computed "
                    "(fields: %s)" % (required, ", ".join(names))
                )

        self._dtype = np.dtype({
            "names": names,
            "formats": formats,
            "offsets": offsets,
            "itemsize": message.point_step,
        })
        self._dtype_key = key
        return self._dtype

    def _callback(self, message):
        if message.is_bigendian:
            # Reinterpreting the buffer would silently produce garbage
            # coordinates, so pass the cloud through untouched instead.
            if not self._warned_bigendian:
                self.get_logger().error(
                    "cloud is big-endian; passing it through unfiltered")
                self._warned_bigendian = True
            self._publisher.publish(message)
            return

        points = np.frombuffer(message.data, dtype=self._point_dtype(message))
        x = points["x"]
        y = points["y"]
        z = points["z"]
        radius = np.hypot(x, y)

        # Compared as slopes rather than angles: no atan2 per point, and
        # NaN coordinates compare false, so they drop out here the same way
        # pointcloud_to_laserscan would drop them.
        keep = np.isfinite(z)
        if self._min_slope is not None:
            keep &= z >= self._min_slope * radius
        if self._max_slope is not None:
            keep &= z <= self._max_slope * radius

        kept = points[keep]
        # array.array("B") is the only fast path the generated setter has for a
        # uint8[] field: hand it bytes instead and the __debug__ block walks
        # every byte twice in Python (measured on the Pi 5, 2026-08-05: 90ms for
        # 260KB, against 0.03ms here -- at 10Hz that alone was half a core).
        # A numpy array is not an option; it is not a Sequence, so the same
        # block rejects it outright.
        message.data = array.array("B", kept.tobytes())
        # Filtering breaks any row structure, so the cloud comes out
        # unordered (height 1) regardless of how it went in.
        message.height = 1
        message.width = int(kept.shape[0])
        message.row_step = message.point_step * message.width
        message.is_dense = True
        self._publisher.publish(message)


def main(args=None):
    rclpy.init(args=args)
    node = ElevationFilter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
