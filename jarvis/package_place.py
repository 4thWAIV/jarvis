"""Where he is in a page package, exactly: the place Ask keeps and Return
restores (story_teller.py), and Stop keeps.

A place is two halves. The story's half is JARVIS's own: the slide being
told and the beat within it (the first beat whose words were not heard in
full, read from the speech scheduler's own outcomes), what the story was
doing (telling, waiting for his answer, paused, stopped) and whether its
words were sounding when he asked. The page's half is what the package's
frame reported it shows (`View`): the deck slide on screen (its opener,
"#go-3"), where each scrolled part of the page is scrolled to, the part he
selected (a selector, its words and his selection within them) and the
element that had keyboard focus. The frame has no origin and its page is
the sender's, so the view is the sender's words: bounded here, walled
whenever the brain is shown it, and given back to the frame only as the
selectors and numbers it reported.

`revision_of` is what the place was asked about: the bytes of the package
(the SHA-256 of the zip it was unpacked from, as its record keeps it), or
an architecture package's checked revision. Return refuses a place whose
package is no longer that revision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from jarvis import package_story, work_packages
from jarvis.package_story import Story
from jarvis.work_packages import Package

# What a view may hold: scrolled parts, a part's words and his selection.
MAX_SCROLLED = 8
MAX_SCROLL_PX = 10_000_000
MAX_WORDS = 600
MAX_SELECTOR_CHARS = package_story.MAX_SELECTOR_CHARS
# Nested asks kept, the oldest dropped past this.
MAX_ASKS = 8


class ViewError(ValueError):
    """Why a view the page reported is not one."""


@dataclass(frozen=True)
class View:
    """What the frame shows: the sender's page as it reported it."""
    page: Optional[str] = None                   # the deck slide's opener shown
    # (selector of the scrolled element, "" for the page itself, x, y)
    scroll: tuple[tuple[str, int, int], ...] = ()
    part: Optional[str] = None                   # the part he selected
    words: str = ""                              # its words
    selection: Optional[tuple[int, int]] = None  # his selection within them
    selected: str = ""                           # the words he selected
    focused: Optional[str] = None                # what had keyboard focus

    def as_dict(self) -> dict:
        return {"page": self.page, "scroll": [{"at": s, "x": x, "y": y} for s, x, y in self.scroll],
                "part": self.part, "words": self.words,
                "selection": list(self.selection) if self.selection else None,
                "selected": self.selected, "focused": self.focused}


def _selector(value, what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > MAX_SELECTOR_CHARS or \
            any(ord(c) < 32 for c in value):
        raise ViewError(f"{what} is not a selector of at most {MAX_SELECTOR_CHARS} characters "
                        f"on one line.")
    return value


def _words(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ViewError("A part's words are text.")
    return " ".join(value.split())[:MAX_WORDS]


def _px(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            not -MAX_SCROLL_PX <= value <= MAX_SCROLL_PX:
        raise ViewError(f"{what} is not a number of pixels.")
    return int(round(value))


def view_from(raw) -> View:
    """The view the page reported (the frame's `place`, with the deck slide
    it shows), checked and bounded, or ViewError."""
    if raw is None:
        return View()
    if not isinstance(raw, dict):
        raise ViewError("A view is an object.")
    scroll = raw.get("scroll") or []
    if not isinstance(scroll, list) or len(scroll) > MAX_SCROLLED:
        raise ViewError(f"A view's scroll is a list of at most {MAX_SCROLLED} parts.")
    scrolled = []
    for n, s in enumerate(scroll, 1):
        if not isinstance(s, dict):
            raise ViewError(f"Scrolled part {n} is not an object.")
        scrolled.append((_selector(s.get("at"), f"Scrolled part {n}") or "",
                         _px(s.get("x", 0), f"Scrolled part {n}'s x"),
                         _px(s.get("y", 0), f"Scrolled part {n}'s y")))
    selection = raw.get("selection")
    if selection is not None:
        if not (isinstance(selection, list) and len(selection) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1_000_000
                        for v in selection) and selection[0] <= selection[1]):
            raise ViewError("A selection is [start, end], character offsets in its part.")
        selection = (selection[0], selection[1])
    return View(_selector(raw.get("page"), "The slide shown"), tuple(scrolled),
                _selector(raw.get("part"), "The part"), _words(raw.get("words")), selection,
                _words(raw.get("selected")), _selector(raw.get("focused"), "The focused element"))


@dataclass(frozen=True)
class Place:
    """One place he asked from: the story's half and the page's."""
    package: str
    revision: str
    state: str                       # what the story was doing; "" for none told
    slide: Optional[int] = None      # the story slide, from 1
    beat: Optional[int] = None       # the beat within it, from 1
    speaking: bool = False           # its words were sounding when he asked
    question: Optional[str] = None   # the question it waited on
    view: View = field(default_factory=View)
    at: float = 0.0

    def frame(self) -> dict:
        """What the page is told of it (its own selectors and numbers)."""
        return {"package": self.package, "state": self.state, "slide": self.slide,
                "beat": self.beat, "speaking": self.speaking, "page": self.view.page}


def revision_of(package: Package) -> str:
    """The revision a place is in: an architecture package's checked
    revision, else the SHA-256 of the zip it was unpacked from, else (a
    folder from before records were kept) its id."""
    communication = package.manifest.communication or {}
    if communication.get("revision"):
        return f"architecture:{communication['revision']}"
    return work_packages.sha256_of(package) or f"id:{package.id}"


def revealed(story: Optional[Story], slide: Optional[int], beat: Optional[int],
             through: bool) -> list[dict]:
    """What the story had brought up on its slide by that beat, to bring up
    again in order: the slide shown, then every click of the beats before
    `beat` (and of `beat` itself when `through`: it had begun, and is not
    told again). Empty with no story or slide."""
    if story is None or not slide or not 1 <= slide <= len(story.slides):
        return []
    s = story.slides[slide - 1]
    last = min(len(s.beats), (beat or 1) - (0 if through else 1))
    actions = [{"show": s.target}] if s.target else []
    return actions + [{kind: sel} for b in s.beats[:max(0, last)] for kind, sel in b.actions
                      if kind == "click"]
