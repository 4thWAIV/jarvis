"""The Work tracker's routes and the calls the brain's tools share with them.

  GET  /api/work                          the open items, the groups the Work tab lists them
                                          in, and History (closed, or done a while)
  GET  /api/work/{id}                     one item, with its latest events and the open
                                          packages sent for it, with their questions
  GET  /api/work/{id}/events              its log (?after=<seq>&kinds=a,b)
  POST /api/work                          begin tracking one (a reporter)
  POST /api/work/{id}/events              one report: update, piece, decision_needed,
                                          decision_withdrawn (its asker only), closed
  POST /api/work/{id}/decisions/{key}     his answer, from the page
  POST /api/work/{id}/review              his review: approve, not_done or add_work
  GET  /api/work/answers                  his answers to the calling access key's
                                          decisions (?since=<epoch seconds>)

Every answer of his is also written for the agent that asked it, as one
Markdown file in `data_paths.answers_dir()/<project>/<item>-<key>.md`
(`write_answer`), so it is told without asking again.

Reporters reach the two POSTs (and the answers read) with an access key in the `work` category
(access_keys.py) through `work_report.py`; his answer needs the page's own
origin or the tool token, as every other change does (web_auth.py). Every
committed change is told to `recorded`, which the server turns into the
`work` hint on /ws/sessions and, for news, an attention item.

Who is on a piece is shown by the one name of its record: an agent's from
the agent directory, a run's project, a workflow's name. A piece's who must
name a record JARVIS has when it is reported.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import re
import time
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jarvis import (agent_directory, data_paths, package_inbox, package_inbox_api, run_store,
                    web_auth, workflow_store, work_store)

log = logging.getLogger(__name__)

# (the item as it is now, the kind of change, what the report said)
Recorded = Callable[[work_store.WorkItem, str, Optional[dict]], None]


# The newest reports one item's detail shows.
EVENTS_SHOWN = 50


class StartRequest(BaseModel):
    title: str
    source: dict
    state: str = work_store.PLANNED
    area: str = ""


class EventRequest(BaseModel):
    kind: str
    summary: str
    detail: dict = {}


class AnswerRequest(BaseModel):
    answer: str


class ReviewRequest(BaseModel):
    action: str
    words: str = ""


# ── who, by name ────────────────────────────────────────────────────────

class WhoNames:
    """The one name of the record each piece names, for one read: the agent
    directory is built at most once however many pieces name agents."""

    def __init__(self) -> None:
        self._agents: Optional[dict[str, str]] = None

    def __call__(self, person: work_store.Who) -> Optional[str]:
        """The name, or None when JARVIS has no such record (or for Tim,
        whom each side words itself)."""
        if person.kind == work_store.AGENT:
            if self._agents is None:
                self._agents = agent_directory.names_by_session()
            return self._agents.get(person.ref)
        if person.kind == work_store.RUN:
            run = run_store.get_run(person.ref)
            return run["project_name"] if run else None
        if person.kind == work_store.WORKFLOW:
            flow = workflow_store.snapshot(person.ref)
            return flow.name if flow else None
        return None


def _check_who(detail: dict) -> None:
    """A reported piece's who must be a record JARVIS has."""
    on = detail.get("who")
    if not isinstance(on, dict):
        return
    person = work_store.who(on.get("kind"), on.get("ref"))
    if person.kind != work_store.TIM and WhoNames()(person) is None:
        raise work_store.WorkRefused(f"JARVIS has no {person.kind} {person.ref}.")


def asker(scope: dict) -> str:
    """Who is reporting, as a decision's asker is kept: an access key by its
    label, else how the request was admitted (the tool token, or a page)."""
    label = web_auth.access_key_label(scope)
    return f"key:{label}" if label is not None else str(scope.get(web_auth.ADMITTED) or "")


def item_json(item: work_store.WorkItem, names: Optional[WhoNames] = None,
              package_questions: Optional[dict[str, int]] = None) -> dict:
    """An item as the page shows it, with `package_questions`: how many
    questions the packages sent for it still wait on him for (package_inbox
    rows' counts, `package_inbox.waiting_by_work`, read once per listing)."""
    names = names or WhoNames()
    out = dataclasses.asdict(item)
    for piece, raw in zip(item.pieces, out["pieces"]):
        raw["who"]["name"] = names(piece.who)
    waiting = package_inbox.waiting_by_work() if package_questions is None else package_questions
    out["package_questions"] = waiting.get(item.id, 0)
    return out


def packages_json(item_id: str) -> list[dict]:
    """The open packages sent for this item (`make_package --work`), each
    with its questions and what is recorded, oldest first."""
    return [package_inbox_api.detail(e) for e in package_inbox.for_work(item_id)]


def group_json(group: work_store.WorkGroup) -> dict:
    """A group as the Work tab heads it: an area by its words, a folder by
    its last name (its whole path beside it), and its items' ids in order."""
    label = group.name
    if group.grouped_by == work_store.BY_FOLDER:
        label = os.path.basename(group.name.rstrip("/")) or group.name
    return {"grouped_by": group.grouped_by, "name": group.name, "label": label,
            "ids": [i.id for i in group.items]}


def event_json(event: work_store.WorkEvent) -> dict:
    return dataclasses.asdict(event)


# ── the shared calls ────────────────────────────────────────────────────

def answer_decision(item_id: str, key: str, answer: str, where: str) -> work_store.WorkItem:
    """Record his answer to an open decision, as the item's own
    decision_made, so its reporter reads it (`work_report.py answer`).
    The page's route and the brain's tool both call this."""
    words = " ".join(str(answer or "").split())
    item = work_store.record(work_store.WorkEvent(
        item_id, work_store.DECISION_MADE, f"He answered {key}: {words}",
        {"key": key, "answer": words, "where": where}))
    write_answer(item, key)
    return item


# A project folder's name as an answers folder may be named.
_PROJECT = re.compile(r"[^/\x00]{1,255}")


def answer_path(item: work_store.WorkItem, name: str):
    """Where his word `name` (a decision's key, or a review's) is written
    for the item's agents, or None when the item's project has no name a
    folder can take."""
    project = work_store.project_of(item)
    if not _PROJECT.fullmatch(project) or project in (".", ".."):
        return None
    return data_paths.answers_dir() / project / f"{item.id}-{name}.md"


def _write_for_asker(item: work_store.WorkItem, name: str, head: str, lines: list[str],
                     at: float, where: str) -> None:
    """One file for the item's agents, replaced whole through a rename.
    Never raises: the record already has it."""
    path = answer_path(item, name)
    if path is None:
        return
    when = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(at))
    text = (f"# {head}\n\n- Item: {item.id} ({item.title})\n"
            + "".join(f"- {line}\n" for line in lines) + f"- When: {when} ({where})\n")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f".{path.name}.part")
        part.write_text(text, encoding="utf-8")
        os.replace(part, path)
    except OSError:
        log.warning("work: %s/%s was not written for its asker", item.id, name, exc_info=True)


def write_answer(item: work_store.WorkItem, key: str) -> None:
    """His answer to `key`, written for the agent that asked it, which
    `work_report.py answer` also reads from the record."""
    done = next((a for a in item.answered if a.key == key and a.where in ("page", "voice")), None)
    if done is not None:
        _write_for_asker(item, key, f"Tim answered {key} on {item.id}",
                         [f"Decision: {key}", f"Question: {done.question}",
                          f"His answer: {done.answer}"], done.answered_at, done.where)


# His review, as the page, his voice and the record name it.
APPROVE, NOT_DONE, ADD_WORK = "approve", "not_done", "add_work"
REVIEW_ACTIONS = {APPROVE: work_store.APPROVED, NOT_DONE: work_store.REVIEW_RETURNED,
                  ADD_WORK: work_store.WORK_ADDED}


def review(item_id: str, action: str, words: str, where: str) -> work_store.WorkItem:
    """His review of an item, from the page and the brain's acting
    `review_work` alike: approve it (done, his alone to give), say it is not
    done yet (back to working, with his note), or add work (a new piece in
    his words). A note or new work is written for the item's agents in
    `answers_dir()/<project>/<item>-review-<when>.md`."""
    kind = REVIEW_ACTIONS.get(action)
    if kind is None:
        raise work_store.WorkRefused("A review is approve, not_done or add_work.")
    words = " ".join(str(words or "").split())
    summary = {APPROVE: "He approved it: done.", NOT_DONE: f"Not done yet, he says: {words}",
               ADD_WORK: f"He added work: {words}"}[action]
    item = work_store.record(work_store.WorkEvent(item_id, kind, summary,
                                                  {"words": words, "where": where}))
    head, lines = {
        APPROVE: (f"Tim approved {item.id}: it is done", []),
        NOT_DONE: (f"Tim says {item.id} is not done yet",
                   [f"His note: {words}", "It is back to working: address his note, then "
                                         "report it verifying again."]),
        ADD_WORK: (f"Tim added work to {item.id}",
                   [f"His words: {words}", f"It is the new piece {_added_key(item_id)}: report "
                                           f"it as you work on it."]),
    }[action]
    _write_for_asker(item, f"review-{int(time.time())}", head, lines, time.time(), where)
    return item


def _added_key(item_id: str) -> str:
    """The key of the piece he added last."""
    for e in reversed(work_store.latest(item_id, 10)):
        if e.kind == work_store.WORK_ADDED:
            return e.detail.get("key", "")
    return ""


def answers_json(asked_by: str, since: float) -> list[dict]:
    """His answers to the decisions `asked_by` asked, since `since`."""
    return [dataclasses.asdict(a) for a in work_store.answers_for(asked_by, since)]


def signed_off_without_answer(item_id: str, key: str) -> work_store.WorkItem:
    """He signed off the package this decision names without answering its
    matching question: the decision is closed, and on record as closed that
    way, never as an answer of his (`work_report.py answer` says so)."""
    return work_store.record(work_store.WorkEvent(
        item_id, work_store.DECISION_SIGNED_OFF,
        f"Closed {key}: he signed off its package without an answer", {"key": key}))


def mount(app: FastAPI, recorded: Recorded) -> None:
    def refused(e: Exception) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=409)

    @app.get("/api/work")
    async def list_work():
        def read():
            names, waiting = WhoNames(), package_inbox.waiting_by_work()
            items = work_store.listing()
            return {"items": [item_json(i, names, waiting) for i in items],
                    "groups": [group_json(g) for g in work_store.grouped(items)],
                    "history": [item_json(i, names, waiting)
                                for i in work_store.listing(history=True)]}
        return await asyncio.to_thread(read)

    @app.get("/api/work/answers")
    async def given_answers(request: Request, since: float = 0.0):
        """His answers to the decisions the calling access key asked, oldest
        first: what `work_report.py answers --since` lists."""
        web_auth.scope_authorized(request.scope)
        label = web_auth.access_key_label(request.scope)
        if label is None:
            return JSONResponse({"error": "Answers are listed for the access key that asked "
                                          "the decisions."}, status_code=403)
        found = await asyncio.to_thread(answers_json, f"key:{label}", since)
        return {"answers": found}

    @app.get("/api/work/{item_id}")
    async def one_item(item_id: str):
        def read():
            item = work_store.get(item_id)
            if item is None:
                return None
            return {"item": item_json(item), "packages": packages_json(item_id),
                    "events": [event_json(e) for e in work_store.latest(item_id, EVENTS_SHOWN)]}
        found = await asyncio.to_thread(read)
        if found is None:
            raise HTTPException(status_code=404, detail="No such work item")
        return found

    @app.get("/api/work/{item_id}/events")
    async def item_events(item_id: str, after: int = 0, kinds: str = ""):
        wanted = {k for k in kinds.split(",") if k} or None
        found = await asyncio.to_thread(work_store.events, item_id, after, wanted)
        return {"events": [event_json(e) for e in found]}

    @app.post("/api/work")
    async def start_work(req: StartRequest):
        try:
            item = await asyncio.to_thread(work_store.create, req.title, req.source.get("kind"),
                                           req.source.get("path"), req.state, req.area)
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, work_store.CREATED, None)
        return {"id": item.id, "item": item_json(item)}

    @app.post("/api/work/{item_id}/events")
    async def report(item_id: str, req: EventRequest, request: Request):
        """One report from the agent doing the work. Answering a decision
        is his alone (the page, his voice), never a reporter's; withdrawing
        one is its asker's alone, never his."""
        if req.kind in work_store.HIS_KINDS:
            return refused(ValueError("Only he answers a decision or reviews an item: done is "
                                      "his to give. Report it verifying, with what to check."))
        by = asker(request.scope)
        if req.kind == work_store.DECISION_WITHDRAWN and by == web_auth.BY_ORIGIN:
            return refused(ValueError("Only the agent that asked a decision withdraws it; "
                                      "he answers it."))
        # Who asked or withdraws is the route's to say, never the report's.
        detail = {k: v for k, v in req.detail.items() if k not in ("asked_by", "by")}
        if req.kind == work_store.DECISION_NEEDED:
            detail["asked_by"] = by
        elif req.kind == work_store.DECISION_WITHDRAWN:
            detail["by"] = by

        def write():
            if req.kind == work_store.PIECE:
                _check_who(detail)
            return work_store.record(work_store.WorkEvent(item_id, req.kind, req.summary, detail))
        try:
            item = await asyncio.to_thread(write)
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, req.kind, detail)
        return {"item": item_json(item)}

    @app.post("/api/work/{item_id}/decisions/{key}")
    async def answer(item_id: str, key: str, req: AnswerRequest):
        """He answered an item's decision on the page."""
        try:
            item = await asyncio.to_thread(answer_decision, item_id, key, req.answer, "page")
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, work_store.DECISION_MADE, None)
        return {"item": item_json(item)}

    @app.post("/api/work/{item_id}/review")
    async def review_item(item_id: str, req: ReviewRequest):
        """His review of an item on the page: Approve, Not done yet, Add work."""
        try:
            item = await asyncio.to_thread(review, item_id, req.action, req.words, "page")
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, REVIEW_ACTIONS[req.action], None)
        return {"item": item_json(item)}
