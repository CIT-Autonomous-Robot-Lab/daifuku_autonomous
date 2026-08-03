"""GPIO character device access, v1 uAPI.

v1 is deprecated in libgpiod terms but the kernel still supports it, and its
request struct is one fixed layout with no pointers, which keeps this module
dependency-free.  The Humble container image has neither python3-libgpiod nor
python3-gpiod, and adding one would force a `docker compose build`.

The same code covers both models: a Pi 4 exposes the header pins through the
gpiochip labelled "pinctrl-bcm2835" and a Pi 5 through "pinctrl-rp1", but the
line offsets are the BCM numbers on both.
"""

import fcntl
import os
import struct

CONSUMER = "raspicat_driver"


# _IOC() from asm-generic/ioctl.h.  fcntl.ioctl() wants a value that fits in a
# signed int, and every direction we use here sets bit 31, so fold it round.

_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction, type_, nr, size):
    value = (direction << 30) | (size << 16) | (type_ << 8) | nr
    if value >= 0x80000000:
        value -= 0x100000000
    return value


_GPIO_MAGIC = 0xB4

# struct gpiochip_info { char name[32]; char label[32]; __u32 lines; }
_CHIPINFO_FMT = "=32s32sI"
_CHIPINFO_SIZE = struct.calcsize(_CHIPINFO_FMT)

# struct gpiohandle_request { __u32 lineoffsets[64]; __u32 flags;
#                             __u8 default_values[64]; char consumer_label[32];
#                             __u32 lines; int fd; }
_HANDLE_REQ_FMT = "=64I I 64B 32s I i"
_HANDLE_REQ_SIZE = struct.calcsize(_HANDLE_REQ_FMT)

# struct gpiohandle_data { __u8 values[64]; }
_HANDLE_DATA_FMT = "=64B"
_HANDLE_DATA_SIZE = struct.calcsize(_HANDLE_DATA_FMT)

_GPIO_GET_CHIPINFO = _ioc(_IOC_READ, _GPIO_MAGIC, 0x01, _CHIPINFO_SIZE)
_GPIO_GET_LINEHANDLE = _ioc(_IOC_READ | _IOC_WRITE, _GPIO_MAGIC, 0x03, _HANDLE_REQ_SIZE)
_GPIO_SET_LINE_VALUES = _ioc(_IOC_READ | _IOC_WRITE, _GPIO_MAGIC, 0x09, _HANDLE_DATA_SIZE)

_GPIOHANDLE_REQUEST_OUTPUT = 1 << 1


def chip_devices():
    """Return every /dev/gpiochip*, readable or not."""
    return [
        os.path.join("/dev", name)
        for name in sorted(os.listdir("/dev"))
        if name.startswith("gpiochip")
    ]


def chip_labels():
    """Return {path: label} for every *readable* /dev/gpiochip*.

    Compare with chip_devices() to tell "no such chip" from "no permission".
    """
    found = {}
    for path in chip_devices():
        try:
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            info = fcntl.ioctl(fd, _GPIO_GET_CHIPINFO, bytes(_CHIPINFO_SIZE))
        except OSError:
            continue
        finally:
            os.close(fd)
        found[path] = struct.unpack(_CHIPINFO_FMT, info)[1].split(b"\0")[0].decode()
    return found


def find_gpiochip(label):
    """Return the /dev/gpiochipN whose driver label matches, or None.

    The chip number moves between kernel releases (and between models), so
    match on the label rather than the digit.
    """
    for path, found in chip_labels().items():
        if found == label:
            return path
    return None


class GpioOutputs:
    """Output lines on one gpiochip, requested together and set together."""

    def __init__(self, chip_path, offsets):
        self._offsets = list(offsets)
        self._values = [0] * len(self._offsets)
        self._chip_fd = os.open(chip_path, os.O_RDWR | os.O_CLOEXEC)
        try:
            fields = self._offsets + [0] * (64 - len(self._offsets))
            fields.append(_GPIOHANDLE_REQUEST_OUTPUT)
            fields.extend([0] * 64)
            fields.append(CONSUMER.encode("ascii")[:31])
            fields.append(len(self._offsets))
            fields.append(0)
            reply = fcntl.ioctl(
                self._chip_fd,
                _GPIO_GET_LINEHANDLE,
                struct.pack(_HANDLE_REQ_FMT, *fields),
            )
        except OSError:
            os.close(self._chip_fd)
            raise
        self._line_fd = struct.unpack(_HANDLE_REQ_FMT, reply)[-1]

    def set(self, index, value):
        """Drive one of the requested lines high or low."""
        self._values[index] = 1 if value else 0
        self.flush()

    def flush(self):
        """Write every requested line in one ioctl."""
        padded = self._values + [0] * (64 - len(self._values))
        fcntl.ioctl(
            self._line_fd, _GPIO_SET_LINE_VALUES, struct.pack(_HANDLE_DATA_FMT, *padded)
        )

    def close(self):
        """Release the line handle and the chip.  Safe to call twice."""
        for fd in (self._line_fd, self._chip_fd):
            try:
                os.close(fd)
            except OSError:
                pass
