"""The single source of truth for Claude Code runs.

Every execution path writes here. The dashboard only reads. State
transitions are persisted before they are announced.
"""

import logging
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Optional

from jarvis import session_steer
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
    "started_at", "ended_at", "project_path", "project_name", "is_error",
})


def _connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """A connection to JARVIS's database (`path` for a store given its own
    file), with the pragmas every one of its users needs."""
    conn = sqlite3.connect(str(path or db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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

            -- One row per message a session sent Tim (session_messages.py):
            -- written when its file is picked up, before the file is deleted
            -- and before anything is said about it.
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
                -- The name the user set, if any: the agent's official name.
                -- Never written by sync_agents. NULL means "use the derived
                -- one". `name` holds whichever applies.
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
               folder_id: str | None = None, package_id: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO runs (id, project_name, project_path, prompt, origin, "
            "status, resume_from, folder_id, package_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, project_name, project_path, prompt, origin,
             RunStatus.QUEUED, resume_from, folder_id, package_id, time.time()),
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
        return int(cur.lastrowid)


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
        conn.execute(f"UPDATE messages SET {assignments} WHERE id=?",
                     (*fields.values(), message_id))
        conn.commit()


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


# --- Messages from sessions --------------------------------------------------

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


def create_session_message(project: str, agent_name: str, subject: str,
                           body: str) -> int:
    """Record a message a session sent. Its id."""
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO session_messages (project, agent_name, subject, body, "
            "received_at) VALUES (?, ?, ?, ?, ?)",
            (project, agent_name, subject, body, time.time()))
        conn.commit()
        return int(cur.lastrowid)


def get_session_message(message_id: int) -> Optional[SessionMessage]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM session_messages WHERE id=?",
                           (message_id,)).fetchone()
    return SessionMessage(**dict(row)) if row else None


def list_session_messages(agent_name: Optional[str] = None,
                          project: Optional[str] = None,
                          unread_only: bool = False,
                          limit: int = 20) -> list[SessionMessage]:
    """Most recent first. `agent_name` and `project` are matched without
    regard to case, since names are spoken."""
    sql = "SELECT * FROM session_messages WHERE 1=1"
    params: list = []
    if agent_name is not None:
        sql += " AND lower(agent_name) = lower(?)"
        params.append(agent_name)
    if project is not None:
        sql += " AND lower(project) = lower(?)"
        params.append(project)
    if unread_only:
        sql += " AND read_at IS NULL"
    sql += " ORDER BY received_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [SessionMessage(**dict(r)) for r in rows]


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


def sync_agents(running: dict[str, tuple[str, str]], now: float | None = None) -> bool:
    """Make the agents list match what is running now: `running` maps each
    running session's id to its (name, state).

    A session seen for the first time is added INACTIVE; only the user
    activates one (set_agent_active). A known one keeps its `active` and has
    its state refreshed, and its name too unless the user has set one
    (rename_agent), which always wins. One that is no longer running is
    marked ended, not removed: the same session resumed (same session id)
    comes back with its name and activation. Ended ones are dropped after
    AGENT_ENDED_KEEP_SEC. Returns whether the running list changed, so the
    caller only tells the pages when it did.
    """
    now = time.time() if now is None else now
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT session_id, name, state, custom_name, ended_at FROM agents").fetchall()
        known = {r["session_id"]: (r["name"], r["state"]) for r in rows}
        stopped = {r["session_id"] for r in rows if r["ended_at"] is not None}
        # A name the user set is the official one, whatever was derived.
        custom = {r["session_id"]: r["custom_name"] for r in rows if r["custom_name"]}
        running = {sid: (custom.get(sid, name), state)
                   for sid, (name, state) in running.items()}
        added = [(sid, name, state) for sid, (name, state) in running.items()
                 if sid not in known]
        changed = [(name, state, sid) for sid, (name, state) in running.items()
                   if sid in known and (known[sid] != (name, state) or sid in stopped)]
        ended = [(now, sid) for sid in known if sid not in running and sid not in stopped]
        conn.executemany(
            "INSERT INTO agents (session_id, name, state, active) VALUES (?, ?, ?, 0)",
            added)
        conn.executemany(
            "UPDATE agents SET name=?, state=?, ended_at=NULL WHERE session_id=?", changed)
        conn.executemany("UPDATE agents SET ended_at=? WHERE session_id=?", ended)
        conn.execute("DELETE FROM agents WHERE ended_at < ?", (now - AGENT_ENDED_KEEP_SEC,))
        conn.commit()
    return bool(added or changed or ended)


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


def official_names() -> dict[str, str]:
    """session id -> the name the user set, for every agent that has one."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT session_id, custom_name FROM agents WHERE custom_name IS NOT NULL"
        ).fetchall()
    return {r["session_id"]: r["custom_name"] for r in rows}


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
