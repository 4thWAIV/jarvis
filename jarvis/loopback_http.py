"""The one HTTP server class every loopback HTTP server uses (the knowledge
engine's, and every test server through tests/loopback_server.py).

The stdlib's `HTTPServer.server_bind` sets `server_name` from
`socket.getfqdn(host)`, a reverse DNS lookup of the bound address. On the
macOS CI runner the first such lookup of 127.0.0.1 takes about 35 seconds:
the knowledge engine had not listened 30 s after it started, so an
isolated JARVIS stopped at startup. Nothing reads `server_name` for anything
a lookup would change. These classes bind exactly as the stdlib does and
take the name from the address.
"""

import http.server
import socketserver


class LoopbackHTTPServer(http.server.HTTPServer):
    """`http.server.HTTPServer` without the reverse DNS lookup on bind."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port


class LoopbackThreadingHTTPServer(socketserver.ThreadingMixIn, LoopbackHTTPServer):
    """`http.server.ThreadingHTTPServer` without the reverse DNS lookup."""

    daemon_threads = True
