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
"""Display transport and its simulator-owned lifetime."""
from concurrent.futures import ThreadPoolExecutor
import socket

from daifuku_sim.wslg_proxy import relay


def test_display_relay_transports_both_directions_and_stops_on_disconnect() -> None:
    # Given: real stream sockets on each side of the X11 relay.
    client, relay_client = socket.socketpair()
    server, relay_server = socket.socketpair()
    with ThreadPoolExecutor(max_workers=1) as executor, client, server:
        client.settimeout(5)
        server.settimeout(5)
        completed = executor.submit(relay, relay_client, relay_server)
        # When: both peers exchange data and one closes the connection.
        client.sendall(b'X11 request')
        with server.makefile('rb') as stream:
            request = stream.read(11)
        server.sendall(b'X11 reply')
        with client.makefile('rb') as stream:
            reply = stream.read(9)
        client.shutdown(socket.SHUT_RDWR)
        completed.result(timeout=5)
        # Then: the transport preserves bytes and releases the server connection.
        assert request == b'X11 request'
        assert reply == b'X11 reply'
        assert server.recv(1) == b''
