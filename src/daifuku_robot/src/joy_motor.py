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

"""モータ電源の状態。joy_teleop がサービス呼び出しの前後で使う。

自前実装 (driver:=original) は /motor_power_state を出すので、来ていればそれが
答え。公式実装 (driver:=raspimouse) は出さないので、そのときだけ自分が投げた
要求の写しに落ちる (外から変えられると 1 回ぶんずれる)。
"""


class MotorPower:
    """ドライバが出す実状態と、自分が投げた要求の写し。"""

    def __init__(self, start_state=False):
        self._requested = bool(start_state)
        self._actual = None
        self.pending = False

    def is_on(self):
        return self._requested if self._actual is None else self._actual

    def note_driver_state(self, value):
        self._actual = bool(value)

    def begin_request(self, wanted):
        """要求を投げる直前。通らなければ revert_request。"""
        self.pending = True
        self._requested = bool(wanted)

    def complete_request(self):
        self.pending = False

    def revert_request(self):
        """サービスが拒んだ / 落ちた。写しを使う構成で次の長押しが逆にならないように戻す。"""
        self.pending = False
        self._requested = not self._requested
