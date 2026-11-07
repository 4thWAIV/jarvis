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

A piece waiting on Tim may name the answer it waits for (`until`, a
wait_links link: `decision:<key>`, `work:<item>:<key>`,
`workflow:<id>:<key>`, `package:<id>[:<decision>]`) and the state it moves
to then (`then`, working by default). His answer, by any route, moves it in
the same transaction (`end_waits`); one already given moves it when the
record is next read (`sweep_waits`). His answer to an item's last open
decision moves an item reported as waiting on him to working, in the same
transaction.

Read-time only, never stored: an open decision shows the item as waiting on
Tim; every piece done and no decision open shows it as `ready_to_close`; a
decision he answered with no report since and no piece working shows the
item as waiting for its asker to start (`follow_up`), flagged on Blocking as
"answered, no follow-up" after FOLLOW_UP_AFTER_SEC; a wait on him with no
link, open for wait_links.UNLINKED_AFTER_SEC, is flagged as "waiting, no link
to an answer"; a working or verifying item with no event for QUIET_AFTER_SEC reads as
`quiet` ("no recent update"); whether it is in History (closed, or done for
DONE_LINGER_SEC; nothing is ever deleted); what is holding it up
(`blockers`); the group an item is listed under (its area,
else its source folder); how many of its pieces are blocked or waiting on
Tim; and, for each piece, which of the pieces it comes after are not done
yet, with the pieces in the order their dependencies give.

A piece keeps its latest note (the summary of the last report about it),
an optional worker label (the reporting agent's own words for who did it,
for example a coordinator's subagent, shown beside the owner and never in
place of it), the keys of the pieces it comes after, and its evidence (a
commit, an absolute path or an http(s) URL, recorded and never opened).

Everything an item holds (its title, updates, piece titles, questions) was
written by the agent reporting it: another session's words, shown and
spoken only through the same walls as any other.
"""

from __future__ import annotations

import dataclasses
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

from jarvis import run_store, wait_links

# An item's and a piece's state. WAITING is also what an open decision shows.
# VERIFYING sits between working and done: the work is in, being checked.
PLANNED, WORKING, VERIFYING, WAITING, BLOCKED, DONE = (
    "planned", "working", "verifying", "waiting_on_tim", "blocked", "done")
STATES = (PLANNED, WORKING, VERIFYING, WAITING, BLOCKED, DONE)
# Read time only, never reported: every piece done and no decision open.
READY = "ready_to_close"
# The states that should be reporting: silent for QUIET_AFTER_SEC is quiet.
ACTIVE = (WORKING, VERIFYING)

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
# The asker taking its own open decision back, with why: never his answer.
DECISION_WITHDRAWN = "decision_withdrawn"
WITHDRAWN = "withdrawn"
# `closed` moves an item to History for good; nothing more is reported to it.
CLOSED = "closed"
REPORTED_KINDS = (UPDATE, PIECE, DECISION_NEEDED, DECISION_WITHDRAWN, CLOSED)
CREATED = "created"

# What an item is listed under: the area it was given, else its source folder.
BY_AREA, BY_FOLDER = "area", "folder"

# A piece's evidence, told apart by its shape alone; nothing is opened.
COMMIT, PATH, URL = "commit", "path", "url"
_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")
_URL = re.compile(r"https?://[^\s]{1,1000}", re.IGNORECASE)

QUIET_AFTER_SEC = 10 * 60
# A done item stays on the main list this long, then moves to History.
DONE_LINGER_SEC = 5 * 60
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
TITLE_MAX = 120
SUMMARY_MAX = 300
REF_MAX = 128
PATH_MAX = 1024
OPTIONS_MAX = 8
ANSWERED_SHOWN = 20
# A package is named as the packages are (its id, or its own name).
PACKAGE_REF = re.compile(r"[A-Za-z0-9._-]{1,128}")

# What can hold an item up, in the order they are listed: his decisions
# first, the item itself said to be blocked, its blocked pieces, its pieces
# waiting on him, then pieces waiting on other pieces not yet done.
# A decision he answered that nobody has acted on for FOLLOW_UP_AFTER_SEC
# comes straight after his open decisions; the item itself reported as
# waiting on him (with no decision open) after it being blocked.
DECISION_BLOCKER, NO_FOLLOW_UP, ITEM_BLOCKED, ITEM_WAITING = (
    "decision", "no_follow_up", "blocked", "item_waiting")
PIECE_BLOCKED, PIECE_WAITING, PIECE_AFTER = "piece_blocked", "piece_waiting", "piece_after"
BLOCKER_KINDS = (DECISION_BLOCKER, NO_FOLLOW_UP, ITEM_BLOCKED, ITEM_WAITING, PIECE_BLOCKED,
                 PIECE_WAITING, PIECE_AFTER)
FOLLOW_UP_AFTER_SEC = 30 * 60
PIECES_MAX = 50
AREA_MAX = 60
WORKER_MAX = 80
EVIDENCE_MAX = 20
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

# 2: an item's area; a piece's latest note (filled from its last report),
# worker label, the pieces it comes after and its evidence.
_PIECE_DETAIL = """
    ALTER TABLE work_items  ADD COLUMN area       TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_pieces ADD COLUMN note       TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_pieces ADD COLUMN note_at    REAL;
    ALTER TABLE work_pieces ADD COLUMN worker     TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_pieces ADD COLUMN after_keys TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE work_pieces ADD COLUMN evidence   TEXT NOT NULL DEFAULT '[]';
    UPDATE work_pieces SET
        note = COALESCE((SELECT e.summary FROM work_events e WHERE e.item_id = work_pieces.item_id
                         AND e.kind = 'piece' AND json_extract(e.detail, '$.key') = work_pieces.key
                         ORDER BY e.seq DESC LIMIT 1), ''),
        note_at = (SELECT e.at FROM work_events e WHERE e.item_id = work_pieces.item_id
                   AND e.kind = 'piece' AND json_extract(e.detail, '$.key') = work_pieces.key
                   ORDER BY e.seq DESC LIMIT 1);
"""

# Each step is appended, never changed (run_store.migrate).
# 3: the package a decision asks him to look at, by its id or name; who
# asked it (the access key's label) and whether that asker withdrew it.
# And when an item was closed (it moves to History; nothing is deleted).
_DECISION_PACKAGE = """
    ALTER TABLE work_decisions ADD COLUMN package TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_items ADD COLUMN closed_at REAL;
    ALTER TABLE work_decisions ADD COLUMN asked_by TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_decisions ADD COLUMN withdrawn_at REAL;
    ALTER TABLE work_decisions ADD COLUMN withdrawn_reason TEXT;
"""

# 4: what a piece waiting on him waits for (a wait_links link) and the state
# it moves to then, and since when it and an item have waited on him; when
# an item last had a report from its agents (not his answers).
_WAIT_LINKS = """
    ALTER TABLE work_pieces ADD COLUMN until         TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_pieces ADD COLUMN then_state    TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_pieces ADD COLUMN waiting_since REAL;
    ALTER TABLE work_items  ADD COLUMN waiting_since REAL;
    ALTER TABLE work_items  ADD COLUMN reported_at   REAL;
    UPDATE work_pieces SET waiting_since = note_at WHERE state = 'waiting_on_tim';
    UPDATE work_items SET waiting_since = updated_at WHERE state = 'waiting_on_tim';
    UPDATE work_items SET reported_at = COALESCE((SELECT MAX(e.at) FROM work_events e
        WHERE e.item_id = work_items.id AND e.kind <> 'decision_made'), created_at);
"""

_MIGRATIONS = {1: _SCHEMA, 2: _PIECE_DETAIL, 3: _DECISION_PACKAGE, 4: _WAIT_LINKS}

_migrated: set[str] = set()
_migrating = threading.Lock()


class WorkRefused(ValueError):
    """A report or an answer that cannot be recorded, with why."""


@dataclass(frozen=True)
class Who:
    kind: str                 # agent, run, workflow or tim
    ref: Optional[str]        # session id, run id or workflow id; None for Tim


@dataclass(frozen=True)
class Evidence:
    kind: str                 # commit, path or url
    value: str


@dataclass(frozen=True)
class Piece:
    key: str
    title: str
    who: Who
    state: str
    note: str = ""            # the summary of the latest report about it
    note_at: Optional[float] = None
    worker: str = ""          # the reporter's own words for who did it; untrusted
    after: list[str] = field(default_factory=list)       # keys of the pieces it comes after
    waits_for: list[str] = field(default_factory=list)   # of those, the ones not done yet
    evidence: list[Evidence] = field(default_factory=list)
    # Read time only: while working or verifying, when it goes quiet with no
    # report after its latest note. None in any other state.
    quiet_at: Optional[float] = None
    until: str = ""           # waiting on him: the answer it waits for (a link), or ""
    then: str = ""            # the state it moves to on that answer; "" is working
    waiting_since: Optional[float] = None
    # Read time only: waiting on him with no link, for UNLINKED_AFTER_SEC.
    unlinked: bool = False


@dataclass(frozen=True)
class Decision:
    key: str
    question: str
    options: list[str]
    recommendation: str
    opened_at: float
    package: str = ""         # a package to look at before answering: its id or name


@dataclass(frozen=True)
class Answered:
    """A decision that is no longer open: answered by him, or withdrawn by
    the agent that asked it."""
    key: str
    question: str
    answer: str               # his answer, or the asker's reason for withdrawing it
    where: str                # page or voice; `withdrawn` when the asker took it back
    answered_at: float
    package: str = ""


@dataclass(frozen=True)
class Blocker:
    """One thing holding the item up, worked out at read time."""
    kind: str                 # one of BLOCKER_KINDS
    key: str                  # the decision's or piece's key; "" for the item itself
    title: str                # the question, the piece's title, or the item's title
    text: str = ""            # the piece's latest note, the item's latest update, the asker
    waits_for: list[str] = field(default_factory=list)   # piece_after: the keys not done
    # A wait on him with no link to an answer, open for UNLINKED_AFTER_SEC.
    unlinked: bool = False


@dataclass(frozen=True)
class FollowUp:
    """He answered a decision and nothing has been reported since, with no
    piece working: waiting for the decision's asker to start."""
    key: str
    question: str
    asker: str                # the asker's access-key label, or "" when unknown
    answered_at: float
    overdue: bool             # FOLLOW_UP_AFTER_SEC gone with no report


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
    quiet: bool               # working or verifying, and nothing reported for QUIET_AFTER_SEC
    latest_update: str
    headline: str             # the one useful update: an open decision's question first
    revision: int
    created_at: float
    updated_at: float
    area: str = ""            # as reported; untrusted
    grouped_by: str = BY_FOLDER   # area, or folder when it has none
    group: str = ""           # the area, or the source's folder path
    blocked_pieces: int = 0
    waiting_pieces: int = 0
    pieces: list[Piece] = field(default_factory=list)   # in dependency order
    decisions: list[Decision] = field(default_factory=list)
    closed_at: Optional[float] = None
    history: bool = False     # closed, or done for DONE_LINGER_SEC: listed under History
    answered: list[Answered] = field(default_factory=list)  # newest first
    blockers: list[Blocker] = field(default_factory=list)   # in BLOCKER_KINDS order
    # Read time only: as a piece's, from its latest report.
    quiet_at: Optional[float] = None
    follow_up: Optional[FollowUp] = None


@dataclass(frozen=True)
class WorkGroup:
    grouped_by: str           # area or folder
    name: str                 # the area, or the folder's path
    items: list[WorkItem]


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


def ready() -> None:
    """This module's tables exist and are migrated (another store's
    transaction may write them)."""
    with closing(_connect()):
        pass


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


# ── evidence ────────────────────────────────────────────────────────────

def evidence(value) -> Evidence:
    """One piece of evidence, told apart by its shape: a commit (7 to 40 hex
    digits), an http(s) URL, or an absolute path. Recorded as given (a path
    normalised); nothing is opened, fetched or checked out."""
    text = str(value or "").strip()
    if not text or len(text) > PATH_MAX or any(c in text for c in "\x00\n\r"):
        raise WorkRefused("Evidence is a commit, an absolute path or an http(s) URL.")
    if _COMMIT.fullmatch(text):
        return Evidence(COMMIT, text.lower())
    if _URL.fullmatch(text):
        return Evidence(URL, text)
    expanded = os.path.expanduser(text)
    if os.path.isabs(expanded):
        return Evidence(PATH, os.path.normpath(expanded))
    raise WorkRefused("Evidence is a commit, an absolute path or an http(s) URL.")


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


def create(title: str, source_kind: str, path: str, state: str = PLANNED,
           area: str = "") -> WorkItem:
    """Begin tracking a piece of work on a zip or folder that is on this Mac
    now, under an area if it is given one. Returns it."""
    title = _one_line(title, TITLE_MAX)
    if not title:
        raise WorkRefused("A work item needs a title.")
    kept = source_path(source_kind, path)
    found = check_source(source_kind, kept)
    if found != PRESENT:
        raise WorkRefused(f"There is no {source_kind} at {kept}." if found == MISSING
                          else f"{kept} is not a {source_kind}.")
    state = _state(state)
    area = _one_line(area, AREA_MAX)
    item_id = "work-" + secrets.token_hex(6)
    now = time.time()
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO work_items (id, title, source_kind, source_path, state, latest_update, "
            "revision, created_at, updated_at, area) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (item_id, title, source_kind, kept, state, f"{title} is tracked.", now, now, area))
        _insert_event(conn, item_id, CREATED, f"{title} is tracked.",
                      {"source_kind": source_kind, "source_path": kept, "state": state,
                       "area": area}, now)
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
        if row["closed_at"] is not None and event.kind != DECISION_MADE:
            raise WorkRefused("That work item is closed; it is kept in History. Start a new one.")
        kept = _apply(conn, event.item_id, event.kind, summary, detail, now)
        conn.execute("UPDATE work_items SET latest_update=?, revision=revision+1, updated_at=? "
                     "WHERE id=?", (summary, now, event.item_id))
        if event.kind in REPORTED_KINDS:
            conn.execute("UPDATE work_items SET reported_at=? WHERE id=?", (now, event.item_id))
        moved = wait_links.Settled()
        if event.kind == DECISION_MADE:
            kept, moved = _answered(conn, event.item_id, kept, now)
        _insert_event(conn, event.item_id, event.kind, summary, kept, now)
        conn.commit()
    wait_links.tell(moved)
    return get(event.item_id)


def _answered(conn, item_id: str, kept: dict, now: float):
    """His answer moves on what waited for it, in this transaction: the item
    itself, reported as waiting on him, to working once no decision is left
    open (`state` in what is kept), and every piece or workflow wait linked
    to it. (what is kept, the records moved)."""
    still = conn.execute("SELECT 1 FROM work_decisions WHERE item_id=? AND answer IS NULL AND "
                         "withdrawn_at IS NULL", (item_id,)).fetchone()
    if still is None and conn.execute("SELECT 1 FROM work_items WHERE id=? AND state=?",
                                      (item_id, WAITING)).fetchone():
        _set_item_state(conn, item_id, WORKING)
        kept = {**kept, "state": WORKING}
    found = wait_links.answer(wait_links.WORK, {item_id}, kept["key"], kept.get("where", "page"),
                              now)
    return kept, wait_links.settle(conn, found, now)


def _set_item_state(conn, item_id: str, state: str) -> None:
    """An item's reported state, and since when it has waited on him."""
    conn.execute("UPDATE work_items SET state=?, waiting_since=CASE WHEN ?=? THEN "
                 "COALESCE(CASE WHEN state=? THEN waiting_since END, ?) END WHERE id=?",
                 (state, state, WAITING, WAITING, time.time(), item_id))


def _apply(conn, item_id: str, kind: str, summary: str, detail: dict, now: float) -> dict:
    """Bring the snapshot up to date with one event; what of its detail is kept."""
    if kind == UPDATE:
        kept: dict = {}
        if detail.get("state") is not None:
            kept["state"] = _state(detail["state"])
            _set_item_state(conn, item_id, kept["state"])
        if detail.get("area") is not None:
            # An empty area takes it back to its source folder's group.
            kept["area"] = _one_line(detail["area"], AREA_MAX)
            conn.execute("UPDATE work_items SET area=? WHERE id=?", (kept["area"], item_id))
        return kept
    if kind == PIECE:
        return _apply_piece(conn, item_id, summary, detail, now)
    if kind == CLOSED:
        conn.execute("UPDATE work_items SET closed_at=? WHERE id=?", (now, item_id))
        return {}
    if kind == DECISION_NEEDED:
        key = _slug(detail.get("key"), "The decision's key")
        options = detail.get("options") or []
        if not isinstance(options, list) or len(options) > OPTIONS_MAX:
            raise WorkRefused(f"Give at most {OPTIONS_MAX} options, as a list.")
        options = [o for o in (_one_line(o, TITLE_MAX) for o in options) if o]
        recommendation = _one_line(detail.get("recommendation"), SUMMARY_MAX)
        package = str(detail.get("package") or "").strip()
        if package and not PACKAGE_REF.fullmatch(package):
            raise WorkRefused("A package is named by its id or its name: letters, digits, "
                              "dots, dashes and underscores.")
        # An answered key is never asked again: its reporter reads the answer
        # by key (`work_report.py answer`), so a new question needs a new key.
        if conn.execute("SELECT 1 FROM work_decisions WHERE item_id=? AND key=? AND (answer "
                        "IS NOT NULL OR withdrawn_at IS NOT NULL)", (item_id, key)).fetchone():
            raise WorkRefused(f"Decision {key} was already answered or withdrawn; ask with a new key.")
        conn.execute(
            "INSERT OR REPLACE INTO work_decisions (item_id, key, question, options, "
            "recommendation, opened_at, package, asked_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, key, summary, json.dumps(options), recommendation, now, package,
             str(detail.get("asked_by") or "")))
        return {"key": key, "options": options, "recommendation": recommendation,
                "package": package}
    if kind == DECISION_WITHDRAWN:
        return _withdraw(conn, item_id, summary, detail, now)
    # DECISION_MADE: his answer, from the page or his voice.
    key = _slug(detail.get("key"), "The decision's key")
    answer = _one_line(detail.get("answer"), SUMMARY_MAX)
    if not answer:
        raise WorkRefused("An answer needs words.")
    where = detail.get("where") if detail.get("where") in ("page", "voice") else "page"
    done = conn.execute("UPDATE work_decisions SET answer=?, answered_where=?, answered_at=? "
                        "WHERE item_id=? AND key=? AND answer IS NULL AND withdrawn_at IS NULL",
                        (answer, where, now, item_id, key)).rowcount
    if not done:
        raise WorkRefused(f"No decision {key} is open.")
    return {"key": key, "answer": answer, "where": where}


def _withdraw(conn, item_id: str, reason: str, detail: dict, now: float) -> dict:
    """The asker takes its own open decision back; the summary is why. Only
    the asker may: the access key that asked it (`by`, set by the route,
    never by the report), or any reporter for a decision asked before
    askers were recorded."""
    key = _slug(detail.get("key"), "The decision's key")
    row = conn.execute("SELECT asked_by FROM work_decisions WHERE item_id=? AND key=? AND answer "
                       "IS NULL AND withdrawn_at IS NULL", (item_id, key)).fetchone()
    if row is None:
        raise WorkRefused(f"No decision {key} is open.")
    if row["asked_by"] and row["asked_by"] != str(detail.get("by") or ""):
        raise WorkRefused(f"Only the agent that asked decision {key} can withdraw it.")
    conn.execute("UPDATE work_decisions SET withdrawn_at=?, withdrawn_reason=? WHERE item_id=? "
                 "AND key=?", (now, reason, item_id, key))
    return {"key": key}


def _after(conn, item_id: str, key: str, value) -> list[str]:
    """The keys a piece comes after: pieces this item has, never itself, and
    never one that already comes after it (no circle)."""
    keys = value.split(",") if isinstance(value, str) else value
    if not isinstance(keys, list) or len(keys) > PIECES_MAX:
        raise WorkRefused("Give the pieces it comes after as a list of their keys.")
    wanted: list[str] = []
    for one in keys:
        if str(one or "").strip():
            k = _slug(str(one).strip(), "Each piece it comes after")
            if k not in wanted:
                wanted.append(k)
    graph = {r["key"]: json.loads(r["after_keys"]) for r in
             conn.execute("SELECT key, after_keys FROM work_pieces WHERE item_id=?", (item_id,))}
    for k in wanted:
        if k == key:
            raise WorkRefused("A piece cannot come after itself.")
        if k not in graph:
            raise WorkRefused(f"This item has no piece {k}.")
    graph[key] = wanted
    seen, todo = set(), list(wanted)
    while todo:
        k = todo.pop()
        if k == key:
            raise WorkRefused(f"Piece {key} would come after itself, through the pieces it names.")
        if k not in seen:
            seen.add(k)
            todo.extend(graph.get(k, []))
    return wanted


def _add_evidence(have: str, value) -> list[dict]:
    """The piece's evidence with the new added, each once, oldest first."""
    given = value if isinstance(value, list) else [value]
    kept = json.loads(have or "[]")
    for one in given:
        found = dataclasses.asdict(evidence(one))
        if found not in kept:
            kept.append(found)
    if len(kept) > EVIDENCE_MAX:
        raise WorkRefused(f"A piece keeps at most {EVIDENCE_MAX} pieces of evidence.")
    return kept


def _apply_piece(conn, item_id: str, summary: str, detail: dict, now: float) -> dict:
    """Add a piece, or change its title, who is on it, its state, its worker
    label, the pieces it comes after or its evidence. The report's summary is
    the piece's latest note."""
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
                     "position, note, note_at, waiting_since) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (item_id, key, title, person.kind, person.ref, state, count, summary, now,
                      now if state == WAITING else None))
        kept.update({"title": title, "who": {"kind": person.kind, "ref": person.ref},
                     "state": state})
    else:
        conn.execute("UPDATE work_pieces SET note=?, note_at=? WHERE item_id=? AND key=?",
                     (summary, now, item_id, key))
        kept.update(_change_piece(conn, item_id, key, detail, on))
    if detail.get("worker") is not None:
        # The reporter's own words; an empty label takes it off.
        kept["worker"] = _one_line(detail["worker"], WORKER_MAX)
        conn.execute("UPDATE work_pieces SET worker=? WHERE item_id=? AND key=?",
                     (kept["worker"], item_id, key))
    if detail.get("after") is not None:
        kept["after"] = _after(conn, item_id, key, detail["after"])
        conn.execute("UPDATE work_pieces SET after_keys=? WHERE item_id=? AND key=?",
                     (json.dumps(kept["after"]), item_id, key))
    if detail.get("evidence"):
        have = conn.execute("SELECT evidence FROM work_pieces WHERE item_id=? AND key=?",
                            (item_id, key)).fetchone()["evidence"]
        every = _add_evidence(have, detail["evidence"])
        conn.execute("UPDATE work_pieces SET evidence=? WHERE item_id=? AND key=?",
                     (json.dumps(every), item_id, key))
        kept["evidence"] = every
    kept.update(_piece_link(conn, item_id, key, detail))
    return kept


def _piece_link(conn, item_id: str, key: str, detail: dict) -> dict:
    """What a piece waiting on him waits for (`until`, a link) and the state
    it moves to then (`then`). A piece not waiting on him waits for nothing.
    A decision of its own item it names must be one the item has asked."""
    state = conn.execute("SELECT state FROM work_pieces WHERE item_id=? AND key=?",
                         (item_id, key)).fetchone()["state"]
    if state != WAITING:
        conn.execute("UPDATE work_pieces SET until='', then_state='' WHERE item_id=? AND key=?",
                     (item_id, key))
        if detail.get("until") or detail.get("then"):
            raise WorkRefused("Only a piece waiting on him (--state waiting_on_tim) waits for "
                              "an answer.")
        return {}
    kept: dict = {}
    if detail.get("until") is not None:
        try:
            link = wait_links.parse(detail["until"])
        except wait_links.LinkRefused as e:
            raise WorkRefused(str(e)) from None
        if str(detail["until"]).strip() and link is None:
            raise WorkRefused("--until names the answer it waits for: decision:<key>, "
                              "work:<item>:<key>, workflow:<id>:<key> or package:<id>.")
        if link is not None and link.kind == wait_links.DECISION and not conn.execute(
                "SELECT 1 FROM work_decisions WHERE item_id=? AND key=?",
                (item_id, link.key)).fetchone():
            raise WorkRefused(f"This item has asked no decision {link.key}; ask it with "
                              f"decision first, then wait on it.")
        kept["until"] = str(link) if link else ""
        conn.execute("UPDATE work_pieces SET until=? WHERE item_id=? AND key=?",
                     (kept["until"], item_id, key))
    if detail.get("then") is not None:
        then = _state(detail["then"], "The state it moves to then") if detail["then"] else ""
        if then == WAITING:
            raise WorkRefused("A piece waiting on him moves on to another state.")
        kept["then"] = then
        conn.execute("UPDATE work_pieces SET then_state=? WHERE item_id=? AND key=?",
                     (then, item_id, key))
    return kept


def _change_piece(conn, item_id: str, key: str, detail: dict, on) -> dict:
    """Change a piece's who, title or state, as a report about it says."""
    kept: dict = {}
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
        conn.execute("UPDATE work_pieces SET waiting_since=CASE WHEN ?=? THEN "
                     "COALESCE(CASE WHEN state=? THEN waiting_since END, ?) END, state=? "
                     "WHERE item_id=? AND key=?",
                     (state, WAITING, WAITING, time.time(), state, item_id, key))
        kept["state"] = state
    return kept


# ── reading ─────────────────────────────────────────────────────────────

def _in_order(pieces: list[Piece]) -> list[Piece]:
    """The pieces in the order their dependencies give: each after every
    piece it names, otherwise in the order they were reported."""
    left = list(pieces)
    placed: list[Piece] = []
    keys: set[str] = set()
    while left:
        # The first piece whose every dependency is placed; the record keeps
        # no circle, so one always is (and one is taken anyway if not).
        ready = next((p for p in left if all(k in keys for k in p.after)), left[0])
        left.remove(ready)
        placed.append(ready)
        keys.add(ready.key)
    return placed


def quiet_at(state: str, heard_at: Optional[float]) -> Optional[float]:
    """When something working or verifying goes quiet: QUIET_AFTER_SEC after
    it was last heard from (at once if never). None in any other state."""
    if state not in ACTIVE:
        return None
    return (heard_at or 0.0) + QUIET_AFTER_SEC


def _unlinked_for(link: str, since: Optional[float], now: float) -> bool:
    """A wait on him with no link to an answer, open UNLINKED_AFTER_SEC."""
    return not link and since is not None and now - since > wait_links.UNLINKED_AFTER_SEC


def _pieces(conn, item_id: str, now: Optional[float] = None) -> list[Piece]:
    now = time.time() if now is None else now
    rows = conn.execute("SELECT * FROM work_pieces WHERE item_id=? ORDER BY position",
                        (item_id,)).fetchall()
    states = {r["key"]: r["state"] for r in rows}
    pieces = []
    for r in rows:
        after = json.loads(r["after_keys"])
        pieces.append(Piece(
            r["key"], r["title"], Who(r["who_kind"], r["who_ref"]), r["state"],
            note=r["note"], note_at=r["note_at"], worker=r["worker"], after=after,
            waits_for=[k for k in after if states.get(k) != DONE],
            evidence=[Evidence(e["kind"], e["value"]) for e in json.loads(r["evidence"])],
            quiet_at=quiet_at(r["state"], r["note_at"]), until=r["until"],
            then=r["then_state"], waiting_since=r["waiting_since"],
            unlinked=r["state"] == WAITING and _unlinked_for(r["until"], r["waiting_since"], now)))
    return _in_order(pieces)


def _source_folder(kind: str, path: str) -> str:
    """The folder an item is grouped under when it has no area: the folder
    itself, or the folder a zip is in."""
    return path if kind == FOLDER else os.path.dirname(path)


def project_of(item: WorkItem) -> str:
    """The project an item's agents work in, as the talk-to-jarvis folders
    name it: the last name of its source folder (or of the folder its zip
    is in)."""
    return os.path.basename(_source_folder(item.source_kind, item.source_path).rstrip("/"))


def _item(conn, row, now: float) -> WorkItem:
    item_id = row["id"]
    pieces = _pieces(conn, item_id, now)
    decisions = [Decision(r["key"], r["question"], json.loads(r["options"]), r["recommendation"],
                          r["opened_at"], r["package"])
                 for r in conn.execute("SELECT * FROM work_decisions WHERE item_id=? AND answer "
                                       "IS NULL AND withdrawn_at IS NULL ORDER BY opened_at",
                                       (item_id,))]
    answered = [Answered(r["key"], r["question"],
                         r["answer"] if r["answer"] is not None else r["withdrawn_reason"],
                         r["answered_where"] if r["answer"] is not None else WITHDRAWN,
                         r["answered_at"] if r["answer"] is not None else r["withdrawn_at"],
                         r["package"])
                for r in conn.execute("SELECT * FROM work_decisions WHERE item_id=? AND (answer "
                                      "IS NOT NULL OR withdrawn_at IS NOT NULL) ORDER BY "
                                      "COALESCE(answered_at, withdrawn_at) DESC LIMIT ?",
                                      (item_id, ANSWERED_SHOWN))]
    reported = row["state"]
    state = (WAITING if decisions and reported != DONE
             else READY if _all_done(row, pieces, decisions) else reported)
    area = row["area"]
    follow_up = _follow_up(conn, row, pieces, decisions, now)
    return WorkItem(
        id=item_id, title=row["title"], source_kind=row["source_kind"],
        source_path=row["source_path"], source=check_source(row["source_kind"], row["source_path"]),
        state=state, reported_state=reported,
        quiet=state in ACTIVE and now > quiet_at(state, row["updated_at"]),
        latest_update=row["latest_update"],
        headline=decisions[0].question if decisions and reported != DONE else row["latest_update"],
        revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"],
        area=area, grouped_by=BY_AREA if area else BY_FOLDER,
        group=area or _source_folder(row["source_kind"], row["source_path"]),
        blocked_pieces=sum(p.state == BLOCKED for p in pieces),
        waiting_pieces=sum(p.state == WAITING for p in pieces),
        closed_at=row["closed_at"], history=_in_history(row, now),
        pieces=pieces, decisions=decisions, answered=answered,
        blockers=_blockers(row, pieces, decisions, follow_up, now),
        quiet_at=None if row["closed_at"] is not None else quiet_at(state, row["updated_at"]),
        follow_up=follow_up)


def _all_done(row, pieces: list[Piece], decisions: list[Decision]) -> bool:
    """Ready to close: it has pieces, every one done, no decision open, and
    it is neither reported done nor closed."""
    return (bool(pieces) and all(p.state == DONE for p in pieces) and not decisions
            and row["state"] != DONE and row["closed_at"] is None)


def asker_label(asked_by: str) -> str:
    """Who asked a decision, as shown: its access key's label, else ""."""
    return asked_by[4:] if str(asked_by or "").startswith("key:") else ""


def _follow_up(conn, row, pieces: list[Piece], decisions: list[Decision],
               now: float) -> Optional[FollowUp]:
    """His latest answer, when nothing has been reported since and no piece
    is working: the item waits for the decision's asker to start."""
    if (decisions or row["closed_at"] is not None or row["state"] == DONE
            or any(p.state == WORKING for p in pieces)):
        return None
    last = conn.execute("SELECT key, question, asked_by, answered_at FROM work_decisions WHERE "
                        "item_id=? AND answer IS NOT NULL ORDER BY answered_at DESC LIMIT 1",
                        (row["id"],)).fetchone()
    if last is None or (row["reported_at"] or 0) >= last["answered_at"]:
        return None
    return FollowUp(last["key"], last["question"], asker_label(last["asked_by"]),
                    last["answered_at"], now - last["answered_at"] > FOLLOW_UP_AFTER_SEC)


def _in_history(row, now: float) -> bool:
    return (row["closed_at"] is not None
            or (row["state"] == DONE and now - row["updated_at"] > DONE_LINGER_SEC))


def _blockers(row, pieces: list[Piece], decisions: list[Decision],
              follow_up: Optional[FollowUp], now: float) -> list[Blocker]:
    """Everything holding the item up, in BLOCKER_KINDS order. A done item
    has none."""
    if row["state"] == DONE or row["closed_at"] is not None:
        return []
    found = [Blocker(DECISION_BLOCKER, d.key, d.question) for d in decisions]
    if follow_up is not None and follow_up.overdue:
        found.append(Blocker(NO_FOLLOW_UP, follow_up.key, follow_up.question, follow_up.asker))
    if row["state"] == BLOCKED:
        found.append(Blocker(ITEM_BLOCKED, "", row["title"], row["latest_update"]))
    if row["state"] == WAITING and not decisions:
        # Waiting on him with no decision open: a wait with no link to an answer.
        found.append(Blocker(ITEM_WAITING, "", row["title"], row["latest_update"],
                             unlinked=_unlinked_for("", row["waiting_since"], now)))
    found += [Blocker(PIECE_BLOCKED, p.key, p.title, p.note) for p in pieces if p.state == BLOCKED]
    found += [Blocker(PIECE_WAITING, p.key, p.title, p.note, unlinked=p.unlinked)
              for p in pieces if p.state == WAITING]
    found += [Blocker(PIECE_AFTER, p.key, p.title, p.note, p.waits_for) for p in pieces
              if p.waits_for and p.state != DONE]
    return found


def get(item_id: str) -> Optional[WorkItem]:
    wait_links.sweep()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM work_items WHERE id=?", (item_id,)).fetchone()
        return _item(conn, row, time.time()) if row else None


# Which items are in History, in SQL: `_in_history`'s rule, with its `now`.
_HISTORY_SQL = "(closed_at IS NOT NULL OR (state = ? AND updated_at < ?))"


def listing(limit: int = LIST_MAX, history: bool = False) -> list[WorkItem]:
    """The open items (not closed, and not done for DONE_LINGER_SEC): those
    not done first, then done ones, most recent first. With `history`, the
    items in History instead, the most recently closed or finished first."""
    now = time.time()
    if history:
        sql = (f"SELECT * FROM work_items WHERE {_HISTORY_SQL} "
               "ORDER BY COALESCE(closed_at, updated_at) DESC LIMIT ?")
        args: tuple = (DONE, now - DONE_LINGER_SEC, limit)
    else:
        sql = (f"SELECT * FROM work_items WHERE NOT {_HISTORY_SQL} "
               "ORDER BY state = ?, updated_at DESC LIMIT ?")
        args = (DONE, now - DONE_LINGER_SEC, DONE, limit)
    wait_links.sweep()
    with closing(_connect()) as conn:
        return [_item(conn, r, now) for r in conn.execute(sql, args).fetchall()]


def grouped(items: list[WorkItem]) -> list[WorkGroup]:
    """The items under their groups, each group where its first item comes
    and its items in the order given."""
    groups: dict[tuple[str, str], list[WorkItem]] = {}
    for item in items:
        groups.setdefault((item.grouped_by, item.group), []).append(item)
    return [WorkGroup(by, name, members) for (by, name), members in groups.items()]


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
    items = listing() + listing(history=True)
    by_id = [i for i in items if i.id == text]
    if by_id:
        return by_id
    exact = [i for i in items if agent_directory.spoken_key(i.title) == said]
    return exact or [i for i in items if said in agent_directory.spoken_key(i.title)]


# ── his answers, and what waits for them ────────────────────────────────

def decision_answer(conn, item_id: str, key: Optional[str]) -> "Optional[wait_links.Answer]":
    """His answer to this item's decision `key`, as what it ends, or None
    while he has not given one."""
    if key is None:
        return None
    try:
        row = conn.execute("SELECT answered_where, answered_at FROM work_decisions WHERE "
                           "item_id=? AND key=? AND answer IS NOT NULL", (item_id, key)).fetchone()
    except sqlite3.OperationalError:
        return None
    return (wait_links.answer(wait_links.WORK, {item_id}, key, row["answered_where"],
                              row["answered_at"]) if row else None)


def _linked_pieces(conn):
    """(item id, piece key, link, then) for every piece waiting on him with
    a link, on an item not closed."""
    rows = conn.execute("SELECT p.item_id, p.key, p.until, p.then_state FROM work_pieces p "
                        "JOIN work_items i ON i.id = p.item_id WHERE p.state=? AND p.until<>'' "
                        "AND i.closed_at IS NULL", (WAITING,)).fetchall()
    for r in rows:
        try:
            link = wait_links.parse(r["until"])
        except wait_links.LinkRefused:
            continue
        if link is not None:
            yield r["item_id"], r["key"], link, r["then_state"] or WORKING


def _note(conn, item_id: str, kind: str, summary: str, detail: dict, now: float) -> None:
    """JARVIS's own entry in an item's log, as a change it made on his
    answer: the item's update and revision move, its agents' last report
    (reported_at) does not."""
    conn.execute("UPDATE work_items SET latest_update=?, revision=revision+1, updated_at=? "
                 "WHERE id=?", (summary, now, item_id))
    _insert_event(conn, item_id, kind, summary, detail, now)


def _end_piece_wait(conn, item_id: str, key: str, link, then: str,
                    found: "wait_links.Answer", now: float) -> None:
    """A piece waiting on him moves to its next state on his answer, which
    the report records as his, with when and how."""
    summary = _one_line(f"{found.said()} ({link}); back to {then}.", SUMMARY_MAX)
    conn.execute("UPDATE work_pieces SET state=?, until='', then_state='', waiting_since=NULL, "
                 "note=?, note_at=? WHERE item_id=? AND key=?",
                 (then, summary, now, item_id, key))
    _note(conn, item_id, PIECE, summary,
          {"key": key, "state": then,
           "wait_ended": {"link": str(link), "by": "tim", "how": found.how,
                          "answered_at": found.at}}, now)


def end_waits(conn, found: "wait_links.Answer", now: float) -> set:
    """Move every piece waiting on him that `found` answers, in the caller's
    transaction. The items it moved."""
    moved = set()
    for item_id, key, link, then in list(_linked_pieces(conn)):
        if found.ends(link, wait_links.WORK, item_id):
            _end_piece_wait(conn, item_id, key, link, then, found, now)
            moved.add(item_id)
    return moved


def _answered_waiting_items(conn):
    """(item id, his latest answer) for every item reported as waiting on
    him with no decision open, answered since it began to wait."""
    return conn.execute(
        "SELECT i.id, d.key, d.answered_where, MAX(d.answered_at) AS answered_at FROM work_items i "
        "JOIN work_decisions d ON d.item_id = i.id WHERE i.state=? AND i.closed_at IS NULL "
        "AND d.answer IS NOT NULL AND d.answered_at >= COALESCE(i.waiting_since, 0) "
        "AND NOT EXISTS (SELECT 1 FROM work_decisions o WHERE o.item_id = i.id AND "
        "o.answer IS NULL AND o.withdrawn_at IS NULL) "
        "GROUP BY i.id", (WAITING,)).fetchall()


def sweep_waits() -> "wait_links.Settled":
    """Move on everything still waiting on an answer he has already given
    (at start, and on every read: wait_links.sweep): each linked piece, and
    each item reported as waiting on him whose decisions are all answered
    since."""
    moved = set()
    with closing(_connect()) as conn:
        now = time.time()
        for item_id, key, link, then in list(_linked_pieces(conn)):
            found = wait_links.already(conn, link, wait_links.WORK, item_id)
            if found is not None:
                _end_piece_wait(conn, item_id, key, link, then, found, now)
                moved.add(item_id)
        for r in _answered_waiting_items(conn):
            _set_item_state(conn, r["id"], WORKING)
            _note(conn, r["id"], UPDATE,
                  f"Tim answered {r['key']}; nothing else waits on him, so back to working.",
                  {"state": WORKING, "wait_ended": {"link": f"decision:{r['key']}", "by": "tim",
                                                    "how": r["answered_where"],
                                                    "answered_at": r["answered_at"]}}, now)
            moved.add(r["id"])
        if moved:
            conn.commit()
    return wait_links.Settled(work=moved)


@dataclass(frozen=True)
class GivenAnswer:
    """His answer to a decision an asker asked, for that asker."""
    item_id: str
    title: str
    key: str
    question: str
    answer: str
    where: str
    answered_at: float


def answers_for(asked_by: str, since: float = 0.0, limit: int = LIST_MAX) -> list[GivenAnswer]:
    """Every answer he gave since `since` to a decision `asked_by` asked
    (its access key, as `work_api.asker` keeps it), oldest first."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT d.*, i.title FROM work_decisions d JOIN work_items i ON i.id = d.item_id "
            "WHERE d.asked_by=? AND d.answer IS NOT NULL AND d.answered_at>=? "
            "ORDER BY d.answered_at LIMIT ?", (asked_by, since, limit)).fetchall()
    return [GivenAnswer(r["item_id"], r["title"], r["key"], r["question"], r["answer"],
                        r["answered_where"], r["answered_at"]) for r in rows]
