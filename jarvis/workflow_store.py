"""The workflow monitor's record: one durable account of every long workflow.

A workflow is a long piece of work, a phased build or a multi-agent run,
carried out by one or more sessions. Whatever runs it reports each step
here, and everything that shows or says how a workflow is going (the
Workflows view, `workflow_status`, JARVIS's spoken updates) reads it from
here, so JARVIS can always say the latest state, after a restart, a brain
reset or a dropped page alike.

The truth is `workflow_events`, which is only ever appended to. `workflows`,
`workflow_items` (its phases and tasks) and `workflow_open` (its open waits,
decisions and blockers) are the snapshot of it, updated in the SAME
transaction as each event is inserted, so a reader never sees one without
the other. `record` is the only writer.

Whether a workflow has gone quiet is worked out when it is read, never
stored: a running workflow with no event for SILENT_AFTER_SEC reads as
`silent`, so a stale "running" cannot stick.

Everything in a workflow's record (its name, summaries, titles, questions)
was written by the session running it: it is another session's words, and
is shown and spoken only through the same walls as any other.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, get_args

from jarvis import run_store


class WorkflowState(str, Enum):
    running = "running"
    waiting = "waiting"
    blocked = "blocked"
    needs_you = "needs_you"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ItemState(str, Enum):
    pending = "pending"
    running = "running"
    waiting = "waiting"
    blocked = "blocked"
    done = "done"
    failed = "failed"
    skipped = "skipped"


EventKind = Literal[
    "workflow_started", "workflow_finished",
    "phase_started", "phase_finished",
    "task_added", "task_started", "task_progress", "task_finished",
    "wait_started", "wait_ended",              # waiting on what, until when
    "decision_needed", "decision_made",        # question, options, recommendation; answer
    "blocker_raised", "blocker_cleared",
    "agent_attached",                          # session id + provider: the live transcript
    "note",
    "workflow_dismissed",                      # he dismissed it once finished (the page)
]
EVENT_KINDS = frozenset(get_args(EventKind))

# Read-time only: a running or waiting workflow that has reported nothing
# for this long.
SILENT = "silent"
SILENT_AFTER_SEC = 10 * 60

# A finished workflow stays in view this long, so its finish can be seen,
# then counts as dismissed (read-time, like SILENT) unless he dismissed it
# sooner. Dismissed is for good: it is listed as done, never as current.
LINGER_SEC = 2 * 60

FINISHED = frozenset({WorkflowState.succeeded, WorkflowState.failed, WorkflowState.cancelled})
_DONE_ITEMS = frozenset({ItemState.done, ItemState.skipped})

# What a reporter may send. A summary is one sentence; detail is small.
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
NAME_MAX = 80
SUMMARY_MAX = 300
DETAIL_MAX_BYTES = 4096
PHASES_MAX = 50

# What each kind of open item is opened and closed by.
_OPENS = {"wait_started": "wait", "decision_needed": "decision", "blocker_raised": "blocker"}
_CLOSES = {"wait_ended": "wait", "decision_made": "decision", "blocker_cleared": "blocker"}


class WorkflowRefused(ValueError):
    """A start or an event that cannot be recorded, with why."""


@dataclass(frozen=True)
class WorkflowEvent:
    workflow_id: str
    kind: EventKind
    item: str | None          # phase or task slug; None for the workflow itself
    summary: str              # one plain sentence, what JARVIS says
    detail: dict              # kind-specific: progress (done, total), options, until, session_id ...
    at: float = 0.0           # server clock, set on insert
    seq: int = 0              # its place in the log, set on insert


@dataclass(frozen=True)
class OpenItem:
    kind: str                 # wait, decision, blocker
    key: str
    summary: str
    detail: dict
    opened_at: float


@dataclass(frozen=True)
class Item:
    slug: str
    kind: str                 # phase or task
    phase: str | None         # a task's phase
    title: str
    state: ItemState
    done: int | None
    total: int | None


@dataclass(frozen=True)
class WorkflowSnapshot:
    id: str
    name: str
    project: str
    state: str                # a WorkflowState, or "silent" (read time only)
    phase: str | None
    phases_done: int
    phases_total: int
    tasks_done: int
    tasks_total: int
    open_waits: list[str]
    open_decisions: list[str]
    open_blockers: list[str]
    agents: list[str]         # session ids attached
    started_at: float
    last_event_at: float
    last_summary: str
    items: list[Item] = field(default_factory=list)
    open: list[OpenItem] = field(default_factory=list)
    finished_at: float | None = None
    dismissed: bool = False           # he dismissed it, or it lingered its LINGER_SEC
    dismiss_at: float | None = None   # when a finished one still in view dismisses itself


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS workflows (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        project TEXT NOT NULL,
        state TEXT NOT NULL,
        phase TEXT,
        agents TEXT NOT NULL DEFAULT '[]',
        started_at REAL NOT NULL,
        last_event_at REAL NOT NULL,
        last_summary TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS workflow_items (
        workflow_id TEXT NOT NULL REFERENCES workflows(id),
        slug TEXT NOT NULL,
        kind TEXT NOT NULL,
        phase TEXT,
        title TEXT NOT NULL,
        state TEXT NOT NULL,
        done INTEGER,
        total INTEGER,
        position INTEGER NOT NULL,
        PRIMARY KEY (workflow_id, slug)
    );
    CREATE TABLE IF NOT EXISTS workflow_open (
        workflow_id TEXT NOT NULL REFERENCES workflows(id),
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        summary TEXT NOT NULL,
        detail TEXT NOT NULL,
        opened_at REAL NOT NULL,
        PRIMARY KEY (workflow_id, kind, key)
    );
    CREATE TABLE IF NOT EXISTS workflow_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id TEXT NOT NULL REFERENCES workflows(id),
        kind TEXT NOT NULL,
        item TEXT,
        summary TEXT NOT NULL,
        detail TEXT NOT NULL,
        at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS workflow_events_by_workflow
        ON workflow_events (workflow_id, seq);
"""


_LATER_COLUMNS = (("finished_at", "REAL"), ("dismissed_at", "REAL"))


def _connect() -> sqlite3.Connection:
    """The same database as runs and messages, with this module's tables,
    and the columns added after the first of them shipped."""
    conn = run_store._connect()
    conn.executescript(_SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(workflows)")}
    for name, kind in _LATER_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE workflows ADD COLUMN {name} {kind}")
            if name == "finished_at":
                # One finished before the column existed finished at its last event.
                done = tuple(s.value for s in FINISHED)
                conn.execute(f"UPDATE workflows SET finished_at=last_event_at WHERE state IN "
                             f"({','.join('?' * len(done))})", done)
                conn.commit()
    return conn


def _one_line(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:64] or "item"


def _check_slug(value, what: str) -> str:
    slug = str(value or "").strip()
    if not SLUG.fullmatch(slug):
        raise WorkflowRefused(f"{what} must be lowercase letters, digits and dashes, "
                              f"up to 64 characters.")
    return slug


def start(name: str, project: str, phases: list[str]) -> str:
    """Begin recording a workflow; its phases, in order, as slugs or titles.
    Returns its id."""
    name = _one_line(name, NAME_MAX)
    project = _one_line(project, NAME_MAX)
    if not name or not project:
        raise WorkflowRefused("A workflow needs a name and a project.")
    if not isinstance(phases, list) or len(phases) > PHASES_MAX:
        raise WorkflowRefused(f"Give the phases as a list of at most {PHASES_MAX}.")
    titles = [_one_line(p, NAME_MAX) for p in phases if _one_line(p, NAME_MAX)]
    slugs = [_slug(t) for t in titles]
    if len(set(slugs)) != len(slugs):
        raise WorkflowRefused("Two phases have the same name.")
    workflow_id = "wf-" + secrets.token_hex(6)
    now = time.time()
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO workflows (id, name, project, state, started_at, last_event_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (workflow_id, name, project, WorkflowState.running.value, now, now))
        conn.executemany(
            "INSERT INTO workflow_items (workflow_id, slug, kind, title, state, position) "
            "VALUES (?, ?, 'phase', ?, ?, ?)",
            [(workflow_id, slug, title, ItemState.pending.value, i)
             for i, (slug, title) in enumerate(zip(slugs, titles))])
        _insert_event(conn, WorkflowEvent(workflow_id, "workflow_started", None,
                                          f"{name} started.",
                                          {"phases": slugs}), now)
        conn.commit()
    return workflow_id


def _insert_event(conn, event: WorkflowEvent, now: float) -> int:
    cur = conn.execute(
        "INSERT INTO workflow_events (workflow_id, kind, item, summary, detail, at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (event.workflow_id, event.kind, event.item, event.summary,
         json.dumps(event.detail), now))
    return cur.lastrowid


def _clean(event: WorkflowEvent) -> WorkflowEvent:
    if event.kind not in EVENT_KINDS or event.kind == "workflow_started":
        raise WorkflowRefused("Kinds are: " + ", ".join(sorted(EVENT_KINDS - {"workflow_started"})) + ".")
    summary = _one_line(event.summary, SUMMARY_MAX)
    if not summary:
        raise WorkflowRefused("Every event needs a one-sentence summary.")
    detail = event.detail if isinstance(event.detail, dict) else {}
    if len(json.dumps(detail)) > DETAIL_MAX_BYTES:
        raise WorkflowRefused(f"The detail is over {DETAIL_MAX_BYTES} bytes.")
    item = _check_slug(event.item, "The item") if event.item not in (None, "") else None
    if event.kind.startswith(("phase_", "task_")) and item is None:
        raise WorkflowRefused(f"A {event.kind} event names its phase or task.")
    return WorkflowEvent(event.workflow_id, event.kind, item, summary, detail)


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def record(event: WorkflowEvent) -> WorkflowSnapshot:
    """Append one event and bring the snapshot up to date with it, in one
    transaction. The only writer. Refused (WorkflowRefused) if the event is
    malformed, the workflow unknown or already finished, or it names an item
    the workflow does not have."""
    event = _clean(event)
    now = time.time()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM workflows WHERE id=?", (event.workflow_id,)).fetchone()
        if row is None:
            raise WorkflowRefused("There is no workflow with that id.")
        finished = row["state"] in {s.value for s in FINISHED}
        if event.kind == "workflow_dismissed":
            if not finished:
                raise WorkflowRefused("Only a finished workflow is dismissed.")
            if row["dismissed_at"] is not None:
                raise WorkflowRefused("That workflow is already dismissed.")
            conn.execute("UPDATE workflows SET dismissed_at=? WHERE id=?",
                         (now, event.workflow_id))
            _insert_event(conn, event, now)
            conn.commit()
            return snapshot(event.workflow_id)
        if finished:
            raise WorkflowRefused("That workflow has finished; start a new one.")
        _apply(conn, row, event, now)
        _insert_event(conn, event, now)
        _settle_state(conn, event.workflow_id, event, now)
        conn.commit()
    return snapshot(event.workflow_id)


def _item(conn, workflow_id: str, slug: str):
    return conn.execute("SELECT * FROM workflow_items WHERE workflow_id=? AND slug=?",
                        (workflow_id, slug)).fetchone()


def _set_item(conn, workflow_id: str, slug: str, state: ItemState, **counts) -> None:
    sets = ["state=?"] + [f"{k}=?" for k in counts]
    conn.execute(f"UPDATE workflow_items SET {', '.join(sets)} WHERE workflow_id=? AND slug=?",
                 (state.value, *counts.values(), workflow_id, slug))


def _apply(conn, row, event: WorkflowEvent, now: float) -> None:
    wid, kind, slug, detail = event.workflow_id, event.kind, event.item, event.detail
    if kind.startswith("phase_"):
        item = _item(conn, wid, slug)
        if item is None or item["kind"] != "phase":
            raise WorkflowRefused(f"This workflow has no phase {slug}.")
        if kind == "phase_started":
            _set_item(conn, wid, slug, ItemState.running)
            conn.execute("UPDATE workflows SET phase=? WHERE id=?", (slug, wid))
        else:
            failed = detail.get("outcome") == "failed"
            _set_item(conn, wid, slug, ItemState.failed if failed else ItemState.done)
    elif kind == "task_added":
        if _item(conn, wid, slug) is not None:
            raise WorkflowRefused(f"There is already an item {slug}.")
        phase = detail.get("phase") or row["phase"]
        if phase is not None and (_item(conn, wid, str(phase)) or {"kind": ""})["kind"] != "phase":
            raise WorkflowRefused(f"This workflow has no phase {phase}.")
        position = conn.execute("SELECT COUNT(*) FROM workflow_items WHERE workflow_id=?",
                                (wid,)).fetchone()[0]
        conn.execute(
            "INSERT INTO workflow_items (workflow_id, slug, kind, phase, title, state, total, "
            "position) VALUES (?, ?, 'task', ?, ?, ?, ?, ?)",
            (wid, slug, phase, _one_line(detail.get("title") or event.summary, NAME_MAX),
             ItemState.pending.value, _int(detail.get("total")), position))
    elif kind.startswith("task_"):
        item = _item(conn, wid, slug)
        if item is None or item["kind"] != "task":
            raise WorkflowRefused(f"This workflow has no task {slug}; add it with task_added.")
        if kind == "task_started":
            _set_item(conn, wid, slug, ItemState.running)
        elif kind == "task_progress":
            done, total = _int(detail.get("done")), _int(detail.get("total"))
            _set_item(conn, wid, slug, ItemState.running,
                      done=done if done is not None else item["done"],
                      total=total if total is not None else item["total"])
        else:
            outcome = {"failed": ItemState.failed, "skipped": ItemState.skipped}.get(
                detail.get("outcome"), ItemState.done)
            _set_item(conn, wid, slug, outcome)
    elif kind in _OPENS:
        key = _open_key(event)
        conn.execute(
            "INSERT OR REPLACE INTO workflow_open (workflow_id, kind, key, summary, detail, "
            "opened_at) VALUES (?, ?, ?, ?, ?, ?)",
            (wid, _OPENS[kind], key, event.summary, json.dumps(detail), now))
    elif kind in _CLOSES:
        key = _open_key(event)
        gone = conn.execute("DELETE FROM workflow_open WHERE workflow_id=? AND kind=? AND key=?",
                            (wid, _CLOSES[kind], key)).rowcount
        if not gone:
            raise WorkflowRefused(f"No {_CLOSES[kind]} {key} is open.")
    elif kind == "agent_attached":
        session_id = _one_line(detail.get("session_id"), 128)
        if not session_id:
            raise WorkflowRefused("agent_attached gives the session_id.")
        agents = json.loads(row["agents"])
        if session_id not in agents:
            conn.execute("UPDATE workflows SET agents=? WHERE id=?",
                         (json.dumps(agents + [session_id]), wid))
    elif kind == "workflow_finished":
        outcome = detail.get("outcome", "succeeded")
        if outcome not in {s.value for s in FINISHED}:
            raise WorkflowRefused("A finished workflow succeeded, failed or was cancelled.")
        conn.execute("UPDATE workflows SET finished_at=? WHERE id=?", (now, wid))


def _open_key(event: WorkflowEvent) -> str:
    """Which wait, decision or blocker: `detail.key`, else the item, else
    the summary made a slug. The closing event names the same one."""
    key = event.detail.get("key")
    if key is not None:
        return _check_slug(key, "The key")
    return event.item or _slug(event.summary)


def _settle_state(conn, wid: str, event: WorkflowEvent, now: float) -> None:
    if event.kind == "workflow_finished":
        state = WorkflowState(event.detail.get("outcome", "succeeded"))
    else:
        kinds = {r["kind"] for r in conn.execute(
            "SELECT kind FROM workflow_open WHERE workflow_id=?", (wid,))}
        state = (WorkflowState.needs_you if "decision" in kinds else
                 WorkflowState.blocked if "blocker" in kinds else
                 WorkflowState.waiting if "wait" in kinds else WorkflowState.running)
    conn.execute("UPDATE workflows SET state=?, last_event_at=?, last_summary=? WHERE id=?",
                 (state.value, now, event.summary, wid))


def _read_state(stored: str, last_event_at: float, now: float) -> str:
    """Only a running workflow goes quiet. One that has said it is waiting
    (on a test suite, a build) is waiting, not silent: its open wait shows
    how long, found when the pilot's ten-minute test run was called quiet."""
    if stored == WorkflowState.running.value and now - last_event_at > SILENT_AFTER_SEC:
        return SILENT
    return stored


def _dismissed(row, now: float) -> bool:
    """Dismissed by him, or finished long enough ago to have gone (LINGER_SEC)."""
    if row["dismissed_at"] is not None:
        return True
    return row["finished_at"] is not None and now - row["finished_at"] > LINGER_SEC


def _snapshot(conn, row, now: float) -> WorkflowSnapshot:
    wid = row["id"]
    items = [Item(r["slug"], r["kind"], r["phase"], r["title"], ItemState(r["state"]),
                  r["done"], r["total"])
             for r in conn.execute("SELECT * FROM workflow_items WHERE workflow_id=? "
                                   "ORDER BY position", (wid,))]
    open_items = [OpenItem(r["kind"], r["key"], r["summary"], json.loads(r["detail"]),
                           r["opened_at"])
                  for r in conn.execute("SELECT * FROM workflow_open WHERE workflow_id=? "
                                        "ORDER BY opened_at", (wid,))]
    phases = [i for i in items if i.kind == "phase"]
    tasks = [i for i in items if i.kind == "task"]
    return WorkflowSnapshot(
        id=wid, name=row["name"], project=row["project"],
        state=_read_state(row["state"], row["last_event_at"], now), phase=row["phase"],
        phases_done=sum(i.state in _DONE_ITEMS for i in phases), phases_total=len(phases),
        tasks_done=sum(i.state in _DONE_ITEMS for i in tasks), tasks_total=len(tasks),
        open_waits=[o.key for o in open_items if o.kind == "wait"],
        open_decisions=[o.key for o in open_items if o.kind == "decision"],
        open_blockers=[o.key for o in open_items if o.kind == "blocker"],
        agents=json.loads(row["agents"]), started_at=row["started_at"],
        last_event_at=row["last_event_at"], last_summary=row["last_summary"],
        finished_at=row["finished_at"],
        dismissed=_dismissed(row, now),
        dismiss_at=(row["finished_at"] + LINGER_SEC
                    if row["finished_at"] is not None and not _dismissed(row, now) else None),
        items=items, open=open_items)


def snapshot(workflow_id: str) -> WorkflowSnapshot | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        return _snapshot(conn, row, time.time()) if row else None


def active() -> list[WorkflowSnapshot]:
    """Every workflow not yet finished, most recently heard from first."""
    finished = tuple(s.value for s in FINISHED)
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT * FROM workflows WHERE state NOT IN ({','.join('?' * len(finished))}) "
            "ORDER BY last_event_at DESC", finished).fetchall()
        now = time.time()
        return [_snapshot(conn, r, now) for r in rows]


def recent(limit: int = 20) -> list[WorkflowSnapshot]:
    """Every workflow, finished ones included, most recently heard from first."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM workflows ORDER BY last_event_at DESC LIMIT ?",
                            (limit,)).fetchall()
        now = time.time()
        return [_snapshot(conn, r, now) for r in rows]


def events(workflow_id: str, after: int = 0, kinds: set[str] | None = None,
           limit: int = 500) -> list[WorkflowEvent]:
    """The log, oldest first, from just after `after` (an event's seq)."""
    sql = "SELECT * FROM workflow_events WHERE workflow_id=? AND seq>?"
    args: list = [workflow_id, after]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        args += sorted(kinds)
    sql += " ORDER BY seq LIMIT ?"
    args.append(limit)
    with closing(_connect()) as conn:
        return [WorkflowEvent(r["workflow_id"], r["kind"], r["item"], r["summary"],
                              json.loads(r["detail"]), r["at"], r["seq"])
                for r in conn.execute(sql, args)]


def find(reference: str) -> list[WorkflowSnapshot]:
    """Workflows by what the user called one: its id, or its name as said
    (spoken words, any order of punctuation). Several means ask."""
    from jarvis import agent_directory
    ref = agent_directory.spoken_key(reference)
    candidates = recent(100)
    if not ref:
        return []
    by_id = [w for w in candidates if w.id == reference.strip()]
    if by_id:
        return by_id
    exact = [w for w in candidates if agent_directory.spoken_key(w.name) == ref]
    matches = exact or [w for w in candidates if ref in agent_directory.spoken_key(w.name)]
    # Of a name used twice, the one still running is the one he means.
    running = [w for w in matches if w.state not in {s.value for s in FINISHED}]
    return running if len(running) == 1 else matches


def with_decisions(boards: list) -> list:
    """The needs boards (session_needs.Board), each project's with the open
    decisions of its running workflows added under Decisions, and a board
    for a project that has none of its own. The board files are the
    sessions' own; this never writes them."""
    from jarvis import session_needs
    lines: dict[str, list[str]] = {}
    for w in active():
        for o in w.open:
            if o.kind == "decision":
                lines.setdefault(w.project.lower(), []).append(
                    f"- {w.name} needs a decision ({o.key}): {o.summary}")
    if not lines:
        return boards
    out = []
    for b in boards:
        extra = lines.pop(b.project.lower(), None)
        out.append(session_needs.Board(b.project, b.text + "\n\n## Workflow decisions\n"
                                       + "\n".join(extra), b.mtime) if extra else b)
    for project, extra in lines.items():
        out.append(session_needs.Board(project, "## Workflow decisions\n" + "\n".join(extra),
                                       time.time()))
    return out
