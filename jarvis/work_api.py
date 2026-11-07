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

Reporters reach the two POSTs with an access key in the `work` category
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
import os
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jarvis import (agent_directory, package_inbox, package_inbox_api, run_store, web_auth,
                    workflow_store, work_store)

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
    return work_store.record(work_store.WorkEvent(
        item_id, work_store.DECISION_MADE, f"He answered {key}: {words}",
        {"key": key, "answer": words, "where": where}))


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
        if req.kind == work_store.DECISION_MADE:
            return refused(ValueError("Only he answers a decision."))
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
