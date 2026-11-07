"""The one gate above every engine route (design section 7.3).

It mirrors `web_auth`'s rules, narrowed because no browser ever talks to
the engine:

- `Host` must name loopback on the engine's own port, so a rebinding DNS
  name cannot reach it from a page;
- any request carrying an `Origin` is refused, since only the JARVIS server
  calls, and it sends none;
- every request needs `Authorization: Bearer <engine token>`, compared in
  constant time.

There is no CORS: nothing is ever allowed cross-origin.
"""
from __future__ import annotations

import hmac
from typing import NamedTuple, Optional

LOOPBACK_NAMES = ("127.0.0.1", "localhost")


class Refusal(NamedTuple):
    status: int
    code: str


def host_allowed(host_header: Optional[str], port: int) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    return host in {f"{name}:{port}" for name in LOOPBACK_NAMES}


def bearer(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    scheme, _, token = value.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def check(headers, port: int, token: str) -> Optional[Refusal]:
    """None when the request may pass; else the refusal to answer with."""
    if not host_allowed(headers.get("Host"), port):
        return Refusal(421, "host_refused")
    if headers.get("Origin") is not None:
        return Refusal(403, "origin_refused")
    supplied = bearer(headers.get("Authorization"))
    if supplied is None or not token or not hmac.compare_digest(
            supplied.encode("utf-8"), token.encode("utf-8")):
        return Refusal(401, "token_refused")
    return None
