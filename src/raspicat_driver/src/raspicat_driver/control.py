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

"""The wheel speed trim used by control_mode: "closed" (node.py).

Nothing here touches ROS or the hardware, so it can be exercised on a host
without rclpy (test/test_control.py).

The trim sits on top of the feed-forward command rather than replacing it: the
step frequency the open loop would have written is still the bulk of the
output, and this only adds what the encoders say is missing.  That keeps the
open and closed modes the same command path, and it keeps a dead encoder from
meaning a dead robot -- node.py drops the trim to zero and carries on.

Why the proportional gain defaults to zero: one encoder count is
2*pi/pulses_per_revolution of a wheel turn, which over one odom_hz interval is
about 0.28 rad/s on this robot.  A proportional term multiplies that
quantization straight into the step frequency every tick.  The integral does
not accumulate it, because the counter is free-running: successive truncations
cancel and the integral converges on the distance actually not travelled,
which is the slip we are trying to cancel in the first place.
"""


class WheelTrim:
    """PI trim for one wheel, in wheel rad/s, with conditional integration."""

    def __init__(self):
        self.integral = 0.0

    def reset(self):
        """Forget the accumulated error.  Used whenever the wheel is stopped."""
        self.integral = 0.0

    def step(self, target, measured, dt, kp, ki, limit):
        """Return the correction to add to `target`, both in wheel rad/s.

        `measured` is what the encoder saw over `dt` seconds.  A stopped wheel
        resets instead of integrating: a commanded stop has to be a real stop,
        and the watchdog relies on it.
        """
        if dt <= 0.0 or limit <= 0.0 or target == 0.0:
            self.reset()
            return 0.0

        error = target - measured
        candidate = self.integral + error * dt
        correction = kp * error + ki * candidate
        # Conditional integration: while the output is pinned, accumulating
        # would only build a debt that has to be unwound before the wheel can
        # respond again.  An error that changes sign shrinks `correction` back
        # under the limit on the next call, so this unwinds on its own.
        if correction > limit:
            return limit
        if correction < -limit:
            return -limit
        self.integral = candidate
        return correction


def trimmed_speed(target, correction):
    """`target` plus `correction`, clamped so it cannot cross zero.

    The trim slows a wheel down or speeds it up; it never reverses one.  A
    large correction against a small target would otherwise turn a wheel the
    opposite way from the one cmd_vel asked for, and the odometry would not
    even see it -- node.py borrows the sign of each pulse delta from the
    direction line it last wrote.
    """
    if target > 0.0:
        return max(0.0, target + correction)
    if target < 0.0:
        return min(0.0, target + correction)
    return 0.0
