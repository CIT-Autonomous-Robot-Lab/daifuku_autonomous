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

"""The two 16-bit pulse counters on the Raspberry Pi Cat control board.

Register-addressed reads, done as one combined write+read transaction so the
repeated START matches what i2c_smbus_read_byte_data() issues in rtmouse.
Every failure surfaces as OSError -- that is the whole point of doing this from
userspace.  rtmouse holds a kernel mutex across the transfer, so one timeout
leaves every reader of /dev/rtcounter_* in permanent D state and only a reboot
recovers it (see configs/README.md).

The counters hang off the control board, not off the SoC, so this module is
the same on both models; only the I2C controller behind /dev/i2c-1 differs.
"""

import ctypes
import fcntl
import os

_I2C_RDWR = 0x0707
_I2C_M_RD = 0x0001


class _I2cMsg(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("len", ctypes.c_uint16),
        ("buf", ctypes.POINTER(ctypes.c_uint8)),
    ]


class _I2cRdwrData(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(_I2cMsg)), ("nmsgs", ctypes.c_uint32)]


class PulseCounter:
    """One wheel's pulse counter, addressed on an I2C bus."""

    REG_MSB = 0x10
    REG_LSB = 0x11

    # How many times read() re-reads the high byte looking for a carry-free
    # pair.  Two carries inside the three transactions this takes would need a
    # wheel spinning far past max_step_frequency, so it never runs out.
    READ_ATTEMPTS = 3

    def __init__(self, bus_path, address):
        self._fd = os.open(bus_path, os.O_RDWR | os.O_CLOEXEC)
        self._address = address

    def read(self):
        """Return the raw 16-bit count.  Raises OSError if the bus does not answer.

        The two bytes come from two transactions and the counter keeps running
        between them, so a carry landing in the gap tears the value: read the
        low byte as 0xff just before the count reaches 0x0500 and the high byte
        as 0x05 just after, and the pair reads 0x05ff -- 255 counts high.  The
        next honest read then looks like the counter went *backwards*, which
        node.py resolves modulo 2**16 into most of a revolution of travel that
        never happened (measured 2026-08-04 on the Pi 5: one tear while a wheel
        was turned by hand moved odom 45 m).

        So bracket the low byte with the high one and start over if it moved.
        """
        msb = self._read_register(self.REG_MSB)
        for _ in range(self.READ_ATTEMPTS):
            lsb = self._read_register(self.REG_LSB)
            again = self._read_register(self.REG_MSB)
            if again == msb:
                return (msb << 8) | lsb
            msb = again
        # Every attempt straddled a carry, which no reachable wheel speed can
        # do.  Hand back the freshest pair anyway: the caller bounds the delta,
        # so a wrong value here costs one interval rather than the pose.
        return (msb << 8) | lsb

    def close(self):
        """Close the bus handle.  Safe to call twice."""
        try:
            os.close(self._fd)
        except OSError:
            pass

    def _read_register(self, register):
        out = (ctypes.c_uint8 * 1)(register)
        into = (ctypes.c_uint8 * 1)()
        pointer = ctypes.POINTER(ctypes.c_uint8)
        messages = (_I2cMsg * 2)(
            _I2cMsg(self._address, 0, 1, ctypes.cast(out, pointer)),
            _I2cMsg(self._address, _I2C_M_RD, 1, ctypes.cast(into, pointer)),
        )
        fcntl.ioctl(self._fd, _I2C_RDWR, _I2cRdwrData(messages, 2))
        return into[0]
