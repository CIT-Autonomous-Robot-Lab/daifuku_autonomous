"""Raspberry Pi 4 (BCM2711) backend.

The step clock comes from the SoC PWM block at 0xfe20c000, which the
`pwm-2chan` overlay hands to pwm-bcm2835.c as a two-channel pwmchip (GPIO12 ->
channel 0, GPIO13 -> channel 1).  Direction and motor power are ordinary GPIO
lines on the pinctrl-bcm2835 chip.

This backend deliberately does not go through rtmouse.  Pi 4 can run either
driver, and the choice is made by robot_bringup.launch.py's driver:= argument:

  driver:=raspimouse  upstream raspimouse2 on top of the rtmouse module
  driver:=original    this backend, no kernel module involved

The two cannot run at once, and the kernel will not stop you.  rtmouse
ioremap()s the GPIO and PWM registers instead of registering a pinctrl
consumer, so its writes are invisible to the kernel PWM and gpiochip drivers:
both would own the direction lines, and the wheels can turn the wrong way with
the motors enabled.  Hence the refusal below.
"""

from .backend import Backend
from .backend import rtmouse_present


class Pi4Backend(Backend):
    """BCM2711 hardware identity, plus the rtmouse exclusion."""

    name = "pi4"
    soc = "BCM2711"
    gpiochip_label = "pinctrl-bcm2835"
    pwmchip_match = "fe20c000.pwm"

    def preflight(self, wiring, logger):
        """Refuse to share the motor path with rtmouse."""
        super().preflight(wiring, logger)
        if not rtmouse_present():
            return
        if wiring.allow_rtmouse:
            logger.warning(
                "rtmouse is loaded and allow_rtmouse is true: this driver and rtmouse "
                "now both own GPIO 16/6/5 and the PWM block. Expect wrong-direction "
                "motion."
            )
            return
        raise RuntimeError(
            "rtmouse is loaded on this host. It writes the GPIO and PWM registers "
            "directly, so it would fight this driver for the direction lines and the "
            "step clock. Use driver:=raspimouse instead, or remove rtmouse "
            "(rmmod rtmouse and delete /etc/modules-load.d/rtmouse.conf; images can be "
            "built with create_image.py --no-rtmouse). Set allow_rtmouse:=true only to "
            "override this on purpose."
        )
