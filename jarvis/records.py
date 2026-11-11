"""JARVIS's records in the document engine: the one way every store reads
and writes them (round 3, `records-as-documents`, of
docs/planning/knowledge-engine/plan-uds.md).

SQLite is gone. Each store keeps its public functions; inside, it reads
and writes documents at paths through this module, over the UDS interface
(`knowledge/uds.py`). One record is one document holding its current state
and its small children; the changes that were event rows are the
document's versions, each tagged with who wrote it, the kind of change and
its summary, with the event's own fields (its original time among them,
since the engine stamps write times itself) in the version's data.

Which engine: the one `bind` was given (the server binds the engine it
started and waits for); else, when JARVIS starts its engine itself
(`knowledge_client.autostart`), the engine at its recorded address; else
(tests, CI, an isolated JARVIS with autostart off) an engine opened in this
process over `data_paths.knowledge_dir()`, opened when first used and kept
per directory until `close_all`. Its lock is the engine's own: if another
process holds it, the recorded address is used instead.

Writing is `atomic(fn)`: `fn(unit)` reads through the unit and stages
writes; at the end every staged write and every document it read goes to
the engine as one transaction with conditions (each document read must
still be at the version read, each read as missing still missing), so a
read-modify-write of several documents lands whole or not at all. When a
condition fails nothing was written and `fn` runs again from the start
(at most `RETRIES` times), so `fn` must do nothing but read and stage
through the unit; what is announced after the write goes to `unit.after`,
which runs once the transaction has landed (write before notify).
"""
from __future__ import annotations

import copy as _copy
import logging
import random
import threading
import time
import urllib.parse
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, TypeVar

from jarvis import data_paths
from jarvis.knowledge.uds import KEEP, Caller, Conflict, EngineUDS, Stream, Transaction, Version
from jarvis.knowledge_client import EngineUnavailable

log = logging.getLogger("jarvis.records")

JARVIS = Caller(person="tim", agent="jarvis")
"""JARVIS writing its own records (Tim's records, written for him)."""
TIM = Caller(person="tim")
"""Tim's own act (an answer, an approval)."""

RETRIES = 50
DEADLINE_SEC = 10.0
DATA = frozenset({Stream.ENTRY, Stream.DATA})
SCAN_PAGE = 1000

T = TypeVar("T")


class RecordsUnavailable(EngineUnavailable):
    """The engine holding JARVIS's records cannot be reached."""


class TooContended(RuntimeError):
    """A unit's conditions failed `RETRIES` times in a row."""


def by(principal: Optional[str], *, model: Optional[str] = None) -> Caller:
    """Tim's records written by `principal` (an agent's `agent:<session>`,
    an access key's `key:<label>`, `jarvis`, ...): the person is always
    Tim, whose records these are; the agent says who acted."""
    if not principal or principal in ("tim", "user", "his"):
        return TIM if model is None else Caller("tim", model=model)
    return Caller(person="tim", agent=str(principal)[:200], model=model)


# -- which engine -------------------------------------------------------------

_lock = threading.RLock()
_bound: Optional[EngineUDS] = None
_opened: dict[Path, tuple[Any, EngineUDS]] = {}


def bind(uds: Optional[EngineUDS]) -> None:
    """The engine every record goes to from now on (None: unbind)."""
    global _bound
    with _lock:
        _bound = uds


def _in_process(directory: Path) -> EngineUDS:
    from jarvis.knowledge.engine import Engine
    from jarvis.knowledge_client import InProcessTransport
    found = _opened.get(directory)
    if found is not None:
        return found[1]
    engine = Engine.open(directory)
    engine.compact_in_child = False
    engine.store.compact_in_child = False
    uds = EngineUDS(InProcessTransport(engine), deadline=DEADLINE_SEC)
    _opened[directory] = (engine, uds)
    return uds


def _recorded() -> EngineUDS:
    from jarvis.knowledge import address
    from jarvis.knowledge_client import HTTPTransport
    if address.running() is None:
        raise RecordsUnavailable("no knowledge engine is running to hold JARVIS's records")
    return EngineUDS(HTTPTransport(lambda: getattr(address.running(), "pid", None)),
                     deadline=DEADLINE_SEC)


def uds() -> EngineUDS:
    """The UDS interface to the engine holding JARVIS's records."""
    from jarvis import knowledge_client
    with _lock:
        if _bound is not None:
            return _bound
        if knowledge_client.autostart():
            return _recorded()
        directory = data_paths.knowledge_dir().resolve()
        from jarvis.knowledge.engine import EngineLocked
        try:
            return _in_process(directory)
        except EngineLocked:
            return _recorded()


def engine_key() -> str:
    """Which engine records go to now, as a key for what a process holds in
    memory about them, so what was read from one engine is never taken for
    another's (a test's engine, the server's bound one)."""
    from jarvis import knowledge_client
    with _lock:
        if _bound is not None:
            return f"bound:{id(_bound)}"
    if knowledge_client.autostart():
        return "recorded"
    return str(data_paths.knowledge_dir().resolve())


def in_process() -> bool:
    """Whether records are read in this process (no other process to wait
    on): no engine bound and none started by JARVIS."""
    from jarvis import knowledge_client
    with _lock:
        return _bound is None and not knowledge_client.autostart()


def opened_here() -> list[Any]:
    """The engines this process opened for its records (tests)."""
    with _lock:
        return [e for e, _ in _opened.values()]


def close_all() -> None:
    """Close every engine this process opened for its records."""
    with _lock:
        found = list(_opened.values())
        _opened.clear()
    for engine, _ in found:
        try:
            engine.close()
        except Exception:  # pragma: no cover - closing is best effort
            log.exception("records: closing an engine")


# -- paths --------------------------------------------------------------------

_KEEP_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-:@+=,~")
SEGMENT_KEEP_BYTES = 200


def segment(value: Any) -> str:
    """Any id as one path segment, one spelling each: letters, digits and
    `_-:@+=,~` kept, every other character percent-encoded in UTF-8 (so a
    `/` or `.` in an id never splits a path or names a type); one longer
    than `SEGMENT_KEEP_BYTES` is its first part and a hash."""
    text = str(value)
    out = "".join(c if c in _KEEP_CHARS else urllib.parse.quote(c, safe="") for c in text)
    if not out:
        out = "%00"
    if len(out.encode("utf-8")) > SEGMENT_KEEP_BYTES:
        out = out[:120] + "~" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]
    return out


def unsegment(seg: str) -> str:
    """The id a `segment` was made from (not for hashed long ones)."""
    return "" if seg == "%00" else urllib.parse.unquote(seg)


# -- reading ------------------------------------------------------------------

@dataclass
class Rec:
    """One record as read: its path, version and data (a private copy)."""
    path: str
    version: int
    data: dict
    at: Optional[float] = None
    content: Optional[dict] = None

    @classmethod
    def of(cls, item) -> "Rec":
        return cls(item.path, item.version, _copy.deepcopy(dict(item.data or {})),
                   (item.entry or {}).get("at"), None if item.content is None else dict(item.content))


def get(path: str, *, version: Optional[int] = None, caller: Caller = JARVIS,
        streams=DATA) -> Optional[Rec]:
    item = uds().get(caller, path, streams, version=version)
    return None if item is None else Rec.of(item)


def scan(under: str, *, parent: Optional[str] = None, types: Optional[list[str]] = None,
         where: Optional[list] = None, order: Optional[tuple[str, str]] = None,
         limit: Optional[int] = None, caller: Caller = JARVIS, streams=DATA) -> Iterator[Rec]:
    """Every record under `under` (and with `parent`, of `types`, matching
    `where`: [[field, op, value], ...]) in path order, or by `order`
    (field, "asc"|"desc"); at most `limit`."""
    page_size = SCAN_PAGE if limit is None else max(1, min(limit, SCAN_PAGE))
    seen = 0
    for item in uds().scan(caller, under, parent=parent, types=types, where=where, order=order,
                           limit=page_size, streams=streams):
        yield Rec.of(item)
        seen += 1
        if limit is not None and seen >= limit:
            return


def all_of(under: str, **kw) -> list[Rec]:
    return list(scan(under, **kw))


def count(under: str, *, where: Optional[list] = None, types: Optional[list[str]] = None,
          parent: Optional[str] = None, group_by: Optional[str] = None, caller: Caller = JARVIS):
    return uds().count(caller, under, where=where, types=types, parent=parent, group_by=group_by)


def history(path: str, *, fields: Optional[list[str]] = None, limit: int = 10000,
            caller: Caller = JARVIS) -> list[Version]:
    """The record's versions, oldest first, each with `fields` of its data
    as they stood then."""
    return list(reversed(uds().history(caller, path, limit=limit, fields=fields)))


def changes(since: int = 0, *, under: Optional[str] = None, limit: int = 1000,
            caller: Caller = JARVIS) -> dict:
    return uds().changes(caller, since, limit=limit, under=under)


# -- writing ------------------------------------------------------------------

_MISSING = object()


@dataclass
class _Staged:
    op: str                         # put or delete
    data: Optional[dict] = None
    content: Any = KEEP
    change: Optional[str] = None
    summary: Optional[str] = None


class Unit:
    """One atomic write being gathered: reads remembered with their
    versions, writes staged. See the module's words."""

    def __init__(self, caller: Caller, change: Optional[str], summary: Optional[str]) -> None:
        self.by = caller
        self.change = change
        self.summary = summary
        self.full_sync = False
        self._read: dict[str, int] = {}          # path -> version read (0: missing)
        self._cache: dict[str, Optional[Rec]] = {}
        self._staged: dict[str, _Staged] = {}
        self._after: list[Callable[[], None]] = []
        self.lsn: Optional[int] = None

    # reading, remembered
    def get(self, path: str) -> Optional[Rec]:
        staged = self._staged.get(path)
        if staged is not None:
            if staged.op == "delete":
                return None
            return Rec(path, self._read.get(path, 0), _copy.deepcopy(staged.data))
        if path not in self._cache:
            rec = get(path, caller=self.by)
            self._cache[path] = rec
            self._read.setdefault(path, 0 if rec is None else rec.version)
        rec = self._cache[path]
        return None if rec is None else Rec(rec.path, rec.version, _copy.deepcopy(rec.data), rec.at)

    def scan(self, under: str, *, watch: bool = True, **kw) -> list[Rec]:
        """Records under `under` (as `scan`); with `watch`, each one found
        is a condition of the write, like a `get`. Staged writes are seen."""
        out: list[Rec] = []
        for rec in scan(under, caller=self.by, **kw):
            if rec.path not in self._cache:
                self._cache[rec.path] = rec
                if watch:
                    self._read.setdefault(rec.path, rec.version)
            staged = self._staged.get(rec.path)
            if staged is not None:
                if staged.op == "delete":
                    continue
                out.append(Rec(rec.path, rec.version, _copy.deepcopy(staged.data), rec.at))
            else:
                out.append(Rec(rec.path, rec.version, _copy.deepcopy(rec.data), rec.at))
        return out

    def watch(self, path: str) -> None:
        """Make the record's current version a condition without using it."""
        self.get(path)

    # staging
    def put(self, path: str, data: dict, *, content: Any = KEEP, change: Optional[str] = None,
            summary: Optional[str] = None) -> None:
        self._staged[path] = _Staged("put", _copy.deepcopy(dict(data)), content, change, summary)

    def delete(self, path: str, *, change: Optional[str] = None, summary: Optional[str] = None) -> None:
        self._staged[path] = _Staged("delete", None, KEEP, change, summary)

    def after(self, callback: Callable[[], None]) -> None:
        """Run once the write has landed (notifications)."""
        self._after.append(callback)

    @property
    def writes(self) -> bool:
        return bool(self._staged)

    def _transaction(self) -> Transaction:
        tx = Transaction()
        for path, s in self._staged.items():
            expect = self._read.get(path)
            if s.op == "delete":
                if expect == 0:
                    continue              # read as missing, still to be missing: a check below
                tx.delete(path, expect_version=expect)
            else:
                tx.put(path, s.data, content=s.content, expect_version=expect)
            if s.change is not None or s.summary is not None:
                tx.tag(s.change, s.summary)
        for path, version in self._read.items():
            s = self._staged.get(path)
            if s is None or (s.op == "delete" and version == 0):
                if version == 0:
                    tx.check(path, absent=True)
                else:
                    tx.check(path, expect_version=version)
        return tx


def atomic(fn: Callable[[Unit], T], *, by: Caller = JARVIS, change: Optional[str] = None,
           summary: Optional[str] = None, full_sync: bool = False) -> T:
    """`fn(unit)` and its staged writes as one conditioned transaction,
    run again from the start when a condition failed; returns what `fn`
    returned. Nothing is sent when `fn` staged no write."""
    for attempt in range(RETRIES):
        unit = Unit(by, change, summary)
        unit.full_sync = full_sync
        result = fn(unit)
        if not unit.writes:
            for cb in unit._after:
                cb()
            return result
        tx = unit._transaction()
        if not any(w["op"] in ("put", "delete") for w in tx.writes):
            for cb in unit._after:
                cb()
            return result
        try:
            answer = uds().transaction(unit.by, tx, change=unit.change, summary=unit.summary,
                                       full_sync=unit.full_sync)
        except Conflict:
            time.sleep(min(0.05, 0.001 * (attempt + 1)) * random.random())
            continue
        unit.lsn = (answer or {}).get("lsn")
        for cb in unit._after:
            cb()
        return result
    raise TooContended(f"{getattr(fn, '__name__', 'a write')}: conditions failed {RETRIES} times")


def write(path: str, data: dict, *, by: Caller = JARVIS, change: Optional[str] = None,
          summary: Optional[str] = None, content: Any = KEEP) -> None:
    """One blind put (no condition): for a record only this writer writes."""
    tx = Transaction().put(path, data, content=content)
    uds().transaction(by, tx, change=change, summary=summary)


def update(path: str, fn: Callable[[Optional[dict]], Optional[dict]], *, by: Caller = JARVIS,
           change: Optional[str] = None, summary: Optional[str] = None,
           full_sync: bool = False) -> Optional[dict]:
    """Read-modify-write of one record: `fn(data or None)` gives the new
    data (None: leave it as it is); returns what was written."""
    def run(unit: Unit) -> Optional[dict]:
        rec = unit.get(path)
        new = fn(None if rec is None else rec.data)
        if new is not None:
            unit.put(path, new)
        return new
    return atomic(run, by=by, change=change, summary=summary, full_sync=full_sync)


def delete(path: str, *, by: Caller = JARVIS, change: Optional[str] = None,
           summary: Optional[str] = None) -> None:
    tx = Transaction().delete(path)
    uds().transaction(by, tx, change=change, summary=summary)


def write_many(writes: list[tuple[str, dict]], *, by: Caller = JARVIS, change: Optional[str] = None,
               summary: Optional[str] = None, batch: int = 500) -> int:
    """Blind puts in transactions of at most `batch` (a bulk load)."""
    n = 0
    for i in range(0, len(writes), batch):
        tx = Transaction()
        for path, data in writes[i:i + batch]:
            tx.put(path, data)
        uds().transaction(by, tx, change=change, summary=summary)
        n += len(writes[i:i + batch])
    return n
