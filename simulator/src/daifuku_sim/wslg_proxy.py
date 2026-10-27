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
"""X11 byte transport, executed inside the owned WSLg connection container."""
from __future__ import annotations

import selectors
import socket
import threading


def relay(client: socket.socket, server: socket.socket) -> None:
    """Copy both directions until either peer closes its connection."""
    with client, server, selectors.DefaultSelector() as selector:
        selector.register(client, selectors.EVENT_READ)
        selector.register(server, selectors.EVENT_READ)
        while True:
            for key, _ in selector.select():
                source, target = (client, server) if key.fd == client.fileno() else (server, client)
                data = source.recv(65536)
                if not data:
                    return
                target.sendall(data)


def connect(client: socket.socket) -> None:
    with client, socket.socket(socket.AF_UNIX) as server:
        server.connect('/tmp/.X11-unix/X0')
        relay(client, server)


def main() -> None:
    # Fail before readiness if Docker Desktop has no accessible WSLg server.
    with socket.socket(socket.AF_UNIX) as probe:
        probe.connect('/tmp/.X11-unix/X0')
    with socket.create_server(('0.0.0.0', 6000)) as listener:
        print('DISPLAY_READY', flush=True)
        while True:
            client, _ = listener.accept()
            threading.Thread(target=connect, args=(client,), daemon=True).start()


if __name__ == '__main__':
    main()
