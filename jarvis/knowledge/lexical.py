"""The keyword index: BM25 over chunk text (design section 7.9), and over
items: a file's content (its sections) and a record's own words (its text
fields, bounded: `record_text`, for the `RECORD_TYPES` that hold people's
words), so a record is found by its words as it is, with no chunk made of
it (UDS round 3).

Terms come from `spoken_words.folded_words`, the one splitter, so accents
fold and Spanish words are words, less `jarvis_memory`'s stop words and the
Spanish ones below. BM25 with k1 1.2 and b 0.75.

Postings are fixed-width blocks of 128 ordinals and 128 term frequencies
with a skip entry per block (its last ordinal and largest frequency), one
file per generation built from the newest chunks in it; the empty term
holds each chunk's length. As with the general index, a posting counts only
when it comes from the generation holding its chunk's newest version.

A query keeps the top `CANDIDATES` with a WAND-style upper bound per term
(max-score): terms are taken from the highest bound down, and once the
k-th best score beats everything the remaining terms could add, those terms
only score chunks already found, skipping straight to the one block that
can hold each, so a common term never scans its whole list.

What stays resident is columnar: each chunk's length (stored plus one, so
zero means not a live chunk), the live count and the total length. A
term's document frequency is read from its posting counts, which include
superseded versions until a compaction merges them away; it only ever
over-counts by the updates not yet merged.

This index is what finds proper nouns, file names, error codes and
identifiers, which the measured embeddings separate weakly.
"""
from __future__ import annotations

import heapq
import json
import math
import threading
from array import array
from collections import Counter
from typing import Callable, Iterable, Iterator, Optional

from jarvis import spoken_words
from jarvis.jarvis_memory import _STOP_WORDS
from jarvis.knowledge import postings
from jarvis.knowledge.documents import DOC_HEAD, MEM, DocumentStore, decode_doc
from jarvis.knowledge.segment import Version
from jarvis.knowledge.kinds import ITEM
from jarvis.knowledge.store import CONTENT, DOCS, Store
from jarvis.knowledge.wal import Op

LEX = 11
K1 = 1.2
B = 0.75
CANDIDATES = 200
LENGTHS = b""       # the empty term: each chunk's length
INDEXED_KINDS = ("chunk",)
TEXT = "text"

SPANISH_STOP_WORDS = frozenset(
    "el la los las un una unos unas de del y o que en es por para con se su sus al lo como "
    "pero mas este esta estos estas ese esa eso le les me mi mis te tu tus nos ya muy".split())
STOP_WORDS = frozenset(_STOP_WORDS) | SPANISH_STOP_WORDS


def terms(text: str) -> list[str]:
    return [w for w in spoken_words.folded_words(text or "") if len(w) > 1 and w not in STOP_WORDS]


def _chunk_terms(doc: dict) -> Optional[Counter]:
    if doc.get("_kind") not in INDEXED_KINDS:
        return None
    return Counter(terms(str(doc.get(TEXT) or "")))


def content_terms(content: dict) -> Counter:
    """An item's terms: every section's heading and text."""
    found: Counter = Counter()
    for s in content.get("sections") or []:
        found.update(terms(str(s.get("heading") or "")))
        found.update(terms(str(s.get("text") or "")))
    return found


RECORD_FIELD_WORDS = 2000     # characters of one text field a record is found by
RECORD_WORDS = 8000           # characters of all its text fields together
# The record types that hold people's words (UDS round 3's document types):
# found by them as they are. Other records (settings, attention, usage...)
# and an item of any other type are found by their content only.
RECORD_TYPES = frozenset({"turn", "message", "run", "workflow", "work", "package", "project", "thread",
                          "langdoc"})


def record_text(doc: dict, keep=None) -> str:
    """The words a record (an item without content) is found by: each
    text field holding words (a space in it), the first
    `RECORD_FIELD_WORDS` characters of each, `RECORD_WORDS` in all, in
    field order. Bounded, so a record field of 100 KB costs a write what
    2,000 characters cost. `keep(field)` leaves out fields a reader may
    not see."""
    from jarvis.knowledge.kinds import flatten
    out: list[str] = []
    left = RECORD_WORDS
    for path, value in flatten({k: v for k, v in doc.items() if not k.startswith("_")}):
        if left <= 0:
            break
        if not isinstance(value, str) or " " not in value or (keep is not None and not keep(path)):
            continue
        piece = value[:min(RECORD_FIELD_WORDS, left)]
        out.append(piece)
        left -= len(piece)
    return "\n".join(out)


def _item_terms(doc: dict, content: Optional[bytes]) -> Optional[Counter]:
    """An item's terms: its content's (the content stream, written in the
    same transaction as its document) when it has content, else its
    record words (`record_text`); None when it has neither."""
    if doc.get("_kind") != ITEM:
        return None
    if doc.get("_content"):
        return None if content is None else content_terms(json.loads(content))
    if doc.get("_type") not in RECORD_TYPES:
        return None
    found = Counter(terms(record_text(doc)))
    return found or None


def _terms_of(doc: dict, content: Optional[bytes]) -> Optional[Counter]:
    found = _chunk_terms(doc)
    return found if found is not None else _item_terms(doc, content)


def is_item_ordinal(docs: DocumentStore, o: int) -> bool:
    key = docs.columns.key(o)
    return key is not None and key[:1] == b"/"


class LexicalPart:
    name = "keyword_index"
    families = {LEX: "lex"}

    def build(self, gen: int, newest: Callable[[int], Iterator[Version]],
              write: Callable[[int, Iterable[Version]], dict],
              read_family: Callable[[int], Iterator[Version]]) -> None:
        table: dict[str, list[tuple[int, int]]] = {}
        lengths: list[tuple[int, int]] = []
        contents = newest(CONTENT)
        held = next(contents, None)
        for v in newest(DOCS):
            if v.value is None:
                continue
            o, doc = decode_doc(v.value)
            content = None
            if doc.get("_kind") == ITEM:
                # Both families arrive in key order; an item's content is
                # always written with its document, so it is in this
                # generation beside it.
                while held is not None and held.key < v.key:
                    held = next(contents, None)
                if held is not None and held.key == v.key:
                    content = held.value
            counts = _terms_of(doc, content)
            if counts is None:
                continue
            lengths.append((o, sum(counts.values())))
            for term, tf in counts.items():
                table.setdefault(term, []).append((o, tf))
        if not lengths:
            return

        def versions() -> Iterator[Version]:
            lengths.sort()
            yield Version(LENGTHS, 0, postings.encode([o for o, _ in lengths], [n for _, n in lengths]))
            for term in sorted(table):
                rows = sorted(table[term])
                yield Version(term.encode(), 0, postings.encode([o for o, _ in rows], [t for _, t in rows]))
        write(LEX, versions())


class Lexical:
    def __init__(self, docs: DocumentStore) -> None:
        self.docs = docs
        self.store: Store = docs.store
        self.length = array("I", [0])         # chunk length + 1; 0: not a live chunk
        self.live = 0
        self.total = 0
        self.mem: dict[str, dict[int, int]] = {}
        self._mem_terms: dict[int, list[str]] = {}
        self._lock = threading.RLock()
        self._load()
        self.store.listeners.append(self._on_apply)
        self.store.generation_listeners.append(self._on_generation)

    # -- the resident columns ----------------------------------------------
    def _set_length(self, o: int, n: Optional[int]) -> None:
        if o >= len(self.length):
            self.length.extend([0] * (o + 1 - len(self.length)))
        old = self.length[o]
        if old:
            self.live -= 1
            self.total -= old - 1
        if n is None:
            self.length[o] = 0
        else:
            self.length[o] = n + 1
            self.live += 1
            self.total += n

    def _load(self) -> None:
        src = self.docs.columns.src
        for gen, seg in self.store.derived(LEX):
            found = seg.versions(LENGTHS)
            if not found:
                continue
            for o, n in postings.Postings(found[0].value):
                if o < len(src) and src[o] == gen:
                    self._set_length(o, n)
        mems, _ = self.store._sources(DOCS)
        for mem in mems:
            for v in mem.all_versions(DOCS):
                if v.value is None:
                    continue
                o, doc = decode_doc(v.value)
                if src[o] == MEM and self.docs.columns.lsn[o] == v.lsn:
                    content = None
                    if doc.get("_kind") == ITEM:
                        found = mem.versions_of(CONTENT, v.key)
                        content = found[0].value if found and found[0].lsn == v.lsn else None
                    self._index_mem(o, doc, content)

    def _index_mem(self, o: int, doc: dict, content: Optional[bytes] = None) -> None:
        self._unindex_mem(o)
        counts = _terms_of(doc, content)
        if counts is None:
            self._set_length(o, None)
            return
        for term, tf in counts.items():
            self.mem.setdefault(term, {})[o] = tf
        self._mem_terms[o] = list(counts)
        self._set_length(o, sum(counts.values()))

    def _unindex_mem(self, o: int) -> None:
        for term in self._mem_terms.pop(o, ()):
            group = self.mem.get(term)
            if group is not None:
                group.pop(o, None)
                if not group:
                    del self.mem[term]

    def _on_apply(self, lsn: int, ops: list[Op]) -> None:
        with self._lock:
            contents = {op.key: op.value for op in ops if op.family == CONTENT}
            for op in ops:
                if op.family != DOCS:
                    continue
                if op.value is not None:
                    o, doc = decode_doc(op.value)
                    self._index_mem(o, doc, contents.get(op.key))
                else:
                    o = self.docs._ord_from_store(op.key)
                    if o is not None:
                        self._unindex_mem(o)
                        self._set_length(o, None)

    def _on_generation(self, gen: int, replaced: list[int]) -> None:
        src = self.docs.columns.src
        with self._lock:
            for o in [o for o in self._mem_terms if src[o] != MEM]:
                self._unindex_mem(o)
            for o in range(len(self.length)):
                if self.length[o] and o < len(src) and src[o] == 0:
                    self._set_length(o, None)

    # -- searching ---------------------------------------------------------
    def _lists(self, term: str) -> tuple[list[tuple[int, postings.Postings]], dict[int, int]]:
        found = []
        for gen, seg in self.store.derived(LEX):
            for v in seg.versions(term.encode()):
                found.append((gen, postings.Postings(v.value)))
        with self._lock:
            mem = dict(self.mem.get(term, {}))
        return found, mem

    def idf(self, df: int) -> float:
        n = max(self.live, 1)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def _tf_part(self, tf: int, dl: int, avgdl: float) -> float:
        return tf * (K1 + 1.0) / (tf + K1 * (1.0 - B + B * dl / avgdl))

    def search(self, text: str, k: int = CANDIDATES, within: Optional[set[int]] = None,
               items: bool = False) -> list[tuple[int, float, dict]]:
        """The top `k` chunks (or, with `items`, items) for `text`: (ordinal,
        score, the score of each query term that matched)."""
        query = list(dict.fromkeys(terms(text)))
        if not query or not self.live:
            return []
        avgdl = max(self.total / self.live, 1e-9)
        src = self.docs.columns.src
        length = self.length
        plans = []
        for term in query:
            lists, mem = self._lists(term)
            df = sum(p.count for _, p in lists) + len(mem)
            if df == 0:
                continue
            idf = self.idf(df)
            max_tf = max([p.max_tf for _, p in lists] + list(mem.values()) or [0])
            bound = idf * self._tf_part(max_tf, 0, avgdl)
            plans.append((bound, term, idf, lists, mem))
        plans.sort(key=lambda p: -p[0])
        remaining = [sum(p[0] for p in plans[i:]) for i in range(len(plans) + 1)]
        scores: dict[int, float] = {}
        why: dict[int, dict] = {}
        top: list[float] = []

        def add(o: int, tf: int, term: str, idf: float) -> None:
            if within is not None and o not in within:
                return
            if is_item_ordinal(self.docs, o) != items:
                return
            s = idf * self._tf_part(tf, length[o] - 1, avgdl)
            scores[o] = scores.get(o, 0.0) + s
            why.setdefault(o, {})[term] = round(s, 4)

        for i, (bound, term, idf, lists, mem) in enumerate(plans):
            threshold = top[-1] if len(top) >= k else 0.0
            essential = len(top) < k or remaining[i] > threshold
            if essential:
                for o, tf in mem.items():
                    if o < len(src) and src[o] == MEM:
                        add(o, tf, term, idf)
                for gen, plist in lists:
                    for b in range(plist.blocks):
                        ords, tfs = plist.block(b)
                        for o, tf in zip(ords, tfs):
                            if src[o] == gen:
                                add(o, tf, term, idf)
            else:
                # Only chunks already found can still rise; reach each one's
                # block through the skip table.
                for o in list(scores):
                    if src[o] == MEM:
                        tf = mem.get(o)
                        if tf:
                            add(o, tf, term, idf)
                        continue
                    for gen, plist in lists:
                        if src[o] != gen:
                            continue
                        for b, (last, _) in enumerate(plist.skips):
                            if o <= last:
                                ords, tfs = plist.block(b)
                                j = _index(ords, o)
                                if j is not None:
                                    add(o, tfs[j], term, idf)
                                break
            top = heapq.nlargest(k, scores.values())
        best = heapq.nlargest(k, scores.items(), key=lambda kv: (kv[1], -kv[0]))
        return [(o, s, why[o]) for o, s in best]


    def holding(self, term: str, items: bool = True) -> set[int]:
        """Every live ordinal whose text holds `term` (a folded word)."""
        src = self.docs.columns.src
        lists, mem = self._lists(term)
        out = {o for o, _ in mem.items() if o < len(src) and src[o] == MEM}
        for gen, plist in lists:
            for o in plist.ordinals():
                if o < len(src) and src[o] == gen:
                    out.add(o)
        return {o for o in out if is_item_ordinal(self.docs, o) == items}

    def phrase_candidates(self, phrase: str) -> Optional[set[int]]:
        """The items holding every indexed word of `phrase` (its rarest word
        first), which a phrase check then confirms; None when the phrase has
        no indexed word (stop words and single letters only)."""
        words = sorted(set(terms(phrase)))
        if not words:
            return None
        found: Optional[set[int]] = None
        for word in sorted(words, key=lambda w: sum(p.count for _, p in self._lists(w)[0]) + len(self._lists(w)[1])):
            have = self.holding(word)
            found = have if found is None else found & have
            if not found:
                return set()
        return found or set()


def phrase_in(phrase: str, text: str) -> bool:
    """Whether the folded words of `phrase` occur in `text` one after
    another: an exact phrase, accents and case folded, every word kept."""
    want = spoken_words.folded_words(phrase or "")
    if not want:
        return False
    have = spoken_words.folded_words(text or "")
    n = len(want)
    first = want[0]
    for i in range(len(have) - n + 1):
        if have[i] == first and have[i:i + n] == want:
            return True
    return False


def _index(ords: array, o: int) -> Optional[int]:
    lo, hi = 0, len(ords)
    while lo < hi:
        mid = (lo + hi) // 2
        if ords[mid] < o:
            lo = mid + 1
        else:
            hi = mid
    return lo if lo < len(ords) and ords[lo] == o else None
