# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Discover checkout worlds and display them with the installed Gazebo Classic."""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys


WORLDS = Path(__file__).resolve().parents[2] / "worlds"


class WorldOptions(argparse.Namespace):
    headless: bool
    wsl_distro: str


def run_world(world: Path, options: WorldOptions) -> int:
    """Show a world without creating a robot or starting a navigation scenario."""
    executable = "gzserver" if options.headless else "gazebo"
    try:
        if sys.platform == "win32":
            prefix = ["wsl.exe", "--distribution", options.wsl_distro, "--exec"]
            converted = subprocess.run(
                [*prefix, "wslpath", "-a", world.as_posix()],
                capture_output=True, text=True, check=False,
            )
            if converted.returncode:
                print(converted.stderr, file=sys.stderr, end="")
                return converted.returncode
            world_path = converted.stdout.strip()
        else:
            prefix = []
            world_path = str(world)
        directory = shlex.quote(str(PurePosixPath(world_path).parent))
        master_uri = shlex.quote(os.environ.get("GAZEBO_MASTER_URI", "http://127.0.0.1:11349"))
        model_database = shlex.quote(os.environ.get("GAZEBO_MODEL_DATABASE_URI", ""))
        script = (
            "source /usr/share/gazebo/setup.bash && "
            f"cd {directory} && "
            'export GAZEBO_RESOURCE_PATH="$PWD:$GAZEBO_RESOURCE_PATH" && '
            f"export GAZEBO_MODEL_DATABASE_URI={model_database} && "
            f"export GAZEBO_MASTER_URI={master_uri} && "
            f"exec {executable} --verbose {shlex.quote(world_path)}"
        )
        return subprocess.run([*prefix, "bash", "-lc", script], check=False).returncode
    except FileNotFoundError as error:
        print(f"Cannot start Gazebo: {error}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


def register_worlds(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Built-in scenario names take precedence over filesystem world names."""
    if not WORLDS.is_dir():
        return
    for directory in sorted(WORLDS.iterdir()):
        world = directory / "world.sdf"
        if directory.name in commands.choices or not world.is_file():
            continue
        parser = commands.add_parser(directory.name, help=f"Show worlds/{directory.name}/world.sdf")
        parser.add_argument("--headless", action="store_true", help="run the physics server without GUI")
        parser.add_argument(
            "--wsl-distro", default=os.environ.get("DAIFUKU_WSL_DISTRO", "Ubuntu-22.04"),
            help="Windows: WSL distribution containing Gazebo Classic (default: Ubuntu-22.04)",
        )

        def dispatch(options: WorldOptions, world: Path = world) -> int:
            return run_world(world, options)

        parser.set_defaults(handler=dispatch)
