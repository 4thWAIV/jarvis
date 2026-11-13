"""The workflow monitor's record: one durable account of every long workflow.

A workflow is a long piece of work, a phased build or a multi-agent run,
carried out by one or more sessions. Whatever runs it reports each step
here, and everything that shows or says how a workflow is going (the
Workflows view, `workflow_status`, JARVIS's spoken updates) reads it from
here, so JARVIS can always say the latest state, after a restart, a brain
reset or a dropped page alike.

One workflow is one document in JARVIS's records (`records`, the document
engine), `/workflows/<id>.workflow`: its state, its phases and tasks and its
open waits, decisions and blockers, with its log as the document's
versions, each recording its own events. Every event and the state it
brings are one write of that document (`records.atomic`), so a reader never
sees one without the other. `record` is the only writer.

Whether a workflow has gone quiet is worked out when it is read, never
stored: a running workflow with no event for SILENT_AFTER_SEC reads as
`silent`, so a stale "running" cannot stick.

A wait may name the answer it waits for (`detail.until`, a wait_links link:
`decision:<key>`, `work:<item>:<key>`, `workflow:<id>:<key>`,
`package:<id>[:<decision>]`), kept as `detail.link`. His answer to it, by
any route, ends the wait in the same transaction (`end_waits`, a
`wait_ended` recorded as his, with when and how); one already answered is
ended when the record is next read (`sweep_waits`). A wait with no link open
for wait_links.UNLINKED_AFTER_SEC reads as `unlinked`.

Everything in a workflow's record (its name, summaries, titles, questions)
was written by the session running it: it is another session's words, and
is shown and spoken only through the same walls as any other.
"""

from __future__ import annotations

import dataclasses
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional, get_args

from jarvis import record_cells, records, wait_links


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
    "phase_added", "phase_started", "phase_finished",   # added: one not declared at start
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
    # Who reported it, as the route proved it (reporters.recorded: an access
    # key's `key:<label>`; JARVIS's own records leave it empty). Never the
    # report's own word.
    reporter: str = ""


@dataclass(frozen=True)
class OpenItem:
    kind: str                 # wait, decision, blocker
    key: str
    summary: str
    detail: dict
    opened_at: float
    # Read time only: a wait with no link to an answer, open for
    # wait_links.UNLINKED_AFTER_SEC ("waiting, no link to an answer").
    unlinked: bool = False


@dataclass(frozen=True)
class Item:
    slug: str
    kind: str                 # phase or task
    phase: str | None         # a task's phase
    title: str
    state: ItemState
    done: int | None
    total: int | None
    # Read time only: its latest event (a phase's own or any of its tasks'),
    # and, while it is running, when it goes quiet with no event after that.
    heard_at: float | None = None
    quiet_at: float | None = None


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
    reporter: str = ""                # who started it, as the route proved it (reporters.recorded)
    work: str = ""                    # the Work item it is for: its messages go there


# ── the document ────────────────────────────────────────────────────────
#
# One workflow is one document, `/workflows/<id>.workflow` (its id through
# records.segment): its own fields under the names its columns had, its
# phases and tasks (`items`, in their order) and its open waits, decisions
# and blockers (`open`) inline, and the events this version records
# (`events`, record_cells). Its log is the document's versions. Kept in
# the same write as each event, worked out from the log: `last_seq`, when
# each item was last heard from (`heard`), every decision_made (`made`:
# its key, whether it carried an answer, whether it was his, how and when)
# and `linked` (an open wait with a link an answer may end). A row the
# move brought keeps its SQLite rowid (`rowid`).

ROOT, TYPE = "/workflows", "workflow"
WORKFLOW_COLUMNS = ("id", "name", "project", "state", "phase", "agents", "started_at",
                    "last_event_at", "last_summary", "finished_at", "dismissed_at", "reporter",
                    "work")
ITEM_COLUMNS = ("workflow_id", "slug", "kind", "phase", "title", "state", "done", "total",
                "position")
OPEN_COLUMNS = ("workflow_id", "kind", "key", "summary", "detail", "opened_at")
EVENT_COLUMNS = ("seq", "workflow_id", "kind", "item", "summary", "detail", "at", "reporter")
TABLES = ("workflows", "workflow_items", "workflow_open", "workflow_events")
_FINISHED_STATES = frozenset(s.value for s in FINISHED)


def path_of(workflow_id: str) -> str:
    return f"{ROOT}/{records.segment(workflow_id)}.{TYPE}"


def _is_workflow(data) -> bool:
    return isinstance(data, dict) and "project" in data and isinstance(data.get("items"), list)


def ready() -> None:
    """Nothing to make: the records are documents in the engine."""


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


def _caller(event: WorkflowEvent) -> "records.Caller":
    """Who wrote an event: its reporter as the route proved it, else him
    for his own answer, else JARVIS."""
    if event.reporter:
        return records.by(event.reporter)
    if event.kind == "decision_made" and _his(event.detail):
        return records.TIM
    return records.JARVIS


def _made(event: WorkflowEvent) -> Optional[dict]:
    """A decision_made as the document keeps it for `_answered` and
    `decision_answer`; None for one whose key cannot be read."""
    try:
        key = open_key(event)
    except WorkflowRefused:
        return None
    return {"key": key, "answer": "answer" in event.detail, "his": _his(event.detail),
            "how": str(event.detail.get("how") or "page"), "at": event.at}


def _note_event(doc: dict, event: WorkflowEvent) -> None:
    """What the document keeps of one event besides the event itself."""
    if event.item is not None:
        doc["heard"][event.item] = max(doc["heard"].get(event.item, event.at), event.at)
    if event.kind == "decision_made":
        made = _made(event)
        if made is not None:
            doc["made"].append(made)


def _add_event(doc: dict, event: WorkflowEvent, now: float) -> None:
    """One event of the workflow's log, recorded by the version being written."""
    kept = dataclasses.replace(event, at=now, seq=record_cells.next_seq(doc))
    doc.setdefault(record_cells.EVENTS, []).append(
        {"seq": kept.seq, "kind": kept.kind, "item": kept.item, "summary": kept.summary,
         "detail": kept.detail, "at": now, "reporter": kept.reporter})
    _note_event(doc, kept)


def _take(unit, workflow_id: str) -> Optional[dict]:
    doc = record_cells.take(unit, path_of(workflow_id))
    return doc if _is_workflow(doc) else None


def _stage(unit, doc: dict) -> None:
    doc["linked"] = any(True for _ in _linked_waits(doc))
    record_cells.stage(unit, path_of(doc["id"]), doc)


def start(name: str, project: str, phases: list[str], reporter: str = "",
          work: str = "") -> str:
    """Begin recording a workflow; its phases, in order, as slugs or titles;
    `reporter` who started it, as the route proved it; `work` the Work item
    it is for (checked open by the route), where its messages to him go.
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
    started = WorkflowEvent(workflow_id, "workflow_started", None, f"{name} started.",
                            {"phases": slugs}, reporter=reporter)

    def write(unit) -> None:
        if unit.get(path_of(workflow_id)) is not None:
            raise WorkflowRefused("That workflow id is taken; try again.")
        doc = {"id": workflow_id, "name": name, "project": project,
               "state": WorkflowState.running.value, "phase": None, "agents": [],
               "started_at": now, "last_event_at": now, "last_summary": "",
               "finished_at": None, "dismissed_at": None, "reporter": reporter, "work": work,
               "items": [{"slug": slug, "kind": "phase", "phase": None, "title": title,
                          "state": ItemState.pending.value, "done": None, "total": None,
                          "position": i} for i, (slug, title) in enumerate(zip(slugs, titles))],
               "open": [], record_cells.EVENTS: [], "last_seq": 0, "heard": {}, "made": []}
        _add_event(doc, started, now)
        _stage(unit, doc)

    records.atomic(write, by=_caller(started))
    return workflow_id


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
    return WorkflowEvent(event.workflow_id, event.kind, item, summary, detail,
                         reporter=event.reporter)


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def record(event: WorkflowEvent) -> WorkflowSnapshot:
    """Record one event and bring the workflow up to date with it, in one
    write of its document (and of every record his answer releases). The
    only writer. Refused (WorkflowRefused) if the event is malformed, the
    workflow unknown or already finished, or it names an item the workflow
    does not have."""
    event = _clean(event)

    def write(unit) -> "wait_links.Settled":
        now = time.time()
        doc = _take(unit, event.workflow_id)
        if doc is None:
            raise WorkflowRefused("There is no workflow with that id.")
        finished = doc["state"] in _FINISHED_STATES
        if event.kind == "workflow_dismissed":
            if not finished:
                raise WorkflowRefused("Only a finished workflow is dismissed.")
            if doc["dismissed_at"] is not None:
                raise WorkflowRefused("That workflow is already dismissed.")
            doc["dismissed_at"] = now
            _add_event(doc, event, now)
            _stage(unit, doc)
            return wait_links.Settled()
        if finished:
            raise WorkflowRefused("That workflow has finished; start a new one.")
        kept = _apply(doc, event, now) or event
        _add_event(doc, kept, now)
        _settle_state(doc, kept, now)
        _stage(unit, doc)
        return _answered_by(unit, doc, kept, now)

    his = event.kind == "decision_made" and _his(event.detail)
    moved = records.atomic(write, by=_caller(event), full_sync=his)
    wait_links.tell(moved)
    return snapshot(event.workflow_id)


def _answered_by(unit, doc: dict, event: WorkflowEvent, now: float) -> "wait_links.Settled":
    """His answer ends every wait linked to it, here and in the Work
    tracker, in this unit; a wait opened on an answer already given ends at
    once."""
    if event.kind == "decision_made" and _his(event.detail):
        found = wait_links.answer(wait_links.WORKFLOW, {event.workflow_id},
                                  open_key(event), str(event.detail.get("how") or "page"), now)
        return wait_links.settle(unit, found, now)
    if event.kind == "wait_started" and event.detail.get("link"):
        link = wait_links.parse(event.detail["link"])
        found = wait_links.already(unit, link, wait_links.WORKFLOW, event.workflow_id)
        if found is not None:
            _end_wait(doc, event.workflow_id, open_key(event), link, found, now)
            _stage(unit, doc)
            return wait_links.Settled(workflows={event.workflow_id})
    return wait_links.Settled()


def _his(detail: dict) -> bool:
    """A decision_made that is his answer: given by him (the server's
    answer routes say `by: user`; a reporter's never can), with words."""
    return detail.get("by") == "user" and "answer" in detail


def _item(doc: dict, slug: str) -> Optional[dict]:
    return next((i for i in doc["items"] if i["slug"] == slug), None)


def _set_item(doc: dict, slug: str, state: ItemState, **counts) -> None:
    _item(doc, slug).update(state=state.value, **counts)


def _open_item(doc: dict, kind: str, key: str) -> Optional[dict]:
    return next((o for o in doc["open"] if o["kind"] == kind and o["key"] == key), None)


def _apply(doc: dict, event: WorkflowEvent, now: float) -> Optional[WorkflowEvent]:
    """Bring the document up to date with one event; the event as it is
    kept when that differs (a wait with its link), else None."""
    wid, kind, slug, detail = event.workflow_id, event.kind, event.item, event.detail
    if kind == "phase_added":
        if _item(doc, slug) is not None:
            raise WorkflowRefused(f"There is already an item {slug}.")
        if sum(1 for i in doc["items"] if i["kind"] == "phase") >= PHASES_MAX:
            raise WorkflowRefused(f"A workflow has at most {PHASES_MAX} phases.")
        doc["items"].append({"slug": slug, "kind": "phase", "phase": None,
                             "title": _one_line(detail.get("title"), NAME_MAX) or slug,
                             "state": ItemState.pending.value, "done": None, "total": None,
                             "position": len(doc["items"])})
    elif kind.startswith("phase_"):
        item = _item(doc, slug)
        if item is None or item["kind"] != "phase":
            raise WorkflowRefused(f"This workflow has no phase {slug}; add it with phase_added.")
        if kind == "phase_started":
            _set_item(doc, slug, ItemState.running)
            doc["phase"] = slug
        else:
            failed = detail.get("outcome") == "failed"
            _set_item(doc, slug, ItemState.failed if failed else ItemState.done)
    elif kind == "task_added":
        if _item(doc, slug) is not None:
            raise WorkflowRefused(f"There is already an item {slug}.")
        phase = detail.get("phase") or doc["phase"]
        if phase is not None and (_item(doc, str(phase)) or {"kind": ""})["kind"] != "phase":
            raise WorkflowRefused(f"This workflow has no phase {phase}; add it with phase_added.")
        doc["items"].append({"slug": slug, "kind": "task", "phase": phase,
                             "title": _one_line(detail.get("title") or event.summary, NAME_MAX),
                             "state": ItemState.pending.value, "done": None,
                             "total": _int(detail.get("total")), "position": len(doc["items"])})
    elif kind.startswith("task_"):
        item = _item(doc, slug)
        if item is None or item["kind"] != "task":
            raise WorkflowRefused(f"This workflow has no task {slug}; add it with task_added.")
        if kind == "task_started":
            _set_item(doc, slug, ItemState.running)
        elif kind == "task_progress":
            done, total = _int(detail.get("done")), _int(detail.get("total"))
            _set_item(doc, slug, ItemState.running,
                      done=done if done is not None else item["done"],
                      total=total if total is not None else item["total"])
        else:
            outcome = {"failed": ItemState.failed, "skipped": ItemState.skipped}.get(
                detail.get("outcome"), ItemState.done)
            _set_item(doc, slug, outcome)
    elif kind in _OPENS:
        key = open_key(event)
        if kind == "wait_started":
            event = _linked(doc, event, key)
            detail = event.detail
        # An answered key is never asked again: its reporter reads the answer
        # by key (`workflow_report.py answer`), so a new question needs a new key.
        if kind == "decision_needed" and _answered(doc, key):
            raise WorkflowRefused(f"Decision {key} was already answered; ask with a new key.")
        # Opened again while open: it replaces the one opened before.
        doc["open"] = [o for o in doc["open"] if not (o["kind"] == _OPENS[kind]
                                                       and o["key"] == key)]
        doc["open"].append({"kind": _OPENS[kind], "key": key, "summary": event.summary,
                            "detail": detail, "opened_at": now})
        return event
    elif kind in _CLOSES:
        key = open_key(event)
        if _open_item(doc, _CLOSES[kind], key) is None:
            raise WorkflowRefused(f"No {_CLOSES[kind]} {key} is open.")
        doc["open"] = [o for o in doc["open"] if not (o["kind"] == _CLOSES[kind]
                                                       and o["key"] == key)]
    elif kind == "agent_attached":
        session_id = _one_line(detail.get("session_id"), 128)
        if not session_id:
            raise WorkflowRefused("agent_attached gives the session_id.")
        if session_id not in doc["agents"]:
            doc["agents"] = list(doc["agents"]) + [session_id]
    elif kind == "workflow_finished":
        outcome = detail.get("outcome", "succeeded")
        if outcome not in _FINISHED_STATES:
            raise WorkflowRefused("A finished workflow succeeded, failed or was cancelled.")
        doc["finished_at"] = now
    return None


def _linked(doc: dict, event: WorkflowEvent, key: str) -> WorkflowEvent:
    """A wait whose `until` names an answer keeps it as `link`; a decision
    of its own it names must be one this workflow has asked."""
    try:
        link = wait_links.parse(event.detail.get("until"))
    except wait_links.LinkRefused as e:
        raise WorkflowRefused(str(e)) from None
    if link is None:
        return event
    if link.kind == wait_links.DECISION and not (
            _answered(doc, link.key) or _open_item(doc, "decision", link.key)):
        raise WorkflowRefused(f"This workflow has asked no decision {link.key}; ask it with "
                              f"decision_needed first, then wait on it.")
    return dataclasses.replace(event, detail={**event.detail, "link": str(link)})


def decision_answer(unit, wid: str, key: Optional[str]) -> "Optional[wait_links.Answer]":
    """His answer to this workflow's decision `key`, as what it ends, or
    None while he has not given one. Read through `unit` (a condition of
    its write) when one is given."""
    if key is None:
        return None
    rec = (unit.get if unit is not None else records.get)(path_of(str(wid)))
    if rec is None or not _is_workflow(rec.data):
        return None
    for made in rec.data["made"]:
        if made["his"] and made["key"] == key:
            return wait_links.answer(wait_links.WORKFLOW, {wid}, key, made["how"], made["at"])
    return None


def _end_wait(doc: dict, wid: str, key: str, link, found: "wait_links.Answer",
              now: float) -> None:
    """End one wait as his answer: `wait_ended` with when and how, and the
    workflow's state settled again (waiting no longer, unless it has more)."""
    doc["open"] = [o for o in doc["open"] if not (o["kind"] == "wait" and o["key"] == key)]
    event = WorkflowEvent(wid, "wait_ended", None,
                          f"{found.said()} ({link}); the wait is over.",
                          {"key": key, "link": str(link), "by": found.by, "how": found.how,
                           "answered_at": found.at})
    _add_event(doc, event, now)
    _settle_state(doc, event, now)


def _linked_waits(doc: dict):
    """(workflow id, key, link) for every open linked wait of a workflow
    not finished."""
    if doc["state"] in _FINISHED_STATES:
        return
    for o in doc["open"]:
        if o["kind"] != "wait" or (o.get("detail") or {}).get("link") is None:
            continue
        try:
            link = wait_links.parse(o["detail"]["link"])
        except wait_links.LinkRefused:
            continue
        if link is not None:
            yield doc["id"], o["key"], link


def _end_answered(unit, answered, now: float) -> set:
    """End every open wait `answered(link, workflow id)` gives an answer
    for, in `unit`: the workflows the engine finds with a linked wait, and
    those this unit has changed. The workflows it moved."""
    paths = {r.path for r in unit.scan(ROOT, types=[TYPE], where=[["linked", "=", True]])}
    paths |= record_cells.touched(unit, ROOT, TYPE)
    moved = set()
    for path in sorted(paths):
        doc = record_cells.take(unit, path)
        if not _is_workflow(doc):
            continue
        for wid, key, link in list(_linked_waits(doc)):
            found = answered(link, wid)
            if found is not None:
                _end_wait(doc, wid, key, link, found, now)
                moved.add(wid)
        if doc["id"] in moved:
            _stage(unit, doc)
    return moved


def end_waits(unit, found: "wait_links.Answer", now: float) -> set:
    """End every open wait `found` answers, in the caller's unit. The
    workflows it moved."""
    return _end_answered(
        unit, lambda link, wid: found if found.ends(link, wait_links.WORKFLOW, wid) else None,
        now)


def sweep_waits() -> "wait_links.Settled":
    """End every open wait whose answer was already given (at start, and on
    every read: wait_links.sweep)."""
    def run(unit) -> set:
        return _end_answered(
            unit, lambda link, wid: wait_links.already(unit, link, wait_links.WORKFLOW, wid),
            time.time())
    moved = records.atomic(run, by=records.JARVIS, change="wait_ended",
                           summary="His answers already given end the waits on them.")
    return wait_links.Settled(workflows=moved)


def _answered(doc: dict, key: str) -> bool:
    """Whether a decision_made under `key` carried an answer."""
    return any(m["answer"] and m["key"] == key for m in doc["made"])


def open_key(event: WorkflowEvent) -> str:
    """Which wait, decision or blocker: `detail.key`, else the item, else
    the summary made a slug. The closing event names the same one."""
    key = event.detail.get("key")
    if key is not None:
        return _check_slug(key, "The key")
    return event.item or _slug(event.summary)


def _settle_state(doc: dict, event: WorkflowEvent, now: float) -> None:
    if event.kind == "workflow_finished":
        state = WorkflowState(event.detail.get("outcome", "succeeded"))
    else:
        kinds = {o["kind"] for o in doc["open"]}
        state = (WorkflowState.needs_you if "decision" in kinds else
                 WorkflowState.blocked if "blocker" in kinds else
                 WorkflowState.waiting if "wait" in kinds else WorkflowState.running)
    doc.update(state=state.value, last_event_at=now, last_summary=event.summary)


def _read_state(stored: str, last_event_at: float, now: float) -> str:
    """Only a running workflow goes quiet. One that has said it is waiting
    (on a test suite, a build) is waiting, not silent: its open wait shows
    how long, found when the pilot's ten-minute test run was called quiet."""
    if stored == WorkflowState.running.value and now - last_event_at > SILENT_AFTER_SEC:
        return SILENT
    return stored


def _dismissed(doc: dict, now: float) -> bool:
    """Dismissed by him, or finished long enough ago to have gone (LINGER_SEC)."""
    if doc["dismissed_at"] is not None:
        return True
    return doc["finished_at"] is not None and now - doc["finished_at"] > LINGER_SEC


def _heard(doc: dict, items: list[dict]) -> dict[str, float]:
    """Each item's latest event: its own, and for a phase its tasks' too."""
    own = doc["heard"]
    heard = {i["slug"]: own[i["slug"]] for i in items if i["slug"] in own}
    for i in items:
        if i["kind"] == "task" and i["phase"] and i["slug"] in own:
            heard[i["phase"]] = max(heard.get(i["phase"], 0.0), own[i["slug"]])
    return heard


def _quiet_at(state: str, heard_at: float | None) -> float | None:
    """When a running item goes quiet: SILENT_AFTER_SEC after its latest
    event (at once if it has none). None for any other state."""
    if state != ItemState.running.value:
        return None
    return (heard_at or 0.0) + SILENT_AFTER_SEC


def _unlinked(o: dict, now: float) -> bool:
    """A wait with no link to an answer, open too long to be left unsaid."""
    return (o["kind"] == "wait" and "link" not in (o.get("detail") or {})
            and now - o["opened_at"] > wait_links.UNLINKED_AFTER_SEC)


def _snapshot(doc: dict, now: float) -> WorkflowSnapshot:
    wid = doc["id"]
    rows = sorted(doc["items"], key=lambda i: i["position"])
    heard = _heard(doc, rows)
    items = [Item(r["slug"], r["kind"], r["phase"], r["title"], ItemState(r["state"]),
                  r["done"], r["total"], heard_at=heard.get(r["slug"]),
                  quiet_at=_quiet_at(r["state"], heard.get(r["slug"])))
             for r in rows]
    open_items = [OpenItem(o["kind"], o["key"], o["summary"], dict(o.get("detail") or {}),
                           o["opened_at"], unlinked=_unlinked(o, now))
                  for o in sorted(doc["open"], key=lambda o: o["opened_at"])]
    phases = [i for i in items if i.kind == "phase"]
    tasks = [i for i in items if i.kind == "task"]
    return WorkflowSnapshot(
        id=wid, name=doc["name"], project=doc["project"],
        state=_read_state(doc["state"], doc["last_event_at"], now), phase=doc["phase"],
        phases_done=sum(i.state in _DONE_ITEMS for i in phases), phases_total=len(phases),
        tasks_done=sum(i.state in _DONE_ITEMS for i in tasks), tasks_total=len(tasks),
        open_waits=[o.key for o in open_items if o.kind == "wait"],
        open_decisions=[o.key for o in open_items if o.kind == "decision"],
        open_blockers=[o.key for o in open_items if o.kind == "blocker"],
        agents=list(doc["agents"]), started_at=doc["started_at"],
        last_event_at=doc["last_event_at"], last_summary=doc["last_summary"],
        finished_at=doc["finished_at"],
        dismissed=_dismissed(doc, now),
        dismiss_at=(doc["finished_at"] + LINGER_SEC
                    if doc["finished_at"] is not None and not _dismissed(doc, now) else None),
        items=items, open=open_items, reporter=doc["reporter"], work=doc["work"])


def _docs() -> list[dict]:
    """Every workflow's document, the most recently heard from first."""
    found = [r.data for r in records.scan(ROOT, types=[TYPE]) if _is_workflow(r.data)]
    return sorted(found, key=lambda d: d["last_event_at"], reverse=True)


def snapshot(workflow_id: str) -> WorkflowSnapshot | None:
    wait_links.sweep()
    rec = records.get(path_of(str(workflow_id)))
    if rec is None or not _is_workflow(rec.data):
        return None
    return _snapshot(rec.data, time.time())


def active(sweep: bool = True) -> list[WorkflowSnapshot]:
    """Every workflow not yet finished, most recently heard from first.
    Without `sweep`, as recorded, for a reader polled several times a second
    (the worker inventory, which a report's write reads through its pieces'
    live owners): the Work view's tick sweeps every SWEEP_EVERY_SEC."""
    if sweep:
        wait_links.sweep()
    now = time.time()
    return [_snapshot(d, now) for d in _docs() if d["state"] not in _FINISHED_STATES]


def recent(limit: int = 20) -> list[WorkflowSnapshot]:
    """Every workflow, finished ones included, most recently heard from first."""
    wait_links.sweep()
    now = time.time()
    return [_snapshot(d, now) for d in _docs()[:limit]]


def projects() -> dict[str, int]:
    """How many workflows each project has had (project_seed's evidence)."""
    found: dict[str, int] = {}
    for d in _docs():
        found[d["project"]] = found.get(d["project"], 0) + 1
    return found


def _event(workflow_id: str, e: dict) -> WorkflowEvent:
    return WorkflowEvent(workflow_id, e["kind"], e.get("item"), e["summary"],
                         dict(e.get("detail") or {}), e["at"], e["seq"], e.get("reporter") or "")


def _logged(workflow_id: str, newest: Optional[int] = None) -> list[dict]:
    """The workflow's log, oldest first, from its document's versions: all
    of it, or at least its `newest` events (read from the newest versions
    on, a few at a time, never more than it needs)."""
    path = path_of(str(workflow_id))
    if newest is None:
        return [e for v in records.history(path, fields=[record_cells.EVENTS])
                for e in (v.data or {}).get(record_cells.EVENTS) or []]
    versions = max(newest, 8)
    while True:
        found = records.history(path, fields=[record_cells.EVENTS], limit=versions)
        events = [e for v in found for e in (v.data or {}).get(record_cells.EVENTS) or []]
        if len(events) >= newest or len(found) < versions:
            return events
        versions *= 4


def events(workflow_id: str, after: int = 0, kinds: set[str] | None = None,
           limit: int = 500) -> list[WorkflowEvent]:
    """The log, oldest first, from just after `after` (an event's seq)."""
    found = sorted((e for e in _logged(workflow_id) if e["seq"] > after
                    and (not kinds or e["kind"] in kinds)), key=lambda e: e["seq"])
    return [_event(workflow_id, e) for e in found[:limit]]


def latest(workflow_id: str, count: int) -> list[WorkflowEvent]:
    """A workflow's newest `count` events, oldest of them first: read from
    its newest versions only (bounded by `count`, never its whole log)."""
    if count <= 0:
        return []
    found = sorted(_logged(workflow_id, newest=count), key=lambda e: e["seq"])
    return [_event(workflow_id, e) for e in found[-count:]]


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


# ── the move from SQLite (knowledge_move) ───────────────────────────────

def _doc_from_rows(row, items: list, opened: list, logged: list) -> tuple[dict, list]:
    """A workflow's document from its rows (`knowledge_move.Row`s), and its
    log as the versions before it (each `{id, events: [one]}`: the workflow
    as it stood then was never kept), oldest first."""
    from jarvis.knowledge_move import Revision
    doc = {c: row.cells[c] for c in WORKFLOW_COLUMNS}
    doc["agents"] = record_cells.loads(row.cells["agents"], [])
    record_cells.keep(doc, "agents", row.cells["agents"], doc["agents"])
    doc["rowid"] = row.rowid
    doc["items"] = []
    for r in sorted(items, key=lambda r: (r.cells["position"], r.rowid or 0)):
        i = {c: r.cells[c] for c in ITEM_COLUMNS if c != "workflow_id"}
        i["rowid"] = r.rowid
        doc["items"].append(i)
    doc["open"] = []
    for r in opened:
        o = {c: r.cells[c] for c in OPEN_COLUMNS if c != "workflow_id"}
        o["detail"] = record_cells.loads(r.cells["detail"], {})
        record_cells.keep(o, "detail", r.cells["detail"], o["detail"])
        o["rowid"] = r.rowid
        doc["open"].append(o)
    doc.update({record_cells.EVENTS: [], "last_seq": 0, "heard": {}, "made": []})
    history = []
    for r in sorted(logged, key=lambda r: r.cells["seq"]):
        e = {c: r.cells[c] for c in EVENT_COLUMNS if c != "workflow_id"}
        e["detail"] = record_cells.loads(r.cells["detail"], {})
        record_cells.keep(e, "detail", r.cells["detail"], e["detail"])
        event = _event(doc["id"], e)
        doc["last_seq"] = max(doc["last_seq"], e["seq"])
        _note_event(doc, event)
        history.append(Revision({"id": doc["id"], record_cells.EVENTS: [e]},
                                by=_caller(event), change=e["kind"], summary=e["summary"]))
    doc["linked"] = any(True for _ in _linked_waits(doc))
    return doc, history


class _WorkflowMapping:
    """workflows, workflow_items, workflow_open and workflow_events as one
    workflow's document each, its log as the document's versions."""
    name = "workflow"
    tables = TABLES
    under = (ROOT,)
    history = True

    def documents(self, conn):
        from jarvis.knowledge_move import Doc
        by_workflow: dict[str, dict[str, list]] = {}
        for table in TABLES[1:]:
            for r in record_cells.table_rows(conn, table):
                by_workflow.setdefault(r.cells["workflow_id"], {}).setdefault(table, []).append(r)
        for row in record_cells.table_rows(conn, "workflows"):
            mine = by_workflow.get(row.cells["id"], {})
            doc, history = _doc_from_rows(row, mine.get("workflow_items", []),
                                          mine.get("workflow_open", []),
                                          mine.get("workflow_events", []))
            yield Doc(path_of(doc["id"]), doc, history=tuple(history))

    def rows(self, docs):
        from jarvis.knowledge_move import Row
        out: dict[str, list] = {t: [] for t in TABLES}
        logged: list[dict] = []
        for d in docs:
            if not _is_workflow(d.data):
                continue
            data, wid = d.data, d.data["id"]
            cells = {c: data.get(c) for c in WORKFLOW_COLUMNS}
            cells["agents"] = record_cells.text_of(data, "agents", list(data["agents"]))
            out["workflows"].append(Row(cells, data.get("rowid")))
            out["workflow_items"] += [
                Row({**{c: i.get(c) for c in ITEM_COLUMNS}, "workflow_id": wid}, i.get("rowid"))
                for i in data["items"]]
            out["workflow_open"] += [
                Row({**{c: o.get(c) for c in OPEN_COLUMNS}, "workflow_id": wid,
                     "detail": record_cells.text_of(o, "detail", o.get("detail") or {})},
                    o.get("rowid"))
                for o in data["open"]]
            logged += [{**e, "workflow_id": wid} for e in record_cells.history_events(d)]
        record_cells.resolve_seqs(logged)
        out["workflow_events"] = [
            Row({"seq": e["seq"], "workflow_id": e["workflow_id"], "kind": e["kind"],
                 "item": e.get("item"), "summary": e["summary"],
                 "detail": record_cells.text_of(e, "detail", e.get("detail") or {}),
                 "at": e["at"], "reporter": e.get("reporter") or ""})
            for e in logged]
        return out


MAPPINGS = [_WorkflowMapping()]
