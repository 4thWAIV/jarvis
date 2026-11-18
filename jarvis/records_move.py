"""JARVIS answering while its records move into the engine.

The first start after the records moved copies jarvis.db into the engine
(`records_cutover.cut_over`), which takes minutes on a large file. The
server does not wait for it to serve: `server.lifespan` starts the engine,
and when a move is needed (`records_cutover.needed`) runs it here in the
background, then starts everything that needs the records once it is done.
Until then:

- `status()` says where the move is: its state (`MOVING`, `STARTING` once
  it is done and the rest starts, `READY`, or `FAILED` with why), the step
  (`records_cutover.STEPS`), the plan (rows, expected seconds, the disk it
  needs) and the time left, worked out at read time from the plan. The page
  reads it at `GET /api/records/move` and shows it in place of the
  workspace (`frontend/src/RecordsMove.tsx`); the brain's `records_move`
  reads the same, and after the move the cut-over's own record.
- `Gate` answers every route that needs the records 503 with that reason
  (a WebSocket is closed 1013, try again later), so the voice and agents
  wait and nothing reads as down: a client is told the records are moving.
  The page itself, `/api/health`, this status and the language setting
  (a file) are open.
- A move that fails leaves the server up saying so and why; jarvis.db is
  untouched, and the next start tries again.
- The same cover holds while the knowledge engine is still opening the
  records past knowledge_client.READY_WAIT_SEC (`OPENING`, `kind` `open`:
  slow, not failed; the server starts everything the moment it answers,
  then `STARTING` and `READY` as after a move, with no Try again), for a
  start that cannot open the records at all (`cause` `ENGINE`: the engine
  kept exiting while it opened, or had not answered within
  knowledge_client.OPEN_BOUND_SEC, with the supervisor's reason and the end
  of the engine's log) and for a start that failed once they were open
  (`START`). Nothing is up but this server and the page.
- While it has failed, `POST /api/restart` from the page's own origin
  (`RETRY`) goes through the gate, so the page's Try again starts JARVIS
  again from the dashboard. `boot` is this process's own id, so the page
  knows the server it asked has been replaced.

The move runs in a daemon thread of its own, so stopping JARVIS during it
(Ctrl-C, a closed terminal) stops at once; the next start clears what was
written and copies again in full (records_cutover's rule).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from jarvis import records_cutover

log = logging.getLogger("jarvis.records_move")

MOVING, STARTING, READY, FAILED = "moving", "starting", "ready", "failed"
OPENING = "opening"
# What the page is waiting on: the records moving, or the engine opening them.
MOVE_KIND, OPEN_KIND = "move", "open"
OPEN = frozenset({("GET", "/api/health"), ("GET", "/api/records/move"),
                  ("GET", "/api/settings/language")})
GATED_PREFIXES = ("/api/", "/ws/", "/internal/")
# What failed: the move itself, the knowledge engine before the records could
# be opened, or the start after the records were open.
MOVE, ENGINE, START = "move", "engine", "start"
# The one route open while it has failed: the page's Try again.
RETRY = ("POST", "/api/restart")
# This process: a restart is a new one, which is how the page knows its Try
# again took.
BOOT = uuid.uuid4().hex
SAFE_TO_STOP = ("It is safe to stop JARVIS during the move: the next start copies again "
                "from the beginning, and jarvis.db is never changed.")


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.state = READY
        self.kind = MOVE_KIND
        self.step: Optional[str] = None
        self.started_at: Optional[float] = None       # time.time()
        self.step_at: Optional[float] = None          # time.monotonic()
        self.begun: Optional[float] = None            # time.monotonic()
        self.done_steps: list[str] = []
        self.plan: Optional[dict] = None
        self.reason: Optional[str] = None
        self.cause: Optional[str] = None


_state = _State()


def reset() -> None:
    """Ready, with no move (tests, and a server started again in process)."""
    with _state.lock:
        _state.reset()


def ready() -> bool:
    return _state.state == READY


def _left(plan: Optional[dict], step: Optional[str], done_steps: list[str],
          in_step: float) -> Optional[float]:
    """Seconds left by the plan: the rest of this step, then every step
    after it. None before the plan; 0 once past what was expected."""
    if not plan or step is None:
        return None
    expected = plan.get("expected") or {}
    rest = sum(v for k, v in expected.items() if k != step and k not in done_steps)
    return round(max(0.0, expected.get(step, 0.0) - in_step) + rest, 1)


def status() -> dict:
    """Where the move is, for the page and the brain alike."""
    with _state.lock:
        s = _state
        now = time.monotonic()
        in_step = now - s.step_at if s.step_at is not None else 0.0
        out: dict[str, Any] = {
            "state": s.state, "kind": s.kind, "step": s.step,
            "steps": [st for st in records_cutover.STEPS
                      if st != records_cutover.CLEAR or (s.plan or {}).get("clearing")],
            "done_steps": list(s.done_steps), "started_at": s.started_at,
            "elapsed": round(now - s.begun, 1) if s.begun is not None else None,
            "left": _left(s.plan, s.step, s.done_steps, in_step) if s.state == MOVING else None,
            "plan": s.plan, "reason": s.reason, "cause": s.cause, "boot": BOOT,
            "safe_to_stop": SAFE_TO_STOP}
    return out


def _on_step(step: str, facts: dict) -> None:
    with _state.lock:
        if _state.step is not None and _state.step not in _state.done_steps:
            _state.done_steps.append(_state.step)
        _state.step, _state.step_at = step, time.monotonic()
        if step == records_cutover.SPACE:
            _state.plan = dict(facts)
    log.info("records move: %s", step)


def _set(state: str, reason: Optional[str] = None, cause: Optional[str] = None) -> None:
    with _state.lock:
        _state.state = state
        if reason is not None:
            _state.reason = reason
        if cause is not None:
            _state.cause = cause


def opening(since: Optional[float] = None) -> None:
    """The engine is still opening the records (slow, not failed): every
    route that needs them waits, saying so, timed from `since`
    (time.monotonic(), the engine's start) for how long so far."""
    with _state.lock:
        _state.reset()
        _state.state, _state.kind = OPENING, OPEN_KIND
        now = time.monotonic()
        _state.begun = now if since is None else since
        _state.started_at = time.time() - (now - _state.begun)


def opened() -> None:
    """The engine answered: everything that needs the records starts."""
    _set(STARTING)


def begin() -> None:
    """A move is starting: every route that needs the records waits."""
    with _state.lock:
        _state.reset()
        _state.state = MOVING
        _state.started_at, _state.begun = time.time(), time.monotonic()


async def run(cut_over=None) -> bool:
    """Move the records (`begin` first) in a daemon thread; True once they
    are moved and proven, False (the state `FAILED`, with why) otherwise."""
    cut_over = cut_over or records_cutover.cut_over
    loop = asyncio.get_running_loop()
    finished: asyncio.Future = loop.create_future()

    def settle(fn, value) -> None:
        if not finished.done():
            fn(value)

    def work() -> None:
        try:
            out = cut_over(on_step=_on_step)
        except BaseException as e:            # every way out is told to the page
            try:
                loop.call_soon_threadsafe(settle, finished.set_exception, e)
            except RuntimeError:              # the server already stopped
                pass
            return
        try:
            loop.call_soon_threadsafe(settle, finished.set_result, out)
        except RuntimeError:
            pass

    threading.Thread(target=work, name="records-move", daemon=True).start()
    try:
        out = await finished
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("records move failed")
        failed(f"{type(e).__name__}: {e}" if not isinstance(e, records_cutover.CutoverFailed)
               else str(e))
        return False
    log.info("records moved into the engine: %d rows, %s", out.rows, out.seconds)
    with _state.lock:
        if _state.step is not None and _state.step not in _state.done_steps:
            _state.done_steps.append(_state.step)
        _state.step = None
    _set(STARTING)
    return True


def failed(reason: str, cause: str = MOVE) -> None:
    """The move, the engine before it, or the start after it failed
    (`cause`): the server stays up saying so and why, and the page offers
    Try again."""
    _set(FAILED, reason[:4000], cause)


def started() -> None:
    """Everything that needs the records is up."""
    _set(READY)


def said(record: Optional[dict] = None) -> str:
    """The move in words, for the brain: where it is, or, once done, the
    cut-over's record."""
    st = status()
    if st["state"] == FAILED and st["cause"] == ENGINE:
        return (f"JARVIS could not open its records: {st['reason']}. The page offers Try again, "
                "which starts JARVIS again.")
    if st["state"] == FAILED and st["cause"] == START:
        return (f"JARVIS could not start on its records: {st['reason']}. The page offers Try "
                "again, which starts JARVIS again.")
    if st["state"] == FAILED:
        return (f"The records move failed: {st['reason']}. jarvis.db is untouched; the page's Try "
                "again starts JARVIS again, which tries the move again.")
    if st["kind"] == OPEN_KIND and st["state"] == OPENING:
        return (f"JARVIS is opening its records: the knowledge engine has been opening them for "
                f"{round(st['elapsed'] or 0)} s. Everything starts the moment it answers.")
    if st["kind"] == OPEN_KIND and st["state"] == STARTING:
        return "JARVIS has opened its records and is starting everything that needs them."
    if st["state"] in (MOVING, STARTING):
        left = st["left"]
        return (f"The records are moving into the engine (step {st['step']}"
                + (f", about {round(left)} s left" if left else "") + "). " + SAFE_TO_STOP)
    if record is None:
        return "No records move has happened on this install: it began on the engine."
    secs = (record.get("seconds") or {}).get("total")
    when = time.strftime("%d %B %Y %H:%M", time.localtime(record.get("cut_over_at") or 0))
    return (f"The records moved into the engine on {when}: {record.get('rows')} rows of "
            f"{record.get('tables')} tables, proven both ways"
            + (f" in {secs:.0f} s" if secs else "") + ". jarvis.db is kept untouched beside it.")


class Gate:
    """Every route that needs the records answers 503 while they move."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or ready() or not _gated(scope):
            return await self.app(scope, receive, send)
        st = status()
        if st["state"] == FAILED and _retry_from_page(scope):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await receive()                   # the connect
            await send({"type": "websocket.close", "code": 1013,
                        "reason": "records move failed" if st["state"] == FAILED
                        else "records opening" if st["kind"] == OPEN_KIND else "records moving"})
            return
        error = ("records_move_failed" if st["state"] == FAILED
                 else "records_opening" if st["kind"] == OPEN_KIND else "records_moving")
        detail = ("JARVIS is opening its records; try again shortly."
                  if st["state"] != FAILED and st["kind"] == OPEN_KIND
                  else "JARVIS is moving its records into the new engine; try again shortly."
                  if st["state"] != FAILED
                  else f"JARVIS could not open its records: {st['reason']}" if st["cause"] == ENGINE
                  else f"JARVIS could not start on its records: {st['reason']}" if st["cause"] == START
                  else f"JARVIS's records could not be moved: {st['reason']}")
        retry = str(max(5, int(st["left"] or 5)))
        await JSONResponse({"error": error, "detail": detail, "move": st}, status_code=503,
                           headers={"Retry-After": retry})(scope, receive, send)


def _gated(scope) -> bool:
    path = scope.get("path") or ""
    if not path.startswith(GATED_PREFIXES):
        return False
    return (scope.get("method", "GET"), path) not in OPEN


def _retry_from_page(scope) -> bool:
    """The page's Try again: `POST /api/restart` carrying an `Origin` (the
    OriginGuard outside this gate has already refused any origin JARVIS does
    not serve from) and no Authorization, so neither the tool token nor an
    agent's key restarts a JARVIS that has failed."""
    if (scope.get("method"), scope.get("path")) != RETRY:
        return False
    headers = {k.lower(): v for k, v in scope.get("headers") or []}
    return bool(headers.get(b"origin")) and b"authorization" not in headers


def mount(app: FastAPI) -> None:
    @app.get("/api/records/move")
    async def api_records_move():
        return status()
