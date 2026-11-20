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
          waiting_on_sender
                 |
                 v
               error   (any step that cannot go on)

A step that asks the sender questions (`ask`) leaves the job waiting on it;
the sender answers with `answer` (package_answer, make_package --answer).
At most MAX_ROUNDS rounds are asked. A sender that does not answer within
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
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

from jarvis import data_paths, env_names, records

TYPE = "job"
RECORD_NAME = f"run.{TYPE}"
WORK_ROOT = "/work"
PACKAGES_ROOT = "/packages"
QUEUED, RUNNING, WAITING = "queued", "running", "waiting_on_sender"
DELIVERED, ERROR = "delivered", "error"
STATES = (QUEUED, RUNNING, WAITING, DELIVERED, ERROR)
GOING = (QUEUED, RUNNING)
ENDED = (DELIVERED, ERROR)
# The steps a run is produced by, in order (workflow.yaml record_and_start
# produce).
STEPS = ("gather", "mark", "research", "swarm", "deck", "deliver")
# Each step's program, "module:function", called with the job's record;
# it returns WAITING when it asked the sender and waits on the answers,
# raises Blocked when it cannot go on. A step with none is not built yet.
# The deliver step signs with JARVIS's own key (`sign`).
PRODUCERS: dict[str, str] = {"gather": "jarvis.package_gather:gather",
                             "mark": "jarvis.package_mark:mark"}
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
    from jarvis import model_tiers, sessions
    try:
        return asyncio.run(ask)
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
                documents, submitted, change: str, after: Optional[dict] = None) -> dict:
    now = time.time()
    doc = {"id": _new_id("job"), "package": package, "run": run, "work": work or None,
           "general": bool(general), "requested_by": f"agent:{session}", "session": session,
           "requested_at": (after or {}).get("requested_at") or now,
           "engine": engine or None, "project": project or "", "workflow": workflow or "",
           "source": source or "", "documents": [list(d) for d in documents],
           "request": request, "submitted": submitted, "state": QUEUED, "step": None,
           "done": [], "rounds": [], "result": None, "error": None, "told": None,
           "after": (after or {}).get("id"), "pid": None, "queued_by": _this_server(),
           "created_at": now,
           "updated_at": now}
    doc["folder"] = str(folder_of(doc))
    doc["log"] = str(Path(doc["folder"]) / LOG)
    records.atomic(lambda unit: unit.put(path_of(doc), doc), by=records.by(JARVIS),
                   change=change)
    return doc


def new_run(job_id: str, why: str) -> dict:
    """The next run of the same package (the sender answered a failed run's
    findings, said what is wrong at its check, or Tim rejected it): the
    package keeps its id, a new job names the next run id, asked as the
    first was."""
    before = get(job_id)
    runs = _jobs([["package", "=", before["package"]]])
    n = 1 + max(int(str(d["run"]).rsplit("-", 1)[-1]) for d in runs)
    return _record_run(before["package"], f"run-{n}", before["request"],
                       session=before["session"], work=before["work"] or "",
                       general=before["general"], engine=before["engine"] or "",
                       project=before["project"], workflow=before["workflow"],
                       source=before["source"], documents=tuple(map(tuple, before["documents"])),
                       submitted=before["submitted"], change=f"new run: {why}", after=before)


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
        d["rounds"][-1].update(answers=given, answered_at=time.time())
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


# -- what a step calls -------------------------------------------------------------------

def ask(job_id: str, questions: list[dict]) -> bool:
    """A step's questions to the sender, each {"id", "question"}: True when
    they are asked (the job waits on the sender); False when every round
    is used."""
    asked = {}

    def write(d):
        if len(d["rounds"]) >= MAX_ROUNDS:
            asked["now"] = False
            return False
        d["rounds"] = d["rounds"] + [{"questions": questions, "asked_at": time.time(),
                                      "answers": None, "answered_at": None}]
        d["state"], d["pid"] = WAITING, None
        asked["now"] = True
    _change(job_id, "asked", write)
    return asked["now"]


def sign(doc: dict, source: Path, target: Path, statements: tuple[str, ...] = ()) -> None:
    """Sign the package the run made with JARVIS's own key, naming who
    asked (`requested_by`, proven as the request was taken): the deliver
    step's signature (workflow.yaml deliver, decided "signing")."""
    from jarvis import package_signature
    package_signature.sign_as_jarvis(source, target, doc["requested_by"], statements)


def delivered(job_id: str, result: dict) -> dict:
    return _change(job_id, DELIVERED, lambda d: d.update(state=DELIVERED, result=result,
                                                         pid=None))


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
                moved.append(error(
                    doc["id"], step, "The sender did not answer the questions within 2 hours.",
                    f"JARVIS asked the sending agent {n} question(s) (round "
                    f"{len(doc['rounds'])}) and waited 2 hours.",
                    "The sending agent answers the questions (package_answer) and asks for a "
                    "new run, or tells Tim why it cannot."))
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


def errored_work() -> set[str]:
    """The Work items with a package run in Error that no later run of its
    package has followed (`new_run`)."""
    return {d["work"] for d in _jobs([["state", "=", ERROR]])
            if d.get("work") and not _jobs([["after", "=", d["id"]]])}


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
            if producer(get(job_id)) == WAITING:
                return 0
            doc = _change(job_id, f"{step} done",
                          lambda d, s=step: d.update(done=d["done"] + [s]))
        if get(job_id)["state"] == RUNNING:
            raise Blocked(STEPS[-1], "The deliver step ended without delivering the package.",
                          "JARVIS ran every step.", "The deliver step must record the delivery.")
        return 0
    except Blocked as blocked:
        error(job_id, blocked.step, blocked.reason, blocked.tried, blocked.fix)
        return 1
    except BaseException as failed:    # noqa: BLE001 - the job must end, whatever ended it
        with contextlib.suppress(Exception):
            error(job_id, step, f"The {step} step failed: {type(failed).__name__}: {failed}",
                  f"JARVIS ran the {step} step (log: {doc['log']}).",
                  "Read the run's log for the cause; once it is fixed, a new run.")
        if isinstance(failed, Exception):
            return 1
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.package_jobs")
    parser.add_argument("action", choices=("run",))
    parser.add_argument("job")
    args = parser.parse_args(argv)
    from jarvis import settings_service
    settings_service.load_once()
    return run(args.job)


if __name__ == "__main__":
    sys.exit(main())
