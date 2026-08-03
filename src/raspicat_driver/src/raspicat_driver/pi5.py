"""Raspberry Pi 5 (BCM2712 + RP1) backend.

On a Pi 5 the 40-pin header's GPIO, PWM, SPI and I2C all live in the RP1
southbridge behind PCIe.  The step clock comes from rp1_pwm0, which rp1.dtsi
puts at offset 0x98000 and which pwm-rp1.c exposes as a pwmchip (GPIO12 ->
channel 0, GPIO13 -> channel 1); the header pins are on the gpiochip labelled
pinctrl-rp1.

There is no choice of driver here.  rtmouse ioremap()s the BCM2711 registers
at 0xfe000000, and nothing of the sort exists on a BCM2712, so `driver:=
raspimouse` cannot work on this model and `driver:=original` is the only
option (docs/setup/raspberry-pi-5.md).
"""

from .backend import Backend
from .backend import rtmouse_present


class Pi5Backend(Backend):
    """RP1 hardware identity."""

    name = "pi5"
    soc = "BCM2712 + RP1"
    gpiochip_label = "pinctrl-rp1"
    pwmchip_match = "98000.pwm"

    def preflight(self, wiring, logger):
        """Warn about a loaded rtmouse; it cannot reach RP1, so it cannot fight us."""
        super().preflight(wiring, logger)
        if rtmouse_present():
            logger.warning(
                "rtmouse is loaded, which cannot work on a Pi 5 (it ioremap()s BCM2711 "
                "registers). Harmless here, but /dev/rt* will not do anything."
            )
