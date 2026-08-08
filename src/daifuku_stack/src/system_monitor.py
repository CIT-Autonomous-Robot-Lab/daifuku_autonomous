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

"""Publish CPU load on /diagnostics so the rqt panel can watch the Pi.

Everything is read from /proc, so this node needs nothing the workspace did not
already have (python3-psutil is on neither image; diagnostic_updater is on
both).  Two scopes are reported because Nav2 starves here in two ways:

  * Host-wide utilisation and load average answer "is the Pi saturated at all".
    /proc/stat is not namespaced, so these are the host's numbers even though
    this node runs inside the container.
  * Per-process CPU time attributes that saturation to a node.  This one *is*
    namespaced: docker/raspberrypi/compose.yaml does not set `pid: host`, so
    only processes in this container are visible.  The body driver runs in the
    daifuku-raspicat container and rtmouse runs on the host -- neither shows up
    until `pid: host` is added.

The Float32 topics exist only so rqt_plot can graph the same values; the
diagnostic KeyValues it cannot plot are strings.
"""

import os

from diagnostic_msgs.msg import DiagnosticStatus
import diagnostic_updater
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32


def _read_cpu_times():
    """Return {"cpu": (busy, total), "cpu0": ..., ...} in clock ticks.

    Fields after the label are user, nice, system, idle, iowait, irq, softirq,
    steal, guest, guest_nice.  iowait counts as idle: the Pi 4 spends real time
    there on SD reads, but the core is available for another thread.
    """
    out = {}
    with open("/proc/stat") as handle:
        for line in handle:
            if not line.startswith("cpu"):
                break
            fields = line.split()
            values = [int(v) for v in fields[1:]]
            total = sum(values)
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            out[fields[0]] = (total - idle, total)
    return out


def _read_process_times(watch):
    """Return {name: ticks} of utime+stime for processes matching `watch`.

    A name matches when it appears anywhere in the command line, which is how
    ROS 2 executables have to be found: the process name is truncated to 15
    characters and a composed node shows up as component_container.
    """
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as handle:
                cmdline = handle.read().decode("utf-8", "replace").replace("\0", " ")
            name = next((w for w in watch if w in cmdline), None)
            if name is None:
                continue
            with open("/proc/%s/stat" % pid) as handle:
                stat = handle.read()
            # The comm field is parenthesised and may itself contain spaces and
            # parentheses, so split after the last ')' rather than on spaces.
            fields = stat.rpartition(")")[2].split()
            # After comm, field 0 is state; utime and stime are 11 and 12.
            out[name] = out.get(name, 0) + int(fields[11]) + int(fields[12])
        except (OSError, IndexError, ValueError, StopIteration):
            continue
    return out


def _read_first_int(path):
    try:
        with open(path) as handle:
            return int(handle.read().split()[0])
    except (OSError, IndexError, ValueError):
        return None


class SystemMonitor(Node):
    def __init__(self):
        super().__init__("system_monitor")
        period = self.declare_parameter("update_period", 1.0).value
        self._warn = self.declare_parameter("warn_percent", 85.0).value
        self._error = self.declare_parameter("error_percent", 95.0).value
        self._watch = self.declare_parameter(
            "watch",
            [
                "vi_planner",
                "controller_server",
                "planner_server",
                "bt_navigator",
                "emcl2",
                "livox_ros_driver2",
                "component_container",
            ],
        ).value

        self._clock_ticks = os.sysconf("SC_CLK_TCK")
        self._period = period
        self._prev_cpu = _read_cpu_times()
        self._prev_proc = _read_process_times(self._watch)

        self._cpu_pub = self.create_publisher(Float32, "~/cpu_percent", 10)
        self._temp_pub = self.create_publisher(Float32, "~/temperature", 10)

        self._updater = diagnostic_updater.Updater(self, period=period)
        self._updater.setHardwareID(os.uname().nodename)
        self._updater.add("CPU", self._cpu_task)
        self._updater.add("Nodes", self._process_task)

    def _cpu_task(self, stat):
        current = _read_cpu_times()
        percents = {}
        for name, (busy, total) in current.items():
            prev = self._prev_cpu.get(name)
            if prev is None or total <= prev[1]:
                continue
            percents[name] = 100.0 * (busy - prev[0]) / (total - prev[1])
        self._prev_cpu = current

        overall = percents.pop("cpu", None)
        if overall is None:
            stat.summary(DiagnosticStatus.OK, "measuring")
            return stat

        if overall >= self._error:
            level = DiagnosticStatus.ERROR
        elif overall >= self._warn:
            level = DiagnosticStatus.WARN
        else:
            level = DiagnosticStatus.OK
        stat.summary(level, "%.1f%%" % overall)
        stat.add("total", "%.1f%%" % overall)
        for name in sorted(percents):
            stat.add(name, "%.1f%%" % percents[name])

        try:
            with open("/proc/loadavg") as handle:
                stat.add("loadavg", " ".join(handle.read().split()[:3]))
        except (OSError, ValueError):
            pass

        milli = _read_first_int("/sys/class/thermal/thermal_zone0/temp")
        if milli is not None:
            celsius = milli / 1000.0
            stat.add("temperature", "%.1f degC" % celsius)
            self._temp_pub.publish(Float32(data=celsius))

        self._cpu_pub.publish(Float32(data=float(overall)))
        return stat

    def _process_task(self, stat):
        current = _read_process_times(self._watch)
        found = []
        for name in sorted(current):
            prev = self._prev_proc.get(name)
            if prev is None:
                continue
            seconds = (current[name] - prev) / float(self._clock_ticks)
            # Above 100% means several threads ran; the Pi has four cores.
            stat.add(name, "%.1f%%" % (100.0 * seconds / self._period))
            found.append(name)
        self._prev_proc = current

        if not found:
            # Not an error: the container's PID namespace legitimately holds
            # none of these when only the driver stack is up.
            stat.summary(DiagnosticStatus.OK, "no watched process in this namespace")
        else:
            stat.summary(DiagnosticStatus.OK, "%d process(es)" % len(found))
        return stat


def main(args=None):
    rclpy.init(args=args)
    node = SystemMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
