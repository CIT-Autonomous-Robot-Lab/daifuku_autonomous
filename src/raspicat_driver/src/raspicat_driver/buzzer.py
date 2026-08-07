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

"""The buzzer as a square wave toggled from a thread.

The buzzer sits on GPIO19, which on a Pi 4 is the *same* PWM channel as GPIO13
-- the right motor's step clock (BCM2711 PWM0, channel 1).  rtmouse gets away
with sharing it because it ioremap()s GPFSEL and re-muxes GPIO19 between ALT5
and plain output around every beep; neither sysfs PWM nor the gpiochip
character device can change a pin's alt function, so this driver cannot.  A Pi
4 therefore has no PWM channel left for the buzzer, and a Pi 5's RP1 only has
one if the pin is muxed to it by an overlay (see the README).

That leaves toggling the line by hand, which is what this module does.  The
pitch is the only casualty: a scheduling hiccup stretches one half-period, so
the tone warbles a little.  It is a notification buzzer, so that is a fair
trade for needing no overlay, no reboot and no PWM channel.

The thread is the only writer of its line, and the line is claimed in its own
handle (gpio.py), so nothing here shares state with the motor path.
"""

import threading
import time

from .gpio import GpioOutputs

# How long before an edge we stop sleeping and spin.  time.sleep() returns late
# by a few hundred microseconds under load, which is most of a half-period at
# the top of the range, so the last stretch is busy-waited instead.  Capped at a
# quarter of the half-period so a high note does not spin away a whole core.
SPIN_MARGIN = 0.0002

# Longest a stale deadline is chased before the phase is simply restarted.  A
# thread descheduled for longer than this would otherwise emit a burst of edges
# back to back trying to catch up.
MAX_CATCHUP = 0.05


class SoftwareBuzzer:
    """A square wave on one GPIO line, produced by a thread.

    set_frequency() and stop() return immediately; the thread does the work and
    lives until close().  While silent it blocks on an event and costs nothing.
    """

    def __init__(self, chip_path, offset):
        self._line = GpioOutputs(chip_path, [offset])
        # Written by the ROS callback, read by the thread.  A float assignment
        # is atomic under the GIL and a half-period of staleness is inaudible,
        # so there is no lock on the audio path.
        self._frequency = 0.0
        self._closing = False
        self._wake = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="raspicat_buzzer", daemon=True
        )
        self._thread.start()

    def set_frequency(self, freq_hz):
        """Start (or retune) the tone.  0 or less is silence."""
        self._frequency = float(freq_hz) if freq_hz > 0 else 0.0
        self._wake.set()

    def stop(self):
        """Silence the buzzer, keeping the thread and the line."""
        self.set_frequency(0.0)

    def close(self):
        """Silence it, stop the thread and release the line.  Safe to call twice."""
        self._closing = True
        self._frequency = 0.0
        self._wake.set()
        if self._thread.is_alive():
            # Longer than any half-period the node will ask for, so the thread
            # is always between edges rather than killed mid-tone.
            self._thread.join(timeout=1.0)
        self._line.close()

    def _run(self):
        while not self._closing:
            frequency = self._frequency
            if frequency <= 0.0:
                self._write(0)
                # Nothing to do until set_frequency() or close() says otherwise.
                self._wake.wait()
                self._wake.clear()
                continue
            self._play(frequency)
        self._write(0)

    def _play(self, frequency):
        """Toggle the line at `frequency` until the request changes."""
        half = 0.5 / frequency
        margin = min(SPIN_MARGIN, half / 4.0)
        level = 0
        deadline = time.perf_counter()
        while self._frequency == frequency and not self._closing:
            deadline += half
            now = time.perf_counter()
            if deadline < now - MAX_CATCHUP:
                # Descheduled for a long while: start the phase again rather
                # than emitting the edges we owe as fast as the ioctls go.
                deadline = now + half
            remaining = deadline - time.perf_counter()
            if remaining > margin:
                time.sleep(remaining - margin)
            while time.perf_counter() < deadline:
                pass
            level ^= 1
            self._write(level)
        self._write(0)

    def _write(self, level):
        try:
            self._line.set(0, level)
        except OSError:
            # Nothing above can act on a failed beep, and this runs thousands
            # of times a second -- so give up on the tone instead of logging.
            self._frequency = 0.0
