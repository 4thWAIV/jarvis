"""A package request: the sender's handoff, checked, and the sender's side
of asking for a package (workflow.yaml, step `request`).

The sending agent asks; it writes nothing of the package. Its handoff is a
JSON object of exactly these fields:

  sender_answers  {"plainly": ..., "why_now": ...}: its answers to the two
                  questions every sender answers before anything runs
                  (QUESTIONS, word for word as Tim approved them). Kept
                  labelled as the sender's: checked by the author against
                  the evidence and the briefing's starting point, never
                  copied to Tim's screen.
  questions       one or several, each {"id", "text", "options"?}: the
                  questions and decisions for Tim, one list. `options` are
                  the options the sender sees ({"id", "name", "says"}),
                  labelled as the sender's; the author writes the final ones.
  evidence        a list of typed entries, each {"type", ...} with its
                  type's fields (evidence_types.FIELDS): file, url, content,
                  work_item, package, message, workflow, run, pr, ci_run,
                  commit, issue, branch, and label (free text, the one type
                  a model resolves). The situation is one of them (a file or
                  content entry). A file is checked to be there and its
                  SHA-256 and size recorded as the request is taken.
  purpose         optional: the sender's own reason (what it needs from Tim,
                  why, what it does after his answer), labelled the sender's.
  recommendation  optional: the sender's recommendation, labelled the same.
  work            optional, as before: a Work item id or a list of them, the
                  Work items the package is ABOUT, kept as a list of ids for
                  the gather step to read as material. It never decides where
                  the package goes: that is `--work` (or none, `--general`).
                  Each must exist: JARVIS refuses a request naming one it
                  has no record of (server._missing_links).
  approval        optional, as before: an architecture document's approval.

`conclusion` and `audience` are refused (the sender's view is its labelled
recommendation; the audience is always Tim), as is any structure for the
briefing. `check` is the one reader, run by JARVIS as it accepts the
request (package_jobs.accept); `start`, `status`, `answer` and `rerun` are the
sender's calls, signed with its own key (make_package --start, --status,
--answer and --rerun, and the agent MCP server's package tools, call them).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import urllib.parse
from pathlib import Path
from typing import Optional

from jarvis import evidence_types

# The two questions every sender answers (Tim, 2026-11-19: "Yes, use that
# wording."), word for word.
QUESTIONS: dict[str, str] = {
    "plainly": ("State plainly and exactly what you are telling us. Name each thing you "
                "mention as what it specifically is, and keep separate things separate: do not "
                "merge two problems, results or decisions into one. Say what you know, what you "
                "only believe, and what you do not know. Leave out wording meant to sound "
                "technical, confident or complete."),
    "why_now": ("Why does Tim need this now? What happened, what happens if he does nothing, "
                "and what changes for him with each answer."),
}

FIELDS = ("sender_answers", "questions", "evidence", "purpose", "recommendation", "work",
          "approval")
# What a handoff may not give, and why.
_ORDER = "the briefing's order is worked out from the evidence, never a sender's structure"
REFUSED = {
    "conclusion": "your view goes in recommendation, labelled as yours",
    "audience": "the audience is always Tim, known from his record",
    "shape": _ORDER, "structure": _ORDER, "slides": _ORDER, "outline": _ORDER, "order": _ORDER,
}
# Fields of the old handoff, and where what they said goes now.
MOVED = {
    "question": "give questions: a list of one or several, each {id, text}",
    "decisions": "questions and decisions are one list: give each decision as a question, "
                 "with the options you see as its options",
    "situation": "give the situation as an evidence entry (a file or content entry)",
}
# The label every value from the sender carries where it is kept.
FROM_THE_SENDER = "the sender's"

class Refused(ValueError):
    """A request not taken: `problems` says each thing wrong with it."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = list(problems)


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _hash(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _entry(n: int, entry, problems: list[str]) -> Optional[dict]:
    """One evidence entry, typed and with its type's fields (each kept as
    the sender gave it, a number as its text); a file's path checked to be
    a file here, its hash and size recorded. Anything more about an entry
    is the gather step's to resolve."""
    where = f"evidence entry {n}"
    if not isinstance(entry, dict):
        problems.append(f"{where} is an object: {{type, ...}}.")
        return None
    kind = entry.get("type")
    if kind not in evidence_types.FIELDS:
        problems.append(f"{where}'s type is one of " + ", ".join(evidence_types.FIELDS) + ".")
        return None
    out: dict = {"type": kind}
    for name in evidence_types.FIELDS[kind]:
        value = entry.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        text = _text(value)
        if not text:
            problems.append(f"{where} ({kind}) needs {name}.")
        out[name] = value if name == "text" and text else text
    if kind == "file" and out.get("path"):
        path = Path(out["path"]).expanduser()
        if not path.is_file():
            problems.append(f"{where} (file): there is no file at {path}.")
        else:
            out["path"] = str(path)
            with contextlib.suppress(OSError):
                out["sha256"], out["size"] = _hash(path)
    return out


def _options(where: str, value, problems: list[str]) -> list[dict]:
    if value is None or value == []:
        return []
    if not isinstance(value, list):
        problems.append(f"{where}'s options is a list of {{id, name, says}}.")
        return []
    out = []
    for m, option in enumerate(value, 1):
        if not isinstance(option, dict) or set(option) - {"id", "name", "says"}:
            problems.append(f"{where}'s option {m} is {{id, name, says}}.")
            continue
        oid, name, says = (_text(option.get(k)) for k in ("id", "name", "says"))
        if not oid or not name or not says:
            problems.append(f"{where}'s option {m} needs id, name and says.")
        out.append({"id": oid, "name": name, "says": says})
    return out


def check(value) -> dict:
    """The handoff as JARVIS keeps it, or Refused with every problem. The
    sender's own words are kept under `from_the_sender` (its answers to
    QUESTIONS, each with the question asked, its purpose and its
    recommendation) and each question's options under
    `options_from_the_sender`."""
    if not isinstance(value, dict):
        raise Refused(["A handoff is a JSON object."])
    problems: list[str] = []
    for key in value:
        if key in REFUSED:
            problems.append(f"The handoff may not give {key}: {REFUSED[key]}.")
        elif key in MOVED:
            problems.append(f"The handoff no longer takes {key}: {MOVED[key]}.")
        elif key not in FIELDS:
            problems.append(f"The handoff may not give {key}: it gives only "
                            + ", ".join(FIELDS) + ".")
    answers = value.get("sender_answers")
    said: dict = {}
    if not isinstance(answers, dict):
        problems.append("The handoff needs sender_answers: your answer to each of the two "
                        "questions, " + " and ".join(QUESTIONS) + ".")
    else:
        for key in sorted(set(answers) - set(QUESTIONS)):
            problems.append(f"sender_answers answers only {' and '.join(QUESTIONS)}; not {key}.")
        for key, question in QUESTIONS.items():
            answer = _text(answers.get(key))
            if not answer:
                problems.append(f"sender_answers needs {key}, your answer to: {question}")
            said[key] = {"question": question, "answer": answer}
    questions = value.get("questions")
    asked: list[dict] = []
    if not isinstance(questions, list) or not questions:
        problems.append("The handoff needs questions: one or several, each {id, text}.")
    else:
        for n, q in enumerate(questions, 1):
            if not isinstance(q, dict) or set(q) - {"id", "text", "options"}:
                problems.append(f"question {n} is {{id, text, options?}}.")
                continue
            qid, text = _text(q.get("id")), _text(q.get("text"))
            if not qid:
                problems.append(f"question {n} needs id.")
            if not text:
                problems.append(f"question {n} needs text: the question for Tim.")
            asked.append({"id": qid, "text": text,
                          "options_from_the_sender": _options(f"question {n}", q.get("options"),
                                                              problems)})
    evidence = value.get("evidence")
    entries: list[dict] = []
    if not isinstance(evidence, list) or not evidence:
        problems.append("The handoff needs evidence: a list of typed entries, each {type, ...}, "
                        "the situation among them.")
    else:
        entries = [e for e in (_entry(n, entry, problems)
                               for n, entry in enumerate(evidence, 1)) if e is not None]
    sender = {}
    for key in ("purpose", "recommendation"):
        if key in value and not _text(value[key]):
            problems.append(f"{key}, when given, is your words.")
        sender[key] = _text(value.get(key)) or None
    work = value.get("work")
    about: list[str] = []
    if work is not None:
        if _text(work):
            about = [_text(work)]
        elif isinstance(work, list) and work and all(_text(w) for w in work):
            about = [_text(w) for w in work]
        else:
            problems.append("The handoff's work is a Work item id or a list of them: the "
                            "Work items the package is about.")
    if "approval" in value and not isinstance(value["approval"], bool):
        problems.append("The handoff's approval is true (ask Tim to approve the architecture "
                        "design) or false.")
    if problems:
        raise Refused(problems)
    return {"questions": asked, "evidence": entries,
            "from_the_sender": {"label": FROM_THE_SENDER, "answers": said, **sender},
            "work": about, "approval": value.get("approval") is True}


def questions_text(request: dict) -> str:
    """A checked request's questions for Tim as one text, in the sender's
    order: what an architecture package states as its purpose."""
    return " ".join(q["text"] for q in request["questions"])


# -- the sender's side ------------------------------------------------------------------

ROUTE = "/api/packages/requests"


def _client(session: str):
    from jarvis import data_paths
    from jarvis.workflow_report import signed_client
    return signed_client(data_paths.CONFIG_DIR / "cert.pem", session, "packages")


def _answer(response) -> dict:
    data = response.json() if response.headers.get("content-type", "").startswith(
        "application/json") else {}
    if response.status_code != 200:
        problems = data.get("problems") or [data.get("error") or response.text]
        raise Refused([str(p) for p in problems])
    return data


def read(handoff: Path) -> dict:
    """The handoff file's JSON as the sender wrote it, each file entry's
    path taken from the handoff's own folder when it is not absolute (the
    folder the sender wrote it in, never JARVIS's). Refused when it cannot
    be read."""
    handoff = Path(handoff).expanduser()
    try:
        handed = json.loads(handoff.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused([f"Cannot read the handoff {handoff}: {e}"]) from None
    if isinstance(handed, dict) and isinstance(handed.get("evidence"), list):
        base = handoff.resolve().parent
        for entry in handed["evidence"]:
            if isinstance(entry, dict) and entry.get("type") == "file" and \
                    isinstance(entry.get("path"), str) and entry["path"].strip():
                path = Path(entry["path"].strip()).expanduser()
                entry["path"] = str(path if path.is_absolute() else base / path)
    return handed


def start(handoff: Path, *, session: str, work: str = "", general: bool = False,
          engine: str = "", project: str = "", workflow: str = "", source: str = "",
          documents: tuple[tuple[str, str], ...] = ()) -> dict:
    """Ask JARVIS for a package: the handoff file's JSON, signed with the
    session's own key, at most once (workflow_report.report). JARVIS
    records who asked from the signature, keeps the job and runs it itself;
    the answer is {job, package, run, state}, or Refused at once."""
    from jarvis.workflow_report import report
    handed = read(handoff)
    query = urllib.parse.urlencode({k: v for k, v in (
        ("work", work), ("general", "1" if general else "")) if v})
    body = {"handoff": handed, "engine": engine, "project": project, "workflow": workflow,
            "source": source, "documents": [list(d) for d in documents]}
    with _client(session) as client:
        try:
            return report(client, ROUTE + (f"?{query}" if query else ""), body)
        except SystemExit as refused:
            raise Refused([str(refused)]) from None


def status(job: str, *, session: str) -> dict:
    """The job's record as JARVIS keeps it (only its own sender reads it)."""
    with _client(session) as client:
        return _answer(client.get(f"{ROUTE}/{urllib.parse.quote(job, safe='')}"))


def answer(job: str, answers: dict, *, session: str) -> dict:
    """The sender's answers to the questions the job asked it, by id."""
    with _client(session) as client:
        return _answer(client.post(f"{ROUTE}/{urllib.parse.quote(job, safe='')}/answer",
                                   {"answers": answers}))


def rerun(job: str, *, session: str) -> dict:
    """Ask for the package again after the run `job` ended in Error or
    failed, with no new answers: the next run goes on from the step that
    failed, reusing what that run kept (package_resume). The new run's job."""
    with _client(session) as client:
        return _answer(client.post(f"{ROUTE}/{urllib.parse.quote(job, safe='')}/rerun", {}))
