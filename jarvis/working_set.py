"""The working set a swarm participant works from, in place of the whole
evidence (construction-drafts.md "The author works from a prioritized
working set and calls back for more", approved by Tim 2026-11-21).

Giving the author every evidence file whole with every mark came to 1.1 MB
on the real-fixes evidence, past Codex's limit. The working set is, in
this order of priority:

  contents     a table of contents of the evidence: each stored file, its
               type, size, Tim's knowledge state and a one-line summary
               (its first heading, else its first sentence; no model call)
  hits         the program's search results, each short (its path, score,
               which index found it and the opening of its words): few and
               short, so never crowded out by the sentences
  sentences    for each question, the sentences (and facts) marking found
               bearing on it at 0.5 or more (`BEARS`), highest first, then
               by their label scores, each with its labels, file and
               position (its file's knowledge state is in the contents),
               its words given once (under the question it bears on most,
               the others named beside it)

packed into the room the model's window leaves (`room`: pages.Budget,
never over half the window), cutting the lowest priority first and saying
in the set how much was left out and that the participant can read it with
its tools.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from jarvis import pages, sessions

# A sentence is prioritized for a question it bears on at least this much
# (the classification score; the construction draft's "0.5 or more").
BEARS = 0.5
# The words of a search hit given, and of a file's one-line summary.
HIT_CHARS = 280
SUMMARY_CHARS = 160
# What the note saying what was left out may take.
NOTE_TOKENS = 400

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_SENTENCE = re.compile(r"(.+?[.!?])(?:\s|$)", re.DOTALL)


# ── the room ──────────────────────────────────────────────────────────────

def room(budget: pages.Budget) -> int:
    """The most tokens a call's input may hold under this budget: never
    over half the window, and with the most the answer can take and the
    margin, inside it (pages.Budget.fits)."""
    most = budget.answer_most
    return max(0, min(budget.window // 2,
                      budget.window - budget.margin - budget.reserve(budget.window // 2, most)))


# ── the parts ─────────────────────────────────────────────────────────────

def summary_line(text: str) -> str:
    """A file's one line: its first heading, else its first sentence."""
    heading = _HEADING.search(text or "")
    if heading:
        line = heading.group(1)
    else:
        lines = [l.strip() for l in (text or "").splitlines() if re.search(r"[^\W\d_]", l)]
        first = " ".join(lines[:3])
        found = _SENTENCE.match(first)
        line = found.group(1) if found else first
    line = " ".join(line.split())
    return line if len(line) <= SUMMARY_CHARS else line[:SUMMARY_CHARS - 1] + "…"


def contents(rows, texts: dict[str, str], states: Optional[tuple[str, ...]] = None) -> list[str]:
    """The table of contents: one line a stored file."""
    out = []
    for r in rows:
        if not r.stored or (states is not None and r.knowledge_state not in states):
            continue
        out.append(sessions._tag("file", summary_line(texts.get(r.stored, "")), path=r.stored,
                                 type=r.file_type or r.type, size=r.size,
                                 state=r.knowledge_state))
    return out


def _scores(scores: dict) -> str:
    return " ".join(f"{k} {v:.2f}".replace("0.", ".", 1) if v < 1 else f"{k} 1"
                    for k, v in sorted(scores.items(), key=lambda kv: -kv[1]))


@dataclass
class Sentence:
    """One marked sentence (or fact) prioritized for a question."""
    id: str
    file: str
    start: Optional[int]
    end: Optional[int]
    text: str
    state: str
    labels: dict = field(default_factory=dict)
    questions: dict = field(default_factory=dict)       # each it bears on at BEARS or more

    @property
    def question(self) -> str:
        """The question it bears on most (the first given on a tie)."""
        return max(self.questions, key=lambda q: self.questions[q])

    @property
    def priority(self) -> tuple[float, float]:
        return (max(self.questions.values()), max(self.labels.values(), default=0.0))

    def shown(self) -> str:
        return sessions._tag("sentence", self.text, id=self.id, file=self.file,
                             start=self.start, end=self.end,
                             questions=_scores(self.questions),
                             labels=_scores(self.labels) or None)


def prioritized(marked: dict, rows, question_order: list[str],
                states: Optional[tuple[str, ...]] = None) -> list[Sentence]:
    """Every sentence and fact marking found bearing on a question at BEARS
    or more, highest first: by its question score, then its label scores,
    then the question's order and where it is."""
    state_of = {r.stored: r.knowledge_state for r in rows if r.stored}
    out: list[Sentence] = []
    for entry in marked.get("files") or []:
        file = entry.get("file")
        if file not in state_of or (states is not None and state_of[file] not in states):
            continue
        marks = entry.get("marks") or {}
        bears = {q.get("item"): {k: v for k, v in (q.get("scores") or {}).items()
                                 if isinstance(v, (int, float)) and v >= BEARS}
                 for q in marks.get("questions") or []}
        items = [(p.get("id"), p.get("segment") or "", p.get("position") or {},
                  p.get("scores") or {}) for p in marks.get("parts") or []]
        items += [(f.get("id"), f"{f.get('field')}: {f.get('value')}" if f.get("field")
                   else str(f.get("value")), f.get("position") or {}, {})
                  for f in marks.get("facts") or []]
        for item_id, text, pos, labels in items:
            asked = bears.get(item_id) or {}
            if not asked or not text.strip():
                continue
            out.append(Sentence(str(item_id), file, pos.get("start"), pos.get("end"), text,
                                state_of[file], dict(labels), asked))
    order = {q: n for n, q in enumerate(question_order)}
    out.sort(key=lambda s: (-s.priority[0], -s.priority[1], order.get(s.question, len(order)),
                            s.file, s.start if s.start is not None else 0))
    return out


def short_hits(looked: list[dict]) -> list[str]:
    """The program's search results, one short hit a line, each search's
    hits in its order, the searches in theirs."""
    out = []
    for q in looked:
        said = q["query"] if isinstance(q["query"], str) else json.dumps(
            q["query"], ensure_ascii=False, default=str)
        for h in q.get("hits") or []:
            words = h.get("text")
            words = words if isinstance(words, str) else json.dumps(h, ensure_ascii=False,
                                                                    default=str)
            path = (h.get("path") or h.get("key") or h.get("url") or h.get("number")
                    or h.get("databaseId") or "")
            out.append(sessions._tag("hit", " ".join(words.split())[:HIT_CHARS],
                                     tool=q["tool"], query=said, under=q.get("under"),
                                     path=path or None, score=h.get("score")))
    return out


# ── packing ───────────────────────────────────────────────────────────────

@dataclass
class WorkingSet:
    text: str
    tokens: int
    given: dict                 # how many of each part are in it
    total: dict                 # how many of each there were
    left_out_tokens: int        # what was left out, counted as given

    @property
    def cut(self) -> bool:
        return any(self.given[k] < self.total[k] for k in self.total)


def build(rows, texts: dict[str, str], marked: dict, question_order: list[str],
          looked: list[dict], room_tokens: int, *, reads: str,
          states: Optional[tuple[str, ...]] = None) -> WorkingSet:
    """The working set in at most `room_tokens`, cutting the lowest priority
    first (the contents, then the hits, then the sentences highest first),
    with a note of what was left out and how to read it (`reads`: the tools
    and where)."""
    toc = contents(rows, texts, states)
    sentences = prioritized(marked, rows, question_order, states)
    hits = short_hits(looked)
    ranked = ([("contents", line) for line in toc] + [("hits", h) for h in hits]
              + [("sentences", s) for s in sentences])
    budget = room_tokens - NOTE_TOKENS
    used, kept = 0, []
    for kind, thing in ranked:
        cost = pages.tokens(thing.shown() if kind == "sentences" else thing) + 1
        if used + cost > budget:
            break
        kept.append((kind, thing))
        used += cost
    given = {k: sum(1 for kind, _ in kept if kind == k) for k in ("contents", "sentences", "hits")}
    total = {"contents": len(toc), "sentences": len(sentences), "hits": len(hits)}
    left = sum(pages.tokens(t.shown() if k == "sentences" else t) for k, t in ranked[len(kept):])
    whole = sum(pages.tokens(texts.get(r.stored, "")) for r in rows if r.stored
                and (states is None or r.knowledge_state in states))
    parts = []
    shown_toc = [t for k, t in kept if k == "contents"]
    if shown_toc:
        parts.append(sessions._tag("contents", "\n" + "\n".join(shown_toc) + "\n"))
    by_question: dict[str, list[Sentence]] = {}
    for kind, s in kept:
        if kind == "sentences":
            by_question.setdefault(s.question, []).append(s)
    order = question_order + [q for q in by_question if q not in question_order]
    for q in order:
        if by_question.get(q):
            parts.append(sessions._tag("prioritized", "\n" + "\n".join(
                s.shown() for s in by_question[q]) + "\n", question=q))
    shown_hits = [t for k, t in kept if k == "hits"]
    if shown_hits:
        parts.append(sessions._tag("hits", "\n" + "\n".join(shown_hits) + "\n"))
    note = (f"This is a working set, not everything: the contents name every file "
            f"({total['contents']}, about {whole:,} tokens whole). It holds "
            f"{given['contents']} of {total['contents']} contents lines, "
            f"{given['sentences']} of {total['sentences']} prioritized sentences and "
            f"{given['hits']} of {total['hits']} search hits")
    note += (f"; about {left:,} tokens of them were left out, lowest priority first. "
             if left else ". ")
    note += f"Read anything more you need: {reads}"
    parts.append(sessions._tag("left_out", note))
    text = "\n".join(parts)
    return WorkingSet(text, pages.tokens(text), given, total, left)
