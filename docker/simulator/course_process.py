# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Restart only the dedicated course container's VI process between regional maps."""
from __future__ import annotations

import os
from pathlib import Path
import select
import shutil
import signal
import subprocess


def planner_command(output: Path) -> tuple[int, list[str]]:
    """Capture the one launched VI process and preserve its temporary parameter files."""
    matches: list[tuple[int, list[str]]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() != "vi_planner":
                continue
            argv = [
                item.decode() for item in (entry / "cmdline").read_bytes().split(b"\0") if item
            ]
            if argv:
                matches.append((int(entry.name), argv))
        except FileNotFoundError:
            continue
    if len(matches) != 1:
        raise RuntimeError(f"Expected one owned VI process, found {[pid for pid, _ in matches]}")
    pid, argv = matches[0]
    for index, argument in enumerate(argv[:-1]):
        if argument == "--params-file":
            snapshot = output / f"vi-params-{index:02}.yaml"
            shutil.copyfile(argv[index + 1], snapshot)
            argv[index + 1] = str(snapshot)
    return pid, argv


def stop_planner(pid: int) -> None:
    """Await actual exit, accounting for SIGINT ignored by an asynchronous launch tree."""
    with os.fdopen(os.pidfd_open(pid), "rb", buffering=0) as exited:
        ignored = next(
            int(line.split()[1], 16)
            for line in Path(f"/proc/{pid}/status").read_text().splitlines()
            if line.startswith("SigIgn:")
        )
        requested = signal.SIGTERM if ignored & (1 << (signal.SIGINT - 1)) else signal.SIGINT
        print(
            f"COURSE_PLANNER_STOP pid={pid} signal={requested.name} ignored={ignored:x}",
            flush=True,
        )
        os.kill(pid, requested)
        if not select.select([exited], [], [], 30)[0]:
            raise TimeoutError(f"VI process {pid} did not exit")


def start_planner(argv: list[str], log: Path) -> subprocess.Popen[bytes]:
    """Start VI; the caller awaits its uniquely named action server's discovery."""
    with log.open("wb") as stream:
        return subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT)
