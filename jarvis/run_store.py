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
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Optional

from jarvis import brain_activity, data_paths, records, session_steer
from jarvis.data_paths import db_path

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


WAL_WAIT_SEC = 15.0


def _ensure_wal(conn: sqlite3.Connection, wait: float = WAL_WAIT_SEC) -> None:
    """The database in WAL, or the error that stopped it."""
    deadline = time.monotonic() + wait
    while True:
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
            return
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
            continue
        if mode != "wal":
            raise sqlite3.OperationalError(f"the database stayed in {mode} mode, not WAL")


def _connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """A connection to JARVIS's database (`path` for a store given its own
    file), with the pragmas every one of its users needs."""
    conn = sqlite3.connect(str(path or db_path()), timeout=15.0)
    conn.row_factory = sqlite3.Row
    # Switching to WAL takes an exclusive lock. Once a database is in WAL it
    # stays there, so only switch when it is not: asking every connection to
    # switch made a busy moment fail with "database is locked".
    # SQLite refuses the switch at once (no busy wait) while another
    # connection is open, which happens when two first connections race on a
    # new database. Only that race is let through: the database must be seen
    # in WAL (the other connection's switch landed) within the busy timeout,
    # trying the switch again meanwhile; anything else is raised as it was.
    _ensure_wal(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection, schema_table: str,
            migrations: dict[int, str], what: str) -> int:
    """Bring one module's own tables forward to the last of `migrations`,
    inside the caller's open transaction, and return that version.

    For a store that keeps its tables in this database under a schema
    version of its own (`schema_table`, one row): each step takes the schema
    from the version before it to its own, in order, and a step is only ever
    appended, never changed. A database already newer than this JARVIS is
    left exactly as it is (RuntimeError), so a rollback never meets tables it
    does not know."""
    latest = max(migrations)
    conn.execute(f"CREATE TABLE IF NOT EXISTS {schema_table} (version INTEGER NOT NULL)")
    row = conn.execute(f"SELECT version FROM {schema_table}").fetchone()
    version = row[0] if row else 0
    if version > latest:
        raise RuntimeError(f"{what} schema {version} is newer than this "
                           f"JARVIS ({latest}); it is left as it is")
    for step in range(version + 1, latest + 1):
        conn.executescript(migrations[step])
    if row is None:
        conn.execute(f"INSERT INTO {schema_table} (version) VALUES (?)", (latest,))
    else:
        conn.execute(f"UPDATE {schema_table} SET version = ?", (latest,))
    return latest


def init_db() -> None:
    """Kept callable: runs need no table. A run is a document
    (`/runs/<id>.run`) and its output a file (`data/runs/<id>.jsonl`), and
    a run written before a field existed reads with that field's default
    (`_RUN_DEFAULTS`), which is what the old column backfill did."""
    _tables_of_other_groups()


def _tables_of_other_groups() -> None:
    """The SQLite tables the steers, messages, session messages and agents
    functions below still read until their own rewrites land (UDS round
    3). Deleted with `_connect`."""
    with closing(_connect()) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS steers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                voice_name TEXT NOT NULL,
                project TEXT NOT NULL,
                prompt TEXT NOT NULL,
                outcome TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_steers_created ON steers(created_at DESC);

            -- One row per message JARVIS sends a session, written BEFORE it
            -- is sent and updated at every step after: sent, seen in the
            -- session's transcript, the turn on it begun, that turn ended
            -- and its answer kept. See MessageStatus and the session
            -- providers' `track`. Keystrokes and Terminal commands are not
            -- messages; they stay in `steers`.
            CREATE TABLE IF NOT EXISTS messages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT NOT NULL,
                provider      TEXT NOT NULL,          -- "anthropic", "openai"
                agent_name    TEXT NOT NULL,          -- the name it was sent under
                project       TEXT NOT NULL,
                capture_id    INTEGER,                -- the capture it came from
                text          TEXT NOT NULL,          -- exactly what was sent
                status        TEXT NOT NULL,          -- see MessageStatus
                staged_at     REAL NOT NULL,
                sent_at       REAL,
                delivered_at  REAL,                   -- seen in the session's transcript
                started_at    REAL,                   -- the session's turn on it began
                answered_at   REAL,                   -- that turn ended
                reply         TEXT,                   -- the turn's final answer, as said
                failure       TEXT                    -- why, when status is failed
            );
            CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
            CREATE INDEX IF NOT EXISTS idx_messages_staged ON messages(staged_at DESC);

            -- One row per message a session sent Tim as a file, before every
            -- message came by the signed route (messages_api.py): kept so
            -- the ones on record stay readable. Nothing writes it now.
            CREATE TABLE IF NOT EXISTS session_messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                project      TEXT NOT NULL,     -- the folder it was dropped in
                agent_name   TEXT NOT NULL,     -- who sent it, as the agents list names them
                subject      TEXT NOT NULL,
                body         TEXT NOT NULL,
                received_at  REAL NOT NULL,
                read_at      REAL               -- when it was first read to the brain
            );
            CREATE INDEX IF NOT EXISTS idx_session_messages_received
                ON session_messages(received_at DESC);

            -- Every Claude Code session running now or stopped lately, by
            -- name, its state as the session watcher derives it, and whether
            -- the user has activated it. `active` is his choice alone: a
            -- session seen for the first time is inactive. See sync_agents.
            CREATE TABLE IF NOT EXISTS agents (
                session_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT '',
                -- The name the user set, if any. Never written by
                -- sync_agents. `name` is the agent's one name either way:
                -- set once when first seen, and only he changes it.
                custom_name TEXT,
                -- When JARVIS last sent it a message (0: never). Orders the list.
                last_message_at REAL NOT NULL DEFAULT 0,
                -- When its session stopped running (NULL: running). Kept, not
                -- deleted, so a resumed session gets its name and activation
                -- back. See sync_agents.
                ended_at REAL
            );
        """)
        conn.commit()
        message_cols = {r["name"] for r in conn.execute("PRAGMA table_info(session_messages)")}
        for column in ("sender_session", "sender_evidence", "sender_subagent"):
            # Who sent it, and on what evidence (session_message_sender.py).
            if column not in message_cols:
                conn.execute(f"ALTER TABLE session_messages ADD COLUMN {column} TEXT")
                conn.commit()
        agent_cols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)")}
        if "state" not in agent_cols:
            conn.execute(
                "ALTER TABLE agents ADD COLUMN state TEXT NOT NULL DEFAULT ''")
            conn.commit()
        if "custom_name" not in agent_cols:
            conn.execute("ALTER TABLE agents ADD COLUMN custom_name TEXT")
            conn.commit()
        if "last_message_at" not in agent_cols:
            conn.execute(
                "ALTER TABLE agents ADD COLUMN last_message_at REAL NOT NULL DEFAULT 0")
            conn.commit()
        if "ended_at" not in agent_cols:
            conn.execute("ALTER TABLE agents ADD COLUMN ended_at REAL")
            conn.commit()


# --- Runs: one document each; the output stream a file ------------------------
#
# A run is the document `/runs/<id>.run`, its data the run's fields by name
# (`RUN_FIELDS`, the old `runs` columns), written by JARVIS with each status
# as its change. Its output is a log read whole, so it is a file of its own,
# `data/runs/<id>.jsonl` (`data_paths.runs_dir()`): one JSON line per event,
# appended before any hint about it, and indexed as Content (`/jarvis/runs`,
# knowledge_content.jarvis_roots). `RunMapping` moves the old tables into
# both and back.

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
    return records.atomic(write, by=records.JARVIS, change=f"status:{status}")


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
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            body = b"".join(_event_line(run_id, e) for e in added)
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                body = b"\n" + body           # a torn last line stays on its own
            view = memoryview(body)
            while view:
                view = view[os.write(fd, view):]
            st = os.fstat(fd)
        finally:
            os.close(fd)
        _remember(str(path), _stat_key(st), events + added)


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
    answer. A message itself is a `messages` row instead (create_message)."""
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO steers (session_id, voice_name, project, prompt, outcome, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, voice_name, project, prompt, outcome, time.time()))
        conn.commit()


def list_steers(limit: int = 50) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM steers ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# --- Messages ----------------------------------------------------------------

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
    """One row of `messages`."""
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


def _message(row) -> Message:
    return Message(**{k: row[k] for k in row.keys()})


def create_message(session_id: str, provider: str, agent_name: str,
                   project: str, text: str, capture_id: Optional[int] = None,
                   status: str = MessageStatus.STAGED,
                   failure: Optional[str] = None) -> int:
    """Record a message before anything is done with it. Its id."""
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, provider, agent_name, project, "
            "capture_id, text, status, staged_at, failure) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, provider, agent_name, project, capture_id, text,
             str(status), time.time(), failure))
        conn.commit()
        message_id = int(cur.lastrowid)
    _message_activity(message_id, str(status), session_id, agent_name)
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
    assignments = ", ".join(f"{k}=?" for k in fields)
    with closing(_connect()) as conn:
        before = (conn.execute("SELECT status, session_id, agent_name FROM messages "
                               "WHERE id=?", (message_id,)).fetchone()
                  if "status" in fields else None)
        conn.execute(f"UPDATE messages SET {assignments} WHERE id=?",
                     (*fields.values(), message_id))
        conn.commit()
    if before is not None and before["status"] != fields["status"]:
        _message_activity(message_id, fields["status"], before["session_id"],
                          before["agent_name"])


def get_message(message_id: int) -> Optional[Message]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM messages WHERE id=?",
                           (message_id,)).fetchone()
    return _message(row) if row else None


def get_messages(message_ids) -> dict[int, Message]:
    """Several messages by id, in one read: what the conversation log reads
    each message's live status from."""
    ids = sorted({int(i) for i in message_ids})
    if not ids:
        return {}
    with closing(_connect()) as conn:
        rows = conn.execute(f"SELECT * FROM messages WHERE id IN ({','.join('?' * len(ids))})",
                            ids).fetchall()
    return {r["id"]: _message(r) for r in rows}


def list_messages(session_id: Optional[str] = None,
                  agent_name: Optional[str] = None,
                  open_only: bool = False, limit: int = 20) -> list[Message]:
    """Most recent first. `agent_name` is matched without regard to case,
    since names are spoken."""
    sql = "SELECT * FROM messages WHERE 1=1"
    params: list = []
    if session_id is not None:
        sql += " AND session_id = ?"
        params.append(session_id)
    if agent_name is not None:
        sql += " AND lower(agent_name) = lower(?)"
        params.append(agent_name)
    if open_only:
        sql += f" AND status IN ({','.join('?' * len(OPEN_MESSAGE_STATUSES))})"
        params.extend(str(s) for s in OPEN_MESSAGE_STATUSES)
    sql += " ORDER BY staged_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_message(r) for r in rows]


def open_messages() -> list[Message]:
    """Every message sent and not yet answered, oldest first."""
    marks = ",".join("?" * len(OPEN_MESSAGE_STATUSES))
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT * FROM messages WHERE status IN ({marks}) "
            "ORDER BY staged_at, id", [str(s) for s in OPEN_MESSAGE_STATUSES]
        ).fetchall()
    return [_message(r) for r in rows]


def queued_messages() -> list[Message]:
    """Every message waiting its turn, oldest first. Never swept on a
    restart: the queue is durable, and each goes out in order once its
    session is free (session_watch.SessionWatcher.send_queued)."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE status=? ORDER BY staged_at, id",
            (str(MessageStatus.QUEUED),)).fetchall()
    return [_message(r) for r in rows]


def sweep_staged_messages() -> int:
    """A message still staged when the server starts was never sent: the
    server stopped inside its cancel window. Failed, and said why."""
    with closing(_connect()) as conn:
        cur = conn.execute(
            "UPDATE messages SET status=?, failure=? WHERE status=?",
            (str(MessageStatus.FAILED), "the server restarted before it was sent",
             str(MessageStatus.STAGED)))
        conn.commit()
        count = cur.rowcount
    if count:
        log.warning("swept %d staged message(s) after restart", count)
    return count


# --- Messages from sessions (as files, before the signed route; read only) ----

@dataclass(frozen=True)
class SessionMessage:
    """One row of `session_messages`."""
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


def get_session_message(message_id: int) -> Optional[SessionMessage]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM session_messages WHERE id=?",
                           (message_id,)).fetchone()
    return SessionMessage(**dict(row)) if row else None


def mark_session_message_read(message_id: int) -> None:
    """The first read only: `read_at` keeps when he was first read it."""
    with closing(_connect()) as conn:
        conn.execute("UPDATE session_messages SET read_at=? "
                     "WHERE id=? AND read_at IS NULL", (time.time(), message_id))
        conn.commit()


# --- Agents ----------------------------------------------------------------

def _agent_row(row) -> dict:
    return {"session_id": row["session_id"], "name": row["name"],
            "active": bool(row["active"]), "state": row["state"]}


# How long a stopped session's agent is kept for a resume to find it.
AGENT_ENDED_KEEP_SEC = 30 * 24 * 3600


# The labels JARVIS used to make up to tell two conversations of one project
# apart ("the older denice", "the denice that's working", "hammer, the memory
# tools one"). An agent whose stored name is one of these, and that he never named,
# gets its plain name the next time it is seen running; nothing else ever
# replaces a stored name (sync_agents).
# One line only: a stored name with a line break in it is not one of them.
_ONE_LINE = r"[^\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029]+"
_MADE_UP_NAME = re.compile(
    rf"the (newer|older|newest|second|third|fourth|fifth) {_ONE_LINE}"
    rf"|the {_ONE_LINE} (that needs you|that's working|that's idle|"
    r"that's working in the background|that's finished|that hasn't started|"
    r"in an unclear state)"
    rf"|{_ONE_LINE}, the {_ONE_LINE} one"
    rf"|{_ONE_LINE} number \d+")


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
    dropped after AGENT_ENDED_KEEP_SEC. Returns whether the running list
    changed, so the caller only tells the pages when it did.
    """
    now = time.time() if now is None else now
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT session_id, name, state, custom_name, ended_at FROM agents").fetchall()
        known = {r["session_id"]: (r["name"], r["state"]) for r in rows}
        stopped = {r["session_id"] for r in rows if r["ended_at"] is not None}
        made_up = {r["session_id"] for r in rows
                   if not r["custom_name"] and _MADE_UP_NAME.fullmatch(r["name"] or "")}
        added = [(sid, name, state) for sid, (name, state) in running.items()
                 if sid not in known]
        renamed = [(name, sid) for sid, (name, _state) in running.items()
                   if sid in made_up and name != known[sid][0]]
        changed = [(state, sid) for sid, (_name, state) in running.items()
                   if sid in known and (known[sid][1] != state or sid in stopped)]
        ended = [(now, sid) for sid in known if sid not in running and sid not in stopped]
        conn.executemany(
            "INSERT INTO agents (session_id, name, state, active) VALUES (?, ?, ?, 0)",
            added)
        conn.executemany("UPDATE agents SET name=? WHERE session_id=?", renamed)
        conn.executemany(
            "UPDATE agents SET state=?, ended_at=NULL WHERE session_id=?", changed)
        conn.executemany("UPDATE agents SET ended_at=? WHERE session_id=?", ended)
        conn.execute("DELETE FROM agents WHERE ended_at < ?", (now - AGENT_ENDED_KEEP_SEC,))
        conn.commit()
    return bool(added or renamed or changed or ended)


# The states that count as busy: doing something, or waiting on the user.
# Everything else (idle, not yet started, a shell) counts as idle.
BUSY_AGENT_STATES = ("working", "needs_you", "background")


def list_agents() -> list[dict]:
    """In order of activity: busy agents above idle ones, and within each,
    the one most recently sent a message first, then by name."""
    busy = ", ".join("?" * len(BUSY_AGENT_STATES))
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT * FROM agents WHERE ended_at IS NULL ORDER BY (state IN ({busy})) DESC, "
            "last_message_at DESC, name COLLATE NOCASE", BUSY_AGENT_STATES).fetchall()
    return [_agent_row(r) for r in rows]


def mark_agent_messaged(session_id: str) -> None:
    """JARVIS has just sent this agent a message: it moves up its group."""
    with closing(_connect()) as conn:
        conn.execute("UPDATE agents SET last_message_at=? WHERE session_id=?",
                     (time.time(), session_id))
        conn.commit()


def set_agent_active(session_id: str, active: bool) -> dict | None:
    """The user activating or deactivating one agent. None if it is not in
    the list (it has ended, or was never seen)."""
    with closing(_connect()) as conn:
        conn.execute("UPDATE agents SET active=? WHERE session_id=? AND ended_at IS NULL",
                     (int(bool(active)), session_id))
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE session_id=? AND ended_at IS NULL",
                           (session_id,)).fetchone()
    return _agent_row(row) if row else None


class DuplicateAgentName(ValueError):
    """Another agent, running or kept for a resume, already goes by that name."""


def agent_names() -> dict[str, str]:
    """session id -> its name, for every agent on the list, running or kept
    for a resume (but a made-up label it was never renamed from, see
    `_MADE_UP_NAME`). The one field an agent's name is read from: the session
    snapshot goes by it (session_watch.Snapshot.named), so every screen,
    record and lookup says the same name."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT session_id, name, custom_name FROM agents").fetchall()
    # A made-up label stored before names were made one is not a name: the
    # snapshot keeps the plain derived name, and sync_agents stores it.
    return {r["session_id"]: r["name"] for r in rows
            if r["custom_name"] or not _MADE_UP_NAME.fullmatch(r["name"] or "")}


def rename_agent(session_id: str, new_name: str) -> dict | None:
    """Set an agent's official name. None if there is no such agent;
    DuplicateAgentName if another agent already has that name (compared
    without regard to case, since names are spoken)."""
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM agents WHERE session_id=?",
                           (session_id,)).fetchone()
        if row is None:
            return None
        clash = conn.execute(
            "SELECT 1 FROM agents WHERE session_id != ? AND lower(name) = lower(?)",
            (session_id, new_name)).fetchone()
        if clash:
            raise DuplicateAgentName(new_name)
        conn.execute("UPDATE agents SET custom_name=?, name=? WHERE session_id=?",
                     (new_name, new_name, session_id))
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE session_id=?",
                           (session_id,)).fetchone()
    return _agent_row(row)
