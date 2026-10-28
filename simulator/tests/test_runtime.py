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
"""The shared process supervisor stops its owned child through standard input."""
from __future__ import annotations

import sys
from pathlib import Path

from daifuku_sim.runtime import start_session


def test_session_delivers_stop_and_environment_to_owned_process(tmp_path: Path) -> None:
    # Given: a child waits for the supervisor's exact stop signal.
    log = tmp_path / 'child.log'
    script = (
        'import os, sys\n'
        'print(sys.stdin.readline().strip())\n'
        'print(os.environ["DAIFUKU_TEST_SESSION"])\n'
    )
    session = start_session(
        [sys.executable, '-c', script], log, {'DAIFUKU_TEST_SESSION': 'owned'},
    )
    # When: shutdown sends the signal and joins the real process.
    try:
        code = session.stop(timeout=10)
    finally:
        if session.poll() is None:
            session.stop(timeout=0)
    # Then: the child exits cleanly after receiving both the signal and environment.
    assert code == 0
    assert log.read_text(encoding='utf-8').splitlines() == ['stop', 'owned']
