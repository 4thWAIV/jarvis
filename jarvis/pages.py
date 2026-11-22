"""
pages.py — a role's material packed into pages that fit its model, and the
pages asked in parallel (construction-drafts.md "Mark rebuilt for speed",
approved by Tim 2026-11-21).

What it replaced: one call per 40 parts, one at a time, each a new
session, each re-sending its whole file and writing every part's score for
all 28 labels; about 190 calls of two minutes a package.

- `grouped`: the files in the order they are packed, so similar ones share
  pages: the same type together, then the same folder, then close in
  meaning (the engine's own embeddings, where it has them).
- `pack`: pages packed by the program to a token budget worked out from
  the model's own context window (`Budget`): the instructions and the page
  at most half the window, and with them the most the page's answer can
  take and a generous margin, all inside it; the answer never more than
  the model gives in one turn. Several files to a page; a file too long
  for one is split across pages at its section breaks, each piece keeping
  its file and positions. Tokens are counted cheaply, erring high
  (`tokens`).
- `run`: the pages asked at once, at most the brain's own number
  (model_tiers.PARALLEL), each worker keeping one session across its
  pages (a new conversation for each page, on the same brain process where
  the brain allows). A page whose answer does not check is asked again
  once, then split in half, each half asked the same way.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Sequence, TypeVar

from jarvis import model_tiers

log = logging.getLogger("jarvis.pages")

T = TypeVar("T")

# The starting context each brain gives a role before its prompt
# (sessions.yaml launch_v1: about 637 tokens on Claude, 2,500 on Codex).
STARTING_CONTEXT = {"claude": 637, "codex": 2_500}

# A cheap count that errs high: a token is taken as three bytes of UTF-8
# (English prose runs nearer four characters a token, so this counts more).
BYTES_PER_TOKEN = 3

# How strong a break comes before a piece of a file: where a long file is
# split across pages.
NO_BREAK, PARAGRAPH, SECTION = 0, 1, 2


class TooLarge(ValueError):
    """The material cannot be packed inside the model's window: the
    instructions alone, or one sentence with them, leave no room."""


def tokens(text: str) -> int:
    """A text's tokens, counted cheaply and on the high side."""
    return math.ceil(len(text.encode("utf-8")) / BYTES_PER_TOKEN)


# ── the budget ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Budget:
    """What one call may hold, from the model's own context window: the
    instructions and the page at most half of it, and with them the most
    the page's answer can take and a margin of a quarter of it, all inside
    it; the answer never more than the model gives in one turn."""
    window: int
    answer_most: int

    @property
    def margin(self) -> int:
        return self.window // 4

    def fits(self, given: int, answer: int) -> bool:
        return (given <= self.window // 2 and answer <= self.answer_most
                and given + answer + self.margin <= self.window)


def budget(pick: model_tiers.Pick) -> Budget:
    """The budget of a role's calls on its brain: the smallest window among
    the models its sweep may take (the session may be swept onto any)."""
    models = list(pick.tried or [pick.model])
    window = min(model_tiers.context_window(pick.engine, m) for m in models)
    return Budget(window, model_tiers.answer_most(pick.engine))


def starting_context(engine: str) -> int:
    return STARTING_CONTEXT.get(engine, max(STARTING_CONTEXT.values()))


# ── grouping ──────────────────────────────────────────────────────────────

def _engine_embed(texts: list[str]) -> Optional[list[list[float]]]:
    """The engine's own embeddings of the texts, or None while it has no
    model or cannot be asked."""
    try:
        from jarvis import records
        return records.uds().embed(texts)
    except Exception as e:  # noqa: BLE001 - grouping by meaning is only where present
        log.info("pages: no embeddings to group by meaning (%s)", e)
        return None


# What of a file its meaning is read from: the embedding model reads its
# opening (the multilingual-e5 model takes 512 tokens).
MEANING_CHARS = 2_000


def _chain(group: list[T], vectors: dict[int, list[float]], ids: list[int]) -> list[T]:
    """The group in a nearest-neighbour chain by meaning, from its first."""
    left = list(range(len(group)))
    order = [left.pop(0)]
    while left:
        here = vectors[ids[order[-1]]]
        best = max(left, key=lambda i: sum(a * b for a, b in zip(here, vectors[ids[i]])))
        left.remove(best)
        order.append(best)
    return [group[i] for i in order]


def grouped(things: Sequence[T], *, kind: Callable[[T], str], folder: Callable[[T], str],
            text: Callable[[T], str], name: Callable[[T], str],
            embed: Callable[[list[str]], Optional[list[list[float]]]] = _engine_embed
            ) -> list[T]:
    """The things in the order they are packed: by type, then by folder,
    each group's own in name order, then chained so each is followed by
    the one closest to it in meaning, where the engine gives embeddings."""
    ordered = sorted(things, key=lambda t: (kind(t), folder(t), name(t)))
    groups: dict[tuple[str, str], list[T]] = {}
    for t in ordered:
        groups.setdefault((kind(t), folder(t)), []).append(t)
    vectors = embed([text(t)[:MEANING_CHARS] for t in ordered]) if len(ordered) > 2 else None
    if not vectors or len(vectors) != len(ordered):
        return ordered
    by_id = {id(t): n for n, t in enumerate(ordered)}
    found = dict(enumerate(vectors))
    out: list[T] = []
    for group in groups.values():
        out += _chain(group, found, [by_id[id(t)] for t in group]) if len(group) > 2 else group
    return out


# ── packing ───────────────────────────────────────────────────────────────

@dataclass
class Atom:
    """The smallest thing a page holds: a sentence with its phrases, or one
    item. `given`: its tokens on the page; `answer`: the most its answer can
    take; `brk`: how strong a break comes before it."""
    items: list
    given: int
    answer: int
    brk: int = NO_BREAK


@dataclass
class Doc:
    """One file (or one slide) to pack: its atoms in order, and `context`,
    the tokens of the material given with any page holding some of it (its
    whole text), 0 for none."""
    key: str
    atoms: list[Atom]
    context: int = 0
    meta: Any = None


@dataclass
class Entry:
    doc: Doc
    atoms: list[Atom]
    whole: bool                 # every atom of its doc is on this page
    context: bool = True        # its context is given with it

    @property
    def given(self) -> int:
        return sum(a.given for a in self.atoms) + (self.doc.context if self.context else 0)

    @property
    def answer(self) -> int:
        return sum(a.answer for a in self.atoms)


@dataclass
class Page:
    entries: list[Entry] = field(default_factory=list)
    tries: int = 0

    @property
    def given(self) -> int:
        return sum(e.given for e in self.entries)

    @property
    def answer(self) -> int:
        return sum(e.answer for e in self.entries)

    @property
    def items(self) -> list:
        return [i for e in self.entries for a in e.atoms for i in a.items]

    def halves(self) -> Optional[tuple["Page", "Page"]]:
        """The page cut in two between its atoms, in order; None for a page
        of one atom. A doc cut in two keeps its context on both sides."""
        flat = [(n, a) for n, e in enumerate(self.entries) for a in e.atoms]
        if len(flat) < 2:
            return None
        cut = len(flat) // 2

        def side(part: list[tuple[int, Atom]]) -> "Page":
            out: list[Entry] = []
            for n, atom in part:
                if out and out[-1].doc is self.entries[n].doc:
                    out[-1].atoms.append(atom)
                else:
                    out.append(Entry(self.entries[n].doc, [atom], False, True))
            for e in out:
                was = next(x for x in self.entries if x.doc is e.doc)
                e.whole = was.whole and len(e.atoms) == len(was.atoms)
                e.context = was.context or not e.whole
            return Page(out)
        return side(flat[:cut]), side(flat[cut:])


def _cut(atoms: list[Atom], start: int, stop: int) -> int:
    """Where a piece that fits from `start` to `stop` ends: at the strongest
    break in its later half, the latest such; else at `stop`."""
    if stop >= len(atoms):
        return stop
    later = range(start + 1 + (stop - start) // 2, stop + 1)
    best = max(later, key=lambda k: (atoms[k].brk, k), default=stop)
    return best if atoms[best].brk > NO_BREAK else stop


def pack(docs: Sequence[Doc], fixed: int, room: Budget) -> list[Page]:
    """The docs, in order, packed into pages that fit `room` with `fixed`,
    the tokens every call holds (the instructions). A doc goes whole where
    it fits, on the page under way or else a new one; one too long for any
    page is split across pages, each piece ending at a section break where
    one falls late enough, given with its context."""
    if not room.fits(fixed, 0):
        raise TooLarge(f"the instructions alone ({fixed} tokens) do not fit half of the "
                       f"model's {room.window}-token window")
    pages: list[Page] = []
    page = Page()

    def fits(on: Page, given: int, answer: int) -> bool:
        return room.fits(fixed + on.given + given, on.answer + answer)

    def close() -> None:
        nonlocal page
        if page.entries:
            pages.append(page)
        page = Page()

    for doc in docs:
        if not doc.atoms:
            continue
        whole_given = sum(a.given for a in doc.atoms) + doc.context
        whole_answer = sum(a.answer for a in doc.atoms)
        if fits(page, whole_given, whole_answer):
            page.entries.append(Entry(doc, list(doc.atoms), True))
            continue
        if page.entries and fits(Page(), whole_given, whole_answer):
            close()
            page.entries.append(Entry(doc, list(doc.atoms), True))
            continue
        # Too long for a page: pieces, each with its context where every
        # one of its atoms fits beside it, else without.
        context = all(fits(Page(), a.given + doc.context, a.answer) for a in doc.atoms)
        extra = doc.context if context else 0
        at = 0
        while at < len(doc.atoms):
            first = doc.atoms[at]
            if not fits(page, first.given + extra, first.answer):
                if not page.entries:
                    raise TooLarge(f"one sentence of {doc.key} does not fit a page with the "
                                   f"instructions ({fixed} tokens)")
                close()
                continue
            given, answer, stop = extra, 0, at
            while stop < len(doc.atoms) and fits(page, given + doc.atoms[stop].given,
                                                 answer + doc.atoms[stop].answer):
                given += doc.atoms[stop].given
                answer += doc.atoms[stop].answer
                stop += 1
            stop = _cut(doc.atoms, at, stop)
            page.entries.append(Entry(doc, doc.atoms[at:stop], False, context))
            at = stop
            if at < len(doc.atoms):
                close()
    close()
    return pages


# ── asking the pages ──────────────────────────────────────────────────────

class NotChecked(Exception):
    """A page's answer that did not check (raised by the caller's `ask`)."""


@dataclass
class Report:
    """What asking a role's pages took: the pages packed, the calls made
    (each ask, again and halves included), how long, and each call's turn
    (its seconds and tokens, as the session reports them)."""
    pages: int = 0
    calls: int = 0
    again: int = 0
    halved: int = 0
    workers: int = 0
    seconds: float = 0.0
    turns: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        used: dict[str, int] = {}
        for turn in self.turns:
            for k, v in (turn.get("turn_tokens") or {
                    "inputTokens": turn.get("context_tokens") or 0,
                    "outputTokens": turn.get("output_tokens") or 0}).items():
                used[k] = used.get(k, 0) + int(v or 0)
        return {"pages": self.pages, "calls": self.calls, "asked_again": self.again,
                "halved": self.halved, "workers": self.workers,
                "seconds": round(self.seconds, 1), "tokens": used}


async def run(pages: list[Page], *, first: Any, make: Callable[[], Awaitable[Any]],
              parallel: int, ask: Callable[[Any, Page], Awaitable[T]],
              failed: Callable[[str], Exception], report: Optional[Report] = None) -> list[T]:
    """Every page's answer, asked by at most `parallel` workers at once,
    each keeping one session across its pages: `first` (already made) for
    the first worker, `make()` for each other. A session is opened before
    its first page and given a new conversation (`fresh`) before each
    after; all are closed at the end. `ask(session, page)` raises
    NotChecked for an answer that does not check: the page is asked again
    once, then split in half, each half asked the same way; a page of one
    atom that still does not check is `failed(why)`. Any other failure
    stops every worker and is raised."""
    report = report if report is not None else Report()
    report.pages = len(pages)
    if not pages:
        return []
    started = time.monotonic()
    queue: asyncio.Queue = asyncio.Queue()
    for p in pages:
        queue.put_nowait(p)
    results: list[T] = []
    failures: list[BaseException] = []
    outstanding = len(pages)
    workers = max(1, min(parallel, len(pages)))
    report.workers = workers

    def finish_one() -> None:
        nonlocal outstanding
        outstanding -= 1
        if outstanding <= 0 or failures:
            for _ in range(workers):
                queue.put_nowait(None)

    async def worker(n: int) -> None:
        nonlocal outstanding
        session, opened = (first if n == 0 else None), False
        try:
            while True:
                page = await queue.get()
                if page is None:
                    return
                try:
                    if failures:
                        continue
                    if session is None:
                        session = await make()
                    if not opened:
                        await session.open()
                        opened = True
                    else:
                        await session.fresh()
                    report.calls += 1
                    try:
                        results.append(await ask(session, page))
                    finally:
                        turn = getattr(session, "last_turn", None)
                        if isinstance(turn, dict):
                            report.turns.append(dict(turn))
                except NotChecked as e:
                    if page.tries == 0:
                        page.tries = 1
                        report.again += 1
                        log.info("pages: a page of %d item(s) did not check (%s); asked again",
                                 len(page.items), e)
                        outstanding += 1
                        queue.put_nowait(page)
                    else:
                        halves = page.halves()
                        if halves is None:
                            failures.append(failed(str(e)))
                        else:
                            report.halved += 1
                            log.info("pages: a page of %d item(s) did not check again (%s); "
                                     "split in half", len(page.items), e)
                            outstanding += 2
                            for half in halves:
                                queue.put_nowait(half)
                except BaseException as e:  # noqa: BLE001 - stops every worker, raised below
                    failures.append(e)
                finally:
                    finish_one()
        finally:
            if session is not None and opened:
                await session.close()

    try:
        await asyncio.gather(*(worker(n) for n in range(workers)))
    finally:
        report.seconds = time.monotonic() - started
    if failures:
        raise failures[0]
    return results
