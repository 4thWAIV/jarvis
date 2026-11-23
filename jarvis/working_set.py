"""What a swarm participant is given of the evidence (construction-drafts.md
"The author works from a prioritized working set and calls back for more",
approved by Tim 2026-11-21).

Every file is given whole with its marks in place when the evidence fits
the room the model's window leaves (`room`: pages.Budget, never over half
the window); only when it does not is it given as a working set. Either
way it holds, in this order:

  legend       what a mark says: [s4 q1 .9 evidence .8] is sentence (or
               fact) s4, bearing on question q1 at .9 and marked evidence
               at .8; the handoff's questions numbered q1, q2 ... (only the
               handoff's own questions: classification's `background` is no
               question); every score shown is 0.5 or more (`BEARS`)
  contents     a table of contents of the evidence: each stored file, its
               type, size, Tim's knowledge state and a one-line summary
               (its first heading, else its first sentence; no model call)
  the files    whole: each file once, named once, its marks short and in
               place before the sentence they mark (`in_place`), no
               position repeated (measured on package-870e45145b2d: the
               working set with each sentence's id, file, positions,
               questions and labels as attributes came to 289 KB against
               253 KB of whole files)
     or        the working set: for each question, the sentences (and
               facts) bearing on it at BEARS or more, highest first, then
               by their label scores, under the file they are in (named
               once), each with its mark, its words given once (under the
               question it bears on most); cut lowest priority first, the
               set saying how much was left out and which tools read it
  hits         the program's search results, each short (its path, score,
               which index found it and the opening of its words)
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

_HEADING = re.compile(r"[ \t]{0,3}#{1,6}[ \t]+(.*?\S)[ \t]*#*[ \t]*")
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
    lines = (text or "").splitlines()
    heading = next((match for line in lines if (match := _HEADING.fullmatch(line))), None)
    if heading:
        line = heading.group(1)
    else:
        lines = [l.strip() for l in lines if re.search(r"[^\W\d_]", l)]
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


def _score(v: float) -> str:
    """A score in one or two figures: .7, .85, 1."""
    return "1" if v >= 1 else f"{v:.2f}".rstrip("0").rstrip(".").lstrip("0") or "0"


def numbered(question_order: list[str]) -> dict[str, str]:
    """The handoff's questions by their short names in a mark: q1, q2 ..."""
    return {q: f"q{n}" for n, q in enumerate(question_order, 1)}


def mark(item_id: str, questions: dict, labels: dict, names: dict[str, str]) -> str:
    """One sentence's (or fact's) mark: its id, each question it bears on
    and each label, at BEARS or more, highest first; "" when it has none."""
    said = [f"{names[q]} {_score(v)}" for q, v in
            sorted(questions.items(), key=lambda kv: -kv[1]) if q in names and v >= BEARS]
    said += [f"{k} {_score(v)}" for k, v in sorted(labels.items(), key=lambda kv: -kv[1])
             if isinstance(v, (int, float)) and v >= BEARS]
    return f"[{item_id} {' '.join(said)}]" if said else ""


@dataclass
class Sentence:
    """One marked sentence (or fact) of a file, with its marks."""
    id: str
    file: str
    start: Optional[int]
    end: Optional[int]
    text: str
    state: str
    labels: dict = field(default_factory=dict)
    questions: dict = field(default_factory=dict)       # each handoff question, at BEARS or more
    fact: bool = False          # a fact (a value within a line), not a sentence

    @property
    def question(self) -> str:
        """The question it bears on most (the first given on a tie)."""
        return max(self.questions, key=lambda q: self.questions[q])

    @property
    def priority(self) -> tuple[float, float]:
        return (max(self.questions.values(), default=0.0),
                max(self.labels.values(), default=0.0))

    def mark(self, names: dict[str, str]) -> str:
        return mark(self.id, self.questions, self.labels, names)

    def line(self, names: dict[str, str]) -> str:
        """Its mark and its words, one line of the working set."""
        return f"{self.mark(names)} {' '.join(self.text.split())}".strip()


def _sentences(entry: dict, file: str, state: str, question_order: list[str]) -> list[Sentence]:
    """Every sentence and fact of one file's marks, each with the handoff's
    questions it bears on at BEARS or more (never classification's
    background) and its labels."""
    asked = set(question_order)
    marks = entry.get("marks") or {}
    bears = {q.get("item"): {k: v for k, v in (q.get("scores") or {}).items()
                             if k in asked and isinstance(v, (int, float)) and v >= BEARS}
             for q in marks.get("questions") or []}
    items = [(p.get("id"), p.get("segment") or "", p.get("position") or {},
              p.get("scores") or {}, False) for p in marks.get("parts") or []]
    items += [(f.get("id"), f"{f.get('field')}: {f.get('value')}" if f.get("field")
               else str(f.get("value")), f.get("position") or {}, {}, True)
              for f in marks.get("facts") or []]
    return [Sentence(str(item_id), file, pos.get("start"), pos.get("end"), text, state,
                     {k: v for k, v in labels.items() if isinstance(v, (int, float))},
                     bears.get(item_id) or {}, fact)
            for item_id, text, pos, labels, fact in items if text.strip()]


def _entries(marked: dict, rows, states: Optional[tuple[str, ...]]):
    """(entry, file, state) for each marked file of this share, in the
    manifest's order."""
    state_of = {r.stored: r.knowledge_state for r in rows if r.stored}
    by_file = {e.get("file"): e for e in marked.get("files") or []}
    for r in rows:
        if r.stored in by_file and (states is None or state_of[r.stored] in states):
            yield by_file[r.stored], r.stored, state_of[r.stored]


def prioritized(marked: dict, rows, question_order: list[str],
                states: Optional[tuple[str, ...]] = None) -> list[Sentence]:
    """Every sentence and fact marking found bearing on one of the
    handoff's questions (`question_order`; classification's background is
    no question) at BEARS or more, highest first: by its question score,
    then its label scores, then the question's order and where it is."""
    out = [s for entry, file, state in _entries(marked, rows, states)
           for s in _sentences(entry, file, state, question_order) if s.questions]
    order = {q: n for n, q in enumerate(question_order)}
    out.sort(key=lambda s: (-s.priority[0], -s.priority[1], order.get(s.question, len(order)),
                            s.file, s.start if s.start is not None else 0))
    return out


def in_place(text: str, sentences: list[Sentence], names: dict[str, str]) -> str:
    """A file's words with each sentence's mark put in place before it, and
    each fact's at the start of the line it is in (a value is never split
    from the words around it), where its position holds exactly its words;
    nothing else of the file changed."""
    at: list[tuple[int, str]] = []
    for s in sentences:
        shown = s.mark(names)
        if not shown or s.start is None or s.end is None:
            continue
        if 0 <= s.start < s.end <= len(text) and (
                text[s.start:s.end] == s.text or s.fact and s.text.endswith(text[s.start:s.end])):
            at.append((text.rfind("\n", 0, s.start) + 1 if s.fact else s.start, shown))
    out, last = [], len(text)
    # From the end back, so each position still holds; marks at one place
    # come out in the order given.
    for n, (start, shown) in sorted(enumerate(at), key=lambda x: (x[1][0], x[0]), reverse=True):
        out.append(text[start:last])
        out.append(shown + " ")
        last = start
    out.append(text[:last])
    return "".join(reversed(out))


def legend(names: dict[str, str], question_text: Optional[dict[str, str]] = None) -> str:
    """What a mark says, and the questions by their short names."""
    said = "\n".join(f"{short}: {(question_text or {}).get(q) or q}" for q, short in names.items())
    return sessions._tag("marks", (
        "Each mark is the program's, never the file's words: [s4 q1 .9 evidence .8] is "
        "sentence s4, bearing on question q1 at .9 and marked evidence at .8. Every score "
        "shown is 0.5 or more.\n" + said))


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
    whole: bool                 # every file whole with its marks, not a working set
    given: dict                 # how many of each part are in it
    total: dict                 # how many of each there were
    left_out_tokens: int        # what was left out, counted as given

    @property
    def cut(self) -> bool:
        return any(self.given[k] < self.total[k] for k in self.total)


def _file_tag(path: str, row, text: str) -> str:
    return sessions._tag("document", "\n" + text + "\n", path=path,
                         type=row.file_type or row.type, state=row.knowledge_state)


def whole_files(rows, texts: dict[str, str], marked: dict, question_order: list[str],
                states: Optional[tuple[str, ...]] = None) -> list[str]:
    """Every file of this share whole, each named once, its marks in place."""
    names = numbered(question_order)
    by_file = {file: _sentences(entry, file, state, question_order)
               for entry, file, state in _entries(marked, rows, states)}
    return [_file_tag(r.stored, r, in_place(texts[r.stored], by_file.get(r.stored, []), names))
            for r in rows if r.stored in texts
            and (states is None or r.knowledge_state in states)]


def build(rows, texts: dict[str, str], marked: dict, question_order: list[str],
          looked: list[dict], room_tokens: int, *, reads: str,
          states: Optional[tuple[str, ...]] = None) -> WorkingSet:
    """What a participant is given of the evidence in at most `room_tokens`:
    every file whole with its marks in place, the contents and the hits,
    when they fit; else the working set, cutting the lowest priority first
    (the contents, then the hits, then the sentences highest first), with a
    note of what was left out and how to read it (`reads`: the tools and
    where). `question_order` is the handoff's questions only."""
    names = numbered(question_order)
    asks = {q.get("id"): q.get("asks") for q in marked.get("questions") or []}
    head = legend(names, asks)
    toc = contents(rows, texts, states)
    hits = short_hits(looked)
    toc_text = sessions._tag("contents", "\n" + "\n".join(toc) + "\n") if toc else ""
    hits_text = sessions._tag("hits", "\n" + "\n".join(hits) + "\n") if hits else ""

    files = whole_files(rows, texts, marked, question_order, states)
    whole_text = "\n".join(p for p in [head, toc_text, *files, hits_text, sessions._tag(
        "more", f"These are the evidence's files whole. Search or read more: {reads}")] if p)
    if pages.tokens(whole_text) <= room_tokens:
        counts = {"contents": len(toc), "files": len(files), "hits": len(hits)}
        return WorkingSet(whole_text, pages.tokens(whole_text), True, counts, dict(counts), 0)

    sentences = prioritized(marked, rows, question_order, states)
    ranked = ([("contents", line) for line in toc] + [("hits", h) for h in hits]
              + [("sentences", s) for s in sentences])
    budget = room_tokens - NOTE_TOKENS - pages.tokens(head) - 1
    used, kept, opened = 0, [], set()
    for kind, thing in ranked:
        if kind == "sentences":
            # A file is named once under each question it is shown for.
            cost = pages.tokens(thing.line(names)) + 1
            if (thing.question, thing.file) not in opened:
                cost += pages.tokens(sessions._tag("from", "\n\n", file=thing.file)) + 1
        else:
            cost = pages.tokens(thing) + 1
        if used + cost > budget:
            break
        kept.append((kind, thing))
        if kind == "sentences":
            opened.add((thing.question, thing.file))
        used += cost
    given = {k: sum(1 for kind, _ in kept if kind == k) for k in ("contents", "sentences", "hits")}
    total = {"contents": len(toc), "sentences": len(sentences), "hits": len(hits)}
    left = sum(pages.tokens(t.line(names) if k == "sentences" else t)
               for k, t in ranked[len(kept):])
    whole = sum(pages.tokens(texts.get(r.stored, "")) for r in rows if r.stored
                and (states is None or r.knowledge_state in states))
    parts = [head]
    shown_toc = [t for k, t in kept if k == "contents"]
    if shown_toc:
        parts.append(sessions._tag("contents", "\n" + "\n".join(shown_toc) + "\n"))
    by_question: dict[str, dict[str, list[Sentence]]] = {}
    for kind, s in kept:
        if kind == "sentences":
            by_question.setdefault(s.question, {}).setdefault(s.file, []).append(s)
    for q in question_order:
        if by_question.get(q):
            parts.append(sessions._tag("prioritized", "\n" + "\n".join(
                sessions._tag("from", "\n" + "\n".join(s.line(names) for s in said) + "\n",
                              file=file)
                for file, said in by_question[q].items()) + "\n", question=names[q]))
    shown_hits = [t for k, t in kept if k == "hits"]
    if shown_hits:
        parts.append(sessions._tag("hits", "\n" + "\n".join(shown_hits) + "\n"))
    note = (f"This is a working set, not everything: the evidence whole (about {whole:,} "
            f"tokens) does not fit, and the contents name every file ({total['contents']}). It "
            f"holds {given['contents']} of {total['contents']} contents lines, "
            f"{given['sentences']} of {total['sentences']} prioritized sentences and "
            f"{given['hits']} of {total['hits']} search hits")
    note += (f"; about {left:,} tokens of them were left out, lowest priority first. "
             if left else ". ")
    note += f"Read anything more you need: {reads}"
    parts.append(sessions._tag("left_out", note))
    text = "\n".join(parts)
    return WorkingSet(text, pages.tokens(text), False, given, total, left)
