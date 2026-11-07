"""The package inbox: every package an agent sends Tim, from its arrival
until it is closed (docs/planning/package-inbox/design.md).

A package arrives one way, `python -m jarvis.make_package`, which posts the
zip to `POST /api/packages/inbox` (package_inbox_api.py). It is checked and
unpacked as any local zip is (work_packages.unpack_held), recorded here, and
shown nowhere: it reaches his screen only on his own act (his Open, or
asking JARVIS on a turn he drove).

One row per arrival, `package_inbox`, and every change to it one line of
`package_inbox_events` (no words of the package). The rows are the backlog
the Queue's Packages section lists and the brain's `package_inbox` reads.
This module is the only writer: one transaction per change, the revision
moved inside it, written before any hint. The tables live in JARVIS's one
database under their own schema version (`package_inbox_schema`,
run_store.migrate).

States, in order:

    arrived -> opened -> answering -> answered -> closed

closed with why: `signed_off` (he is done with it: whatever he answered, or
that he has reviewed it, goes back to its sender, `signed_off_at` its
time), `dismissed` (he set it aside, nothing sent back) or `superseded` (a
newer one of the same name came from the same sender while it was open).
A package unpacked here outside the inbox gets a row when he signs it off
(`adopt`), so every package is signed off the same way.

A decision package (the shared deck's: `deck-data.js` and
`scripts/decide.py`) has its questions in `deck-data.js`, read as JSON after
`window.DECK = ` and never executed, and his answers in its own record,
`results/decisions.json`, which the deck's page and `decide.py` both write.
The row only mirrors how many of the questions that apply are answered,
read again after each change of that record (`questions`, `counts`).

Everything a package says (its title, its questions) is the sender's words,
shown and spoken only through the same walls as any other.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from typing import Optional

from jarvis import decision_stamp, run_store, work_packages
from jarvis.work_packages import Package, PackageError

ARRIVED, OPENED, ANSWERING, ANSWERED, CLOSED = (
    "arrived", "opened", "answering", "answered", "closed")
STATES = (ARRIVED, OPENED, ANSWERING, ANSWERED, CLOSED)
# Not closed: in the backlog.
OPEN_STATES = STATES[:-1]
SIGNED_OFF, DISMISSED, SUPERSEDED = "signed_off", "dismissed", "superseded"
CLOSED_WHY = (SIGNED_OFF, DISMISSED, SUPERSEDED)

# How a change came about, as each event records it.
BY_VOICE, ON_THE_PAGE, BY_THE_SENDER, BY_JARVIS = (
    "by voice", "on the page", "by the sender", "by JARVIS")
HOWS = (BY_VOICE, ON_THE_PAGE, BY_THE_SENDER, BY_JARVIS)

# The deck's own files (decision_stamp.MARKS) and how its data is written.
DECK_DATA, DECIDE = decision_stamp.MARKS
DATA_PREFIX = "window.DECK = "
RECORD = "decisions.json"
SUMMARY = "decisions.md"
# The answers a decision takes besides its options, as decide.py names them.
LATER, NONE = "later", "none"
SPECIAL = {LATER: "Decide later", NONE: "None of these"}

# A decision's and an option's id, as the deck's own checks make them
# (decision_package.SLUG): the only words of a deck JARVIS ever says
# outside a wall. A decision or option with any other id is not read.
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

TITLE_MAX = 120
REF_MAX = 128
LIST_MAX = 100

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS package_inbox (
        id                 TEXT PRIMARY KEY,
        package_id         TEXT NOT NULL,
        sha256             TEXT NOT NULL,
        name               TEXT NOT NULL,
        version            TEXT NOT NULL,
        title              TEXT NOT NULL,
        purpose            TEXT NOT NULL,
        runs               TEXT NOT NULL,
        decisions_total    INTEGER NOT NULL DEFAULT 0,
        decisions_answered INTEGER NOT NULL DEFAULT 0,
        from_project       TEXT NOT NULL DEFAULT '',
        from_session       TEXT NOT NULL DEFAULT '',
        from_agent         TEXT NOT NULL DEFAULT '',
        workflow_id        TEXT,
        work_id            TEXT,
        project_id         TEXT,
        state              TEXT NOT NULL,
        revision           INTEGER NOT NULL,
        capture_id         INTEGER,
        return_asked_at    REAL,
        message_id         INTEGER,
        arrived_at         REAL NOT NULL,
        opened_at          REAL,
        answered_at        REAL,
        returned_at        REAL,
        closed_at          REAL,
        closed_why         TEXT
    );
    CREATE INDEX IF NOT EXISTS package_inbox_by_state ON package_inbox (state, arrived_at);
    CREATE TABLE IF NOT EXISTS package_inbox_events (
        seq      INTEGER PRIMARY KEY AUTOINCREMENT,
        inbox_id TEXT NOT NULL REFERENCES package_inbox(id),
        kind     TEXT NOT NULL,
        revision INTEGER NOT NULL,
        at       REAL NOT NULL,
        how      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS package_inbox_events_by_entry
        ON package_inbox_events (inbox_id, seq);
"""

# 2: when he signed it off (`signed_off`).
_SIGNED_OFF = """
    ALTER TABLE package_inbox ADD COLUMN signed_off_at REAL;
"""

# Each step is appended, never changed (run_store.migrate).
_MIGRATIONS = {1: _SCHEMA, 2: _SIGNED_OFF}

_migrated: set[str] = set()
_migrating = threading.Lock()


class InboxRefused(ValueError):
    """A change the inbox cannot make, with why, in words he can hear."""


@dataclass(frozen=True)
class Sender:
    """Who sent it: the project the command was run for, the session that
    ran it (what the answers go back to) and that agent's one name."""
    project: str = ""
    session: str = ""
    agent: str = ""

    def key(self) -> tuple[str, str]:
        """Who counts as the same sender: the session, else the project."""
        return ("session", self.session) if self.session else ("project", self.project)


@dataclass(frozen=True)
class Entry:
    id: str
    package_id: str
    sha256: str
    name: str
    version: str
    title: str
    purpose: str
    runs: str
    decisions_total: int
    decisions_answered: int
    from_project: str
    from_session: str
    from_agent: str
    workflow_id: Optional[str]
    work_id: Optional[str]
    project_id: Optional[str]
    state: str
    revision: int
    capture_id: Optional[int]
    return_asked_at: Optional[float]
    message_id: Optional[int]
    arrived_at: float
    opened_at: Optional[float]
    answered_at: Optional[float]
    returned_at: Optional[float]
    closed_at: Optional[float]
    closed_why: Optional[str]
    signed_off_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        return self.state != CLOSED

    @property
    def is_decisions(self) -> bool:
        """It asks him decisions (a decision deck with at least one)."""
        return self.decisions_total > 0

    @property
    def waiting(self) -> int:
        """How many of the questions that apply have no answer yet."""
        return max(0, self.decisions_total - self.decisions_answered)

    def sender_name(self) -> str:
        """Who sent it, as it is shown: the agent's one name, else the project."""
        return self.from_agent or self.from_project

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Arrival:
    entry: Entry
    fresh: bool                                        # False: these bytes were already open here
    superseded: list[Entry] = field(default_factory=list)


@dataclass(frozen=True)
class Question:
    """One decision a deck asks, with what is recorded for it."""
    id: str
    question: str
    context: str
    options: list[dict]                 # {"id", "name", "says"}
    recommended: str
    reason: str
    applies: bool                       # a follow-up asked only after its parent's answer
    answer: Optional[dict]              # {"answer", "choice", "note", "at", "recommended"}

    def option_name(self, option_id: str) -> str:
        if option_id in SPECIAL:
            return SPECIAL[option_id]
        return next((o["name"] for o in self.options if o["id"] == option_id), option_id)

    def answered_id(self) -> str:
        """His answer by its id (an option's, later or none), with his own
        words after it; "" while unanswered. Ids only: no word of the deck."""
        if not self.answer:
            return ""
        return self.answer["answer"] + (f" (his words: {self.answer['note']})"
                                        if self.answer.get("note") else "")

    def answered_words(self) -> str:
        """His answer in words: the option's name, Decide later or None of
        these, with his own words after it; "" while unanswered."""
        if not self.answer:
            return ""
        said = self.option_name(self.answer["answer"])
        return said + (f" ({self.answer['note']})" if self.answer.get("note") else "")

    def public(self) -> dict:
        return asdict(self)


# ── the deck's own record ────────────────────────────────────────────────

def is_decision_package(package: Package) -> bool:
    """The shared deck's files are both in its folder, as regular files."""
    return all((package.folder / mark).is_file() and not (package.folder / mark).is_symlink()
               for mark in (DECK_DATA, DECIDE))


def deck(package: Package) -> Optional[dict]:
    """A decision package's deck data, read as JSON after its prefix and
    never executed; None for any other package, or one whose data cannot
    be read."""
    if not is_decision_package(package):
        return None
    try:
        text = work_packages.page_file(package, DECK_DATA).decode("utf-8")
        start = text.index(DATA_PREFIX) + len(DATA_PREFIX)
        data = json.JSONDecoder().raw_decode(text, start)[0]
    except (PackageError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _record(package: Package) -> dict:
    """Its answers as results/decisions.json holds them now; {} when there
    are none or the file cannot be read."""
    raw = work_packages.read_result(package, RECORD)
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def questions(package: Package) -> list[Question]:
    """The deck's decisions in its order, each with whether it applies (a
    follow-up only once its parent's answer opens it, as decide.py says)
    and the answer recorded; [] for a package that asks none."""
    data = deck(package)
    raw = [d for d in (data or {}).get("decisions") or [] if isinstance(d, dict)
           and ID.fullmatch(_text(d.get("id")))]
    if not raw:
        return []
    record = _record(package)
    options = {d["id"]: [{"id": _text(o.get("id")), "name": _text(o.get("name")),
                          "says": _text(o.get("says"))}
                         for o in d.get("options") or []
                         if isinstance(o, dict) and ID.fullmatch(_text(o.get("id")))]
               for d in raw}
    parents: dict[str, tuple[str, list[str]]] = {}
    for d in raw:
        for o in d.get("options") or []:
            if not isinstance(o, dict):
                continue
            for child in o.get("opens") or []:
                parent = parents.setdefault(str(child), (d["id"], []))
                parent[1].append(_text(o.get("id")))

    def answer_of(d_id: str) -> Optional[dict]:
        e = record.get(d_id)
        if not isinstance(e, dict) or not isinstance(e.get("answer"), str):
            return None
        if e["answer"] not in SPECIAL and e["answer"] not in {o["id"] for o in options[d_id]}:
            return None
        return {"answer": e["answer"], "choice": _text(e.get("choice")),
                "note": _text(e.get("note")), "at": _text(e.get("at")),
                "recommended": bool(e.get("recommended"))}

    def applies(d_id: str, seen: frozenset = frozenset()) -> bool:
        if d_id not in parents or d_id in seen:
            return True
        parent, opening = parents[d_id]
        given = answer_of(parent) if parent in options else None
        return bool(given) and given["answer"] in opening and applies(parent, seen | {d_id})

    return [Question(d["id"], _text(d.get("question")), _text(d.get("context")),
                     options[d["id"]], _text(d.get("recommended")), _text(d.get("reason")),
                     applies(d["id"]), answer_of(d["id"]))
            for d in raw]


def counts(asked: list[Question]) -> tuple[int, int]:
    """(answered, total) over the questions that apply."""
    applying = [q for q in asked if q.applies]
    return sum(1 for q in applying if q.answer), len(applying)


def title_of(package: Package) -> str:
    """What it is called: a deck's title, else its name in words."""
    data = deck(package) or {}
    title = _text(data.get("title")) or package.manifest.name.replace("-", " ")
    return " ".join(title.split())[:TITLE_MAX]


def summary(package: Package) -> str:
    """results/decisions.md as decide.py and the page write it, or ""."""
    raw = work_packages.read_result(package, SUMMARY)
    return raw.decode("utf-8", errors="replace") if raw else ""


# ── the record ────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = run_store._connect()
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    if path not in _migrated:
        with _migrating:
            if path not in _migrated:
                run_store.migrate(conn, "package_inbox_schema", _MIGRATIONS, "package inbox")
                conn.commit()
                _migrated.add(path)
    return conn


def _entry(row) -> Entry:
    return Entry(**{k: row[k] for k in row.keys()})


def _ref(value, what: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > REF_MAX:
        raise InboxRefused(f"{what} is longer than {REF_MAX} characters.")
    return text


def _event(conn, inbox_id: str, kind: str, revision: int, now: float, how: str) -> None:
    conn.execute("INSERT INTO package_inbox_events (inbox_id, kind, revision, at, how) "
                 "VALUES (?, ?, ?, ?, ?)", (inbox_id, kind, revision, now, how))


def _change(conn, entry: Entry, kind: str, how: str, now: float, **fields) -> Entry:
    """Move one row on: its fields, its next revision and one event, inside
    the caller's transaction."""
    revision = entry.revision + 1
    sets = ", ".join(f"{k}=?" for k in (*fields, "revision"))
    conn.execute(f"UPDATE package_inbox SET {sets} WHERE id=?",
                 (*fields.values(), revision, entry.id))
    _event(conn, entry.id, kind, revision, now, how)
    return _entry(conn.execute("SELECT * FROM package_inbox WHERE id=?", (entry.id,)).fetchone())


def _one(conn, inbox_id: str) -> Entry:
    row = conn.execute("SELECT * FROM package_inbox WHERE id=?", (str(inbox_id),)).fetchone()
    if row is None:
        raise InboxRefused("There is no package by that id in the inbox; package_inbox lists "
                           "them.")
    return _entry(row)


def arrive(package: Package, sha256: str, sender: Sender, *, workflow_id: str = "",
           work_id: str = "", now: Optional[float] = None) -> Arrival:
    """Record a package that has just been unpacked here, unshown. The same
    bytes while their row is still open are that row (fresh False). Any
    open package of the same name from the same sender is closed as
    superseded, in the same transaction."""
    now = time.time() if now is None else now
    sender = Sender(_ref(sender.project, "The project"), _ref(sender.session, "The session"),
                    _ref(sender.agent, "The agent's name"))
    answered, total = counts(questions(package))
    m = package.manifest
    with closing(_connect()) as conn, conn:
        same = conn.execute(
            f"SELECT * FROM package_inbox WHERE sha256=? AND state IN "
            f"({','.join('?' * len(OPEN_STATES))}) ORDER BY arrived_at DESC LIMIT 1",
            (sha256, *OPEN_STATES)).fetchone()
        if same is not None:
            return Arrival(_entry(same), False)
        column, value = {"session": ("from_session", sender.session),
                         "project": ("from_project", sender.project)}[sender.key()[0]]
        older = [_entry(r) for r in conn.execute(
            f"SELECT * FROM package_inbox WHERE name=? AND {column}=? AND state IN "
            f"({','.join('?' * len(OPEN_STATES))})", (m.name, value, *OPEN_STATES))] \
            if value else []
        superseded = [_change(conn, e, "closed", BY_THE_SENDER, now, state=CLOSED,
                              closed_at=now, closed_why=SUPERSEDED) for e in older]
        inbox_id = "pkg-" + secrets.token_hex(6)
        conn.execute(
            "INSERT INTO package_inbox (id, package_id, sha256, name, version, title, purpose, "
            "runs, decisions_total, decisions_answered, from_project, from_session, "
            "from_agent, workflow_id, work_id, state, revision, arrived_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (inbox_id, package.id, sha256, m.name, m.version, title_of(package), m.purpose,
             m.runs, total, answered, sender.project, sender.session, sender.agent,
             workflow_id or None, work_id or None, ARRIVED, now))
        _event(conn, inbox_id, "arrived", 1, now, BY_THE_SENDER)
        entry = _one(conn, inbox_id)
    return Arrival(entry, True, superseded)


def get(inbox_id: str) -> Optional[Entry]:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM package_inbox WHERE id=?", (str(inbox_id),)).fetchone()
    return _entry(row) if row else None


def listing(closed: bool = False, limit: int = LIST_MAX) -> list[Entry]:
    """The open packages, newest first; with `closed`, the closed ones,
    most recently closed first."""
    query = ("SELECT * FROM package_inbox WHERE state=? ORDER BY closed_at DESC LIMIT ?"
             if closed else
             f"SELECT * FROM package_inbox WHERE state IN ({','.join('?' * len(OPEN_STATES))}) "
             f"ORDER BY arrived_at DESC LIMIT ?")
    args = (CLOSED,) if closed else OPEN_STATES
    with closing(_connect()) as conn:
        return [_entry(r) for r in conn.execute(query, (*args, max(1, min(limit, LIST_MAX))))]


def closed_count() -> int:
    with closing(_connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM package_inbox WHERE state=?",
                            (CLOSED,)).fetchone()[0]


def for_package(package_id: str) -> list[Entry]:
    """The open rows that are this unpacked package."""
    with closing(_connect()) as conn:
        return [_entry(r) for r in conn.execute(
            f"SELECT * FROM package_inbox WHERE package_id=? AND state IN "
            f"({','.join('?' * len(OPEN_STATES))})", (package_id, *OPEN_STATES))]


def for_work(work_id: str) -> list[Entry]:
    """The open packages sent for this work item, oldest first."""
    with closing(_connect()) as conn:
        return [_entry(r) for r in conn.execute(
            f"SELECT * FROM package_inbox WHERE work_id=? AND state IN "
            f"({','.join('?' * len(OPEN_STATES))}) ORDER BY arrived_at", (work_id, *OPEN_STATES))]


def waiting_by_work() -> dict[str, int]:
    """For each work item, how many questions its open packages still wait
    on him for, from the counts the rows keep (no file is read)."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT work_id, SUM(MAX(decisions_total - decisions_answered, 0)) AS waiting "
            f"FROM package_inbox WHERE work_id IS NOT NULL AND state IN "
            f"({','.join('?' * len(OPEN_STATES))}) GROUP BY work_id", OPEN_STATES).fetchall()
    return {r["work_id"]: int(r["waiting"] or 0) for r in rows if r["waiting"]}


def find(reference: str) -> list[Entry]:
    """The open packages `reference` names: an inbox id, its package's id,
    its name, or words of its title or its sender ("the tea one", "from
    chitauri"), best fit first; every open one for no words."""
    reference = " ".join(str(reference or "").split())
    rows = listing()
    if not reference:
        return rows
    exact = [e for e in rows if reference in (e.id, e.package_id, e.name)]
    if exact:
        return exact
    from jarvis import spoken_words
    words = set(spoken_words.folded_words(reference)) - {"the", "package", "one", "from",
                                                          "el", "la", "paquete", "de"}
    scored = []
    for e in rows:
        have = set(spoken_words.folded_words(f"{e.title} {e.name} {e.sender_name()}"))
        score = len(words & have)
        if score:
            scored.append((score, e))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [e for s, e in scored if s == best]


# ── moving one on ────────────────────────────────────────────────────────

def opened(inbox_id: str, how: str, now: Optional[float] = None) -> Entry:
    """He has been shown it (or JARVIS has it in hand, for a package that is
    not a page): an arrived one is opened. Every open is an event."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        entry = _one(conn, inbox_id)
        if not entry.is_open:
            raise InboxRefused(f"{entry.id} is closed ({entry.closed_why}).")
        fields = {"opened_at": entry.opened_at or now}
        if entry.state == ARRIVED:
            fields["state"] = OPENED
        return _change(conn, entry, "opened", how, now, **fields)


def counted(inbox_id: str, answered: int, total: int, how: str,
            now: Optional[float] = None) -> Entry:
    """The deck's record has changed: its counts, and with them its state
    (answering while some wait, answered once none do). A returned or
    closed one keeps its state; an unchanged count changes nothing."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        entry = _one(conn, inbox_id)
        if (entry.decisions_answered, entry.decisions_total) == (answered, total):
            return entry
        fields: dict = {"decisions_answered": answered, "decisions_total": total}
        if entry.state in (ARRIVED, OPENED, ANSWERING, ANSWERED) and total:
            if answered >= total:
                state = ANSWERED
            elif answered:
                state = ANSWERING
            else:
                state = ARRIVED if entry.state == ARRIVED else OPENED
            fields["state"] = state
            if state == ANSWERED:
                fields["answered_at"] = now
        return _change(conn, entry, "answered" if fields.get("state") == ANSWERED else "counted",
                       how, now, **fields)


def signed_off(inbox_id: str, capture_id: Optional[int], how: str,
               now: Optional[float] = None) -> Entry:
    """He has signed it off: done with it, whatever is answered so far. Its
    sign-off and its time are recorded and it is closed, `signed_off`. With
    a capture (the message to its sender, which goes only on his yes), that
    message is followed (`follow_messages`)."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        entry = _one(conn, inbox_id)
        if not entry.is_open:
            raise InboxRefused(f"{entry.id} is already closed ({entry.closed_why}).")
        return _change(conn, entry, "signed_off", how, now, state=CLOSED, closed_at=now,
                       closed_why=SIGNED_OFF, signed_off_at=now, capture_id=capture_id,
                       return_asked_at=now)


def adopt(package: Package, sha256: str, how: str) -> Entry:
    """A record for a package unpacked here before the inbox, or outside it
    (an older local one), so it is signed off the same way: arrived from no
    one known, and opened, since he has it in front of him."""
    entry = arrive(package, sha256, Sender()).entry
    return opened(entry.id, how)


# A message's statuses once it has gone to its session (run_store.MessageStatus).
_GONE = ("sent", "undelivered", "delivered", "working", "answered")


def follow_messages(now: Optional[float] = None) -> list[Entry]:
    """Bring the signed-off rows up to date with the messages that carry
    their answers back, from the `messages` record: a row whose capture has
    gone to its sender since he signed it off is given that message's id.
    The rows that moved."""
    now = time.time() if now is None else now
    moved = []
    with closing(_connect()) as conn, conn:
        try:
            for e in [_entry(r) for r in conn.execute(
                    "SELECT * FROM package_inbox WHERE closed_why=? AND capture_id IS NOT NULL "
                    "AND message_id IS NULL", (SIGNED_OFF,))]:
                m = conn.execute(
                    f"SELECT id FROM messages WHERE capture_id=? AND session_id=? AND "
                    f"staged_at>=? AND status IN ({','.join('?' * len(_GONE))}) "
                    f"ORDER BY id DESC LIMIT 1",
                    (e.capture_id, e.from_session, e.return_asked_at or 0, *_GONE)).fetchone()
                if m is not None:
                    moved.append(_change(conn, e, "returned", BY_JARVIS, now, returned_at=now,
                                         message_id=m["id"]))
        except sqlite3.OperationalError:      # no messages table yet: nothing has gone
            return []
    return moved


def close(inbox_id: str, how: str, now: Optional[float] = None) -> Entry:
    """He sets it aside without signing it off: dismissed, whatever is
    answered so far kept in its record and nothing sent back."""
    now = time.time() if now is None else now
    with closing(_connect()) as conn, conn:
        entry = _one(conn, inbox_id)
        if not entry.is_open:
            raise InboxRefused(f"{entry.id} is already closed ({entry.closed_why}).")
        return _change(conn, entry, "closed", how, now, state=CLOSED, closed_at=now,
                       closed_why=DISMISSED)


def events(inbox_id: str) -> list[dict]:
    with closing(_connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM package_inbox_events WHERE inbox_id=? ORDER BY seq", (inbox_id,))]
