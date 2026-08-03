"""Hardware PWM through the kernel PWM subsystem (sysfs).

The stepper drivers on the control board take a step clock, and rtmouse
produces it by writing RNG/DAT on the SoC PWM block.  Both of the kernel
drivers we go through instead -- pwm-bcm2835.c on a Pi 4 and pwm-rp1.c on a
Pi 5 -- turn a period/duty pair into the same two register writes, so this
module is model-independent; only the pwmchip that backs it differs.

There is no character device for PWM, so sysfs is the only interface.  In a
container that means /sys/class/pwm has to be mounted rw (see
docker/raspberrypi/compose.original.yaml) and the attributes have to be owned
by the container user (tools/image/udev/99-daifuku-raspicat.rules).
"""

import os
import time


def find_pwmchip(match):
    """Return the /sys/class/pwm/pwmchipN backing the given device, or None.

    `match` is matched against the resolved device path -- "fe20c000.pwm" on a
    Pi 4, "98000.pwm" (the RP1 offset from rp1.dtsi) on a Pi 5.  The pwmchip
    number itself moves between kernel releases and must not be hardcoded.
    """
    for path, device in pwmchips().items():
        if match in device:
            return path
    return None


def pwmchips():
    """Return {sysfs path: resolved device path} for every pwmchip present.

    Also used to make the "no such pwmchip" error say what there was instead.
    """
    base = "/sys/class/pwm"
    found = {}
    if not os.path.isdir(base):
        return found
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        # The class entry is a symlink into /sys/devices, and the device link
        # points at the platform device -- the SoC address lives in both.
        found[path] = "%s %s" % (
            os.path.realpath(path),
            os.path.realpath(os.path.join(path, "device")),
        )
    return found


class StepClock:
    """One PWM channel driving a stepper step clock at 50% duty."""

    def __init__(self, chip_dir, channel, settle_timeout=2.0):
        self._dir = os.path.join(chip_dir, "pwm%d" % channel)
        self._chip_dir = chip_dir
        self._channel = channel
        self._period_ns = 0
        self._enabled = False
        if not os.path.isdir(self._dir):
            sysfs_write(os.path.join(chip_dir, "export"), channel)
            deadline = time.monotonic() + settle_timeout
            while not os.path.isdir(self._dir):
                if time.monotonic() > deadline:
                    raise OSError("%s did not appear after export" % self._dir)
                time.sleep(0.01)
        # udev has to chown the attributes that export just created, and it may
        # not have run yet, so the first write tolerates EACCES for a moment.
        self._write("duty_cycle", 0, tolerate_eacces=settle_timeout)

    def set_frequency(self, freq_hz):
        """Set the step frequency.  0 or less stops the clock (duty 0)."""
        if freq_hz <= 0:
            self._write("duty_cycle", 0)
            return
        period_ns = int(round(1e9 / freq_hz))
        if period_ns != self._period_ns:
            # duty must never exceed period, so collapse it before shrinking
            # the period and restore it afterwards.
            self._write("duty_cycle", 0)
            self._write("period", period_ns)
            self._period_ns = period_ns
        self._write("duty_cycle", period_ns // 2)
        if not self._enabled:
            self._write("enable", 1)
            self._enabled = True

    def stop(self):
        """Hold the clock low without disabling the channel (no re-arm glitch)."""
        self._write("duty_cycle", 0)

    def close(self):
        """Stop, disable and unexport the channel.  Safe to call twice."""
        try:
            self.stop()
            if self._enabled:
                self._write("enable", 0)
                self._enabled = False
            sysfs_write(os.path.join(self._chip_dir, "unexport"), self._channel)
        except OSError:
            pass

    def _write(self, attribute, value, tolerate_eacces=0.0):
        sysfs_write(os.path.join(self._dir, attribute), value, tolerate_eacces)


def sysfs_write(path, value, tolerate_eacces=0.0):
    """Write an integer to a sysfs attribute, optionally waiting out EACCES."""
    deadline = time.monotonic() + tolerate_eacces
    while True:
        try:
            with open(path, "w") as handle:
                handle.write("%d" % value)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
