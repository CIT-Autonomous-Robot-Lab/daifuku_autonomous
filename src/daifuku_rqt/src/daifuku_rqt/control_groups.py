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

"""ControlPanelWidget が並べる GroupBox。ROS コールバックは持たない。"""

from python_qt_binding.QtWidgets import (
    QGroupBox,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class StatusGroup(QGroupBox):
    """system_monitor が出す /diagnostics を CPU とプロセス表に映す。"""

    def __init__(self, parent=None):
        super(StatusGroup, self).__init__("稼働状況", parent)
        layout = QVBoxLayout(self)

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
        rows.setMinimumSectionSize(rows.defaultSectionSize() * 2 // 3)
        rows.setDefaultSectionSize(rows.defaultSectionSize() * 2 // 3)
        rows.setSectionResizeMode(QHeaderView.Fixed)
        self._node_table.horizontalHeader().setStretchLastSection(True)
        self._node_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )
        self._node_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._node_table.setFixedHeight(320)
        layout.addWidget(self._node_table)

    def show_diagnostics(self, message):
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
