"""An agent's one record, and the other records of agent keys, as
documents (round 3, `records-as-documents`, of
docs/planning/knowledge-engine/plan-uds.md): the shapes, the paths, the
in-memory copy every hot read is answered from, the one way they are
written, and their move from and back to SQLite.

`agent` at `/agents/<session id>.agent`: one session's agent and
everything about it in one document. `agent` is its row on the agents list
(None while the session is not on it: a key may be submitted before its
session is seen, and an agent dropped from the list keeps its keys and
what was sent to it); `keys` are its keys (principal `agent:<session>`),
`rights` its grants, `author_proofs` the packages it vouched for as an
author (`<engine>:<session>`), `steers` the keystrokes, Terminal commands
and refused attempts JARVIS made at it. A work-item key has no session: it
is a document of its own, `workkey` at `/keys/work/<fingerprint>.workkey`,
with its key, its rights and its history. (Any other principal, never
written by JARVIS but kept by the move, is `/keys/other/<principal>.keyholder`.)
The key events are each document's versions: every write sets `events` to
the event(s) that version records (`[]` for none), tagged with the event's
kind and who acted, read back with `records.history(path, fields=["events"])`.

`hold` at `/keys/holds/<subject>.hold`: what an unknown key (or an unproven
session) tried to send, `held`, and the error package about it, `error`.
`settings` at `/settings/agent-keys.settings` (`settings`: the cut-over
guard's named facts) and `/settings/legacy-admissions.settings`
(`admissions`: access-key use per day and label).

Rows moved from SQLite keep their `rowid` (and `id`) inline, so the way
back gives the same rows; a row written since has none and is numbered
after the largest on the way back.

Hot reads (the web gate's keys and rights on every signed request, the
agents' names every second) are answered from `CACHE`: every document of
these types, read once from the engine this process uses and refreshed
from the engine after every write made through `change`, so a read never
waits on the engine. Only this process writes them (the server; the
agents' own commands go through its routes).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Callable, Iterable, Iterator, Optional, TypeVar

from jarvis import records

T = TypeVar("T")

AGENTS = "/agents"
WORK_KEYS = "/keys/work"
OTHER_KEYS = "/keys/other"
HOLDS = "/keys/holds"
SETTINGS = "/settings"
KEY_SETTINGS = SETTINGS + "/agent-keys.settings"
LEGACY_SETTINGS = SETTINGS + "/legacy-admissions.settings"
AGENT_PREFIX, WORK_PREFIX = "agent:", "workkey:"

# Each table's columns (the document's field names), and which hold REAL.
AGENT_FIELDS = ("name", "active", "state", "custom_name", "last_message_at", "ended_at")
KEY_FIELDS = ("fingerprint", "public_key", "principal", "session_id", "provider", "state",
              "submitted_at", "proven_at", "proven_by", "onboarded_at", "onboarded_by",
              "revoked_at", "revoked_by", "revoked_why", "label", "work_id", "issued_by",
              "expires_at")
RIGHT_FIELDS = ("principal", "right", "scope", "granted_at", "granted_by")
PROOF_FIELDS = ("author", "package_sha", "bound_at")
STEER_FIELDS = ("id", "session_id", "voice_name", "project", "prompt", "outcome", "created_at")
EVENT_FIELDS = ("id", "fingerprint", "kind", "at", "detail")
HELD_FIELDS = ("id", "subject", "fingerprint", "session_id", "kind", "what", "path", "detail",
               "work_id", "held_at", "state", "settled_at")
ERROR_FIELDS = ("subject", "package_id", "inbox_id", "made_at")
SETTING_FIELDS = ("name", "value", "at")
LEGACY_FIELDS = ("day", "label", "count")
KEY_DEFAULTS = {"session_id": "", "provider": "", "proven_at": None, "proven_by": "",
                "onboarded_at": None, "onboarded_by": "", "revoked_at": None, "revoked_by": "",
                "revoked_why": "", "label": "", "work_id": "", "issued_by": "", "expires_at": None}
AGENT_DEFAULTS = {"active": 0, "state": "", "custom_name": None, "last_message_at": 0.0,
                  "ended_at": None}
HELD_DEFAULTS = {"fingerprint": "", "session_id": "", "path": "", "detail": "{}", "work_id": "",
                 "settled_at": None}


def segment(value: Any) -> str:
    return records.segment(value)


def agent_path(session_id: str) -> str:
    return f"{AGENTS}/{segment(session_id)}.agent"


def holder_path(principal: str) -> str:
    """The document a principal's keys and rights live in."""
    principal = str(principal)
    if principal.startswith(AGENT_PREFIX):
        return agent_path(principal[len(AGENT_PREFIX):])
    if principal.startswith(WORK_PREFIX):
        return f"{WORK_KEYS}/{segment(principal[len(WORK_PREFIX):])}.workkey"
    return f"{OTHER_KEYS}/{segment(principal)}.keyholder"


def author_session(author: str) -> str:
    """The session an author (`<engine>:<session>`) is."""
    author = str(author)
    return author.split(":", 1)[1] if ":" in author else author


def hold_path(subject: str) -> str:
    return f"{HOLDS}/{segment(subject)}.hold"


def new_agent(session_id: str) -> dict:
    return {"session_id": session_id, "agent": None, "keys": [], "rights": [],
            "author_proofs": [], "steers": [], "events": []}


def new_holder(principal: Optional[str]) -> dict:
    return {"principal": principal, "keys": [], "rights": [], "events": []}


def new_hold(subject: str) -> dict:
    return {"subject": subject, "held": [], "error": None, "events": []}


def new_settings(path: str) -> dict:
    if path == KEY_SETTINGS:
        return {"area": "agent-keys", "settings": [], "events": []}
    return {"area": "legacy-admissions", "admissions": [], "events": []}


def orphan_path(fingerprint: Any) -> str:
    """Where the events of a key dropped before the move live when no event
    names its session (the move only)."""
    return f"{OTHER_KEYS}/{segment('fingerprint:' + str(fingerprint))}.keyholder"


def event_summary(ev: dict) -> str:
    """A key event's version summary: the key and what happened to it."""
    return f"key {str(ev['fingerprint'])[:16]} {ev['kind']}"


def is_holder(path: str) -> bool:
    return path.endswith((".agent", ".workkey", ".keyholder"))


def event(fingerprint: str, kind: str, at: float, **detail) -> dict:
    return {"fingerprint": fingerprint, "kind": kind, "at": float(at),
            "detail": json.dumps(detail, sort_keys=True)}


def caller_for(by: Optional[str]) -> records.Caller:
    """Who acted, as a version's writer: his act on the page or by voice is
    Tim's; an agent or key principal is that agent; else JARVIS."""
    by = str(by or "")
    if by in ("page", "voice"):
        return records.TIM
    if not by or by in ("jarvis",):
        return records.JARVIS
    return records.by(by)


def event_caller(ev: dict) -> records.Caller:
    try:
        detail = json.loads(ev.get("detail") or "{}")
    except (TypeError, ValueError):
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    kind = ev.get("kind")
    if kind == "submitted" and detail.get("session"):
        return records.by(AGENT_PREFIX + str(detail["session"]))
    if kind in ("proven", "dropped"):
        return records.JARVIS
    return caller_for(detail.get("by"))


# -- the in-memory copy ---------------------------------------------------------

class _Cache:
    """Every document of these types, as last read from the engine in use."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._engine: Any = None
        self._docs: dict[str, records.Rec] = {}
        self._by_fp: Optional[dict[str, str]] = None

    def _ensure(self) -> None:
        engine = records.uds()
        if engine is self._engine:
            return
        docs: dict[str, records.Rec] = {}
        for under, parent, kind in ((AGENTS, AGENTS, "agent"), (WORK_KEYS, WORK_KEYS, "workkey"),
                                    (OTHER_KEYS, OTHER_KEYS, "keyholder"), (HOLDS, HOLDS, "hold")):
            for rec in records.scan(under, parent=parent, types=[kind]):
                docs[rec.path] = rec
        for path in (KEY_SETTINGS, LEGACY_SETTINGS):
            rec = records.get(path)
            if rec is not None:
                docs[path] = rec
        self._docs = docs
        self._by_fp = None
        self._engine = engine

    def refresh(self, path: str) -> None:
        """The document as the engine holds it now (after a write)."""
        with self._lock:
            if self._engine is None or self._engine is not records.uds():
                return
            rec = records.get(path)
            held = self._docs.get(path)
            if rec is None:
                self._docs.pop(path, None)
            elif held is None or rec.version >= held.version:
                self._docs[path] = rec
            self._by_fp = None

    def forget(self) -> None:
        with self._lock:
            self._engine = None
            self._docs = {}
            self._by_fp = None

    def get(self, path: str) -> Optional[dict]:
        with self._lock:
            self._ensure()
            rec = self._docs.get(path)
            return None if rec is None else rec.data

    def docs(self, suffix: Optional[str] = None) -> list[tuple[str, dict]]:
        """(path, data) of every document, or those whose path ends with
        `suffix`; the data is the cache's own: read it, never change it."""
        with self._lock:
            self._ensure()
            return [(p, r.data) for p, r in self._docs.items()
                    if suffix is None or p.endswith(suffix)]

    def key_path(self, fingerprint: str) -> Optional[str]:
        with self._lock:
            self._ensure()
            if self._by_fp is None:
                self._by_fp = {}
                for p, r in self._docs.items():
                    if is_holder(p):
                        for k in r.data.get("keys") or ():
                            self._by_fp.setdefault(k.get("fingerprint"), p)
            return self._by_fp.get(fingerprint)

    def key(self, fingerprint: str) -> Optional[dict]:
        path = self.key_path(fingerprint)
        if path is None:
            return None
        with self._lock:
            data = self._docs[path].data if path in self._docs else {}
            return next((k for k in data.get("keys") or () if k.get("fingerprint") == fingerprint),
                        None)

    def keys(self) -> list[dict]:
        return [k for p, d in self.docs() if is_holder(p) for k in d.get("keys") or ()]


CACHE = _Cache()


# -- writing ----------------------------------------------------------------------

class Writer:
    """One atomic change being gathered over these documents: each document
    read once through the unit (a condition of the write), changed in place,
    and put once at the end with this version's events."""

    def __init__(self, unit: records.Unit) -> None:
        self.unit = unit
        self._docs: dict[str, dict] = {}
        self._tags: dict[str, tuple[Optional[str], Optional[str]]] = {}
        self._deleted: set[str] = set()

    def doc(self, path: str, new: Optional[Callable[[], dict]] = None) -> Optional[dict]:
        if path in self._docs:
            return self._docs[path]
        rec = self.unit.get(path)
        data = rec.data if rec is not None else (new() if new is not None else None)
        if data is None:
            return None
        data["events"] = []
        self._docs[path] = data
        return data

    def agent(self, session_id: str, create: bool = True) -> Optional[dict]:
        return self.doc(agent_path(session_id), (lambda: new_agent(session_id)) if create else None)

    def holder(self, principal: str) -> dict:
        path = holder_path(principal)
        if principal.startswith(AGENT_PREFIX):
            return self.agent(principal[len(AGENT_PREFIX):])
        return self.doc(path, lambda: new_holder(principal))

    def scan(self, under: str, **kw) -> list[records.Rec]:
        return self.unit.scan(under, **kw)

    def changed(self, path: str, change: Optional[str], summary: Optional[str] = None) -> None:
        """The document at `path` (read through `doc`) is to be written,
        tagged with this kind of change."""
        self._deleted.discard(path)
        self._tags[path] = (change, summary)

    def event(self, path: str, ev: dict, summary: Optional[str] = None) -> None:
        self._docs[path]["events"].append(ev)
        self.changed(path, ev["kind"], summary)

    def delete(self, path: str, change: Optional[str] = None) -> None:
        self._tags.pop(path, None)
        self._deleted.add(path)
        self.unit.delete(path, change=change)
        self.unit.after(lambda: CACHE.refresh(path))

    def flush(self) -> None:
        for path, (change, summary) in self._tags.items():
            self.unit.put(path, self._docs[path], change=change, summary=summary)
            self.unit.after(lambda p=path: CACHE.refresh(p))


def change(fn: Callable[[Writer], T], *, by: records.Caller = records.JARVIS,
           what: Optional[str] = None, full_sync: bool = False) -> T:
    """`fn(writer)` as one atomic write over these documents (run again from
    the start when a document it read changed meanwhile); the cache is
    refreshed once it has landed."""
    def run(unit: records.Unit) -> T:
        w = Writer(unit)
        result = fn(w)
        w.flush()
        return result
    return records.atomic(run, by=by, change=what, full_sync=full_sync)


def has_events(path: str) -> bool:
    """Whether any version of the document recorded an event."""
    return any(v.data.get("events") for v in records.history(path, fields=["events"]))


def events_of(path: str) -> list[dict]:
    """Every event the document's versions recorded, oldest first."""
    out: list[dict] = []
    for v in records.history(path, fields=["events"]):
        out.extend(v.data.get("events") or ())
    return out


# -- the move to and from SQLite ---------------------------------------------------

def _move():
    from jarvis import knowledge_move
    return knowledge_move


def _table_rows(conn: sqlite3.Connection, table: str) -> list:
    move = _move()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone() is None:
        return []
    return list(move.read_rows(conn, move.table_info(conn, table)))


def _dict(row, fields: Iterable[str], skip: Iterable[str] = ()) -> dict:
    """A row's cells as a document's fields (SQLite's odd values as the move
    kit's cells), with its rowid when it has one apart from its columns."""
    move = _move()
    skip = set(skip)
    out = {f: move.cell(row.cells.get(f)) for f in fields if f not in skip and f in row.cells}
    if row.rowid is not None:
        out["rowid"] = row.rowid
    return out


def _row(raw: dict, fields: Iterable[str], extra: Optional[dict] = None):
    move = _move()
    cells = {f: move.uncell(raw.get(f)) for f in fields}
    if extra:
        cells.update(extra)
    return move.Row(cells, raw.get("rowid"))


def _numbered(rows: list, key: str = "") -> list:
    """Rows written since the move have no rowid (or no `id`, `key`): each
    gets the next after the largest, in the order given."""
    move = _move()
    if key:
        top = max((r.cells[key] for r in rows if r.cells.get(key) is not None), default=0)
        out = []
        for r in rows:
            if r.cells.get(key) is None:
                top += 1
                r = move.Row({**r.cells, key: top}, r.rowid)
            out.append(r)
        return out
    top = max((r.rowid for r in rows if r.rowid is not None), default=0)
    out = []
    for r in rows:
        if r.rowid is None:
            top += 1
            r = move.Row(r.cells, top)
        out.append(r)
    return out


class AgentMapping:
    """agents, steers, agent_keys, agent_rights, agent_key_events (history)
    and agent_author_proofs as `agent` (and `workkey`) documents."""

    name = "agent"
    tables = ("agents", "steers", "agent_keys", "agent_rights", "agent_key_events",
              "agent_author_proofs")
    under = (AGENTS, WORK_KEYS, OTHER_KEYS)
    history = True

    def documents(self, conn: sqlite3.Connection) -> Iterator:
        move = _move()
        docs: dict[str, dict] = {}

        def agent(sid: Any) -> dict:
            path = agent_path(sid)
            if path not in docs:
                docs[path] = new_agent(move.cell(sid))
            return docs[path]

        def holder(principal: Any) -> dict:
            if isinstance(principal, str) and principal.startswith(AGENT_PREFIX):
                return agent(principal[len(AGENT_PREFIX):])
            path = holder_path(principal)
            if path not in docs:
                docs[path] = new_holder(move.cell(principal))
            return docs[path]

        for r in _table_rows(conn, "agents"):
            agent(r.cells["session_id"])["agent"] = _dict(r, AGENT_FIELDS)
        for r in _table_rows(conn, "agent_keys"):
            holder(r.cells["principal"])["keys"].append(_dict(r, KEY_FIELDS))
        for r in _table_rows(conn, "agent_rights"):
            holder(r.cells["principal"])["rights"].append(_dict(r, RIGHT_FIELDS))
        for r in _table_rows(conn, "agent_author_proofs"):
            agent(author_session(r.cells["author"]))["author_proofs"].append(_dict(r, PROOF_FIELDS))
        for r in _table_rows(conn, "steers"):
            agent(r.cells["session_id"])["steers"].append(_dict(r, STEER_FIELDS))
        owner = {}
        for path, d in docs.items():
            for k in d["keys"]:
                owner.setdefault(k["fingerprint"], path)
        history: dict[str, list] = {}
        sessions: dict[Any, Any] = {}
        events = _table_rows(conn, "agent_key_events")
        for r in events:
            try:
                session = json.loads(r.cells.get("detail") or "{}").get("session")
            except (TypeError, ValueError, AttributeError):
                session = None
            if session:
                sessions.setdefault(r.cells["fingerprint"], session)
        for r in events:
            fp = r.cells["fingerprint"]
            path = owner.get(fp)
            if path is None:
                # A key dropped before the move: its events stay with its session's
                # agent, else with a document of their own.
                if fp in sessions:
                    agent(sessions[fp])
                    path = agent_path(sessions[fp])
                else:
                    path = orphan_path(fp)
                    docs.setdefault(path, new_holder(None))
                owner[fp] = path
            history.setdefault(path, []).append(_dict(r, EVENT_FIELDS))
        for path, d in docs.items():
            ident = ({"session_id": d["session_id"]} if path.endswith(".agent")
                     else {"principal": d["principal"]})
            revisions = tuple(
                move.Revision({**ident, "events": [ev]}, by=event_caller(ev), change=str(ev["kind"]),
                              summary=event_summary(ev))
                for ev in history.get(path, ()))
            yield move.Doc(path, d, history=revisions)

    def rows(self, docs) -> dict:
        out: dict[str, list] = {t: [] for t in self.tables}
        events: list = []
        for doc in docs:
            if not is_holder(doc.path):
                continue
            d = doc.data
            if doc.path.endswith(".agent") and d.get("agent") is not None:
                out["agents"].append(_row({**AGENT_DEFAULTS, **d["agent"]}, AGENT_FIELDS,
                                          {"session_id": _move().uncell(d["session_id"])}))
            for k in d.get("keys") or ():
                out["agent_keys"].append(_row({**KEY_DEFAULTS, **k}, KEY_FIELDS))
            for g in d.get("rights") or ():
                out["agent_rights"].append(_row(g, RIGHT_FIELDS))
            for p in d.get("author_proofs") or ():
                out["agent_author_proofs"].append(_row(p, PROOF_FIELDS))
            for s in d.get("steers") or ():
                out["steers"].append(_row(s, STEER_FIELDS))
            for rev in tuple(doc.history) + (doc,):
                for ev in rev.data.get("events") or ():
                    events.append(_row({"detail": "{}", **ev}, EVENT_FIELDS))
        for t in ("agents", "agent_keys", "agent_rights", "agent_author_proofs"):
            out[t] = _numbered(out[t])
        out["steers"] = _numbered(sorted(out["steers"], key=lambda r: (
            r.cells["id"] is None, r.cells["created_at"] or 0)), "id")
        out["agent_key_events"] = _numbered(events, "id")
        return out


class HoldMapping:
    """agent_key_held and agent_key_errors as `hold` documents."""

    name = "hold"
    tables = ("agent_key_held", "agent_key_errors")
    under = (HOLDS,)
    history = False

    def documents(self, conn: sqlite3.Connection) -> Iterator:
        move = _move()
        docs: dict[str, dict] = {}

        def hold(subject: Any) -> dict:
            path = hold_path(subject)
            if path not in docs:
                docs[path] = new_hold(move.cell(subject))
            return docs[path]

        for r in _table_rows(conn, "agent_key_held"):
            hold(r.cells["subject"])["held"].append(_dict(r, HELD_FIELDS))
        for r in _table_rows(conn, "agent_key_errors"):
            hold(r.cells["subject"])["error"] = _dict(r, ERROR_FIELDS)
        for path, d in docs.items():
            yield move.Doc(path, d)

    def rows(self, docs) -> dict:
        held, errors = [], []
        for doc in docs:
            if not doc.path.endswith(".hold"):
                continue
            for h in doc.data.get("held") or ():
                held.append(_row({**HELD_DEFAULTS, **h}, HELD_FIELDS))
            if doc.data.get("error") is not None:
                errors.append(_row(doc.data["error"], ERROR_FIELDS))
        return {"agent_key_held": _numbered(held), "agent_key_errors": _numbered(errors)}


class SettingsMapping:
    """agent_key_settings and legacy_admissions as two `settings` documents."""

    name = "agent-key-settings"
    tables = ("agent_key_settings", "legacy_admissions")
    under = (SETTINGS,)
    history = False

    def documents(self, conn: sqlite3.Connection) -> Iterator:
        move = _move()
        named = [_dict(r, SETTING_FIELDS) for r in _table_rows(conn, "agent_key_settings")]
        counted = [_dict(r, LEGACY_FIELDS) for r in _table_rows(conn, "legacy_admissions")]
        if named:
            yield move.Doc(KEY_SETTINGS, {**new_settings(KEY_SETTINGS), "settings": named})
        if counted:
            yield move.Doc(LEGACY_SETTINGS, {**new_settings(LEGACY_SETTINGS), "admissions": counted})

    def rows(self, docs) -> dict:
        named, counted = [], []
        for doc in docs:
            if doc.path == KEY_SETTINGS:
                named = [_row(s, SETTING_FIELDS) for s in doc.data.get("settings") or ()]
            elif doc.path == LEGACY_SETTINGS:
                counted = [_row(a, LEGACY_FIELDS) for a in doc.data.get("admissions") or ()]
        return {"agent_key_settings": _numbered(named), "legacy_admissions": _numbered(counted)}


MAPPINGS = [AgentMapping(), HoldMapping(), SettingsMapping()]
