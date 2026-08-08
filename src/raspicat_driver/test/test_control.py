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

from raspicat_driver.control import WheelTrim
from raspicat_driver.control import trimmed_speed

DT = 0.02          # one odom_hz interval
KP = 0.0           # the shipped defaults
KI = 1.0
LIMIT = 2.0


def run(trim, target, measured, ticks):
    """Feed the trim a wheel held at `measured` and return the last correction."""
    correction = 0.0
    for _ in range(ticks):
        correction = trim.step(target, measured, DT, KP, KI, LIMIT)
    return correction


def test_a_wheel_that_keeps_up_is_left_alone():
    trim = WheelTrim()
    assert run(trim, 5.0, 5.0, 50) == 0.0


def test_a_slipping_wheel_is_sped_up_and_the_correction_is_capped():
    trim = WheelTrim()
    # 10 % short for one second: the integral is the speed not delivered.
    assert 0.0 < run(trim, 5.0, 4.5, 50) <= LIMIT
    # Held short for a minute it must not wind past the limit.
    assert run(trim, 5.0, 4.5, 3000) == LIMIT
    # ... and a wheel running fast is pulled back the other way.
    assert run(WheelTrim(), 5.0, 5.5, 50) < 0.0


def test_a_stopped_wheel_forgets_everything():
    trim = WheelTrim()
    run(trim, 5.0, 4.5, 50)
    assert trim.step(0.0, 0.0, DT, KP, KI, LIMIT) == 0.0
    assert trim.integral == 0.0


def test_the_trim_never_reverses_a_wheel():
    assert trimmed_speed(0.5, -2.0) == 0.0
    assert trimmed_speed(-0.5, 2.0) == 0.0
    assert trimmed_speed(0.0, 2.0) == 0.0
    assert trimmed_speed(5.0, 0.5) == 5.5
