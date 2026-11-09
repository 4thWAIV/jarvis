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

from jarvis import spoken_words

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
class Story:
    title: str
    language: str
    slides: tuple[Slide, ...]
    answers: Optional[str] = None               # results/<name>.json

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
    return Story(_text(body.get("title"), "The story's title", MAX_TITLE_CHARS), language,
                 tuple(slides), answers)
