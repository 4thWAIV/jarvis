"""The Work tracker's routes and the calls the brain's tools share with them.

  GET  /api/work                          every item, as the Work tab lists them
  GET  /api/work/{id}                     one item, with its latest events
  GET  /api/work/{id}/events              its log (?after=<seq>&kinds=a,b)
  POST /api/work                          begin tracking one (a reporter)
  POST /api/work/{id}/events              one report: update, piece, decision_needed
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
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from jarvis import agent_directory, run_store, workflow_store, work_store

# (the item as it is now, the kind of change, what the report said)
Recorded = Callable[[work_store.WorkItem, str, Optional[dict]], None]


class StartRequest(BaseModel):
    title: str
    source: dict
    state: str = work_store.PLANNED


class EventRequest(BaseModel):
    kind: str
    summary: str
    detail: dict = {}


class AnswerRequest(BaseModel):
    answer: str


# ── who, by name ────────────────────────────────────────────────────────

def who_name(person: work_store.Who) -> Optional[str]:
    """The one name of the record a piece names, or None when JARVIS has no
    such record (or for Tim, whom each side words itself)."""
    if person.kind == work_store.AGENT:
        found = next((a for a in agent_directory.agents() if a.session_id == person.ref), None)
        return found.name if found else None
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
    if person.kind != work_store.TIM and who_name(person) is None:
        raise work_store.WorkRefused(f"JARVIS has no {person.kind} {person.ref}.")


def item_json(item: work_store.WorkItem) -> dict:
    out = dataclasses.asdict(item)
    for piece, raw in zip(item.pieces, out["pieces"]):
        raw["who"]["name"] = who_name(piece.who)
    return out


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
        items = await asyncio.to_thread(lambda: [item_json(i) for i in work_store.listing()])
        return {"items": items}

    @app.get("/api/work/{item_id}")
    async def one_item(item_id: str):
        def read():
            item = work_store.get(item_id)
            if item is None:
                return None
            return {"item": item_json(item),
                    "events": [event_json(e) for e in work_store.events(item_id)[-50:]]}
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
                                           req.source.get("path"), req.state)
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, work_store.CREATED, None)
        return {"id": item.id, "item": item_json(item)}

    @app.post("/api/work/{item_id}/events")
    async def report(item_id: str, req: EventRequest):
        """One report from the agent doing the work. Answering a decision
        is his alone (the page, his voice), never a reporter's."""
        if req.kind == work_store.DECISION_MADE:
            return refused(ValueError("Only he answers a decision."))

        def write():
            if req.kind == work_store.PIECE:
                _check_who(req.detail)
            return work_store.record(work_store.WorkEvent(item_id, req.kind, req.summary,
                                                          req.detail))
        try:
            item = await asyncio.to_thread(write)
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, req.kind, req.detail)
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
