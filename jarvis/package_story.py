"""A page package's story: what JARVIS says over the page, beat by beat, and
what it points at while each beat is heard. The format is in
docs/work-packages.md ("Telling a page's story").

    parse    a story file's bytes into a Story, or StoryError with the reason
             in words he can hear; work_packages checks it on unpack like the
             rest of the manifest
    cues     one slide's beats as (words, cue) pairs for the speech
             scheduler's `narrate`: the cue rides on each of the beat's audio
             frames, and the page hands it to the package's frame (the helper,
             package_story.js) when those words start playing

A story may ask for answers: `answers` names its record, a JSON object in
the package's results/ keyed by question id (a decision deck's
results/decisions.json, which the page's Choose and scripts/decide.py both
write), and a slide that `waits` for one question is followed by nothing
until that question has an answer there (story_teller.py). `asks_when`
makes a slide's question apply only once another question has one of the
given answers; a question that does not apply is passed over.

A story may also declare its questions (`questions`: each its id, wording
and options). A declared question is answered in the package view's head,
outside the frame, through the inbox's answer route (package_inbox:
`_story_questions`, `page_vote`, `record_story_answer`); a question a slide
waits on that the story does not declare is the page's own, answered inside
it, as before.

An architecture package's author may write a story beside its
UNDERSTANDING.md. `authored` reads it strictly (no duplicate keys, no field
the format does not have, nothing the factory owns: its answers file or the
standard vote), and `compose` is the one composition of it, or of none,
with the standard vote as its one last slide: the factory writes it and the
receiver's gate (language_projection.gate) compares the package's story with
it byte for byte.

Only the shape is checked here. A selector is a string the frame's helper
gives to querySelectorAll inside its own sandboxed page; one that matches
nothing, or is not a selector at all, is reported back by the frame and
skipped, never run as anything else.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from jarvis import decision_package as _dp, spoken_words

# The largest story file read, and how much of one is taken.
STORY_MAX_BYTES = 256 * 1024
MAX_SLIDES = 60
MAX_BEATS = 40            # in one slide
MAX_SAY_CHARS = 800       # one beat's words
MAX_TARGETS = 12          # in one beat
MAX_ACTIONS = 6           # in one beat
MAX_SELECTOR_CHARS = 400
MAX_TITLE_CHARS = 200
# What a beat may do to the page before its targets are pointed at:
# `show` scrolls an element into view, `click` clicks one (a detail view's
# opener or its close button).
ACTIONS = ("show", "click")
LANGUAGES = (spoken_words.ENGLISH, spoken_words.SPANISH)
# A question's id, and where a story's answers are kept: one JSON file in
# the package's results folder.
QUESTION_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
ANSWERS = re.compile(r"results/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.json")
# The helper the frame loads: one copy, jarvis/package_story.js, served at
# this address by packages_api.py.
HELPER_PATH = "/api/packages/story.js"
# Questions a story may declare, and the options one may offer.
MAX_QUESTIONS = 20
MAX_OPTIONS = 6
QUESTION_FIELDS = ("id", "question", "options", "recommended", "reason", "context")
OPTION_FIELDS = ("id", "name", "says")
# Ids no story may declare: the standard vote and his approval of an
# architecture design, which the factory alone adds (package_inbox
# UNDERSTOOD_ID, APPROVAL_ID), and his review of a Work item
# (work_api.REVIEW_QUESTION), which an ordinary question must never become.
# His approval of an architecture design: package_inbox owns its words and
# answers; named here so the receiver (work_packages) can hold it to
# architecture packages without reading the inbox.
APPROVAL_QUESTION = "approve-architecture"
RESERVED_QUESTIONS = ("understood", "approve-item", APPROVAL_QUESTION)
# What an author's story (beside an architecture package's UNDERSTANDING.md)
# may hold, at each level, and its size.
AUTHORED_MAX_BYTES = 128 * 1024
AUTHORED_FIELDS = ("title", "language", "slides", "questions")
SLIDE_FIELDS = ("title", "target", "beats", "waits", "asks_when")
BEAT_FIELDS = ("say", "targets", "actions")


def encode(story: dict) -> bytes:
    """The existing factory's JSON encoding for a generated story."""
    return (json.dumps(story, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


class StoryError(ValueError):
    """Why a story file cannot be told, in words he can hear."""


@dataclass(frozen=True)
class Beat:
    say: str
    targets: tuple[str, ...]
    actions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Slide:
    title: str
    target: Optional[str]      # the slide's own element: going to it shows this
    beats: tuple[Beat, ...]
    waits: Optional[str] = None                 # the question this slide asks
    # (question, answers): it applies only once that question has one of them
    asks_when: Optional[tuple[str, tuple[str, ...]]] = None


@dataclass(frozen=True)
class Option:
    id: str
    name: str
    says: str


@dataclass(frozen=True)
class Declared:
    """A question the story declares, answered in the package view's head."""
    id: str
    question: str
    options: tuple[Option, ...]
    recommended: str = ""
    reason: str = ""
    context: str = ""


@dataclass(frozen=True)
class Story:
    title: str
    language: str
    slides: tuple[Slide, ...]
    answers: Optional[str] = None               # results/<name>.json
    questions: tuple[Declared, ...] = ()        # answered in the view's head

    def declared(self, question: str) -> Optional[Declared]:
        return next((q for q in self.questions if q.id == question), None)

    def asking(self, question: str) -> Optional[int]:
        """The slide (from 1) that asks `question`, or None."""
        return next((n for n, s in enumerate(self.slides, 1) if s.waits == question), None)

    def applies(self, slide: Slide, record: dict, seen: frozenset = frozenset()) -> bool:
        """Whether a slide's question is asked, given the answers recorded:
        only once the question it depends on has one of its answers, and
        that question applies too."""
        if slide.asks_when is None:
            return True
        question, answers = slide.asks_when
        entry = record.get(question)
        if not isinstance(entry, dict) or entry.get("answer") not in answers:
            return False
        parent = self.asking(question)
        if parent is None or question in seen:
            return True
        return self.applies(self.slides[parent - 1], record, seen | {question})

    def open_questions(self, record: dict) -> list[int]:
        """The slides whose question applies and has no answer, in order."""
        return [n for n, s in enumerate(self.slides, 1)
                if s.waits and not isinstance(record.get(s.waits), dict)
                and self.applies(s, record)]

    def cues(self, package_id: str, number: int) -> list[tuple[str, dict]]:
        """Slide `number` (from 1) as (words, cue) pairs. Every beat first
        shows its slide, so a page he scrolled away is brought back, then
        runs its own actions; its targets are what is pointed at."""
        slide = self.slides[number - 1]
        go = [{"show": slide.target}] if slide.target else []
        return [(beat.say, {"package": package_id, "beat": f"{number}.{n}", "slide": number,
                            "targets": list(beat.targets),
                            "actions": go + [{kind: sel} for kind, sel in beat.actions]})
                for n, beat in enumerate(slide.beats, 1)]

    def outline(self) -> dict:
        """What the page shows of it: its title and its slides' titles."""
        return {"title": self.title, "language": self.language,
                "slides": [s.title for s in self.slides],
                "asks": [n for n, s in enumerate(self.slides, 1) if s.waits]}


def _text(value, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StoryError(f"{what} is missing.")
    value = value.strip()
    if len(value) > limit:
        raise StoryError(f"{what} is longer than {limit} characters.")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise StoryError(f"{what} has control characters in it.")
    return value


def _selector(value, what: str) -> str:
    selector = _text(value, what, MAX_SELECTOR_CHARS)
    if "\n" in selector:
        raise StoryError(f"{what} is not one line.")
    return selector


def _list(value, what: str, limit: int, required: bool = False) -> list:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        raise StoryError(f"{what} is not a list{' with something in it' if required else ''}.")
    if len(value) > limit:
        raise StoryError(f"{what} has more than {limit} entries.")
    return value


def _beat(raw, where: str) -> Beat:
    if not isinstance(raw, dict):
        raise StoryError(f"{where} is not an object.")
    targets = tuple(_selector(t, f"{where}'s target {n}")
                    for n, t in enumerate(_list(raw.get("targets"), f"{where}'s targets",
                                                MAX_TARGETS), 1))
    actions = []
    for n, action in enumerate(_list(raw.get("actions"), f"{where}'s actions", MAX_ACTIONS), 1):
        if not isinstance(action, dict) or len(action) != 1 or next(iter(action)) not in ACTIONS:
            raise StoryError(f"{where}'s action {n} is not one of "
                             f"{', '.join(f'{{{a!r}: selector}}' for a in ACTIONS)}.")
        kind, selector = next(iter(action.items()))
        actions.append((kind, _selector(selector, f"{where}'s action {n}")))
    return Beat(_text(raw.get("say"), f"{where}'s say", MAX_SAY_CHARS), targets, tuple(actions))


def _question(value, what: str) -> str:
    if not isinstance(value, str) or not QUESTION_ID.fullmatch(value):
        raise StoryError(f"{what} is not a question id (a-z, 0-9 and -).")
    return value


def _asks_when(raw, where: str) -> Optional[tuple[str, tuple[str, ...]]]:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"question", "answers"}:
        raise StoryError(f'{where}\'s asks_when is not {{"question", "answers"}}.')
    answers = _list(raw["answers"], f"{where}'s asks_when answers", 20, required=True)
    return (_question(raw["question"], f"{where}'s asks_when question"),
            tuple(_question(a, f"{where}'s asks_when answer") for a in answers))


def _line(value, what: str, limit: int) -> str:
    text = _text(value, what, limit)
    if "\n" in text:
        raise StoryError(f"{what} is not one line.")
    return text


def _declared(raw, n: int) -> Declared:
    """One question a story declares: {"id", "question", "options": [{"id",
    "name", "says"}], "recommended"?, "reason"?, "context"?}, held to the
    deck's own limits for a question and its options (decision_package)."""
    where = f"Question {n}"
    if not isinstance(raw, dict):
        raise StoryError(f"{where} is not an object.")
    unknown = sorted(set(raw) - set(QUESTION_FIELDS))
    if unknown:
        raise StoryError(f"{where} has {', '.join(unknown)}, which a question does not have.")
    ident = _question(raw.get("id"), f"{where}'s id")
    if ident in RESERVED_QUESTIONS:
        raise StoryError(f"{where}'s id is reserved ({', '.join(RESERVED_QUESTIONS)}): the "
                         f"standard vote is added by the factory, and an ordinary question is "
                         f"never a review.")
    where = f"Question {ident}"
    question = _line(raw.get("question"), f"{where}'s question", _dp.CAPS["question"])
    reason = ("" if raw.get("reason") is None
              else _line(raw["reason"], f"{where}'s reason", _dp.CAPS["reason"]))
    context = ("" if raw.get("context") is None
               else _line(raw["context"], f"{where}'s context", _dp.CAPS["context"]))
    options = []
    for m, o in enumerate(_list(raw.get("options"), f"{where}'s options", MAX_OPTIONS,
                                required=True), 1):
        owhere = f"{where}, option {m}"
        if not isinstance(o, dict) or set(o) - set(OPTION_FIELDS):
            raise StoryError(f'{owhere} is not {{"id", "name", "says"}}.')
        oid = _question(o.get("id"), f"{owhere}'s id")
        if oid in _dp.RESERVED:
            raise StoryError(f"{owhere}'s id may not be {', '.join(_dp.RESERVED)}.")
        options.append(Option(oid, _line(o.get("name"), f"{owhere}'s name",
                                         _dp.CAPS["option name"]),
                              _line(o.get("says"), f"{owhere}'s says",
                                    _dp.CAPS["option says"])))
    if len(options) < 2:
        raise StoryError(f"{where} needs at least two options.")
    if len({o.id for o in options}) != len(options):
        raise StoryError(f"{where} has two options with one id.")
    recommended = raw.get("recommended") or ""
    if recommended and recommended not in {o.id for o in options}:
        raise StoryError(f"{where}'s recommended is not one of its options.")
    return Declared(ident, question, tuple(options), recommended, reason, context)


def parse(raw: bytes) -> Story:
    """A story file: {"title", "language"?, "answers"?, "slides": [{"title",
    "target"?, "waits"?, "asks_when"?, "beats": [{"say", "targets"?,
    "actions"?}]}]}."""
    if len(raw) > STORY_MAX_BYTES:
        raise StoryError(f"The story is over {STORY_MAX_BYTES // 1024} KB.")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise StoryError("The story is not valid JSON.") from None
    if not isinstance(body, dict):
        raise StoryError("The story is not an object.")
    language = body.get("language", spoken_words.ENGLISH)
    if language not in LANGUAGES:
        raise StoryError(f"The story's language is not one of {', '.join(LANGUAGES)}.")
    slides = []
    for n, raw_slide in enumerate(_list(body.get("slides"), "The story's slides", MAX_SLIDES,
                                        required=True), 1):
        where = f"Slide {n}"
        if not isinstance(raw_slide, dict):
            raise StoryError(f"{where} is not an object.")
        target = raw_slide.get("target")
        beats = _list(raw_slide.get("beats"), f"{where}'s beats", MAX_BEATS, required=True)
        waits = raw_slide.get("waits")
        slides.append(Slide(
            _text(raw_slide.get("title"), f"{where}'s title", MAX_TITLE_CHARS),
            None if target is None else _selector(target, f"{where}'s target"),
            tuple(_beat(b, f"{where}, beat {m}") for m, b in enumerate(beats, 1)),
            None if waits is None else _question(waits, f"{where}'s waits"),
            _asks_when(raw_slide.get("asks_when"), where)))
    answers = body.get("answers")
    if answers is not None and (not isinstance(answers, str) or not ANSWERS.fullmatch(answers)):
        raise StoryError("The story's answers is not results/<name>.json.")
    asked = [s.waits for s in slides if s.waits]
    if asked and answers is None:
        raise StoryError("A slide waits for an answer, but the story names no answers file.")
    if len(asked) != len(set(asked)):
        raise StoryError("Two slides wait for the same question.")
    for n, s in enumerate(slides, 1):
        if s.asks_when and s.asks_when[0] not in asked:
            raise StoryError(f"Slide {n} asks only when {s.asks_when[0]} is answered, "
                             f"which no slide asks.")
    declared = tuple(_declared(q, n) for n, q in enumerate(
        _list(body.get("questions"), "The story's questions", MAX_QUESTIONS), 1))
    ids = [q.id for q in declared]
    if len(ids) != len(set(ids)):
        raise StoryError("Two questions have the same id.")
    for q in declared:
        if q.id not in asked:
            raise StoryError(f"Question {q.id} is declared, but no slide waits for it.")
    return Story(_text(body.get("title"), "The story's title", MAX_TITLE_CHARS), language,
                 tuple(slides), answers, declared)


# -- an author's story, and the one composition with the standard vote ----------------

def _unique_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise StoryError("The authored story has one key twice in one object.")
        seen[key] = value
    return seen


def _only(raw, fields: tuple[str, ...], where: str) -> None:
    if isinstance(raw, dict) and set(raw) - set(fields):
        raise StoryError(f"{where} has a field the story format does not have (it has "
                         f"{', '.join(fields)}).")


def authored(raw: bytes) -> dict:
    """An author's story for an architecture package, read strictly: at most
    AUTHORED_MAX_BYTES of UTF-8 JSON with no key twice, only the format's own
    fields, every slide's question declared (answered in the view's head:
    the canonical page asks nothing itself), and nothing the factory owns:
    no `answers` file and no standard vote (`understood`), which `compose`
    adds once. Its slides and questions are checked as any story's once
    composed. The story as a dict, or StoryError saying what to change."""
    if len(raw) > AUTHORED_MAX_BYTES:
        raise StoryError(f"The authored story is over {AUTHORED_MAX_BYTES // 1024} KB.")
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys)
    except StoryError:
        raise
    except (UnicodeDecodeError, ValueError):
        raise StoryError("The authored story is not valid UTF-8 JSON.") from None
    if not isinstance(body, dict):
        raise StoryError("The authored story is not an object.")
    if "answers" in body:
        raise StoryError("The authored story names an answers file; the factory sets it.")
    _only(body, AUTHORED_FIELDS, "The authored story")
    for n, q in enumerate(body.get("questions") or [], 1):
        _only(q, QUESTION_FIELDS, f"Question {n}")
        if isinstance(q, dict) and q.get("id") in RESERVED_QUESTIONS:
            raise StoryError(f"Question {n}'s id is reserved ({', '.join(RESERVED_QUESTIONS)}): "
                             f"the factory adds the standard vote, and an ordinary question is "
                             f"never a review.")
        for m, o in enumerate((q.get("options") or []) if isinstance(q, dict) else [], 1):
            _only(o, OPTION_FIELDS, f"Question {n}, option {m}")
    declared = {q.get("id") for q in body.get("questions") or [] if isinstance(q, dict)}
    for n, slide in enumerate(_list(body.get("slides"), "The story's slides", MAX_SLIDES,
                                    required=True), 1):
        _only(slide, SLIDE_FIELDS, f"Slide {n}")
        if not isinstance(slide, dict):
            continue
        for m, beat in enumerate(slide.get("beats") or [], 1):
            _only(beat, BEAT_FIELDS, f"Slide {n}, beat {m}")
        waits, when = slide.get("waits"), slide.get("asks_when")
        on = when.get("question") if isinstance(when, dict) else None
        if waits in RESERVED_QUESTIONS or on in RESERVED_QUESTIONS:
            raise StoryError(f"Slide {n} uses a reserved question "
                             f"({', '.join(RESERVED_QUESTIONS)}): the factory adds the "
                             f"standard vote once, last.")
        if waits is not None and waits not in declared:
            raise StoryError(f"Slide {n} waits for a question the story does not declare in "
                             f"its questions.")
    return body


def compose(story: Optional[dict], language: str, approval: bool = False) -> bytes:
    """THE composition of a page's story with the standard vote as its one
    last slide, in `language`, encoded: an author's story (`authored`) or
    none. With `approval` (an architecture package whose handoff asks for
    his approval) the factory's approval slide (package_inbox.APPROVAL_ID)
    comes once, just before the vote. Checked as any story is, count and
    size, after composition; the factory writes these bytes and the gate
    compares them. With no story and no approval it is exactly the standard
    vote alone, as every such package before had."""
    from jarvis import package_inbox
    if story is not None:
        if story.get("language", language) != language:
            raise StoryError(f"The story is not in {language}, the language it is told in.")
        story = {**story, "language": language}
    told = package_inbox.with_understood_slide(story, language)
    if approval:
        told["slides"].insert(len(told["slides"]) - 1, package_inbox.approval_slide(language))
    encoded = encode(told)
    parsed = parse(encoded)
    waits = [s.waits for s in parsed.slides]
    if waits.count(package_inbox.UNDERSTOOD_ID) != 1 or waits[-1] != package_inbox.UNDERSTOOD_ID:
        raise StoryError("The composed story must end with the one standard vote.")
    if waits.count(package_inbox.APPROVAL_ID) != (1 if approval else 0):
        raise StoryError("Only the factory asks his approval, once, before the vote.")
    return encoded


def target(declaration: str, member: Optional[str] = None, part: Optional[str] = None) -> str:
    """The selector a beat points with at one declaration of a rendered
    architecture page, or one member of it, or one part of it: each value
    written as a CSS string, a backslash and a double quote escaped with a
    backslash. A value with a line break or other control character is
    refused (StoryError)."""
    def quoted(value: str) -> str:
        if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
            raise StoryError("A target names a declaration, member or part as one line of text.")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    selector = f"[data-declaration={quoted(declaration)}]"
    if part is not None:
        selector += f" [data-part={quoted(part)}]"
    if member is not None:
        selector += f" [data-member={quoted(member)}]"
    return selector
