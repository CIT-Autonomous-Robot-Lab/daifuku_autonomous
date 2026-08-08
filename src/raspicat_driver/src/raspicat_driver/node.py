#!/usr/bin/env python3
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

"""The ROS-facing lifecycle node of the userspace Raspberry Pi Cat driver.

The contract is the one raspimouse (raspimouse2) offers, so robot_bringup,
the EKF and Nav2 do not care which driver is running:

  subscribe  cmd_vel        geometry_msgs/Twist
  subscribe  leds           raspimouse_msgs/Leds
  subscribe  buzzer         std_msgs/Int16      (data = Hz, 0 = silence)
  publish    odom           nav_msgs/Odometry
  publish    switches       raspimouse_msgs/Switches  (true = pressed)
  publish    odom -> base_footprint TF   (publish_tf, unlike raspimouse)
  publish    motor_power_state  std_msgs/Bool  (latched, unlike raspimouse)
  service    motor_power    std_srvs/SetBool
  lifecycle  configure -> activate, driven by robot_bringup.launch.py

motor_power_state is ours alone: raspimouse takes the SetBool and tells nobody,
so everything that wants to show whether the power is on (joy_teleop's LEDs)
had to mirror its own request and drifted the moment anyone else called the
service.  It is latched (transient local, depth 1) because it changes a few
times an hour and a late subscriber has no other way to ask.  Consumers must
treat it as optional -- with driver:=raspimouse nothing publishes it.

Everything below the node is a Backend (backend.py, pi4.py, pi5.py); this file
never mentions a register, a chip label or a kernel module.

Still not provided: the light sensors.  They hang off the board's SPI ADC
rather than a GPIO line, nothing in this workspace reads /light_sensors, and
polling them at 100 Hz is what wedges rtmouse on the real robot
(docs/usage/troubleshooting.md).

The three peripherals that are provided are optional in a way the motor path
is not: if a line cannot be claimed the node says so and carries on driving.

Three deliberate differences from raspimouse:
  * the encoder and the stepper are counted separately.  On this robot a wheel
    revolution is 1118 encoder pulses but only 570 motor steps (measured
    2026-08-04), so the single number raspimouse uses cannot serve both:
    pulses_per_revolution converts counts to metres, steps_per_revolution
    converts cmd_vel to a step frequency.  raspimouse_component.cpp hardcodes
    400.0 on the command side and cannot be corrected by a parameter at all.
  * with the pulse counters live, the published Twist is measured rather than
    commanded.  mid360_ekf.yaml takes vx and vyaw (and nothing else) from this
    message, so feeding it the command would close a loop on our own output.
  * control_mode defaults to "closed", which trims the step frequency from
    that same measurement (control.py).  raspimouse has no equivalent -- it
    writes the commanded frequency and never reads the counters back into the
    command.  The trim is for slip and load, and it makes a stall worse: these
    are steppers, so a wheel that is losing steps answers a raised frequency by
    losing more of them, and the pulse deltas take their sign from the
    direction line we last wrote, so neither the loop nor the odometry can see
    that happening.  wheel_correction_limit is what bounds how bad that gets;
    "open" is the way out, and is exactly what raspimouse does.
    The gains were measured on the robot on 2026-08-08 against a wall seen by
    the Mid-360, which is the only ruler here that is independent of the
    encoders: wheel_ki 8 oscillates, 4 amplifies the counter's own noise, and
    1-3 all settle, so 2.0 is the middle of the usable range.  wheel_kp 0.3
    destabilises and 0.1 changes nothing measurable, which is why it is 0.

An I2C stall cannot wedge the robot the way rtmouse does: the ioctl returns
ETIMEDOUT to us, the counters are read in their own callback group, and
repeated failures fall back to integrating cmd_vel until the bus answers
again.  It is not free either -- see docs/setup/raspberry-pi-5.md.
"""

import math
import sys
import threading
import time

from geometry_msgs.msg import TransformStamped
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from raspimouse_msgs.msg import Leds
from raspimouse_msgs.msg import Switches
import rclpy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.lifecycle import LifecycleNode
from rclpy.lifecycle import LifecycleState
from rclpy.lifecycle import TransitionCallbackReturn
from rclpy.qos import DurabilityPolicy
from rclpy.qos import QoSProfile
from std_msgs.msg import Bool
from std_msgs.msg import Int16
from std_srvs.srv import SetBool
from tf2_ros import TransformBroadcaster

from .backend import Hardware
from .backend import LEFT
from .backend import RIGHT
from .backend import Wiring
from .backend import create_backend
from .control import WheelTrim
from .control import trimmed_speed

# Index into the GPIO line bundle opened by the backend; the first two are the
# direction lines, so LEFT/RIGHT double as their indices.
MOTOR_ENABLE = 2

SIDE_NAMES = ("left", "right")

# How far past "max_step_frequency for the whole interval" a pulse delta may
# land before it is treated as noise rather than motion.  Only has to separate
# real travel from the ~2**16 a bad delta carries, so it is deliberately loose.
PLAUSIBLE_DELTA_MARGIN = 4.0


class RaspicatDriver(LifecycleNode):
    """cmd_vel in, odom out, with the hardware hidden behind a backend."""

    def __init__(self):
        super().__init__("raspicat_driver")

        self.declare_parameter("model", "auto")

        self.declare_parameter("use_pulse_counters", True)
        self.declare_parameter("odometry_scale_left_wheel", 1.0)
        self.declare_parameter("odometry_scale_right_wheel", 1.0)
        self.declare_parameter("wheel_diameter", 0.2)
        self.declare_parameter("wheel_tread", 0.35)
        self.declare_parameter("pulses_per_revolution", 1073.0)
        self.declare_parameter("steps_per_revolution", 447.0)
        self.declare_parameter("odom_hz", 50.0)
        self.declare_parameter("initial_motor_power", False)
        self.declare_parameter("cmd_vel_timeout", 60.0)
        self.declare_parameter("publish_tf", True)
        self.declare_parameter("odom_frame_id", "odom")
        self.declare_parameter("odom_child_frame_id", "base_footprint")
        self.declare_parameter("odom_frame_prefix", "")

        self.declare_parameter("min_step_frequency", 5.0)
        self.declare_parameter("max_step_frequency", 10000.0)

        self.declare_parameter("control_mode", "closed")
        self.declare_parameter("wheel_kp", 0.0)
        self.declare_parameter("wheel_ki", 2.0)
        self.declare_parameter("wheel_correction_limit", 2.0)

        self.declare_parameter("gpiochip_label", "")
        self.declare_parameter("gpiochip_device", "")
        self.declare_parameter("gpio_direction_left", 16)
        self.declare_parameter("gpio_direction_right", 6)
        self.declare_parameter("gpio_motor_enable", 5)
        self.declare_parameter("direction_left_forward_level", 0)
        self.declare_parameter("direction_right_forward_level", 1)

        self.declare_parameter("pwmchip_match", "")
        self.declare_parameter("pwmchip_path", "")
        self.declare_parameter("pwm_channel_left", 0)
        self.declare_parameter("pwm_channel_right", 1)

        self.declare_parameter("use_leds", True)
        self.declare_parameter("use_switches", True)
        self.declare_parameter("use_buzzer", True)
        self.declare_parameter("gpio_leds", [25, 24, 23, 18])
        self.declare_parameter("gpio_switches", [20, 26, 21])
        self.declare_parameter("gpio_buzzer", 19)
        self.declare_parameter("switch_pull_up", True)
        self.declare_parameter("switches_hz", 10.0)
        self.declare_parameter("buzzer_pwm_channel", -1)
        self.declare_parameter("buzzer_max_frequency", 5000.0)

        self.declare_parameter("i2c_bus", "/dev/i2c-1")
        self.declare_parameter("i2c_address_left", 0x10)
        self.declare_parameter("i2c_address_right", 0x11)
        self.declare_parameter("counter_error_limit", 5)
        self.declare_parameter("counter_retry_period", 1.0)

        self.declare_parameter("allow_rtmouse", False)

        self._motor_group = MutuallyExclusiveCallbackGroup()
        self._odom_group = MutuallyExclusiveCallbackGroup()
        # LEDs, buzzer and switches share a group of their own: they must not
        # sit behind a cmd_vel callback, and an odom tick must not sit behind
        # them.
        self._aux_group = MutuallyExclusiveCallbackGroup()

        self._state_lock = threading.Lock()
        # The step clocks are written from the cmd_vel callback and, in closed
        # mode, from the odometry tick as well.  Those are different callback
        # groups, so without this they can be halfway through the same sysfs
        # channel at once.
        self._motor_lock = threading.Lock()
        self._trim = (WheelTrim(), WheelTrim())
        self._backend = None
        self._hardware = Hardware()
        self._odom_pub = None
        self._switches_pub = None
        self._motor_power_pub = None
        self._tf_broadcaster = None
        self._odom_timer = None
        self._watchdog_timer = None
        self._switches_timer = None
        self._cmd_vel_sub = None
        self._leds_sub = None
        self._buzzer_sub = None
        self._motor_power_srv = None
        self._forward = [True, True]

        self._reset_state()

    # -- lifecycle ---------------------------------------------------------

    def on_configure(self, state: LifecycleState) -> TransitionCallbackReturn:
        """Pick the backend, claim the hardware and create every entity."""
        try:
            wiring = self._load_parameters()
            self._backend = create_backend(self.get_parameter("model").value)
            self._backend.preflight(wiring, self.get_logger())
            self._hardware = self._backend.open(wiring, self.get_logger())
        except Exception as exc:
            # rclpy swallows an exception here and reports ERROR with no
            # message, so catch it while we still know what happened.
            self.get_logger().error("configure failed: %s" % exc)
            self._hardware.close()
            return TransitionCallbackReturn.FAILURE

        self._reset_state()
        self._arm_directions()

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
        self._leds_sub = self.create_subscription(
            Leds, "leds", self._on_leds, 10, callback_group=self._aux_group
        )
        self._buzzer_sub = self.create_subscription(
            Int16, "buzzer", self._on_buzzer, 10, callback_group=self._aux_group
        )
        self._switches_pub = self.create_lifecycle_publisher(Switches, "switches", 10)
        # Not a lifecycle publisher: the state has to reach the LEDs from
        # on_deactivate and teardown too, and a lifecycle publisher drops
        # exactly those (leaving the last "on" as the newest thing anyone can
        # read).  Latched so a subscriber that comes up later still knows.
        self._motor_power_pub = self.create_publisher(
            Bool,
            "motor_power_state",
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
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
        self._switches_timer = self.create_timer(
            1.0 / max(self._switches_hz, 1.0),
            self._publish_switches,
            callback_group=self._aux_group,
        )
        self._switches_timer.cancel()

        self._set_motor_power(False)
        self.get_logger().info(
            "configured: model=%s (%s) gpiochip=%s pwmchip=%s counters=%s control=%s"
            % (self._backend.name, self._backend.soc, self._hardware.gpiochip_path,
               self._hardware.pwmchip_path, "on" if self._use_pulse_counters else "off",
               "closed" if self._closed_loop else "open")
        )
        self.get_logger().info(
            "peripherals: leds=%s switches=%s buzzer=%s"
            % ("on" if self._hardware.leds is not None else "off",
               "on" if self._hardware.switches is not None else "off",
               self._hardware.buzzer_kind or "off")
        )
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: LifecycleState) -> TransitionCallbackReturn:
        """Start the timers.  The pose survives from before deactivate."""
        super().on_activate(state)
        self._reset_runtime()
        self._arm_directions()
        self._active = True
        self._odom_timer.reset()
        self._watchdog_timer.reset()
        self._switches_timer.reset()
        self._set_motor_power(self._initial_motor_power)
        self.get_logger().info("activated")
        return TransitionCallbackReturn.SUCCESS

    def on_deactivate(self, state: LifecycleState) -> TransitionCallbackReturn:
        """Stop the wheels but keep the hardware claimed."""
        self._active = False
        self._stop_motors()
        self._set_motor_power(False)
        # An inactive node publishes no switch states, so leaving the LEDs lit
        # and the buzzer sounding would be reporting something we no longer
        # know.  raspimouse silences its buzzer here for the same reason.
        self._silence_buzzer()
        self._clear_leds()
        for timer in (self._odom_timer, self._watchdog_timer, self._switches_timer):
            if timer is not None:
                timer.cancel()
        super().on_deactivate(state)
        return TransitionCallbackReturn.SUCCESS

    def on_cleanup(self, state: LifecycleState) -> TransitionCallbackReturn:
        """Release everything; on_configure builds it again."""
        self.teardown()
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: LifecycleState) -> TransitionCallbackReturn:
        """Release everything."""
        self.teardown()
        return TransitionCallbackReturn.SUCCESS

    def on_error(self, state: LifecycleState) -> TransitionCallbackReturn:
        """Never leave the wheels turning because a transition failed."""
        self.get_logger().error("error processing; stopping motors")
        self.teardown()
        return TransitionCallbackReturn.SUCCESS

    def teardown(self):
        """Stop the wheels and release every handle.  Safe to call twice."""
        self._active = False
        self._stop_motors()
        self._set_motor_power(False)
        self._silence_buzzer()
        self._clear_leds()
        self._hardware.close()
        # Entities are recreated by on_configure, so drop them here rather than
        # leaving a second subscription/timer behind after a cleanup+configure.
        for timer in (self._odom_timer, self._watchdog_timer, self._switches_timer):
            if timer is not None:
                timer.cancel()
                self.destroy_timer(timer)
        self._odom_timer = None
        self._watchdog_timer = None
        self._switches_timer = None
        for subscription in (self._cmd_vel_sub, self._leds_sub, self._buzzer_sub):
            if subscription is not None:
                self.destroy_subscription(subscription)
        self._cmd_vel_sub = None
        self._leds_sub = None
        self._buzzer_sub = None
        if self._motor_power_srv is not None:
            self.destroy_service(self._motor_power_srv)
            self._motor_power_srv = None
        for publisher in (self._odom_pub, self._switches_pub):
            if publisher is not None:
                self.destroy_lifecycle_publisher(publisher)
        self._odom_pub = None
        self._switches_pub = None
        # Ordinary publisher (see on_configure), so not in the loop above.  It
        # has already carried the "off" from _set_motor_power at the top of
        # this method.
        if self._motor_power_pub is not None:
            self.destroy_publisher(self._motor_power_pub)
            self._motor_power_pub = None
        self._tf_broadcaster = None

    # -- setup -------------------------------------------------------------

    def _load_parameters(self):
        """Read every parameter into attributes and return the Wiring."""
        get = self.get_parameter
        self._use_pulse_counters = get("use_pulse_counters").value
        self._scale = (
            get("odometry_scale_left_wheel").value,
            get("odometry_scale_right_wheel").value,
        )
        self._wheel_diameter = get("wheel_diameter").value
        self._wheel_tread = get("wheel_tread").value
        # Two separate numbers, and on this robot they differ by 1.96: the
        # encoder is not on the same shaft as the stepper.  Crossing them makes
        # the robot travel at the wrong speed while odom reports the commanded
        # one, which nothing but a tape measure can see.
        self._pulses_per_revolution = get("pulses_per_revolution").value
        self._steps_per_revolution = get("steps_per_revolution").value
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
        # Cached, unlike the gains below: flipping the mode at runtime would
        # leave a correction applied on the way out to open.
        mode = get("control_mode").value
        if mode not in ("open", "closed"):
            raise ValueError("control_mode must be open or closed, not %r" % mode)
        self._closed_loop = mode == "closed"
        if self._closed_loop and not self._use_pulse_counters:
            # Not fatal, because closed is the default: refusing would mean
            # that turning the counters off for a diagnostic fails configure,
            # and a failed configure on robot_bringup takes the LiDAR and the
            # EKF down with it and then loops on restart: unless-stopped.
            # Falling back is only safe because it is said out loud -- the
            # configured: line below reports what we ended up with.
            self.get_logger().error(
                "control_mode closed needs use_pulse_counters true; running open"
            )
            self._closed_loop = False
        if get("wheel_correction_limit").value < 0.0:
            raise ValueError("wheel_correction_limit must not be negative")
        self._counter_error_limit = get("counter_error_limit").value
        self._counter_retry_period = get("counter_retry_period").value
        self._switches_hz = get("switches_hz").value
        # Every edge costs an ioctl and, in the software buzzer, a spin to the
        # microsecond -- so the ceiling is a CPU budget, not a musical one.
        self._buzzer_max = get("buzzer_max_frequency").value

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
        if self._steps_per_revolution <= 0.0:
            raise ValueError("steps_per_revolution must be positive")
        # The message has a fixed number of fields, so a short list here would
        # otherwise become an IndexError inside a callback.
        if len(get("gpio_leds").value) != 4:
            raise ValueError("gpio_leds must have 4 entries (Leds has led0..led3)")
        if len(get("gpio_switches").value) != 3:
            raise ValueError(
                "gpio_switches must have 3 entries (Switches has switch0..switch2)"
            )

        self._forward_level = (
            get("direction_left_forward_level").value,
            get("direction_right_forward_level").value,
        )

        return Wiring(
            gpiochip_label=get("gpiochip_label").value,
            gpiochip_device=get("gpiochip_device").value,
            gpio_direction=(
                get("gpio_direction_left").value,
                get("gpio_direction_right").value,
            ),
            gpio_motor_enable=get("gpio_motor_enable").value,
            pwmchip_match=get("pwmchip_match").value,
            pwmchip_path=get("pwmchip_path").value,
            pwm_channel=(
                get("pwm_channel_left").value,
                get("pwm_channel_right").value,
            ),
            i2c_bus=get("i2c_bus").value,
            i2c_address=(
                get("i2c_address_left").value,
                get("i2c_address_right").value,
            ),
            use_pulse_counters=self._use_pulse_counters,
            allow_rtmouse=get("allow_rtmouse").value,
            gpio_leds=tuple(get("gpio_leds").value),
            gpio_switches=tuple(get("gpio_switches").value),
            gpio_buzzer=get("gpio_buzzer").value,
            switch_pull_up=get("switch_pull_up").value,
            buzzer_pwm_channel=get("buzzer_pwm_channel").value,
            use_leds=get("use_leds").value,
            use_switches=get("use_switches").value,
            use_buzzer=get("use_buzzer").value,
        )

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
        self._target_omega = [0.0, 0.0]       # wheel speed asked for [rad/s]
        self._correction = [0.0, 0.0]         # what the trim adds to it
        for trim in self._trim:
            trim.reset()
        # What each step clock is already doing, so an unchanged command is not
        # rewritten.  None means "unknown", which forces the next write.
        self._last_frequency = [None, None]

    def _arm_directions(self):
        """Drive both direction lines to "forward" and record that we did.

        The lines come up low, but forward is level 0 on the left and 1 on the
        right (rtmouse clears MOTDIR_L and sets MOTDIR_R for a positive
        frequency), so the right line has to be written before _forward can
        claim both wheels are pointing forward.
        """
        self._forward = [True, True]
        if self._hardware.gpio is None:
            return
        self._hardware.gpio.set(LEFT, self._forward_level[LEFT])
        self._hardware.gpio.set(RIGHT, self._forward_level[RIGHT])

    # -- motor path (motor callback group) ---------------------------------

    def _on_cmd_vel(self, msg):
        with self._state_lock:
            self._commanded = (msg.linear.x, msg.angular.z)
            self._last_cmd_time = time.monotonic()
            self._watchdog_tripped = False
        self._apply_velocity(msg.linear.x, msg.angular.z)

    def _apply_velocity(self, linear, angular):
        radius = self._wheel_diameter / 2.0
        with self._state_lock:
            self._target_omega = [
                (linear - angular * self._wheel_tread / 2.0) / radius,
                (linear + angular * self._wheel_tread / 2.0) / radius,
            ]
        self._push_command()

    def _push_command(self):
        """Write target-plus-trim to both step clocks.

        The one place the clocks are driven from, so open and closed mode take
        the same path: in open mode the trim is simply always zero, and a
        cmd_vel arriving mid-interval carries the current trim with it rather
        than undoing it until the next odometry tick.
        """
        turns_to_steps = self._steps_per_revolution / (2.0 * math.pi)
        with self._state_lock:
            omega = [
                trimmed_speed(self._target_omega[side], self._correction[side])
                for side in (LEFT, RIGHT)
            ]
        with self._motor_lock:
            self._drive(LEFT, omega[LEFT] * turns_to_steps)
            self._drive(RIGHT, omega[RIGHT] * turns_to_steps)

    def _drive(self, side, frequency):
        """Set one step clock.  Call with the motor lock held."""
        # rtmouse resets anything below MOTOR_UNCONTROLLABLE_FREQ to zero
        # because the driver cannot hold a step that slow.
        if abs(frequency) < self._min_step:
            frequency = 0.0
        frequency = max(-self._max_step, min(self._max_step, frequency))
        # In closed mode this runs at odom_hz, and most ticks ask the clock for
        # what it is already doing.  Rewriting the period of a running stepper
        # for no change is load on the odometry thread and an extra chance to
        # land a write between two edges.
        if frequency == self._last_frequency[side]:
            return
        # A write that does not complete leaves the clock in a state we cannot
        # name, so the cache is only set once the hardware has taken the value.
        self._last_frequency[side] = None

        clock = self._hardware.clocks[side]
        if frequency == 0.0:
            # Stop the clock but leave the direction line alone: pulses already
            # counted and not yet read must keep the sign of the motion that
            # produced them.
            if clock is not None:
                try:
                    clock.stop()
                except OSError as exc:
                    self.get_logger().error("step clock stop failed: %s" % exc)
                    return
            self._last_frequency[side] = 0.0
            return
        forward = frequency > 0.0

        # Direction before clock: a stepper already running must not meet the
        # next edge with the direction line still on the old level.
        gpio = self._hardware.gpio
        if gpio is not None and forward != self._forward[side]:
            level = self._forward_level[side] if forward else 1 - self._forward_level[side]
            try:
                gpio.set(side, level)
            except OSError as exc:
                self.get_logger().error("direction line write failed: %s" % exc)
                return
        # _read_counters signs each delta with this, from the other thread.
        with self._state_lock:
            self._forward[side] = forward

        if clock is not None:
            try:
                clock.set_frequency(abs(frequency))
            except OSError as exc:
                self.get_logger().error("step clock write failed: %s" % exc)
                return
        self._last_frequency[side] = frequency

    def _stop_motors(self):
        # The target and the trim have to go with the clocks.  The closed-loop
        # tick pushes the current target every interval, so a watchdog stop
        # that only stopped the clocks would be undone 20 ms later.
        with self._state_lock:
            self._commanded = (0.0, 0.0)
            self._target_omega = [0.0, 0.0]
            self._correction = [0.0, 0.0]
        for trim in self._trim:
            trim.reset()
        with self._motor_lock:
            for side in (LEFT, RIGHT):
                clock = self._hardware.clocks[side]
                if clock is not None:
                    try:
                        clock.stop()
                    except OSError as exc:
                        self.get_logger().error("step clock stop failed: %s" % exc)
                        self._last_frequency[side] = None
                        continue
                self._last_frequency[side] = 0.0

    def _set_motor_power(self, enabled):
        if self._hardware.gpio is None:
            return
        try:
            self._hardware.gpio.set(MOTOR_ENABLE, 1 if enabled else 0)
        except OSError as exc:
            self.get_logger().error("motor enable write failed: %s" % exc)
            return
        self._motor_power = bool(enabled)
        if self._motor_power_pub is not None:
            self._motor_power_pub.publish(Bool(data=self._motor_power))
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

    # -- LEDs, buzzer and switches (aux callback group) --------------------

    def _on_leds(self, msg):
        """Light the LEDs the message asks for.  High is lit, as in rtmouse."""
        leds = self._hardware.leds
        if leds is None:
            return
        try:
            leds.set_many((msg.led0, msg.led1, msg.led2, msg.led3))
        except OSError as exc:
            self.get_logger().error(
                "LED write failed: %s" % exc, throttle_duration_sec=5.0
            )

    def _on_buzzer(self, msg):
        """Sound the buzzer at msg.data Hz; 0 (or less) is silence."""
        buzzer = self._hardware.buzzer
        if buzzer is None:
            return
        frequency = float(msg.data)
        if frequency > self._buzzer_max:
            self.get_logger().warning(
                "buzzer asked for %.0f Hz; capped at buzzer_max_frequency (%.0f Hz)"
                % (frequency, self._buzzer_max),
                throttle_duration_sec=5.0,
            )
            frequency = self._buzzer_max
        try:
            if frequency <= 0.0:
                buzzer.stop()
            else:
                buzzer.set_frequency(frequency)
        except OSError as exc:
            self.get_logger().error(
                "buzzer write failed: %s" % exc, throttle_duration_sec=5.0
            )

    def _silence_buzzer(self):
        if self._hardware.buzzer is None:
            return
        try:
            self._hardware.buzzer.stop()
        except OSError as exc:
            self.get_logger().error("buzzer stop failed: %s" % exc)

    def _clear_leds(self):
        if self._hardware.leds is None:
            return
        try:
            self._hardware.leds.set_many((False, False, False, False))
        except OSError as exc:
            self.get_logger().error("LED write failed: %s" % exc)

    def _publish_switches(self):
        """Publish the three push switches.  True is pressed, as raspimouse has it."""
        switches = self._hardware.switches
        if not self._active or self._switches_pub is None or switches is None:
            return
        try:
            levels = switches.read()
        except OSError as exc:
            self.get_logger().error(
                "switch read failed: %s" % exc, throttle_duration_sec=5.0
            )
            return
        message = Switches()
        # The switches pull their line to ground, so a pressed one reads low.
        message.switch0 = levels[0] == 0
        message.switch1 = levels[1] == 0
        message.switch2 = levels[2] == 0
        self._switches_pub.publish(message)

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
        travelled = self._read_counters(dt) if self._use_pulse_counters else None
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
            if self._closed_loop:
                self._update_trim(left, right, dt)
        else:
            if self._closed_loop:
                self._release_trim()
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

    def _update_trim(self, left, right, dt):
        """Correct the step frequency by what the encoders say the wheels did.

        Runs on the odometry tick because that is where the measurement already
        is; a second timer would only read the same counters again over the
        same I2C bus.  `left` and `right` are metres travelled over `dt`.
        """
        radius = self._wheel_diameter / 2.0
        # Read live, unlike control_mode: config_sentinel drops the launch when
        # the yaml is edited on a running robot, so `ros2 param set` is the
        # only way to tune these against a wheel that is actually turning.
        kp = self.get_parameter("wheel_kp").value
        ki = self.get_parameter("wheel_ki").value
        limit = self.get_parameter("wheel_correction_limit").value
        measured = (left / radius / dt, right / radius / dt)

        with self._state_lock:
            target = list(self._target_omega)
        correction = [
            self._trim[side].step(target[side], measured[side], dt, kp, ki, limit)
            for side in (LEFT, RIGHT)
        ]
        with self._state_lock:
            self._correction = correction
        self._push_command()

    def _release_trim(self):
        """Fall back to feed-forward while the counters are unavailable.

        _read_counters already drops odometry to integrating cmd_vel when the
        I2C bus stops answering; the loop has to let go of the same tick, or it
        would hold the last correction it happened to have when the bus died.
        """
        with self._state_lock:
            if self._correction == [0.0, 0.0]:
                return
            self._correction = [0.0, 0.0]
        for trim in self._trim:
            trim.reset()
        self._push_command()

    def _read_counters(self, dt):
        """Distance travelled by each wheel since the last call, in metres.

        Returns None when the counters are unavailable, which tells the caller
        to fall back to integrating cmd_vel for this tick.  `dt` is the length
        of the interval, used only to bound how far a wheel could have gone.
        """
        counters = self._hardware.counters
        if counters[LEFT] is None or counters[RIGHT] is None:
            return None

        if not self._counters_ready:
            if time.monotonic() < self._counter_retry_at:
                return None
            try:
                self._last_raw = [counters[LEFT].read(), counters[RIGHT].read()]
            except OSError:
                self._counter_retry_at = time.monotonic() + self._counter_retry_period
                return None
            self._counters_ready = True
            self._counter_errors = 0
            if self._counter_degraded:
                self._counter_degraded = False
                self.get_logger().info(
                    "pulse counters answered again; odometry back on encoders"
                )
            return None

        try:
            raw = [counters[LEFT].read(), counters[RIGHT].read()]
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

        # Widest delta a wheel could really produce over this interval.  A
        # delta past it is not travel: the counters cannot report a decrease,
        # so anything that looks like one -- a read torn across a carry, or a
        # wheel turned by hand against the direction the sign is borrowed from
        # -- comes back as nearly 2**16, worth about 100 m.  The floor keeps a
        # short interval from making the bound absurdly tight.
        # max_step is in motor steps, the delta is in encoder counts, so the
        # ratio between the two revolutions has to come along.
        counts_per_step = self._pulses_per_revolution / self._steps_per_revolution
        limit = max(
            self._max_step * counts_per_step * dt * PLAUSIBLE_DELTA_MARGIN,
            self._pulses_per_revolution,
        )

        travelled = []
        for side in (LEFT, RIGHT):
            # The counters are 16-bit up-counters that do not know direction,
            # so the wrap is exact modulo 2**16 and the sign comes from the
            # command that produced the pulses.
            delta = (raw[side] - self._last_raw[side]) & 0xFFFF
            if delta > limit:
                # Resync on the value we just read rather than integrating it;
                # the interval's real travel is lost, which is worth far less
                # than the pose.
                self.get_logger().warning(
                    "%s pulse delta %d exceeds the %d possible in %.3f s; "
                    "dropping the interval" % (SIDE_NAMES[side], delta, int(limit), dt),
                    throttle_duration_sec=5.0,
                )
                delta = 0
            if not forward[side]:
                delta = -delta
            revolutions = delta / self._pulses_per_revolution
            travelled.append(revolutions * math.pi * self._wheel_diameter * self._scale[side])
        self._last_raw = raw
        return travelled


def main():
    """Spin the node until it is shut down, then stop the wheels."""
    rclpy.init(args=sys.argv)
    node = RaspicatDriver()
    # One thread for the motor group, one for the counters, one for the LEDs,
    # buzzer and switches, and the rest for the lifecycle services -- an I2C
    # stall must not delay cmd_vel.
    executor = MultiThreadedExecutor(num_threads=5)
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
