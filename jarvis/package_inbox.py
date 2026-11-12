"""The package inbox: every package an agent sends Tim, from its arrival
until it is closed (docs/planning/package-inbox/design.md).

A package arrives one way, `python -m jarvis.make_package`, which posts the
zip to `POST /api/packages/inbox` (package_inbox_api.py). It is checked and
unpacked as any local zip is (work_packages.unpack_held), recorded here, and
shown nowhere: it reaches his screen only on his own act (his Open, or
asking JARVIS on a turn he drove).

One document per arrival in JARVIS's records (`records`, the document
engine), `/packages/<inbox id>.package`, and every change to it one version
recording its event (no words of the package). The entries are the backlog
the Queue's Packages section lists and the brain's `package_inbox` reads.
This module is the only writer: one write per change (`records.atomic`),
the revision moved inside it, written before any hint.

States, in order:

    arrived -> opened -> answering -> answered -> closed

closed with why: `signed_off` (he is done with it: whatever he answered, or
that he has reviewed it, goes back to its sender, `signed_off_at` its
time), `dismissed` (he set it aside, nothing sent back) or `superseded` (a
newer one of the same name came from the same sender while it was open).
Whatever way it closes, nothing is left waiting on it in the same write
(wait_links): signed off or set aside, every wait on it ends and its Work
item's vote (`understood-<inbox id>`) closes with it; replaced, every wait
on it waits on the newer one instead (`superseded_by`, which also takes its
Work item when it names none) and its vote closes.
A package unpacked here outside the inbox gets a row when he signs it off
(`adopt`), so every package is signed off the same way.

A decision package (the shared deck's: `deck-data.js` and
`scripts/decide.py`) has its questions in `deck-data.js`, read as JSON after
`window.DECK = ` and never executed, and his answers in its own record,
`results/decisions.json`, which the deck's page and `decide.py` both write.
The row only mirrors how many of the questions that apply are answered,
read again after each change of that record (`questions`, `counts`).

Everything a package says (its title, its questions) is the sender's words,
shown and spoken only through the same walls as any other.
"""
from __future__ import annotations

import dataclasses
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from jarvis import (decision_stamp, package_story, record_cells, records, wait_links,
                    work_packages)
from jarvis.work_packages import Package, PackageError

ARRIVED, OPENED, ANSWERING, ANSWERED, CLOSED = (
    "arrived", "opened", "answering", "answered", "closed")
STATES = (ARRIVED, OPENED, ANSWERING, ANSWERED, CLOSED)
# Not closed: in the backlog.
OPEN_STATES = STATES[:-1]
SIGNED_OFF, DISMISSED, SUPERSEDED = "signed_off", "dismissed", "superseded"
CLOSED_WHY = (SIGNED_OFF, DISMISSED, SUPERSEDED)

# How a change came about, as each event records it.
BY_VOICE, ON_THE_PAGE, BY_THE_SENDER, BY_JARVIS = (
    "by voice", "on the page", "by the sender", "by JARVIS")
HOWS = (BY_VOICE, ON_THE_PAGE, BY_THE_SENDER, BY_JARVIS)

# The deck's own files (decision_stamp.MARKS) and how its data is written.
DECK_DATA, DECIDE = decision_stamp.MARKS
DATA_PREFIX = "window.DECK = "
RECORD = "decisions.json"
SUMMARY = "decisions.md"
# The answers a decision takes besides its options, as decide.py names them.
LATER, NONE = "later", "none"
SPECIAL = {LATER: "Decide later", NONE: "None of these"}

# A decision's and an option's id, as the deck's own checks make them
# (decision_package.SLUG): the only words of a deck JARVIS ever says
# outside a wall. A decision or option with any other id is not read.
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

TITLE_MAX = 120
REF_MAX = 128
LIST_MAX = 100

class InboxRefused(ValueError):
    """A change the inbox cannot make, with why, in words he can hear."""


@dataclass(frozen=True)
class Sender:
    """Who sent it: the project the command was run for, the session proven
    to have run it (what the answers go back to; server._package_sender),
    that agent's one name, the session it claimed (`?session=`, only a
    claim) and how the claim was proven ("" when it was not)."""
    project: str = ""
    session: str = ""
    agent: str = ""
    claimed: str = ""
    evidence: str = ""

    def key(self) -> tuple[str, str]:
        """Who counts as the same sender: the session, else the project."""
        return ("session", self.session) if self.session else ("project", self.project)


@dataclass(frozen=True)
class Entry:
    id: str
    package_id: str
    sha256: str
    name: str
    version: str
    title: str
    purpose: str
    runs: str
    decisions_total: int
    decisions_answered: int
    from_project: str
    from_session: str
    from_agent: str
    workflow_id: Optional[str]
    work_id: Optional[str]
    project_id: Optional[str]
    state: str
    revision: int
    capture_id: Optional[int]
    return_asked_at: Optional[float]
    message_id: Optional[int]
    arrived_at: float
    opened_at: Optional[float]
    answered_at: Optional[float]
    returned_at: Optional[float]
    closed_at: Optional[float]
    closed_why: Optional[str]
    signed_off_at: Optional[float] = None
    claimed_session: str = ""        # the session it said it came from: only a claim
    sender_evidence: str = ""        # how `from_session` was proven; "" when it was not
    reprove: int = 0                 # 1: its claim is still to be proven once again (migration 4)

    @property
    def unproven_claim(self) -> bool:
        """It named a session nothing proved sent it, and nothing is still
        looked for: his answers go nowhere."""
        return bool(self.claimed_session) and not self.from_session and not self.reprove

    @property
    def proof_pending(self) -> bool:
        """It named a session whose proof is still looked for (`reprove`)."""
        return bool(self.claimed_session) and not self.from_session and bool(self.reprove)

    @property
    def is_open(self) -> bool:
        return self.state != CLOSED

    @property
    def is_decisions(self) -> bool:
        """It asks him decisions (a decision deck with at least one)."""
        return self.decisions_total > 0

    @property
    def waiting(self) -> int:
        """How many of the questions that apply have no answer yet."""
        return max(0, self.decisions_total - self.decisions_answered)

    def sender_name(self) -> str:
        """Who sent it, as it is shown: the agent's one name, else the project."""
        return self.from_agent or self.from_project

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Arrival:
    entry: Entry
    fresh: bool                                        # False: these bytes were already open here
    superseded: list[Entry] = field(default_factory=list)


@dataclass(frozen=True)
class Question:
    """One decision a deck asks, with what is recorded for it."""
    id: str
    question: str
    context: str
    options: list[dict]                 # {"id", "name", "says"}
    recommended: str
    reason: str
    applies: bool                       # a follow-up asked only after its parent's answer
    answer: Optional[dict]              # {"answer", "choice", "note", "at", "recommended",
    #                                     and for his approval "subject"}
    # His approval of an architecture design (APPROVAL_ID) only: the answer
    # was given to a revision or kit no longer current, so it counts for
    # nothing (`current_subject`).
    stale: bool = False

    def option_name(self, option_id: str) -> str:
        if option_id in SPECIAL:
            return SPECIAL[option_id]
        return next((o["name"] for o in self.options if o["id"] == option_id), option_id)

    def answered_id(self) -> str:
        """His answer by its id (an option's, later or none), with his own
        words after it, and for his approval the revision it was given to
        and whether it is stale; "" while unanswered. Ids and hashes only:
        no word of the deck."""
        if not self.answer:
            return ""
        return self.answer["answer"] + (f" (his words: {self.answer['note']})"
                                        if self.answer.get("note") else "") + self._given_to()

    def _given_to(self) -> str:
        """For his approval: the revision it was given to, and if stale."""
        subject = (self.answer or {}).get("subject")
        if not subject:
            return ""
        said = f", given to revision {subject.get('revision')} of {subject.get('document_id')}"
        return said + (" (stale: a newer revision or kit has been checked since)"
                       if self.stale else "")

    def counts(self) -> bool:
        """Whether his answer stands: given, and not stale."""
        return bool(self.answer) and not self.stale

    def ends_waits(self) -> bool:
        """Whether his answer ends what waits on this question: any answer
        that stands, except that only his approval of the current revision
        ends a wait on his approval."""
        return self.applies and self.counts() and (
            self.id != APPROVAL_ID or self.answer["answer"] == APPROVE)

    def answered_words(self) -> str:
        """His answer in words: the option's name, Decide later or None of
        these, with his own words after it; "" while unanswered."""
        if not self.answer:
            return ""
        said = self.option_name(self.answer["answer"])
        return said + (f" ({self.answer['note']})" if self.answer.get("note") else "") \
            + self._given_to()

    def public(self) -> dict:
        return asdict(self)


# ── the deck's own record ────────────────────────────────────────────────

def is_decision_package(package: Package) -> bool:
    """The shared deck's files are both in its folder, as regular files."""
    return all((package.folder / mark).is_file() and not (package.folder / mark).is_symlink()
               for mark in (DECK_DATA, DECIDE))


def deck(package: Package) -> Optional[dict]:
    """A decision package's deck data, read as JSON after its prefix and
    never executed; None for any other package, or one whose data cannot
    be read."""
    if not is_decision_package(package):
        return None
    try:
        text = work_packages.page_file(package, DECK_DATA).decode("utf-8")
        start = text.index(DATA_PREFIX) + len(DATA_PREFIX)
        data = json.JSONDecoder().raw_decode(text, start)[0]
    except (PackageError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _record(package: Package, name: str = RECORD) -> dict:
    """Its answers as results/<name> (decisions.json by default) holds them
    now; {} when there are none or the file cannot be read."""
    raw = work_packages.read_result(package, name)
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def questions(package: Package) -> list[Question]:
    """The deck's decisions in its order, each with whether it applies (a
    follow-up only once its parent's answer opens it, as decide.py says)
    and the answer recorded; [] for a package that asks none."""
    data = deck(package)
    raw = [d for d in (data or {}).get("decisions") or [] if isinstance(d, dict)
           and ID.fullmatch(_text(d.get("id")))]
    if not raw:
        return _story_questions(package)
    record = _record(package)
    options = {d["id"]: [{"id": _text(o.get("id")), "name": _text(o.get("name")),
                          "says": _text(o.get("says"))}
                         for o in d.get("options") or []
                         if isinstance(o, dict) and ID.fullmatch(_text(o.get("id")))]
               for d in raw}
    parents: dict[str, tuple[str, list[str]]] = {}
    for d in raw:
        for o in d.get("options") or []:
            if not isinstance(o, dict):
                continue
            for child in o.get("opens") or []:
                parent = parents.setdefault(str(child), (d["id"], []))
                parent[1].append(_text(o.get("id")))

    def answer_of(d_id: str) -> Optional[dict]:
        e = record.get(d_id)
        if not isinstance(e, dict) or not isinstance(e.get("answer"), str):
            return None
        if e["answer"] not in SPECIAL and e["answer"] not in {o["id"] for o in options[d_id]}:
            return None
        return {"answer": e["answer"], "choice": _text(e.get("choice")),
                "note": _text(e.get("note")), "at": _text(e.get("at")),
                "recommended": bool(e.get("recommended"))}

    def applies(d_id: str, seen: frozenset = frozenset()) -> bool:
        if d_id not in parents or d_id in seen:
            return True
        parent, opening = parents[d_id]
        given = answer_of(parent) if parent in options else None
        return bool(given) and given["answer"] in opening and applies(parent, seen | {d_id})

    return [Question(d["id"], _text(d.get("question")), _text(d.get("context")),
                     options[d["id"]], _text(d.get("recommended")), _text(d.get("reason")),
                     applies(d["id"]), answer_of(d["id"]))
            for d in raw]


def counts(asked: list[Question]) -> tuple[int, int]:
    """(answered, total) over the questions that apply."""
    applying = [q for q in asked if q.applies]
    return sum(1 for q in applying if q.counts()), len(applying)


def title_of(package: Package) -> str:
    """What it is called: a deck's title, else its name in words."""
    data = deck(package) or {}
    title = _text(data.get("title")) or package.manifest.name.replace("-", " ")
    return " ".join(title.split())[:TITLE_MAX]


# ── every package carries his vote ───────────────────────────────────────
# Tim's rule: packages carry votes for him. make_package gives a deck or page
# that asks nothing the standard vote, `understood`, at its end: a deck as one
# more decision (`understood`), a hand-made page as its story's last slide
# (`understood_slide`), waiting on its answers file, each in the deck's or
# story's language. JARVIS reads a page's
# `understood` from that file (`questions`) and records his answer there
# (`record_story_answer`), since a page has no decide.py. An agent or a
# conversation package cannot carry one, so it may not name a Work item;
# make_package and the inbox route refuse in `no_vote`'s words.

UNDERSTOOD_ID, GOT_IT, HAS_QUESTIONS = "understood", "got-it", "questions"
# The vote's words in each language a deck or story may be in (en, es).
_VOTE_WORDS = {
    "en": {"short": "Got it?", "question": "Is it clear, or do you have questions?",
           "got": ("Got it", "It is clear: nothing to ask."),
           "asks": ("I have questions",
                    "Say what you want to ask: your words go back to the sender."),
           "reason": "Choose Got it once you have read it; your questions go back to the sender.",
           "text_only": "Whether he understood it is a reply, not a choice between looks.",
           "say": "That is all of it. Got it, or do you have questions?"},
    "es": {"short": "¿Entendido?", "question": "¿Está claro, o tiene preguntas?",
           "got": ("Entendido", "Está claro: no hay nada que preguntar."),
           "asks": ("Tengo preguntas",
                    "Diga lo que quiere preguntar: sus palabras vuelven a quien lo envió."),
           "reason": "Elija Entendido cuando lo haya leído; sus preguntas vuelven a quien lo "
                     "envió.",
           "text_only": "Si lo entendió es una respuesta, no una elección entre imágenes.",
           "say": "Eso es todo. ¿Entendido, o tiene preguntas?"},
}


def _vote_words(language: str) -> dict:
    return _VOTE_WORDS.get(language, _VOTE_WORDS["en"])


def understood(language: str) -> dict:
    """The standard vote as a deck's decision, in `language`."""
    w = _vote_words(language)
    return {"id": UNDERSTOOD_ID, "short": w["short"], "question": w["question"],
            "options": [{"id": GOT_IT, "name": w["got"][0], "says": w["got"][1]},
                        {"id": HAS_QUESTIONS, "name": w["asks"][0], "says": w["asks"][1]}],
            "recommended": GOT_IT, "reason": w["reason"], "text_only": w["text_only"]}


def understood_slide(language: str) -> dict:
    """The standard vote as a story's last slide, in `language`."""
    w = _vote_words(language)
    return {"title": w["short"], "waits": UNDERSTOOD_ID, "beats": [{"say": w["say"]}]}


# ── his approval of an architecture design ───────────────────────────────
# Not the standard vote: `understood` is acknowledgement only and never counts
# as approval. The factory alone adds this question (package_story.compose),
# only to an architecture package whose handoff asks for his approval
# ("approval": true), and the gate holds it there. One answer for the whole
# design: approve, or request changes with his note (which may name
# declarations or members). His answer is recorded with the checked canonical
# revision it was given to (`subject_of`); once a newer revision of the same
# document is checked, or the kit changes, it is stale and counts for nothing.

APPROVAL_ID, APPROVE, REQUEST_CHANGES = package_story.APPROVAL_QUESTION, "approve", "request-changes"
_APPROVAL_WORDS = {
    "en": {"short": "Approve the design?",
           "question": "Do you approve this architecture design, as checked?",
           "approve": ("Approve", "You approve this checked revision of the design."),
           "changes": ("Request changes",
                       "Say what to change: you may name declarations or members."),
           "reason": "Approval is of this exact checked revision; a newer one asks again.",
           "say": "Do you approve this design as it stands, or do you want changes?"},
    "es": {"short": "¿Aprueba el diseño?",
           "question": "¿Aprueba este diseño de arquitectura, tal como se verificó?",
           "approve": ("Aprobar", "Aprueba esta revisión verificada del diseño."),
           "changes": ("Pedir cambios",
                       "Diga qué cambiar: puede nombrar declaraciones o miembros."),
           "reason": "La aprobación es de esta revisión verificada; una más nueva pregunta "
                     "otra vez.",
           "say": "¿Aprueba este diseño tal como está, o quiere cambios?"},
}
SUBJECT = ("document_id", "revision", "source_revision", "kit_digest")


def approval_slide(language: str) -> dict:
    """His approval as the slide the factory puts before the vote."""
    w = _APPROVAL_WORDS.get(language, _APPROVAL_WORDS["en"])
    return {"title": w["short"], "waits": APPROVAL_ID, "beats": [{"say": w["say"]}]}


def _approval_question(language: str) -> tuple[str, list[dict], str]:
    w = _APPROVAL_WORDS.get(language, _APPROVAL_WORDS["en"])
    return (w["question"],
            [{"id": APPROVE, "name": w["approve"][0], "says": w["approve"][1]},
             {"id": REQUEST_CHANGES, "name": w["changes"][0], "says": w["changes"][1]}],
            w["reason"])


def subject_of(package: Package) -> Optional[dict]:
    """The checked canonical revision an architecture package puts to him:
    {document_id, revision, source_revision, kit_digest}; None for any
    other package."""
    communication = package.manifest.communication or {}
    if communication.get("kind") != "architecture":
        return None
    return {key: communication.get(key) for key in SUBJECT}


def asks_approval(package: Package) -> bool:
    """Whether this is an architecture package the factory asked his
    approval in (its canonical binding says so; the gate holds the story to
    it)."""
    story = _story_of(package) if subject_of(package) is not None else None
    return story is not None and any(s.waits == APPROVAL_ID for s in story.slides)


# Where make_package keeps a page's answers when its story names none.
STORY_ANSWERS = f"{work_packages.RESULTS}/{RECORD}"
# The runs that can carry a vote.
VOTING_RUNS = (work_packages.PAGE,)


def asks_a_question(deck_data: Optional[dict],
                    story: Optional[package_story.Story]) -> bool:
    """Whether a deck's content or a story asks him at least one question."""
    if isinstance(deck_data, dict) and any(isinstance(d, dict)
                                           for d in deck_data.get("decisions") or []):
        return True
    return story is not None and story.answers is not None and any(
        s.waits for s in story.slides)


def _story_of(package: Package) -> Optional[package_story.Story]:
    try:
        return package.story()
    except PackageError:
        return None


def asks_him(package: Package) -> bool:
    """`asks_a_question` for an unpacked package: its deck's decisions (a
    decision package only) or its story's waiting slides."""
    return bool(questions(package)) or asks_a_question(None, _story_of(package))


def _story_questions(package: Package) -> list[Question]:
    """A package with no deck: the questions its story asks in the package
    view's head, in its slides' order, each with whether it applies yet
    (`asks_when`) and the answer its answers file holds: the questions it
    declares (package_story) and the standard `understood`; [] when it has
    none. A question a slide waits on that the story does not declare is
    its own page's, never answered here."""
    story = _story_of(package)
    if story is None or story.answers is None:
        return []
    record = _record(package, story.answers.split("/", 1)[1])
    asked = []
    for slide in story.slides:
        declared = story.declared(slide.waits) if slide.waits else None
        if declared is not None:
            options = [{"id": o.id, "name": o.name, "says": o.says} for o in declared.options]
            asked.append(Question(declared.id, declared.question, declared.context, options,
                                  declared.recommended, declared.reason,
                                  story.applies(slide, record),
                                  _story_answer(record.get(declared.id), options,
                                                declared.recommended)))
        elif slide.waits == UNDERSTOOD_ID:
            vote = understood(story.language)
            options = [{k: o[k] for k in ("id", "name", "says")} for o in vote["options"]]
            asked.append(Question(UNDERSTOOD_ID, vote["question"], "", options, GOT_IT,
                                  vote["reason"], True,
                                  _story_answer(record.get(UNDERSTOOD_ID), options, GOT_IT)))
        elif slide.waits == APPROVAL_ID and subject_of(package) is not None:
            question, options, reason = _approval_question(story.language)
            answer = _story_answer(record.get(APPROVAL_ID), options, APPROVE)
            stale = False
            if answer is not None:
                given = record[APPROVAL_ID].get("subject")
                answer["subject"] = given if isinstance(given, dict) else None
                stale = not (given == subject_of(package) and current_subject(given))
            asked.append(Question(APPROVAL_ID, question, "", options, "", reason, True,
                                  answer, stale))
    return asked


def current_subject(subject) -> bool:
    """Whether a checked canonical revision is still the one in force: the
    newest checked revision of its document, under the kit in force. False
    when it cannot be told (fail closed: a stale approval satisfies
    nothing)."""
    from jarvis import language_projection
    return language_projection.current_subject(subject)


def _story_answer(entry, options: list[dict], recommended: str) -> Optional[dict]:
    """An answer the story's answers file holds for one of its questions,
    or None when it holds none that question takes."""
    if not isinstance(entry, dict) or entry.get("answer") not in \
            {o["id"] for o in options} | set(SPECIAL):
        return None
    return {"answer": entry["answer"], "choice": _text(entry.get("choice")),
            "note": _text(entry.get("note")), "at": _text(entry.get("at")),
            "recommended": entry["answer"] == recommended}


def record_story_answer(package: Package, question: Question, option: str,
                        note: str) -> None:
    """His answer to a question a story asks in the view's head (one it
    declares, or its `understood`), written into its answers file as
    decide.py writes a deck's (the story waiting on it goes on). Refused
    (PackageError) for a package whose story does not ask it there, or not
    yet (a question that does not apply)."""
    story = _story_of(package)
    asked = next((q for q in _story_questions(package) if q.id == question.id), None)
    if story is None or story.answers is None or asked is None:
        raise PackageError(f"{package.id} does not ask {question.id}.")
    if not asked.applies:
        raise PackageError(f"{question.id} is not asked yet: it follows another answer.")
    if asked.id == APPROVAL_ID and option not in (APPROVE, REQUEST_CHANGES):
        raise PackageError(f"{APPROVAL_ID} takes {APPROVE} or {REQUEST_CHANGES}.")
    if asked.id == APPROVAL_ID and option == REQUEST_CHANGES and not note.strip():
        raise PackageError("Request changes needs his words: say what to change (you may name "
                           "declarations or members).")
    name = story.answers.split("/", 1)[1]
    record = _record(package, name)
    record[asked.id] = {
        "answer": option, "choice": asked.option_name(option), "note": note,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "recommended": option == asked.recommended}
    if asked.id == APPROVAL_ID:
        # What he approved (or asked changes to): this exact checked revision.
        record[asked.id]["subject"] = subject_of(package)
    work_packages.save_result(package, name, (json.dumps(
        record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def with_understood_slide(story: Optional[dict], language: str) -> dict:
    """A page's story, as JSON, with the standard vote at its end in
    `language`: its answers file kept (STORY_ANSWERS when it names none) and
    the vote's slide last. With no story it is a story of that one slide,
    told in `language`."""
    told = dict(story or {"language": language})
    told.setdefault("title", _vote_words(language)["short"])
    told["answers"] = told.get("answers") or STORY_ANSWERS
    told["slides"] = [*(told.get("slides") or []), understood_slide(language)]
    return told


def page_vote(package: Package) -> Optional[dict]:
    """What a page package (not a deck, which asks on its own slide) asks
    in the package view's head: the standard vote, with his answer and
    words (`question` is None when it carries none), and in `asks` every
    question its story declares and the factory's approval of an
    architecture design, in order, with its options, whether it applies yet
    and his answer; None for a package that asks nothing there. His
    approval is `kind` "approval", never the vote: with the revision it puts
    to him, the options that need his words, and whether his answer is
    stale."""
    if is_decision_package(package):
        return None
    asked = _story_questions(package)
    vote = next((q for q in asked if q.id == UNDERSTOOD_ID), None)
    declared = [q for q in asked if q.id != UNDERSTOOD_ID]
    if vote is None and not declared:
        return None
    subject = subject_of(package) or {}

    def ask(q: Question) -> dict:
        shown = {"id": q.id, "question": q.question, "context": q.context,
                 "options": q.options, "recommended": q.recommended, "applies": q.applies,
                 "answer": q.answer["answer"] if q.answer else None,
                 "note": q.answer["note"] if q.answer else ""}
        if q.id == APPROVAL_ID:
            shown.update(kind="approval", revision=subject.get("revision"),
                         note_required=[REQUEST_CHANGES], stale=q.stale,
                         answered_revision=(q.answer.get("subject") or {}).get("revision")
                         if q.answer else None)
        return shown
    return {"question": vote.id if vote else None,
            "answer": vote.answer["answer"] if vote and vote.answer else None,
            "note": vote.answer["note"] if vote and vote.answer else "",
            "asks": [ask(q) for q in declared]}


def no_vote(work_id: str, runs: str) -> str:
    """The refusal: a package for a Work item that does not carry his vote."""
    if runs not in VOTING_RUNS:
        article = "an" if runs[:1] in "aeiou" else "a"
        return (f"It names work item {work_id}, but {article} {runs} package cannot carry "
                f"Tim's vote, "
                f"and every package for a Work item must. Send it without --work, or send "
                f"what he should answer as a deck or a page package, which carry the vote. "
                f"Nothing was delivered.")
    return (f"It names work item {work_id} but asks Tim nothing, and every package must carry "
            f"his vote. Make it with python -m jarvis.make_package, which adds the standard "
            f"vote (Got it / I have questions) to a deck or page that asks nothing. Nothing "
            f"was delivered.")


def summary(package: Package) -> str:
    """results/decisions.md as decide.py and the page write it, or ""."""
    raw = work_packages.read_result(package, SUMMARY)
    return raw.decode("utf-8", errors="replace") if raw else ""


# ── the record ────────────────────────────────────────────────────────────
#
# One arrival is one document, `/packages/<inbox id>.package` (the id
# through records.segment): its fields under the names its columns had, and
# the changes this version records (`events`: kind, the revision it moved
# to, when and how; no words of the package). Its changes are the
# document's versions. Kept with it: `last_seq` (its events' own order) and,
# for a row the move brought, its SQLite rowid (`rowid`). Every arrival also
# writes `ARRIVALS`, so two arrivals never decide "the same bytes" or "a
# newer one of the same name" from the same reading.

ROOT, TYPE = "/packages", "package"
ARRIVALS = f"{ROOT}/arrivals.inbox"
COLUMNS = tuple(f.name for f in dataclasses.fields(Entry))
EVENT_COLUMNS = ("seq", "inbox_id", "kind", "revision", "at", "how")
TABLES = ("package_inbox", "package_inbox_events")


def path_of(inbox_id: str) -> str:
    return f"{ROOT}/{records.segment(inbox_id)}.{TYPE}"


def _is_entry(data) -> bool:
    return isinstance(data, dict) and "sha256" in data and "package_id" in data


def _entry(doc: dict) -> Entry:
    return Entry(**{k: doc.get(k) for k in COLUMNS})


def _caller(how: str, session: str = "") -> "records.Caller":
    """Who made a change: him (by voice, on the page), the sending agent
    when its session is proven, else JARVIS."""
    if how in (BY_VOICE, ON_THE_PAGE):
        return records.TIM
    if how == BY_THE_SENDER and session:
        return records.by(f"agent:{session}")
    return records.JARVIS


def _ref(value, what: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > REF_MAX:
        raise InboxRefused(f"{what} is longer than {REF_MAX} characters.")
    return text


def _event(doc: dict, kind: str, revision: int, now: float, how: str) -> None:
    doc.setdefault(record_cells.EVENTS, []).append(
        {"seq": record_cells.next_seq(doc), "kind": kind, "revision": revision, "at": now,
         "how": how})


def _change(unit, doc: dict, kind: str, how: str, now: float, **fields) -> Entry:
    """Move one entry on: its fields, its next revision and one event,
    staged in the caller's unit."""
    doc.update(fields)
    doc["revision"] = doc["revision"] + 1
    _event(doc, kind, doc["revision"], now, how)
    record_cells.stage(unit, path_of(doc["id"]), doc, change=kind)
    return _entry(doc)


def _scan(unit=None, states: Optional[tuple] = None, **where) -> list[dict]:
    """The entries whose fields equal `where` (exactly: the engine's text
    match ignores case) and, given `states`, whose state is one of them,
    read through `unit` when one is given. Every condition is the engine's
    to answer from its index, so only the entries asked for are read."""
    query = [[k, "=", v] for k, v in where.items()]
    if states is not None:
        query.append(["state", "in", list(states)])
    found = (unit.scan if unit is not None else records.scan)(ROOT, types=[TYPE],
                                                               where=query or None)
    return [r.data for r in found if _is_entry(r.data)
            and all(r.data.get(k) == v for k, v in where.items())
            and (states is None or r.data.get("state") in states)]


def _open_ones(unit=None, **where) -> list[dict]:
    return _scan(unit, OPEN_STATES, **where)


def _take(unit, inbox_id: str) -> dict:
    doc = record_cells.take(unit, path_of(str(inbox_id)))
    if not _is_entry(doc):
        raise InboxRefused("There is no package by that id in the inbox; package_inbox lists "
                           "them.")
    return doc


def _still_open(doc: dict, already: str) -> None:
    """Refused for an entry already closed, saying why it closed."""
    entry = _entry(doc)
    if not entry.is_open:
        raise InboxRefused(f"{entry.id} is {already}closed ({entry.closed_why}).")


def arrive(package: Package, sha256: str, sender: Sender, *, workflow_id: str = "",
           work_id: str = "", now: Optional[float] = None) -> Arrival:
    """Record a package that has just been unpacked here, unshown. The same
    bytes while their entry is still open are that entry (fresh False). Any
    open package of the same name from the same sender is closed as
    superseded, in the same write."""
    now = time.time() if now is None else now
    sender = Sender(_ref(sender.project, "The project"), _ref(sender.session, "The session"),
                    _ref(sender.agent, "The agent's name"), _ref(sender.claimed, "The session"),
                    _ref(sender.evidence, "The evidence"))
    answered, total = counts(questions(package))
    m = package.manifest
    title = title_of(package)
    inbox_id = "pkg-" + secrets.token_hex(6)

    def write(unit) -> Arrival:
        same = sorted(_open_ones(unit, sha256=sha256), key=lambda d: d["arrived_at"],
                      reverse=True)
        if same:
            return Arrival(_entry(same[0]), False), wait_links.Settled()
        arrivals = unit.get(ARRIVALS)
        unit.put(ARRIVALS, {"arrivals": int((arrivals.data if arrivals else {}).get(
            "arrivals") or 0) + 1, "last": inbox_id})
        column, value = {"session": ("from_session", sender.session),
                         "project": ("from_project", sender.project)}[sender.key()[0]]
        older = [record_cells.take(unit, path_of(d["id"]))
                 for d in _open_ones(unit, name=m.name, **{column: value})] if value else []
        if unit.get(path_of(inbox_id)) is not None:
            raise InboxRefused("That inbox id is taken; try again.")
        # What waited on an older version waits on this one, and this one is
        # for its Work item when it names none (wait_links.carry).
        moved = wait_links.Settled()
        for d in older:
            moved.add(wait_links.carry(unit, wait_links.replaced(
                _refs(unit, d["package_id"]), inbox_id, now), now))
        carried_work = next((d["work_id"] for d in older if d["work_id"]), None)
        superseded = [_change(unit, d, "closed", BY_THE_SENDER, now, state=CLOSED,
                              closed_at=now, closed_why=SUPERSEDED, superseded_by=inbox_id)
                      for d in older]
        for e in superseded:
            moved.add(_close_vote(unit, e, SUPERSEDED, "asker", now))
        doc = {"id": inbox_id, "package_id": package.id, "sha256": sha256, "name": m.name,
               "version": m.version, "title": title, "purpose": m.purpose, "runs": m.runs,
               "decisions_total": total, "decisions_answered": answered,
               "from_project": sender.project, "from_session": sender.session,
               "from_agent": sender.agent, "workflow_id": workflow_id or None,
               "work_id": work_id or carried_work or None, "project_id": None,
               "state": ARRIVED,
               "revision": 1, "capture_id": None, "return_asked_at": None,
               "message_id": None, "arrived_at": now, "opened_at": None,
               "answered_at": None, "returned_at": None, "closed_at": None,
               "closed_why": None, "signed_off_at": None,
               "claimed_session": sender.claimed, "sender_evidence": sender.evidence,
               # Nothing proves a claim later: a package's sender is its
               # signature's, as it arrives (server._package_signer).
               "reprove": 0, record_cells.EVENTS: [], "last_seq": 0}
        _event(doc, "arrived", 1, now, BY_THE_SENDER)
        record_cells.stage(unit, path_of(inbox_id), doc)
        return Arrival(_entry(doc), True, superseded), moved

    arrival, moved = records.atomic(write, by=_caller(BY_THE_SENDER, sender.session))
    wait_links.tell(moved)
    return arrival


# The events of the one-time re-proof of a row's claimed session from the
# transcripts, which agent keys replaced: kept so old rows read as they did.
SENDER_PROVEN, SENDER_NOT_PROVEN = "sender_proven", "sender_not_proven"


def get(inbox_id: str) -> Optional[Entry]:
    rec = records.get(path_of(str(inbox_id)))
    return _entry(rec.data) if rec is not None and _is_entry(rec.data) else None


def listing(closed: bool = False, limit: int = LIST_MAX) -> list[Entry]:
    """The open packages, newest first; with `closed`, the closed ones,
    most recently closed first."""
    limit = max(1, min(limit, LIST_MAX))
    if closed:
        found = sorted(_scan(state=CLOSED), key=lambda d: d["closed_at"] or 0.0, reverse=True)
    else:
        found = sorted(_open_ones(), key=lambda d: d["arrived_at"], reverse=True)
    return [_entry(d) for d in found[:limit]]


def named(reference: str) -> list[Entry]:
    """Every entry, open or closed, that `reference` names exactly as a
    wait names a package: its inbox id, its package's id, its name or its
    name-version. Each is one indexed lookup, never a scan of the inbox
    (the wait links ask this for every held link). Open ones first, newest
    first, then the closed ones, most recently closed first."""
    reference = str(reference or "")
    if not reference:
        return []
    found: dict[str, dict] = {}
    rec = records.get(path_of(reference))
    if rec is not None and _is_entry(rec.data):
        found[rec.data["id"]] = rec.data
    looks = [{"package_id": reference}, {"name": reference}]
    looks += [{"name": reference[:i], "version": reference[i + 1:]}
              for i, c in enumerate(reference) if c == "-" and 0 < i < len(reference) - 1]
    for where in looks:
        for d in _scan(**where):
            found.setdefault(d["id"], d)
    rows = list(found.values())
    open_ = sorted((d for d in rows if d["state"] in OPEN_STATES), key=lambda d: d["arrived_at"],
                   reverse=True)
    shut = sorted((d for d in rows if d["state"] not in OPEN_STATES),
                  key=lambda d: d["closed_at"] or 0.0, reverse=True)
    return [_entry(d) for d in open_ + shut]


def closed_count() -> int:
    return len(_scan(state=CLOSED))


def for_package(package_id: str) -> list[Entry]:
    """The open entries that are this unpacked package."""
    return [_entry(d) for d in sorted(_open_ones(package_id=package_id),
                                      key=lambda d: d["arrived_at"])]


def for_work(work_id: str) -> list[Entry]:
    """The open packages sent for this work item, oldest first."""
    return [_entry(d) for d in sorted(_open_ones(work_id=work_id),
                                      key=lambda d: d["arrived_at"])]


def waiting_by_work() -> dict[str, int]:
    """For each work item, how many questions its open packages still wait
    on him for, from the counts the entries keep (no file is read)."""
    waiting: dict[str, int] = {}
    for d in _open_ones():
        if d["work_id"] is not None:
            waiting[d["work_id"]] = waiting.get(d["work_id"], 0) + max(
                d["decisions_total"] - d["decisions_answered"], 0)
    return {k: v for k, v in waiting.items() if v}


def find(reference: str) -> list[Entry]:
    """The open packages `reference` names: an inbox id, its package's id,
    its name, or words of its title or its sender ("the tea one", "from
    chitauri"), best fit first; every open one for no words."""
    reference = " ".join(str(reference or "").split())
    rows = listing()
    if not reference:
        return rows
    exact = [e for e in rows if reference in (e.id, e.package_id, e.name)]
    if exact:
        return exact
    from jarvis import spoken_words
    words = set(spoken_words.folded_words(reference)) - {"the", "package", "one", "from",
                                                          "el", "la", "paquete", "de"}
    scored = []
    for e in rows:
        have = set(spoken_words.folded_words(f"{e.title} {e.name} {e.sender_name()}"))
        score = len(words & have)
        if score:
            scored.append((score, e))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [e for s, e in scored if s == best]


# ── moving one on ────────────────────────────────────────────────────────

def opened(inbox_id: str, how: str, now: Optional[float] = None) -> Entry:
    """He has been shown it (or JARVIS has it in hand, for a package that is
    not a page): an arrived one is opened. Every open is an event."""
    now = time.time() if now is None else now

    def write(unit) -> Entry:
        doc = _take(unit, inbox_id)
        _still_open(doc, "")
        fields = {"opened_at": doc["opened_at"] or now}
        if doc["state"] == ARRIVED:
            fields["state"] = OPENED
        return _change(unit, doc, "opened", how, now, **fields)

    return records.atomic(write, by=_caller(how))


def counted(inbox_id: str, answered: int, total: int, how: str,
            now: Optional[float] = None) -> Entry:
    """The deck's record has changed: its counts, and with them its state
    (answering while some wait, answered once none do). A returned or
    closed one keeps its state; an unchanged count changes nothing."""
    now = time.time() if now is None else now

    def write(unit) -> Entry:
        doc = _take(unit, inbox_id)
        if (doc["decisions_answered"], doc["decisions_total"]) == (answered, total):
            return _entry(doc)
        fields: dict = {"decisions_answered": answered, "decisions_total": total}
        if doc["state"] in (ARRIVED, OPENED, ANSWERING, ANSWERED) and total:
            if answered >= total:
                state = ANSWERED
            elif answered:
                state = ANSWERING
            else:
                state = ARRIVED if doc["state"] == ARRIVED else OPENED
            fields["state"] = state
            if state == ANSWERED:
                fields["answered_at"] = now
        return _change(unit, doc, "answered" if fields.get("state") == ANSWERED else "counted",
                       how, now, **fields)

    # His answers, counted: written with full sync.
    return records.atomic(write, by=_caller(how), full_sync=True)


def signed_off(inbox_id: str, capture_id: Optional[int], how: str,
               now: Optional[float] = None) -> Entry:
    """He has signed it off: done with it, whatever is answered so far. Its
    sign-off and its time are recorded and it is closed, `signed_off`. With
    a capture (the message to its sender, which goes only on his yes), that
    message is followed (`follow_messages`)."""
    now = time.time() if now is None else now

    def write(unit) -> tuple[Entry, "wait_links.Settled"]:
        doc = _take(unit, inbox_id)
        _still_open(doc, "already ")
        entry = _change(unit, doc, "signed_off", how, now, state=CLOSED, closed_at=now,
                        closed_why=SIGNED_OFF, signed_off_at=now, capture_id=capture_id,
                        return_asked_at=now)
        # Everything waiting on the package ends with his sign-off, in this
        # write (wait_links).
        return entry, _ended(unit, entry, SIGNED_OFF, "user", now)

    entry, moved = records.atomic(write, by=_caller(how), full_sync=True)
    wait_links.tell(moved)
    return entry


def _actor(how: str) -> str:
    """Whose act a change was, as a wait it ends records it."""
    return "user" if how in (BY_VOICE, ON_THE_PAGE) else "jarvis"


def _ended(unit, entry: Entry, why: str, actor: str, now: float) -> "wait_links.Settled":
    """It is closed, signed off or set aside (`why`): every wait on it ends
    (wait_links.settle) and its Work item's vote closes, in the caller's
    unit (the close's own write)."""
    moved = wait_links.settle(unit, wait_links.answer(
        wait_links.PACKAGE, _refs(unit, entry.package_id), None, why, now, actor), now)
    if why != SIGNED_OFF:
        # Signed off, its vote is answered or closed by the sign-off itself
        # (server._inbox_sign_off_work), which knows his answer.
        moved.add(_close_vote(unit, entry, why, actor, now))
    return moved


def vote_key(inbox_id: str) -> str:
    """The Work decision a package's vote is asked as on its item."""
    return f"{UNDERSTOOD_ID}-{inbox_id}"


def _close_vote(unit, entry: Entry, why: str, actor: str, now: float) -> "wait_links.Settled":
    """Its Work item's vote on it (`understood-<inbox id>`), still open, is
    closed with it: set aside or replaced, never answered."""
    if not entry.work_id:
        return wait_links.Settled()
    from jarvis import work_store
    return work_store.close_with_package(unit, entry.work_id, vote_key(entry.id), why, now,
                                         actor)


def replacement(entry: Entry) -> Optional[Entry]:
    """The newest version that replaced a superseded entry, following each
    replacement in turn: `superseded_by`, or, for one replaced before that
    was kept, the first entry of the same name from the same sender that
    arrived as it closed. None for an entry not replaced, or not found."""
    seen = {entry.id}
    doc = records.get(path_of(entry.id))
    current = doc.data if doc is not None and _is_entry(doc.data) else None
    while current is not None and current["closed_why"] == SUPERSEDED:
        nxt = current.get("superseded_by")
        if not nxt:
            column = "from_session" if current["from_session"] else "from_project"
            later = sorted((d for d in _scan(name=current["name"],
                                             **{column: current[column]})
                            if d["arrived_at"] >= (current["closed_at"] or 0) - 1
                            and d["id"] != current["id"]),
                           key=lambda d: d["arrived_at"])
            nxt = later[0]["id"] if later else None
        if not nxt or nxt in seen:
            return None
        seen.add(nxt)
        rec = records.get(path_of(nxt))
        current = rec.data if rec is not None and _is_entry(rec.data) else None
    return _entry(current) if current is not None and current["id"] != entry.id else None


def settle_closed() -> "wait_links.Settled":
    """At start: the vote of every package already set aside or replaced
    that its Work item still asks is closed, as it would have been then
    (waits on the packages themselves are wait_links.sweep's)."""
    closed = [d for d in _scan(state=CLOSED)
              if d["closed_why"] in (DISMISSED, SUPERSEDED) and d["work_id"]]
    if not closed:
        return wait_links.Settled()

    def write(unit) -> "wait_links.Settled":
        moved = wait_links.Settled()
        for d in closed:
            actor = "asker" if d["closed_why"] == SUPERSEDED else "user"
            moved.add(_close_vote(unit, _entry(d), d["closed_why"], actor,
                                  d["closed_at"] or time.time()))
        return moved

    moved = records.atomic(write, by=records.JARVIS, change="decision_signed_off",
                           summary="Votes on packages already closed are closed.")
    wait_links.tell(moved)
    return moved


def _refs(unit, package_id: str) -> set:
    """Every name a wait may give the package by: its id, and each of its
    entries' inbox id, name and name-version (read through `unit` when one
    is given)."""
    found = {package_id}
    for r in _scan(unit, package_id=package_id):
        found |= {r["id"], r["name"], f"{r['name']}-{r['version']}"}
    return found


def refs(package_id: str) -> set:
    """The names a wait may give a package by (`_refs`)."""
    return _refs(None, package_id)


def adopt(package: Package, sha256: str, how: str) -> Entry:
    """A record for a package with no open entry, so it is signed off the
    same way, opened, since he has it in front of him: one unpacked here
    before the inbox or outside it arrived from no one known; one signed off
    before and answered since keeps its last entry's sender, workflow and
    work item, so what he answers now still goes where it was sent for."""
    last = latest_for_package(package.id)
    sender = Sender(last.from_project, last.from_session, last.from_agent) if last else Sender()
    entry = arrive(package, sha256, sender, workflow_id=last.workflow_id or "" if last else "",
                   work_id=last.work_id or "" if last else "").entry
    return opened(entry.id, how)


def latest_for_package(package_id: str) -> Optional[Entry]:
    """The newest entry of this unpacked package, open or closed, or None."""
    found = sorted(_scan(package_id=package_id), key=lambda d: d["arrived_at"])
    return _entry(found[-1]) if found else None


def rows_for_package(package_id: str) -> list[Entry]:
    """Every entry this unpacked package has had, open or closed."""
    return [_entry(d) for d in sorted(_scan(package_id=package_id),
                                      key=lambda d: d["arrived_at"])]


def for_review(package_id: str) -> list[Entry]:
    """The entries his answers in this package count for: its open entries,
    else its newest entry once he has signed it off (he may answer in it
    after pressing Done), never one he set aside or that was replaced."""
    rows = for_package(package_id)
    if rows:
        return rows
    last = latest_for_package(package_id)
    return [last] if last is not None and last.closed_why == SIGNED_OFF else []


# A message's statuses once it has gone to its session (run_store.MessageStatus).
_GONE = ("sent", "undelivered", "delivered", "working", "answered")
# How many of a session's newest messages are looked through for one.


def _carried_by(entry: Entry) -> Optional[int]:
    """The message that carried a signed-off entry's answers back to its
    sender, once it has gone: the newest of its session's messages from
    that capture since he signed it off."""
    from jarvis import run_store
    return run_store.last_message_gone(entry.capture_id, entry.from_session,
                                       entry.return_asked_at or 0, _GONE)


def follow_messages(now: Optional[float] = None) -> list[Entry]:
    """Bring the signed-off entries up to date with the messages that carry
    their answers back, from the messages record: an entry whose capture
    has gone to its sender since he signed it off is given that message's
    id. The entries that moved."""
    now = time.time() if now is None else now
    waiting = [d for d in _scan(closed_why=SIGNED_OFF)
               if d["capture_id"] is not None and d["message_id"] is None]
    carried = {}
    for d in waiting:
        message_id = _carried_by(_entry(d))
        if message_id is not None:
            carried[d["id"]] = (d["capture_id"], message_id)
    if not carried:
        return []

    def write(unit) -> list[Entry]:
        moved = []
        for inbox_id, (capture_id, message_id) in carried.items():
            doc = _take(unit, inbox_id)
            if doc["capture_id"] == capture_id and doc["message_id"] is None:
                moved.append(_change(unit, doc, "returned", BY_JARVIS, now, returned_at=now,
                                     message_id=message_id))
        return moved

    return records.atomic(write, by=records.JARVIS)


def close(inbox_id: str, how: str, now: Optional[float] = None) -> Entry:
    """He sets it aside without signing it off: dismissed, whatever is
    answered so far kept in its record and nothing sent back. Every wait on
    it ends, and its Work item's vote closes, in the same write."""
    now = time.time() if now is None else now

    def write(unit) -> tuple[Entry, "wait_links.Settled"]:
        doc = _take(unit, inbox_id)
        _still_open(doc, "already ")
        entry = _change(unit, doc, "closed", how, now, state=CLOSED, closed_at=now,
                        closed_why=DISMISSED)
        # Nothing waits on a package he set aside, in this write (wait_links).
        return entry, _ended(unit, entry, DISMISSED, _actor(how), now)

    entry, moved = records.atomic(write, by=_caller(how), full_sync=True)
    wait_links.tell(moved)
    return entry


def events(inbox_id: str) -> list[dict]:
    """The entry's changes, oldest first, as the table's rows held them."""
    found = [e for v in records.history(path_of(str(inbox_id)), fields=[record_cells.EVENTS])
             for e in (v.data or {}).get(record_cells.EVENTS) or []]
    return [{"seq": e["seq"], "inbox_id": str(inbox_id), "kind": e["kind"],
             "revision": e["revision"], "at": e["at"], "how": e["how"]}
            for e in sorted(found, key=lambda e: e["seq"])]


# ── the move from SQLite (knowledge_move) ───────────────────────────────

class _InboxMapping:
    """package_inbox and package_inbox_events as one document per arrival,
    its changes as the document's versions (each `{id, events: [one]}`: the
    entry as it stood then was never kept)."""
    name = "package"
    tables = TABLES
    under = (ROOT,)
    history = True

    def documents(self, conn):
        from jarvis.knowledge_move import Doc, Revision
        by_entry: dict[str, list] = {}
        for r in record_cells.table_rows(conn, "package_inbox_events"):
            by_entry.setdefault(r.cells["inbox_id"], []).append(r)
        for row in record_cells.table_rows(conn, "package_inbox"):
            doc = {k: row.cells[k] for k in COLUMNS}
            doc.update({"rowid": row.rowid, record_cells.EVENTS: [], "last_seq": 0})
            history = []
            for r in sorted(by_entry.get(doc["id"], []), key=lambda r: r.cells["seq"]):
                e = {c: r.cells[c] for c in EVENT_COLUMNS if c != "inbox_id"}
                doc["last_seq"] = max(doc["last_seq"], e["seq"])
                history.append(Revision({"id": doc["id"], record_cells.EVENTS: [e]},
                                        by=_caller(e["how"], doc["from_session"]),
                                        change=e["kind"]))
            yield Doc(path_of(doc["id"]), doc, history=tuple(history))

    def rows(self, docs):
        from jarvis.knowledge_move import Row
        entries, logged = [], []
        for d in docs:
            if not _is_entry(d.data):
                continue
            entries.append(Row({k: d.data.get(k) for k in COLUMNS}, d.data.get("rowid")))
            logged += [{**e, "inbox_id": d.data["id"]} for e in record_cells.history_events(d)]
        record_cells.resolve_seqs(logged)
        return {"package_inbox": entries,
                "package_inbox_events": [Row({c: e.get(c) for c in EVENT_COLUMNS})
                                         for e in logged]}


MAPPINGS = [_InboxMapping()]
