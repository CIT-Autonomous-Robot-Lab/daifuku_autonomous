"""A hand-built rqt panel for driving and watching the Raspberry Pi Cat.

Four things live here: the CPU view fed by daifuku_stack's system_monitor, a
NavigateToPose client, the body driver's lifecycle and motor_power services,
and a teleop pad.

Two constraints shape the code more than anything else.

*Threading.*  rqt spins the node on its own thread, so every ROS callback
arrives off the Qt thread and must not touch a widget directly -- doing so
crashes intermittently rather than immediately.  Callbacks therefore only emit
a Qt signal, and the slots do the widget work.  For the same reason every
service and action call is asynchronous: a blocking spin inside a slot would
freeze the GUI.

*Stopping.*  raspicat_driver.yaml sets cmd_vel_timeout to 60 s, so a robot
that is moving when the last cmd_vel arrives keeps moving for a minute.  The
panel must therefore publish a zero Twist itself on every exit path -- button
release, key release, hide, plugin shutdown -- and it also stops on its own
after HOLD_LIMIT even while a key is held.  See README.md.
"""

import math

from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import PoseStamped
from geometry_msgs.msg import Twist
from lifecycle_msgs.msg import State
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState
from lifecycle_msgs.srv import GetState
from nav2_msgs.action import NavigateToPose
from python_qt_binding.QtCore import QEvent
from python_qt_binding.QtCore import QTimer
from python_qt_binding.QtCore import Qt
from python_qt_binding.QtCore import Signal
from python_qt_binding.QtWidgets import QApplication
from python_qt_binding.QtWidgets import QCheckBox
from python_qt_binding.QtWidgets import QComboBox
from python_qt_binding.QtWidgets import QDoubleSpinBox
from python_qt_binding.QtWidgets import QGridLayout
from python_qt_binding.QtWidgets import QGroupBox
from python_qt_binding.QtWidgets import QHBoxLayout
from python_qt_binding.QtWidgets import QHeaderView
from python_qt_binding.QtWidgets import QLabel
from python_qt_binding.QtWidgets import QProgressBar
from python_qt_binding.QtWidgets import QPushButton
from python_qt_binding.QtWidgets import QTableWidget
from python_qt_binding.QtWidgets import QTableWidgetItem
from python_qt_binding.QtWidgets import QVBoxLayout
from python_qt_binding.QtWidgets import QWidget
from rclpy.action import ActionClient
from rqt_gui_py.plugin import Plugin
from std_srvs.srv import SetBool

# The panel zeroes cmd_vel after this many seconds of continuous drive even if
# the operator is still holding the key.  A held arrow key plus a lost focus
# event is otherwise indistinguishable from a wedged panel.
HOLD_LIMIT = 5.0

# cmd_vel publish rate.  vi_planner runs its control loop at 10 Hz
# (config/nav2/vi_planner.yaml), so this matches what the driver already sees.
TELEOP_HZ = 10.0

# Human-issued velocity goes to the high-priority input of twist_mux, not to
# /cmd_vel (which is the autonomous stack's own output).  robot_bringup launches
# the mux by default; with twist_mux:=false nothing subscribes here and the pad
# silently does nothing -- see config/README.md.
TELEOP_CMD_VEL_TOPIC = "/cmd_vel_teleop"

DRIVER_NODES = ["raspicat_driver", "raspimouse"]

LIFECYCLE_LABELS = {
    State.PRIMARY_STATE_UNKNOWN: "unknown",
    State.PRIMARY_STATE_UNCONFIGURED: "unconfigured",
    State.PRIMARY_STATE_INACTIVE: "inactive",
    State.PRIMARY_STATE_ACTIVE: "active",
    State.PRIMARY_STATE_FINALIZED: "finalized",
}


class _SquareButton(QPushButton):
    """A button that keeps its height equal to its width.

    QGridLayout ignores heightForWidth, so the aspect ratio has to be enforced
    from resizeEvent instead.  Column widths do not depend on row heights, so
    the extra layout pass this triggers settles immediately; the guard keeps it
    from looping.
    """

    def resizeEvent(self, event):
        super(_SquareButton, self).resizeEvent(event)
        if self.height() != self.width():
            self.setFixedHeight(self.width())


def _yaw_to_quaternion(yaw_degrees):
    half = math.radians(yaw_degrees) * 0.5
    return math.sin(half), math.cos(half)


def _result_or_none(future):
    """Read a finished future without letting it raise into rclpy's executor.

    An exception escaping a done-callback takes the spin thread down with it,
    which kills every other subscription in the panel.
    """
    try:
        return future.result()
    except Exception:  # noqa: BLE001 - reported through the UI instead
        return None


def _success_text(future):
    response = _result_or_none(future)
    return "成功" if response is not None and response.success else "失敗"


class ControlPanelWidget(QWidget):

    # Emitted from ROS callbacks; the slots below own every widget touch.
    diagnostics_received = Signal(object)
    goal_state_changed = Signal(str)
    lifecycle_state_changed = Signal(int)
    service_answered = Signal(str)

    def __init__(self, node):
        super(ControlPanelWidget, self).__init__()
        self.setObjectName("RaspicatControlPanel")
        self.setWindowTitle("Raspicat Control Panel")

        self._node = node
        self._goal_handle = None
        self._goal_active = False
        self._twist = Twist()
        self._held_seconds = 0.0
        self._zero_bursts = 0
        self._change_state_client = None
        self._get_state_client = None
        self._lifecycle_clients = {}

        self._build_ui()

        self._cmd_vel_pub = node.create_publisher(Twist, TELEOP_CMD_VEL_TOPIC, 10)
        self._nav_client = ActionClient(node, NavigateToPose, "navigate_to_pose")
        self._motor_client = node.create_client(SetBool, "/motor_power")
        self._diag_sub = node.create_subscription(
            DiagnosticArray, "/diagnostics", self._on_diagnostics, 10
        )
        self._rebuild_lifecycle_clients()

        self.diagnostics_received.connect(self._show_diagnostics)
        self.goal_state_changed.connect(self._show_goal_state)
        self.lifecycle_state_changed.connect(self._show_lifecycle_state)
        self.service_answered.connect(self._service_label.setText)

        self._teleop_timer = QTimer(self)
        self._teleop_timer.timeout.connect(self._publish_teleop)
        self._teleop_timer.start(int(1000.0 / TELEOP_HZ))

        self._state_timer = QTimer(self)
        self._state_timer.timeout.connect(self._poll_lifecycle_state)
        self._state_timer.start(2000)

        self.setFocusPolicy(Qt.StrongFocus)
        QApplication.instance().installEventFilter(self)

    # -- construction ------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_status_group())
        layout.addWidget(self._build_navigation_group())
        layout.addWidget(self._build_driver_group())
        layout.addWidget(self._build_teleop_group())
        layout.addStretch(1)

    def _build_status_group(self):
        group = QGroupBox("稼働状況")
        layout = QVBoxLayout(group)

        self._cpu_bar = QProgressBar()
        self._cpu_bar.setRange(0, 100)
        self._cpu_bar.setFormat("CPU %p%")
        layout.addWidget(self._cpu_bar)

        self._cpu_detail = QLabel("system_monitor を待っています")
        self._cpu_detail.setWordWrap(True)
        layout.addWidget(self._cpu_detail)

        self._node_table = QTableWidget(0, 2)
        self._node_table.setHorizontalHeaderLabels(["プロセス", "CPU"])
        rows = self._node_table.verticalHeader()
        rows.setVisible(False)
        # Two thirds of the style's row height, and twice the box: three times
        # as many processes fit.  minimumSectionSize is style-derived and would
        # otherwise clamp the shorter rows straight back, and Fixed keeps
        # _show_node_table's items from growing them again.
        rows.setMinimumSectionSize(rows.defaultSectionSize() * 2 // 3)
        rows.setDefaultSectionSize(rows.defaultSectionSize() * 2 // 3)
        rows.setSectionResizeMode(QHeaderView.Fixed)
        self._node_table.horizontalHeader().setStretchLastSection(True)
        self._node_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._node_table.setEditTriggers(QTableWidget.NoEditTriggers)
        # Fixed, not a maximum: QAbstractScrollArea hands out a 192 px size hint
        # and the layout's trailing stretch swallows everything above it, so a
        # raised maximum alone would leave the box at 192 whatever the panel
        # height.  The old maximum of 160 was reached for the same reason.
        self._node_table.setFixedHeight(320)
        layout.addWidget(self._node_table)
        return group

    def _build_navigation_group(self):
        group = QGroupBox("ゴール")
        layout = QGridLayout(group)

        self._goal_x = QDoubleSpinBox()
        self._goal_x.setRange(-1000.0, 1000.0)
        self._goal_x.setDecimals(2)
        self._goal_x.setSuffix(" m")
        self._goal_y = QDoubleSpinBox()
        self._goal_y.setRange(-1000.0, 1000.0)
        self._goal_y.setDecimals(2)
        self._goal_y.setSuffix(" m")
        self._goal_yaw = QDoubleSpinBox()
        self._goal_yaw.setRange(-180.0, 180.0)
        self._goal_yaw.setDecimals(1)
        self._goal_yaw.setSuffix(" deg")

        layout.addWidget(QLabel("x"), 0, 0)
        layout.addWidget(self._goal_x, 0, 1)
        layout.addWidget(QLabel("y"), 0, 2)
        layout.addWidget(self._goal_y, 0, 3)
        layout.addWidget(QLabel("yaw"), 0, 4)
        layout.addWidget(self._goal_yaw, 0, 5)

        self._send_goal_button = QPushButton("送信")
        self._send_goal_button.clicked.connect(self._send_goal)
        self._cancel_goal_button = QPushButton("中断")
        self._cancel_goal_button.clicked.connect(self._cancel_goal)
        self._cancel_goal_button.setEnabled(False)
        layout.addWidget(self._send_goal_button, 1, 0, 1, 3)
        layout.addWidget(self._cancel_goal_button, 1, 3, 1, 3)

        self._goal_label = QLabel("idle")
        layout.addWidget(self._goal_label, 2, 0, 1, 6)
        return group

    def _build_driver_group(self):
        group = QGroupBox("ドライバ")
        layout = QGridLayout(group)

        self._driver_combo = QComboBox()
        self._driver_combo.setEditable(True)
        self._driver_combo.addItems(DRIVER_NODES)
        self._driver_combo.currentTextChanged.connect(
            lambda _text: self._rebuild_lifecycle_clients()
        )
        layout.addWidget(QLabel("ノード名"), 0, 0)
        layout.addWidget(self._driver_combo, 0, 1, 1, 3)

        self._lifecycle_label = QLabel("状態: 未取得")
        layout.addWidget(self._lifecycle_label, 1, 0, 1, 4)

        transitions = [
            ("configure", Transition.TRANSITION_CONFIGURE),
            ("activate", Transition.TRANSITION_ACTIVATE),
            ("deactivate", Transition.TRANSITION_DEACTIVATE),
        ]
        for column, (label, transition_id) in enumerate(transitions):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, t=transition_id: self._change_state(t)
            )
            layout.addWidget(button, 2, column)

        motor_on = QPushButton("モータ ON")
        motor_on.clicked.connect(lambda: self._set_motor_power(True))
        motor_off = QPushButton("モータ OFF")
        motor_off.clicked.connect(lambda: self._set_motor_power(False))
        layout.addWidget(motor_on, 3, 0, 1, 2)
        layout.addWidget(motor_off, 3, 2, 1, 2)

        self._service_label = QLabel("")
        self._service_label.setWordWrap(True)
        layout.addWidget(self._service_label, 4, 0, 1, 4)
        return group

    def _build_teleop_group(self):
        self._teleop_group = QGroupBox("teleop")
        layout = QGridLayout(self._teleop_group)

        self._linear_speed = QDoubleSpinBox()
        self._linear_speed.setRange(0.0, 1.0)
        self._linear_speed.setSingleStep(0.05)
        self._linear_speed.setValue(0.15)
        self._linear_speed.setSuffix(" m/s")
        self._angular_speed = QDoubleSpinBox()
        self._angular_speed.setRange(0.0, 3.0)
        self._angular_speed.setSingleStep(0.1)
        self._angular_speed.setValue(0.6)
        self._angular_speed.setSuffix(" rad/s")
        layout.addWidget(QLabel("並進"), 0, 0)
        layout.addWidget(self._linear_speed, 0, 1)
        layout.addWidget(QLabel("旋回"), 0, 2)
        layout.addWidget(self._angular_speed, 0, 3)

        # The pad gets a grid of its own.  Sharing the group's columns would tie
        # the arrows to the label and spinbox widths above, so the left and right
        # ones would come out narrower -- and squares of a different size.
        pad = QWidget()
        pad_layout = QGridLayout(pad)
        pad_layout.setContentsMargins(0, 0, 0, 0)
        for column in range(3):
            pad_layout.setColumnStretch(column, 1)

        # Press and hold.  A click that only fired once would leave the robot
        # driving until the driver's 60 s cmd_vel_timeout expired.
        directions = [
            ("▲", 0, 1, 1.0, 0.0),
            ("◀", 1, 0, 0.0, 1.0),
            ("■", 1, 1, 0.0, 0.0),
            ("▶", 1, 2, 0.0, -1.0),
            ("▼", 2, 1, -1.0, 0.0),
        ]
        for label, row, column, linear, angular in directions:
            button = _SquareButton(label)
            button.setAutoRepeat(False)
            if linear == 0.0 and angular == 0.0:
                button.clicked.connect(self._stop_teleop)
            else:
                button.pressed.connect(
                    lambda ln=linear, an=angular: self._start_teleop(ln, an)
                )
                button.released.connect(self._stop_teleop)
            pad_layout.addWidget(button, row, column)

        # The pad gets half the group's width and sits in the middle of it;
        # squares a third of the panel wide were too big to aim at.
        pad_row = QWidget()
        pad_row_layout = QHBoxLayout(pad_row)
        pad_row_layout.setContentsMargins(0, 0, 0, 0)
        pad_row_layout.addStretch(1)
        pad_row_layout.addWidget(pad, 2)
        pad_row_layout.addStretch(1)
        layout.addWidget(pad_row, 1, 0, 1, 4)

        self._keyboard_checkbox = QCheckBox("矢印キーで操作 (このパネルに入力があるとき)")
        self._keyboard_checkbox.toggled.connect(self._on_keyboard_toggled)
        layout.addWidget(self._keyboard_checkbox, 2, 0, 1, 4)

        self._teleop_label = QLabel("停止中")
        layout.addWidget(self._teleop_label, 3, 0, 1, 4)
        return self._teleop_group

    # -- diagnostics -------------------------------------------------------

    def _on_diagnostics(self, message):
        self.diagnostics_received.emit(message)

    def _show_diagnostics(self, message):
        for status in message.status:
            values = dict((kv.key, kv.value) for kv in status.values)
            if status.name.endswith("CPU"):
                self._show_cpu(status, values)
            elif status.name.endswith("Nodes"):
                self._show_node_table(values)

    def _show_cpu(self, status, values):
        total = values.get("total", "")
        try:
            self._cpu_bar.setValue(int(float(total.rstrip("%"))))
        except ValueError:
            pass
        extras = [status.message]
        for key in ("loadavg", "temperature"):
            if key in values:
                extras.append("%s %s" % (key, values[key]))
        cores = sorted(k for k in values if k.startswith("cpu"))
        if cores:
            extras.append(" ".join("%s %s" % (k, values[k]) for k in cores))
        self._cpu_detail.setText(" / ".join(extras))

    def _show_node_table(self, values):
        self._node_table.setRowCount(len(values))
        for row, key in enumerate(sorted(values)):
            self._node_table.setItem(row, 0, QTableWidgetItem(key))
            self._node_table.setItem(row, 1, QTableWidgetItem(values[key]))

    # -- navigation --------------------------------------------------------

    def _send_goal(self):
        if not self._nav_client.server_is_ready():
            self.goal_state_changed.emit("navigate_to_pose のサーバがいません")
            return

        goal = NavigateToPose.Goal()
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = self._node.get_clock().now().to_msg()
        pose.pose.position.x = self._goal_x.value()
        pose.pose.position.y = self._goal_y.value()
        sin_half, cos_half = _yaw_to_quaternion(self._goal_yaw.value())
        pose.pose.orientation.z = sin_half
        pose.pose.orientation.w = cos_half
        goal.pose = pose

        self._set_goal_active(True)
        self.goal_state_changed.emit("送信しました")
        future = self._nav_client.send_goal_async(goal)
        future.add_done_callback(self._on_goal_response)

    def _on_goal_response(self, future):
        try:
            goal_handle = future.result()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self._goal_handle = None
            self.goal_state_changed.emit("送信に失敗: %s" % exc)
            return
        if not goal_handle.accepted:
            self._goal_handle = None
            self.goal_state_changed.emit("拒否されました")
            return
        self._goal_handle = goal_handle
        self.goal_state_changed.emit("走行中")
        goal_handle.get_result_async().add_done_callback(self._on_goal_result)

    def _on_goal_result(self, future):
        self._goal_handle = None
        try:
            status = future.result().status
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.goal_state_changed.emit("結果の取得に失敗: %s" % exc)
            return
        # GoalStatus: 4 SUCCEEDED, 5 CANCELED, 6 ABORTED.
        self.goal_state_changed.emit(
            {4: "完了", 5: "中断しました", 6: "失敗 (ABORTED)"}.get(
                status, "終了 (status=%d)" % status
            )
        )

    def _cancel_goal(self):
        if self._goal_handle is None:
            self._set_goal_active(False)
            return
        self.goal_state_changed.emit("中断を要求しました")
        self._goal_handle.cancel_goal_async()

    def _show_goal_state(self, text):
        self._goal_label.setText(text)
        finished = text not in ("送信しました", "走行中", "中断を要求しました")
        if finished:
            self._set_goal_active(False)

    def _set_goal_active(self, active):
        """Gate teleop on the goal state.

        twist_mux would let the operator win here -- but only while a key is
        held plus its 0.5 s timeout, after which the autonomous stack resumes
        mid-goal.  Fighting the planner in half-second bursts is worse than not
        offering it, so cancel is the way out and that button stays enabled.
        """
        self._goal_active = active
        self._send_goal_button.setEnabled(not active)
        self._cancel_goal_button.setEnabled(active)
        self._teleop_group.setEnabled(not active)
        if active:
            self._stop_teleop()

    # -- driver ------------------------------------------------------------

    def _rebuild_lifecycle_clients(self):
        """Point the lifecycle clients at the driver named in the combo box.

        Clients are cached and never destroyed.  Destroying one from the Qt
        thread while the executor may be servicing its pending call_async is
        the same intermittent crash the module docstring warns about, and the
        combo box only ever holds a couple of names -- leaking two idle
        clients is cheaper than the race.
        """
        name = self._driver_combo.currentText().strip("/ ")
        if not name:
            return
        if name not in self._lifecycle_clients:
            self._lifecycle_clients[name] = (
                self._node.create_client(ChangeState, "/%s/change_state" % name),
                self._node.create_client(GetState, "/%s/get_state" % name),
            )
        self._change_state_client, self._get_state_client = self._lifecycle_clients[name]

    def _poll_lifecycle_state(self):
        client = self._get_state_client
        if client is None or not client.service_is_ready():
            self._lifecycle_label.setText("状態: サービスがいません")
            return
        future = client.call_async(GetState.Request())
        future.add_done_callback(self._on_lifecycle_state)

    def _on_lifecycle_state(self, future):
        response = _result_or_none(future)
        self.lifecycle_state_changed.emit(
            State.PRIMARY_STATE_UNKNOWN
            if response is None
            else response.current_state.id
        )

    def _show_lifecycle_state(self, state_id):
        self._lifecycle_label.setText(
            "状態: %s" % LIFECYCLE_LABELS.get(state_id, "unknown")
        )

    def _change_state(self, transition_id):
        client = self._change_state_client
        if client is None or not client.service_is_ready():
            self.service_answered.emit("change_state のサービスがいません")
            return
        request = ChangeState.Request()
        request.transition.id = transition_id
        self.service_answered.emit("遷移を要求しました")
        client.call_async(request).add_done_callback(
            lambda f: self.service_answered.emit("遷移 " + _success_text(f))
        )

    def _set_motor_power(self, enabled):
        # The driver creates motor_power in on_configure with a relative name,
        # so it lands at /motor_power -- not under the node name.
        if not self._motor_client.service_is_ready():
            self.service_answered.emit(
                "motor_power がいません (ドライバが configure 済みか確認)"
            )
            return
        self.service_answered.emit("モータ %s を要求しました" % ("ON" if enabled else "OFF"))
        self._motor_client.call_async(SetBool.Request(data=enabled)).add_done_callback(
            lambda f: self.service_answered.emit("モータ " + _success_text(f))
        )

    # -- teleop ------------------------------------------------------------

    def _is_moving(self):
        return self._twist.linear.x != 0.0 or self._twist.angular.z != 0.0

    def _start_teleop(self, linear_sign, angular_sign):
        if self._goal_active:
            return
        self._twist.linear.x = linear_sign * self._linear_speed.value()
        self._twist.angular.z = angular_sign * self._angular_speed.value()
        self._held_seconds = 0.0
        self._zero_bursts = 0
        self._teleop_label.setText(
            "vx %.2f m/s  wz %.2f rad/s" % (self._twist.linear.x, self._twist.angular.z)
        )

    def _stop_teleop(self):
        if not self._is_moving() and self._zero_bursts == 0:
            return
        self._twist = Twist()
        self._held_seconds = 0.0
        # Repeat the zero a few times rather than once: a single dropped datagram
        # over WiFi would otherwise leave the robot driving for cmd_vel_timeout.
        self._zero_bursts = 3
        self._cmd_vel_pub.publish(self._twist)
        self._teleop_label.setText("停止中")

    def _publish_teleop(self):
        """Publish only while driving, plus the trailing zeros.

        Going quiet afterwards is what hands the robot back.  twist_mux keeps
        the teleop input winning for as long as it receives messages plus its
        0.5 s timeout, so a panel that streamed zeros the whole time would pin
        the mux to teleop and block the autonomous stack for as long as it
        stayed open -- while looking idle.
        """
        if self._is_moving():
            self._held_seconds += 1.0 / TELEOP_HZ
            if self._held_seconds >= HOLD_LIMIT:
                self._stop_teleop()
                self._teleop_label.setText("連続 %.0f 秒で自動停止しました" % HOLD_LIMIT)
                return
            self._cmd_vel_pub.publish(self._twist)
        elif self._zero_bursts > 0:
            self._zero_bursts -= 1
            self._cmd_vel_pub.publish(self._twist)

    def _on_keyboard_toggled(self, enabled):
        if enabled:
            self.setFocus(Qt.OtherFocusReason)
        else:
            self._stop_teleop()

    def _keyboard_target(self):
        """Return True when arrow keys should drive the robot right now.

        The filter is installed on the application, because WindowDeactivate
        goes to the window and key events go to whichever child has focus --
        neither reaches a filter installed on this widget.  So the gate has to
        be explicit: the panel is visible, focus is inside it, and the focused
        widget is not one that wants the arrow keys for itself.
        """
        if not self._keyboard_checkbox.isChecked() or self._goal_active:
            return False
        if not self.isVisible():
            return False
        focused = QApplication.focusWidget()
        if focused is None or not (focused is self or self.isAncestorOf(focused)):
            return False
        return not isinstance(focused, (QDoubleSpinBox, QComboBox))

    def eventFilter(self, obj, event):
        event_type = event.type()
        # Anything that takes the operator's attention away from the panel is
        # treated as letting go of the controls.
        if event_type == QEvent.WindowDeactivate and self.isVisible():
            self._stop_teleop()
            return False
        if not self._keyboard_target():
            return False
        if event_type == QEvent.KeyPress and not event.isAutoRepeat():
            direction = self._key_direction(event.key())
            if direction is not None:
                self._start_teleop(*direction)
                return True
            if event.key() == Qt.Key_Space:
                self._stop_teleop()
                return True
        elif event_type == QEvent.KeyRelease and not event.isAutoRepeat():
            if self._key_direction(event.key()) is not None:
                self._stop_teleop()
                return True
        return False

    def hideEvent(self, event):
        self._stop_teleop()
        super(ControlPanelWidget, self).hideEvent(event)

    @staticmethod
    def _key_direction(key):
        return {
            Qt.Key_Up: (1.0, 0.0),
            Qt.Key_Down: (-1.0, 0.0),
            Qt.Key_Left: (0.0, 1.0),
            Qt.Key_Right: (0.0, -1.0),
        }.get(key)

    # -- perspective settings ----------------------------------------------

    def goal_pose(self):
        return self._goal_x.value(), self._goal_y.value(), self._goal_yaw.value()

    def set_goal_pose(self, x, y, yaw):
        self._goal_x.setValue(x)
        self._goal_y.setValue(y)
        self._goal_yaw.setValue(yaw)

    def driver_name(self):
        return self._driver_combo.currentText()

    def set_driver_name(self, name):
        self._driver_combo.setCurrentText(name)

    # -- teardown ----------------------------------------------------------

    def shutdown(self):
        self._teleop_timer.stop()
        self._state_timer.stop()
        QApplication.instance().removeEventFilter(self)
        # Publish the stop several times: this is the last chance to do it, and
        # the driver would otherwise keep the wheels turning for cmd_vel_timeout.
        for _ in range(3):
            self._cmd_vel_pub.publish(Twist())
        self._nav_client.destroy()
        self._node.destroy_subscription(self._diag_sub)
        self._node.destroy_publisher(self._cmd_vel_pub)


class ControlPanel(Plugin):

    def __init__(self, context):
        super(ControlPanel, self).__init__(context)
        self.setObjectName("ControlPanel")
        self._widget = ControlPanelWidget(context.node)
        if context.serial_number() > 1:
            self._widget.setWindowTitle(
                "%s (%d)" % (self._widget.windowTitle(), context.serial_number())
            )
        context.add_widget(self._widget)

    def shutdown_plugin(self):
        self._widget.shutdown()

    def save_settings(self, plugin_settings, instance_settings):
        x, y, yaw = self._widget.goal_pose()
        instance_settings.set_value("goal_x", x)
        instance_settings.set_value("goal_y", y)
        instance_settings.set_value("goal_yaw", yaw)
        instance_settings.set_value("driver", self._widget.driver_name())

    def restore_settings(self, plugin_settings, instance_settings):
        values = []
        for key in ("goal_x", "goal_y", "goal_yaw"):
            value = instance_settings.value(key)
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                values.append(0.0)
        self._widget.set_goal_pose(*values)
        driver = instance_settings.value("driver")
        if driver:
            self._widget.set_driver_name(driver)
