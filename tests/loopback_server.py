"""The one HTTP server class every test server uses: JARVIS's own
(jarvis/loopback_http.py), which binds without the stdlib's reverse DNS
lookup of the address (about 35 seconds on the macOS CI runner)."""

from jarvis.loopback_http import LoopbackHTTPServer, LoopbackThreadingHTTPServer

__all__ = ["LoopbackHTTPServer", "LoopbackThreadingHTTPServer"]
