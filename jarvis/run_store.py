"""The single source of truth for Claude Code runs.

Every execution path writes here. The dashboard only reads. State
transitions are persisted before they are announced.
"""

import logging
import re
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Optional

from jarvis import brain_activity, session_steer
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
    with closing(_connect()) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id            TEXT PRIMARY KEY,
                project_name  TEXT NOT NULL,
                project_path  TEXT NOT NULL,
                prompt        TEXT NOT NULL,
                origin        TEXT NOT NULL,
                status        TEXT NOT NULL,
                resume_from   TEXT,
                result_text   TEXT DEFAULT '',
                summary       TEXT DEFAULT '',
                error         TEXT DEFAULT '',
                exit_code     INTEGER,
                pid           INTEGER,
                cost_usd      REAL    DEFAULT 0,
                input_tokens  INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens     INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                num_turns     INTEGER DEFAULT 0,
                model         TEXT DEFAULT '',
                requested_model TEXT DEFAULT '',
                is_error      INTEGER DEFAULT 0,
                -- The registered folder it ran in (folder_store), when the
                -- brain started it in one rather than in a project.
                folder_id     TEXT,
                -- The work package it carries out (work_packages), when the
                -- brain started a package that runs as an agent.
                package_id    TEXT,
                -- The SHA-256 of the context pack it was given
                -- (context_service.py), kept beside it in run_context_dir().
                context_sha256 TEXT,
                -- The project record and the piece of work it was started
                -- for (project_store.py), when the brain named them.
                project_id    TEXT,
                work_id       TEXT,
                created_at    REAL NOT NULL,
                started_at    REAL,
                ended_at      REAL
            );
            CREATE INDEX IF NOT EXISTS idx_runs_status  ON runs(status);
            CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);

            CREATE TABLE IF NOT EXISTS run_events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  TEXT    NOT NULL REFERENCES runs(id),
                seq     INTEGER NOT NULL,
                ts      REAL    NOT NULL,
                kind    TEXT    NOT NULL,
                payload TEXT    NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_run_seq
                ON run_events(run_id, seq);

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

        # `CREATE TABLE IF NOT EXISTS` never alters a table that already
        # exists, so a live jarvis.db predating a column needs an explicit
        # backfill. Same PRAGMA-table_info-then-ALTER pattern as
        # migrations/001_dispatches_to_runs.py.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
        if "requested_model" not in cols:
            conn.execute(
                "ALTER TABLE runs ADD COLUMN requested_model TEXT DEFAULT ''")
            conn.commit()
        if "is_error" not in cols:
            conn.execute(
                "ALTER TABLE runs ADD COLUMN is_error INTEGER DEFAULT 0")
            conn.commit()
        if "folder_id" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN folder_id TEXT")
            conn.commit()
        if "package_id" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN package_id TEXT")
            conn.commit()
        if "context_sha256" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN context_sha256 TEXT")
            conn.commit()
        for column in ("project_id", "work_id"):
            if column not in cols:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {column} TEXT")
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


def create_run(prompt: str, project_name: str, project_path: str,
               origin: str, resume_from: str | None = None,
               folder_id: str | None = None, package_id: str | None = None,
               project_id: str | None = None, work_id: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO runs (id, project_name, project_path, prompt, origin, "
            "status, resume_from, folder_id, package_id, project_id, work_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, project_name, project_path, prompt, origin,
             RunStatus.QUEUED, resume_from, folder_id, package_id, project_id, work_id,
             time.time()),
        )
        conn.commit()
    log.info("run %s created (%s, origin=%s)", run_id, project_name, origin)
    return run_id


def get_run(run_id: str) -> dict | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def all_run_ids() -> set[str]:
    """Every run id ever recorded.

    A run id is ALSO the Claude Code session id: `run_executor._command`
    passes it as `--session-id`. That makes this the exact set of roster
    sessions JARVIS started himself, which is what lets the voice path tell
    its own one-shot runs apart from the user's real conversations.
    """
    with closing(_connect()) as conn:
        return {r["id"] for r in conn.execute("SELECT id FROM runs")}


def list_runs(status: list[str] | None = None, project: str | None = None,
              limit: int = 50, before: float | None = None,
              package_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM runs WHERE 1=1"
    params: list = []
    if package_id:
        sql += " AND package_id = ?"
        params.append(package_id)
    if status:
        sql += f" AND status IN ({','.join('?' * len(status))})"
        params.extend(status)
    if project:
        sql += " AND project_name LIKE ?"
        params.append(f"%{project}%")
    if before is not None:
        sql += " AND created_at < ?"
        params.append(before)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with closing(_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def update_run(run_id: str, **fields) -> None:
    if not fields:
        return
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"not updatable: {sorted(unknown)}")
    assignments = ", ".join(f"{k}=?" for k in fields)
    with closing(_connect()) as conn:
        conn.execute(f"UPDATE runs SET {assignments} WHERE id=?",
                     (*fields.values(), run_id))
        conn.commit()


def next_seq(run_id: str) -> int:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM run_events WHERE run_id=?",
            (run_id,)).fetchone()
        return int(row["m"]) + 1


def append_event(run_id: str, seq: int, kind: str, payload: str) -> None:
    append_events(run_id, [(seq, kind, payload)])


def append_events(run_id: str, rows: list[tuple[int, str, str]]) -> None:
    """Insert many events in one transaction.

    The executor batches through this so a chatty build costs one commit per
    batch rather than one per event.
    """
    if not rows:
        return
    now = time.time()
    with closing(_connect()) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO run_events (run_id, seq, ts, kind, payload) "
            "VALUES (?,?,?,?,?)",
            [(run_id, seq, now, kind, payload) for seq, kind, payload in rows])
        conn.commit()


def get_events(run_id: str, after_seq: int = 0, limit: int = 200) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE run_id=? AND seq>? "
            "ORDER BY seq ASC LIMIT ?",
            (run_id, after_seq, limit)).fetchall()
        return [dict(r) for r in rows]


def count_events(run_id: str) -> int:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM run_events WHERE run_id=?",
            (run_id,)).fetchone()
        return int(row["n"])


def sweep_stale_runs() -> int:
    """Mark runs left active by a crashed server as failed."""
    with closing(_connect()) as conn:
        cur = conn.execute(
            "UPDATE runs SET status=?, error=?, ended_at=? WHERE status IN (?,?)",
            (RunStatus.FAILED, "server restarted during run", time.time(),
             RunStatus.QUEUED, RunStatus.RUNNING))
        conn.commit()
        count = cur.rowcount
    if count:
        log.warning("swept %d stale run(s) after restart", count)
    return count


_PERIODS = {"day": 86400, "week": 86400 * 7, "month": 86400 * 30}


def stats(period: str = "day") -> dict:
    cutoff = time.time() - _PERIODS.get(period, 86400)
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n, "
            "COALESCE(SUM(cost_usd),0) AS cost, "
            "COALESCE(SUM(input_tokens),0) AS inp, "
            "COALESCE(SUM(output_tokens),0) AS out "
            "FROM runs WHERE created_at >= ? GROUP BY status",
            (cutoff,)).fetchall()

    by_status = {s: 0 for s in RunStatus.ALL}
    total_cost = total_in = total_out = 0
    for r in rows:
        by_status[r["status"]] = r["n"]
        total_cost += r["cost"]
        total_in += r["inp"]
        total_out += r["out"]

    return {
        "period": period,
        "by_status": by_status,
        "total_runs": sum(by_status.values()),
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
    }


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
