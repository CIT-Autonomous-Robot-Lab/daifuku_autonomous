"""What the Pi 4 and Pi 5 implementations share, and how one is chosen.

Both models drive the same control board through the same three kernel
interfaces -- sysfs PWM for the step clock, the gpiochip character device for
direction and motor power, I2C for the pulse counters -- so the sequencing
lives here and each backend only carries what its SoC does differently:

                        Pi 4 (BCM2711)        Pi 5 (BCM2712 + RP1)
  gpiochip label        pinctrl-bcm2835       pinctrl-rp1
  pwmchip device        fe20c000.pwm          98000.pwm
  rtmouse               must not be loaded    cannot work at all

Pin offsets, PWM channels and I2C addresses are identical because they are
properties of the control board, not of the Pi.  They stay parameters all the
same, so a bench session can correct them without touching code.
"""

from dataclasses import dataclass
from dataclasses import field
import os

from .buzzer import SoftwareBuzzer
from .gpio import GpioInputs
from .gpio import GpioOutputs
from .gpio import chip_devices
from .gpio import chip_labels
from .gpio import find_gpiochip
from .i2c import PulseCounter
from .pwm import StepClock
from .pwm import find_pwmchip
from .pwm import pwmchips

LEFT = 0
RIGHT = 1


@dataclass
class Wiring:
    """Where the motor path is, as resolved from the node's parameters.

    Empty strings mean "let the backend decide", which is how the single
    params file stays model-independent.
    """

    gpiochip_label: str = ""
    gpiochip_device: str = ""
    gpio_direction: tuple = (16, 6)
    gpio_motor_enable: int = 5
    pwmchip_match: str = ""
    pwmchip_path: str = ""
    pwm_channel: tuple = (0, 1)
    i2c_bus: str = "/dev/i2c-1"
    i2c_address: tuple = (0x10, 0x11)
    use_pulse_counters: bool = True
    allow_rtmouse: bool = False
    gpio_leds: tuple = (25, 24, 23, 18)
    gpio_switches: tuple = (20, 26, 21)
    gpio_buzzer: int = 19
    switch_pull_up: bool = True
    # -1 means "no PWM channel is free for the buzzer, toggle the line
    # instead"; see buzzer.py for why that is the default on both models.
    buzzer_pwm_channel: int = -1
    use_leds: bool = True
    use_switches: bool = True
    use_buzzer: bool = True


@dataclass
class Hardware:
    """Every handle the node holds while it is configured."""

    gpiochip_path: str = ""
    pwmchip_path: str = ""
    gpio: object = None
    clocks: list = field(default_factory=lambda: [None, None])
    counters: list = field(default_factory=lambda: [None, None])
    # None whenever the peripheral is switched off or could not be claimed;
    # unlike the motor path, none of these are worth failing configure over.
    leds: object = None
    switches: object = None
    buzzer: object = None
    buzzer_kind: str = ""

    def close(self):
        """Release everything.  Safe to call twice, and on a half-built object."""
        for index in (LEFT, RIGHT):
            for device in (self.clocks[index], self.counters[index]):
                if device is not None:
                    device.close()
            self.clocks[index] = None
            self.counters[index] = None
        # The buzzer goes first: it has a thread of its own, and it must not
        # still be toggling a line we are about to hand back.
        for name in ("buzzer", "switches", "leds", "gpio"):
            device = getattr(self, name)
            if device is not None:
                device.close()
                setattr(self, name, None)
        self.buzzer_kind = ""


class Backend:
    """Base implementation.  Subclasses carry the per-model identity."""

    name = ""
    soc = ""
    gpiochip_label = ""
    pwmchip_match = ""

    def preflight(self, wiring, logger):
        """Refuse to touch the hardware if it is not ours to touch.

        Raises RuntimeError with an operator-readable reason.  Subclasses add
        the model-specific checks and call this one first.
        """
        if not os.path.isdir("/sys/class/pwm"):
            raise RuntimeError(
                "/sys/class/pwm does not exist. In a container it has to be mounted rw "
                "(docker/raspberrypi/compose.original.yaml); on the host it means the "
                "PWM overlay is missing from config.txt."
            )
        if wiring.use_buzzer and wiring.buzzer_pwm_channel in wiring.pwm_channel:
            # A channel drives every pin muxed to it, so this does not merely
            # fail to buzz: every beep would step that wheel.
            side = "left" if wiring.buzzer_pwm_channel == wiring.pwm_channel[LEFT] else "right"
            raise RuntimeError(
                "buzzer_pwm_channel:=%d is the %s motor's step clock. Give the buzzer a "
                "channel of its own, or leave buzzer_pwm_channel at -1 to have the line "
                "toggled in software." % (wiring.buzzer_pwm_channel, side)
            )

    def open(self, wiring, logger):
        """Claim the GPIO lines, the step clocks and (optionally) the counters.

        Order matters: the direction and motor-enable lines are claimed first,
        so a failure further down leaves the motors disabled rather than
        enabled with an unknown step clock.  The LEDs, switches and buzzer come
        last and never raise -- losing a beep must not cost us a robot.
        """
        hardware = Hardware()
        try:
            hardware.gpiochip_path = self._resolve_gpiochip(wiring)
            hardware.gpio = GpioOutputs(
                hardware.gpiochip_path,
                [
                    wiring.gpio_direction[LEFT],
                    wiring.gpio_direction[RIGHT],
                    wiring.gpio_motor_enable,
                ],
            )
            hardware.pwmchip_path = self._resolve_pwmchip(wiring)
            for side in (LEFT, RIGHT):
                hardware.clocks[side] = StepClock(
                    hardware.pwmchip_path, wiring.pwm_channel[side]
                )
            if wiring.use_pulse_counters:
                for side in (LEFT, RIGHT):
                    hardware.counters[side] = PulseCounter(
                        wiring.i2c_bus, wiring.i2c_address[side]
                    )
        except Exception:
            hardware.close()
            raise
        self._open_peripherals(hardware, wiring, logger)
        return hardware

    def _open_peripherals(self, hardware, wiring, logger):
        """Claim the LEDs, the switches and the buzzer, or log why not.

        Each is claimed on its own handle, so one line another consumer already
        holds costs only that peripheral.  None of them is worth refusing to
        drive over, so nothing here raises.
        """
        if wiring.use_leds:
            try:
                hardware.leds = GpioOutputs(hardware.gpiochip_path, wiring.gpio_leds)
            except OSError as exc:
                logger.warning("LEDs unavailable (GPIO %s): %s" % (
                    ", ".join(str(line) for line in wiring.gpio_leds), exc))

        if wiring.use_switches:
            try:
                hardware.switches = GpioInputs(
                    hardware.gpiochip_path, wiring.gpio_switches, pull_up=wiring.switch_pull_up
                )
            except OSError as exc:
                # A pin controller that refuses bias fails the whole request,
                # so fall back to whatever pull-up the board itself has.
                if not wiring.switch_pull_up:
                    logger.warning("switches unavailable: %s" % exc)
                else:
                    logger.warning(
                        "the pin controller refused an internal pull-up on the switch "
                        "lines (%s); relying on the board's own. A switch that reads "
                        "pressed at rest means there is none." % exc
                    )
                    try:
                        hardware.switches = GpioInputs(
                            hardware.gpiochip_path, wiring.gpio_switches, pull_up=False
                        )
                    except OSError as retry:
                        logger.warning("switches unavailable: %s" % retry)

        if wiring.use_buzzer:
            try:
                if wiring.buzzer_pwm_channel >= 0:
                    hardware.buzzer = StepClock(
                        hardware.pwmchip_path, wiring.buzzer_pwm_channel
                    )
                    hardware.buzzer_kind = "pwm channel %d" % wiring.buzzer_pwm_channel
                else:
                    hardware.buzzer = SoftwareBuzzer(
                        hardware.gpiochip_path, wiring.gpio_buzzer
                    )
                    hardware.buzzer_kind = "software on GPIO%d" % wiring.gpio_buzzer
            except OSError as exc:
                logger.warning("buzzer unavailable: %s" % exc)

    def _resolve_gpiochip(self, wiring):
        if wiring.gpiochip_device:
            return wiring.gpiochip_device
        label = wiring.gpiochip_label or self.gpiochip_label
        path = find_gpiochip(label)
        if path:
            return path
        # Tell the three failures apart: nothing there, nothing we may open,
        # or something there under another label.
        labels = chip_labels()
        if labels:
            raise OSError(
                "no gpiochip labelled %r (found: %s); set gpiochip_label or "
                "gpiochip_device explicitly"
                % (label, ", ".join("%s=%s" % item for item in sorted(labels.items())))
            )
        devices = chip_devices()
        if devices:
            raise OSError(
                "none of %s could be opened; the container user (uid 1000) has to own "
                "them -- see tools/image/udev/99-daifuku-raspicat.rules"
                % ", ".join(devices)
            )
        raise OSError(
            "there is no /dev/gpiochip* at all; is /dev mounted into this container?"
        )

    def _resolve_pwmchip(self, wiring):
        if wiring.pwmchip_path:
            return wiring.pwmchip_path
        match = wiring.pwmchip_match or self.pwmchip_match
        path = find_pwmchip(match)
        if path:
            return path
        raise OSError(
            "no pwmchip matching %r under /sys/class/pwm (found: %s); is the PWM "
            "overlay in config.txt and is /sys/class/pwm mounted rw in this container?"
            % (match, ", ".join(sorted(pwmchips())) or "none")
        )


def rtmouse_present():
    """True if the rtmouse kernel module is loaded on this host.

    Checked through /proc/modules and the device files it creates, not through
    pinctrl: rtmouse ioremap()s the registers and never registers a pinctrl
    consumer, so the kernel cannot tell us about the conflict.  /proc and /dev
    are the host's inside the container, so this works from either side.
    """
    try:
        with open("/proc/modules") as handle:
            for line in handle:
                if line.startswith("rtmouse "):
                    return True
    except OSError:
        pass
    return any(
        os.path.exists(path)
        for path in ("/dev/rtmotoren0", "/dev/rtmotor_raw_l0", "/dev/rtmotor_raw_r0")
    )


def detect_model():
    """Return "pi4", "pi5" or None, from the device tree or /proc/cpuinfo."""
    compatible = _read_text("/proc/device-tree/compatible").replace("\0", " ")
    model = _read_text("/proc/device-tree/model").replace("\0", " ")
    if not model:
        for line in _read_text("/proc/cpuinfo").splitlines():
            if line.startswith("Model"):
                model = line.split(":", 1)[-1].strip()
                break
    haystack = "%s %s" % (compatible, model)
    # The SoC is the thing that decides, so prefer it over the marketing name.
    if "bcm2712" in haystack or "Raspberry Pi 5" in haystack:
        return "pi5"
    if "bcm2711" in haystack or "Raspberry Pi 4" in haystack:
        return "pi4"
    return None


def _read_text(path):
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def create_backend(model):
    """Return the backend for `model`, or for this machine when it is "auto"."""
    # Imported here rather than at module scope: pi4/pi5 subclass Backend.
    from .pi4 import Pi4Backend
    from .pi5 import Pi5Backend

    backends = {"pi4": Pi4Backend, "pi5": Pi5Backend}
    resolved = model
    if model in ("", "auto"):
        resolved = detect_model()
        if resolved is None:
            raise RuntimeError(
                "could not tell a Pi 4 from a Pi 5 here (/proc/device-tree/model and "
                "/proc/cpuinfo say nothing useful); set the model parameter explicitly"
            )
    if resolved not in backends:
        raise RuntimeError(
            "model:=%s is not supported; use auto, %s"
            % (model, " or ".join(sorted(backends)))
        )
    return backends[resolved]()
