import socket

import pytest
from http.server import BaseHTTPRequestHandler

from tests.loopback_server import LoopbackHTTPServer, LoopbackThreadingHTTPServer


@pytest.mark.parametrize("server_class", [LoopbackHTTPServer, LoopbackThreadingHTTPServer])
def test_binding_never_looks_up_the_host_name(server_class, monkeypatch):
    def no_lookup(*args, **kwargs):
        raise AssertionError("socket.getfqdn was called")

    monkeypatch.setattr(socket, "getfqdn", no_lookup)
    server = server_class(("127.0.0.1", 0), BaseHTTPRequestHandler)
    try:
        port = server.socket.getsockname()[1]
        assert server.server_name == "127.0.0.1"
        assert server.server_port == port
        assert server.server_address[1] == port
    finally:
        server.server_close()
