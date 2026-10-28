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
"""Command-line entry point for daifuku simulation scenarios."""
from __future__ import annotations

import argparse
from typing import Callable


class CommandOptions(argparse.Namespace):
    handler: Callable[[argparse.Namespace], int] | None = None


def build_parser() -> argparse.ArgumentParser:
    from .vtc.cli import configure_parser

    parser = argparse.ArgumentParser(description='Run daifuku simulation scenarios.')
    scenarios = parser.add_subparsers(dest='scenario')
    configure_parser(scenarios.add_parser('vtc', help='VTC mapping and navigation'))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(argv, namespace=CommandOptions())
    if options.handler is None:
        parser.print_help()
        return 0
    return options.handler(options)
