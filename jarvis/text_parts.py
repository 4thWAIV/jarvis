"""
text_parts.py — a written text split into its sentences, and each sentence
into the parts that go together (phrases), by a program: what a
MarkingSession scores, and where an extracted value's context is read.

The design (sessions.yaml MarkingSession splitter, approved 2026-11-19):
- sentences: JARVIS's own splitter (speech.py's rules: abbreviations,
  English and Spanish), extended for written text: lists, headings, line
  breaks. A blank line ends a paragraph; a heading is a sentence of its
  own; a list item starts a new one; a line wrapped inside a paragraph is
  not a break.
- phrases: each sentence cut at its joints: commas, semicolons, colons,
  dashes, brackets, and words that start a new part (and, but, because,
  which, so, when; y, pero, porque, que in Spanish). No grammar parse.

Every segment is the text's own characters at its offsets, never rewritten.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from jarvis.speech import sentence_end

SENTENCE = "sentence"
PHRASE = "phrase"

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s")
JOINING_WORDS = ("and", "but", "because", "which", "so", "when",
                 "y", "pero", "porque", "que")
_JOINING = re.compile(r"(?<=\s)(?:" + "|".join(JOINING_WORDS) + r")\b", re.IGNORECASE)
_AFTER = re.compile(r"[,;:](?=\s)|[)\]]")         # a part ends after these
_BEFORE = re.compile(r"\s[—–-]\s|[(\[]")  # a part starts at these


@dataclass(frozen=True)
class Segment:
    id: str
    kind: str                   # SENTENCE or PHRASE
    text: str                   # exactly the text's characters at [start, end)
    start: int
    end: int
    within: Optional[str]       # the sentence a phrase belongs to


def _trimmed(text: str, start: int, end: int) -> Optional[tuple[int, int]]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if any(c.isalnum() for c in text[start:end]) else None


def _units(text: str) -> list[tuple[int, int]]:
    """The stretches sentences are found in: paragraphs, headings and list
    items, as offsets."""
    units: list[tuple[int, int]] = []
    open_at: Optional[int] = None
    heading_open = False
    pos = 0
    for line in text.splitlines(keepends=True):
        bare = line.strip()
        starts_new = bool(_HEADING.match(line) or _LIST_ITEM.match(line))
        if not bare or starts_new or heading_open:
            if open_at is not None:
                units.append((open_at, pos))
            open_at = None
        if bare and open_at is None:
            open_at = pos
        heading_open = bool(bare) and bool(_HEADING.match(line))
        pos += len(line)
    if open_at is not None:
        units.append((open_at, pos))
    return units


def sentences(text: str) -> list[tuple[int, int]]:
    """Every sentence of the text, as (start, end) offsets."""
    found: list[tuple[int, int]] = []
    for start, end in _units(text):
        at = start
        while at < end:
            cut = sentence_end(text[at:end])
            stop = end if cut is None else at + cut
            span = _trimmed(text, at, stop)
            if span:
                found.append(span)
            at = stop
    return found


def phrases(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The parts of one sentence at its joints, as offsets; one part when it
    has no joint."""
    sentence = text[start:end]
    cuts = {m.end() for m in _AFTER.finditer(sentence)}
    cuts |= {m.start() for m in _BEFORE.finditer(sentence)}
    cuts |= {m.start() for m in _JOINING.finditer(sentence)}
    bounds = [0] + sorted(c for c in cuts if 0 < c < len(sentence)) + [len(sentence)]
    parts = []
    for a, b in zip(bounds, bounds[1:]):
        span = _trimmed(text, start + a, start + b)
        if span:
            parts.append(span)
    return parts


def split(text: str) -> list[Segment]:
    """Each sentence, then its phrases when it has more than one part."""
    out: list[Segment] = []
    for n, (start, end) in enumerate(sentences(text), 1):
        sid = f"s{n}"
        out.append(Segment(sid, SENTENCE, text[start:end], start, end, None))
        parts = phrases(text, start, end)
        if len(parts) > 1:
            out += [Segment(f"{sid}.p{m}", PHRASE, text[a:b], a, b, sid)
                    for m, (a, b) in enumerate(parts, 1)]
    return out


def sentence_around(text: str, start: int, end: int) -> str:
    """The sentence or sentences the span [start, end) sits in: an
    extracted value's context."""
    spans = [(a, b) for a, b in sentences(text) if a < end and b > start]
    if not spans:
        return text[start:end]
    return text[spans[0][0]:spans[-1][1]]
