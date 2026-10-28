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
"""Container engines and owned child-process lifecycle, independent of scenarios."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import IO, Protocol

class Handle(Protocol):
    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class Session(Protocol):
    def poll(self) -> int | None: ...

    def stop(self, timeout: float) -> int: ...


class Runtime(Protocol):
    engine: str
    network: str

    def create_network(self, name: str) -> None: ...

    def remove_network(self) -> None: ...

    def available(self) -> tuple[bool, str]: ...

    def image_exists(self, tag: str) -> bool: ...

    def build(
        self, dockerfile: Path, context: Path, tag: str, build_args: dict[str, str], log: Path
    ) -> int: ...

    def start(
        self,
        name: str,
        image: str,
        env: dict[str, str],
        mount: Path,
        command: list[str],
        log: Path,
    ) -> Handle: ...

    def remove(self, name: str) -> None: ...

    def names(self, prefix: str) -> list[str]: ...


class _ProcessHandle:
    def __init__(self, process: subprocess.Popen[bytes], log: IO[str]) -> None:
        self._process, self._log = process, log

    def poll(self) -> int | None:
        code = self._process.poll()
        if code is not None:
            self._log.close()
        return code

    def wait(self, timeout: float | None = None) -> int:
        code = self._process.wait(timeout=timeout)
        self._log.close()
        return code


class ContainerRuntime:
    """Docker- or Podman-CLI implementation (same subcommands on both)."""

    def __init__(self, engine: str) -> None:
        self.engine = engine
        self.network = 'host'

    def create_network(self, name: str) -> None:
        result = self._run(['network', 'create', '--driver', 'bridge', name])
        if result.returncode:
            raise RuntimeError(f'creating network {name}: {result.stdout}{result.stderr}')
        self.network = name

    def remove_network(self) -> None:
        if self.network != 'host':
            result = self._run(['network', 'rm', self.network])
            if result.returncode:
                raise RuntimeError(f'removing network {self.network}: {result.stdout}{result.stderr}')
            self.network = 'host'

    def _run(self, args: list[str], timeout: float = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.engine, *args], capture_output=True, text=True, timeout=timeout, check=False
        )

    def available(self) -> tuple[bool, str]:
        try:
            result = self._run(['info'], timeout=90)
        except FileNotFoundError as error:
            return False, f'{self.engine}: command not found ({error})'
        except subprocess.TimeoutExpired:
            return False, f'`{self.engine} info` did not answer within 90 s'
        if result.returncode == 0:
            return True, ''
        return False, (result.stdout + result.stderr).strip()

    def image_exists(self, tag: str) -> bool:
        return self._run(['image', 'inspect', tag]).returncode == 0

    def build(
        self, dockerfile: Path, context: Path, tag: str, build_args: dict[str, str], log: Path
    ) -> int:
        command = [self.engine, 'build', '-f', str(dockerfile), '-t', tag]
        for key, value in build_args.items():
            command += ['--build-arg', f'{key}={value}']
        command.append(str(context))
        with log.open('w', encoding='utf-8') as stream:
            stream.write('$ ' + ' '.join(command) + '\n')
            stream.flush()
            return subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=False).returncode

    def start(
        self,
        name: str,
        image: str,
        env: dict[str, str],
        mount: Path,
        command: list[str],
        log: Path,
    ) -> Handle:
        args = [self.engine, 'run', '--rm', '--name', name, '--init', '--network', self.network]
        args += ['--shm-size', '512m']
        if hasattr(os, 'getuid'):
            args += ['--user', f'{os.getuid()}:{os.getgid()}']
        for key, value in env.items():
            args += ['--env', f'{key}={value}']
        args += ['--mount', f'type=bind,source={mount},target=/output', image, *command]
        stream = log.open('w', encoding='utf-8')
        stream.write('$ ' + ' '.join(args) + '\n')
        stream.flush()
        process = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT)
        return _ProcessHandle(process, stream)

    def remove(self, name: str) -> None:
        if name in self.names(name):
            self._run(['rm', '--force', name], timeout=60)

    def names(self, prefix: str) -> list[str]:
        result = self._run(['ps', '--all', '--filter', f'name={prefix}', '--format', '{{.Names}}'])
        return [line.strip() for line in result.stdout.splitlines() if line.strip().startswith(prefix)]


class _SessionProcess:
    def __init__(self, process: subprocess.Popen[str], log: IO[str]) -> None:
        self._process, self._log = process, log

    def poll(self) -> int | None:
        return self._process.poll()

    def stop(self, timeout: float) -> int:
        try:
            if self._process.stdin and not self._process.stdin.closed:
                self._process.stdin.write('stop\n')
                self._process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            code = self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            code = self._process.wait(timeout=30)
        self._log.close()
        return code


def start_session(command: list[str], log: Path, env: dict[str, str]) -> Session:
    """Start a supervised CLI process that accepts stop on standard input."""
    stream = log.open('w', encoding='utf-8')
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=stream,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, **env, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'},
    )
    return _SessionProcess(process, stream)
