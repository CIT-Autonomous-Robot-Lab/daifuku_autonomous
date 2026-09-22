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

"""ウェイポイント巡回の進行状態。joy_teleop がアクション呼び出しの前後で使う。

ROS のクライアントは持たない。二重押しと、取り消し中に次のゴールを出す穴を
ここで塞ぐ。
"""


class WaypointPatrol:
    """投げたゴールが受理されるまでのあいだと、走っているあいだ。"""

    def __init__(self):
        self.handle = None
        self.pending = False

    def running(self):
        return self.pending or self.handle is not None

    def mark_sent(self):
        self.pending = True

    def accepted(self, handle):
        self.pending = False
        self.handle = handle

    def rejected(self):
        self.pending = False
        self.handle = None

    def finished(self):
        self.handle = None
        self.pending = False
