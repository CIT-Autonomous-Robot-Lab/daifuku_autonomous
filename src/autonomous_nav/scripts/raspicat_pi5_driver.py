#!/usr/bin/env python3

"""Userspace Raspberry Pi Cat motor driver for the Raspberry Pi 5 (RP1).

The rtmouse kernel module cannot work on a Pi 5: it ioremap()s the BCM2711
GPIO, PWM and clock-manager registers at 0xfe000000, and on a Pi 5 those
peripherals live in the RP1 southbridge behind PCIe.  This node replaces
rtmouse *and* the raspimouse node for the motor path, entirely from userspace:

  step clock   RP1 hardware PWM via /sys/class/pwm  (GPIO12 -> ch0, GPIO13 -> ch1)
  direction    GPIO character device, v1 uAPI       (GPIO16 left, GPIO6 right)
  motor enable same                                  (GPIO5)
  odometry     I2C pulse counters at 0x10 / 0x11 on /dev/i2c-1

The contract above this node is identical to raspimouse, so robot_bringup,
the EKF and Nav2 are unchanged:

  subscribe  cmd_vel        geometry_msgs/Twist
  publish    odom           nav_msgs/Odometry
  publish    odom -> base_footprint TF   (publish_tf, unlike raspimouse)
  service    motor_power    std_srvs/SetBool
  lifecycle  configure -> activate, driven by robot_bringup.launch.py

Deliberately not provided: LEDs, buzzer, switches and light sensors.  Nothing
in this workspace subscribes to /leds or /buzzer or reads /switches or
/light_sensors -- only raspimouse's own parameters mention them.

Two deliberate differences from raspimouse:
  * pulses_per_revolution is used to convert cmd_vel to a step frequency.
    raspimouse_component.cpp hardcodes 400.0 there and only honours the
    parameter in the odometry path.
  * with the pulse counters live, the published Twist is measured rather than
    commanded.  mid360_ekf.yaml takes vx and vyaw (and nothing else) from this
    message, so feeding it the command would close a loop on our own output.

An I2C stall here cannot wedge the robot the way rtmouse does.  rtmouse holds
a kernel mutex across the transfer, so one timeout leaves every reader of
/dev/rtcounter_* in permanent D state and only a reboot recovers (see
config/README.md).  Here the ioctl returns ETIMEDOUT to us, the counter timer
runs in its own callback group, and repeated failures fall back to integrating
cmd_vel until the bus answers again.

None of this has been run on hardware -- there is no Pi 5 Raspberry Pi Cat.
Every pin, channel, address and device path is a parameter so the first bench
session can correct them without touching the logic.
"""

import ctypes
import fcntl
import math
import os
import struct
import sys
import threading
import time

import rclpy
from geometry_msgs.msg import TransformStamped
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.lifecycle import LifecycleNode
from rclpy.lifecycle import LifecycleState
from rclpy.lifecycle import TransitionCallbackReturn
from std_srvs.srv import SetBool
from tf2_ros import TransformBroadcaster

CONSUMER = "raspicat_pi5_driver"


# --------------------------------------------------------------------------
# ioctl plumbing
# --------------------------------------------------------------------------
# _IOC() from asm-generic/ioctl.h.  fcntl.ioctl() wants a value that fits in a
# signed int, and every direction we use here sets bit 31, so fold it round.

_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction, type_, nr, size):
    value = (direction << 30) | (size << 16) | (type_ << 8) | nr
    if value >= 0x80000000:
        value -= 0x100000000
    return value


# --------------------------------------------------------------------------
# GPIO character device (v1 uAPI)
# --------------------------------------------------------------------------
# v1 is deprecated in libgpiod terms but the kernel still supports it, and its
# request struct is one fixed layout with no pointers, which keeps this file
# dependency-free.  The Humble container image has neither python3-libgpiod nor
# python3-gpiod, and adding one would force a `docker compose build`.

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


def find_gpiochip(label):
    """Return the /dev/gpiochipN whose driver label matches, or None.

    On a Pi 5 the RP1 bank is labelled "pinctrl-rp1", but its chip number has
    moved between kernel releases, so match on the label rather than the digit.
    """
    for name in sorted(os.listdir("/dev")):
        if not name.startswith("gpiochip"):
            continue
        path = os.path.join("/dev", name)
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
        found = struct.unpack(_CHIPINFO_FMT, info)[1].split(b"\0")[0].decode()
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
        self._values[index] = 1 if value else 0
        self.flush()

    def flush(self):
        padded = self._values + [0] * (64 - len(self._values))
        fcntl.ioctl(
            self._line_fd, _GPIO_SET_LINE_VALUES, struct.pack(_HANDLE_DATA_FMT, *padded)
        )

    def close(self):
        for fd in (self._line_fd, self._chip_fd):
            try:
                os.close(fd)
            except OSError:
                pass


# --------------------------------------------------------------------------
# RP1 hardware PWM through sysfs
# --------------------------------------------------------------------------


def find_pwmchip(match):
    """Return the /sys/class/pwm/pwmchipN backing the given device, or None.

    `match` is matched against the resolved device path.  rp1.dtsi puts
    rp1_pwm0 at offset 0x98000, so the default "98000.pwm" picks the block that
    owns GPIO12/13/18/19.  The pwmchip number itself moves between kernel
    releases and must not be hardcoded.
    """
    base = "/sys/class/pwm"
    if not os.path.isdir(base):
        return None
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        candidates = (
            os.path.realpath(path),
            os.path.realpath(os.path.join(path, "device")),
        )
        if any(match in candidate for candidate in candidates):
            return path
    return None


class PwmStepClock:
    """One RP1 PWM channel driving a stepper step clock at 50% duty.

    rtmouse writes RNG/DAT on the BCM PWM block for exactly this; pwm-rp1.c
    writes PWM_RANGE/PWM_DUTY plus a SET_UPDATE bit, so a frequency change here
    is the same two register writes, reached through the kernel PWM subsystem.
    """

    def __init__(self, chip_dir, channel, settle_timeout=2.0):
        self._dir = os.path.join(chip_dir, "pwm%d" % channel)
        self._chip_dir = chip_dir
        self._channel = channel
        self._period_ns = 0
        self._enabled = False
        if not os.path.isdir(self._dir):
            _sysfs_write(os.path.join(chip_dir, "export"), channel)
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
        try:
            self.stop()
            if self._enabled:
                self._write("enable", 0)
                self._enabled = False
            _sysfs_write(os.path.join(self._chip_dir, "unexport"), self._channel)
        except OSError:
            pass

    def _write(self, attribute, value, tolerate_eacces=0.0):
        _sysfs_write(os.path.join(self._dir, attribute), value, tolerate_eacces)


def _sysfs_write(path, value, tolerate_eacces=0.0):
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


# --------------------------------------------------------------------------
# I2C pulse counters
# --------------------------------------------------------------------------

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
    """One of the two 16-bit pulse counters on the control board.

    Register-addressed reads, done as one combined write+read transaction so
    the repeated START matches what i2c_smbus_read_byte_data() issues in
    rtmouse.  Every failure surfaces as OSError -- that is the whole point of
    doing this from userspace.
    """

    REG_MSB = 0x10
    REG_LSB = 0x11

    def __init__(self, bus_path, address):
        self._fd = os.open(bus_path, os.O_RDWR | os.O_CLOEXEC)
        self._address = address

    def read(self):
        """Return the raw 16-bit count.  Raises OSError if the bus does not answer."""
        lsb = self._read_register(self.REG_LSB)
        msb = self._read_register(self.REG_MSB)
        return ((msb << 8) | lsb) & 0xFFFF

    def close(self):
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


# --------------------------------------------------------------------------
# Node
# --------------------------------------------------------------------------

LEFT = 0
RIGHT = 1
# Index into the GPIO line bundle requested in _open_hardware(); the first two
# are the direction lines, so LEFT/RIGHT double as their indices.
MOTOR_ENABLE = 2


class RaspicatPi5Driver(LifecycleNode):

    def __init__(self):
        super().__init__("raspicat_pi5_driver")

        self.declare_parameter("use_pulse_counters", True)
        self.declare_parameter("odometry_scale_left_wheel", 1.0)
        self.declare_parameter("odometry_scale_right_wheel", 1.0)
        self.declare_parameter("wheel_diameter", 0.2)
        self.declare_parameter("wheel_tread", 0.35)
        self.declare_parameter("pulses_per_revolution", 400.0)
        self.declare_parameter("odom_hz", 50.0)
        self.declare_parameter("initial_motor_power", False)
        self.declare_parameter("cmd_vel_timeout", 60.0)
        self.declare_parameter("publish_tf", True)
        self.declare_parameter("odom_frame_id", "odom")
        self.declare_parameter("odom_child_frame_id", "base_footprint")
        self.declare_parameter("odom_frame_prefix", "")

        self.declare_parameter("min_step_frequency", 5.0)
        self.declare_parameter("max_step_frequency", 10000.0)

        self.declare_parameter("gpiochip_label", "pinctrl-rp1")
        self.declare_parameter("gpiochip_device", "")
        self.declare_parameter("gpio_direction_left", 16)
        self.declare_parameter("gpio_direction_right", 6)
        self.declare_parameter("gpio_motor_enable", 5)
        self.declare_parameter("direction_left_forward_level", 0)
        self.declare_parameter("direction_right_forward_level", 1)

        self.declare_parameter("pwmchip_match", "98000.pwm")
        self.declare_parameter("pwmchip_path", "")
        self.declare_parameter("pwm_channel_left", 0)
        self.declare_parameter("pwm_channel_right", 1)

        self.declare_parameter("i2c_bus", "/dev/i2c-1")
        self.declare_parameter("i2c_address_left", 0x10)
        self.declare_parameter("i2c_address_right", 0x11)
        self.declare_parameter("counter_error_limit", 5)
        self.declare_parameter("counter_retry_period", 1.0)

        self._motor_group = MutuallyExclusiveCallbackGroup()
        self._odom_group = MutuallyExclusiveCallbackGroup()

        self._state_lock = threading.Lock()
        self._gpio = None
        self._pwm = [None, None]
        self._counters = [None, None]
        self._odom_pub = None
        self._tf_broadcaster = None
        self._odom_timer = None
        self._watchdog_timer = None
        self._cmd_vel_sub = None
        self._motor_power_srv = None
        self._forward = [True, True]

        self._reset_state()

    # -- lifecycle ---------------------------------------------------------

    def on_configure(self, state: LifecycleState) -> TransitionCallbackReturn:
        try:
            self._load_parameters()
            self._open_hardware()
        except Exception as exc:
            # rclpy swallows an exception here and reports ERROR with no
            # message, so catch it while we still know what happened.
            self.get_logger().error("configure failed: %s" % exc)
            self._close_hardware()
            return TransitionCallbackReturn.FAILURE

        self._reset_state()

        self._odom_pub = self.create_lifecycle_publisher(Odometry, "odom", 10)
        self._tf_broadcaster = TransformBroadcaster(self)
        self._cmd_vel_sub = self.create_subscription(
            Twist, "cmd_vel", self._on_cmd_vel, 10, callback_group=self._motor_group
        )
        self._motor_power_srv = self.create_service(
            SetBool,
            "motor_power",
            self._on_motor_power,
            callback_group=self._motor_group,
        )

        period = 1.0 / max(self._odom_hz, 1.0)
        self._odom_timer = self.create_timer(
            period, self._publish_odometry, callback_group=self._odom_group
        )
        self._odom_timer.cancel()
        self._watchdog_timer = self.create_timer(
            1.0, self._check_watchdog, callback_group=self._motor_group
        )
        self._watchdog_timer.cancel()

        self._set_motor_power(False)
        self.get_logger().info(
            "configured: gpiochip=%s pwmchip=%s counters=%s"
            % (self._gpiochip_path, self._pwmchip_path,
               "on" if self._use_pulse_counters else "off")
        )
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: LifecycleState) -> TransitionCallbackReturn:
        super().on_activate(state)
        self._reset_runtime()
        self._arm_directions()
        self._active = True
        self._odom_timer.reset()
        self._watchdog_timer.reset()
        self._set_motor_power(self._initial_motor_power)
        self.get_logger().info("activated")
        return TransitionCallbackReturn.SUCCESS

    def on_deactivate(self, state: LifecycleState) -> TransitionCallbackReturn:
        self._active = False
        self._stop_motors()
        self._set_motor_power(False)
        if self._odom_timer is not None:
            self._odom_timer.cancel()
        if self._watchdog_timer is not None:
            self._watchdog_timer.cancel()
        super().on_deactivate(state)
        return TransitionCallbackReturn.SUCCESS

    def on_cleanup(self, state: LifecycleState) -> TransitionCallbackReturn:
        self.teardown()
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: LifecycleState) -> TransitionCallbackReturn:
        self.teardown()
        return TransitionCallbackReturn.SUCCESS

    def on_error(self, state: LifecycleState) -> TransitionCallbackReturn:
        self.get_logger().error("error processing; stopping motors")
        self.teardown()
        return TransitionCallbackReturn.SUCCESS

    def teardown(self):
        """Stop the wheels and release every handle.  Safe to call twice."""
        self._active = False
        self._stop_motors()
        self._set_motor_power(False)
        self._close_hardware()
        # Entities are recreated by on_configure, so drop them here rather than
        # leaving a second subscription/timer behind after a cleanup+configure.
        for timer in (self._odom_timer, self._watchdog_timer):
            if timer is not None:
                timer.cancel()
                self.destroy_timer(timer)
        self._odom_timer = None
        self._watchdog_timer = None
        if self._cmd_vel_sub is not None:
            self.destroy_subscription(self._cmd_vel_sub)
            self._cmd_vel_sub = None
        if self._motor_power_srv is not None:
            self.destroy_service(self._motor_power_srv)
            self._motor_power_srv = None
        if self._odom_pub is not None:
            self.destroy_lifecycle_publisher(self._odom_pub)
            self._odom_pub = None
        self._tf_broadcaster = None

    # -- setup -------------------------------------------------------------

    def _load_parameters(self):
        get = self.get_parameter
        self._use_pulse_counters = get("use_pulse_counters").value
        self._scale = (
            get("odometry_scale_left_wheel").value,
            get("odometry_scale_right_wheel").value,
        )
        self._wheel_diameter = get("wheel_diameter").value
        self._wheel_tread = get("wheel_tread").value
        self._pulses_per_revolution = get("pulses_per_revolution").value
        self._odom_hz = get("odom_hz").value
        # Longest interval still worth integrating cmd_vel over.  Generous
        # against scheduling jitter, well under the ~1 s an I2C adapter takes
        # to give up on a stuck bus.
        self._max_odom_dt = max(5.0 / max(self._odom_hz, 1.0), 0.2)
        self._initial_motor_power = get("initial_motor_power").value
        self._cmd_vel_timeout = get("cmd_vel_timeout").value
        self._publish_tf = get("publish_tf").value
        self._min_step = get("min_step_frequency").value
        self._max_step = get("max_step_frequency").value
        self._counter_error_limit = get("counter_error_limit").value
        self._counter_retry_period = get("counter_retry_period").value

        prefix = get("odom_frame_prefix").value
        self._odom_frame = get("odom_frame_id").value
        self._base_frame = get("odom_child_frame_id").value
        if prefix:
            self._odom_frame = "%s/%s" % (prefix, self._odom_frame)
            self._base_frame = "%s/%s" % (prefix, self._base_frame)

        if self._wheel_diameter <= 0.0 or self._wheel_tread <= 0.0:
            raise ValueError("wheel_diameter and wheel_tread must be positive")
        if self._pulses_per_revolution <= 0.0:
            raise ValueError("pulses_per_revolution must be positive")

        self._forward_level = (
            get("direction_left_forward_level").value,
            get("direction_right_forward_level").value,
        )

    def _open_hardware(self):
        get = self.get_parameter

        chip = get("gpiochip_device").value
        if not chip:
            chip = find_gpiochip(get("gpiochip_label").value)
        if not chip:
            raise OSError(
                "no gpiochip labelled %r; set gpiochip_device explicitly"
                % get("gpiochip_label").value
            )
        self._gpiochip_path = chip
        self._gpio = GpioOutputs(
            chip,
            [
                get("gpio_direction_left").value,
                get("gpio_direction_right").value,
                get("gpio_motor_enable").value,
            ],
        )
        self._arm_directions()

        pwmchip = get("pwmchip_path").value
        if not pwmchip:
            pwmchip = find_pwmchip(get("pwmchip_match").value)
        if not pwmchip:
            raise OSError(
                "no pwmchip matching %r under /sys/class/pwm; is the RP1 PWM "
                "overlay enabled and is /sys/class/pwm writable in this "
                "container?" % get("pwmchip_match").value
            )
        self._pwmchip_path = pwmchip
        self._pwm[LEFT] = PwmStepClock(pwmchip, get("pwm_channel_left").value)
        self._pwm[RIGHT] = PwmStepClock(pwmchip, get("pwm_channel_right").value)

        if self._use_pulse_counters:
            bus = get("i2c_bus").value
            self._counters[LEFT] = PulseCounter(bus, get("i2c_address_left").value)
            self._counters[RIGHT] = PulseCounter(bus, get("i2c_address_right").value)

    def _close_hardware(self):
        for index in (LEFT, RIGHT):
            for device in (self._pwm[index], self._counters[index]):
                if device is not None:
                    device.close()
            self._pwm[index] = None
            self._counters[index] = None
        if self._gpio is not None:
            self._gpio.close()
            self._gpio = None

    def _reset_state(self):
        """Full reset, for configure time only."""
        self._pose = [0.0, 0.0, 0.0]          # x, y, theta
        self._commanded = (0.0, 0.0)          # linear [m/s], angular [rad/s]
        self._reset_runtime()

    def _reset_runtime(self):
        """Reset what must not carry across an inactive period.

        The pose is deliberately kept: a deactivate/activate cycle must not
        teleport the odom frame out from under emcl2.
        """
        self._active = False
        self._motor_power = False
        self._last_cmd_time = time.monotonic()
        self._watchdog_tripped = False
        self._last_odom_time = None
        self._counters_ready = False
        self._counter_errors = 0
        self._counter_retry_at = 0.0
        self._counter_degraded = False
        self._last_raw = [0, 0]

    def _arm_directions(self):
        """Drive both direction lines to "forward" and record that we did.

        The lines come up low, but forward is level 0 on the left and 1 on the
        right (rtmouse clears MOTDIR_L and sets MOTDIR_R for a positive
        frequency), so the right line has to be written before _forward can
        claim both wheels are pointing forward.
        """
        self._forward = [True, True]
        if self._gpio is None:
            return
        self._gpio.set(LEFT, self._forward_level[LEFT])
        self._gpio.set(RIGHT, self._forward_level[RIGHT])

    # -- motor path (motor callback group) ---------------------------------

    def _on_cmd_vel(self, msg):
        with self._state_lock:
            self._commanded = (msg.linear.x, msg.angular.z)
            self._last_cmd_time = time.monotonic()
            self._watchdog_tripped = False
        self._apply_velocity(msg.linear.x, msg.angular.z)

    def _apply_velocity(self, linear, angular):
        radius = self._wheel_diameter / 2.0
        omega_left = (linear - angular * self._wheel_tread / 2.0) / radius
        omega_right = (linear + angular * self._wheel_tread / 2.0) / radius
        turns_to_steps = self._pulses_per_revolution / (2.0 * math.pi)
        self._drive(LEFT, omega_left * turns_to_steps)
        self._drive(RIGHT, omega_right * turns_to_steps)

    def _drive(self, side, frequency):
        # rtmouse resets anything below MOTOR_UNCONTROLLABLE_FREQ to zero
        # because the driver cannot hold a step that slow.
        if abs(frequency) < self._min_step:
            frequency = 0.0
        frequency = max(-self._max_step, min(self._max_step, frequency))

        if frequency == 0.0:
            # Stop the clock but leave the direction line alone: pulses already
            # counted and not yet read must keep the sign of the motion that
            # produced them.
            if self._pwm[side] is not None:
                try:
                    self._pwm[side].stop()
                except OSError as exc:
                    self.get_logger().error("step clock stop failed: %s" % exc)
            return
        forward = frequency > 0.0

        # Direction before clock: a stepper already running must not meet the
        # next edge with the direction line still on the old level.
        if self._gpio is not None and forward != self._forward[side]:
            level = self._forward_level[side] if forward else 1 - self._forward_level[side]
            try:
                self._gpio.set(side, level)
            except OSError as exc:
                self.get_logger().error("direction line write failed: %s" % exc)
                return
        # _read_counters signs each delta with this, from the other thread.
        with self._state_lock:
            self._forward[side] = forward

        if self._pwm[side] is None:
            return
        try:
            self._pwm[side].set_frequency(abs(frequency))
        except OSError as exc:
            self.get_logger().error("step clock write failed: %s" % exc)

    def _stop_motors(self):
        with self._state_lock:
            self._commanded = (0.0, 0.0)
        for side in (LEFT, RIGHT):
            if self._pwm[side] is not None:
                try:
                    self._pwm[side].stop()
                except OSError as exc:
                    self.get_logger().error("step clock stop failed: %s" % exc)

    def _set_motor_power(self, enabled):
        if self._gpio is None:
            return
        try:
            self._gpio.set(MOTOR_ENABLE, 1 if enabled else 0)
        except OSError as exc:
            self.get_logger().error("motor enable write failed: %s" % exc)
            return
        self._motor_power = bool(enabled)
        if enabled:
            with self._state_lock:
                self._last_cmd_time = time.monotonic()
        else:
            self._stop_motors()
        self.get_logger().info("motors %s" % ("on" if enabled else "off"))

    def _on_motor_power(self, request, response):
        self._set_motor_power(request.data)
        response.success = True
        response.message = "Motors are on" if request.data else "Motors are off"
        return response

    def _check_watchdog(self):
        if not self._motor_power:
            return
        with self._state_lock:
            idle = time.monotonic() - self._last_cmd_time
            tripped = self._watchdog_tripped
        if tripped or idle < self._cmd_vel_timeout:
            return
        with self._state_lock:
            self._watchdog_tripped = True
        self.get_logger().warning("no cmd_vel for %.1f s; stopping motors" % idle)
        self._stop_motors()

    # -- odometry path (odom callback group) -------------------------------

    def _publish_odometry(self):
        if not self._active or self._odom_pub is None:
            return
        now = self.get_clock().now()
        if self._last_odom_time is None:
            self._last_odom_time = now
            return
        dt = (now - self._last_odom_time).nanoseconds / 1e9
        self._last_odom_time = now
        if dt <= 0.0:
            return
        travelled = self._read_counters() if self._use_pulse_counters else None
        if travelled is not None:
            left, right = travelled
            # Same order as raspimouse_component.cpp: heading first, then the
            # step is taken along the new heading.  No clamp is needed on this
            # branch -- a pulse delta is exact however long the interval was.
            delta_theta = math.atan2(right - left, self._wheel_tread)
            advance = (left + right) / 2.0
            self._pose[2] += delta_theta
            self._pose[0] += advance * math.cos(self._pose[2])
            self._pose[1] += advance * math.sin(self._pose[2])
            linear = advance / dt
            angular = delta_theta / dt
        else:
            with self._state_lock:
                linear, angular = self._commanded
            # A timed-out I2C transfer blocks this callback for about a second.
            # Integrating a second-old command in one step would jump the pose
            # by up to a full second of travel, so drop the interval instead.
            if dt > self._max_odom_dt:
                self.get_logger().warning(
                    "odom interval stretched to %.2f s; not integrating cmd_vel over it" % dt
                )
            else:
                self._pose[0] += linear * math.cos(self._pose[2]) * dt
                self._pose[1] += linear * math.sin(self._pose[2]) * dt
                self._pose[2] += angular * dt

        stamp = now.to_msg()
        half = self._pose[2] / 2.0
        qz = math.sin(half)
        qw = math.cos(half)

        message = Odometry()
        message.header.stamp = stamp
        message.header.frame_id = self._odom_frame
        message.child_frame_id = self._base_frame
        message.pose.pose.position.x = self._pose[0]
        message.pose.pose.position.y = self._pose[1]
        message.pose.pose.orientation.z = qz
        message.pose.pose.orientation.w = qw
        message.twist.twist.linear.x = linear
        message.twist.twist.angular.z = angular
        self._odom_pub.publish(message)

        if not self._publish_tf or self._tf_broadcaster is None:
            return
        transform = TransformStamped()
        transform.header.stamp = stamp
        transform.header.frame_id = self._odom_frame
        transform.child_frame_id = self._base_frame
        transform.transform.translation.x = self._pose[0]
        transform.transform.translation.y = self._pose[1]
        transform.transform.rotation.z = qz
        transform.transform.rotation.w = qw
        self._tf_broadcaster.sendTransform(transform)

    def _read_counters(self):
        """Distance travelled by each wheel since the last call, in metres.

        Returns None when the counters are unavailable, which tells the caller
        to fall back to integrating cmd_vel for this tick.
        """
        if self._counters[LEFT] is None or self._counters[RIGHT] is None:
            return None

        if not self._counters_ready:
            if time.monotonic() < self._counter_retry_at:
                return None
            try:
                self._last_raw = [self._counters[LEFT].read(), self._counters[RIGHT].read()]
            except OSError:
                self._counter_retry_at = time.monotonic() + self._counter_retry_period
                return None
            self._counters_ready = True
            self._counter_errors = 0
            if self._counter_degraded:
                self._counter_degraded = False
                self.get_logger().info("pulse counters answered again; odometry back on encoders")
            return None

        try:
            raw = [self._counters[LEFT].read(), self._counters[RIGHT].read()]
        except OSError as exc:
            self._counter_errors += 1
            if self._counter_errors >= self._counter_error_limit:
                # Nothing is stuck here -- unlike rtmouse, a failed transfer
                # returns to us and the robot stays drivable.  Drop to the
                # open-loop estimate and keep retrying.
                self._counters_ready = False
                self._counter_degraded = True
                self._counter_retry_at = time.monotonic() + self._counter_retry_period
                self.get_logger().error(
                    "pulse counters failed %d times (%s); integrating cmd_vel instead"
                    % (self._counter_errors, exc)
                )
            return None
        self._counter_errors = 0

        with self._state_lock:
            forward = list(self._forward)

        travelled = []
        for side in (LEFT, RIGHT):
            # The counters are 16-bit up-counters that do not know direction,
            # so the wrap is exact modulo 2**16 and the sign comes from the
            # command that produced the pulses.
            delta = (raw[side] - self._last_raw[side]) & 0xFFFF
            if not forward[side]:
                delta = -delta
            revolutions = delta / self._pulses_per_revolution
            travelled.append(revolutions * math.pi * self._wheel_diameter * self._scale[side])
        self._last_raw = raw
        return travelled


def main():
    rclpy.init(args=sys.argv)
    node = RaspicatPi5Driver()
    # One thread for the motor group, one for the counters, and the rest for
    # the lifecycle services -- an I2C stall must not delay cmd_vel.
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        # Never leave the wheels turning because the process went away.
        try:
            node.teardown()
        finally:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
