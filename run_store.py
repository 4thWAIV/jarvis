"""The single source of truth for Claude Code runs.

Every execution path writes here. The dashboard only reads. State
transitions are persisted before they are announced.
"""

import logging
import sqlite3
import time
import uuid
from contextlib import closing

from data_paths import db_path

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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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

            -- Every running Claude Code session, by name, its state as the
            -- session watcher derives it, and whether the user has activated
            -- it. `active` is his choice alone: a session seen for the first
            -- time is inactive. See sync_agents.
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
                last_message_at REAL NOT NULL DEFAULT 0
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


def create_run(prompt: str, project_name: str, project_path: str,
               origin: str, resume_from: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO runs (id, project_name, project_path, prompt, origin, "
            "status, resume_from, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, project_name, project_path, prompt, origin,
             RunStatus.QUEUED, resume_from, time.time()),
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
              limit: int = 50, before: float | None = None) -> list[dict]:
    sql = "SELECT * FROM runs WHERE 1=1"
    params: list = []
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
    """Every steer is recorded, including the refused ones — 'did you send
    that?' must have an answer."""
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


# --- Agents ----------------------------------------------------------------

def _agent_row(row) -> dict:
    return {"session_id": row["session_id"], "name": row["name"],
            "active": bool(row["active"]), "state": row["state"]}


def sync_agents(running: dict[str, tuple[str, str]]) -> bool:
    """Make the agents list match what is running now: `running` maps each
    running session's id to its (name, state).

    A session seen for the first time is added INACTIVE; only the user
    activates one (set_agent_active). A known one keeps its `active` and has
    its state refreshed, and its name too unless the user has set one
    (rename_agent), which always wins. One that is no longer running is removed.
    Returns whether anything changed, so the caller only tells the pages
    when it did.
    """
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT session_id, name, state, custom_name FROM agents").fetchall()
        known = {r["session_id"]: (r["name"], r["state"]) for r in rows}
        # A name the user set is the official one, whatever was derived.
        custom = {r["session_id"]: r["custom_name"] for r in rows if r["custom_name"]}
        running = {sid: (custom.get(sid, name), state)
                   for sid, (name, state) in running.items()}
        added = [(sid, name, state) for sid, (name, state) in running.items()
                 if sid not in known]
        changed = [(name, state, sid) for sid, (name, state) in running.items()
                   if sid in known and known[sid] != (name, state)]
        ended = [(sid,) for sid in known if sid not in running]
        conn.executemany(
            "INSERT INTO agents (session_id, name, state, active) VALUES (?, ?, ?, 0)",
            added)
        conn.executemany("UPDATE agents SET name=?, state=? WHERE session_id=?", changed)
        conn.executemany("DELETE FROM agents WHERE session_id=?", ended)
        conn.commit()
    return bool(added or changed or ended)


# The states that count as busy: doing something, or waiting on the user.
# Everything else (idle, not yet started, a shell) counts as idle.
BUSY_AGENT_STATES = ("working", "needs_you")


def list_agents() -> list[dict]:
    """In order of activity: busy agents above idle ones, and within each,
    the one most recently sent a message first, then by name."""
    busy = ", ".join("?" * len(BUSY_AGENT_STATES))
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT * FROM agents ORDER BY (state IN ({busy})) DESC, "
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
        conn.execute("UPDATE agents SET active=? WHERE session_id=?",
                     (int(bool(active)), session_id))
        conn.commit()
        row = conn.execute("SELECT * FROM agents WHERE session_id=?",
                           (session_id,)).fetchone()
    return _agent_row(row) if row else None


class DuplicateAgentName(ValueError):
    """Another running agent already goes by that name."""


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
