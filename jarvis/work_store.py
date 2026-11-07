"""The Work tracker's record: each piece of work in progress, and exactly
who is doing each part of it.

A work item wraps one source on this Mac, a ZIP work package or a folder,
named by its path. The path is only ever checked with a stat (is it there,
is it the kind it says); nothing here opens, unpacks or runs it. An item
has a state, pieces (each naming exactly who is on it: an agent by session
id, a JARVIS run, a workflow-monitor workflow, or Tim), its latest update
and its open decisions.

The truth is `work_events`, only ever appended to. `work_items`,
`work_pieces` and `work_decisions` are the snapshot of it, updated in the
SAME transaction as each event is inserted. `create` and `record` are the
only writers. The tables live in JARVIS's one database under their own
schema version (`work_schema`, `run_store.migrate`).

Read-time only, never stored: an open decision shows the item as waiting on
Tim, and a working item with no event for QUIET_AFTER_SEC reads as `quiet`
("no recent update").

Everything an item holds (its title, updates, piece titles, questions) was
written by the agent reporting it: another session's words, shown and
spoken only through the same walls as any other.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jarvis import run_store

# An item's and a piece's state. WAITING is also what an open decision shows.
PLANNED, WORKING, WAITING, BLOCKED, DONE = "planned", "working", "waiting_on_tim", "blocked", "done"
STATES = (PLANNED, WORKING, WAITING, BLOCKED, DONE)

# Where an item's work is: a ZIP work package or a folder, on this Mac.
ZIP, FOLDER = "zip", "folder"
SOURCE_KINDS = (ZIP, FOLDER)
# What the stat found, at read time.
PRESENT, MISSING, WRONG_KIND = "present", "missing", "wrong_kind"

# Who is on a piece. Every kind but Tim names its record by `ref`.
AGENT, RUN, WORKFLOW, TIM = "agent", "run", "workflow", "tim"
WHO_KINDS = (AGENT, RUN, WORKFLOW, TIM)

# What a reporter may send; `decision_made` is his alone (the page, his voice).
UPDATE, PIECE, DECISION_NEEDED, DECISION_MADE = "update", "piece", "decision_needed", "decision_made"
REPORTED_KINDS = (UPDATE, PIECE, DECISION_NEEDED)
CREATED = "created"

QUIET_AFTER_SEC = 10 * 60
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
TITLE_MAX = 120
SUMMARY_MAX = 300
REF_MAX = 128
PATH_MAX = 1024
OPTIONS_MAX = 8
PIECES_MAX = 50
LIST_MAX = 100

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS work_items (
        id            TEXT PRIMARY KEY,
        title         TEXT NOT NULL,
        source_kind   TEXT NOT NULL,
        source_path   TEXT NOT NULL,
        state         TEXT NOT NULL,
        latest_update TEXT NOT NULL DEFAULT '',
        revision      INTEGER NOT NULL,
        created_at    REAL NOT NULL,
        updated_at    REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS work_pieces (
        item_id   TEXT NOT NULL REFERENCES work_items(id),
        key       TEXT NOT NULL,
        title     TEXT NOT NULL,
        who_kind  TEXT NOT NULL,
        who_ref   TEXT,
        state     TEXT NOT NULL,
        position  INTEGER NOT NULL,
        PRIMARY KEY (item_id, key)
    );
    CREATE TABLE IF NOT EXISTS work_decisions (
        item_id        TEXT NOT NULL REFERENCES work_items(id),
        key            TEXT NOT NULL,
        question       TEXT NOT NULL,
        options        TEXT NOT NULL,
        recommendation TEXT NOT NULL DEFAULT '',
        answer         TEXT,
        answered_where TEXT,
        opened_at      REAL NOT NULL,
        answered_at    REAL,
        PRIMARY KEY (item_id, key)
    );
    CREATE TABLE IF NOT EXISTS work_events (
        seq     INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id TEXT NOT NULL REFERENCES work_items(id),
        kind    TEXT NOT NULL,
        summary TEXT NOT NULL,
        detail  TEXT NOT NULL,
        at      REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS work_events_by_item ON work_events (item_id, seq);
"""

# Each step is appended, never changed (run_store.migrate).
_MIGRATIONS = {1: _SCHEMA}

_migrated: set[str] = set()
_migrating = threading.Lock()


class WorkRefused(ValueError):
    """A report or an answer that cannot be recorded, with why."""


@dataclass(frozen=True)
class Who:
    kind: str                 # agent, run, workflow or tim
    ref: Optional[str]        # session id, run id or workflow id; None for Tim


@dataclass(frozen=True)
class Piece:
    key: str
    title: str
    who: Who
    state: str


@dataclass(frozen=True)
class Decision:
    key: str
    question: str
    options: list[str]
    recommendation: str
    opened_at: float


@dataclass(frozen=True)
class WorkEvent:
    item_id: str
    kind: str
    summary: str
    detail: dict
    at: float = 0.0
    seq: int = 0


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str
    source_kind: str
    source_path: str
    source: str               # present, missing or wrong_kind: the stat, now
    state: str                # waiting_on_tim while a decision is open, else as reported
    reported_state: str
    quiet: bool               # working, and nothing reported for QUIET_AFTER_SEC
    latest_update: str
    headline: str             # the one useful update: an open decision's question first
    revision: int
    created_at: float
    updated_at: float
    pieces: list[Piece] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)


def _connect() -> sqlite3.Connection:
    conn = run_store._connect()
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    if path not in _migrated:
        with _migrating:
            if path not in _migrated:
                run_store.migrate(conn, "work_schema", _MIGRATIONS, "work tracker")
                conn.commit()
                _migrated.add(path)
    return conn


def _one_line(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _slug(value, what: str) -> str:
    slug = str(value or "").strip()
    if not SLUG.fullmatch(slug):
        raise WorkRefused(f"{what} is lowercase letters, digits and dashes, up to 64 characters.")
    return slug


def _state(value, what: str = "The state") -> str:
    if value not in STATES:
        raise WorkRefused(f"{what} is one of: {', '.join(STATES)}.")
    return value


# ── the source ──────────────────────────────────────────────────────────

def source_path(kind: str, path: str) -> str:
    """The source's path as it is kept: absolute and normalised. Refused for
    an unknown kind or a path that is not absolute."""
    if kind not in SOURCE_KINDS:
        raise WorkRefused("The source is a zip or a folder.")
    text = str(path or "").strip()
    if not text or "\x00" in text or len(text) > PATH_MAX:
        raise WorkRefused("Give the source's path.")
    expanded = os.path.expanduser(text)
    if not os.path.isabs(expanded):
        raise WorkRefused("Give the source's full path, from /.")
    return os.path.normpath(expanded)


def check_source(kind: str, path: str) -> str:
    """Whether the source is on this Mac and the kind it says, by a stat
    alone: nothing is opened, read or run."""
    try:
        found = os.stat(path)
    except (OSError, ValueError):
        return MISSING
    if kind == FOLDER:
        return PRESENT if stat.S_ISDIR(found.st_mode) else WRONG_KIND
    is_zip = stat.S_ISREG(found.st_mode) and Path(path).suffix.lower() == ".zip"
    return PRESENT if is_zip else WRONG_KIND


# ── who ─────────────────────────────────────────────────────────────────

def who(kind, ref=None) -> Who:
    """Who is on a piece, checked for shape (the caller checks the record
    exists). Tim has no ref; everyone else does."""
    if kind not in WHO_KINDS:
        raise WorkRefused(f"Who is one of: {', '.join(WHO_KINDS)}.")
    if kind == TIM:
        return Who(TIM, None)
    text = str(ref or "").strip()
    if not text or len(text) > REF_MAX or any(c.isspace() for c in text):
        raise WorkRefused(f"A piece on a {kind} names its id.")
    return Who(kind, text)


def parse_who(text: str) -> Who:
    """`agent:<session id>`, `run:<run id>`, `workflow:<workflow id>` or `tim`."""
    kind, _, ref = str(text or "").strip().partition(":")
    return who(kind.lower(), ref or None)


# ── writing ─────────────────────────────────────────────────────────────

def _insert_event(conn, item_id: str, kind: str, summary: str, detail: dict, now: float) -> None:
    conn.execute("INSERT INTO work_events (item_id, kind, summary, detail, at) VALUES (?, ?, ?, ?, ?)",
                 (item_id, kind, summary, json.dumps(detail), now))


def create(title: str, source_kind: str, path: str, state: str = PLANNED) -> WorkItem:
    """Begin tracking a piece of work on a zip or folder that is on this Mac
    now. Returns it."""
    title = _one_line(title, TITLE_MAX)
    if not title:
        raise WorkRefused("A work item needs a title.")
    kept = source_path(source_kind, path)
    found = check_source(source_kind, kept)
    if found != PRESENT:
        raise WorkRefused(f"There is no {source_kind} at {kept}." if found == MISSING
                          else f"{kept} is not a {source_kind}.")
    state = _state(state)
    item_id = "work-" + secrets.token_hex(6)
    now = time.time()
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO work_items (id, title, source_kind, source_path, state, latest_update, "
            "revision, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (item_id, title, source_kind, kept, state, f"{title} is tracked.", now, now))
        _insert_event(conn, item_id, CREATED, f"{title} is tracked.",
                      {"source_kind": source_kind, "source_path": kept, "state": state}, now)
        conn.commit()
    return get(item_id)


def record(event: WorkEvent) -> WorkItem:
    """Append one event and bring the snapshot up to date with it, in one
    transaction. Refused (WorkRefused) for a malformed event, an unknown
    item, a piece or decision it does not have."""
    summary = _one_line(event.summary, SUMMARY_MAX)
    if not summary:
        raise WorkRefused("Every report needs a one-sentence summary.")
    if event.kind not in REPORTED_KINDS + (DECISION_MADE,):
        raise WorkRefused("Kinds are: " + ", ".join(REPORTED_KINDS) + ".")
    detail = event.detail if isinstance(event.detail, dict) else {}
    now = time.time()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM work_items WHERE id=?", (event.item_id,)).fetchone()
        if row is None:
            raise WorkRefused("There is no work item with that id.")
        kept = _apply(conn, event.item_id, event.kind, summary, detail, now)
        conn.execute("UPDATE work_items SET latest_update=?, revision=revision+1, updated_at=? "
                     "WHERE id=?", (summary, now, event.item_id))
        _insert_event(conn, event.item_id, event.kind, summary, kept, now)
        conn.commit()
    return get(event.item_id)


def _apply(conn, item_id: str, kind: str, summary: str, detail: dict, now: float) -> dict:
    """Bring the snapshot up to date with one event; what of its detail is kept."""
    if kind == UPDATE:
        if detail.get("state") is None:
            return {}
        state = _state(detail["state"])
        conn.execute("UPDATE work_items SET state=? WHERE id=?", (state, item_id))
        return {"state": state}
    if kind == PIECE:
        return _apply_piece(conn, item_id, summary, detail)
    if kind == DECISION_NEEDED:
        key = _slug(detail.get("key"), "The decision's key")
        options = detail.get("options") or []
        if not isinstance(options, list) or len(options) > OPTIONS_MAX:
            raise WorkRefused(f"Give at most {OPTIONS_MAX} options, as a list.")
        options = [o for o in (_one_line(o, TITLE_MAX) for o in options) if o]
        recommendation = _one_line(detail.get("recommendation"), SUMMARY_MAX)
        # An answered key is never asked again: its reporter reads the answer
        # by key (`work_report.py answer`), so a new question needs a new key.
        if conn.execute("SELECT 1 FROM work_decisions WHERE item_id=? AND key=? AND answer "
                        "IS NOT NULL", (item_id, key)).fetchone():
            raise WorkRefused(f"Decision {key} was already answered; ask with a new key.")
        conn.execute(
            "INSERT OR REPLACE INTO work_decisions (item_id, key, question, options, "
            "recommendation, opened_at) VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, key, summary, json.dumps(options), recommendation, now))
        return {"key": key, "options": options, "recommendation": recommendation}
    # DECISION_MADE: his answer, from the page or his voice.
    key = _slug(detail.get("key"), "The decision's key")
    answer = _one_line(detail.get("answer"), SUMMARY_MAX)
    if not answer:
        raise WorkRefused("An answer needs words.")
    where = detail.get("where") if detail.get("where") in ("page", "voice") else "page"
    done = conn.execute("UPDATE work_decisions SET answer=?, answered_where=?, answered_at=? "
                        "WHERE item_id=? AND key=? AND answer IS NULL",
                        (answer, where, now, item_id, key)).rowcount
    if not done:
        raise WorkRefused(f"No decision {key} is open.")
    return {"key": key, "answer": answer, "where": where}


def _apply_piece(conn, item_id: str, summary: str, detail: dict) -> dict:
    """Add a piece, or change its title, who is on it or its state."""
    key = _slug(detail.get("key"), "The piece's key")
    have = conn.execute("SELECT * FROM work_pieces WHERE item_id=? AND key=?",
                        (item_id, key)).fetchone()
    kept: dict = {"key": key}
    on = detail.get("who")
    if have is None:
        if not isinstance(on, dict):
            raise WorkRefused("A new piece says who is on it.")
        count = conn.execute("SELECT COUNT(*) FROM work_pieces WHERE item_id=?",
                             (item_id,)).fetchone()[0]
        if count >= PIECES_MAX:
            raise WorkRefused(f"A work item has at most {PIECES_MAX} pieces.")
        person = who(on.get("kind"), on.get("ref"))
        title = _one_line(detail.get("title"), TITLE_MAX) or summary[:TITLE_MAX]
        state = _state(detail.get("state") or PLANNED, "The piece's state")
        conn.execute("INSERT INTO work_pieces (item_id, key, title, who_kind, who_ref, state, "
                     "position) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (item_id, key, title, person.kind, person.ref, state, count))
        return {**kept, "title": title, "who": {"kind": person.kind, "ref": person.ref},
                "state": state}
    if isinstance(on, dict):
        person = who(on.get("kind"), on.get("ref"))
        conn.execute("UPDATE work_pieces SET who_kind=?, who_ref=? WHERE item_id=? AND key=?",
                     (person.kind, person.ref, item_id, key))
        kept["who"] = {"kind": person.kind, "ref": person.ref}
    if detail.get("title"):
        title = _one_line(detail["title"], TITLE_MAX)
        conn.execute("UPDATE work_pieces SET title=? WHERE item_id=? AND key=?", (title, item_id, key))
        kept["title"] = title
    if detail.get("state") is not None:
        state = _state(detail["state"], "The piece's state")
        conn.execute("UPDATE work_pieces SET state=? WHERE item_id=? AND key=?", (state, item_id, key))
        kept["state"] = state
    return kept


# ── reading ─────────────────────────────────────────────────────────────

def _item(conn, row, now: float) -> WorkItem:
    item_id = row["id"]
    pieces = [Piece(r["key"], r["title"], Who(r["who_kind"], r["who_ref"]), r["state"])
              for r in conn.execute("SELECT * FROM work_pieces WHERE item_id=? ORDER BY position",
                                    (item_id,))]
    decisions = [Decision(r["key"], r["question"], json.loads(r["options"]), r["recommendation"],
                          r["opened_at"])
                 for r in conn.execute("SELECT * FROM work_decisions WHERE item_id=? AND answer "
                                       "IS NULL ORDER BY opened_at", (item_id,))]
    reported = row["state"]
    state = WAITING if decisions and reported != DONE else reported
    return WorkItem(
        id=item_id, title=row["title"], source_kind=row["source_kind"],
        source_path=row["source_path"], source=check_source(row["source_kind"], row["source_path"]),
        state=state, reported_state=reported,
        quiet=state == WORKING and now - row["updated_at"] > QUIET_AFTER_SEC,
        latest_update=row["latest_update"],
        headline=decisions[0].question if decisions and reported != DONE else row["latest_update"],
        revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"],
        pieces=pieces, decisions=decisions)


def get(item_id: str) -> Optional[WorkItem]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM work_items WHERE id=?", (item_id,)).fetchone()
        return _item(conn, row, time.time()) if row else None


def listing(limit: int = LIST_MAX) -> list[WorkItem]:
    """Every item: those not done first, then done ones; most recent first."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM work_items ORDER BY state = ?, updated_at DESC LIMIT ?",
            (DONE, limit)).fetchall()
        now = time.time()
        return [_item(conn, r, now) for r in rows]


def _event(row) -> WorkEvent:
    return WorkEvent(row["item_id"], row["kind"], row["summary"], json.loads(row["detail"]),
                     row["at"], row["seq"])


def events(item_id: str, after: int = 0, kinds: Optional[set[str]] = None,
           limit: int = 200) -> list[WorkEvent]:
    """The log, oldest first, from just after `after` (an event's seq)."""
    sql = "SELECT * FROM work_events WHERE item_id=? AND seq>?"
    args: list = [item_id, after]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += sorted(kinds)
    sql += " ORDER BY seq LIMIT ?"
    args.append(limit)
    with closing(_connect()) as conn:
        return [_event(r) for r in conn.execute(sql, args)]


def latest(item_id: str, count: int) -> list[WorkEvent]:
    """An item's newest `count` events, oldest of them first."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM work_events WHERE item_id=? ORDER BY seq DESC LIMIT ?",
                            (item_id, count)).fetchall()
    return [_event(r) for r in reversed(rows)]


def find(reference: str) -> list[WorkItem]:
    """Items by what he called one: its id, or its title as said. Several
    means ask."""
    from jarvis import agent_directory
    text = str(reference or "").strip()
    said = agent_directory.spoken_key(text)
    if not said:
        return []
    items = listing()
    by_id = [i for i in items if i.id == text]
    if by_id:
        return by_id
    exact = [i for i in items if agent_directory.spoken_key(i.title) == said]
    return exact or [i for i in items if said in agent_directory.spoken_key(i.title)]
