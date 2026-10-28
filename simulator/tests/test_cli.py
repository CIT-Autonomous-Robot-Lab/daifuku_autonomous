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
"""Public CLI routing without ROS or a container engine."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from daifuku_sim.cli import main
from daifuku_sim.vtc import cli as vtc_cli


@pytest.mark.parametrize('arguments', [
    [],
    ['--help'],
    ['vtc', '--help'],
    ['vtc', 'run', '--help'],
    ['vtc', 'video', '--help'],
])
def test_help_needs_no_simulator_or_video_dependencies(arguments: list[str]) -> None:
    # Given: an independent interpreter with no simulator dependencies imported.
    script = (
        'import sys\n'
        'from daifuku_sim.cli import main\n'
        'try:\n'
        '    code = main(sys.argv[1:])\n'
        'except SystemExit as error:\n'
        '    code = error.code\n'
        'assert code == 0\n'
        'assert not any(name.split(".")[0] in '
        '{"usim", "usim_gazebo", "usim_env_vtc", "PIL"} for name in sys.modules)\n'
    )
    # When: the public CLI handles a help request or no command.
    result = subprocess.run(
        [sys.executable, '-c', script, *arguments],
        capture_output=True, text=True, timeout=30, check=False,
    )
    # Then: help is available without loading either runtime or rendering packages.
    assert result.returncode == 0, result.stderr
    assert result.stdout


@pytest.mark.parametrize('arguments', [
    ['unknown'],
    ['vtc'],
    ['vtc', 'unknown'],
    ['vtc', 'run', '--tolerance', 'nan'],
    ['vtc', 'run', '--goal', 'invalid'],
    ['vtc', 'run', '--survey', '1,2'],
])
def test_invalid_commands_fail_before_creating_run_data(
    tmp_path: Path, arguments: list[str],
) -> None:
    # Given: an empty working directory and invalid command input.
    # When: the real module entry point processes it.
    result = subprocess.run(
        [sys.executable, '-m', 'daifuku_sim', *arguments],
        cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False,
    )
    # Then: argument parsing rejects it without starting a workflow.
    assert result.returncode == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('legacy', [False, True])
def test_run_dispatch_preserves_result_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], legacy: bool,
) -> None:
    # Given: an explicit invalid asset checkout, requiring no external services.
    run_dir = tmp_path / 'run'
    arguments = [
        '--usim-root', str(tmp_path / 'missing'),
        '--run-dir', str(run_dir),
    ]
    # When: either the unified command or the retained alias runs the workflow.
    code = vtc_cli.main(arguments) if legacy else main(['vtc', 'run', *arguments])
    # Then: the real workflow writes the same classified verdict and summary.
    result = json.loads((run_dir / 'result.json').read_text(encoding='utf-8'))
    summary = json.loads(capsys.readouterr().out)
    assert code == result['exit_code'] == summary['exit_code'] == 2
    assert result['schema'] == 'daifuku-usim-vtc/1'
    assert result['status'] == summary['status'] == 'FAIL'
    assert result['stage'] == summary['stage'] == 'preflight'
    assert result['cleanup'] == {'engine_calls': False}


@pytest.mark.parametrize('legacy', [False, True])
def test_video_dispatch_reports_missing_input_without_creating_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], legacy: bool,
) -> None:
    # Given: no recorded telemetry exists.
    output = tmp_path / 'video.mp4'
    arguments = [str(tmp_path / 'missing.jsonl'), str(output)]
    # When: either video command invokes the real renderer.
    code = vtc_cli.video_main(arguments) if legacy else main(['vtc', 'video', *arguments])
    # Then: the input error retains exit code 2 and does not create a movie.
    assert code == 2
    assert capsys.readouterr().err
    assert not output.exists()
