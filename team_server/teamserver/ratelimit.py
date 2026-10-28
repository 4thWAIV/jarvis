"""In-process sliding-window rate limits (the service runs one replica).

Keyed by bucket and client address. Behind Railway's proxy the client
address is the last X-Forwarded-For hop, the one the proxy itself added.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.requests import Request

from teamserver.errors import Problem

# bucket: (requests, window seconds)
LIMITS = {
    "login": (30, 60),
    "pairing_start": (10, 60),
    "pairing_exchange": (40, 60),
    "pairing_decide": (20, 60),
    "admin": (60, 60),
}
_MAX_KEYS = 10_000


def client_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if hops:
        return hops[-1]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(self, limits: dict | None = None, monotonic=time.monotonic):
        self.limits = dict(LIMITS if limits is None else limits)
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._now = monotonic

    def check(self, bucket: str, key: str) -> None:
        count, window = self.limits[bucket]
        now = self._now()
        if len(self._hits) > _MAX_KEYS:
            self._hits.clear()
        hits = self._hits[(bucket, key)]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= count:
            retry = max(1, int(window - (now - hits[0])))
            raise Problem(429, "rate_limited", "Too many requests; slow down.",
                          retry_after=retry)
        hits.append(now)

    def hit(self, bucket: str, request: Request) -> None:
        self.check(bucket, client_address(request))
