"""A package's runs: recorded, run by JARVIS itself, and put in Error when
they cannot go on (workflow.yaml, steps `record_and_start` and `deliver`
error_state).

The sending agent only asks (package_request.py). JARVIS takes its signed
request (server._accept_package_request): who asked is the signing key's
agent, `agent:<session id>`, recorded as the request is taken. A package
has one id and each attempt at it is a run under it; one job is one run,
and its record lives with its run:

    /work/<work id>/packages/<package id>/runs/<run id>/run.job
    /packages/<package id>/runs/<run id>/run.job      (for no Work item)

with the run's files on disk at the matching folder (`folder_of`:
data/work/<work id>/packages/... or data/package/<package id>/runs/...).
Each change of the job is one version of its record.

The run is produced in JARVIS's own process (`python -m jarvis.package_jobs
run <job>`, which the server starts, SPAWN), step by step (STEPS: gather,
mark, research, swarm, deck, deliver), each step the program PRODUCERS
names for it:

    queued -> running (step) -> delivered
                 |    ^
                 v    |  (the sender answered)
          waiting_on_sender ---> failed   (it answered a failed swarm's
                 |                         findings: a new run follows)
                 v
               error   (any step that cannot go on)

A step that asks the sender questions (`ask`) leaves the job waiting on it;
the sender answers with `answer` (package_answer, make_package --answer).
The finished package goes back to the sender before delivery
(`ask_check`; workflow.yaml deliver sender_check), with the pre-written
instructions (SENDER_CHECK): it answers `check` APPROVE, and JARVIS
delivers it (server._deliver_package_run), or CHANGE with `what_is_wrong`,
which ends the run `failed` and starts a new run revising it with its
words (check_answer; `new_run`'s `revision`). Every round asked of the
sender (questions, findings, its check) is told to it once in a message to
its session (`to_tell_sender`). Tim's Reject of a delivered package starts
a new run too, a revision with his words (`delivered_as`, `rejected`;
server._rejected_starts_new_run).
At most MAX_ROUNDS rounds of questions are asked. A swarm that fails
(`failed_swarm`) sends the sender its findings, through the same waiting:
the server tells it once (`findings_to_tell`) and puts them on the Work
item; its answer ends the run `failed` and a new run of the package
follows with the answers (server._answer_package_job, `new_run`).

Every run of a package goes through one mechanism, the step cache
(step_cache.py, construction-drafts.md "Step cache keyed by inputs", Tim
2026-11-22): each step, and each unit within it, is keyed by its inputs and
reused when they are kept, so only what changed is done again. The cache is
the package's own (Tim, 2026-11-22): only its runs reuse what it kept, and
a new package, whatever its handoff, starts fresh. A new run
carries the inputs of the run before (`inputs`: the revisions asked, each
change at the check or Reject with its words, and the sender's answers to
each stage's findings, with the stage they are for) and adds its own; a run
asked again after an Error adds none, so it reuses everything up to the
step that stopped. A sender
that does not answer within
SENDER_WAIT_SEC, a step that cannot go on (Blocked), a step that fails
unexpectedly, and a run whose process ended without finishing it, each put
the job in ERROR with the step, the reason in plain words, what was tried
and what would fix it (`error`). The server then tells everyone
(server._package_job_errored): the Work item goes to Error, else Tim gets a
message; JARVIS has an update; the asking agent gets a message from JARVIS
in its session saying it all, and reads the same on its job (`status`).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import logging
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

from jarvis import data_paths, env_names, records

log = logging.getLogger("jarvis.package_jobs")

TYPE = "job"
RECORD_NAME = f"run.{TYPE}"
WORK_ROOT = "/work"
PACKAGES_ROOT = "/packages"
QUEUED, RUNNING, WAITING = "queued", "running", "waiting_on_sender"
DELIVERED, ERROR = "delivered", "error"
# The swarm failed and the sender answered its findings: a new run follows.
FAILED = "failed"
STATES = (QUEUED, RUNNING, WAITING, DELIVERED, ERROR, FAILED)
GOING = (QUEUED, RUNNING)
ENDED = (DELIVERED, ERROR, FAILED)
# What a round asked the sender: questions the swarm voted it needs, a
# failed swarm's findings (workflow.yaml swarm ask_sender, when_it_fails),
# or its check of the finished package (deliver sender_check).
QUESTIONS, FINDINGS, CHECK = "questions", "findings", "check"
# The sender's check: its one question's id, the instructions word for word
# (Tim 2026-11-20, approved as recommended), and its structured answer
# (approved by Tim 2026-11-20): `check` is APPROVE or CHANGE, and CHANGE
# needs `what_is_wrong`, its words; no free text is parsed.
CHECK_ID = "check"
SENDER_CHECK = ("This is the package JARVIS made from your request, before the current user sees it. Open it "
                "and judge broadly whether it does what you asked for and what the user needs from "
                "it. Approve it, or say what is wrong, and a new run starts with your words.")
APPROVE, CHANGE = "approve", "change"
WHAT_IS_WRONG = "what_is_wrong"
CHECK_ANSWERS = (APPROVE, CHANGE)
# The steps a run is produced by, in order (workflow.yaml record_and_start
# produce).
STEPS = ("gather", "mark", "research", "swarm", "deck", "deliver")
# Each step's program, "module:function", called with the job's record;
# it returns WAITING when it asked the sender and waits on the answers,
# raises Blocked when it cannot go on. A step with none is not built yet.
# The deliver step signs with JARVIS's own key (`sign`).
PRODUCERS: dict[str, str] = {"gather": "jarvis.package_gather:gather",
                             "mark": "jarvis.package_mark:mark",
                             "research": "jarvis.package_swarm:research",
                             "swarm": "jarvis.package_swarm:swarm",
                             "deck": "jarvis.package_deck:deck",
                             "deliver": "jarvis.package_deliver:deliver"}
# At most five rounds of questions to the sender (Tim, 2026-11-19).
MAX_ROUNDS = 5
# A sender's answer is waited for 2 hours before Error (Tim, 2026-11-19).
SENDER_WAIT_SEC = 2 * 3600.0
LOG = "job.log"
JARVIS = "jarvis"


T = TypeVar("T")


class JobError(ValueError):
    """A job call refused, in words the calling agent reads."""


class Blocked(Exception):
    """A step that cannot go on: which, why in plain words, what was tried
    and what would fix it."""

    def __init__(self, step: str, reason: str, tried: str, fix: str):
        super().__init__(reason)
        self.step, self.reason, self.tried, self.fix = step, reason, tried, fix


def asked(step: str, role: str, tried: str, ask: Awaitable[T]) -> T:
    """A role session's answer to one call of a step. A model sweep that
    finds no model, or a session that cannot answer, puts the run in Error
    (Blocked), saying which role and what was tried."""
    return in_step(answered(step, role, tried, ask))


def in_step(work: Awaitable[T]) -> T:
    """A step's role calls run, on one event loop, each role's Codex
    sessions sharing one app server for them all, closed when they end
    (codex_provider.role_servers)."""
    from jarvis import codex_provider

    async def go() -> T:
        async with codex_provider.role_servers():
            return await work
    return asyncio.run(go())


async def answered(step: str, role: str, tried: str, ask: Awaitable[T]) -> T:
    """`ask`'s answer; no model found, or a session that cannot answer, is
    the run's Error (Blocked), saying which role and what was tried."""
    from jarvis import model_tiers, sessions
    try:
        return await ask
    except model_tiers.NoModel as e:
        raise Blocked(step, f"No model could be found for {role}: {e}", tried,
                      f"Make a model of the {role} role's tier available (Settings, Brains), "
                      "then a new run.") from e
    except sessions.SessionFailed as e:
        raise Blocked(step, f"{role[:1].upper()}{role[1:]} could not answer: {e}", tried,
                      "Read the run's log for the cause; once it is fixed, a new run.") from e


# -- where a run is ----------------------------------------------------------------------

def _package_folder(work: Optional[str], package: str) -> str:
    from jarvis import work_store
    seg = records.segment(package)
    return (f"{work_store.folder_of(work)}/packages/{seg}" if work
            else f"{PACKAGES_ROOT}/{seg}")


def run_folder(work: Optional[str], package: str, run: str) -> str:
    """The run's folder in the knowledge engine."""
    return f"{_package_folder(work, package)}/runs/{records.segment(run)}"


def path_of(doc: dict) -> str:
    """The job's record: `run.job` in its run's folder."""
    return f"{run_folder(doc.get('work'), doc['package'], doc['run'])}/{RECORD_NAME}"


def folder_of(doc: dict) -> Path:
    """The run's folder on disk, which the content root maps to its run
    folder in the engine (data/work -> /work, data/package -> /packages)."""
    if doc.get("work"):
        base = data_paths.work_dir() / records.segment(doc["work"]) / "packages"
    else:
        base = data_paths.package_dir()
    folder = base / records.segment(doc["package"]) / "runs" / records.segment(doc["run"])
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _jobs(where: list) -> list[dict]:
    """Every job (a run's record) matching `where`, under both roots."""
    found = []
    for root in (WORK_ROOT, PACKAGES_ROOT):
        for rec in records.scan(root, types=[TYPE], where=where):
            if rec.data.get("run") and rec.data.get("package"):
                found.append(rec.data)
    return found


def get(job_id: str) -> dict:
    if not isinstance(job_id, str) or not job_id:
        raise JobError("Give the job's id.")
    found = _jobs([["id", "=", job_id]])
    if not found:
        raise JobError(f"There is no package job {job_id}.")
    return found[0]


def runs_of(package: str) -> list[dict]:
    """Every run's job of a package."""
    return _jobs([["package", "=", package]])


def runs_of_work(work: str) -> list[dict]:
    """Every run's job of every package made for a Work item."""
    return _jobs([["work", "=", work]])


def _change(job_id: str, change: str, fn: Callable[[dict], Optional[bool]]) -> dict:
    """One change of the job: `fn(doc)` edits it (returns False: leave it)."""
    path = path_of(get(job_id))

    def write(unit):
        rec = unit.get(path)
        if rec is None:
            raise JobError(f"There is no package job {job_id}.")
        doc = rec.data
        if fn(doc) is False:
            return doc
        doc["updated_at"] = time.time()
        unit.put(path, doc)
        return doc
    return records.atomic(write, by=records.by(JARVIS), change=change)


# -- taking a request --------------------------------------------------------------------

def _this_server() -> str:
    """This server process's own id (records_move.BOOT): a job queued by it
    and not started yet is one it is about to start; queued by a server
    that has since stopped, nothing will start it."""
    from jarvis import records_move
    return records_move.BOOT


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def accept(request: dict, *, session: str, work: str = "", general: bool = False,
           engine: str = "", project: str = "", workflow: str = "", source: str = "",
           documents: tuple[tuple[str, str], ...] = (), submitted=None) -> dict:
    """Record a new package's first run for a checked request
    (package_request.check), asked by `session`'s onboarded key: the
    package id, its first run id, and the job queued. JARVIS's record,
    written by JARVIS; who asked is `requested_by`."""
    package = _new_id("package")
    return _record_run(package, "run-1", request, session=session, work=work, general=general,
                       engine=engine, project=project, workflow=workflow, source=source,
                       documents=documents, submitted=submitted, change="requested")


def _record_run(package: str, run: str, request: dict, *, session: str, work: str,
                general: bool, engine: str, project: str, workflow: str, source: str,
                documents, submitted, change: str, after: Optional[dict] = None,
                inputs: Optional[dict] = None) -> dict:
    now = time.time()
    doc = {"id": _new_id("job"), "package": package, "run": run, "work": work or None,
           "general": bool(general), "requested_by": f"agent:{session}", "session": session,
           "requested_at": (after or {}).get("requested_at") or now,
           "engine": engine or None, "project": project or "", "workflow": workflow or "",
           "source": source or "", "documents": [list(d) for d in documents],
           "request": request, "submitted": submitted, "state": QUEUED, "step": None,
           "done": [], "rounds": [], "result": None, "error": None, "told": None,
           "after": (after or {}).get("id"), "pid": None, "queued_by": _this_server(),
           "inputs": inputs or {"revisions": [], "findings": []},
           "created_at": now,
           "updated_at": now}
    doc["folder"] = str(folder_of(doc))
    doc["log"] = str(Path(doc["folder"]) / LOG)
    records.atomic(lambda unit: unit.put(path_of(doc), doc), by=records.by(JARVIS),
                   change=change)
    return doc


def inputs_after(before: dict, revision: Optional[dict] = None) -> dict:
    """What the next run of a package is asked beyond the request: the run
    before's inputs, then the sender's answers to the findings it ended
    with (the stage they are for), then a revision ({"words", "by":
    "sender" or "tim"}: its check said what is wrong, or Tim rejected it)."""
    had = before.get("inputs") or {}
    out = {"revisions": list(had.get("revisions") or []),
           "findings": list(had.get("findings") or [])}
    last = (before.get("rounds") or [None])[-1]
    if last and kind_of(last) == FINDINGS and last.get("answers"):
        out["findings"].append({"run": before["run"], "stage": last.get("stage"),
                                "questions": last.get("questions") or [],
                                "answers": last["answers"]})
    if revision is not None:
        out["revisions"].append({"run": before["run"], "by": revision["by"],
                                 "words": revision["words"]})
    return out


def new_run(job_id: str, why: str, revision: Optional[dict] = None) -> dict:
    """The next run of the same package (the sender answered a failed run's
    findings or asked for it again, said what is wrong at its check, or Tim
    rejected it): the package keeps its id, a new job names the next run
    id, asked as the first was, with the run before's inputs and what this
    run adds (`inputs_after`). It reuses every step and unit whose inputs
    are unchanged from the step cache (step_cache.py), so only what the new
    inputs change is done again."""
    before = get(job_id)
    runs = _jobs([["package", "=", before["package"]]])
    n = 1 + max(int(str(d["run"]).rsplit("-", 1)[-1]) for d in runs)
    return _record_run(before["package"], f"run-{n}", before["request"],
                       session=before["session"], work=before["work"] or "",
                       general=before["general"], engine=before["engine"] or "",
                       project=before["project"], workflow=before["workflow"],
                       source=before["source"], documents=tuple(map(tuple, before["documents"])),
                       submitted=before["submitted"], change=f"new run: {why}", after=before,
                       inputs=inputs_after(before, revision))


def rerun(job_id: str, session: str) -> dict:
    """The sender asks for its package again after a run that ended in
    Error, or failed (its swarm's or deck's findings, answered or not),
    without new answers (make_package --rerun, package_rerun): checked here;
    the server starts the next run, which reuses every step whose inputs
    are unchanged (`new_run`, step_cache.py). Refused while a later run of the package is
    still going. A run waiting on its findings ends `failed`, unanswered."""
    doc = status(job_id, session)
    going = [d for d in runs_of(doc["package"]) if d["state"] in GOING + (WAITING,)
             and d["id"] != job_id and _run_no(d) > _run_no(doc)]
    if going:
        later = going[0]
        raise JobError(f"{later['run']} of package {doc['package']} ({later['id']}) is "
                       f"{later['state']}; ask again once it has ended.")
    findings = bool(doc["rounds"]) and kind_of(doc["rounds"][-1]) == FINDINGS
    if doc["state"] == WAITING and findings:
        def write(d):
            if d["state"] != WAITING:
                raise JobError(f"Job {job_id} is {d['state']}.")
            d["state"], d["pid"] = FAILED, None
        doc = _change(job_id, "asked again without answers", write)
    elif doc["state"] not in (ERROR, FAILED):
        raise JobError(f"Job {job_id} is {doc['state']}: only a run that ended in Error or "
                       f"failed is run again.")
    return doc


def _run_no(doc: dict) -> int:
    return int(str(doc["run"]).rsplit("-", 1)[-1])


# -- what the sender calls ---------------------------------------------------------------

def status(job_id: str, session: str = "") -> dict:
    """The job's record; a sender reads only its own."""
    doc = get(job_id)
    if session and session != doc["session"]:
        raise JobError(f"Job {job_id} is session {doc['session']}'s.")
    return doc


def answer(job_id: str, answers: dict, *, session: str = "") -> dict:
    """The sender's answers to the questions the job asked it, by question
    id; the job is queued again and goes on. Only its own sender answers."""
    doc = status(job_id, session)
    if doc["state"] != WAITING or not doc["rounds"]:
        raise JobError(f"Job {job_id} is {doc['state']}, not waiting on your answers.")
    if not isinstance(answers, dict) or not answers:
        raise JobError("Answer with {question id: your answer}.")
    if kind_of(doc["rounds"][-1]) == CHECK:
        given = check_answer(job_id, answers)
    else:
        asked = {q["id"] for q in doc["rounds"][-1]["questions"]}
        unknown = sorted(set(answers) - asked)
        if unknown:
            raise JobError(f"Job {job_id} asked no question {', '.join(unknown)}; it asked "
                           f"{', '.join(sorted(asked))}.")
        given = {}
        for key, value in answers.items():
            if not isinstance(value, str) or not value.strip():
                raise JobError(f"The answer to {key} is words.")
            given[key] = value.strip()

    def write(d):
        if d["state"] != WAITING:
            raise JobError(f"Job {job_id} is {d['state']}, not waiting on your answers.")
        last = d["rounds"][-1]
        last.update(answers=given, answered_at=time.time())
        if kind_of(last) == CHECK and checked(last):
            # Approved, its answer signed with its own key: JARVIS delivers
            # it at once (server._deliver_package_run).
            last["approved_by"] = f"agent:{d['session']}"
            d.update(state=RUNNING, step=STEPS[-1], pid=None, queued_by=_this_server())
        elif kind_of(last) in (FINDINGS, CHECK):
            d["state"], d["pid"] = FAILED, None     # a new run follows (new_run)
        else:
            d["state"], d["queued_by"] = QUEUED, _this_server()
    doc = _change(job_id, "answered", write)
    # Kept with the package's evidence too, a file a round, indexed as it is
    # written (workflow.yaml gather layout sender-answers/).
    from jarvis import package_evidence
    place = package_evidence.place_of(doc)
    if package_evidence.gathered(place) is not None:
        package_evidence.add_sender_answers(place, len(doc["rounds"]),
                                            doc["rounds"][-1]["questions"], given)
    return doc


def check_answer(job_id: str, answers: dict) -> dict:
    """The sender's answer to its check, structured (workflow.yaml deliver
    sender_check answer): {"check": "approve"}, or {"check": "change",
    "what_is_wrong": its words}. Anything else is refused, saying the form."""
    form = (f"Answer the check with {CHECK_ID}={APPROVE}, or {CHECK_ID}={CHANGE} and "
            f"{WHAT_IS_WRONG}=<what is wrong>.")
    unknown = sorted(set(answers) - {CHECK_ID, WHAT_IS_WRONG})
    if unknown:
        raise JobError(f"Job {job_id}'s check takes no {', '.join(unknown)}. {form}")
    said = answers.get(CHECK_ID)
    said = said.strip().casefold() if isinstance(said, str) else ""
    if said not in CHECK_ANSWERS:
        raise JobError(f"{CHECK_ID} is {APPROVE} or {CHANGE}. {form}")
    wrong = answers.get(WHAT_IS_WRONG)
    wrong = wrong.strip() if isinstance(wrong, str) else ""
    if said == CHANGE and not wrong:
        raise JobError(f"{CHECK_ID}={CHANGE} needs {WHAT_IS_WRONG}: your words of what is wrong.")
    if said == APPROVE and WHAT_IS_WRONG in answers:
        raise JobError(f"{CHECK_ID}={APPROVE} approves it as it is; to say what is wrong, "
                       f"answer {CHECK_ID}={CHANGE} with {WHAT_IS_WRONG}.")
    return {CHECK_ID: said, **({WHAT_IS_WRONG: wrong} if said == CHANGE else {})}


# -- what a step calls -------------------------------------------------------------------

def kind_of(round_: dict) -> str:
    """What a round asked: QUESTIONS (a round from before kinds is one),
    FINDINGS or CHECK."""
    return round_.get("kind") or QUESTIONS


def checked(round_: dict) -> bool:
    """Whether the sender's answer to its check approves the package:
    `check` is APPROVE (check_answer)."""
    said = (round_.get("answers") or {}).get(CHECK_ID) or ""
    return kind_of(round_) == CHECK and said == APPROVE


def to_deliver(doc: dict) -> bool:
    """The sender approved the finished package and it is not delivered yet."""
    return doc["state"] == RUNNING and bool(doc["rounds"]) and checked(doc["rounds"][-1])


def question_rounds(doc: dict) -> int:
    """The rounds of questions asked so far: a failed swarm's findings are
    not one."""
    return sum(1 for r in doc.get("rounds") or [] if kind_of(r) == QUESTIONS)


def ask(job_id: str, questions: list[dict]) -> bool:
    """A step's questions to the sender, each {"id", "question"}: True when
    they are asked (the job waits on the sender); False when every round
    is used."""
    asked = {}

    def write(d):
        if question_rounds(d) >= MAX_ROUNDS:
            asked["now"] = False
            return False
        d["rounds"] = d["rounds"] + [{"kind": QUESTIONS, "questions": questions,
                                      "asked_at": time.time(), "answers": None,
                                      "answered_at": None, "told": False}]
        d["state"], d["pid"] = WAITING, None
        asked["now"] = True
    _change(job_id, "asked", write)
    return asked["now"]


def failed_swarm(job_id: str, questions: list[dict], findings: list[dict],
                 stage: Optional[str] = None) -> dict:
    """The swarm failed (workflow.yaml swarm when_it_fails): its findings go
    to the sender, each failed question one to answer, {"id", "question"};
    the job waits on its answer, which starts a new run, the answers an
    input of `stage` (the step cache unit that failed, step_cache.py). The
    server tells the sender and the Work item once (`findings_to_tell`)."""
    def write(d):
        d["rounds"] = d["rounds"] + [{"kind": FINDINGS, "questions": questions,
                                      "stage": stage,
                                      "findings": findings, "asked_at": time.time(),
                                      "answers": None, "answered_at": None, "told": False}]
        d["state"], d["pid"] = WAITING, None
    return _change(job_id, "swarm failed", write)


def ask_check(job_id: str, package: dict) -> dict:
    """The finished package back to its sender before delivery (workflow.yaml
    deliver sender_check): one question, its check, with the instructions
    word for word; `package` says where the package is and the message that
    carries it. The job waits on its answer; the server tells it once
    (`to_tell_sender`)."""
    def write(d):
        d["rounds"] = d["rounds"] + [{"kind": CHECK, "questions": [
            {"id": CHECK_ID, "question": SENDER_CHECK}], "package": package,
            "asked_at": time.time(), "answers": None, "answered_at": None, "told": False}]
        d["state"], d["pid"] = WAITING, None
    return _change(job_id, "sent to its sender to check", write)


def noted(job_id: str, key: str, value) -> dict:
    """A step's outcome kept on the job (the swarm's result, its files)."""
    return _change(job_id, f"{key} noted", lambda d: d.update({key: value}))


def sign(doc: dict, source: Path, target: Path, statements: tuple[str, ...] = ()) -> None:
    """Sign the package the run made with JARVIS's own key, naming who
    asked (`requested_by`, proven as the request was taken): the deliver
    step's signature (workflow.yaml deliver, decided "signing")."""
    from jarvis import package_signature
    package_signature.sign_as_jarvis(source, target, doc["requested_by"], statements)


def delivered(job_id: str, result: dict) -> dict:
    """Delivered: in Tim's Inbox, `result` saying where."""
    return _change(job_id, DELIVERED, lambda d: d.update(
        state=DELIVERED, result=result, pid=None,
        done=d["done"] + [s for s in (STEPS[-1],) if s not in d["done"]]))


def error(job_id: str, step: str, reason: str, tried: str, fix: str) -> dict:
    """The job in Error: the step, the reason in plain words, what was
    tried and what would fix it. The server tells everyone once
    (`to_tell`, `told`)."""
    def write(d):
        if d["state"] in ENDED:
            return False
        d.update(state=ERROR, pid=None, told=False,
                 error={"step": step, "reason": reason, "tried": tried, "fix": fix,
                        "at": time.time()})
    return _change(job_id, ERROR, write)


def told(job_id: str, message: Optional[int] = None) -> dict:
    """Everyone has been told of the job's Error; `message` is JARVIS's
    message to the asking agent that told it (run_store's id)."""
    return _change(job_id, "told",
                   lambda d: d.update(told=True, told_agent_message=message))


# -- what the server looks after ---------------------------------------------------------

def _alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def overdue(now: Optional[float] = None) -> list[dict]:
    """Put in Error each job that cannot go on any more: its process ended
    without finishing it, it was queued by a server that stopped before
    starting its process (so nothing ever will), or its sender has not
    answered within SENDER_WAIT_SEC. The jobs it put in Error."""
    now = time.time() if now is None else now
    moved = []
    for doc in _jobs([["state", "in", [QUEUED, RUNNING, WAITING]]]):
        step = doc.get("step") or STEPS[0]
        if doc["state"] == WAITING:
            asked_at = doc["rounds"][-1]["asked_at"] if doc["rounds"] else doc["updated_at"]
            if now - asked_at > SENDER_WAIT_SEC:
                n = len(doc["rounds"][-1]["questions"]) if doc["rounds"] else 0
                kind = kind_of(doc["rounds"][-1]) if doc["rounds"] else QUESTIONS
                moved.append(error(
                    doc["id"], step,
                    {FINDINGS: "The sender did not answer the swarm's findings within 2 hours.",
                     CHECK: "The sender did not answer its check of the package within 2 hours."
                     }.get(kind, "The sender did not answer the questions within 2 hours."),
                    {FINDINGS: f"JARVIS sent the sending agent the swarm's findings on {n} "
                               "question(s) and waited 2 hours.",
                     CHECK: "JARVIS sent the sending agent the finished package to check and "
                            "waited 2 hours."}.get(
                        kind, f"JARVIS asked the sending agent {n} question(s) (round "
                              f"{question_rounds(doc)}) and waited 2 hours."),
                    "The sending agent answers the questions (package_answer) and asks for a "
                    "new run, or tells Tim why it cannot."))
        elif not doc.get("pid") and to_deliver(doc):
            if doc.get("queued_by") != _this_server():
                moved.append(error(
                    doc["id"], step, "JARVIS stopped while delivering the package its sender "
                                     "had approved.",
                    "JARVIS was delivering the approved package to Tim's Inbox.",
                    "A new run of the package; JARVIS starts it."))
        elif not doc.get("pid"):
            if doc.get("queued_by") != _this_server():
                moved.append(error(
                    doc["id"], step,
                    "JARVIS stopped after taking the request and before starting the run's "
                    "process, so nothing was running it.",
                    f"JARVIS queued the run (job {doc['id']}) to start in its own process.",
                    "A new run of the package; JARVIS starts it."))
        elif not _alive(doc["pid"]):
            moved.append(error(
                doc["id"], step, "The run's process ended without finishing it.",
                f"JARVIS ran the {step} step in its own process (log: {doc['log']}).",
                "Read the run's log for the cause; once it is fixed, a new run."))
    return moved


def to_tell() -> list[dict]:
    """The jobs in Error whose Error nobody has been told of yet."""
    return _jobs([["state", "=", ERROR], ["told", "=", False]])


def to_tell_sender() -> list[dict]:
    """The jobs waiting on the sender with a round nobody has told it of
    yet: the swarm's questions, a failed swarm's findings, or the finished
    package to check. (A round from before rounds were told has no `told`
    and is not told now.)"""
    return [d for d in _jobs([["state", "=", WAITING]])
            if d["rounds"] and d["rounds"][-1].get("told") is False]


def told_sender(job_id: str, message: Optional[int] = None) -> dict:
    """The sender has been told of its round (the swarm's questions, its
    findings, or its check); `message` is JARVIS's message to it
    (run_store's id)."""
    def write(d):
        if not d["rounds"]:
            return False
        d["rounds"][-1].update(told=True, told_agent_message=message)
    return _change(job_id, "sender told", write)


def later_run(job_id: str) -> Optional[dict]:
    """The run that followed this one (`new_run`), or None."""
    found = _jobs([["after", "=", job_id]])
    return found[0] if found else None


def delivered_as(inbox_id: str) -> Optional[dict]:
    """The run whose package was delivered as this inbox row, when no later
    run of its package has followed it; else None."""
    for d in _jobs([["state", "=", DELIVERED]]):
        if (d.get("result") or {}).get("inbox") == inbox_id and later_run(d["id"]) is None:
            return d
    return None


def rejected(job_id: str, words: str) -> dict:
    """Tim rejected the package this run delivered, with his words
    (workflow.yaml deliver arrive): kept on the run."""
    return _change(job_id, "rejected by Tim", lambda d: d.update(
        rejected={"words": words, "at": time.time()}))


def _rejected_runs(where: list) -> list[dict]:
    runs = sorted(_jobs(where), key=lambda d: (str(d["package"]),
                                               int(str(d["run"]).rsplit("-", 1)[-1])))
    return [{"package": d["package"], "run": d["run"], **d["rejected"]}
            for d in runs if d.get("rejected")]


def rejections(package: str) -> list[dict]:
    """His Reject of each delivered run of a package, in run order: {package,
    run, words, at}."""
    return _rejected_runs([["package", "=", package]])


def rejections_of_request(doc: dict) -> list[dict]:
    """His Reject of each delivered run of this package and of every other
    package with no Work item that the same session asked with the same
    handoff, each package's in run order (Tim's record for a package with
    no Work item: a new request with the same handoff carries his word on
    the earlier ones)."""
    from jarvis import step_cache

    def asked(d: dict):
        return step_cache.digest(d.get("submitted") if d.get("submitted") is not None
                                 else d.get("request"))
    mine = asked(doc)
    same = {d["package"] for d in _jobs([["session", "=", doc.get("session") or ""]])
            if not d.get("work") and asked(d) == mine}
    same.add(doc["package"])
    return [r for p in sorted(same) for r in rejections(p)]


def rejections_on_work(work: str) -> list[dict]:
    """His Reject of each delivered run of every package made for a Work
    item, each package's in run order: {package, run, words, at}."""
    return _rejected_runs([["work", "=", work]])


def errored_work() -> set[str]:
    """The Work items with a package run in Error that no later run of its
    package has followed (`new_run`, from it or from an earlier run)."""
    return {d["work"] for d in _jobs([["state", "=", ERROR]])
            if d.get("work") and not any(_run_no(o) > _run_no(d)
                                         for o in runs_of(d["package"]))}


# -- the run's own process ---------------------------------------------------------------

def _spawn_detached(doc: dict) -> int:
    """The run's process, JARVIS's own (its checkout's code), detached from
    the server (its own session, no terminal), in the run's folder, writing
    to the run's log; its pid."""
    env = env_names.child(PYTHONPATH=str(data_paths.PACKAGE_DIR.parent))
    with open(doc["log"], "ab") as log:
        child = subprocess.Popen(
            [sys.executable, "-P", "-m", "jarvis.package_jobs", "run", doc["id"]],
            cwd=doc["folder"], env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    return child.pid


SPAWN: Callable[[dict], int] = _spawn_detached


def start(doc: dict) -> dict:
    """Start the run's process; the job names its pid. A process that
    cannot be started puts the job in Error, saying why."""
    step = doc.get("step") or STEPS[0]
    try:
        pid = SPAWN(doc)
    except OSError as e:
        return error(doc["id"], step, f"The run's process could not be started: {e}",
                     f"JARVIS started python -m jarvis.package_jobs run {doc['id']} in "
                     f"{doc['folder']}.",
                     "Fix what stopped the process starting (the error says what); once it "
                     "is fixed, a new run.")
    return _change(doc["id"], "started", lambda d: d.update(pid=pid))


def _producer(step: str) -> Optional[Callable[[dict], Optional[str]]]:
    named = PRODUCERS.get(step)
    if not named:
        return None
    module, _, function = named.partition(":")
    return getattr(importlib.import_module(module), function)


def run(job_id: str) -> int:
    """The run's process: each step not yet done, in order. A step that
    cannot go on, or fails, puts the job in Error saying so."""
    doc = get(job_id)
    if doc["state"] != QUEUED:
        print(f"Job {job_id} is {doc['state']}; nothing to run.")
        return 1
    _change(job_id, RUNNING, lambda d: d.update(state=RUNNING, pid=os.getpid()))
    step = doc.get("step") or STEPS[0]
    from jarvis import run_report
    _map_his_words(doc)
    try:
        for step in STEPS:
            if step in doc["done"]:
                continue
            _change(job_id, step, lambda d, s=step: d.update(step=s))
            producer = _producer(step)
            if producer is None:
                raise Blocked(step, f"The {step} step is not built in this JARVIS yet.",
                              f"JARVIS took the request and reached the {step} step.",
                              f"This JARVIS needs the {step} step; until then no package can "
                              f"be made.")
            try:
                with run_report.step(doc, step):
                    said = producer(get(job_id))
            finally:
                if step in REPORTED_AFTER:
                    _quality_report(doc)
            if said == WAITING:
                return 0
            doc = _change(job_id, f"{step} done",
                          lambda d, s=step: d.update(done=d["done"] + [s]))
        if get(job_id)["state"] == RUNNING:
            raise Blocked(STEPS[-1], "The deliver step ended without delivering the package.",
                          "JARVIS ran every step.", "The deliver step must record the delivery.")
        return 0
    except Blocked as blocked:
        log.error("the %s step cannot go on: %s", blocked.step, blocked.reason,
                  exc_info=blocked)
        error(job_id, blocked.step, blocked.reason, blocked.tried, blocked.fix)
        return 1
    except BaseException as failed:    # noqa: BLE001 - the job must end, whatever ended it
        log.error("the %s step failed", step, exc_info=failed)
        with contextlib.suppress(Exception):
            error(job_id, step, f"The {step} step failed: {type(failed).__name__}: {failed}",
                  f"JARVIS ran the {step} step (log: {doc['log']}).",
                  "Read the run's log for the cause; once it is fixed, a new run.")
        if isinstance(failed, Exception):
            return 1
        raise


# The steps after which the run's quality report is written again
# (run_report.quality: the swarm's passes, then the deck review's).
REPORTED_AFTER = ("swarm", "deck")


def _quality_report(doc: dict) -> None:
    """The run's quality report written, timed as a step of its own; it
    never stops the run."""
    from jarvis import run_report
    try:
        with run_report.step(doc, "quality-report"):
            run_report.quality(doc)
    except Exception as e:  # noqa: BLE001 - the report never stops the run
        print(f"The run's quality report could not be written: {e}", flush=True)


def _map_his_words(doc: dict) -> None:
    """His Reject words on the run this one follows, mapped to the labels
    in that run's quality report (the server keeps them unmapped)."""
    if not doc.get("after"):
        return
    from jarvis import run_report
    try:
        before = get(doc["after"])
        if not run_report.waits_mapping(before):
            return
        with run_report.step(doc, "quality-report"):
            run_report.map_pending(before)
    except Exception as e:  # noqa: BLE001 - the report never stops the run
        print(f"His words on the earlier run could not be mapped to the labels: {e}",
              flush=True)


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


@contextlib.contextmanager
def logging_to(path: Path):
    """The run's process logging to its own log (`LOG`, job.log in the run's
    folder): every jarvis.* logger at INFO, each exception with its
    traceback, so an Error's "read the run's log" has the cause to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.setLevel(logging.INFO)
    root = logging.getLogger("jarvis")
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler
    except BaseException:
        log.exception("the run's process ended on an exception")
        raise
    finally:
        root.removeHandler(handler)
        root.setLevel(level)
        handler.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.package_jobs")
    parser.add_argument("action", choices=("run",))
    parser.add_argument("job")
    args = parser.parse_args(argv)
    from jarvis import awake, settings_service
    settings_service.load_once()
    # The Mac kept from idle sleep while the run works (awake.py: measured
    # on package-9a8f7c72f87b, a run left to itself slept from 11:13 to
    # 12:07, moving only in the Mac's 45-second maintenance wakes).
    with logging_to(Path(get(args.job)["log"])), awake.held() as kept_awake:
        log.info("the run's process %d runs job %s%s", os.getpid(), args.job,
                 "" if kept_awake else " (nothing keeps the Mac awake: no caffeinate)")
        return run(args.job)


if __name__ == "__main__":
    sys.exit(main())
