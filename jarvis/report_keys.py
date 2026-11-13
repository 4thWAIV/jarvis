"""Each report an agent sends lands at most once, and a reporter that did
not hear the answer can ask whether it landed.

Every report from the reporters' one client (workflow_report.py's, which
work_report.py and message.py use) carries an idempotency key, `?report=`
on its route (`QUERY`; in the query, so it is signed with the request).
The route (`once`) records the key before the write, as its caller's, with
its route and this server's boot (`pending`), and with the route's answer
once the write is done (`done`): one `report` document per key,
`/reports/<principal>/<key>.report`, through records.py. A repeated key is
not written again: it is answered with the original answer (its status and
body, marked `REPEATED_HEADER`), or, while the first is still being
written, refused with 409 saying so (`STILL_WRITING`). A key claimed by an
earlier run of the server that never finished is `unknown`: whether its
write landed cannot be said, so it is never run again.

`GET /api/reports/<key>` (`lookup`) says what is on record for one of the
caller's own keys: `{"state": "none"}`, `pending`, `unknown`, or `done`
with the original answer. A reporter that timed out asks it, and either
reports the answer it finds or sends the report once more with the same
key (`workflow_report.report`).

Keys are the caller's own: one agent can neither see nor repeat another's.
Kept for `KEEP_SEC`, then dropped by `prune` (at server start).
"""
from __future__ import annotations

import functools
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from jarvis import records, reporters
from jarvis.workflow_report import REPORT_QUERY

QUERY = REPORT_QUERY
ROOT, TYPE = "/reports", "report"
LOOKUP_PATH = "/api/reports/{key}"
KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")
NONE, PENDING, DONE, UNKNOWN = "none", "pending", "done", "unknown"
REPEATED_HEADER = "X-JARVIS-Report"
KEEP_SEC = 7 * 24 * 3600.0
STILL_WRITING = ("This report (key {key}) is still being written; it was not sent again. "
                 "Ask again in a moment.")
NOT_KNOWN = ("Whether this report (key {key}) landed is not known: JARVIS restarted while "
             "writing it. It was not sent again; check the record before reporting it anew.")
# This run of the server: a key still pending from another run never finished.
BOOT = uuid.uuid4().hex


@dataclass
class Found:
    """What is on record for a key."""
    state: str
    status: int = 0
    body: dict = field(default_factory=dict)

    def json(self) -> dict:
        out = {"state": self.state}
        if self.state == DONE:
            out.update(status=self.status, body=self.body)
        return out


def path_of(principal: str, key: str) -> str:
    return f"{ROOT}/{records.segment(principal or 'unknown')}/{key}.{TYPE}"


def valid(key: str) -> bool:
    return bool(KEY_PATTERN.fullmatch(key or ""))


def _found(data: dict) -> Found:
    if data.get("state") == DONE:
        return Found(DONE, int(data.get("status") or 200), dict(data.get("body") or {}))
    return Found(PENDING if data.get("boot") == BOOT else UNKNOWN)


def claim(principal: str, route: str, key: str, now: Optional[float] = None) -> Optional[Found]:
    """Claim the key for a write about to be made: None when it is claimed
    now (go on), else what is already on record for it."""
    path = path_of(principal, key)
    at = time.time() if now is None else now

    def run(unit) -> Optional[Found]:
        rec = unit.get(path)
        if rec is not None:
            return _found(rec.data or {})
        unit.put(path, {"key": key, "principal": principal, "route": route, "state": PENDING,
                        "boot": BOOT, "at": at, "status": None, "body": None})
        return None
    return records.atomic(run, by=records.JARVIS, change="report_claimed",
                          summary="A report's key is claimed before its write.")


def finish(principal: str, key: str, status: int, body: dict) -> None:
    """The write the key was claimed for is done: keep its answer."""
    def done(data):
        if data is None:
            return None
        return {**data, "state": DONE, "status": status, "body": body, "done_at": time.time()}
    records.update(path_of(principal, key), done, by=records.JARVIS, change="report_done",
                   summary="A report's answer is kept with its key.")


def release(principal: str, key: str) -> None:
    """The write the key was claimed for failed before anything landed:
    the key is free to be sent again."""
    records.delete(path_of(principal, key), by=records.JARVIS, change="report_released",
                   summary="A report that failed frees its key.")


def lookup(principal: str, key: str) -> Found:
    """What is on record for one of the caller's keys."""
    rec = records.get(path_of(principal, key))
    return Found(NONE) if rec is None else _found(rec.data or {})


def prune(now: Optional[float] = None) -> int:
    """Drop the keys older than KEEP_SEC. How many went. Never raises."""
    cutoff = (time.time() if now is None else now) - KEEP_SEC
    try:
        old = [r.path for r in records.scan(ROOT, types=[TYPE], where=[["at", "<", cutoff]])]
        for path in old:
            records.delete(path, by=records.JARVIS, change="report_pruned",
                           summary="A report's key past its keeping.")
        return len(old)
    except Exception:
        return 0


def _answer(result) -> tuple[int, dict]:
    """A route's answer as (status, JSON body)."""
    if isinstance(result, Response):
        try:
            return result.status_code, json.loads(bytes(result.body) or b"{}")
        except ValueError:
            return result.status_code, {}
    return 200, jsonable_encoder(result)


def _repeated(key: str, found: Found) -> JSONResponse:
    if found.state == DONE:
        return JSONResponse(found.body, status_code=found.status,
                            headers={REPEATED_HEADER: "repeated"})
    words = STILL_WRITING if found.state == PENDING else NOT_KNOWN
    return JSONResponse({"error": words.format(key=key), "report": found.state},
                        status_code=409, headers={REPEATED_HEADER: found.state})


def once(route):
    """Make a reporting route take each `?report=` key once (see the
    module). The route must take `request: Request`."""
    import asyncio

    @functools.wraps(route)
    async def wrapped(*args, **kwargs):
        request: Request = kwargs["request"]
        key = request.query_params.get(QUERY, "")
        if not key:
            return await route(*args, **kwargs)
        if not valid(key):
            return JSONResponse({"error": "A report key is 16 to 64 letters, digits, - or _."},
                                status_code=400)
        principal = reporters.recorded(request.scope)
        found = await asyncio.to_thread(claim, principal, request.url.path, key)
        if found is not None:
            return _repeated(key, found)
        try:
            result = await route(*args, **kwargs)
        except BaseException:
            await asyncio.to_thread(release, principal, key)
            raise
        status, body = _answer(result)
        await asyncio.to_thread(finish, principal, key, status, body)
        return result
    return wrapped


def mount(app: FastAPI) -> None:
    """`GET /api/reports/<key>`: what is on record for one of the caller's
    own keys."""
    import asyncio

    @app.get(LOOKUP_PATH)
    async def report_landed(key: str, request: Request):
        if not valid(key):
            return JSONResponse({"error": "A report key is 16 to 64 letters, digits, - or _."},
                                status_code=400)
        found = await asyncio.to_thread(lookup, reporters.recorded(request.scope), key)
        return found.json()
