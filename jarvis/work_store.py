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

The record is authoritative. A piece (or an item with no pieces) that
waits on Tim or is blocked is held: it is reported so only naming what
holds it (`until`: wait_links links, `decision:<key>` one of the item's open
decisions, `work:<item>:<key>`, `workflow:<id>:<key>`,
`package:<id>[:<decision>]`, or a blocker's words), and no report moves it
on (`_move`): only his answer to each link, by any route, in the same
transaction (`end_waits`), or `cleared` for a blocker in words. It then
moves to `then` (ready by default). One already answered is released when
the record is next read (`sweep_waits`). Done is his alone to give: no
report says done; his review (APPROVED, REVIEW_RETURNED, WORK_ADDED) is.

Read-time only, never stored: the item's state, worked out from its pieces
(`_computed`: a working report is a claim, never telemetry, so it reads
activity_unknown, which is also how an item reads while a piece claims
work; waiting on him only when nothing can go on), with how many open decisions also wait on him; a
decision he answered with no report since and no piece working shows the
item as waiting for its asker to start (`follow_up`), flagged on Blocking as
"answered, no follow-up" after FOLLOW_UP_AFTER_SEC; a wait on him with no
link, open for wait_links.UNLINKED_AFTER_SEC, is flagged as "waiting, no link
to an answer"; a verifying item with no event for QUIET_AFTER_SEC reads as
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

# An item's and a piece's state. VERIFYING sits between working and done:
# the work is in, being checked. READY: nothing holds it up any more (what
# it waited for is answered) and its agent has yet to pick it up.
PLANNED, READY, WORKING, VERIFYING, WAITING, BLOCKED, DONE = (
    "planned", "ready", "working", "verifying", "waiting_on_tim", "blocked", "done")
STATES = (PLANNED, READY, WORKING, VERIFYING, WAITING, BLOCKED, DONE)
# Held: it cannot go on until what it names is answered or cleared, so it is
# reported only with what holds it (`until`), and no report moves it on.
HELD = (WAITING, BLOCKED)
# The states that should be reporting: silent for QUIET_AFTER_SEC is quiet.
ACTIVE = (VERIFYING,)
# WORKING is retained only as a legacy reporter claim. No provider currently
# supplies exact assignment-bound, current token-generation evidence. Fail closed.
ACTIVITY_UNKNOWN = "activity_unknown"
PUBLIC_STATES = (PLANNED, READY, ACTIVITY_UNKNOWN, VERIFYING, WAITING, BLOCKED, DONE)
ACTIVITY_REASON = "Current token generation for this Work assignment is not confirmed."


def public_state(reported: str) -> str:
    """A report is not telemetry. Never promote a stored claim to Working.

    CLI turn starts, transcript freshness, run state and session-wide tokens
    cannot establish current generation on this particular assignment.
    Do not add a positive path until that evidence is supplied by a trusted
    provider adapter with exact assignment identity and end/disconnect handling.
    """
    return ACTIVITY_UNKNOWN if reported == WORKING else reported

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
# A decision that names a package, closed because he signed that package off
# without answering its matching question: his act, never his answer, and
# never a reporter's to send (package sign-off, server._sign_off_package).
DECISION_SIGNED_OFF = "decision_signed_off"
SIGNED_OFF = "signed_off"
# `closed` moves an item to History for good; nothing more is reported to it.
CLOSED = "closed"
REPORTED_KINDS = (UPDATE, PIECE, DECISION_NEEDED, DECISION_WITHDRAWN, CLOSED)
# His review of an item, his alone (the page, his voice), never a reporter's:
# done is his to give. APPROVED makes the item and every piece done;
# REVIEW_RETURNED ("not done yet") sends it back to working with his note;
# WORK_ADDED adds a piece in his words.
APPROVED, REVIEW_RETURNED, WORK_ADDED = "approved", "review_returned", "work_added"
REVIEW_KINDS = (APPROVED, REVIEW_RETURNED, WORK_ADDED)
# What his act in a review package came to when it changed nothing itself:
# signed off with no verdict, or a verdict that could not be applied (why).
# Recorded by the server, so the item always says what his act did.
REVIEW_NOTED = "review_noted"
NO_VERDICT, NOT_APPLIED = "no_verdict", "not_applied"
# Everything only he does: never a reporter's to send.
HIS_KINDS = (DECISION_MADE, DECISION_SIGNED_OFF) + REVIEW_KINDS + (REVIEW_NOTED,)
# His review acts, as the item shows the latest of them.
_REVIEW_ACTS = REVIEW_KINDS + (REVIEW_NOTED,)
CREATED = "created"

# What an item is listed under: the area it was given, else its source folder.
BY_AREA, BY_FOLDER = "area", "folder"

# A piece's evidence, told apart by its shape alone; nothing is opened.
COMMIT, PATH, URL = "commit", "path", "url"
_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")
_URL = re.compile(r"https?://[^\s]{1,1000}", re.IGNORECASE)

QUIET_AFTER_SEC = 10 * 60
# A piece claimed working with no report for this long is shown stalled.
STALLED_PIECE_AFTER_SEC = 24 * 3600
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

# 4: when a decision was closed by his signing off its package without an
# answer to it (DECISION_SIGNED_OFF).
_SIGNED_OFF = """
    ALTER TABLE work_decisions ADD COLUMN signed_off_at REAL;
"""

# 5: done is his to give (approved_at; his note when he says it is not done
# yet): an open item or piece reported done before, never approved, goes
# back to verifying, "Not approved by Tim". And what holds a piece (or an
# item with no pieces) that waits on him or is
# blocked (`until`, a JSON list of wait_links links, or words for a
# blocker), the state it moves to once that is answered, and since when it
# has been held; when an item last had a report from its agents (not his
# answers). A held piece or item named nothing before this step: it is
# linked to its item's one open decision, or, with none open, to the one he
# answered since it was held (the sweep then moves it on); any other stays
# unlinked, flagged on Blocking.
_HELD_SQL = "state IN ('waiting_on_tim', 'blocked') AND until = ''"
_OPEN_OF = ("FROM work_decisions d WHERE d.item_id = {t}.{id} AND d.answer IS NULL "
            "AND d.withdrawn_at IS NULL AND d.signed_off_at IS NULL")
_ANSWERED_OF = ("FROM work_decisions d WHERE d.item_id = {t}.{id} AND (d.answer IS NOT NULL "
                "OR d.signed_off_at IS NOT NULL) AND COALESCE(d.answered_at, d.signed_off_at) "
                ">= COALESCE({t}.waiting_since, 0)")


def _link_held(table: str, id_column: str) -> str:
    """The migration's two links for one table's held rows with none."""
    open_of = _OPEN_OF.format(t=table, id=id_column)
    answered_of = _ANSWERED_OF.format(t=table, id=id_column)
    return f"""
    UPDATE {table} SET until = json_array('decision:' || (SELECT d.key {open_of}))
        WHERE {_HELD_SQL} AND (SELECT COUNT(*) {open_of}) = 1;
    UPDATE {table} SET until = json_array('decision:' || (SELECT d.key {answered_of}
        ORDER BY COALESCE(d.answered_at, d.signed_off_at) DESC LIMIT 1))
        WHERE {_HELD_SQL} AND (SELECT COUNT(*) {open_of}) = 0 AND EXISTS (SELECT 1 {answered_of});
"""


_WAIT_LINKS = """
    ALTER TABLE work_pieces ADD COLUMN until         TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_pieces ADD COLUMN then_state    TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_pieces ADD COLUMN waiting_since REAL;
    ALTER TABLE work_items  ADD COLUMN until         TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_items  ADD COLUMN then_state    TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_items  ADD COLUMN waiting_since REAL;
    ALTER TABLE work_items  ADD COLUMN reported_at   REAL;
    UPDATE work_pieces SET waiting_since = note_at WHERE state IN ('waiting_on_tim', 'blocked');
    UPDATE work_items SET waiting_since = updated_at WHERE state IN ('waiting_on_tim', 'blocked');
    UPDATE work_items SET reported_at = COALESCE((SELECT MAX(e.at) FROM work_events e
        WHERE e.item_id = work_items.id AND e.kind <> 'decision_made'), created_at);
    ALTER TABLE work_items  ADD COLUMN approved_at    REAL;
    ALTER TABLE work_items  ADD COLUMN review_note    TEXT NOT NULL DEFAULT '';
    ALTER TABLE work_items  ADD COLUMN review_note_at REAL;
    UPDATE work_pieces SET state = 'verifying', note = 'Not approved by Tim. ' || note
        WHERE state = 'done' AND item_id IN (SELECT id FROM work_items WHERE closed_at IS NULL);
    UPDATE work_items SET state = 'verifying', latest_update = 'Not approved by Tim. '
        || latest_update WHERE state = 'done' AND closed_at IS NULL;
""" + _link_held("work_pieces", "item_id") + _link_held("work_items", "id")

_MIGRATIONS = {1: _SCHEMA, 2: _PIECE_DETAIL, 3: _DECISION_PACKAGE, 4: _SIGNED_OFF, 5: _WAIT_LINKS}

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
    # Read time only: while verifying, when it goes quiet with no
    # report after its latest note. None in any other state.
    quiet_at: Optional[float] = None
    # Held (waiting on him, or blocked): what holds it, each a wait_links
    # link or (blocked only) words; the state it moves to once they are all
    # answered ("" is ready); since when it has been held.
    until: list[str] = field(default_factory=list)
    then: str = ""
    waiting_since: Optional[float] = None
    # Read time only: held with nothing named, for UNLINKED_AFTER_SEC.
    unlinked: bool = False
    activity_reason: str = ""
    # Read time only: claimed working with no report for STALLED_PIECE_AFTER_SEC.
    stalled: bool = False


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
    """A decision that is no longer open: answered by him, withdrawn by the
    agent that asked it, or closed by his signing off its package without
    an answer."""
    key: str
    question: str
    answer: str               # his answer, the asker's reason for withdrawing it, or ""
    where: str                # page or voice; `withdrawn`; `signed_off` (no answer)
    answered_at: float
    package: str = ""


@dataclass(frozen=True)
class Blocker:
    """One thing holding the item up, worked out at read time."""
    kind: str                 # one of BLOCKER_KINDS
    key: str                  # the decision's or piece's key; "" for the item itself
    title: str                # the question, the piece's title, or the item's title
    text: str = ""            # the piece's latest note, the item's latest update, the asker
    # piece_after: the keys not done; held: what holds it (its `until`)
    waits_for: list[str] = field(default_factory=list)
    # A wait on him with no link to an answer, open for UNLINKED_AFTER_SEC.
    unlinked: bool = False


@dataclass(frozen=True)
class PackageReview:
    """What his latest act in a review package did to the item: approved,
    review_returned or work_added (his review, applied), no_verdict (signed
    off with none), or not_applied (with why)."""
    outcome: str
    at: float
    why: str
    package: str


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
    # Worked out from the record (`_computed`), never taken from a report,
    # and never Working: a working claim reads activity_unknown.
    state: str
    reported_state: str       # audit intent, never an activity claim
    quiet: bool               # verifying or claimed working, nothing reported for QUIET_AFTER_SEC
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
    package_review: Optional[PackageReview] = None
    # Open decisions that wait on him while the item itself goes on (its
    # state is not waiting on him): "Working — 2 decisions also wait on you".
    also_waiting: int = 0
    # An item with no pieces that is held: what holds it, as a piece's.
    until: list[str] = field(default_factory=list)
    approved_at: Optional[float] = None    # when he approved it done (his act alone)
    approvable: bool = False               # he can approve it now (`approvable`)
    review_note: str = ""                  # his words when he said it is not done yet
    review_note_at: Optional[float] = None
    activity_reason: str = ""
    # Read time only: nobody is moving it: no piece's claim heard from, and
    # no report from its agents, within QUIET_AFTER_SEC (work_api decides
    # whether that is "Stalled", once nothing waits on him).
    unmoved: bool = False


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
    if event.kind not in REPORTED_KINDS + HIS_KINDS:
        raise WorkRefused("Kinds are: " + ", ".join(REPORTED_KINDS) + ".")
    detail = event.detail if isinstance(event.detail, dict) else {}
    now = time.time()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM work_items WHERE id=?", (event.item_id,)).fetchone()
        if row is None:
            raise WorkRefused("There is no work item with that id.")
        if row["closed_at"] is not None and event.kind not in (DECISION_MADE,
                                                                DECISION_SIGNED_OFF):
            # A closed item takes nothing more, his review included; only a
            # late answer to a decision it asked.
            raise WorkRefused("That work item is closed; it is kept in History. Start a new one.")
        kept = _apply(conn, event.item_id, event.kind, summary, detail, now)
        conn.execute("UPDATE work_items SET latest_update=?, revision=revision+1, updated_at=? "
                     "WHERE id=?", (summary, now, event.item_id))
        if event.kind in REPORTED_KINDS:
            conn.execute("UPDATE work_items SET reported_at=? WHERE id=?", (now, event.item_id))
        moved = wait_links.Settled()
        if event.kind in (DECISION_MADE, DECISION_SIGNED_OFF, DECISION_WITHDRAWN):
            moved = _answered(conn, event.item_id, kept, event.kind, now)
        _insert_event(conn, event.item_id, event.kind, summary, kept, now)
        conn.commit()
    wait_links.tell(moved)
    return get(event.item_id)


def _answered(conn, item_id: str, kept: dict, kind: str, now: float) -> "wait_links.Settled":
    """A decision closed (his answer, his signing off its package, or its
    asker withdrawing it) releases everything held on it, here and in the
    workflow monitor, in this transaction. The records moved."""
    how = {DECISION_SIGNED_OFF: SIGNED_OFF, DECISION_WITHDRAWN: WITHDRAWN}.get(
        kind, kept.get("where", "page"))
    return wait_links.settle(conn, wait_links.answer(wait_links.WORK, {item_id}, kept["key"],
                                                     how, now), now)


def _open(alias: str = "") -> str:
    """SQL: a decision still open (not answered, withdrawn, or closed by his
    signing off its package)."""
    return (f"{alias}answer IS NULL AND {alias}withdrawn_at IS NULL "
            f"AND {alias}signed_off_at IS NULL")


def _his(alias: str = "") -> str:
    """SQL: a decision closed by him: answered, or its package signed off."""
    return f"({alias}answer IS NOT NULL OR {alias}signed_off_at IS NOT NULL)"


# ── holds: what a piece (or an item with no pieces) waits on ────────────

def holds_of(text: str) -> list[str]:
    """What holds a piece or item, as kept (a JSON list; "" is nothing)."""
    if not text:
        return []
    try:
        found = json.loads(text)
    except ValueError:
        return [text]
    return [str(h) for h in found] if isinstance(found, list) else [str(found)]


def _link(text: str):
    """The wait_links link a hold names, or None for a blocker's words."""
    try:
        return wait_links.parse(text)
    except wait_links.LinkRefused:
        return None


def _check_holds(conn, item_id: str, state: str, value) -> list[str]:
    """What a report says holds it, checked: every hold of a piece waiting
    on him is a link to an answer of his (`decision:<key>` one of this
    item's open decisions); a blocked one may also name its blocker in
    words. Refused when it names nothing."""
    given = value if isinstance(value, list) else [value]
    holds: list[str] = []
    for one in given:
        text = _one_line(one, SUMMARY_MAX)
        if not text:
            continue
        try:
            link = wait_links.parse(text)
        except wait_links.LinkRefused as e:
            raise WorkRefused(str(e)) from None
        if link is None and state == WAITING:
            raise WorkRefused("Waiting on him names the answer it waits for, with --until: "
                              "decision:<key>, work:<item>:<key>, workflow:<id>:<key> or "
                              "package:<id>[:<decision>].")
        if link is not None and link.kind == wait_links.DECISION:
            row = conn.execute("SELECT * FROM work_decisions WHERE item_id=? AND key=?",
                               (item_id, link.key)).fetchone()
            if row is None:
                raise WorkRefused(f"This item has asked no decision {link.key}; ask it with "
                                  f"decision first, then wait on it.")
            if row["answer"] is not None or row["withdrawn_at"] is not None or \
                    row["signed_off_at"] is not None:
                raise WorkRefused(f"Decision {link.key} is already closed "
                                  f"({_closed_how(row)[1]}): it holds nothing up.")
        text = str(link) if link else text
        if text not in holds:
            holds.append(text)
    if not holds:
        raise WorkRefused(f"A {state} report names what holds it, with --until: an open "
                          f"decision (decision:<key>), work:<item>:<key>, "
                          f"workflow:<id>:<key>, package:<id>[:<decision>]"
                          + (", or the blocker in words." if state == BLOCKED else "."))
    return holds


def _held_refusal(what: str, state: str, holds: list[str]) -> WorkRefused:
    if not holds:
        return WorkRefused(f"{what} is {state}.")
    named = ", ".join(holds)
    if state == WAITING or any(_link(h) for h in holds):
        return WorkRefused(f"{what} is {state} on {named}; it moves on by itself once that is "
                           f"answered. Report work that can go on meanwhile as a piece of "
                           f"its own.")
    return WorkRefused(f"{what} is blocked by: {named}. Once that is cleared, report it with "
                       f"--cleared; report work that can go on meanwhile as a piece of its own.")


# Done is his to give, never a reporter's.
_NOT_YOURS = WorkRefused("Done is Tim's to give: report it verifying (ready for his review), "
                         "with --evidence of what to check, and he approves it.")


_APPROVED_ALREADY = WorkRefused("He approved this done; only he reopens it (Not done yet) or "
                                "adds work to it.")


def _move(conn, table: str, where: str, args: tuple, what: str, item_id: str,
          detail: dict, now: float) -> dict:
    """Move a piece (or an item with no pieces) to the state a report
    gives, under the hold rules: a held one is never moved on by a report
    (only by the answer it waits for, or `cleared` for a blocker in words,
    or when it named nothing, as before holds were kept); one becomes held
    only naming what holds it (`until`), with the state it moves to then
    (`then`, ready by default). What of it is kept."""
    row = conn.execute(f"SELECT state, until, then_state, waiting_since FROM {table} "
                       f"WHERE {where}", args).fetchone()
    current, holds = row["state"], holds_of(row["until"])
    new = _state(detail["state"]) if detail.get("state") is not None else current
    if new == DONE and current != DONE:
        raise _NOT_YOURS
    if current == DONE and new != DONE:
        raise _APPROVED_ALREADY
    if current in HELD and new != current and holds:
        words_only = all(_link(h) is None for h in holds)
        if not (current == BLOCKED and words_only and detail.get("cleared")):
            raise _held_refusal(what, current, holds)
    if new not in HELD:
        if detail.get("until") or detail.get("then"):
            raise WorkRefused(f"Only something waiting on him or blocked names what holds it "
                              f"(--until) and what it moves to then (--then).")
        if new != current or holds:
            conn.execute(f"UPDATE {table} SET state=?, until='', then_state='', "
                         f"waiting_since=NULL WHERE {where}", (new, *args))
        return {"state": new} if new != current else {}
    if detail.get("until") is not None or new != current or not holds:
        holds = _check_holds(conn, item_id, new, detail.get("until"))
    then = row["then_state"]
    if detail.get("then") is not None:
        then = _state(detail["then"], "The state it moves to then") if detail["then"] else ""
        if then == DONE:
            raise _NOT_YOURS
        if then in HELD:
            raise WorkRefused("Once what holds it is answered, it moves on to a state that is "
                              "not held.")
    since = row["waiting_since"] if current in HELD and row["waiting_since"] else now
    conn.execute(f"UPDATE {table} SET state=?, until=?, then_state=?, waiting_since=? "
                 f"WHERE {where}", (new, json.dumps(holds), then, since, *args))
    return {"state": new, "until": holds, "then": then}


def _apply(conn, item_id: str, kind: str, summary: str, detail: dict, now: float) -> dict:
    """Bring the snapshot up to date with one event; what of its detail is kept."""
    if kind == UPDATE:
        kept: dict = {}
        if detail.get("state") is not None or detail.get("until") is not None:
            kept.update(_move_item(conn, item_id, detail, now))
        if detail.get("area") is not None:
            # An empty area takes it back to its source folder's group.
            kept["area"] = _one_line(detail["area"], AREA_MAX)
            conn.execute("UPDATE work_items SET area=? WHERE id=?", (kept["area"], item_id))
        return kept
    if kind == PIECE:
        return _apply_piece(conn, item_id, summary, detail, now)
    if kind in REVIEW_KINDS:
        return _review(conn, item_id, kind, summary, detail, now)
    if kind == REVIEW_NOTED:
        outcome = detail.get("outcome")
        if outcome not in (NO_VERDICT, NOT_APPLIED):
            raise WorkRefused("A review noted is no_verdict or not_applied.")
        return {"outcome": outcome, "why": _one_line(detail.get("why"), SUMMARY_MAX),
                "where": "package", "source": _source(detail)}
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
                        "IS NOT NULL OR withdrawn_at IS NOT NULL OR signed_off_at IS NOT NULL)",
                        (item_id, key)).fetchone():
            raise WorkRefused(f"Decision {key} was already answered or closed; ask with a new key.")
        conn.execute(
            "INSERT OR REPLACE INTO work_decisions (item_id, key, question, options, "
            "recommendation, opened_at, package, asked_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, key, summary, json.dumps(options), recommendation, now, package,
             str(detail.get("asked_by") or "")))
        return {"key": key, "options": options, "recommendation": recommendation,
                "package": package}
    if kind == DECISION_WITHDRAWN:
        return _withdraw(conn, item_id, summary, detail, now)
    if kind == DECISION_SIGNED_OFF:
        key = _slug(detail.get("key"), "The decision's key")
        done = conn.execute("UPDATE work_decisions SET signed_off_at=? WHERE item_id=? AND key=? "
                            "AND answer IS NULL AND withdrawn_at IS NULL AND signed_off_at IS NULL",
                            (now, item_id, key)).rowcount
        if not done:
            raise WorkRefused(f"No decision {key} is open.")
        return {"key": key}
    # DECISION_MADE: his answer, from the page or his voice.
    key = _slug(detail.get("key"), "The decision's key")
    answer = _one_line(detail.get("answer"), SUMMARY_MAX)
    if not answer:
        raise WorkRefused("An answer needs words.")
    where = detail.get("where") if detail.get("where") in ("page", "voice") else "page"
    done = conn.execute("UPDATE work_decisions SET answer=?, answered_where=?, answered_at=? "
                        "WHERE item_id=? AND key=? AND answer IS NULL AND withdrawn_at IS NULL "
                        "AND signed_off_at IS NULL",
                        (answer, where, now, item_id, key)).rowcount
    if not done:
        raise WorkRefused(f"No decision {key} is open.")
    return {"key": key, "answer": answer, "where": where}


def _move_item(conn, item_id: str, detail: dict, now: float) -> dict:
    """An item's own reported state. With pieces, its state is worked out
    from them: it is never reported held (the piece that waits is). Done is
    his approval, never reported, and once he has approved it only he
    reopens it. With no pieces, it moves under the same hold rules as a
    piece."""
    pieces = conn.execute("SELECT key, state, until FROM work_pieces WHERE item_id=?",
                          (item_id,)).fetchall()
    if not pieces:
        return _move(conn, "work_items", "id=?", (item_id,), "The item", item_id, detail, now)
    state = _state(detail.get("state")) if detail.get("state") is not None else None
    if state in HELD or detail.get("until") is not None:
        raise WorkRefused("This item's state is worked out from its pieces: report the piece "
                          "that waits or is blocked, with --until.")
    if state == DONE:
        raise _NOT_YOURS
    if state is None:
        return {}
    if conn.execute("SELECT state FROM work_items WHERE id=?", (item_id,)).fetchone()[0] == DONE:
        raise _APPROVED_ALREADY
    conn.execute("UPDATE work_items SET state=?, until='', then_state='', waiting_since=NULL "
                 "WHERE id=?", (state, item_id))
    return {"state": state}


def _review(conn, item_id: str, kind: str, words: str, detail: dict, now: float) -> dict:
    """His review of an item, his act alone: approve it (it and every piece
    done, with when), say it is not done yet (back to working, every piece
    ready for review back to working, his note kept on it), or add work (a
    new piece in his words, ready for its agent). Approving refuses while a
    decision of his is open or a piece is held."""
    # Where he gave it: the page, his voice, or a review package.
    where = detail.get("where") if detail.get("where") in ("page", "voice", "package") else "page"
    kept: dict = {"by": "tim", "where": where}
    if isinstance(detail.get("source"), dict):
        kept["source"] = _source(detail)
    if kind == APPROVED:
        open_ones = [r["key"] for r in conn.execute(
            f"SELECT key FROM work_decisions WHERE item_id=? AND {_open()}", (item_id,))]
        if open_ones:
            raise WorkRefused(f"Decision {open_ones[0]} is still open: answer it before "
                              f"approving the item.")
        held = conn.execute("SELECT key, state, until FROM work_pieces WHERE item_id=? AND "
                            "state IN (?, ?)", (item_id, *HELD)).fetchone()
        if held is not None:
            raise _held_refusal(f"Piece {held['key']}", held["state"], holds_of(held["until"]))
        going = conn.execute("SELECT key FROM work_pieces WHERE item_id=? AND state=?",
                             (item_id, WORKING)).fetchone()
        if going is not None:
            raise WorkRefused(f"Piece {going['key']} is still working: approve it once its "
                              f"agent reports it ready for review.")
        conn.execute("UPDATE work_pieces SET state=?, until='', then_state='', waiting_since=NULL "
                     "WHERE item_id=? AND state<>?", (DONE, item_id, DONE))
        conn.execute("UPDATE work_items SET state=?, approved_at=?, until='', then_state='', "
                     "waiting_since=NULL, review_note='', review_note_at=NULL WHERE id=?",
                     (DONE, now, item_id))
        return {**kept, "state": DONE}
    note = _one_line(detail.get("words"), SUMMARY_MAX)
    if not note:
        raise WorkRefused("Say it in his words.")
    kept["words"] = note
    if kind == REVIEW_RETURNED:
        conn.execute("UPDATE work_pieces SET state=? WHERE item_id=? AND state IN (?, ?)",
                     (WORKING, item_id, VERIFYING, DONE))
        conn.execute("UPDATE work_items SET state=?, approved_at=NULL, review_note=?, "
                     "review_note_at=? WHERE id=?", (WORKING, note, now, item_id))
        return {**kept, "state": WORKING}
    # WORK_ADDED: a new piece in his words, on whoever last worked the item.
    taken = {r["key"] for r in conn.execute("SELECT key FROM work_pieces WHERE item_id=?",
                                            (item_id,))}
    if len(taken) >= PIECES_MAX:
        raise WorkRefused(f"A work item has at most {PIECES_MAX} pieces.")
    base = re.sub(r"[^a-z0-9]+", "-", note.lower()).strip("-")[:40].strip("-") or "his-request"
    key = next(k for k in (base if n == 1 else f"{base}-{n}" for n in range(1, PIECES_MAX + 2))
               if k not in taken)
    last = conn.execute("SELECT who_kind, who_ref FROM work_pieces WHERE item_id=? AND "
                        "who_kind<>? ORDER BY note_at DESC LIMIT 1", (item_id, TIM)).fetchone()
    person = Who(last["who_kind"], last["who_ref"]) if last else Who(TIM, None)
    conn.execute("INSERT INTO work_pieces (item_id, key, title, who_kind, who_ref, state, "
                 "position, note, note_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (item_id, key, note[:TITLE_MAX], person.kind, person.ref, READY, len(taken),
                  f"Tim added this: {note}", now))
    conn.execute("UPDATE work_items SET state=CASE WHEN state=? THEN ? ELSE state END, "
                 "approved_at=NULL WHERE id=?", (DONE, WORKING, item_id))
    return {**kept, "key": key, "who": {"kind": person.kind, "ref": person.ref}, "state": READY}


def _withdraw(conn, item_id: str, reason: str, detail: dict, now: float) -> dict:
    """The asker takes its own open decision back; the summary is why. Only
    the asker may: the access key that asked it (`by`, set by the route,
    never by the report), or any reporter for a decision asked before
    askers were recorded."""
    key = _slug(detail.get("key"), "The decision's key")
    row = conn.execute("SELECT asked_by FROM work_decisions WHERE item_id=? AND key=? AND answer "
                       "IS NULL AND withdrawn_at IS NULL AND signed_off_at IS NULL",
                       (item_id, key)).fetchone()
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
        if conn.execute("SELECT state FROM work_items WHERE id=?", (item_id,)).fetchone()[0] == DONE:
            raise _APPROVED_ALREADY
        count = conn.execute("SELECT COUNT(*) FROM work_pieces WHERE item_id=?",
                             (item_id,)).fetchone()[0]
        if count >= PIECES_MAX:
            raise WorkRefused(f"A work item has at most {PIECES_MAX} pieces.")
        person = who(on.get("kind"), on.get("ref"))
        title = _one_line(detail.get("title"), TITLE_MAX) or summary[:TITLE_MAX]
        conn.execute("INSERT INTO work_pieces (item_id, key, title, who_kind, who_ref, state, "
                     "position, note, note_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (item_id, key, title, person.kind, person.ref, PLANNED, count, summary, now))
        kept.update({"title": title, "who": {"kind": person.kind, "ref": person.ref},
                     "state": PLANNED})
        kept.update(_move_piece(conn, item_id, key, detail, now))
    else:
        conn.execute("UPDATE work_pieces SET note=?, note_at=? WHERE item_id=? AND key=?",
                     (summary, now, item_id, key))
        kept.update(_change_piece(conn, item_id, key, detail, on))
        kept.update(_move_piece(conn, item_id, key, detail, now))
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
    return kept


def _move_piece(conn, item_id: str, key: str, detail: dict, now: float) -> dict:
    return _move(conn, "work_pieces", "item_id=? AND key=?", (item_id, key), f"Piece {key}",
                 item_id, detail, now)


def _change_piece(conn, item_id: str, key: str, detail: dict, on) -> dict:
    """Change a piece's who or title, as a report about it says (its state
    moves under the hold rules, `_move_piece`)."""
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
    """When something ready for review goes quiet: QUIET_AFTER_SEC after
    it was last heard from (at once if never). None in any other state."""
    if state not in ACTIVE:
        return None
    return (heard_at or 0.0) + QUIET_AFTER_SEC


def _unlinked(row, now: float) -> bool:
    """Held with nothing named (only ever from before holds were kept),
    for UNLINKED_AFTER_SEC: "waiting, no link to an answer"."""
    since = row["waiting_since"]
    return (row["state"] in HELD and not holds_of(row["until"]) and since is not None
            and now - since > wait_links.UNLINKED_AFTER_SEC)


def _pieces(conn, item_id: str, now: Optional[float] = None) -> list[Piece]:
    now = time.time() if now is None else now
    rows = conn.execute("SELECT * FROM work_pieces WHERE item_id=? ORDER BY position",
                        (item_id,)).fetchall()
    states = {r["key"]: r["state"] for r in rows}
    pieces = []
    for r in rows:
        after = json.loads(r["after_keys"])
        pieces.append(Piece(
            r["key"], r["title"], Who(r["who_kind"], r["who_ref"]), public_state(r["state"]),
            note=r["note"], note_at=r["note_at"], worker=r["worker"], after=after,
            waits_for=[k for k in after if states.get(k) != DONE],
            evidence=[Evidence(e["kind"], e["value"]) for e in json.loads(r["evidence"])],
            quiet_at=quiet_at(public_state(r["state"]), r["note_at"]), until=holds_of(r["until"]),
            then=r["then_state"], waiting_since=r["waiting_since"],
            unlinked=_unlinked(r, now),
            activity_reason=ACTIVITY_REASON if r["state"] == WORKING else "",
            stalled=(r["state"] == WORKING and r["note_at"] is not None
                     and now - r["note_at"] > STALLED_PIECE_AFTER_SEC)))
    return _in_order(pieces)


def _closed_how(r) -> tuple[str, str, float]:
    """(its answer or why it closed, how, when): his answer on the page or
    by voice, withdrawn by its asker with why, or closed by his signing off
    its package without an answer (no words: it is not an answer)."""
    if r["answer"] is not None:
        return r["answer"], r["answered_where"], r["answered_at"]
    if r["withdrawn_at"] is not None:
        return r["withdrawn_reason"], WITHDRAWN, r["withdrawn_at"]
    return "", SIGNED_OFF, r["signed_off_at"]


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
                                       "IS NULL AND withdrawn_at IS NULL AND signed_off_at IS NULL "
                                       "ORDER BY opened_at",
                                       (item_id,))]
    answered = [Answered(r["key"], r["question"], *_closed_how(r), r["package"])
                for r in conn.execute("SELECT * FROM work_decisions WHERE item_id=? AND (answer "
                                      "IS NOT NULL OR withdrawn_at IS NOT NULL OR signed_off_at IS "
                                      "NOT NULL) ORDER BY COALESCE(answered_at, withdrawn_at, "
                                      "signed_off_at) DESC LIMIT ?", (item_id, ANSWERED_SHOWN))]
    reported = row["state"]
    state = _computed(row, pieces, decisions, now)
    area = row["area"]
    follow_up = _follow_up(conn, row, pieces, decisions, now)
    return WorkItem(
        id=item_id, title=row["title"], source_kind=row["source_kind"],
        source_path=row["source_path"], source=check_source(row["source_kind"], row["source_path"]),
        state=state, reported_state=reported,
        quiet=_quiet(state, row, pieces, now),
        latest_update=row["latest_update"],
        headline=decisions[0].question if state == WAITING and decisions else row["latest_update"],
        revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"],
        area=area, grouped_by=BY_AREA if area else BY_FOLDER,
        group=area or _source_folder(row["source_kind"], row["source_path"]),
        blocked_pieces=sum(p.state == BLOCKED for p in pieces),
        waiting_pieces=sum(p.state == WAITING for p in pieces),
        closed_at=row["closed_at"], history=_in_history(row, now),
        pieces=pieces, decisions=decisions, answered=answered,
        blockers=_blockers(row, pieces, decisions, follow_up, now),
        quiet_at=None if row["closed_at"] is not None else quiet_at(state, row["updated_at"]),
        follow_up=follow_up, package_review=_package_review(conn, item_id),
        also_waiting=len(decisions) if state not in (WAITING, DONE) else 0,
        until=holds_of(row["until"]) if not pieces else [],
        approved_at=row["approved_at"], review_note=row["review_note"],
        approvable=approvable(row, pieces, decisions),
        review_note_at=row["review_note_at"],
        activity_reason=ACTIVITY_REASON if state == ACTIVITY_UNKNOWN else "",
        unmoved=(not any(_moving(p, now) for p in pieces)
                 and now - (row["reported_at"] or row["created_at"]) > QUIET_AFTER_SEC))


def _quiet(state: str, row, pieces: list[Piece], now: float) -> bool:
    """Ready for review with nothing heard for QUIET_AFTER_SEC. A working
    claim (activity_unknown) is never "quiet": a claim was never activity
    to fall silent from."""
    return state == VERIFYING and now > quiet_at(state, row["updated_at"])


def _moving(piece: Piece, now: float) -> bool:
    """A piece whose agent claims it is working and has reported within
    QUIET_AFTER_SEC. Still a claim (activity_unknown), never telemetry."""
    return (piece.state == ACTIVITY_UNKNOWN and piece.note_at is not None
            and now - piece.note_at <= QUIET_AFTER_SEC)


def _computed(row, pieces: list[Piece], decisions: list[Decision], now: float) -> str:
    """The item's state, from the record. Reported done is done. With
    pieces: activity_unknown (claimed working) while a piece claims work and
    has reported within QUIET_AFTER_SEC (a paused claim is not progress); else ready or planned
    while one can be picked up (a planned piece whose pieces before it are
    not done cannot); else waiting on him while a decision or a piece waits
    on him; else activity_unknown but quiet (paused), blocked, or ready
    for his review, in that order. Ready for review only once he can approve
    it (`approvable`): a list row and its header never disagree. With none,
    waiting on him while a decision is open, else as reported (a working
    report as activity_unknown)."""
    if row["state"] == DONE:
        return DONE
    if not pieces:
        return WAITING if decisions else public_state(row["state"])
    live = {p.state for p in pieces if p.state != DONE}
    if any(_moving(p, now) for p in pieces):
        return ACTIVITY_UNKNOWN
    if READY in live:
        return READY
    if any(p.state == PLANNED and not p.waits_for for p in pieces):
        return PLANNED
    if WAITING in live or decisions:
        return WAITING
    for state in (ACTIVITY_UNKNOWN, BLOCKED, VERIFYING):
        if state in live:
            return state
    return PLANNED if live else VERIFYING


def approvable(row, pieces: list[Piece], decisions: list[Decision]) -> bool:
    """Whether he can approve it done now: not done or closed, no decision
    open, and no piece waiting on him, blocked or still working."""
    return (row["state"] != DONE and row["closed_at"] is None and not decisions
            and row["state"] not in HELD
            and not any(p.state in HELD + (ACTIVITY_UNKNOWN,) for p in pieces))


def asker_label(asked_by: str) -> str:
    """Who asked a decision, as shown: its access key's label, else ""."""
    return asked_by[4:] if str(asked_by or "").startswith("key:") else ""


def _follow_up(conn, row, pieces: list[Piece], decisions: list[Decision],
               now: float) -> Optional[FollowUp]:
    """His latest answer, when nothing has been reported since and no piece
    is working: the item waits for the decision's asker to start."""
    if (decisions or row["closed_at"] is not None or row["state"] == DONE
            or any(p.state in (ACTIVITY_UNKNOWN, VERIFYING) for p in pieces)):
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
    if not pieces:
        # An item with no pieces is held itself: what holds it, unless that
        # is only its own open decisions, listed above.
        holds = holds_of(row["until"])
        own = all((_link(h) or wait_links.Link("", "", None)).kind == wait_links.DECISION
                  for h in holds)
        if row["state"] == BLOCKED:
            found.append(Blocker(ITEM_BLOCKED, "", row["title"], row["latest_update"], holds,
                                 unlinked=_unlinked(row, now)))
        elif row["state"] == WAITING and not (holds and own):
            found.append(Blocker(ITEM_WAITING, "", row["title"], row["latest_update"], holds,
                                 unlinked=_unlinked(row, now)))
    found += [Blocker(PIECE_BLOCKED, p.key, p.title, p.note, p.until, unlinked=p.unlinked)
              for p in pieces if p.state == BLOCKED]
    found += [Blocker(PIECE_WAITING, p.key, p.title, p.note, p.until, unlinked=p.unlinked)
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
    """How this item's decision `key` was closed (his answer, his signing
    off the package it names, or its asker withdrawing it), as what it
    ends, or None while it is open."""
    if key is None:
        return None
    try:
        row = conn.execute(f"SELECT * FROM work_decisions WHERE item_id=? AND key=? AND NOT "
                           f"({_open()})", (item_id, key)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    _, how, at = _closed_how(row)
    return wait_links.answer(wait_links.WORK, {item_id}, key, how, at)


# Where a hold is kept: (table, the row's key columns), a piece's or an item's.
_HOLDERS = (("work_pieces", ("item_id", "key")), ("work_items", ("id",)))


def _held(conn):
    """(table, row key, item id, holds, then) for every held piece or item
    (one with no pieces) that names a link, on an item not closed."""
    for table, keys in _HOLDERS:
        item = "p.item_id" if table == "work_pieces" else "p.id"
        rows = conn.execute(
            f"SELECT p.*, {item} AS owner FROM {table} p JOIN work_items i ON i.id = {item} "
            f"WHERE p.state IN (?, ?) AND p.until NOT IN ('', '[]') AND i.closed_at IS NULL",
            HELD).fetchall()
        for r in rows:
            holds = holds_of(r["until"])
            if any(_link(h) for h in holds):
                yield table, tuple(r[k] for k in keys), r["owner"], holds, r["then_state"]


def _note(conn, item_id: str, kind: str, summary: str, detail: dict, now: float) -> None:
    """JARVIS's own entry in an item's log, as a change it made on his
    answer: the item's update and revision move, its agents' last report
    (reported_at) does not."""
    conn.execute("UPDATE work_items SET latest_update=?, revision=revision+1, updated_at=? "
                 "WHERE id=?", (summary, now, item_id))
    _insert_event(conn, item_id, kind, summary, detail, now)


def _release(conn, table: str, row_key: tuple, item_id: str, holds: list[str], then: str,
             ended: list, now: float) -> None:
    """Take the holds `ended` ((hold, Answer) pairs) off a held piece or
    item; once nothing holds it, it moves to its `then` (ready by default).
    Recorded as JARVIS's entry in the item's log, the answer his (or the
    asker's withdrawal), with when and how."""
    gone = {h for h, _ in ended}
    left = [h for h in holds if h not in gone]
    where = " AND ".join(f"{k}=?" for k in dict(_HOLDERS)[table])
    piece = row_key[1] if table == "work_pieces" else None
    found = ended[-1][1]
    if left:
        summary = _one_line(f"{found.said()}; still waits on {', '.join(left)}.", SUMMARY_MAX)
        conn.execute(f"UPDATE {table} SET until=? WHERE {where}", (json.dumps(left), *row_key))
        state = None
    else:
        state = then or READY
        summary = _one_line(f"{found.said()}; now {state}.", SUMMARY_MAX)
        conn.execute(f"UPDATE {table} SET state=?, until='', then_state='', waiting_since=NULL "
                     f"WHERE {where}", (state, *row_key))
    if piece is not None:
        conn.execute("UPDATE work_pieces SET note=?, note_at=? WHERE item_id=? AND key=?",
                     (summary, now, *row_key))
    detail = {"wait_ended": [{"link": h, "by": a.by, "how": a.how, "answered_at": a.at}
                             for h, a in ended], "until": left}
    if state is not None:
        detail["state"] = state
    if piece is not None:
        detail["key"] = piece
    _note(conn, item_id, PIECE if piece is not None else UPDATE, summary, detail, now)


def end_waits(conn, found: "wait_links.Answer", now: float) -> set:
    """Release every hold `found` answers, in the caller's transaction. The
    items it moved."""
    moved = set()
    for table, row_key, item_id, holds, then in list(_held(conn)):
        ended = [(h, found) for h in holds
                 if (link := _link(h)) and found.ends(link, wait_links.WORK, item_id)]
        if ended:
            _release(conn, table, row_key, item_id, holds, then, ended, now)
            moved.add(item_id)
    return moved


def sweep_waits() -> "wait_links.Settled":
    """Release every hold whose answer was already given (at start, and on
    every read: wait_links.sweep)."""
    moved = set()
    with closing(_connect()) as conn:
        now = time.time()
        for table, row_key, item_id, holds, then in list(_held(conn)):
            ended = [(h, found) for h in holds if (link := _link(h)) and
                     (found := wait_links.already(conn, link, wait_links.WORK, item_id))]
            if ended:
                _release(conn, table, row_key, item_id, holds, then, ended, now)
                moved.add(item_id)
        if moved:
            conn.commit()
    return wait_links.Settled(work=moved)


def _source(detail: dict) -> dict:
    """Which review package and which answer of his (when he gave it; "" for
    a sign-off with no answer) an act came from."""
    given = detail.get("source") if isinstance(detail.get("source"), dict) else {}
    return {"package": str(given.get("package") or ""),
            "answered_at": str(given.get("answered_at") or "")}


def reviewed(item_id: str, source: dict) -> bool:
    """Whether his act in a review package (`source`: the inbox id and when
    he answered) is already on the item's record, applied or noted."""
    with closing(_connect()) as conn:
        return conn.execute(
            f"SELECT 1 FROM work_events WHERE item_id=? AND kind IN "
            f"({','.join('?' * len(_REVIEW_ACTS))}) AND json_extract(detail, '$.source.package')=? "
            "AND json_extract(detail, '$.source.answered_at')=?",
            (item_id, *_REVIEW_ACTS, source["package"], source["answered_at"])).fetchone() is not None


def reviewed_since(item_id: str, since: float) -> bool:
    """Whether he has reviewed the item (approved it, said it is not done
    yet or added work, anywhere) since `since`."""
    with closing(_connect()) as conn:
        return conn.execute(
            f"SELECT 1 FROM work_events WHERE item_id=? AND kind IN "
            f"({','.join('?' * len(REVIEW_KINDS))}) AND at>=?",
            (item_id, *REVIEW_KINDS, since)).fetchone() is not None


def _package_review(conn, item_id: str) -> "Optional[PackageReview]":
    """His latest review act, when it was in a review package: what it did."""
    row = conn.execute(f"SELECT kind, detail, at FROM work_events WHERE item_id=? AND kind IN "
                       f"({','.join('?' * len(_REVIEW_ACTS))}) ORDER BY seq DESC LIMIT 1",
                       (item_id, *_REVIEW_ACTS)).fetchone()
    if row is None:
        return None
    detail = json.loads(row["detail"])
    if not (detail.get("source") or {}).get("package"):
        return None
    outcome = detail.get("outcome") if row["kind"] == REVIEW_NOTED else row["kind"]
    return PackageReview(outcome, row["at"], detail.get("why", ""), detail["source"]["package"])


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
    (its access key, as `work_api.asker` keeps it), oldest first; one closed
    by his signing off its package without an answer has no words and
    `where` signed_off."""
    when = "COALESCE(d.answered_at, d.signed_off_at)"
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT d.*, i.title FROM work_decisions d JOIN work_items i ON i.id = d.item_id "
            f"WHERE d.asked_by=? AND {_his('d.')} AND {when}>=? "
            f"ORDER BY {when} LIMIT ?", (asked_by, since, limit)).fetchall()
    return [GivenAnswer(r["item_id"], r["title"], r["key"], r["question"], *_closed_how(r))
            for r in rows]
