"""The engine's loopback HTTP API (design sections 7.2, 7.3 and 7.12).

The standard library's `ThreadingHTTPServer` on 127.0.0.1 at a port the
kernel chooses: a small, auditable surface with a fast start and no FastAPI
import. Every request passes `auth.check` first.

Routes are one table, `ROUTES`, and `dispatch` is the one way into them, so
the in-process test transport (`knowledge_client.InProcessTransport`) runs
exactly what the HTTP handler runs, minus the gate.

`run()` is `python -m jarvis.knowledge serve`: open the engine (taking the
writer lock), listen, write the address record, and exit cleanly when its
stdin pipe closes, which is how the server that started it ends it, even
across the server's own `os.execv`. The process runs at normal priority, so
record reads and writes are never slowed; the maintenance thread, the
linker and a requested compaction run in the background lane (lanes.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, NamedTuple, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from jarvis.knowledge import address as engine_address
from jarvis.loopback_http import LoopbackThreadingHTTPServer
from jarvis.knowledge import auth
from jarvis.knowledge import lanes
from jarvis.knowledge.engine import Engine, EngineLocked, open_engine

log = logging.getLogger("jarvis.knowledge.serve")

BODY_MAX_BYTES = 64 << 20


class Request(NamedTuple):
    params: dict
    query: dict
    body: Any


class Answer(NamedTuple):
    status: int
    payload: Any


class BadRequest(ValueError):
    """A request the route cannot take; answered 400 with its words."""


Handler = Callable[[Engine, Request], Answer]
ROUTES: list[tuple[str, re.Pattern, Handler]] = []


def route(method: str, template: str) -> Callable[[Handler], Handler]:
    """Register a handler for `template`, where `{name}` matches one path
    segment and `{name:path}` the rest of the path."""
    pattern = re.sub(r"\{(\w+):path\}", r"(?P<\1>.+)", template)
    pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern)
    compiled = re.compile(f"^{pattern}$")

    def register(fn: Handler) -> Handler:
        ROUTES.append((method, compiled, fn))
        return fn
    return register


def dispatch(engine: Engine, method: str, target: str, body: Any = None) -> Answer:
    parts = urlsplit(target)
    path = parts.path
    known_path = False
    for m, pattern, fn in ROUTES:
        found = pattern.match(path)
        if not found:
            continue
        known_path = True
        if m != method:
            continue
        params = {k: unquote(v) for k, v in found.groupdict().items()}
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        try:
            return fn(engine, Request(params, query, body))
        except BadRequest as e:
            return Answer(400, {"error": "bad_request", "detail": str(e)})
    if known_path:
        return Answer(405, {"error": "method_not_allowed"})
    return Answer(404, {"error": "not_found"})


@route("GET", "/v1/health")
def _health(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.health())


@route("POST", "/v1/pace")
def _pace(engine: Engine, request: Request) -> Answer:
    mode = (request.body or {}).get("mode")
    try:
        engine.set_pace(mode)
    except ValueError as e:
        raise BadRequest(str(e))
    return Answer(200, {"pace": engine.pace})


@route("POST", "/v1/snapshot")
def _snapshot(engine: Engine, request: Request) -> Answer:
    path = engine.store.snapshot()
    return Answer(200, {"snapshot": path.name})


@route("POST", "/v1/compact")
def _compact(engine: Engine, request: Request) -> Answer:
    with lanes.lowered():
        engine.store.flush()
        gen = engine.store.compact((request.body or {}).get("generations"))
    return Answer(200, {"generation": gen})


def _at(request: Request) -> Optional[int]:
    raw = request.query.get("at") if request.query.get("at") is not None else (request.body or {}).get("at")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise BadRequest("at is an LSN")


def _guard(fn):
    """Document, schema and path errors are the caller's: answered 400; a
    condition that did not hold is a conflict: 409."""
    from jarvis.knowledge.documents import DocumentError
    from jarvis.knowledge.items import ConditionFailed
    from jarvis.knowledge.paths import PathError
    from jarvis.knowledge.rights import Denied
    from jarvis.knowledge.store import StoreReadOnly

    def wrapped(engine: Engine, request: Request) -> Answer:
        try:
            return fn(engine, request)
        except ConditionFailed as e:
            return Answer(409, {"error": "conflict", "detail": str(e), "path": e.path,
                                "expected": e.expected, "found": e.found})
        except Denied as e:
            return Answer(403, {"error": "denied", "detail": str(e)})
        except (DocumentError, PathError) as e:
            raise BadRequest(str(e))
        except StoreReadOnly as e:
            return Answer(503, {"error": "read_only", "detail": str(e)})
    return wrapped


@route("POST", "/v1/txn")
@_guard
def _txn(engine: Engine, request: Request) -> Answer:
    body = request.body or {}
    lsn = engine.transaction(body)
    return Answer(200, {"lsn": lsn})


@route("GET", "/v1/nodes/{key:path}")
@_guard
def _node(engine: Engine, request: Request) -> Answer:
    doc = engine.docs.get(request.params["key"], _at(request))
    if doc is None:
        return Answer(404, {"error": "not_found"})
    return Answer(200, doc)


@route("GET", "/v1/edges/{key:path}")
@_guard
def _edges(engine: Engine, request: Request) -> Answer:
    q = request.query
    direction = q.get("dir", "out")
    if direction not in ("out", "in"):
        raise BadRequest("dir is out or in")
    limit = int(q.get("limit") or 100)
    if not 0 < limit <= 1000:
        raise BadRequest("limit is 1 to 1000")
    found = []
    for edge in engine.graph.edges(request.params["key"], direction, q.get("kind") or None,
                                   _at(request), q.get("after") or None):
        if len(found) == limit:
            return Answer(200, {"edges": found, "after": found[-1]["kind"] + "\x00" + (
                found[-1]["to"] if direction == "out" else found[-1]["from"])})
        found.append(edge)
    return Answer(200, {"edges": found, "after": None})


@route("POST", "/v1/walk")
@_guard
def _walk(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.graph.walk(request.body or {}))


@route("POST", "/v1/query")
@_guard
def _query(engine: Engine, request: Request) -> Answer:
    from jarvis.knowledge import query
    return Answer(200, query.run(engine, request.body or {}))


@route("POST", "/v1/search")
@_guard
def _search(engine: Engine, request: Request) -> Answer:
    from jarvis.knowledge import query
    return Answer(200, query.search(engine, request.body or {}))


@route("GET", "/v1/watermarks")
def _watermarks(engine: Engine, request: Request) -> Answer:
    return Answer(200, {"watermarks": engine.watermarks()})


@route("POST", "/v1/link")
@_guard
def _link(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.link())


@route("GET", "/v1/proposals")
def _proposals(engine: Engine, request: Request) -> Answer:
    return Answer(200, {"proposals": engine.linker.proposals, "last": engine.linker.last})


@route("GET", "/v1/cards/{key:path}")
@_guard
def _card(engine: Engine, request: Request) -> Answer:
    card = engine.cards.get(request.params["key"])
    if card is None:
        return Answer(404, {"error": "not_found"})
    return Answer(200, card)


@route("GET", "/v1/hot")
def _hot(engine: Engine, request: Request) -> Answer:
    return Answer(200, {"engine_id": engine.store.engine_id, "cards": engine.cards.hot()})


@route("POST", "/v1/focus")
@_guard
def _focus(engine: Engine, request: Request) -> Answer:
    key = (request.body or {}).get("key")
    if not isinstance(key, str) or not key:
        raise BadRequest("focus takes a key")
    return Answer(200, engine.cards.focus(key))


@route("POST", "/v1/resolve")
@_guard
def _resolve(engine: Engine, request: Request) -> Answer:
    name = (request.body or {}).get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadRequest("resolve takes a name")
    return Answer(200, {"matches": engine.resolve(name)})


@route("POST", "/v1/indexes/choices")
@_guard
def _index_choices(engine: Engine, request: Request) -> Answer:
    body = request.body or {}
    pinned, forbidden = body.get("pinned") or [], body.get("forbidden") or []
    if not all(isinstance(x, list) and all(isinstance(k, str) for k in x) for x in (pinned, forbidden)):
        raise BadRequest("pinned and forbidden are lists of index keys")
    changed = engine.advisor.set_choices(pinned, forbidden)
    return Answer(200, {"changed": changed})


@route("GET", "/v1/indexes")
def _indexes(engine: Engine, request: Request) -> Answer:
    return Answer(200, {**engine.advisor.health(), "candidates": engine.advisor.candidates()})


@route("POST", "/v1/find")
@_guard
def _find(engine: Engine, request: Request) -> Answer:
    body = request.body or {}
    order = body.get("order")
    limit = int(body.get("limit") or 200)
    if not 0 < limit <= 200:
        raise BadRequest("limit is 1 to 200")
    docs = engine.docs.find(kinds_=body.get("kinds"), where=body.get("where") or [],
                            at=_at(request), limit=limit,
                            order=(order[0], order[1]) if order else None)
    return Answer(200, {"documents": docs})


@route("POST", "/v1/forget")
@_guard
def _forget(engine: Engine, request: Request) -> Answer:
    body = request.body or {}
    if body.get("retired_kinds"):
        # What ingest made before records and notes were items (UDS round
        # 3): every document of these kinds, with its edges and vectors.
        retired = body["retired_kinds"]
        if not isinstance(retired, list) or not set(retired) <= {"chunk", "document"}:
            raise BadRequest("retired_kinds are some of chunk, document")
        keys = {d["_key"].encode() for d in engine.docs.find(kinds_=retired, limit=1 << 30)}
        return Answer(200, engine.docs.forget_keys(keys) if keys else {"documents": 0})
    sources = body.get("refs") or []
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        raise BadRequest("refs is a list of source references")
    return Answer(200, engine.docs.forget_sources(sources))


# --- Items at paths: the UDS interface (items.py) ---------------------------
# Every route takes a JSON body, so a path is never re-encoded in a URL.

def _int_or_none(value, what: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest(f"{what} is a whole number")
    return value


@route("POST", "/v1/items/txn")
@_guard
def _items_txn(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.transact(request.body or {}))


@route("POST", "/v1/items/get")
@_guard
def _items_get(engine: Engine, request: Request) -> Answer:
    body = request.body or {}
    at = body.get("at")
    if at is not None and (isinstance(at, bool) or not isinstance(at, (int, float))):
        raise BadRequest("at is a time in seconds")
    item = engine.items.get(body.get("path"), version=_int_or_none(body.get("version"), "version"),
                            at=at, streams=body.get("streams"), person=engine.items._reader(body))
    if item is None:
        return Answer(404, {"error": "not_found"})
    return Answer(200, item)


@route("POST", "/v1/items/history")
@_guard
def _items_history(engine: Engine, request: Request) -> Answer:
    body = request.body or {}
    return Answer(200, engine.items.history(body.get("path"), limit=_int_or_none(body.get("limit"), "limit") or 100,
                                            before=_int_or_none(body.get("before"), "before"),
                                            person=engine.items._reader(body), fields=body.get("fields")))


@route("POST", "/v1/items/diff")
@_guard
def _items_diff(engine: Engine, request: Request) -> Answer:
    body = request.body or {}
    a = _int_or_none(body.get("from"), "from")
    if a is None:
        raise BadRequest("diff takes the version it is from")
    return Answer(200, engine.items.diff(body.get("path"), a, _int_or_none(body.get("to"), "to"),
                                         person=engine.items._reader(body)))


@route("POST", "/v1/items/list")
@_guard
def _items_list(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.list(request.body or {}))


@route("POST", "/v1/items/count")
@_guard
def _items_count(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.count(request.body or {}))


@route("POST", "/v1/items/changes")
@_guard
def _items_changes(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.changes(request.body or {}))


@route("POST", "/v1/items/forget")
@_guard
def _items_forget(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.forget(request.body or {}))


@route("POST", "/v1/items/links")
@_guard
def _items_links(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.links(request.body or {}))


@route("POST", "/v1/items/search")
@_guard
def _items_search(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.search(request.body or {}))


@route("POST", "/v1/items/rights")
@_guard
def _items_rights(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.set_rights(request.body or {}))


@route("POST", "/v1/items/propose")
@_guard
def _items_propose(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.propose_link(request.body or {}))


@route("POST", "/v1/items/proposals")
@_guard
def _items_proposals(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.proposals(request.body or {}))


@route("POST", "/v1/items/proposals/answer")
@_guard
def _items_answer(engine: Engine, request: Request) -> Answer:
    return Answer(200, engine.items.answer_proposal(request.body or {}))


class _HTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "jarvis-knowledge"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s " + fmt, self.address_string(), *args)

    def _send(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method: str) -> None:
        server: EngineHTTPServer = self.server  # type: ignore[assignment]
        refusal = auth.check(self.headers, server.server_address[1], server.token)
        if refusal is not None:
            self.close_connection = True
            self._send(refusal.status, {"error": refusal.code})
            return
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length > BODY_MAX_BYTES:
            self.close_connection = True
            self._send(413, {"error": "too_large"})
            return
        if length:
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                self._send(400, {"error": "bad_json"})
                return
        try:
            answer = dispatch(server.engine, method, self.path, body)
        except Exception:
            log.exception("knowledge route %s %s failed", method, self.path)
            answer = Answer(500, {"error": "internal"})
        self._send(answer.status, answer.payload)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")


class EngineHTTPServer(LoopbackThreadingHTTPServer):
    """The engine's loopback server: bound without a reverse name lookup
    (loopback_http), which on the macOS CI runner kept the engine from
    listening for about 35 seconds."""
    allow_reuse_address = False

    def __init__(self, engine: Engine, token: str, host: str = "127.0.0.1", port: int = 0) -> None:
        self.engine = engine
        self.token = token
        super().__init__((host, port), _HTTPHandler)


def _watch_stdin(on_close: Callable[[], None]) -> threading.Thread:
    """End the engine when the pipe from the server that started it closes."""
    def watch() -> None:
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except (OSError, ValueError):
            pass
        log.info("stdin closed: the server that started this engine is gone")
        on_close()
    thread = threading.Thread(target=watch, name="knowledge-stdin", daemon=True)
    thread.start()
    return thread


MAINTAIN_EVERY_SEC = 5.0


def _maintenance(engine: Engine, stopping: threading.Event) -> threading.Thread:
    def loop() -> None:
        lanes.background()
        while not stopping.wait(MAINTAIN_EVERY_SEC):
            try:
                engine.maintain()
            except Exception:
                log.exception("knowledge maintenance failed")
    thread = threading.Thread(target=loop, name="knowledge-maintain", daemon=True)
    thread.start()
    return thread


def run(*, watch_stdin: bool = True) -> int:
    from jarvis import data_paths
    began = time.monotonic()
    log.info("knowledge engine starting (pid %s, Python %s)", os.getpid(), sys.version.split()[0])
    try:
        engine = open_engine()
    except EngineLocked as e:
        log.error("%s", e)
        print(str(e), file=sys.stderr)
        return 3
    log.info("knowledge engine opened %s in %.2f s", engine.directory, time.monotonic() - began)
    token = data_paths.ensure_knowledge_token()
    server = EngineHTTPServer(engine, token)
    host, port = server.server_address[:2]
    stopping = threading.Event()

    def stop() -> None:
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, lambda *_: stop())
    signal.signal(signal.SIGINT, lambda *_: stop())
    if watch_stdin:
        _watch_stdin(stop)
    _maintenance(engine, stopping)
    engine_address.record(engine_address.EngineAddress(
        scheme="http", host=host, port=port, pid=os.getpid(), boot_id=engine.boot_id))
    log.info("knowledge engine listening on %s:%s (pid %s), %.2f s after it started", host, port,
             os.getpid(), time.monotonic() - began)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        engine_address.forget(engine.boot_id)
        engine.close()
    return 0
