"""The single source of truth for Claude Code runs.

Every execution path writes here. The dashboard only reads. State
transitions are persisted before they are announced.

A run is one document in the engine, `/runs/<id>.run` (through
`records`), and its output stream a file read whole, `data/runs/<id>.jsonl`,
indexed as Content (UDS round 3; see "Runs" below).
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Optional

from jarvis import brain_activity, closed_files, data_paths, session_steer
from jarvis import message_records, records
from jarvis.knowledge import oscompat

log = logging.getLogger("jarvis.run_store")


class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"

    ALL = (QUEUED, RUNNING, SUCCEEDED, FAILED, TIMED_OUT, CANCELLED)
    TERMINAL = frozenset({SUCCEEDED, FAILED, TIMED_OUT, CANCELLED})
    ACTIVE = frozenset({QUEUED, RUNNING})


_UPDATABLE = frozenset({
    "status", "result_text", "summary", "error", "exit_code", "pid",
    "cost_usd", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "num_turns", "model", "requested_model",
    "started_at", "ended_at", "project_path", "project_name", "is_error", "context_sha256",
})


def init_db() -> None:
    """Kept callable: no record needs a table. A run is a document
    (`/runs/<id>.run`) and its output a file (`data/runs/<id>.jsonl`), and
    a run written before a field existed reads with that field's default
    (`_RUN_DEFAULTS`), which is what the old column backfill did. SQLite is
    retired (UDS round 3): every record is a document in the engine."""


# --- Runs: one document each; the output stream a file ------------------------
#
# A run is the document `/runs/<id>.run`, its data the run's fields by name
# (`RUN_FIELDS`, the old `runs` columns), written by JARVIS with each status
# as its change. Its output is a log read whole, so it is a file of its own,
# `data/runs/<id>.jsonl` (`data_paths.runs_dir()`): one JSON line per event,
# appended before any hint about it, closed (closed_files: made read-only)
# when the run finishes, and indexed as Content once closed, once
# (`/jarvis/runs`, knowledge_content.jarvis_roots). `RunMapping` moves the
# old tables into both and back.

RUNS = "/runs"
RUN_TYPE = "run"
_TEXT, _INTEGER, _REAL = "text", "integer", "real"

# Every field of a run, in the old table's column order: its kind (the
# column's affinity, kept on every write so an export gives the storage
# classes SQLite gave) and its default.
RUN_FIELDS: dict[str, tuple[str, object]] = {
    "id": (_TEXT, None), "project_name": (_TEXT, None), "project_path": (_TEXT, None),
    "prompt": (_TEXT, None), "origin": (_TEXT, None), "status": (_TEXT, None),
    "resume_from": (_TEXT, None), "result_text": (_TEXT, ""), "summary": (_TEXT, ""),
    "error": (_TEXT, ""), "exit_code": (_INTEGER, None), "pid": (_INTEGER, None),
    "cost_usd": (_REAL, 0.0), "input_tokens": (_INTEGER, 0), "output_tokens": (_INTEGER, 0),
    "cache_read_tokens": (_INTEGER, 0), "cache_creation_tokens": (_INTEGER, 0),
    "num_turns": (_INTEGER, 0), "model": (_TEXT, ""), "requested_model": (_TEXT, ""),
    "is_error": (_INTEGER, 0), "created_at": (_REAL, None), "started_at": (_REAL, None),
    "ended_at": (_REAL, None),
    # The registered folder it ran in (folder_store), when the brain started
    # it in one rather than in a project.
    "folder_id": (_TEXT, None),
    # The work package it carries out (work_packages), when the brain
    # started a package that runs as an agent.
    "package_id": (_TEXT, None),
    # The SHA-256 of the context pack it was given (context_service.py),
    # kept beside it in run_context_dir().
    "context_sha256": (_TEXT, None),
    # The project record and the piece of work it was started for
    # (project_store.py), when the brain named them.
    "project_id": (_TEXT, None), "work_id": (_TEXT, None),
}
_RUN_DEFAULTS = {k: d for k, (_kind, d) in RUN_FIELDS.items()}
# A moved run keeps the rowid SQLite gave it (the move kit counts it); the
# store never writes or returns it.
_ROWID = "rowid"


def _run_path(run_id: str) -> str:
    return f"{RUNS}/{records.segment(run_id)}.{RUN_TYPE}"


def _typed(field_name: str, value):
    """A value as its old column held it: REAL a float, INTEGER an int,
    TEXT text (numbers given as text, as SQLite's affinity did)."""
    if value is None:
        return None
    kind = RUN_FIELDS[field_name][0]
    if kind == _REAL and isinstance(value, (bool, int)):
        return float(value)
    if kind == _INTEGER:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
    if kind == _TEXT and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


def _run_data(fields: dict) -> dict:
    """A run's document data: every field, each typed, defaults filled."""
    return {k: _typed(k, fields.get(k, _RUN_DEFAULTS[k])) for k in RUN_FIELDS}


def _run_dict(data: Optional[dict]) -> Optional[dict]:
    """A run as callers read it: every field in the old column order, a
    field the document predates read as its default."""
    if data is None:
        return None
    return {k: data.get(k, _RUN_DEFAULTS[k]) for k in RUN_FIELDS}


def _run_change(fields: dict) -> str:
    return f"status:{fields['status']}" if "status" in fields else "update"


def create_run(prompt: str, project_name: str, project_path: str,
               origin: str, resume_from: str | None = None,
               folder_id: str | None = None, package_id: str | None = None,
               project_id: str | None = None, work_id: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    data = _run_data({
        "id": run_id, "project_name": project_name, "project_path": project_path,
        "prompt": prompt, "origin": origin, "status": RunStatus.QUEUED,
        "resume_from": resume_from, "folder_id": folder_id, "package_id": package_id,
        "project_id": project_id, "work_id": work_id, "created_at": time.time()})

    def write(unit: records.Unit) -> None:
        if unit.get(_run_path(run_id)) is not None:      # an id once in a universe
            raise RuntimeError(f"run {run_id} exists")
        unit.put(_run_path(run_id), data, change="created", summary=f"run queued in {project_name}"[:200])
    records.atomic(write, by=records.JARVIS, change="created")
    log.info("run %s created (%s, origin=%s)", run_id, project_name, origin)
    return run_id


def get_run(run_id: str) -> dict | None:
    rec = records.get(_run_path(run_id))
    return _run_dict(rec.data) if rec is not None else None


def _all_runs() -> list[dict]:
    return [_run_dict(r.data) for r in records.scan(RUNS, types=[RUN_TYPE])]


def all_run_ids() -> set[str]:
    """Every run id ever recorded.

    A run id is ALSO the Claude Code session id: `run_executor._command`
    passes it as `--session-id`. That makes this the exact set of roster
    sessions JARVIS started himself, which is what lets the voice path tell
    its own one-shot runs apart from the user's real conversations.
    """
    return {r["id"] for r in _all_runs()}


def list_runs(status: list[str] | None = None, project: str | None = None,
              limit: int = 50, before: float | None = None,
              package_id: str | None = None) -> list[dict]:
    """Newest first. `project` matches as the old LIKE '%project%' did: a
    substring, ASCII case ignored."""
    where: list = []
    if package_id:
        where.append(["package_id", "=", package_id])
    if status:
        where.append(["status", "in", list(status)])
    if before is not None:
        where.append(["created_at", "<", before])
    wanted = None if project is None else project.lower()
    out: list[dict] = []
    if limit is not None and limit == 0:
        return out
    for rec in records.scan(RUNS, types=[RUN_TYPE], where=where or None,
                            order=("created_at", "desc")):
        run = _run_dict(rec.data)
        # Text compares folded in the engine's `where`: checked exactly here.
        if package_id and run["package_id"] != package_id:
            continue
        if status and run["status"] not in status:
            continue
        if wanted is not None and wanted not in (run["project_name"] or "").lower():
            continue
        out.append(run)
        if limit is not None and 0 < limit <= len(out):
            break
    return out


def update_run(run_id: str, **fields) -> None:
    if not fields:
        return
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    typed = {k: _typed(k, v) for k, v in fields.items()}

    def change(data: Optional[dict]) -> Optional[dict]:
        if data is None:                  # no such run: nothing written, as before
            return None
        return {**data, **typed}
    records.update(_run_path(run_id), change, by=records.JARVIS, change=_run_change(typed),
                   summary=f"run {typed['status']}" if "status" in typed else None)


def finish_run(run_id: str, status: str, **fields) -> dict | None:
    """A run's terminal transition as one conditioned write: `status`,
    `ended_at` now and `fields`, unless the run is already terminal (or
    missing), when nothing is written and None is returned. The first
    writer wins, which keeps a cancel racing the driver to one status."""
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    typed = {k: _typed(k, v) for k, v in {**fields, "status": status,
                                          "ended_at": time.time()}.items()}

    def write(unit: records.Unit) -> Optional[dict]:
        rec = unit.get(_run_path(run_id))
        if rec is None or rec.data.get("status") in RunStatus.TERMINAL:
            return None
        data = {**rec.data, **typed}
        unit.put(_run_path(run_id), data, change=f"status:{status}", summary=f"run {status}")
        return _run_dict(data)
    finished = records.atomic(write, by=records.JARVIS, change=f"status:{status}")
    if finished is not None:
        close_output(run_id)
    return finished


def sweep_stale_runs() -> int:
    """Mark runs left active by a crashed server as failed."""
    count = 0
    for run in list_runs(status=list(RunStatus.ACTIVE), limit=-1):
        def fail(data: Optional[dict]) -> Optional[dict]:
            if data is None or data.get("status") not in RunStatus.ACTIVE:
                return None
            return {**data, "status": RunStatus.FAILED,
                    "error": "server restarted during run", "ended_at": time.time()}
        if records.update(_run_path(run["id"]), fail, by=records.JARVIS,
                          change="swept", summary="server restarted during run") is not None:
            close_output(run["id"])
            count += 1
    if count:
        log.warning("swept %d stale run(s) after restart", count)
    return count


# --- A run's events: its output stream, one file a run -------------------------

_EVENTS_LOCK = threading.Lock()
_EVENTS_CACHE: dict[str, tuple[tuple, list[dict]]] = {}
_EVENTS_CACHE_MAX = 16


def _events_rel(run_id: str) -> str:
    """The file's path under the data folder (the move kit's files root)."""
    return f"runs/{records.segment(run_id)}.jsonl"


def _events_file(run_id: str) -> Path:
    return data_paths.runs_dir() / f"{records.segment(run_id)}.jsonl"


def _event_line(run_id: str, event: dict) -> bytes:
    """One event as its line: `id` when it has one (moved from SQLite),
    `run_id` only when the file's name cannot give it back."""
    from jarvis.knowledge_move import cell
    line: dict = {}
    if event.get("id") is not None:
        line["id"] = event["id"]
    if records.unsegment(records.segment(run_id)) != run_id:
        line["run_id"] = cell(run_id)
    line.update(seq=event["seq"], ts=event["ts"], kind=cell(event["kind"]),
                payload=cell(event["payload"]))
    return (json.dumps(line, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")


def _parse_events(raw: bytes, run_id: str) -> list[dict]:
    """The events of a file, in the order written. A line that does not
    parse (the tail of an append a crash cut short) is passed over."""
    from jarvis.knowledge_move import uncell
    out: list[dict] = []
    for text in raw.split(b"\n"):
        if not text.strip():
            continue
        try:
            line = json.loads(text)
            out.append({"id": line.get("id"), "run_id": uncell(line.get("run_id", run_id)),
                        "seq": line["seq"], "ts": line["ts"], "kind": uncell(line["kind"]),
                        "payload": uncell(line["payload"])})
        except (ValueError, KeyError, TypeError):
            log.warning("run %s: an unreadable event line passed over", run_id)
    return out


def _stat_key(st: os.stat_result) -> tuple:
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _read_events(run_id: str) -> list[dict]:
    """Every event of the run as written (cached by the file's identity)."""
    path = _events_file(run_id)
    try:
        st = path.stat()
    except FileNotFoundError:
        return []
    key = str(path)
    cached = _EVENTS_CACHE.get(key)
    if cached is not None and cached[0] == _stat_key(st):
        return cached[1]
    with open(path, "rb") as f:
        st = os.fstat(f.fileno())
        events = _parse_events(f.read(), run_id)
    _remember(key, _stat_key(st), events)
    return events


def _remember(key: str, stat_key: tuple, events: list[dict]) -> None:
    _EVENTS_CACHE.pop(key, None)
    _EVENTS_CACHE[key] = (stat_key, events)
    while len(_EVENTS_CACHE) > _EVENTS_CACHE_MAX:
        _EVENTS_CACHE.pop(next(iter(_EVENTS_CACHE)))


def next_seq(run_id: str) -> int:
    return max((e["seq"] for e in _read_events(run_id)), default=0) + 1


def append_event(run_id: str, seq: int, kind: str, payload: str) -> None:
    append_events(run_id, [(seq, kind, payload)])


def append_events(run_id: str, rows: list[tuple[int, str, str]]) -> None:
    """Append many events to the run's file in one write.

    The executor batches through this, and announces each event only once
    this has returned: the line is in the file before any hint about it. A
    seq the run already has is passed over (the old INSERT OR IGNORE)."""
    if not rows:
        return
    now = time.time()
    with _EVENTS_LOCK:
        events = list(_read_events(run_id))
        seen = {e["seq"] for e in events}
        added: list[dict] = []
        for seq, kind, payload in rows:
            if seq in seen:
                continue
            seen.add(seq)
            added.append({"id": None, "run_id": run_id, "seq": int(seq), "ts": now,
                          "kind": kind, "payload": payload})
        if not added:
            return
        path = _events_file(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # An event after the run finished (its last lines racing a cancel)
        # goes in all the same, and the file is closed again after it.
        with closed_files.reopened(path):
            fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                body = b"".join(_event_line(run_id, e) for e in added)
                size = os.fstat(fd).st_size
                if size and oscompat.pread(fd, 1, size - 1) != b"\n":
                    body = b"\n" + body           # a torn last line stays on its own
                view = memoryview(body)
                while view:
                    view = view[os.write(fd, view):]
                st = os.fstat(fd)
            finally:
                os.close(fd)
        _remember(str(path), _stat_key(st), events + added)


def close_output(run_id: str) -> bool:
    """Close the run's output stream (closed_files): it has finished, so the
    content ingest reads it once, whole, now and never while it grows. Never
    in the middle of an append (the same lock)."""
    with _EVENTS_LOCK:
        return closed_files.close(_events_file(run_id))


def close_finished_outputs() -> int:
    """At start: close every output stream left open whose run has finished
    or is not on record (a run from before files were closed, or a crash
    between the finish and the close); how many were closed."""
    folder = data_paths.runs_dir()
    if not folder.is_dir():
        return 0
    count = 0
    for path in folder.glob("*.jsonl"):
        if closed_files.closed(path) or path.is_symlink():
            continue
        run = get_run(records.unsegment(path.name[:-len(".jsonl")]))
        if run is None or run["status"] in RunStatus.TERMINAL:
            with _EVENTS_LOCK:
                count += closed_files.close(path)
    return count


def get_events(run_id: str, after_seq: int = 0, limit: int = 200) -> list[dict]:
    found = sorted((e for e in _read_events(run_id) if e["seq"] > after_seq),
                   key=lambda e: e["seq"])
    # As SQLite's LIMIT did: a negative limit is no limit.
    return [dict(e) for e in (found if limit < 0 else found[:limit])]


def count_events(run_id: str) -> int:
    return len(_read_events(run_id))


_PERIODS = {"day": 86400, "week": 86400 * 7, "month": 86400 * 30}


def stats(period: str = "day") -> dict:
    cutoff = time.time() - _PERIODS.get(period, 86400)
    by_status = {s: 0 for s in RunStatus.ALL}
    total_cost = 0.0
    total_in = total_out = 0
    for rec in records.scan(RUNS, types=[RUN_TYPE], where=[["created_at", ">=", cutoff]]):
        r = _run_dict(rec.data)
        if r["created_at"] is None or r["created_at"] < cutoff:
            continue
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        total_cost += r["cost_usd"] or 0
        total_in += r["input_tokens"] or 0
        total_out += r["output_tokens"] or 0

    return {
        "period": period,
        "by_status": by_status,
        "total_runs": sum(by_status.values()),
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
    }


# --- Moving the old tables (knowledge_move) -------------------------------------

class RunMapping:
    """`runs` to run documents and `run_events` to each run's events file,
    and back, lossless (knowledge_move's `Mapping`)."""

    name = "run"
    tables = ("runs", "run_events")
    under = (RUNS,)
    history = False
    files_under = ("runs",)

    @staticmethod
    def _has(conn, table: str) -> bool:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,)).fetchone() is not None

    def documents(self, conn):
        from jarvis import knowledge_move as km
        if not self._has(conn, "runs"):
            return
        info = km.table_info(conn, "runs")
        for row in km.read_rows(conn, info):
            data = {k: km.cell(v) for k, v in row.cells.items()}
            if row.rowid is not None:
                data[_ROWID] = row.rowid
            yield km.Doc(_run_path(row.cells["id"]), data)

    def files(self, conn):
        from jarvis import knowledge_move as km
        if not self._has(conn, "run_events"):
            return
        info = km.table_info(conn, "run_events")
        by_run: dict = {}
        for row in km.read_rows(conn, info):           # in id order
            by_run.setdefault(row.cells["run_id"], []).append(row.cells)
        for run_id, events in by_run.items():
            yield _events_rel(run_id), b"".join(_event_line(run_id, e) for e in events)

    def rows(self, docs, files):
        from jarvis import knowledge_move as km
        runs = []
        for doc in docs:
            data = doc.data
            cells = {k: km.uncell(data.get(k, _RUN_DEFAULTS[k])) for k in RUN_FIELDS}
            runs.append(km.Row(cells, data.get(_ROWID)))
        # The moved runs (with their rowids) first, so an export inserts them
        # before the runs written since, which then take new rowids after them.
        runs.sort(key=lambda r: (r.rowid is None, r.rowid or 0, r.cells["created_at"] or 0))
        events: list[dict] = []
        for rel in files.list("runs"):
            if not rel.endswith(".jsonl"):
                continue
            stem = rel.rsplit("/", 1)[-1][:-len(".jsonl")]
            events.extend(_parse_events(files.read(rel), records.unsegment(stem)))
        largest = max((e["id"] for e in events if e["id"] is not None), default=0)
        for e in sorted((e for e in events if e["id"] is None),
                        key=lambda e: (e["ts"], e["run_id"], e["seq"])):
            largest += 1
            e["id"] = largest
        return {"runs": runs,
                "run_events": [km.Row({k: e[k] for k in ("id", "run_id", "seq", "ts", "kind", "payload")})
                               for e in events]}


MAPPINGS = [RunMapping()]


def record_steer(session_id: str, voice_name: str, project: str,
                 prompt: str, outcome: str) -> None:
    """Every keystroke, Terminal command and refused send attempt is
    recorded, including the refused ones — 'did you send that?' must have an
    answer. A message itself is a message record instead (create_message).
    Each is one of the `steers` of its session's agent document
    (agent_record.py; a session not named, the document of the empty id)."""
    from jarvis import agent_record
    row = {"id": None, "session_id": session_id, "voice_name": voice_name, "project": project,
           "prompt": prompt, "outcome": outcome, "created_at": time.time()}

    def run(w) -> None:
        w.agent(session_id)["steers"].append(dict(row))
        w.changed(agent_record.agent_path(session_id), "steer")
    agent_record.change(run)


def list_steers(limit: int = 50) -> list[dict]:
    """The latest steers across every agent, newest first."""
    from jarvis import agent_record
    rows = [s for _p, d in agent_record.CACHE.docs(".agent") for s in d.get("steers") or ()]
    rows.sort(key=lambda s: (s["created_at"], s["id"] is None, s["id"] or 0), reverse=True)
    return [{f: s.get(f) for f in agent_record.STEER_FIELDS} for s in rows[:limit]]


# --- Messages ----------------------------------------------------------------
#
# Each message JARVIS sends a session is one `message` document
# (message_records.py: /agents/<session id>/messages/m<id>.message), written
# when it is staged, BEFORE it is sent, and written again at every step
# after, each step a version of it; its conversation items (the TO_AGENT
# that recorded it, the agent's ANSWER) are in the same document.

class MessageStatus(StrEnum):
    STAGED = "staged"          # waiting out the cancel window
    QUEUED = "queued"          # waiting its turn: its session is busy, or has a message open
    CANCELLED = "cancelled"    # Tim stopped it
    SENT = "sent"              # the provider accepted it
    UNDELIVERED = "undelivered"  # not seen within a minute (he was told); still looked for
    DELIVERED = "delivered"    # it is in the session's transcript
    WORKING = "working"        # the session's turn on it has begun
    ANSWERED = "answered"      # that turn ended; reply stored
    FAILED = "failed"          # refused, not live, or unresolved after a day


# Sent and not yet answered: what the session watcher looks for in the
# session's transcript on every poll, after a restart as much as before it.
# The rest are settled and never read again by the watcher, so what it looks
# for each poll is only what is still unresolved, and nothing stays
# unresolved for more than a day (session_watch.MESSAGE_WATCH_SEC).
OPEN_MESSAGE_STATUSES = (MessageStatus.SENT, MessageStatus.UNDELIVERED,
                         MessageStatus.DELIVERED, MessageStatus.WORKING)

_MESSAGE_UPDATABLE = frozenset({
    "status", "sent_at", "delivered_at", "started_at", "answered_at", "reply",
    "failure",
})


@dataclass(frozen=True)
class Message:
    """One message JARVIS sent a session (its document's `message`)."""
    id: int
    session_id: str
    provider: str
    agent_name: str
    project: str
    capture_id: Optional[int]
    text: str
    status: str
    staged_at: float
    sent_at: Optional[float] = None
    delivered_at: Optional[float] = None
    started_at: Optional[float] = None
    answered_at: Optional[float] = None
    reply: Optional[str] = None
    failure: Optional[str] = None

    @property
    def body(self) -> str:
        """The text after JARVIS's signature: Tim's words, and what finds the
        message in the session's transcript."""
        return session_steer.message_body(self.text)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_MESSAGE_STATUSES


def _message(data: dict) -> Message:
    return Message(**message_records.unpack(data, message_records.MESSAGE_COLUMNS))


def _message_docs(where: list, unit=None) -> list:
    """The message documents holding a messages row that match `where`."""
    return [r for r in message_records.message_docs([["message.id", "exists", True], *where], unit)
            if r.data.get("message") is not None]


def _message_doc(message_id: int, unit=None):
    for rec in _message_docs([["message.id", "=", int(message_id)]], unit):
        if rec.data["message"].get("id") == int(message_id):
            return rec
    return None


def create_message(session_id: str, provider: str, agent_name: str,
                   project: str, text: str, capture_id: Optional[int] = None,
                   status: str = MessageStatus.STAGED,
                   failure: Optional[str] = None) -> int:
    """Record a message before anything is done with it. Its id."""
    status = str(status)

    def make(unit) -> int:
        message_id = message_records.take_id(unit, "messages", lambda: message_records.last_message_id() + 1)
        row = {"id": message_id, "session_id": session_id, "provider": provider,
               "agent_name": agent_name, "project": project, "capture_id": capture_id,
               "text": text, "status": status, "staged_at": time.time(), "failure": failure}
        path = message_records.message_path(message_records.doc_id("m", message_id), "agent",
                                            session_id)
        found = unit.get(path)
        data = found.data if found is not None else {"items": []}
        data.update(message=message_records.pack(row, message_records.MESSAGE_COLUMNS), events=[])
        unit.put(path, data, change=status, summary=f"message {message_id} to {agent_name}: {status}")
        return message_id

    message_id = records.atomic(make)
    _message_activity(message_id, status, session_id, agent_name)
    return message_id


def _message_activity(message_id: int, status: str, session_id: str,
                      agent_name: str) -> None:
    """The brain activity feed's `agent_message` event for a status just
    written: ids, the agent's name and the status; never the text."""
    brain_activity.note(brain_activity.AGENT_MESSAGE, status, id=str(message_id),
                        session_id=session_id, counterpart=agent_name)


def update_message(message_id: int, **fields) -> None:
    if not fields:
        return
    unknown = set(fields) - _MESSAGE_UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    if "status" in fields:
        fields["status"] = str(MessageStatus(fields["status"]))

    def change(unit):
        rec = _message_doc(message_id, unit)
        if rec is None:
            return None
        before = message_records.unpack(rec.data["message"], message_records.MESSAGE_COLUMNS)
        row = {**before, **fields}
        data = dict(rec.data, message=message_records.pack(row, message_records.MESSAGE_COLUMNS),
                    events=[])
        kind = fields.get("status", "updated")
        unit.put(rec.path, data, change=kind,
                 summary=f"message {message_id}: {', '.join(sorted(fields))}")
        return before

    before = records.atomic(change)
    if before is not None and "status" in fields and before["status"] != fields["status"]:
        _message_activity(message_id, fields["status"], before["session_id"],
                          before["agent_name"])


def get_message(message_id: int) -> Optional[Message]:
    rec = _message_doc(message_id)
    return _message(rec.data["message"]) if rec else None


def get_messages(message_ids) -> dict[int, Message]:
    """Several messages by id, in one read: what the conversation log reads
    each message's live status from."""
    ids = sorted({int(i) for i in message_ids})
    if not ids:
        return {}
    wanted = set(ids)
    out = {}
    for rec in _message_docs([["message.id", "in", ids]]):
        m = _message(rec.data["message"])
        if m.id in wanted:
            out[m.id] = m
    return out


def _newest_messages(where: list, test, limit: int) -> list[Message]:
    """The newest `limit` messages passing `test`, by staged time then id.
    With a narrowing condition, read in one; else looked for back from the
    newest id a widening window at a time."""
    def order(found):
        return sorted(found, key=lambda m: (m.staged_at, m.id), reverse=True)

    if where:
        return order([m for m in (_message(r.data["message"]) for r in _message_docs(where))
                      if test(m)])[:limit]
    top = message_records.next_value("messages")
    if top is None:
        top = message_records.last_message_id() + 1
    found: list[Message] = []
    span = max(64, limit * 2)
    while top > 0 and len(found) < limit:
        lo = max(0, top - span)
        found += [m for m in (_message(r.data["message"]) for r in
                              _message_docs(message_records.window("message.id", lo, top)))
                  if lo <= m.id < top and test(m)]
        top, span = lo, span * 4
    return order(found)[:limit]


def list_messages(session_id: Optional[str] = None,
                  agent_name: Optional[str] = None,
                  open_only: bool = False, limit: int = 20) -> list[Message]:
    """Most recent first. `agent_name` is matched without regard to case,
    since names are spoken."""
    where: list = []
    if session_id is not None:
        where.append(["message.session_id", "=", session_id])
    if open_only:
        where.append(["message.status", "in", [str(s) for s in OPEN_MESSAGE_STATUSES]])
    wanted_name = agent_name.lower() if agent_name is not None else None
    open_now = {str(s) for s in OPEN_MESSAGE_STATUSES}

    def test(m: Message) -> bool:
        return ((session_id is None or m.session_id == session_id)
                and (wanted_name is None or str(m.agent_name).lower() == wanted_name)
                and (not open_only or m.status in open_now))

    return _newest_messages(where, test, int(limit))


def _oldest_first(where: list, test) -> list[Message]:
    found = [m for m in (_message(r.data["message"]) for r in _message_docs(where)) if test(m)]
    return sorted(found, key=lambda m: (m.staged_at, m.id))


def open_messages() -> list[Message]:
    """Every message sent and not yet answered, oldest first."""
    statuses = [str(s) for s in OPEN_MESSAGE_STATUSES]
    return _oldest_first([["message.status", "in", statuses]], lambda m: m.status in statuses)


def queued_messages() -> list[Message]:
    """Every message waiting its turn, oldest first. Never swept on a
    restart: the queue is durable, and each goes out in order once its
    session is free (session_watch.SessionWatcher.send_queued)."""
    queued = str(MessageStatus.QUEUED)
    return _oldest_first([["message.status", "=", queued]], lambda m: m.status == queued)


def sweep_staged_messages() -> int:
    """A message still staged when the server starts was never sent: the
    server stopped inside its cancel window. Failed, and said why."""
    staged, failed = str(MessageStatus.STAGED), str(MessageStatus.FAILED)
    why = "the server restarted before it was sent"

    def sweep(unit) -> int:
        n = 0
        for rec in _message_docs([["message.status", "=", staged]], unit):
            row = message_records.unpack(rec.data["message"], message_records.MESSAGE_COLUMNS)
            if row["status"] != staged:
                continue
            row.update(status=failed, failure=why)
            unit.put(rec.path, dict(rec.data, message=message_records.pack(
                row, message_records.MESSAGE_COLUMNS), events=[]), change=failed, summary=why)
            n += 1
        return n

    count = records.atomic(sweep)
    if count:
        log.warning("swept %d staged message(s) after restart", count)
    return count


def last_message_gone(capture_id: int, session_id: str, since: float,
                      statuses: tuple) -> Optional[int]:
    """The newest message made from capture `capture_id` to `session_id`,
    staged at `since` or after, in one of `statuses`: its id, or None
    (package_inbox follows a sign-off's answers by it)."""
    found = [m for m in (_message(r.data["message"]) for r in _message_docs(
        [["message.capture_id", "=", int(capture_id)]]))
        if m.capture_id == capture_id and m.session_id == session_id and m.staged_at >= since
        and m.status in statuses]
    return max((m.id for m in found), default=None)


def message_projects() -> dict[str, int]:
    """How many messages went to each project, and how many session
    messages came from each (project_seed's legacy evidence): two counts."""
    def counted(field: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for g in records.count("/agents", types=[message_records.MESSAGE], group_by=field).groups or []:
            out[str(g["value"])] = out.get(str(g["value"]), 0) + int(g["items"])
        for g in records.count("/messages", types=[message_records.MESSAGE], group_by=field).groups or []:
            out[str(g["value"])] = out.get(str(g["value"]), 0) + int(g["items"])
        return out
    return {"messages": counted("message.project"),
            "session_messages": counted("session_message.project")}


# --- Messages from sessions (as files, before the signed route; read only) ----

@dataclass(frozen=True)
class SessionMessage:
    """One message a session sent Tim as a file (its document's
    `session_message`)."""
    id: int
    project: str
    agent_name: str
    subject: str
    body: str
    received_at: float
    read_at: Optional[float] = None
    sender_session: Optional[str] = None    # the session that sent it, when known
    sender_evidence: Optional[str] = None   # which transcript proved it (session_message_sender)
    sender_subagent: Optional[str] = None   # the subagent that wrote it, under its listed parent


def _session_message_doc(message_id: int, unit=None):
    for rec in message_records.message_docs(
            [["session_message.id", "=", int(message_id)]], unit):
        s = rec.data.get("session_message")
        if s is not None and s.get("id") == int(message_id):
            return rec
    return None


def get_session_message(message_id: int) -> Optional[SessionMessage]:
    rec = _session_message_doc(message_id)
    if rec is None:
        return None
    return SessionMessage(**message_records.unpack(rec.data["session_message"],
                                                    message_records.SESSION_MESSAGE_COLUMNS))


def mark_session_message_read(message_id: int) -> None:
    """The first read only: `read_at` keeps when he was first read it."""
    def mark(unit) -> None:
        rec = _session_message_doc(message_id, unit)
        if rec is None or rec.data["session_message"].get("read_at") is not None:
            return
        s = dict(rec.data["session_message"], read_at=time.time())
        unit.put(rec.path, dict(rec.data, session_message=s, events=[]), change="read",
                 summary=f"session message {message_id} read")
    records.atomic(mark)


# --- Agents ----------------------------------------------------------------
#
# Each agent is the `agent` field of its session's agent document
# (agent_record.py: `/agents/<session id>.agent`, which also holds its keys,
# rights and steers); None while the session is not on the list. Every read
# is answered from agent_record.CACHE (agent_names is read every second),
# every change is one atomic write through agent_record.change.

def _agent_row(session_id: str, agent: dict) -> dict:
    return {"session_id": session_id, "name": agent["name"],
            "active": bool(agent.get("active")), "state": agent.get("state") or ""}


def _listed() -> list[tuple[str, dict]]:
    """(session id, agent) of every agent on the list, running or ended."""
    from jarvis import agent_record
    return [(d["session_id"], d["agent"]) for _p, d in agent_record.CACHE.docs(".agent")
            if d.get("agent") is not None]


def _nocase(text: str) -> str:
    """SQLite's NOCASE and lower(): ASCII letters folded, nothing else."""
    return text.translate(_ASCII_LOWER)


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


# How long a stopped session's agent is kept for a resume to find it.
AGENT_ENDED_KEEP_SEC = 30 * 24 * 3600


# The labels JARVIS used to make up to tell two conversations of one project
# apart ("the older denice", "the denice that's working", "hammer, the memory
# tools one"). An agent whose stored name is one of these, and that he never named,
# gets its plain name the next time it is seen running; nothing else ever
# replaces a stored name (sync_agents).
# One line only: a stored name with a line break in it is not one of them.
_ONE_LINE = r"[^\r\n\v\f\x1c\x1d\x1e\x85  ]+"
_MADE_UP_NAME = re.compile(
    rf"the (newer|older|newest|second|third|fourth|fifth) {_ONE_LINE}"
    rf"|the {_ONE_LINE} (that needs you|that's working|that's idle|"
    r"that's working in the background|that's finished|that hasn't started|"
    r"in an unclear state)"
    rf"|{_ONE_LINE}, the {_ONE_LINE} one"
    rf"|{_ONE_LINE} number \d+")


def _sync_one(agent: Optional[dict], running: Optional[tuple[str, str]], now: float) -> tuple:
    """What sync_agents does to one agent: (its new `agent` field, what
    changed: added, renamed, changed, ended, forgotten or None)."""
    if agent is None:
        if running is None:
            return None, None
        name, state = running
        return {"name": name, "active": 0, "state": state, "custom_name": None,
                "last_message_at": 0.0, "ended_at": None}, "added"
    agent = dict(agent)
    if running is None:
        if agent.get("ended_at") is None:
            agent["ended_at"] = float(now)
            return agent, "ended"
        if agent["ended_at"] < now - AGENT_ENDED_KEEP_SEC:
            return None, "forgotten"
        return agent, None
    name, state = running
    what = None
    if not agent.get("custom_name") and _MADE_UP_NAME.fullmatch(agent.get("name") or "") \
            and name != agent.get("name"):
        agent["name"] = name
        what = "renamed"
    if agent.get("state", "") != state or agent.get("ended_at") is not None:
        agent["state"] = state
        agent["ended_at"] = None
        what = what or "changed"
    return agent, what


def sync_agents(running: dict[str, tuple[str, str]], now: float | None = None) -> bool:
    """Make the agents list match what is running now: `running` maps each
    running session's id to its (name, state).

    An agent has ONE name, `name`, and every screen, record and lookup reads
    it. A session seen for the first time is added INACTIVE (only the user
    activates one, set_agent_active) under the name it is given then: its
    plain derived name (session_watch.agent_name). From then on its name is
    never changed here: not by a new Claude Code title, a resume or a
    restart. Only the user renames an agent (rename_agent), and that name is
    kept for good. The one exception is a made-up label stored before this
    rule (`_MADE_UP_NAME`) on an agent he never named, replaced once by its
    plain name. A known one has its state refreshed. One that is no longer
    running is marked ended, not removed: the same session resumed (same
    session id) comes back with its name and activation. Ended ones are
    dropped after AGENT_ENDED_KEEP_SEC (the document itself only when nothing
    else is in it). Returns whether the running list changed, so the caller
    only tells the pages when it did. Called every poll: nothing is written
    when nothing changed.
    """
    from jarvis import agent_record
    now = time.time() if now is None else now
    known = dict(_listed())
    due = [sid for sid in set(known) | set(running)
           if _sync_one(known.get(sid), running.get(sid), now)[1] is not None]
    if not due:
        return False

    def run(w) -> bool:
        moved = False
        for sid in due:
            path = agent_record.agent_path(sid)
            doc = w.agent(sid, create=sid in running)
            if doc is None:
                continue
            agent, what = _sync_one(doc.get("agent"), running.get(sid), now)
            if what is None:
                continue
            moved = moved or what != "forgotten"
            doc["agent"] = agent
            if what == "forgotten" and not (doc["keys"] or doc["rights"] or doc["author_proofs"]
                                            or doc["steers"]) and not agent_record.has_events(path):
                w.delete(path, change="agent_forgotten")
            else:
                w.changed(path, "agent_" + what)
        return moved
    return agent_record.change(run)


# The states that count as busy: doing something, or waiting on the user.
# Everything else (idle, not yet started, a shell) counts as idle.
BUSY_AGENT_STATES = ("working", "needs_you", "background")


def list_agents() -> list[dict]:
    """In order of activity: busy agents above idle ones, and within each,
    the one most recently sent a message first, then by name."""
    rows = [(sid, a) for sid, a in _listed() if a.get("ended_at") is None]
    rows.sort(key=lambda r: (r[1].get("state") not in BUSY_AGENT_STATES,
                             -(r[1].get("last_message_at") or 0.0), _nocase(r[1]["name"])))
    return [_agent_row(sid, a) for sid, a in rows]


def _change_agent(session_id: str, change: str, fn, running_only: bool = False,
                  his: bool = False) -> Optional[dict]:
    """`fn(agent)` changes one listed agent in place; the agent's row
    after, or None when it is not on the list (with `running_only`, or has
    ended)."""
    from jarvis import agent_record

    def run(w) -> Optional[dict]:
        doc = w.agent(session_id, create=False)
        agent = None if doc is None else doc.get("agent")
        if agent is None or (running_only and agent.get("ended_at") is not None):
            return None
        fn(agent)
        w.changed(agent_record.agent_path(session_id), change)
        return _agent_row(session_id, agent)
    return agent_record.change(run, by=agent_record.records.TIM if his else agent_record.records.JARVIS)


def mark_agent_messaged(session_id: str) -> None:
    """JARVIS has just sent this agent a message: it moves up its group."""
    now = time.time()
    _change_agent(session_id, "agent_messaged",
                  lambda a: a.update(last_message_at=float(now)))


def set_agent_active(session_id: str, active: bool) -> dict | None:
    """The user activating or deactivating one agent. None if it is not in
    the list (it has ended, or was never seen)."""
    return _change_agent(session_id, "agent_activated" if active else "agent_deactivated",
                         lambda a: a.update(active=int(bool(active))), running_only=True,
                         his=True)


class DuplicateAgentName(ValueError):
    """Another agent, running or kept for a resume, already goes by that name."""


def agent_names() -> dict[str, str]:
    """session id -> its name, for every agent on the list, running or kept
    for a resume (but a made-up label it was never renamed from, see
    `_MADE_UP_NAME`). The one field an agent's name is read from: the session
    snapshot goes by it (session_watch.Snapshot.named), so every screen,
    record and lookup says the same name."""
    # A made-up label stored before names were made one is not a name: the
    # snapshot keeps the plain derived name, and sync_agents stores it.
    return {sid: a["name"] for sid, a in _listed()
            if a.get("custom_name") or not _MADE_UP_NAME.fullmatch(a.get("name") or "")}


def rename_agent(session_id: str, new_name: str) -> dict | None:
    """Set an agent's official name. None if there is no such agent;
    DuplicateAgentName if another agent already has that name (compared
    without regard to case, since names are spoken)."""
    from jarvis import agent_record

    def run(w) -> Optional[dict]:
        doc = w.agent(session_id, create=False)
        if doc is None or doc.get("agent") is None:
            return None
        wanted = _nocase(new_name)
        for rec in w.scan(agent_record.AGENTS, parent=agent_record.AGENTS, types=["agent"]):
            other = rec.data.get("agent")
            if rec.data.get("session_id") != session_id and other is not None \
                    and _nocase(other.get("name") or "") == wanted:
                raise DuplicateAgentName(new_name)
        doc["agent"].update(custom_name=new_name, name=new_name)
        w.changed(agent_record.agent_path(session_id), "agent_renamed")
        return _agent_row(session_id, doc["agent"])
    return agent_record.change(run, by=agent_record.records.TIM)
