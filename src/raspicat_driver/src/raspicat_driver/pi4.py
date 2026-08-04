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
        """Refuse to share the motor path with rtmouse, or the PWM with the buzzer."""
        super().preflight(wiring, logger)
        if wiring.use_buzzer and wiring.buzzer_pwm_channel >= 0:
            # BCM2711's PWM0 has two channels and both are spoken for: GPIO12
            # is channel 0 and GPIO13 channel 1.  The buzzer's GPIO19 is the
            # ALT5 route to channel 1, i.e. the right motor's, so muxing it
            # would step that wheel on every beep.
            raise RuntimeError(
                "a Pi 4 has no PWM channel to spare for the buzzer: the two channels of "
                "the pwm block are the step clocks, and GPIO19 is a second route to the "
                "right motor's. Leave buzzer_pwm_channel at -1 (the line is toggled in "
                "software instead)."
            )
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
