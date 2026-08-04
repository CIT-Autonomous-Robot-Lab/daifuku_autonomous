#!/usr/bin/env python3

"""The ROS-facing lifecycle node of the userspace Raspberry Pi Cat driver.

The contract is the one raspimouse (raspimouse2) offers, so robot_bringup,
the EKF and Nav2 do not care which driver is running:

  subscribe  cmd_vel        geometry_msgs/Twist
  publish    odom           nav_msgs/Odometry
  publish    odom -> base_footprint TF   (publish_tf, unlike raspimouse)
  service    motor_power    std_srvs/SetBool
  lifecycle  configure -> activate, driven by robot_bringup.launch.py

Everything below the node is a Backend (backend.py, pi4.py, pi5.py); this file
never mentions a register, a chip label or a kernel module.

Deliberately not provided: LEDs, buzzer, switches and light sensors.  Nothing
in this workspace subscribes to /leds or /buzzer or reads /switches or
/light_sensors -- only raspimouse's own parameters mention them.

Two deliberate differences from raspimouse:
  * the encoder and the stepper are counted separately.  On this robot a wheel
    revolution is 1118 encoder pulses but only 570 motor steps (measured
    2026-08-04), so the single number raspimouse uses cannot serve both:
    pulses_per_revolution converts counts to metres, steps_per_revolution
    converts cmd_vel to a step frequency.  raspimouse_component.cpp hardcodes
    400.0 on the command side and cannot be corrected by a parameter at all.
  * with the pulse counters live, the published Twist is measured rather than
    commanded.  mid360_ekf.yaml takes vx and vyaw (and nothing else) from this
    message, so feeding it the command would close a loop on our own output.

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
import rclpy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.lifecycle import LifecycleNode
from rclpy.lifecycle import LifecycleState
from rclpy.lifecycle import TransitionCallbackReturn
from std_srvs.srv import SetBool
from tf2_ros import TransformBroadcaster

from .backend import Hardware
from .backend import LEFT
from .backend import RIGHT
from .backend import Wiring
from .backend import create_backend

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
        self.declare_parameter("pulses_per_revolution", 1118.0)
        self.declare_parameter("steps_per_revolution", 570.0)
        self.declare_parameter("odom_hz", 50.0)
        self.declare_parameter("initial_motor_power", False)
        self.declare_parameter("cmd_vel_timeout", 60.0)
        self.declare_parameter("publish_tf", True)
        self.declare_parameter("odom_frame_id", "odom")
        self.declare_parameter("odom_child_frame_id", "base_footprint")
        self.declare_parameter("odom_frame_prefix", "")

        self.declare_parameter("min_step_frequency", 5.0)
        self.declare_parameter("max_step_frequency", 10000.0)

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

        self.declare_parameter("i2c_bus", "/dev/i2c-1")
        self.declare_parameter("i2c_address_left", 0x10)
        self.declare_parameter("i2c_address_right", 0x11)
        self.declare_parameter("counter_error_limit", 5)
        self.declare_parameter("counter_retry_period", 1.0)

        self.declare_parameter("allow_rtmouse", False)

        self._motor_group = MutuallyExclusiveCallbackGroup()
        self._odom_group = MutuallyExclusiveCallbackGroup()

        self._state_lock = threading.Lock()
        self._backend = None
        self._hardware = Hardware()
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
            "configured: model=%s (%s) gpiochip=%s pwmchip=%s counters=%s"
            % (self._backend.name, self._backend.soc, self._hardware.gpiochip_path,
               self._hardware.pwmchip_path, "on" if self._use_pulse_counters else "off")
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
        self._set_motor_power(self._initial_motor_power)
        self.get_logger().info("activated")
        return TransitionCallbackReturn.SUCCESS

    def on_deactivate(self, state: LifecycleState) -> TransitionCallbackReturn:
        """Stop the wheels but keep the hardware claimed."""
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
        self._hardware.close()
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
        if self._steps_per_revolution <= 0.0:
            raise ValueError("steps_per_revolution must be positive")

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
        omega_left = (linear - angular * self._wheel_tread / 2.0) / radius
        omega_right = (linear + angular * self._wheel_tread / 2.0) / radius
        turns_to_steps = self._steps_per_revolution / (2.0 * math.pi)
        self._drive(LEFT, omega_left * turns_to_steps)
        self._drive(RIGHT, omega_right * turns_to_steps)

    def _drive(self, side, frequency):
        # rtmouse resets anything below MOTOR_UNCONTROLLABLE_FREQ to zero
        # because the driver cannot hold a step that slow.
        if abs(frequency) < self._min_step:
            frequency = 0.0
        frequency = max(-self._max_step, min(self._max_step, frequency))

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

        if clock is None:
            return
        try:
            clock.set_frequency(abs(frequency))
        except OSError as exc:
            self.get_logger().error("step clock write failed: %s" % exc)

    def _stop_motors(self):
        with self._state_lock:
            self._commanded = (0.0, 0.0)
        for side in (LEFT, RIGHT):
            clock = self._hardware.clocks[side]
            if clock is not None:
                try:
                    clock.stop()
                except OSError as exc:
                    self.get_logger().error("step clock stop failed: %s" % exc)

    def _set_motor_power(self, enabled):
        if self._hardware.gpio is None:
            return
        try:
            self._hardware.gpio.set(MOTOR_ENABLE, 1 if enabled else 0)
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
