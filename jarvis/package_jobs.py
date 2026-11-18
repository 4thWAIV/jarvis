"""Packages as jobs: `make_package` started, followed and answered without
holding the sender's turn.

An agent's tool call cannot wait on the package author: the author may need
facts only the sender has, and the sender's model reads nothing until its
call returns. So `package_start` records a job and starts it in a process of
its own (`python -m jarvis.package_jobs run <id>`, detached), which runs the
one factory (`make_package.main`) with the job's arguments, and returns at
once. The job is one record, `/packages/jobs/<id>.job` (records.py), each
change a version:

    queued -> researching -> writing -> checking -> delivered
                  |                        \\-> refused (any step)
                  v
          waiting_on_sender (the author's questions) -> queued (answered)

When the fresh author needs facts it cannot find it writes its questions
(package_author.read_questions) and stops; the job keeps them and waits on
the sender, who answers with `package_answer` (the agent MCP server's tool,
or `make_package --answer`). The job then resumes the SAME author session
(Claude's --resume, Codex's thread/resume) with the answers, so its
transcript, which author_proof reads, is the one it began with the author
prompt. At most `MAX_ROUNDS` rounds are asked; after them the author is told
to write the package with what is still unanswered marked UNANSWERED, which
goes to Tim as such. `package_status` reads the record.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from jarvis import data_paths, env_names, records

ROOT = "/packages/jobs/"
TYPE = "job"
QUEUED, RESEARCHING, WRITING, CHECKING = "queued", "researching", "writing", "checking"
WAITING = "waiting_on_sender"
DELIVERED, REFUSED = "delivered", "refused"
STATES = (QUEUED, RESEARCHING, WRITING, CHECKING, WAITING, DELIVERED, REFUSED)
RUNNING = (QUEUED, RESEARCHING, WRITING, CHECKING)
ENDED = (DELIVERED, REFUSED)
MAX_ROUNDS = 2
ANSWER_CHARS = 4000
# How long a queued job may wait for its process to say it runs before a
# status read takes it as never started.
START_GRACE_SEC = 60.0
LOG = "job.log"


class JobError(ValueError):
    """A job call refused, in words the calling agent reads."""


def path_of(job_id: str) -> str:
    return f"{ROOT}{records.segment(job_id)}.{TYPE}"


def _by(doc: dict):
    return records.by(f"agent:{doc['session']}" if doc.get("session") else "jarvis")


def _read(job_id: str) -> dict:
    rec = records.get(path_of(job_id)) if isinstance(job_id, str) and job_id else None
    if rec is None:
        raise JobError(f"There is no package job {job_id}.")
    return rec.data


def _change(job_id: str, change: str, fn) -> dict:
    """One change of the job: `fn(doc)` edits it (returns False: leave it)."""
    def run(unit):
        rec = unit.get(path_of(job_id))
        if rec is None:
            raise JobError(f"There is no package job {job_id}.")
        doc = rec.data
        if fn(doc) is False:
            return doc
        doc["updated_at"] = time.time()
        unit.put(path_of(job_id), doc)
        return doc
    return records.atomic(run, by=_by(_read(job_id)), change=change)


# -- what the agent calls ----------------------------------------------------------------

def _engine_of(session: str) -> str:
    """The engine a session runs on, from where its own transcript is."""
    from jarvis import author_proof
    for engine in author_proof.ENGINES:
        claim = author_proof.Claim(engine, session, "0" * 64)
        if author_proof._SESSION.fullmatch(session) and author_proof.transcript(claim):
            return engine
    raise JobError(f"No Claude Code or Codex transcript of session {session} is on this Mac: "
                   f"say which engine you run on.")


def package_start(handoff_path, work: Optional[str] = None, general: bool = False, *,
                  session: Optional[str] = None, engine: Optional[str] = None,
                  project: Optional[str] = None, workflow: str = "", says: str = "",
                  out=None, source=None, document=(), revision=(),
                  deliver_to: Optional[str] = None) -> dict:
    """Record a package job for the handoff and start it detached; returns
    {"job", "state"} at once. The session is the sending agent's
    (JARVIS_AGENT_SESSION when not given), whose key signs it."""
    from jarvis import decision_package as dp
    session = (session or env_names.read("JARVIS_AGENT_SESSION")).strip()
    if not session:
        raise JobError("Give the sending session (your session id).")
    if work and general:
        raise JobError("A package is for one work item or for none (general), not both.")
    # One architecture document, or a view set's several, each with its revision.
    documents = [document] if isinstance(document, str) else list(document or ())
    revisions = [revision] if isinstance(revision, str) else list(revision or ())
    documents, revisions = [d for d in documents if d], [r for r in revisions if r]
    if len(documents) != len(revisions):
        raise JobError("A document and its revision go together.")
    handoff = Path(handoff_path).expanduser().resolve()
    if not handoff.is_file():
        raise JobError(f"There is no handoff at {handoff}.")
    engine = engine or _engine_of(session)
    if engine not in ("claude", "codex"):
        raise JobError("The engine is claude or codex.")
    out = Path(out).expanduser().resolve() if out else Path(tempfile.mkdtemp(prefix="package-"))
    out.mkdir(parents=True, exist_ok=True)
    argv = ["--handoff", str(handoff), "--engine", engine, "--session", session,
            "--project", project or Path.cwd().name, "--out", str(out),
            # Delivered where the agent's own commands and tools reach JARVIS
            # (JARVIS_URL), never past it to another one.
            "--deliver-to", deliver_to or env_names.read("JARVIS_URL").strip() or dp.LIVE]
    for flag, value in (("--work", work), ("--workflow", workflow), ("--says", says)):
        if value:
            argv += [flag, str(value)]
    for named, revised in zip(documents, revisions):
        argv += ["--document", str(named), "--revision", str(revised)]
    if general:
        argv.append("--general")
    if source:
        argv.insert(0, str(Path(source).expanduser().resolve()))
    job_id = "job-" + secrets.token_hex(6)
    now = time.time()
    doc = {"id": job_id, "state": QUEUED, "session": session, "engine": engine,
           "work": work or None, "general": bool(general), "handoff": str(handoff),
           "out": str(out), "argv": argv, "cwd": str(Path.cwd()), "rounds": [],
           "unanswered": [], "author_session": None, "author_engine": None,
           "author_model": None, "source_folder": None, "result": None, "problems": [],
           "pid": None, "log": str(out / LOG), "created_at": now, "updated_at": now}
    records.atomic(lambda unit: unit.put(path_of(job_id), doc), by=_by(doc), change="started")
    SPAWN(job_id, doc)
    return {"job": job_id, "state": QUEUED}


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


def package_status(job: str) -> dict:
    """The job's record. A job whose process ended without finishing it is
    refused, saying so (a job always reaches delivered, refused or waiting)."""
    doc = _read(job)
    if doc["state"] in RUNNING:
        gone = (not _alive(doc.get("pid")) if doc.get("pid") else
                time.time() - doc["updated_at"] > START_GRACE_SEC)
        if gone:
            def ended(d):
                if d["state"] not in RUNNING:
                    return False
                d["state"] = REFUSED
                d["problems"] = [f"The job's process ended without finishing it (see {d['log']})."]
            doc = _change(job, REFUSED, ended)
    return doc


def package_answer(job: str, answers: dict, *, session: Optional[str] = None) -> dict:
    """The sender's answers to the author's open questions, by question id;
    the job is queued again and resumes the same author. Only the job's own
    sender answers (`session`, when the caller is known)."""
    doc = _read(job)
    if session and session != doc["session"]:
        raise JobError(f"Job {job} is session {doc['session']}'s to answer.")
    if doc["state"] != WAITING or not doc["rounds"]:
        raise JobError(f"Job {job} is {doc['state']}, not waiting on your answers.")
    if not isinstance(answers, dict) or not answers:
        raise JobError("Answer with {question id: your answer}.")
    asked = {q["id"] for q in doc["rounds"][-1]["questions"]}
    unknown = sorted(set(answers) - asked)
    if unknown:
        raise JobError(f"Job {job} asked no question {', '.join(unknown)}; it asked "
                       f"{', '.join(sorted(asked))}.")
    given = {}
    for key, value in answers.items():
        if not isinstance(value, str) or not value.strip():
            raise JobError(f"The answer to {key} is words.")
        given[key] = value.strip()[:ANSWER_CHARS]

    def answer(d):
        if d["state"] != WAITING:
            raise JobError(f"Job {job} is {d['state']}, not waiting on your answers.")
        d["rounds"][-1].update(answers=given, answered_at=time.time())
        d["state"] = QUEUED
    doc = _change(job, "answered", answer)
    SPAWN(job, doc)
    return doc


# -- the job's own process ---------------------------------------------------------------

def _spawn_detached(job_id: str, doc: dict) -> None:
    """The job's process, detached from the caller (its own session, no
    terminal), writing to the job's log."""
    env = env_names.child(PYTHONPATH=str(data_paths.PACKAGE_DIR.parent))
    with open(doc["log"], "ab") as log:
        subprocess.Popen([sys.executable, "-P", "-m", "jarvis.package_jobs", "run", job_id],
                         cwd=doc.get("cwd") or None, env=env, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         close_fds=True)


SPAWN = _spawn_detached


class Progress:
    """The job as make_package and the author see it (package_author's
    `job`): each step written to the record as it happens."""

    def __init__(self, job_id: str):
        self.id = job_id

    def _set(self, change: str, **fields) -> dict:
        def edit(d):
            d.update(fields)
        return _change(self.id, change, edit)

    @property
    def resuming(self) -> Optional[dict]:
        """The author to resume, with the message carrying the answers, when
        the sender has answered its last questions; else None."""
        doc = _read(self.id)
        rounds = doc["rounds"]
        if not rounds or rounds[-1].get("answers") is None or not doc.get("author_session"):
            return None
        last = rounds[-1]
        lines = [f"The sending agent answered your questions (round {len(rounds)} of at most "
                 f"{MAX_ROUNDS}). Its answers are evidence from the sender:"]
        missing = []
        for q in last["questions"]:
            said = last["answers"].get(q["id"])
            if said:
                lines.append(f"- {q['id']} ({q['question']}): {said}")
            else:
                missing.append(q)
        if missing:
            lines.append("Not answered, so UNANSWERED wherever it matters in the package: "
                         + "; ".join(f"{q['id']} ({q['question']})" for q in missing))
        lines.append("Continue the assignment from where you stopped: research, write the "
                     "source, and run the check last.")
        return {"author_session": doc["author_session"], "author_engine": doc["author_engine"],
                "author_model": doc.get("author_model"), "source_folder": doc["source_folder"],
                "message": "\n".join(lines)}

    def running(self) -> None:
        self._set("running", pid=os.getpid())

    def researching(self, *, folder: str, author_engine: str,
                    author_model: Optional[str]) -> None:
        self._set(RESEARCHING, state=RESEARCHING, source_folder=folder,
                  author_engine=author_engine, author_model=author_model)

    def writing(self) -> None:
        self._set(WRITING, state=WRITING)

    def checking(self) -> None:
        self._set(CHECKING, state=CHECKING)

    def ask(self, questions: list[dict], *, author_session: str) -> bool:
        """The author's questions: True when they are asked of the sender
        (waiting_on_sender); False when every round is used, and they go
        to Tim unanswered."""
        asked = {}

        def edit(d):
            d["author_session"] = author_session
            if len(d["rounds"]) >= MAX_ROUNDS:
                d["unanswered"] = d["unanswered"] + questions
                asked["now"] = False
                return
            d["rounds"] = d["rounds"] + [{"questions": questions, "asked_at": time.time(),
                                          "answers": None, "answered_at": None}]
            d["state"] = WAITING
            d["pid"] = None
            asked["now"] = True
        _change(self.id, "asked", edit)
        return asked["now"]

    @staticmethod
    def unanswered_message(questions: list[dict]) -> str:
        return ("There are no more question rounds: write the package now from what you have. "
                "State each of these as UNANSWERED where it matters, so Tim sees what is not "
                "known: " + "; ".join(f"{q['id']} ({q['question']})" for q in questions)
                + ". Then run the check last.")

    def delivered(self, result: dict) -> None:
        self._set(DELIVERED, state=DELIVERED, result=result, pid=None)

    def refused(self, problems: list[str]) -> None:
        self._set(REFUSED, state=REFUSED, problems=list(problems), pid=None)


def run(job_id: str) -> int:
    """The job's process: the factory with the job's arguments; whatever
    ends it unexpectedly refuses the job, saying why."""
    from jarvis import make_package
    doc = _read(job_id)
    if doc["state"] != QUEUED:
        print(f"Job {job_id} is {doc['state']}; nothing to run.")
        return 1
    progress = Progress(job_id)
    progress.running()
    try:
        code = make_package.main(doc["argv"] + ["--job", job_id])
        if _read(job_id)["state"] in RUNNING:
            progress.refused([f"The factory stopped without finishing (see {doc['log']})."])
        return code
    except BaseException as error:    # noqa: BLE001 - the job must end, whatever ended it
        with contextlib.suppress(Exception):
            if _read(job_id)["state"] in RUNNING:
                progress.refused([f"The job stopped: {type(error).__name__}: {error}"])
        if isinstance(error, Exception):
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
