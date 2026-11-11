"""The Work tracker's record: each piece of work in progress, and exactly
who is doing each part of it.

A work item wraps one source on this Mac, a ZIP work package or a folder,
named by its path. The path is only ever checked with a stat (is it there,
is it the kind it says); nothing here opens, unpacks or runs it. An item
has a state, pieces (each naming exactly who is on it: an agent by session
id, a JARVIS run, a workflow-monitor workflow, or Tim), its latest update
and its open decisions.

One item is one document in JARVIS's records (`records`, the document
engine), `/work/<id>.work`: its state, its pieces and its decisions, with
its log as the document's versions, each recording its own events. Every
change and its event are one write of that document (`records.atomic`),
together with every hold his answer releases elsewhere (wait_links).
`create` and `record` are the only writers.

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
import logging
import os
import re
import secrets
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from jarvis import record_cells, wait_links
from jarvis import records as _records

log = logging.getLogger(__name__)

# An item's and a piece's state. VERIFYING sits between working and done:
# the work is in, being checked. READY: nothing holds it up any more (what
# it waited for is answered) and its agent has yet to pick it up.
PLANNED, READY, WORKING, VERIFYING, WAITING, BLOCKED, DONE = (
    "planned", "ready", "working", "verifying", "waiting_on_tim", "blocked", "done")
STATES = (PLANNED, READY, WORKING, VERIFYING, WAITING, BLOCKED, DONE)
# Error: JARVIS holds something sent for the item (a package or a report
# signed by a key it does not know as an onboarded agent's), with why. Set
# and cleared only by JARVIS itself (ERROR_RAISED, ERROR_CLEARED), never a
# reporter; it is the item's state, ahead of everything but done, until the
# hold is settled (the agent onboarded, or its key refused).
ERROR = "error"
# Held: it cannot go on until what it names is answered or cleared, so it is
# reported only with what holds it (`until`), and no report moves it on.
HELD = (WAITING, BLOCKED)
# The states that should be reporting: silent for QUIET_AFTER_SEC is quiet.
ACTIVE = (VERIFYING,)
# WORKING is retained only as a legacy reporter claim. No provider currently
# supplies exact assignment-bound, current token-generation evidence. Fail closed.
ACTIVITY_UNKNOWN = "activity_unknown"
# WORKING is shown only on live evidence (a subagent on the piece writing now).
PUBLIC_STATES = (PLANNED, READY, WORKING, ACTIVITY_UNKNOWN, VERIFYING, WAITING, BLOCKED, DONE,
                 ERROR)
ACTIVITY_REASON = ("The agent says it is working on this; JARVIS cannot see that work "
                   "happening, so it does not say it is.")


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
# What "not done yet" with no words of his says as its note.
NO_NOTE = "no note given"
REVIEW_KINDS = (APPROVED, REVIEW_RETURNED, WORK_ADDED)
# What his act in a review package came to when it changed nothing itself:
# signed off with no verdict, or a verdict that could not be applied (why).
# Recorded by the server, so the item always says what his act did.
REVIEW_NOTED = "review_noted"
NO_VERDICT, NOT_APPLIED = "no_verdict", "not_applied"
# Everything only he does: never a reporter's to send.
HIS_KINDS = (DECISION_MADE, DECISION_SIGNED_OFF) + REVIEW_KINDS + (REVIEW_NOTED,)
# JARVIS's own: the item's Error, raised and cleared (see ERROR). Never a
# reporter's, never his.
ERROR_RAISED, ERROR_CLEARED = "error_raised", "error_cleared"
JARVIS_KINDS = (ERROR_RAISED, ERROR_CLEARED)
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

# ── the document ────────────────────────────────────────────────────────
#
# One Work item is one document, `/work/<id>.work` (its id through
# _records.segment): the item's own fields under the names its columns had,
# its pieces (`pieces`, in their order) and decisions (`decisions`) inline,
# and the events this version records (`events`, record_cells). The item's
# log is the document's versions, read back with _records.history(path,
# fields=["events"]). An event keeps its number (`seq`: the item's own
# order; a moved event keeps the one the table gave it), kind, summary,
# detail, time and reporter. Kept in the same write as each change, worked
# out from the rest: `last_seq`, `held` (a piece or the item held on a link
# an answer may end: what `end_waits` and the sweep look for) and
# `review_act` (his latest review act, for `_package_review`). A row the
# move brought keeps its SQLite rowid (`rowid`), so the move back gives it
# again.

ROOT, TYPE = "/work", "work"
ITEM_COLUMNS = ("id", "title", "source_kind", "source_path", "state", "latest_update",
                "revision", "created_at", "updated_at", "area", "closed_at", "until",
                "then_state", "waiting_since", "reported_at", "approved_at", "review_note",
                "review_note_at", "error", "error_at")
PIECE_COLUMNS = ("item_id", "key", "title", "who_kind", "who_ref", "state", "position", "note",
                 "note_at", "worker", "after_keys", "evidence", "until", "then_state",
                 "waiting_since", "reporter")
DECISION_COLUMNS = ("item_id", "key", "question", "options", "recommendation", "answer",
                    "answered_where", "opened_at", "answered_at", "package", "asked_by",
                    "withdrawn_at", "withdrawn_reason", "signed_off_at")
EVENT_COLUMNS = ("seq", "item_id", "kind", "summary", "detail", "at", "reporter")
TABLES = ("work_items", "work_pieces", "work_decisions", "work_events")
_JSON_PIECE = ("after_keys", "evidence")


def path_of(item_id: str) -> str:
    return f"{ROOT}/{_records.segment(item_id)}.{TYPE}"


def _is_item(data) -> bool:
    """A Work item's document (project_store's pieces of work may share /work)."""
    return isinstance(data, dict) and "source_path" in data and isinstance(data.get("pieces"), list)


def _until_text(holds: list) -> str:
    """What holds a piece or item, as its column held it ("" is nothing)."""
    return json.dumps(holds) if holds else ""


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
    reporter: str = ""        # who sent its latest report, as the route proved it
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
    # Read time only: the subagent on it, as its own files show it now.
    live: Optional["LiveAgent"] = None


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
    # Who reported it, as the route proved it (reporters.recorded); his own
    # acts and JARVIS's leave it empty. Never the report's own word.
    reporter: str = ""


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
    # Read time only: the subagent it shows as what it is doing (a piece's
    # working one, else one waiting on its own background work).
    live: Optional["LiveAgent"] = None


@dataclass(frozen=True)
class WorkGroup:
    grouped_by: str           # area or folder
    name: str                 # the area, or the folder's path
    items: list[WorkItem]


@dataclass(frozen=True)
class ItemRecord:
    """One item as its document holds it (`load`), in the shape its rows
    had: everything `compose` needs besides the clock and the live owners,
    so an item is worked out again for either without reading the record
    again. The source's stat is taken when it is read (`with_source` takes
    it again)."""
    row: dict
    piece_rows: tuple
    decisions: tuple
    answered: tuple
    last_answer: Optional[dict]            # his latest answer, for `follow_up`
    package_review: Optional[PackageReview]
    source: str

    @property
    def id(self) -> str:
        return self.row["id"]

    def with_source(self) -> "ItemRecord":
        """The same record with its source's stat taken again."""
        return dataclasses.replace(self, source=check_source(self.row["source_kind"],
                                                             self.row["source_path"]))


# ── after each commit ───────────────────────────────────────────────────
#
# Whoever holds items worked out (work_view.py) is told which changed, after
# the commit: by `create` and `record`, the only writers, and by every wait
# an answer ended (wait_links.tell). By the listener's module and name, so a
# module loaded again replaces its own.

_changed_listeners: dict[str, Callable[[set], None]] = {}


def on_changed(listener: Callable[[set], None]) -> None:
    """Call `listener` with the ids of the items each committed change moved."""
    _changed_listeners[f"{listener.__module__}.{listener.__qualname__}"] = listener


def _tell_changed(ids) -> None:
    """Tell the listeners, after the commit. Never raises."""
    ids = set(ids)
    if not ids:
        return
    for listener in list(_changed_listeners.values()):
        try:
            listener(ids)
        except Exception:
            log.warning("work: a change listener failed", exc_info=True)


def _waits_ended(moved: "wait_links.Settled") -> None:
    _tell_changed(moved.work)


wait_links.on_settled(_waits_ended)


def ready() -> None:
    """Nothing to make: the records are documents in the engine."""


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
    exists). Tim has no ref; everyone else does. An agent may be one of a
    session's subagents: `<session id>/sub:<agent id>`, the agent id the
    Agent tool gave its dispatcher, which names its transcript."""
    if kind not in WHO_KINDS:
        raise WorkRefused(f"Who is one of: {', '.join(WHO_KINDS)}.")
    if kind == TIM:
        return Who(TIM, None)
    text = str(ref or "").strip()
    if not text or len(text) > REF_MAX or any(c.isspace() for c in text):
        raise WorkRefused(f"A piece on a {kind} names its id.")
    if SUB in text:
        session, _, agent = text.partition(SUB)
        if kind != AGENT or not session or "/" in session or not _AGENT_ID.fullmatch(agent):
            raise WorkRefused("A subagent is agent:<session id>/sub:<agent id>, the agent id "
                              "its dispatcher's Agent tool gave.")
    return Who(kind, text)


# A session's subagent, as a piece names it: agent:<session id>/sub:<agent id>.
SUB = "/sub:"
_AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def subagent_of(person: Who) -> Optional[tuple[str, str]]:
    """Provider session and optional native child identity."""
    if person.kind != AGENT or not person.ref:
        return None
    if SUB not in person.ref:
        return None
    session, _, agent = person.ref.partition(SUB)
    return session, agent


@dataclass(frozen=True)
class LiveAgent:
    """A projection of the canonical worker result, never a worker claim."""
    name: str                 # its description: the dispatcher's words
    state: str                # working (confirmed generation), finished, stale,
                              # or waiting (on background work it started)
    written: float            # last observed activity boundary
    doing: str                # canonical phase, separate from assignment business status
    active_within: float      # evidence lease for display refresh
    activity: Optional[dict] = None  # exact canonical worker activity
    snapshot_id: Optional[str] = None


# How a subagent is looked up: (session id, agent id) -> LiveAgent or None.
# Set by the server, which holds the sessions snapshot.
_live_lookup: Optional[Callable[[str, str], Optional[LiveAgent]]] = None


def look_up_live_with(lookup) -> None:
    global _live_lookup
    _live_lookup = lookup


def live_agent(person: Who) -> Optional[LiveAgent]:
    """The subagent `person` names, as it is now, or None (not a subagent,
    not found, or nothing to look it up with). Never raises."""
    found = subagent_of(person)
    if found is None and person.kind == AGENT and person.ref:
        found = person.ref, ""
    if found is None or _live_lookup is None:
        return None
    try:
        return _live_lookup(*found)
    except Exception:
        return None


LIVE_WORKING = "working"
# Its turn is over, but background work it started (a command, a monitor)
# has not ended: session_watch.background_waiting.
LIVE_WAITING = "waiting"
# Why a piece on a subagent is not shown working, in his terms.
_LIVE_REASON = {"finished": "Its subagent has finished.",
                "stale": "Current token generation for this assignment is not confirmed.",
                LIVE_WAITING: "Its subagent is waiting on its own background work."}


def parse_who(text: str) -> Who:
    """`agent:<session id>`, `run:<run id>`, `workflow:<workflow id>` or `tim`."""
    kind, _, ref = str(text or "").strip().partition(":")
    return who(kind.lower(), ref or None)


# ── writing ─────────────────────────────────────────────────────────────

def _caller(kind: str, reporter: str = "") -> "_records.Caller":
    """Who wrote an event: its reporter as the route proved it, else him
    for his own acts, else JARVIS."""
    if reporter:
        return _records.by(reporter)
    return _records.TIM if kind in HIS_KINDS else _records.JARVIS


def _add_event(doc: dict, kind: str, summary: str, detail: dict, now: float,
               reporter: str = "") -> None:
    """One event of the item's log, recorded by the version being written."""
    doc.setdefault(record_cells.EVENTS, []).append(
        {"seq": record_cells.next_seq(doc), "kind": kind, "summary": summary, "detail": detail,
         "at": now, "reporter": reporter})
    if kind in _REVIEW_ACTS:
        doc["review_act"] = {"kind": kind, "detail": detail, "at": now}


def _take(unit, item_id: str) -> Optional[dict]:
    """The item's document to change in `unit`, or None when there is none."""
    doc = record_cells.take(unit, path_of(item_id))
    return doc if _is_item(doc) else None


def _stage(unit, doc: dict) -> None:
    """Stage the item's document, what is worked out from it kept with it."""
    doc["held"] = any(True for _ in _held(doc))
    record_cells.stage(unit, path_of(doc["id"]), doc)


def _piece(doc: dict, key: str) -> Optional[dict]:
    return next((p for p in doc["pieces"] if p["key"] == key), None)


def _decision(doc: dict, key: str) -> Optional[dict]:
    return next((d for d in doc["decisions"] if d["key"] == key), None)


def _is_open(d: dict) -> bool:
    """A decision still open (not answered, withdrawn, or closed by his
    signing off its package)."""
    return d["answer"] is None and d["withdrawn_at"] is None and d["signed_off_at"] is None


def _his(d: dict) -> bool:
    """A decision closed by him: answered, or its package signed off."""
    return d["answer"] is not None or d["signed_off_at"] is not None


def _new_piece(key: str, title: str, person: Who, state: str, position: int, note: str,
               now: float) -> dict:
    return {"key": key, "title": title, "who_kind": person.kind, "who_ref": person.ref,
            "state": state, "position": position, "note": note, "note_at": now, "worker": "",
            "after_keys": [], "evidence": [], "until": [], "then_state": "",
            "waiting_since": None, "reporter": ""}


def create(title: str, source_kind: str, path: str, state: str = PLANNED,
           area: str = "", reporter: str = "") -> WorkItem:
    """Begin tracking a piece of work on a zip or folder that is on this Mac
    now, under an area if it is given one; `reporter` who asked, as the
    route proved it. Returns it."""
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

    def write(unit) -> None:
        if unit.get(path_of(item_id)) is not None:
            raise WorkRefused("That work item id is taken; try again.")
        doc = {"id": item_id, "title": title, "source_kind": source_kind, "source_path": kept,
               "state": state, "latest_update": f"{title} is tracked.", "revision": 1,
               "created_at": now, "updated_at": now, "area": area, "closed_at": None,
               "until": [], "then_state": "", "waiting_since": None, "reported_at": None,
               "approved_at": None, "review_note": "", "review_note_at": None, "error": "",
               "error_at": None, "pieces": [], "decisions": [], record_cells.EVENTS: [],
               "last_seq": 0, "review_act": None}
        _add_event(doc, CREATED, f"{title} is tracked.",
                   {"source_kind": source_kind, "source_path": kept, "state": state,
                    "area": area}, now, reporter)
        _stage(unit, doc)

    _records.atomic(write, by=_caller(CREATED, reporter))
    _tell_changed({item_id})
    return get(item_id)


def record(event: WorkEvent) -> WorkItem:
    """Record one event and bring the item up to date with it, in one write
    of its document (and of every record his answer releases). Refused
    (WorkRefused) for a malformed event, an unknown item, a piece or
    decision it does not have."""
    summary = _one_line(event.summary, SUMMARY_MAX)
    if not summary:
        raise WorkRefused("Every report needs a one-sentence summary.")
    if event.kind not in REPORTED_KINDS + HIS_KINDS + JARVIS_KINDS:
        raise WorkRefused("Kinds are: " + ", ".join(REPORTED_KINDS) + ".")
    detail = event.detail if isinstance(event.detail, dict) else {}

    def write(unit) -> "wait_links.Settled":
        now = time.time()
        doc = _take(unit, event.item_id)
        if doc is None:
            raise WorkRefused("There is no work item with that id.")
        if doc["closed_at"] is not None and event.kind not in (DECISION_MADE,
                                                                DECISION_SIGNED_OFF,
                                                                ERROR_CLEARED):
            # A closed item takes nothing more, his review included; only a
            # late answer to a decision it asked.
            raise WorkRefused("That work item is closed; it is kept in History. Start a new one.")
        kept = _apply(doc, event.item_id, event.kind, summary, detail, now)
        doc.update(latest_update=summary, revision=doc["revision"] + 1, updated_at=now)
        if event.kind in REPORTED_KINDS:
            doc["reported_at"] = now
        if event.kind == PIECE:
            _piece(doc, kept["key"])["reporter"] = event.reporter
        _add_event(doc, event.kind, summary, kept, now, event.reporter)
        _stage(unit, doc)
        if event.kind in (DECISION_MADE, DECISION_SIGNED_OFF, DECISION_WITHDRAWN):
            return _answered(unit, event.item_id, kept, event.kind, now)
        return wait_links.Settled()

    moved = _records.atomic(write, by=_caller(event.kind, event.reporter),
                           full_sync=event.kind in HIS_KINDS)
    wait_links.tell(moved)
    _tell_changed({event.item_id})
    return get(event.item_id)


def _answered(unit, item_id: str, kept: dict, kind: str, now: float) -> "wait_links.Settled":
    """A decision closed (his answer, his signing off its package, or its
    asker withdrawing it) releases everything held on it, here and in the
    workflow monitor, in this unit. The records moved."""
    how = {DECISION_SIGNED_OFF: SIGNED_OFF, DECISION_WITHDRAWN: WITHDRAWN}.get(
        kind, kept.get("where", "page"))
    return wait_links.settle(unit, wait_links.answer(wait_links.WORK, {item_id}, kept["key"],
                                                     how, now), now)


# ── holds: what a piece (or an item with no pieces) waits on ────────────

def holds_of(text: str) -> list[str]:
    """What holds a piece or item, as its column kept it (a JSON list; ""
    is nothing)."""
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


def _check_holds(doc: dict, item_id: str, state: str, value) -> list[str]:
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
            asked = _decision(doc, link.key)
            if asked is None:
                raise WorkRefused(f"This item has asked no decision {link.key}; ask it with "
                                  f"decision first, then wait on it.")
            if not _is_open(asked):
                raise WorkRefused(f"Decision {link.key} is already closed "
                                  f"({_closed_how(asked)[1]}): it holds nothing up.")
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


def _move(doc: dict, holder: dict, what: str, item_id: str, detail: dict, now: float) -> dict:
    """Move a piece (or an item with no pieces), `holder`, to the state a
    report gives, under the hold rules: a held one is never moved on by a
    report (only by the answer it waits for, or `cleared` for a blocker in
    words, or when it named nothing, as before holds were kept); one
    becomes held only naming what holds it (`until`), with the state it
    moves to then (`then`, ready by default). What of it is kept."""
    current, holds = holder["state"], list(holder["until"] or [])
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
            holder.update(state=new, until=[], then_state="", waiting_since=None)
        return {"state": new} if new != current else {}
    if detail.get("until") is not None or new != current or not holds:
        holds = _check_holds(doc, item_id, new, detail.get("until"))
    then = holder["then_state"]
    if detail.get("then") is not None:
        then = _state(detail["then"], "The state it moves to then") if detail["then"] else ""
        if then == DONE:
            raise _NOT_YOURS
        if then in HELD:
            raise WorkRefused("Once what holds it is answered, it moves on to a state that is "
                              "not held.")
    since = holder["waiting_since"] if current in HELD and holder["waiting_since"] else now
    holder.update(state=new, until=holds, then_state=then, waiting_since=since)
    return {"state": new, "until": holds, "then": then}


def _apply(doc: dict, item_id: str, kind: str, summary: str, detail: dict, now: float) -> dict:
    """Bring the document up to date with one event; what of its detail is kept."""
    if kind == UPDATE:
        kept: dict = {}
        if detail.get("state") is not None or detail.get("until") is not None:
            kept.update(_move_item(doc, item_id, detail, now))
        if detail.get("area") is not None:
            # An empty area takes it back to its source folder's group.
            kept["area"] = _one_line(detail["area"], AREA_MAX)
            doc["area"] = kept["area"]
        return kept
    if kind == PIECE:
        return _apply_piece(doc, item_id, summary, detail, now)
    if kind in REVIEW_KINDS:
        return _review(doc, item_id, kind, summary, detail, now)
    if kind == REVIEW_NOTED:
        outcome = detail.get("outcome")
        if outcome not in (NO_VERDICT, NOT_APPLIED):
            raise WorkRefused("A review noted is no_verdict or not_applied.")
        return {"outcome": outcome, "why": _one_line(detail.get("why"), SUMMARY_MAX),
                "where": "package", "source": _source(detail)}
    if kind == CLOSED:
        doc["closed_at"] = now
        return {}
    if kind == ERROR_RAISED:
        doc.update(error=summary, error_at=now)
        return {"held": _one_line(detail.get("held"), SUMMARY_MAX)}
    if kind == ERROR_CLEARED:
        doc.update(error="", error_at=None)
        return {"held": _one_line(detail.get("held"), SUMMARY_MAX)}
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
        asked = _decision(doc, key)
        if asked is not None and not _is_open(asked):
            raise WorkRefused(f"Decision {key} was already answered or closed; ask with a new key.")
        # Asked again while open: it replaces the question asked before.
        doc["decisions"] = [d for d in doc["decisions"] if d["key"] != key] + [{
            "key": key, "question": summary, "options": options,
            "recommendation": recommendation, "answer": None, "answered_where": None,
            "opened_at": now, "answered_at": None, "package": package,
            "asked_by": str(detail.get("asked_by") or ""), "withdrawn_at": None,
            "withdrawn_reason": None, "signed_off_at": None}]
        return {"key": key, "options": options, "recommendation": recommendation,
                "package": package}
    if kind == DECISION_WITHDRAWN:
        return _withdraw(doc, item_id, summary, detail, now)
    key = _slug(detail.get("key"), "The decision's key")
    asked = _decision(doc, key)
    if kind == DECISION_SIGNED_OFF:
        if asked is None or not _is_open(asked):
            raise WorkRefused(f"No decision {key} is open.")
        asked["signed_off_at"] = now
        return {"key": key}
    # DECISION_MADE: his answer, from the page or his voice.
    answer = _one_line(detail.get("answer"), SUMMARY_MAX)
    if not answer:
        raise WorkRefused("An answer needs words.")
    where = detail.get("where") if detail.get("where") in ("page", "voice") else "page"
    if asked is None or not _is_open(asked):
        raise WorkRefused(f"No decision {key} is open.")
    asked.update(answer=answer, answered_where=where, answered_at=now)
    return {"key": key, "answer": answer, "where": where}


def _move_item(doc: dict, item_id: str, detail: dict, now: float) -> dict:
    """An item's own reported state. With pieces, its state is worked out
    from them: it is never reported held (the piece that waits is). Done is
    his approval, never reported, and once he has approved it only he
    reopens it. With no pieces, it moves under the same hold rules as a
    piece."""
    if not doc["pieces"]:
        return _move(doc, doc, "The item", item_id, detail, now)
    state = _state(detail.get("state")) if detail.get("state") is not None else None
    if state in HELD or detail.get("until") is not None:
        raise WorkRefused("This item's state is worked out from its pieces: report the piece "
                          "that waits or is blocked, with --until.")
    if state == DONE:
        raise _NOT_YOURS
    if state is None:
        return {}
    if doc["state"] == DONE:
        raise _APPROVED_ALREADY
    doc.update(state=state, until=[], then_state="", waiting_since=None)
    return {"state": state}


def _review(doc: dict, item_id: str, kind: str, words: str, detail: dict, now: float) -> dict:
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
        open_ones = [d["key"] for d in doc["decisions"] if _is_open(d)]
        if open_ones:
            raise WorkRefused(f"Decision {open_ones[0]} is still open: answer it before "
                              f"approving the item.")
        held = next((p for p in doc["pieces"] if p["state"] in HELD), None)
        if held is not None:
            raise _held_refusal(f"Piece {held['key']}", held["state"], list(held["until"]))
        going = next((p for p in doc["pieces"] if p["state"] == WORKING), None)
        if going is not None:
            raise WorkRefused(f"Piece {going['key']} is still working: approve it once its "
                              f"agent reports it ready for review.")
        if _item(doc, now).state != VERIFYING:
            raise WorkRefused("It is not ready for review yet: approve it once its agent "
                              "reports it ready for review (verifying).")
        for p in doc["pieces"]:
            if p["state"] != DONE:
                p.update(state=DONE, until=[], then_state="", waiting_since=None)
        doc.update(state=DONE, approved_at=now, until=[], then_state="", waiting_since=None,
                   review_note="", review_note_at=None)
        return {**kept, "state": DONE}
    note = _one_line(detail.get("words"), SUMMARY_MAX)
    if not note and kind == REVIEW_RETURNED:
        # Not done yet needs no words: it goes back to working all the same.
        note = NO_NOTE
    if not note:
        raise WorkRefused("Say it in his words.")
    kept["words"] = note
    if kind == REVIEW_RETURNED:
        for p in doc["pieces"]:
            if p["state"] in (VERIFYING, DONE):
                p["state"] = WORKING
        doc.update(state=WORKING, approved_at=None, review_note=note, review_note_at=now)
        return {**kept, "state": WORKING}
    # WORK_ADDED: a new piece in his words, on whoever last worked the item.
    taken = {p["key"] for p in doc["pieces"]}
    if len(taken) >= PIECES_MAX:
        raise WorkRefused(f"A work item has at most {PIECES_MAX} pieces.")
    stem = re.sub(r"[^a-z0-9]+", "-", note.lower()).strip("-")[:40].strip("-") or "his-request"
    key = next(k for k in (stem if n == 1 else f"{stem}-{n}" for n in range(1, PIECES_MAX + 2))
               if k not in taken)
    worked = sorted((p for p in doc["pieces"] if p["who_kind"] != TIM),
                    key=lambda p: (p["note_at"] is not None, p["note_at"] or 0.0), reverse=True)
    person = Who(worked[0]["who_kind"], worked[0]["who_ref"]) if worked else Who(TIM, None)
    doc["pieces"].append(_new_piece(key, note[:TITLE_MAX], person, READY, len(taken),
                                    f"Tim added this: {note}", now))
    if doc["state"] == DONE:
        doc["state"] = WORKING
    doc["approved_at"] = None
    return {**kept, "key": key, "who": {"kind": person.kind, "ref": person.ref}, "state": READY}


def _withdraw(doc: dict, item_id: str, reason: str, detail: dict, now: float) -> dict:
    """The asker takes its own open decision back; the summary is why. Only
    the asker may: the access key that asked it (`by`, set by the route,
    never by the report), or any reporter for a decision asked before
    askers were recorded."""
    key = _slug(detail.get("key"), "The decision's key")
    asked = _decision(doc, key)
    if asked is None or not _is_open(asked):
        raise WorkRefused(f"No decision {key} is open.")
    by_any = {str(detail.get("by") or ""), *(str(b) for b in detail.get("by_any") or ())}
    if asked["asked_by"] and asked["asked_by"] not in by_any:
        raise WorkRefused(f"Only the agent that asked decision {key} can withdraw it.")
    asked.update(withdrawn_at=now, withdrawn_reason=reason)
    return {"key": key}


def _after(doc: dict, item_id: str, key: str, value) -> list[str]:
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
    graph = {p["key"]: list(p["after_keys"]) for p in doc["pieces"]}
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


def _add_evidence(have: list, value) -> list[dict]:
    """The piece's evidence with the new added, each once, oldest first."""
    given = value if isinstance(value, list) else [value]
    kept = [dict(e) for e in have or []]
    for one in given:
        found = dataclasses.asdict(evidence(one))
        if found not in kept:
            kept.append(found)
    if len(kept) > EVIDENCE_MAX:
        raise WorkRefused(f"A piece keeps at most {EVIDENCE_MAX} pieces of evidence.")
    return kept


def _apply_piece(doc: dict, item_id: str, summary: str, detail: dict, now: float) -> dict:
    """Add a piece, or change its title, who is on it, its state, its worker
    label, the pieces it comes after or its evidence. The report's summary is
    the piece's latest note."""
    key = _slug(detail.get("key"), "The piece's key")
    have = _piece(doc, key)
    kept: dict = {"key": key}
    on = detail.get("who")
    if have is None:
        if not isinstance(on, dict):
            raise WorkRefused("A new piece says who is on it.")
        if doc["state"] == DONE:
            raise _APPROVED_ALREADY
        count = len(doc["pieces"])
        if count >= PIECES_MAX:
            raise WorkRefused(f"A work item has at most {PIECES_MAX} pieces.")
        person = who(on.get("kind"), on.get("ref"))
        title = _one_line(detail.get("title"), TITLE_MAX) or summary[:TITLE_MAX]
        doc["pieces"].append(_new_piece(key, title, person, PLANNED, count, summary, now))
        kept.update({"title": title, "who": {"kind": person.kind, "ref": person.ref},
                     "state": PLANNED})
        kept.update(_move_piece(doc, item_id, key, detail, now))
    else:
        have.update(note=summary, note_at=now)
        kept.update(_change_piece(doc, item_id, key, detail, on))
        kept.update(_move_piece(doc, item_id, key, detail, now))
    piece = _piece(doc, key)
    if detail.get("worker") is not None:
        # The reporter's own words; an empty label takes it off.
        kept["worker"] = _one_line(detail["worker"], WORKER_MAX)
        piece["worker"] = kept["worker"]
    if detail.get("after") is not None:
        kept["after"] = _after(doc, item_id, key, detail["after"])
        piece["after_keys"] = kept["after"]
    if detail.get("evidence"):
        every = _add_evidence(piece["evidence"], detail["evidence"])
        piece["evidence"] = every
        kept["evidence"] = every
    return kept


def _move_piece(doc: dict, item_id: str, key: str, detail: dict, now: float) -> dict:
    return _move(doc, _piece(doc, key), f"Piece {key}", item_id, detail, now)


def _change_piece(doc: dict, item_id: str, key: str, detail: dict, on) -> dict:
    """Change a piece's who or title, as a report about it says (its state
    moves under the hold rules, `_move_piece`)."""
    kept: dict = {}
    piece = _piece(doc, key)
    if isinstance(on, dict):
        person = who(on.get("kind"), on.get("ref"))
        piece.update(who_kind=person.kind, who_ref=person.ref)
        kept["who"] = {"kind": person.kind, "ref": person.ref}
    if detail.get("title"):
        title = _one_line(detail["title"], TITLE_MAX)
        piece["title"] = title
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


def _pieces(rows, now: float, live: Callable[[Who], Optional[LiveAgent]]) -> list[Piece]:
    """The pieces from their rows, as they are at `now` with each owner's
    live state as `live` gives it."""
    states = {r["key"]: r["state"] for r in rows}
    pieces = []
    for r in rows:
        after = json.loads(r["after_keys"])
        person = Who(r["who_kind"], r["who_ref"])
        agent = live(person)
        # Working is shown only on live evidence: a subagent on the piece
        # writing now. Any other working report reads activity_unknown.
        evidenced = r["state"] == WORKING and agent is not None and agent.state == LIVE_WORKING
        shown = WORKING if evidenced else public_state(r["state"])
        reason = ""
        if shown == ACTIVITY_UNKNOWN:
            reason = _LIVE_REASON.get(agent.state, ACTIVITY_REASON) if agent else ACTIVITY_REASON
        pieces.append(Piece(
            r["key"], r["title"], person, shown,
            note=r["note"], note_at=r["note_at"], worker=r["worker"], reporter=r["reporter"],
            after=after,
            waits_for=[k for k in after if states.get(k) != DONE],
            evidence=[Evidence(e["kind"], e["value"]) for e in json.loads(r["evidence"])],
            quiet_at=(agent.written + agent.active_within if evidenced
                      else quiet_at(shown, r["note_at"])),
            until=holds_of(r["until"]),
            then=r["then_state"], waiting_since=r["waiting_since"],
            unlinked=_unlinked(r, now),
            activity_reason=reason,
            stalled=(r["state"] == WORKING and not evidenced and r["note_at"] is not None
                     and now - r["note_at"] > STALLED_PIECE_AFTER_SEC),
            live=agent))
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


def item_row(doc: dict) -> dict:
    """The item's own fields as its `work_items` row held them."""
    row = {c: doc.get(c) for c in ITEM_COLUMNS}
    row["until"] = record_cells.text_of(doc, "until", list(doc.get("until") or []), holds_of,
                                        _until_text)
    return row


def _piece_row(item_id: str, piece: dict) -> dict:
    """A piece as its `work_pieces` row held it."""
    row = {c: piece.get(c) for c in PIECE_COLUMNS}
    row["item_id"] = item_id
    for name in _JSON_PIECE:
        row[name] = record_cells.text_of(piece, name, piece.get(name) or [])
    row["until"] = record_cells.text_of(piece, "until", list(piece.get("until") or []), holds_of,
                                        _until_text)
    return row


def _decision_row(item_id: str, decision: dict) -> dict:
    """A decision as its `work_decisions` row held it."""
    row = {c: decision.get(c) for c in DECISION_COLUMNS}
    row["item_id"] = item_id
    row["options"] = record_cells.text_of(decision, "options", decision.get("options") or [])
    return row


def _when_closed(d: dict) -> float:
    return d["answered_at"] or d["withdrawn_at"] or d["signed_off_at"] or 0.0


def load(doc: dict) -> ItemRecord:
    """An item's record from its document, and its source's stat."""
    item_id = doc["id"]
    pieces = tuple(_piece_row(item_id, p) for p in sorted(doc["pieces"],
                                                          key=lambda p: p["position"]))
    decisions = tuple(
        Decision(d["key"], d["question"], list(d["options"]), d["recommendation"],
                 d["opened_at"], d["package"])
        for d in sorted((d for d in doc["decisions"] if _is_open(d)),
                        key=lambda d: d["opened_at"]))
    answered = tuple(
        Answered(d["key"], d["question"], *_closed_how(d), d["package"])
        for d in sorted((d for d in doc["decisions"] if not _is_open(d)), key=_when_closed,
                        reverse=True)[:ANSWERED_SHOWN])
    given = [d for d in doc["decisions"] if d["answer"] is not None]
    last = max(given, key=lambda d: d["answered_at"] or 0.0) if given else None
    return ItemRecord(item_row(doc), pieces, decisions, answered,
                      None if last is None else {k: last[k] for k in (
                          "key", "question", "asked_by", "answered_at")},
                      _package_review(doc), check_source(doc["source_kind"], doc["source_path"]))


def compose(rec: ItemRecord, now: float,
            live: Optional[Callable[[Who], Optional[LiveAgent]]] = None) -> WorkItem:
    """The item as it is at `now`, from its record and each piece owner's
    live state (`live`; by default looked up now, `live_agent`). Reads
    nothing: everything it works out is from `rec`, the clock and `live`."""
    row = rec.row
    item_id = row["id"]
    pieces = _pieces(rec.piece_rows, now, live or live_agent)
    decisions = list(rec.decisions)
    reported = row["state"]
    state = _computed(row, pieces, decisions, now)
    area = row["area"]
    follow_up = _follow_up(rec.last_answer, row, pieces, decisions, now)
    item_live = _item_live(pieces)
    return WorkItem(
        id=item_id, title=row["title"], source_kind=row["source_kind"],
        source_path=row["source_path"], source=rec.source,
        state=state, reported_state=reported,
        quiet=_quiet(state, row, pieces, now),
        latest_update=row["latest_update"],
        headline=(row["error"] if state == ERROR else
                  decisions[0].question if state == WAITING and decisions else row["latest_update"]),
        revision=row["revision"], created_at=row["created_at"], updated_at=row["updated_at"],
        area=area, grouped_by=BY_AREA if area else BY_FOLDER,
        group=area or _source_folder(row["source_kind"], row["source_path"]),
        blocked_pieces=sum(p.state == BLOCKED for p in pieces),
        waiting_pieces=sum(p.state == WAITING for p in pieces),
        closed_at=row["closed_at"], history=_in_history(row, now),
        pieces=pieces, decisions=decisions, answered=list(rec.answered),
        blockers=_blockers(row, pieces, decisions, follow_up, now),
        quiet_at=(None if row["closed_at"] is not None
                  else max((p.quiet_at for p in pieces if p.state == WORKING and p.quiet_at),
                           default=None) if state == WORKING
                  else quiet_at(state, row["updated_at"])),
        follow_up=follow_up, package_review=_shown_review(row, rec.package_review),
        also_waiting=len(decisions) if state not in (WAITING, DONE) else 0,
        until=holds_of(row["until"]) if not pieces else [],
        approved_at=row["approved_at"], review_note=row["review_note"],
        approvable=approvable(row, state, pieces, decisions),
        review_note_at=row["review_note_at"],
        activity_reason=_item_reason(state, item_live),
        live=item_live,
        unmoved=(not any(_moving(p, now) for p in pieces)
                 and now - (row["reported_at"] or row["created_at"]) > QUIET_AFTER_SEC))


def next_change(rec: ItemRecord, now: float) -> Optional[float]:
    """The soonest time after `now` at which the clock alone may change what
    `compose` works out for this record (going quiet, stalled, unlinked,
    overdue, into History), or None when nothing waits on the clock. Every
    threshold the read-time rules above measure is listed; one that turns
    out to change nothing only costs composing the item again."""
    row = rec.row
    if row["closed_at"] is not None:
        return None
    times = [row["updated_at"] + QUIET_AFTER_SEC, row["updated_at"] + DONE_LINGER_SEC,
             (row["reported_at"] or row["created_at"]) + QUIET_AFTER_SEC]
    if row["waiting_since"] is not None:
        times.append(row["waiting_since"] + wait_links.UNLINKED_AFTER_SEC)
    if rec.last_answer is not None:
        times.append(rec.last_answer["answered_at"] + FOLLOW_UP_AFTER_SEC)
    for r in rec.piece_rows:
        if r["note_at"] is not None:
            times += [r["note_at"] + QUIET_AFTER_SEC, r["note_at"] + STALLED_PIECE_AFTER_SEC]
        if r["waiting_since"] is not None:
            times.append(r["waiting_since"] + wait_links.UNLINKED_AFTER_SEC)
    return min((t for t in times if t > now), default=None)


def _item(doc: dict, now: float) -> WorkItem:
    return compose(load(doc), now)


def _quiet(state: str, row, pieces: list[Piece], now: float) -> bool:
    """Ready for review with nothing heard for QUIET_AFTER_SEC. A working
    claim (activity_unknown) is never "quiet": a claim was never activity
    to fall silent from."""
    return state == VERIFYING and now > quiet_at(state, row["updated_at"])


def _moving(piece: Piece, now: float) -> bool:
    """A piece being moved now: shown working (its subagent writing now),
    its subagent waiting on background work it started, or claimed working
    and reported within QUIET_AFTER_SEC (still a claim, activity_unknown)."""
    return piece.state == WORKING or (
        piece.live is not None and piece.live.state == LIVE_WAITING) or (
        piece.state == ACTIVITY_UNKNOWN and piece.note_at is not None
        and now - piece.note_at <= QUIET_AFTER_SEC)


def _item_live(pieces: list[Piece]) -> Optional[LiveAgent]:
    """The subagent the item shows as what it is doing: one working now
    first, else one waiting on its own background work."""
    for wanted in (LIVE_WORKING, LIVE_WAITING):
        found = next((p.live for p in pieces if p.live is not None and p.live.state == wanted
                      and p.state != DONE), None)
        if found is not None:
            return found
    return None


def _item_reason(state: str, live: Optional[LiveAgent]) -> str:
    """Why the item is not shown working, in his terms: a subagent on it
    waiting on its background work, or a claim JARVIS cannot see."""
    if live is not None and live.state == LIVE_WAITING:
        return f"Waiting on {live.name}'s background work."
    return ACTIVITY_REASON if state == ACTIVITY_UNKNOWN else ""


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
    if row["error"]:
        return ERROR
    if not pieces:
        return WAITING if decisions else public_state(row["state"])
    live = {p.state for p in pieces if p.state != DONE}
    if any(p.state == WORKING for p in pieces):
        return WORKING            # live evidence: a subagent on a piece writing now
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


def approvable(row, state: str, pieces: list[Piece], decisions: list[Decision]) -> bool:
    """Whether he can approve it done now: only once it is ready for his
    review (`state`, worked out), not closed, no decision open, and no piece
    waiting on him, blocked or still claimed working (docs/work-states.md)."""
    return (state == VERIFYING and row["closed_at"] is None and not decisions
            and row["state"] not in HELD
            and not any(p.state in HELD + (ACTIVITY_UNKNOWN, WORKING) for p in pieces))


def asker_label(asked_by: str) -> str:
    """Who asked a decision, as shown: its agent's one name, its access
    key's label, else ""."""
    asked_by = str(asked_by or "")
    if asked_by.startswith("key:"):
        return asked_by[4:]
    if asked_by.startswith(("agent:", "workkey:")):
        from jarvis import reporters
        return reporters.shown(asked_by)
    return ""


def _follow_up(last, row, pieces: list[Piece], decisions: list[Decision],
               now: float) -> Optional[FollowUp]:
    """His latest answer (`last`), when nothing has been reported since and
    no piece is working: the item waits for the decision's asker to start."""
    if (decisions or row["closed_at"] is not None or row["state"] == DONE
            or any(p.state in (WORKING, ACTIVITY_UNKNOWN, VERIFYING) for p in pieces)):
        return None
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


def _docs() -> list[dict]:
    """Every item's document, as it is now."""
    return [r.data for r in _records.scan(ROOT, types=[TYPE]) if _is_item(r.data)]


def _doc(item_id: str) -> Optional[dict]:
    rec = _records.get(path_of(str(item_id)))
    return rec.data if rec is not None and _is_item(rec.data) else None


def get(item_id: str) -> Optional[WorkItem]:
    wait_links.sweep()
    doc = _doc(item_id)
    return _item(doc, time.time()) if doc is not None else None


def _history_rule(doc: dict, now: float) -> bool:
    """`_in_history`'s rule, on a document."""
    return (doc["closed_at"] is not None
            or (doc["state"] == DONE and doc["updated_at"] < now - DONE_LINGER_SEC))


def listing(limit: int = LIST_MAX, history: bool = False) -> list[WorkItem]:
    """The open items (not closed, and not done for DONE_LINGER_SEC): those
    not done first, then done ones, most recent first. With `history`, the
    items in History instead, the most recently closed or finished first."""
    now = time.time()
    wait_links.sweep()
    docs = [d for d in _docs() if _history_rule(d, now) == history]
    if history:
        docs.sort(key=lambda d: d["closed_at"] if d["closed_at"] is not None
                  else d["updated_at"], reverse=True)
    else:
        docs.sort(key=lambda d: (d["state"] == DONE, -d["updated_at"]))
    return [_item(d, now) for d in docs[:limit]]


def records(item_ids=None) -> dict[str, ItemRecord]:
    """Every item's record (or those of `item_ids`): what work_view.py
    holds. An id with no item is left out."""
    if item_ids is None:
        docs = _docs()
    else:
        docs = [d for d in (_doc(i) for i in sorted(set(item_ids))) if d is not None]
    return {d["id"]: load(d) for d in docs}


def grouped(items: list[WorkItem]) -> list[WorkGroup]:
    """The items under their groups, each group where its first item comes
    and its items in the order given."""
    groups: dict[tuple[str, str], list[WorkItem]] = {}
    for item in items:
        groups.setdefault((item.grouped_by, item.group), []).append(item)
    return [WorkGroup(by, name, members) for (by, name), members in groups.items()]


def _event(item_id: str, e: dict) -> WorkEvent:
    return WorkEvent(item_id, e["kind"], e["summary"], dict(e.get("detail") or {}), e["at"],
                     e["seq"], e.get("reporter") or "")


def _logged(item_id: str, newest: Optional[int] = None) -> list[dict]:
    """The item's log, oldest first, from its document's versions: all of
    it, or at least its `newest` events (read from the newest versions on,
    a few at a time, never more than it needs)."""
    path = path_of(str(item_id))
    if newest is None:
        return [e for v in _records.history(path, fields=[record_cells.EVENTS])
                for e in (v.data or {}).get(record_cells.EVENTS) or []]
    versions = max(newest, 8)
    while True:
        found = _records.history(path, fields=[record_cells.EVENTS], limit=versions)
        events = [e for v in found for e in (v.data or {}).get(record_cells.EVENTS) or []]
        if len(events) >= newest or len(found) < versions:
            return events
        versions *= 4


def events(item_id: str, after: int = 0, kinds: Optional[set[str]] = None,
           limit: int = 200) -> list[WorkEvent]:
    """The log, oldest first, from just after `after` (an event's seq)."""
    found = sorted((e for e in _logged(item_id) if e["seq"] > after
                    and (not kinds or e["kind"] in kinds)), key=lambda e: e["seq"])
    return [_event(item_id, e) for e in found[:limit]]


def latest(item_id: str, count: int) -> list[WorkEvent]:
    """An item's newest `count` events, oldest of them first: read from its
    newest versions only (bounded by `count`, never its whole log)."""
    if count <= 0:
        return []
    found = sorted(_logged(item_id, newest=count), key=lambda e: e["seq"])
    return [_event(item_id, e) for e in found[-count:]]


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


def open_items_of(session_id: str) -> list[tuple[str, str]]:
    """(id, title) of each open item (not in History) on which the agent of
    `session_id`, or one of its subagents, has a piece not done: the items
    a message from it must name (one_channel round 1), oldest first."""
    if not session_id:
        return []
    open_ids = {i.id for i in listing(limit=LIST_MAX * 10)}
    mine = [d for d in _docs() if d["id"] in open_ids and any(
        p["who_kind"] == AGENT and p["state"] != DONE and p["who_ref"] is not None
        and (p["who_ref"] == session_id or p["who_ref"].startswith(session_id + SUB))
        for p in d["pieces"])]
    return [(d["id"], d["title"]) for d in sorted(mine, key=lambda d: d["created_at"])]


# ── his answers, and what waits for them ────────────────────────────────

def decision_answer(unit, item_id: str, key: Optional[str]) -> "Optional[wait_links.Answer]":
    """How this item's decision `key` was closed (his answer, his signing
    off the package it names, or its asker withdrawing it), as what it
    ends, or None while it is open. Read through `unit` (a condition of its
    write) when one is given."""
    if key is None:
        return None
    rec = (unit.get if unit is not None else _records.get)(path_of(str(item_id)))
    asked = _decision(rec.data, key) if rec is not None and _is_item(rec.data) else None
    if asked is None or _is_open(asked):
        return None
    _, how, at = _closed_how(asked)
    return wait_links.answer(wait_links.WORK, {item_id}, key, how, at)


def _held(doc: dict):
    """(holder, piece key or None, holds, then) for every held piece, or the
    item itself when it has no pieces, that names a link, on an item not
    closed."""
    if doc.get("closed_at") is not None:
        return
    for holder, key in [(p, p["key"]) for p in doc["pieces"]] + [(doc, None)]:
        holds = list(holder.get("until") or [])
        if holder["state"] in HELD and holds and any(_link(h) for h in holds):
            yield holder, key, holds, holder["then_state"]


def _held_docs(unit):
    """The documents with a hold an answer may end, read through `unit`:
    those the engine finds held, and those this unit has changed."""
    paths = {r.path for r in unit.scan(ROOT, types=[TYPE], where=[["held", "=", True]])}
    paths |= record_cells.touched(unit, ROOT, TYPE)
    for path in sorted(paths):
        doc = record_cells.take(unit, path)
        if _is_item(doc) and doc.get("closed_at") is None:
            yield doc


def _note(doc: dict, item_id: str, kind: str, summary: str, detail: dict, now: float) -> None:
    """JARVIS's own entry in an item's log, as a change it made on his
    answer: the item's update and revision move, its agents' last report
    (reported_at) does not."""
    doc.update(latest_update=summary, revision=doc["revision"] + 1, updated_at=now)
    _add_event(doc, kind, summary, detail, now)


def _release(doc: dict, holder: dict, piece: Optional[str], item_id: str, holds: list[str],
             then: str, ended: list, now: float) -> None:
    """Take the holds `ended` ((hold, Answer) pairs) off a held piece or
    item; once nothing holds it, it moves to its `then` (ready by default).
    Recorded as JARVIS's entry in the item's log, the answer his (or the
    asker's withdrawal), with when and how."""
    gone = {h for h, _ in ended}
    left = [h for h in holds if h not in gone]
    found = ended[-1][1]
    if left:
        summary = _one_line(f"{found.said()}; still waits on {', '.join(left)}.", SUMMARY_MAX)
        holder["until"] = left
        state = None
    else:
        state = then or READY
        summary = _one_line(f"{found.said()}; now {state}.", SUMMARY_MAX)
        holder.update(state=state, until=[], then_state="", waiting_since=None)
    if piece is not None:
        holder.update(note=summary, note_at=now)
    detail = {"wait_ended": [{"link": h, "by": a.by, "how": a.how, "answered_at": a.at}
                             for h, a in ended], "until": left}
    if state is not None:
        detail["state"] = state
    if piece is not None:
        detail["key"] = piece
    _note(doc, item_id, PIECE if piece is not None else UPDATE, summary, detail, now)


def _release_answered(unit, answered, now: float) -> set:
    """Release every hold `answered(link, item id)` gives an answer for, in
    `unit`. The items it moved."""
    moved = set()
    for doc in list(_held_docs(unit)):
        item_id = doc["id"]
        for holder, piece, holds, then in list(_held(doc)):
            ended = [(h, found) for h in holds
                     if (link := _link(h)) and (found := answered(link, item_id))]
            if ended:
                _release(doc, holder, piece, item_id, holds, then, ended, now)
                moved.add(item_id)
        if item_id in moved:
            _stage(unit, doc)
    return moved


def end_waits(unit, found: "wait_links.Answer", now: float) -> set:
    """Release every hold `found` answers, in the caller's unit. The items
    it moved."""
    return _release_answered(
        unit, lambda link, item_id: found if found.ends(link, wait_links.WORK, item_id) else None,
        now)


def sweep_waits() -> "wait_links.Settled":
    """Release every hold whose answer was already given (at start, and on
    every read: wait_links.sweep)."""
    def run(unit) -> set:
        return _release_answered(
            unit, lambda link, item_id: wait_links.already(unit, link, wait_links.WORK, item_id),
            time.time())
    moved = _records.atomic(run, by=_records.JARVIS, change="wait_ended",
                           summary="His answers already given release what waited on them.")
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
    return any(e["kind"] in _REVIEW_ACTS
               and ((e.get("detail") or {}).get("source") or {}).get("package") == source["package"]
               and ((e.get("detail") or {}).get("source") or {}).get("answered_at")
               == source["answered_at"]
               for e in _logged(item_id))


def reviewed_since(item_id: str, since: float) -> bool:
    """Whether he has reviewed the item (approved it, said it is not done
    yet or added work, anywhere) since `since`."""
    return any(e["kind"] in REVIEW_KINDS and e["at"] >= since for e in _logged(item_id))


def _package_review(doc: dict) -> "Optional[PackageReview]":
    """His latest review act, when it was in a review package: what it did."""
    act = doc.get("review_act")
    if not act:
        return None
    detail = dict(act.get("detail") or {})
    if not (detail.get("source") or {}).get("package"):
        return None
    outcome = detail.get("outcome") if act["kind"] == REVIEW_NOTED else act["kind"]
    return PackageReview(outcome, act["at"], said_to_tim(detail.get("why", "")),
                         detail["source"]["package"])
def _shown_review(row, review: "Optional[PackageReview]") -> "Optional[PackageReview]":
    """What the item shows of his package act: never "without a verdict" or
    "not applied" on an item he has approved done (his Approve in the
    package after his Approve in the header was the same act twice)."""
    if (review is not None and review.outcome in (NO_VERDICT, NOT_APPLIED)
            and row["state"] == DONE):
        return None
    return review


# Why an answer of his in a package could not be applied, in his own terms:
# the refusals are worded for the reporting agents, never shown to him as
# they are (a refusal's start, then what he reads).
_SAID_TO_TIM = (
    ("Say it in his words", "you gave no words with it"),
    ("Decision ", "a decision of yours on it is still open"),
    ("It is not ready for review", "it is not ready for your review yet"),
    ("A package sent for it still asks", "another package for it still asks you something"),
    ("He approved this done", "it is already done"),
)


def said_to_tim(why: str) -> str:
    """A refusal, as Tim reads why his answer was not applied."""
    text = str(why or "")
    if text.startswith("Piece "):
        if " is still working" in text:
            return "a piece of it is still being worked on"
        return "a piece of it is still waiting or blocked"
    for start, said in _SAID_TO_TIM:
        if text.startswith(start):
            return said
    return "it could not be applied" if text else ""


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
    def when(d: dict) -> float:
        return d["answered_at"] if d["answered_at"] is not None else d["signed_off_at"]
    found = sorted(((doc, d) for doc in _docs() for d in doc["decisions"]
                    if d["asked_by"] == asked_by and _his(d) and when(d) is not None
                    and when(d) >= since), key=lambda pair: when(pair[1]))
    return [GivenAnswer(doc["id"], doc["title"], d["key"], d["question"], *_closed_how(d))
            for doc, d in found[:limit]]


# ── the move from SQLite (knowledge_move) ───────────────────────────────

def _doc_from_rows(item, pieces: list, decisions: list, logged: list) -> tuple[dict, list]:
    """An item's document from its rows (`knowledge_move.Row`s), and its
    log as the versions before it (each `{id, events: [one]}`: the item as
    it stood then was never kept), oldest first."""
    from jarvis.knowledge_move import Revision
    cells = item.cells
    doc = {c: cells[c] for c in ITEM_COLUMNS}
    doc["until"] = holds_of(cells["until"])
    record_cells.keep(doc, "until", cells["until"], doc["until"], _until_text)
    doc["rowid"] = item.rowid
    doc["pieces"] = []
    for r in sorted(pieces, key=lambda r: (r.cells["position"], r.rowid or 0)):
        p = {c: r.cells[c] for c in PIECE_COLUMNS if c != "item_id"}
        for name in _JSON_PIECE:
            p[name] = record_cells.loads(r.cells[name], [])
            record_cells.keep(p, name, r.cells[name], p[name])
        p["until"] = holds_of(r.cells["until"])
        record_cells.keep(p, "until", r.cells["until"], p["until"], _until_text)
        p["rowid"] = r.rowid
        doc["pieces"].append(p)
    doc["decisions"] = []
    for r in decisions:
        d = {c: r.cells[c] for c in DECISION_COLUMNS if c != "item_id"}
        d["options"] = record_cells.loads(r.cells["options"], [])
        record_cells.keep(d, "options", r.cells["options"], d["options"])
        d["rowid"] = r.rowid
        doc["decisions"].append(d)
    doc.update({record_cells.EVENTS: [], "last_seq": 0, "review_act": None})
    history = []
    for r in sorted(logged, key=lambda r: r.cells["seq"]):
        e = {c: r.cells[c] for c in EVENT_COLUMNS if c != "item_id"}
        e["detail"] = record_cells.loads(r.cells["detail"], {})
        record_cells.keep(e, "detail", r.cells["detail"], e["detail"])
        doc["last_seq"] = max(doc["last_seq"], e["seq"])
        if e["kind"] in _REVIEW_ACTS:
            doc["review_act"] = {"kind": e["kind"], "detail": e["detail"], "at": e["at"]}
        history.append(Revision({"id": doc["id"], record_cells.EVENTS: [e]},
                                by=_caller(e["kind"], e["reporter"]), change=e["kind"],
                                summary=e["summary"]))
    doc["held"] = any(True for _ in _held(doc))
    return doc, history


class _WorkMapping:
    """work_items, work_pieces, work_decisions and work_events as one Work
    item's document each, its log as the document's versions."""
    name = "work"
    tables = TABLES
    under = (ROOT,)
    types = ("work",)
    history = True

    def documents(self, conn):
        from jarvis.knowledge_move import Doc
        by_item: dict[str, dict[str, list]] = {}
        for table in TABLES[1:]:
            for r in record_cells.table_rows(conn, table):
                by_item.setdefault(r.cells["item_id"], {}).setdefault(table, []).append(r)
        for item in record_cells.table_rows(conn, "work_items"):
            mine = by_item.get(item.cells["id"], {})
            doc, history = _doc_from_rows(item, mine.get("work_pieces", []),
                                          mine.get("work_decisions", []),
                                          mine.get("work_events", []))
            yield Doc(path_of(doc["id"]), doc, history=tuple(history))

    def rows(self, docs):
        from jarvis.knowledge_move import Row
        out: dict[str, list] = {t: [] for t in TABLES}
        logged: list[dict] = []
        for d in docs:
            if not _is_item(d.data):
                continue
            item_id = d.data["id"]
            out["work_items"].append(Row(item_row(d.data), d.data.get("rowid")))
            out["work_pieces"] += [Row(_piece_row(item_id, p), p.get("rowid"))
                                   for p in d.data["pieces"]]
            out["work_decisions"] += [Row(_decision_row(item_id, x), x.get("rowid"))
                                      for x in d.data["decisions"]]
            logged += [{**e, "item_id": item_id} for e in record_cells.history_events(d)]
        record_cells.resolve_seqs(logged)
        out["work_events"] = [Row({"seq": e["seq"], "item_id": e["item_id"], "kind": e["kind"],
                                   "summary": e["summary"],
                                   "detail": record_cells.text_of(e, "detail", e["detail"]),
                                   "at": e["at"], "reporter": e.get("reporter") or ""})
                              for e in logged]
        return out


MAPPINGS = [_WorkMapping()]
