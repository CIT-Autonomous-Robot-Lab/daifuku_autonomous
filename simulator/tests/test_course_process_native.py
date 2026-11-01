# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Linux process-exit evidence for planners inheriting an ignored SIGINT."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import pytest


@pytest.mark.skipif(not hasattr(os, "pidfd_open"), reason="requires Linux pidfd process events")
@pytest.mark.parametrize("ignore_interrupt", [False, True])
def test_planner_stop_observes_exit_even_when_launch_ignores_sigint(ignore_interrupt: bool) -> None:
    # Given: the real OS disposition matches the launched planner's two possible states.
    spec = spec_from_file_location(
        "course_process", Path(__file__).parents[2] / "docker/simulator/course_process.py",
    )
    assert spec is not None and spec.loader is not None
    controller = module_from_spec(spec)
    spec.loader.exec_module(controller)
    disposition = "SIG_IGN" if ignore_interrupt else "SIG_DFL"
    child = subprocess.Popen([
        sys.executable, "-u", "-c",
        "import signal\n"
        f"signal.signal(signal.SIGINT, signal.{disposition})\n"
        "print('PROCESS_READY', flush=True)\n"
        "signal.pause()\n",
    ], stdout=subprocess.PIPE, text=True)
    deadline = threading.Timer(10, child.kill)
    deadline.start()
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "PROCESS_READY"
        # When: the course controller stops this exact owned process.
        controller.stop_planner(child.pid)
        # Then: the disposition-appropriate signal produces an observed process exit.
        expected = signal.SIGTERM if ignore_interrupt else signal.SIGINT
        assert child.wait(timeout=10) == -expected
    finally:
        deadline.cancel()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        if child.stdout is not None:
            child.stdout.close()
