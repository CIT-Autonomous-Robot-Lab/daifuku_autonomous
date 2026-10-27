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
"""An owned Docker-network X11 connection to Docker Desktop's WSLg socket."""
from __future__ import annotations

import subprocess
import threading
import uuid
from pathlib import Path

class WslgDisplay:
    """Keep the display connection alive for exactly one simulator session."""

    def __init__(self, image: str, network: str) -> None:
        self.name = 'daifuku-wslg-' + uuid.uuid4().hex
        self.display = ('127.0.0.1' if network == 'host' else self.name) + ':0'
        subprocess.run(
            [
                'docker', 'create', '--init', '--name', self.name,
                '--network', network,
                '--mount', 'type=bind,source=/mnt/host/wslg/.X11-unix,target=/tmp/.X11-unix,readonly',
                '--mount', f'type=bind,source={Path(__file__).with_name("wslg_proxy.py")},target=/run/display.py,readonly',
                '--entrypoint', 'python3', image, '-u', '/run/display.py',
            ],
            capture_output=True, text=True, check=True, timeout=30,
        )
        try:
            self.process = subprocess.Popen(
                ['docker', 'start', '--attach', self.name],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        except OSError:
            subprocess.run(['docker', 'rm', '--force', self.name], capture_output=True,
                           check=True, timeout=30)
            raise
        # Subscribe to the listener's readiness rather than racing simulator startup.
        deadline = threading.Timer(30, self.process.kill)
        deadline.start()
        try:
            assert self.process.stdout is not None
            output = self.process.stdout.readline()
            if output.strip() != 'DISPLAY_READY':
                raise RuntimeError(f'WSLg display startup failed: {output.strip()}')
        except (OSError, RuntimeError):
            self.close()
            raise
        finally:
            deadline.cancel()

    def close(self) -> None:
        subprocess.run(
            ['docker', 'rm', '--force', self.name],
            capture_output=True, check=True, timeout=30,
        )
        self.process.wait(timeout=30)
        if self.process.stdout is not None:
            self.process.stdout.close()
