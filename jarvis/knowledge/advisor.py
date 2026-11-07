"""Extra indexes that tune themselves, and the advisor that proposes them
(design section 7.17.3).

The general index answers every query correctly; an extra index only makes
some query shapes faster, and a query gives the same answer whichever extra
indexes exist (a test runs the same queries with all, none and each alone).

Every query's shape is recorded, never its values: its kinds, each
condition's property and operator, and its sort, with how long it took, how
many documents it read and how many it returned. At most `SHAPES_MAX`
shapes are kept, each with hourly run counts for `SHAPE_DAYS` days and its
last `DURATIONS_KEPT` timings. Because a shape holds no values, it holds
nothing `forget` has to remove.

The advisor runs while the pace is free, at most every `ADVISE_EVERY_SEC`,
and proposes a composite index for a shape that ran at least `MIN_RUNS`
times in the last day, whose 95th percentile passed `MIN_P95_MS`, and which
read at least `MIN_READ_RATIO` documents for each one it returned, when it
filters on two or more single-valued properties for equality. Guardrails:
at most `PER_KIND_MAX` extra indexes per kind and `TOTAL_MAX` in all, and
their files together at most `BYTES_MAX`. An index not used for
`UNUSED_DAYS` days is dropped.

A definition is logged in the store's own family (`META`, `xidx:defs`), so
it survives a restart and the compaction child reads it; a new one is
followed by a flush and a compaction of every generation, which builds it
everywhere at once. Each generation's file records which definitions it
holds, and an index is used only when every generation holds it; documents
not yet flushed are always checked directly.

He has the last say: an index he pinned is kept even when unused (and made
when it is not there yet), and one he forbade is never made and is dropped
if it is. His choices live in Markdown (knowledge_links: knowledge.md), are
handed to the engine by ingest on every pass and by the change itself
(`set_choices`, kept in `xidx:choices`), and are read here by each
index's key, `<kinds>|<properties>`, which a rebuild keeps.
"""
from __future__ import annotations

import json
import struct
import threading
import time
from typing import Callable, Iterable, Iterator, Optional

from jarvis.knowledge import encoding, kinds, postings
from jarvis.knowledge.documents import MEM, DocumentStore, Predicate, decode_doc
from jarvis.knowledge.segment import Version
from jarvis.knowledge.store import DOCS, META, Store
from jarvis.knowledge.wal import Op

XIDX = 12
DEFS_KEY = b"xidx:defs"
SHAPES_KEY = b"xidx:shapes"
CHOICES_KEY = b"xidx:choices"
SHAPES_MAX = 1000
SHAPE_DAYS = 14
DURATIONS_KEPT = 200
MIN_RUNS = 50
MIN_P95_MS = 20.0
MIN_READ_RATIO = 20
PER_KIND_MAX = 8
TOTAL_MAX = 32
BYTES_MAX = 64 << 20
UNUSED_DAYS = 14
ADVISE_EVERY_SEC = 3600.0
ID = struct.Struct(">H")
LEN = struct.Struct(">H")


def _defs_from(versions: Iterable[Version]) -> list[dict]:
    for v in versions:
        if v.key == DEFS_KEY and v.value:
            return json.loads(v.value)
    return []


def index_key(kinds_: list[str], paths: list[str]) -> str:
    return ",".join(sorted(kinds_)) + "|" + ",".join(sorted(paths))


def parse_index_key(key: str) -> tuple[list[str], list[str]]:
    kinds_part, _, paths_part = key.partition("|")
    return sorted(kinds_part.split(",")), sorted(paths_part.split(","))


def single_values(doc: dict, path: str) -> Optional[list]:
    """The one value of a single-valued property, as a list of one; None when
    it is missing or many (an array path)."""
    if "[]" in path:
        return None
    found = [v for p, v in kinds.flatten(doc) if p == path]
    return found if len(found) == 1 else None


def entry_key(index_id: int, values: list, date_paths: list[bool]) -> Optional[bytes]:
    out = bytearray(b"X" + ID.pack(index_id))
    for value, date in zip(values, date_paths):
        try:
            enc = encoding.encode_value(value, date=date)
        except (TypeError, ValueError):
            return None
        out += LEN.pack(len(enc)) + enc
    return bytes(out)


def _dates(d: dict) -> list[bool]:
    targets = [kinds.kind_of(k) for k in d["kinds"]]
    return [any(kinds.root_of(p) in k.dates for k in targets) for p in d["paths"]]


class ExtraIndexPart:
    """Builds a generation's extra index file: for each definition, an entry
    per document of its kinds, and a `D` key saying the generation holds it."""
    name = "extra_indexes"
    families = {XIDX: "xidx"}

    def build(self, gen: int, newest: Callable[[int], Iterator[Version]],
              write: Callable[[int, Iterable[Version]], dict],
              read_family: Callable[[int], Iterator[Version]]) -> None:
        defs = _defs_from(read_family(META))
        if not defs:
            return
        table: dict[bytes, list[int]] = {}
        dates = {d["id"]: _dates(d) for d in defs}
        for v in newest(DOCS):
            if v.value is None:
                continue
            o, doc = decode_doc(v.value)
            for d in defs:
                if doc.get("_kind") not in d["kinds"]:
                    continue
                values = []
                for path in d["paths"]:
                    one = single_values(doc, path)
                    if one is None:
                        break
                    values.append(one[0])
                else:
                    key = entry_key(d["id"], values, dates[d["id"]])
                    if key is not None:
                        table.setdefault(key, []).append(o)
        rows = [(b"D" + ID.pack(d["id"]), b"") for d in defs]
        rows += [(k, postings.encode(sorted(table[k]))) for k in table]
        write(XIDX, (Version(k, 0, val) for k, val in sorted(rows)))


class Advisor:
    def __init__(self, docs: DocumentStore, clock: Callable[[], float] = time.time) -> None:
        self.docs = docs
        self.store: Store = docs.store
        self.clock = clock
        self._lock = threading.RLock()
        self.shapes: dict[str, dict] = {}
        self.defs: list[dict] = _defs_from(self.store.prefix(META, DEFS_KEY))
        self._load_shapes()
        raw = self.store.value(META, CHOICES_KEY)
        chosen = json.loads(raw) if raw else {}
        self.pinned: set[str] = set(chosen.get("pinned") or [])
        self.forbidden: set[str] = set(chosen.get("forbidden") or [])
        self.last_advice: Optional[dict] = None
        self._advised_at = 0.0
        docs.extra_lookup = self.lookup
        docs.shape_hook = self.record

    # -- shapes ---------------------------------------------------------------
    def _load_shapes(self) -> None:
        v = self.store.value(META, SHAPES_KEY)
        if v:
            try:
                self.shapes = json.loads(v)
            except ValueError:
                self.shapes = {}

    @staticmethod
    def shape_of(kind_names, preds: list[Predicate], order) -> str:
        conds = sorted({(p.path, p.op) for p in preds if p.path != "_kind"})
        return json.dumps({"kinds": sorted(kind_names or []), "where": conds,
                           "order": order[0] if order else None}, separators=(",", ":"))

    def record(self, kind_names, preds, order, took_ms: float, read: int, returned: int) -> None:
        key = self.shape_of(kind_names, preds, order)
        hour = int(self.clock() // 3600)
        with self._lock:
            s = self.shapes.get(key)
            if s is None:
                if len(self.shapes) >= SHAPES_MAX:
                    oldest = min(self.shapes, key=lambda k: self.shapes[k]["last"])
                    del self.shapes[oldest]
                s = self.shapes[key] = {"hours": {}, "ms": [], "read": 0, "returned": 0, "last": 0}
            s["hours"][str(hour)] = s["hours"].get(str(hour), 0) + 1
            s["ms"] = (s["ms"] + [round(took_ms, 3)])[-DURATIONS_KEPT:]
            s["read"] += read
            s["returned"] += returned
            s["last"] = hour
            for h in [h for h in s["hours"] if int(h) < hour - SHAPE_DAYS * 24]:
                del s["hours"][h]

    def save(self) -> None:
        """Write the shapes and the definitions' use counts beside the data."""
        with self._lock:
            ops = [Op(META, SHAPES_KEY, encoding.canonical(self.shapes))]
            if self.defs:
                ops.append(Op(META, DEFS_KEY, encoding.canonical(self.defs)))
        self.store.commit(ops)

    def set_choices(self, pinned: Iterable[str], forbidden: Iterable[str]) -> bool:
        """His pins and forbids, as the settings note holds them; True when
        they changed (the next advice then applies them)."""
        pinned, forbidden = set(pinned), set(forbidden)
        with self._lock:
            if pinned == self.pinned and forbidden == self.forbidden:
                return False
            self.pinned, self.forbidden = pinned, forbidden
        self.store.commit([Op(META, CHOICES_KEY, encoding.canonical(
            {"pinned": sorted(pinned), "forbidden": sorted(forbidden)}))])
        self._advised_at = 0.0
        return True

    # -- the advice --------------------------------------------------------------
    def _runs_last_day(self, s: dict) -> int:
        hour = int(self.clock() // 3600)
        return sum(n for h, n in s["hours"].items() if int(h) > hour - 24)

    @staticmethod
    def _p95(ms: list[float]) -> float:
        if not ms:
            return 0.0
        ordered = sorted(ms)
        return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]

    def candidates(self) -> list[dict]:
        out = []
        with self._lock:
            shapes = dict(self.shapes)
        for key, s in shapes.items():
            shape = json.loads(key)
            eq = sorted({p for p, op in shape["where"] if op == "=" and "[]" not in p})
            if not shape["kinds"] or len(eq) < 2:
                continue
            runs = self._runs_last_day(s)
            ratio = s["read"] / max(1, s["returned"])
            p95 = self._p95(s["ms"])
            if runs >= MIN_RUNS and p95 > MIN_P95_MS and ratio >= MIN_READ_RATIO:
                out.append({"kinds": shape["kinds"], "paths": eq, "runs": runs, "p95_ms": p95,
                            "read_per_returned": round(ratio, 1), "key": index_key(shape["kinds"], eq)})
        out.sort(key=lambda c: -(c["runs"] * c["p95_ms"]))
        return out

    def _bytes(self) -> int:
        return sum(int(e["bytes"]) for e in self.store.manifest.segments if int(e["family"]) == XIDX)

    def advise(self, *, rebuild: Callable[[], None]) -> dict:
        """Drop what went unused, add what the shapes ask for within the
        guardrails, and rebuild every generation when anything changed."""
        now = self.clock()
        added, dropped = [], []
        with self._lock:
            keep = []
            for d in self.defs:
                key = index_key(d["kinds"], d["paths"])
                unused = now - max(d.get("last_used") or 0, d["created"]) > UNUSED_DAYS * 86400
                if key in self.forbidden or (unused and key not in self.pinned):
                    dropped.append(d)
                else:
                    keep.append(d)
            self.defs = keep
            room = self._bytes() < BYTES_MAX
            have = {index_key(d["kinds"], d["paths"]) for d in self.defs}
            wanted = [{"kinds": k, "paths": p, "runs": 0, "p95_ms": 0.0, "read_per_returned": 0.0, "pinned": True}
                      for k, p in (parse_index_key(x) for x in sorted(self.pinned - have - self.forbidden))]
            for c in wanted + self.candidates():
                if index_key(c["kinds"], c["paths"]) in self.forbidden:
                    continue
                if not room or len(self.defs) >= TOTAL_MAX:
                    break
                if any(d["kinds"] == c["kinds"] and d["paths"] == c["paths"] for d in self.defs):
                    continue
                if any(sum(1 for d in self.defs if k in d["kinds"]) >= PER_KIND_MAX for k in c["kinds"]):
                    continue
                if any(k not in kinds.KINDS for k in c["kinds"]):
                    continue
                new_id = max([d["id"] for d in self.defs] + [0]) + 1
                d = {"id": new_id, "kinds": c["kinds"], "paths": c["paths"], "created": now,
                     "last_used": None, "uses": 0, "why": {k: c[k] for k in ("runs", "p95_ms", "read_per_returned")}}
                self.defs.append(d)
                added.append(d)
            defs = list(self.defs)
        if added or dropped:
            self.store.commit([Op(META, DEFS_KEY, encoding.canonical(defs))])
            rebuild()
        self._advised_at = time.monotonic()
        self.last_advice = {"added": [d["id"] for d in added], "dropped": [d["id"] for d in dropped],
                            "indexes": len(defs)}
        return self.last_advice

    def due(self) -> bool:
        return time.monotonic() - self._advised_at >= ADVISE_EVERY_SEC

    # -- using them ----------------------------------------------------------------
    def _held_everywhere(self, index_id: int) -> Optional[list]:
        """The extra index segments per DOCS generation, or None when some
        generation does not hold this definition."""
        doc_gens = {g for g, _ in self.store.segments.get(DOCS, [])}
        segs = {g: s for g, s in self.store.derived(XIDX)}
        marker = b"D" + ID.pack(index_id)
        for g in doc_gens:
            seg = segs.get(g)
            if seg is None or not seg.versions(marker):
                return None
        return [(g, segs[g]) for g in doc_gens]

    def lookup(self, preds: list[Predicate], kind_names: Optional[list[str]]):
        """(estimate, builder) from the extra index that serves these
        conditions, or None."""
        if not kind_names or not self.defs:
            return None
        wanted = sorted(kind_names)
        eq = {p.path: p.value for p in preds if p.op == "="}
        for d in self.defs:
            if d["kinds"] != wanted or not all(path in eq for path in d["paths"]):
                continue
            segs = self._held_everywhere(d["id"])
            if segs is None:
                continue
            key = entry_key(d["id"], [eq[p] for p in d["paths"]], _dates(d))
            if key is None:
                continue
            lists = []
            for gen, seg in segs:
                for v in seg.versions(key):
                    lists.append((gen, v.value))
            src = self.docs.columns.src
            mem = [o for o, s in enumerate(src) if s == MEM]
            estimate = sum(postings.count(data) for _, data in lists) + len(mem)
            d["uses"] = int(d.get("uses") or 0) + 1
            d["last_used"] = self.clock()

            def build(lists=lists, mem=mem) -> set[int]:
                out = set(mem)
                for gen, data in lists:
                    for o in postings.Postings(data).ordinals():
                        if o < len(src) and src[o] == gen:
                            out.add(o)
                return out
            return estimate, build
        return None

    def health(self) -> dict:
        def described(d: dict) -> dict:
            key = index_key(d["kinds"], d["paths"])
            return {**{k: d.get(k) for k in ("id", "kinds", "paths", "uses", "why")}, "key": key,
                    "pinned": key in self.pinned, "forbidden": key in self.forbidden}
        return {"indexes": [described(d) for d in self.defs],
                "pinned": sorted(self.pinned), "forbidden": sorted(self.forbidden),
                "shapes": len(self.shapes), "bytes": self._bytes(), "last_advice": self.last_advice}
