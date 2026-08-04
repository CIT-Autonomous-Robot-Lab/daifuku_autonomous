"""GPIO character device access, v1 uAPI.

v1 is deprecated in libgpiod terms but the kernel still supports it, and its
request struct is one fixed layout with no pointers, which keeps this module
dependency-free.  The Humble container image has neither python3-libgpiod nor
python3-gpiod, and adding one would force a `docker compose build`.

The same code covers both models: a Pi 4 exposes the header pins through the
gpiochip labelled "pinctrl-bcm2835" and a Pi 5 through "pinctrl-rp1", but the
line offsets are the BCM numbers on both.

Outputs (direction, motor power, LEDs, the software buzzer) and inputs (the
push switches) are separate handles even on the same chip, so a chip that
refuses one bundle -- a line already claimed by another consumer, say -- does
not cost us the others.
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
_GPIO_GET_LINE_VALUES = _ioc(_IOC_READ | _IOC_WRITE, _GPIO_MAGIC, 0x08, _HANDLE_DATA_SIZE)
_GPIO_SET_LINE_VALUES = _ioc(_IOC_READ | _IOC_WRITE, _GPIO_MAGIC, 0x09, _HANDLE_DATA_SIZE)

_GPIOHANDLE_REQUEST_INPUT = 1 << 0
_GPIOHANDLE_REQUEST_OUTPUT = 1 << 1
# Kernel 5.5 and later only.  Both images are well past that (5.15 on the Pi 4
# image, 6.8 on the Pi 5 one), but a chip may still refuse bias on a given
# line, so the caller is expected to retry without it.
_GPIOHANDLE_REQUEST_BIAS_PULL_UP = 1 << 5


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


class _GpioLines:
    """One line handle on one gpiochip: several lines claimed in one request."""

    def __init__(self, chip_path, offsets, flags):
        self._offsets = list(offsets)
        self._chip_fd = os.open(chip_path, os.O_RDWR | os.O_CLOEXEC)
        try:
            fields = self._offsets + [0] * (64 - len(self._offsets))
            fields.append(flags)
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

    def close(self):
        """Release the line handle and the chip.  Safe to call twice."""
        for fd in (self._line_fd, self._chip_fd):
            try:
                os.close(fd)
            except OSError:
                pass


class GpioOutputs(_GpioLines):
    """Output lines on one gpiochip, requested together and set together."""

    def __init__(self, chip_path, offsets):
        super().__init__(chip_path, offsets, _GPIOHANDLE_REQUEST_OUTPUT)
        self._values = [0] * len(self._offsets)

    def set(self, index, value):
        """Drive one of the requested lines high or low."""
        self._values[index] = 1 if value else 0
        self.flush()

    def set_many(self, values):
        """Drive every requested line, in one ioctl."""
        self._values = [1 if value else 0 for value in values]
        self.flush()

    def flush(self):
        """Write every requested line in one ioctl."""
        padded = self._values + [0] * (64 - len(self._values))
        fcntl.ioctl(
            self._line_fd, _GPIO_SET_LINE_VALUES, struct.pack(_HANDLE_DATA_FMT, *padded)
        )


class GpioInputs(_GpioLines):
    """Input lines on one gpiochip, read together in one ioctl.

    `pull_up` asks the pin controller for the internal pull-up.  The switches
    on the control board are wired to ground, so without a pull-up -- from the
    board or from here -- an open switch floats and reads as noise.
    """

    def __init__(self, chip_path, offsets, pull_up=False):
        flags = _GPIOHANDLE_REQUEST_INPUT
        if pull_up:
            flags |= _GPIOHANDLE_REQUEST_BIAS_PULL_UP
        super().__init__(chip_path, offsets, flags)

    def read(self):
        """Return the level of every requested line, in the order requested."""
        reply = fcntl.ioctl(
            self._line_fd, _GPIO_GET_LINE_VALUES, bytes(_HANDLE_DATA_SIZE)
        )
        values = struct.unpack(_HANDLE_DATA_FMT, reply)
        return list(values[: len(self._offsets)])
