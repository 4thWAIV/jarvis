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
  at most half the window, and with them the answer's reserve and a
  generous margin, all inside it; the answer never more than the model
  gives in one turn. The reserve is the expected answer, measured
  (`EXPECTED_ANSWER`: output tokens per input token of the 2026-11-21
  proof, times `SAFETY`), never more than the most the page's answer can
  take (each part with its top labels), so pages fill toward half the
  window. Several files to a page; a file too long
  for one is split across pages at its section breaks, each piece keeping
  its file and positions. Tokens are counted cheaply, erring high
  (`tokens`). Asked by several workers at once, the material goes into
  exactly one page a worker while the budget lets it (fewer only with
  fewer atoms than workers), the answers shared evenly (`pages_for`,
  `_even`); never a page more than the workers, waiting for a second wave.
- `run`: the pages asked at once, at most the brain's own number
  (model_tiers.PARALLEL), each worker keeping one session across its
  pages (a new conversation for each page, on the same brain process where
  the brain allows). A page whose answer does not check (a cut-off answer
  among them) is asked again once, then split in half, each half asked
  the same way. A call the brain refuses as rate-limited pauses every
  worker (`RateLimited`): until the time the brain gives, else for a
  doubling wait, and the page is asked again; each hit is counted.
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


# The expected answer of a paged role, in output tokens per input token,
# measured on the smart passes' proof of 2026-11-21 (package-121122ef070f's
# evidence on Codex, whole sentences with at most their top 3 at 0.5 or
# more: marking 287,363 in, 61,278 out, 0.21; classification 156,993 in,
# 46,596 out, 0.30), and the safety factor it is reserved with. A role not
# named here reserves the most its answer can take.
EXPECTED_ANSWER = {"marking": 0.22, "classification": 0.3}
SAFETY = 2


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
    instructions and the page at most half of it, and with them the
    answer's reserve and a margin of a quarter of it, all inside it; the
    answer never more than the model gives in one turn.

    The reserve (`reserve`): `expected` output tokens per input token of
    the whole call, never more than `most`, the most the page's answer
    can take; with no `expected`, `most` itself."""
    window: int
    answer_most: int
    expected: Optional[float] = None

    @property
    def margin(self) -> int:
        return self.window // 4

    def reserve(self, given: int, most: int) -> int:
        if self.expected is None:
            return most
        return min(most, math.ceil(self.expected * given))

    def fits(self, given: int, most: int) -> bool:
        answer = self.reserve(given, most)
        return (given <= self.window // 2 and answer <= self.answer_most
                and given + answer + self.margin <= self.window)


def budget(pick: model_tiers.Pick, role: str = "") -> Budget:
    """The budget of a role's calls on its brain: the smallest window among
    the models its sweep may take (the session may be swept onto any), the
    role's expected answer reserved (`EXPECTED_ANSWER` times `SAFETY`)."""
    models = list(pick.tried or [pick.model])
    window = min(model_tiers.context_window(pick.engine, m) for m in models)
    expected = EXPECTED_ANSWER.get(role)
    return Budget(window, model_tiers.answer_most(pick.engine),
                  expected * SAFETY if expected is not None else None)


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
    """The smallest thing a page holds: a sentence, or one item. `given`:
    its tokens on the page; `answer`: the most its answer can take; `brk`:
    how strong a break comes before it."""
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


def pack(docs: Sequence[Doc], fixed: int, room: Budget, spread: int = 1) -> list[Page]:
    """The docs, in order, packed into pages that fit `room` with `fixed`,
    the tokens every call holds (the instructions). A doc goes whole where
    it fits, on the page under way or else a new one; one too long for any
    page is split across pages, each piece ending at a section break where
    one falls late enough, given with its context.

    `spread`, the workers that ask the pages at once: the pages are asked
    in parallel, so the material goes into exactly as many pages as there
    are workers (`pages_for`: never more while the budget lets the material
    fit in that many, fewer only when there are fewer atoms than workers),
    their answers shared as evenly as the material allows (`_even`: the
    smallest share of the answer that packs into that many pages); the
    budget still bounds every page, so material more than the workers'
    pages hold takes the fewest pages the budget allows, evened the same
    way. Measured 2026-11-21 on Codex, package-121122ef070f's evidence:
    two pages filled to half the window were each answered with a few
    hundred tokens that did not check, and their halves took six minutes
    each; shared across ten workers the same marking took 137 s. Measured
    on package-870e45145b2d: packed greedily to a share, 11 pages on 10
    workers, so one page waited for a second wave (the slowest call 127 s).
    """
    if not room.fits(fixed, 0):
        raise TooLarge(f"the instructions alone ({fixed} tokens) do not fit half of the "
                       f"model's {room.window}-token window")
    atoms = [a for d in docs for a in d.atoms]
    if not atoms:
        return []
    fewest = _greedy(docs, fixed, room, None)
    target = pages_for(len(fewest), len(atoms), spread)
    packed = _even(docs, fixed, room, target, atoms)
    return _split_to(packed, target, fixed, room)


def pages_for(fewest: int, atoms: int, spread: int) -> int:
    """How many pages the material goes into: one a worker (`spread`),
    never more pages than atoms, and never fewer than the `fewest` the
    budget allows."""
    return max(fewest, min(max(spread, 1), atoms))


def _even(docs: Sequence[Doc], fixed: int, room: Budget, target: int,
          atoms: list[Atom]) -> list[Page]:
    """The docs packed into at most `target` pages with the smallest share
    of the answer a page may take that does it (a binary search over the
    share; with no share at all the budget alone packs them into the fewest
    pages, which is at most `target`)."""
    low = max(a.answer for a in atoms)
    high = sum(a.answer for a in atoms)
    best = _greedy(docs, fixed, room, high)
    while low < high:
        middle = (low + high) // 2
        packed = _greedy(docs, fixed, room, middle)
        if len(packed) <= target:
            best, high = packed, middle
        else:
            low = middle + 1
    return best


def _split_to(packed: list[Page], target: int, fixed: int, room: Budget) -> list[Page]:
    """Pages cut in half (`Page.halves`), the largest answer first, until
    there are `target`: a doc that goes whole leaves a page fewer than the
    workers. A page whose halves would not fit the budget (a cut doc's
    context given again) is left whole."""
    out = list(packed)
    while len(out) < target:
        for page in sorted(out, key=lambda p: p.answer, reverse=True):
            halves = page.halves()
            if halves is not None and all(room.fits(fixed + h.given, h.answer)
                                          for h in halves):
                at = out.index(page)
                out[at:at + 1] = list(halves)
                break
        else:
            break
    return out


def _greedy(docs: Sequence[Doc], fixed: int, room: Budget, limit: Optional[int]
            ) -> list[Page]:
    """The docs packed in order, each page filled while it fits the budget
    and, with a `limit`, while its answer is at most that."""
    pages: list[Page] = []
    page = Page()
    held = [0, 0]                   # the page under way's given and answer, kept as it fills

    def fits(on: Page, given: int, answer: int) -> bool:
        on_given, on_answer = held if on is page else (0, 0)
        return ((limit is None or on_answer + answer <= limit)
                and room.fits(fixed + on_given + given, on_answer + answer))

    def add(entry: Entry) -> None:
        page.entries.append(entry)
        held[0] += entry.given
        held[1] += entry.answer

    def close() -> None:
        nonlocal page
        if page.entries:
            pages.append(page)
        page = Page()
        held[:] = [0, 0]

    for doc in docs:
        if not doc.atoms:
            continue
        whole_given = sum(a.given for a in doc.atoms) + doc.context
        whole_answer = sum(a.answer for a in doc.atoms)
        if fits(page, whole_given, whole_answer):
            add(Entry(doc, list(doc.atoms), True))
            continue
        if page.entries and fits(Page(), whole_given, whole_answer):
            close()
            add(Entry(doc, list(doc.atoms), True))
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
            add(Entry(doc, doc.atoms[at:stop], False, context))
            at = stop
            if at < len(doc.atoms):
                close()
    close()
    return pages


def share(atoms: Sequence[Atom], spread: int) -> int:
    """The most one page's answer may take when `spread` workers ask the
    pages at once: an even share of every atom's answer, never less than
    the largest one atom's."""
    if not atoms:
        return 0
    largest = max(a.answer for a in atoms)
    total = sum(a.answer for a in atoms)
    return max(largest, math.ceil(total / max(spread, 1)))


# ── asking the pages ──────────────────────────────────────────────────────

class NotChecked(Exception):
    """A page's answer that did not check (raised by the caller's `ask`)."""


class RateLimited(Exception):
    """The brain refused a call as rate-limited (raised by the caller's
    `ask`): `resets_at`, when the brain says it takes calls again (epoch
    seconds), or None."""

    def __init__(self, why: str, resets_at: Optional[float] = None):
        super().__init__(why)
        self.resets_at = resets_at


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
    rate_limited: int = 0
    waited: float = 0.0             # seconds every worker paused for a rate limit
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
                "rate_limited": self.rate_limited, "waited": round(self.waited, 1),
                "seconds": round(self.seconds, 1), "tokens": used}


class Feed:
    """The pages a run asks, which may go on arriving while it asks them
    (a step's pipeline: a page classified as soon as it is marked). `put`
    each page, `close` once no more will come; a run given a feed ends
    once it is closed and every page put is answered. `free`: the workers
    with nothing to do now, which a page arriving may be spread across."""

    def __init__(self, workers: int = 1):
        self.workers = max(1, workers)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.outstanding = 0
        self.busy = 0
        self.put_pages = 0
        self.closed = False
        self._ended = False

    def free(self) -> int:
        return max(1, self.workers - self.busy - self.queue.qsize())

    def put(self, page: Page) -> None:
        if self._ended:
            return
        self.outstanding += 1
        self.put_pages += 1
        self.queue.put_nowait(page)

    def again(self, page: Page) -> None:
        """A page asked again (or a half of one): answered before the end."""
        self.outstanding += 1
        self.queue.put_nowait(page)

    def close(self) -> None:
        self.closed = True
        if self.outstanding <= 0:
            self.end()

    def answered_one(self) -> None:
        self.outstanding -= 1
        if self.closed and self.outstanding <= 0:
            self.end()

    def end(self) -> None:
        """Every worker told to stop once it has finished its page."""
        if not self._ended:
            self._ended = True
            for _ in range(self.workers):
                self.queue.put_nowait(None)


async def run(pages: list[Page], *, first: Any, make: Callable[[], Awaitable[Any]],
              parallel: int, ask: Callable[[Any, Page], Awaitable[T]],
              failed: Callable[[str], Exception], report: Optional[Report] = None,
              wait_most: Optional[float] = None, feed: Optional[Feed] = None,
              on_answer: Optional[Callable[[Page, T], None]] = None) -> list[T]:
    """Every page's answer, asked by at most `parallel` workers at once,
    each keeping one session across its pages: `first` (already made) for
    the first worker, `make()` for each other. A session is opened before
    its first page and given a new conversation (`fresh`) before each
    after; all are closed at the end. `ask(session, page)` raises
    NotChecked for an answer that does not check: the page is asked again
    once, then split in half, each half asked the same way; a page of one
    atom that still does not check is `failed(why)`. A call refused as
    RateLimited pauses every worker until the brain's reset time, else for
    a wait that doubles with each hit in a row (1, 2, 4 ... s), and asks
    the page again; a pause that would take more than `wait_most` seconds
    (the run's own wall clock) is raised. Any other failure stops every
    worker and is raised.

    `feed`: pages that go on arriving while it runs (`Feed`), asked by
    `parallel` workers as they come, beside `pages`; the run ends once the
    feed is closed and every page is answered. `on_answer(page, answer)`
    is called as each page is answered, so what follows can begin on it
    at once."""
    report = report if report is not None else Report()
    if feed is None:
        if not pages:
            report.pages = 0
            return []
        feed = Feed(min(parallel, len(pages)))
        for p in pages:
            feed.put(p)
        feed.close()
    else:
        feed.workers = max(1, parallel)
        for p in pages:
            feed.put(p)
    started = time.monotonic()
    queue = feed.queue
    results: list[T] = []
    failures: list[BaseException] = []
    workers = feed.workers
    report.workers = workers
    paused_until = 0.0
    in_a_row = 0

    def finish_one() -> None:
        if failures:
            feed.end()
        feed.answered_one()

    async def worker(n: int) -> None:
        nonlocal paused_until, in_a_row
        session, opened = (first if n == 0 else None), False
        try:
            while True:
                page = await queue.get()
                if page is None:
                    return
                feed.busy += 1
                try:
                    if failures:
                        continue
                    if session is None:
                        session = await make()
                    pause = paused_until - time.monotonic()
                    if pause > 0:
                        await asyncio.sleep(pause)
                    if not opened:
                        await session.open()
                        opened = True
                    else:
                        await session.fresh()
                    report.calls += 1
                    try:
                        answer = await ask(session, page)
                        results.append(answer)
                        in_a_row = 0
                        if on_answer is not None:
                            on_answer(page, answer)
                    finally:
                        turn = getattr(session, "last_turn", None)
                        if isinstance(turn, dict):
                            report.turns.append(dict(turn))
                except RateLimited as e:
                    report.rate_limited += 1
                    in_a_row += 1
                    wait = (e.resets_at - time.time() if e.resets_at is not None
                            else float(2 ** (in_a_row - 1)))
                    wait = max(wait, 0.0)
                    if wait_most is not None and wait > wait_most:
                        failures.append(e)
                    else:
                        until = time.monotonic() + wait
                        if until > paused_until:
                            report.waited += until - max(paused_until, time.monotonic())
                            paused_until = until
                        log.info("pages: rate-limited (%s); every worker pauses %.0f s", e, wait)
                        feed.again(page)
                except NotChecked as e:
                    if page.tries == 0:
                        page.tries = 1
                        report.again += 1
                        log.info("pages: a page of %d item(s) did not check (%s); asked again",
                                 len(page.items), e)
                        feed.again(page)
                    else:
                        halves = page.halves()
                        if halves is None:
                            failures.append(failed(str(e)))
                        else:
                            report.halved += 1
                            log.info("pages: a page of %d item(s) did not check again (%s); "
                                     "split in half", len(page.items), e)
                            for half in halves:
                                feed.again(half)
                except BaseException as e:  # noqa: BLE001 - stops every worker, raised below
                    failures.append(e)
                finally:
                    feed.busy -= 1
                    finish_one()
        finally:
            if session is not None and opened:
                await session.close()

    try:
        await asyncio.gather(*(worker(n) for n in range(workers)))
    finally:
        report.seconds = time.monotonic() - started
        report.pages = feed.put_pages
    if failures:
        raise failures[0]
    return results
