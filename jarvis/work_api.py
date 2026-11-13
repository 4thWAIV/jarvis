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

Reporters reach the two POSTs (and the answers read) signed with their own
onboarded key holding the `work` right (agent_rights.py), through
`work_report.py`; his answer needs the page's own
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
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from jarvis import report_keys, reporters
from jarvis.subsystem_diagnostics import READ, WRITE, work_routes

# Every report's write and every read of the list is timed, live, for the
# Diagnostics tab's Work tracker row (subsystem_diagnostics.work).
timed = work_routes.timed
from jarvis.message import ASK_WITH_A_PACKAGE
from jarvis import (agent_directory, data_paths, package_inbox, package_inbox_api, run_store,
                    web_auth, work_packages, workflow_store, work_store)

log = logging.getLogger(__name__)

# (the item as it is now, the kind of change, what the report said)
Recorded = Callable[[work_store.WorkItem, str, Optional[dict]], None]


# The newest reports one item's detail shows.
EVENTS_SHOWN = 50

# The projects holding a path (`ProjectService.projects_for_path`), set by
# the server once project records start; none until then.
projects_for_path: Callable[[str], list[dict]] = lambda path: []


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
    """The one name of the record each piece names, for as long as this
    instance is kept: the agent directory is built at most once however many
    pieces name agents, and each run's and workflow's name is read once.
    A live owner's name is its own, given by `live` (by default looked up
    now, work_store.live_agent)."""

    def __init__(self, live: Optional[Callable[[work_store.Who], object]] = None,
                 agents: Optional[Callable[[], dict[str, str]]] = None,
                 records: Optional[dict[tuple, Optional[str]]] = None) -> None:
        """`agents` gives the agent directory's names when first wanted
        (by default read then); `records` keeps each run's and workflow's
        name, and may be shared by a caller that keeps them longer."""
        self._agents: Optional[dict[str, str]] = None
        self._read_agents = agents or agent_directory.names_by_session
        self._records: dict[tuple, Optional[str]] = {} if records is None else records
        self._live = live or work_store.live_agent

    def __call__(self, person: work_store.Who) -> Optional[str]:
        """The name, or None when JARVIS has no such record (or for Tim,
        whom each side words itself)."""
        live = self._live(person)
        if live is not None:
            return live.name
        if work_store.subagent_of(person) is not None:
            return None               # a subagent JARVIS cannot find
        if person.kind == work_store.AGENT:
            if self._agents is None:
                self._agents = self._read_agents()
            return self._agents.get(person.ref)
        key = (person.kind, person.ref)
        if key not in self._records:
            self._records[key] = self._record_name(person)
        return self._records[key]

    @staticmethod
    def _record_name(person: work_store.Who) -> Optional[str]:
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
    """Who is reporting, as every report's reporter and a decision's asker
    are kept (reporters.recorded): an agent key's principal, an access key
    by its label, else how the request was admitted (the tool token, or a
    page)."""
    return reporters.recorded(scope)


def item_json(item: work_store.WorkItem, names: Optional[WhoNames] = None,
              package_questions: Optional[dict[str, int]] = None,
              projects: Optional[list[dict]] = None) -> dict:
    """An item as the page shows it, with `package_questions`: how many
    questions the packages sent for it still wait on him for (package_inbox
    rows' counts, `package_inbox.waiting_by_work`, read once per listing),
    and `projects`: the projects holding its source (`projects_for_path`,
    unless the caller already has them)."""
    names = names or WhoNames()
    out = dataclasses.asdict(item)
    for piece, raw in zip(item.pieces, out["pieces"]):
        raw["who"]["name"] = names(piece.who)
        raw["reporter_name"] = reporters.shown(piece.reporter)
    waiting = package_inbox.waiting_by_work() if package_questions is None else package_questions
    out["package_questions"] = waiting.get(item.id, 0)
    out["approvable"] = can_approve(item, waiting)
    out["projects"] = projects_for_path(item.source_path) if projects is None else projects
    if out["state"] == work_store.VERIFYING and not out["approvable"]:
        # Ready for review only when he can approve it: what keeps Approve
        # back here is a package's question, which waits on him.
        out["state"] = work_store.WAITING
    out["needs_you"] = needs_of(item, waiting, out["approvable"])
    out["stalled"] = is_stalled(item, out["needs_you"])
    return out


# ── what he is needed for, and work nobody moves ────────────────────────
#
# Every item that needs him names each act and where to do it, the first
# first: the page shows them at the top of the item and the first in its
# row, and the brain's work_status says the same words (`need_words`). An
# item that needs nothing of him and that nobody is moving is Stalled.

DECISION_NEED, PACKAGE_NEED, REVIEW_PACKAGE_NEED, REVIEW_NEED, PIECE_NEED = (
    "decision", "package", "review_package", "review", "piece")


def needs_of(item: work_store.WorkItem, waiting: Optional[dict[str, int]] = None,
             approvable: Optional[bool] = None) -> list[dict]:
    """Each act he is needed for on this item, in the order to do them:
    its open decisions (on the Messages tab), the packages sent for it that
    still ask him something (opened from the item), its review (in its
    header) and a piece waiting on him for something else (what it names)."""
    if item.closed_at is not None or item.state == work_store.DONE:
        return []
    needs = [{"kind": DECISION_NEED, "key": d.key, "text": d.question} for d in item.decisions]
    waiting = package_inbox.waiting_by_work() if waiting is None else waiting
    packaged = set()
    if waiting.get(item.id):
        for entry in package_inbox.for_work(item.id):
            try:
                asked = [q for q in package_inbox.questions(work_packages.find(entry.package_id))
                         if q.applies and not q.answer]
            except work_packages.PackageError:
                continue
            if asked:
                review = all(q.id == REVIEW_QUESTION for q in asked)
                needs.append({"kind": REVIEW_PACKAGE_NEED if review else PACKAGE_NEED,
                              "inbox": entry.id, "text": entry.title})
                packaged |= {entry.id, entry.package_id, entry.name}
    if (can_approve(item, waiting) if approvable is None else approvable) and \
            item.state == work_store.VERIFYING:
        needs.append({"kind": REVIEW_NEED})
    for p in item.pieces:
        if p.state != work_store.WAITING:
            continue
        others = [h for h in p.until if not h.startswith("decision:")
                  and not any(h == f"package:{ref}" or h.startswith(f"package:{ref}:")
                              for ref in packaged)]
        if others:
            needs.append({"kind": PIECE_NEED, "key": p.key, "text": p.title, "links": others})
    return needs


def need_words(need: dict) -> str:
    """A need in English, as the brain says it (the page words the same
    need in its language, main.work.need.*)."""
    kind = need["kind"]
    if kind == DECISION_NEED:
        return f'Answer "{need["text"]}" on the Messages tab'
    if kind == PACKAGE_NEED:
        return f"Open the {need['text']} package and answer its questions"
    if kind == REVIEW_PACKAGE_NEED:
        return f"Open the {need['text']} review package"
    if kind == REVIEW_NEED:
        return "Approve, Not done yet or Add work in this item's header"
    return f"Answer what {need['text']} waits for ({', '.join(need['links'])})"


def is_stalled(item: work_store.WorkItem, needs: list[dict]) -> bool:
    """Needs nothing of him and nobody is moving it: no piece's claim heard
    from and no report within the quiet window. Never done, closed or ready
    for his review."""
    return (not needs and item.unmoved and item.closed_at is None and not item.history
            and item.state not in (work_store.DONE, work_store.VERIFYING, work_store.BLOCKED))


# ── his review in a package ─────────────────────────────────────────────
#
# A review package (`make_package --work <item>`) asks one well-known
# question, `approve-item`, whose options are his review: his answer to it,
# by decide.py or the item's Decisions tab, IS his review of the item it was
# sent for, through the same `review` as the page and `review_work`. It is
# the approval, so it never stands in the way of Approve; any other question
# a package sent for the item still asks does, here and on the page alike.

REVIEW_QUESTION = "approve-item"


def other_package_questions(item_id: str) -> int:
    """How many questions the open packages sent for this item still ask
    him, its review question excepted."""
    count = 0
    for entry in package_inbox.for_work(item_id):
        try:
            package = work_packages.find(entry.package_id)
        except work_packages.PackageError:
            continue
        count += sum(1 for q in package_inbox.questions(package)
                     if q.applies and not q.answer and q.id != REVIEW_QUESTION)
    return count


def can_approve(item: work_store.WorkItem, waiting: Optional[dict[str, int]] = None) -> bool:
    """Whether he can approve the item now: its record allows it
    (`work_store.approvable`) and no package sent for it asks anything but
    its review question. `waiting` (package_inbox.waiting_by_work) spares
    reading a package when none waits."""
    if not item.approvable:
        return False
    if waiting is not None and not waiting.get(item.id):
        return True
    return other_package_questions(item.id) == 0


# Where a review given in a package is recorded as given.
IN_A_PACKAGE = "package"


def _review_asked(package):
    """A review package's `approve-item`, or None when it asks none."""
    return next((q for q in package_inbox.questions(package)
                 if q.id == REVIEW_QUESTION and q.applies), None)


def review_from_package(entry, package) -> Optional[work_store.WorkItem]:
    """His answer to a review package's `approve-item`, as his review of
    the item it was sent for (`where` IN_A_PACKAGE), at once and once per
    answer. When it cannot be applied (Approve while a piece still works,
    say), that is recorded on the item with why, so his act always shows.
    None when there is no answer to give, or it is already on the record."""
    asked = _review_asked(package) if entry.work_id else None
    action = REVIEW_OPTIONS.get(asked.answer.get("answer")) if asked and asked.answer else None
    if action is None:
        return None
    source = {"package": entry.id, "answered_at": str(asked.answer.get("at") or "")}
    if any(work_store.reviewed(entry.work_id, {**source, "package": row.id})
           for row in package_inbox.rows_for_package(entry.package_id)):
        return None             # this answer is on record, through any row of the package
    if action == APPROVE and _approved_already(entry.work_id):
        return None             # he approved it done another way: the same act, on record
    try:
        return review(entry.work_id, action, asked.answer.get("note") or "", IN_A_PACKAGE,
                      source)
    except work_store.WorkRefused as e:
        return _note_review(entry.work_id, work_store.NOT_APPLIED, source,
                            f"His {asked.answer.get('answer')} in the package was not applied: {e}",
                            str(e))


def _approved_already(item_id: str) -> bool:
    """He has approved the item done (its header, by voice, another package)."""
    item = work_store.get(item_id)
    return item is not None and item.state == work_store.DONE and bool(item.approved_at)


def _note_review(item_id: str, outcome: str, source: dict, summary: str,
                 why: str = "") -> Optional[work_store.WorkItem]:
    """What his act in a review package came to when it changed nothing
    itself, on the item's record. None for a closed item."""
    try:
        return work_store.record(work_store.WorkEvent(
            item_id, work_store.REVIEW_NOTED, summary,
            {"outcome": outcome, "why": why, "source": source}))
    except work_store.WorkRefused:
        return None


def signed_off_review(entry, package) -> Optional[work_store.WorkItem]:
    """He signed off a review package: an answer on record is his review
    (`review_from_package`); with none (or Decide later, None of these), the
    item says the review closed without a verdict. Once per package."""
    if not entry.work_id or _review_asked(package) is None:
        return None
    done = review_from_package(entry, package)
    if done is not None:
        return done
    asked = _review_asked(package)
    if asked.answer and asked.answer.get("answer") in REVIEW_OPTIONS:
        return None            # already applied, or noted, when he gave it
    source = {"package": entry.id, "answered_at": ""}
    if (work_store.reviewed(entry.work_id, source)
            or work_store.reviewed_since(entry.work_id, entry.arrived_at)):
        # Noted already, or he has reviewed the item another way since the
        # package came: an unanswered sign-off then says nothing.
        return None
    return _note_review(entry.work_id, work_store.NO_VERDICT, source,
                        "Review closed without a verdict: he signed the package off unanswered.")


def apply_package_reviews() -> int:
    """Every review he has already given in a package sent for an item,
    open or signed off (never one he set aside or that was replaced), on
    the record once (at server start: answers given before the link went
    live, or while JARVIS was down). How many items it moved."""
    moved = 0
    for entry in package_inbox.listing() + package_inbox.listing(closed=True):
        set_aside = (entry.state == package_inbox.CLOSED
                     and entry.closed_why != package_inbox.SIGNED_OFF)
        if not entry.work_id or set_aside:
            continue
        try:
            package = work_packages.find(entry.package_id)
        except work_packages.PackageError:
            continue
        signed = entry.closed_why == package_inbox.SIGNED_OFF
        if (signed_off_review if signed else review_from_package)(entry, package) is not None:
            moved += 1
    return moved


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
    """An event as the page shows it, with who reported it by name
    (`reporter_name`: its key's agent, else the key's label; "" for his acts
    and JARVIS's own)."""
    return {**dataclasses.asdict(event), "reporter_name": reporters.shown(event.reporter)}


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
_PROJECT = re.compile(r"[^/\x00-\x1f\x7f-\x9f\u2028\u2029]{1,255}")


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
# A review package's options, as his review.
REVIEW_OPTIONS = {"approve": APPROVE, "not-done": NOT_DONE, "add-work": ADD_WORK}


def review(item_id: str, action: str, words: str, where: str,
           source: Optional[dict] = None) -> work_store.WorkItem:
    """His review of an item, from the page and the brain's acting
    `review_work` alike: approve it (done, his alone to give), say it is not
    done yet (back to working, with his note), or add work (a new piece in
    his words). A note or new work is written for the item's agents in
    `answers_dir()/<project>/<item>-review-<when>.md`."""
    kind = REVIEW_ACTIONS.get(action)
    if kind is None:
        raise work_store.WorkRefused("A review is approve, not_done or add_work.")
    words = " ".join(str(words or "").split())
    if action == NOT_DONE and not words:
        words = work_store.NO_NOTE      # his words are optional: it goes back all the same
    in_package = where == IN_A_PACKAGE
    if action == APPROVE and (waiting := other_package_questions(item_id)):
        raise work_store.WorkRefused(
            f"A package sent for it still asks him {waiting} question(s): answer them before "
            f"approving it.")
    # His note is kept as the item's review note and shown once, beside
    # this summary, never twice.
    summary = {APPROVE: "He approved it: done.", NOT_DONE: "Sent back: not done yet.",
               ADD_WORK: f"He added work: {words}"}[action]
    if in_package:
        summary = {APPROVE: "Approved by him in the package: done.",
                   NOT_DONE: "Sent back in the package: not done yet.",
                   ADD_WORK: f"Work added by him in the package: {words}"}[action]
    detail = {"words": words, "where": where}
    if source:
        detail["source"] = source
    item = work_store.record(work_store.WorkEvent(item_id, kind, summary, detail))
    head, lines = {
        APPROVE: (f"Tim approved {item.id}: it is done", []),
        NOT_DONE: (f"Tim says {item.id} is not done yet",
                   [f"His note: {words}", "It is back to working: address his note, then "
                                         "report it verifying again."]),
        ADD_WORK: (f"Tim added work to {item.id}",
                   [f"His words: {words}", f"It is the new piece {_added_key(item_id)}: report "
                                           f"it as you work on it."]),
    }[action]
    # One file per review, named by the item's revision after it (one per
    # event): named by the second, two reviews in one second (Approve, then
    # Not done yet) wrote one file and the first was lost.
    _write_for_asker(item, f"review-{item.revision}", head, lines, time.time(), where)
    return item


def _added_key(item_id: str) -> str:
    """The key of the piece he added last."""
    for e in reversed(work_store.latest(item_id, 10)):
        if e.kind == work_store.WORK_ADDED:
            return e.detail.get("key", "")
    return ""


def work_keys_json(item_id: str) -> list[dict]:
    """The item's live work-item keys: each one's fingerprint, the agent
    that issued it (by its one name) and when it ends."""
    from jarvis import agent_key_store
    return [{"fingerprint": k.fingerprint, "issued_by": reporters.shown(k.issued_by),
             "expires_at": k.expires_at, "issued_at": k.submitted_at}
            for k in agent_key_store.work_keys(item_id)]


def answers_json(asked_by, since: float) -> list[dict]:
    """His answers to the decisions `asked_by` (one reporter as recorded,
    or every name it has gone by) asked, since `since`."""
    names = (asked_by,) if isinstance(asked_by, str) else tuple(dict.fromkeys(asked_by))
    found = [a for name in names for a in work_store.answers_for(name, since)]
    return [dataclasses.asdict(a) for a in sorted(found, key=lambda a: a.answered_at)]


def signed_off_without_answer(item_id: str, key: str) -> work_store.WorkItem:
    """He signed off the package this decision names without answering its
    matching question: the decision is closed, and on record as closed that
    way, never as an answer of his (`work_report.py answer` says so)."""
    return work_store.record(work_store.WorkEvent(
        item_id, work_store.DECISION_SIGNED_OFF,
        f"Closed {key}: he signed off its package without an answer", {"key": key}))


def _askers_on(item_id: str) -> list[str]:
    """Everyone who asked a decision on the item."""
    item = work_store.get(item_id)
    if item is None:
        return []
    return list(dict.fromkeys(e.detail.get("asked_by", "") for e in work_store.events(
        item_id, 0, {work_store.DECISION_NEEDED}) if e.detail.get("asked_by")))


def mount(app: FastAPI, recorded: Recorded, view) -> None:
    """The routes, over `view` (work_view.WorkView): the list and each
    item's JSON are the held view's, worked out when they changed."""
    def refused(e: Exception) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=409)

    def shown(item: work_store.WorkItem) -> dict:
        """The item a write left, as the view holds it once it has caught
        up, else worked out from the write's own record: the answer never
        waits for the view (work_view.WorkView.shown)."""
        return view.shown(item)

    @app.get("/api/work")
    @timed(READ)
    async def list_work():
        """The held view (work_view.py): the open items, their groups,
        History and the view's revision. Reads nothing."""
        return Response(content=await asyncio.to_thread(view.body),
                        media_type="application/json")

    @app.get("/api/work/answers")
    async def given_answers(request: Request, since: float = 0.0):
        """His answers to the decisions the calling access key asked, oldest
        first: what `work_report.py answers --since` lists."""
        web_auth.scope_authorized(request.scope)
        who = reporters.of_scope(request.scope)
        if who is None:
            return JSONResponse({"error": "Answers are listed for the agent's key that asked "
                                          "the decisions."}, status_code=403)
        if who.kept.startswith(reporters.WORK_KEY_PREFIX):
            # A work-item key reads the answers to its one item's decisions.
            from jarvis import agent_key_store
            key = await asyncio.to_thread(agent_key_store.get,
                                          who.kept[len(reporters.WORK_KEY_PREFIX):])
            found = await asyncio.to_thread(lambda: [
                a for a in answers_json(_askers_on(key.work_id), since)
                if a["item_id"] == key.work_id]) if key else []
            return {"answers": found}
        found = await asyncio.to_thread(answers_json, (who.kept, *who.aliases), since)
        return {"answers": found}

    @app.get("/api/work/{item_id}")
    async def one_item(item_id: str):
        """One item as the view holds it (with the view's revision then),
        its open packages and its latest events. An item past the list's
        reach is worked out here."""
        def read():
            held, revision = view.held(item_id)
            if held is None:
                item = work_store.get(item_id)
                if item is None:
                    return None
                held = item_json(item)
            return {"item": {**held, "work_keys": work_keys_json(item_id)},
                    "packages": packages_json(item_id), "revision": revision,
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
    @timed(WRITE)
    @report_keys.once
    async def start_work(req: StartRequest, request: Request):
        try:
            item = await asyncio.to_thread(work_store.create, req.title, req.source.get("kind"),
                                           req.source.get("path"), req.state, req.area,
                                           asker(request.scope))
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, work_store.CREATED, None)
        return {"id": item.id, "item": shown(item)}

    @app.post("/api/work/{item_id}/events")
    @timed(WRITE)
    @report_keys.once
    async def report(item_id: str, req: EventRequest, request: Request):
        """One report from the agent doing the work. Answering a decision
        is his alone (the page, his voice), never a reporter's; withdrawing
        one is its asker's alone, never his."""
        if req.kind in work_store.JARVIS_KINDS:
            return refused(ValueError("Only JARVIS raises or clears an item's Error."))
        if req.kind in work_store.HIS_KINDS:
            return refused(ValueError("Only he answers a decision or reviews an item: done is "
                                      "his to give. Report it verifying, with what to check."))
        if req.kind in (work_store.DECISION_NEEDED, work_store.DECISION_WITHDRAWN):
            # A decision reaches him only as a package with its background,
            # carried by a message (one channel round 3).
            return refused(ValueError(ASK_WITH_A_PACKAGE))
        by = asker(request.scope)
        detail = {k: v for k, v in req.detail.items() if k not in ("asked_by", "by", "by_any")}

        def write():
            if req.kind == work_store.PIECE:
                _check_who(detail)
            return work_store.record(work_store.WorkEvent(item_id, req.kind, req.summary, detail,
                                                          reporter=by))
        try:
            item = await asyncio.to_thread(write)
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, req.kind, detail)
        return {"item": shown(item)}

    @app.post("/api/work/{item_id}/decisions/{key}")
    async def answer(item_id: str, key: str, req: AnswerRequest):
        """He answered an item's decision on the page."""
        try:
            item = await asyncio.to_thread(answer_decision, item_id, key, req.answer, "page")
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, work_store.DECISION_MADE, None)
        return {"item": shown(item)}

    @app.post("/api/work/{item_id}/review")
    async def review_item(item_id: str, req: ReviewRequest):
        """His review of an item on the page: Approve, Not done yet, Add work."""
        try:
            item = await asyncio.to_thread(review, item_id, req.action, req.words, "page")
        except work_store.WorkRefused as e:
            return refused(e)
        recorded(item, REVIEW_ACTIONS[req.action], None)
        return {"item": shown(item)}
