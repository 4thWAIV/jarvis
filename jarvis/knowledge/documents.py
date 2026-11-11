"""The document store and its general index (design sections 7.17.1 and
7.17.2).

A document is a JSON object with a key, a kind and properties; every graph
node is one. Its system properties (`_key`, `_kind`, `_label`, `_status`,
`_trust`, `_owner`, `_ns`, `_source`, `_rev`, `_hash`) are written by the
engine's callers under the reserved `_` prefix; `_lsn` and `_ord` are the
engine's own and are only ever read. A legacy document's property its kind
does not declare is refused (I8), and a property that names another
document is written as a graph edge, never kept. An item (a document keyed
by its path, `kinds.ITEM`) holds fields of any shape, every one indexed;
`items.py` writes it.

Every document has an ordinal, a 32-bit number given on first write,
written in its `doc_put` record so recovery replays it, and kept for the
life of the engine directory. Ordinals are what the parts share: the
general index, the keyword index and the vector rows all hold ordinals.

The general index holds every property of every document in one sorted key
space, `<field id><encoded value>` to a posting list of ordinals, with
nested properties by full path and array elements under `name[]`. A kind
may index a short list instead (chunks: their system properties,
`language` and `heading_path[]`). The structural indexes are the same
index's entries on the system properties, which every kind always indexes.

Index entries are not logged. The unflushed documents are indexed in
memory; each generation's index file is built from the newest documents in
that generation, at flush and again at compaction. An entry counts only
when it comes from the generation that holds its document's newest
version, which the resident column `src` says, so stale entries in older
generations are never trusted. Every answer is checked against the
document itself, so a cut string or a range edge never lets a wrong
document through.
"""
from __future__ import annotations

import hashlib
import json
import logging
import struct
import threading
import time
from array import array
from typing import Any, Callable, Iterable, Iterator, Optional

from jarvis import spoken_words
from jarvis.knowledge import encoding, kinds, postings
from jarvis.knowledge.segment import Version
from jarvis.knowledge.store import DOCS, FIELDS, Store, prefix_end
from jarvis.knowledge.wal import Op

log = logging.getLogger("jarvis.knowledge.documents")

IDX = 10
MEM = 0xFFFFFFFF
DOC_HEAD = struct.Struct("<I")
FID = struct.Struct(">I")
FIELDS_MAX = 65536
OPS = ("=", "!=", "<", "<=", ">", ">=", "in", "prefix", "exists")
WHERE_MAX = 8


DocumentError = kinds.SchemaError   # one refusal for the caller's mistakes


def encode_doc(ordinal: int, doc: dict) -> bytes:
    return DOC_HEAD.pack(ordinal) + encoding.canonical(doc)


def decode_doc(value: bytes) -> tuple[int, dict]:
    return DOC_HEAD.unpack_from(value, 0)[0], json.loads(value[DOC_HEAD.size:])


class Columns:
    """Per-ordinal resident state, columnar: the generation holding each
    document's newest version (`src`: 0 none, MEM unflushed), that
    version's LSN, and the key, in one byte blob with offsets."""

    def __init__(self) -> None:
        self.src = array("I", [0])
        self.lsn = array("Q", [0])
        self.key_off = array("Q", [0])
        self.key_len = array("H", [0])
        self.blob = bytearray()

    def ensure(self, ordinal: int) -> None:
        grow = ordinal + 1 - len(self.src)
        if grow > 0:
            self.src.extend([0] * grow)
            self.lsn.extend([0] * grow)
            self.key_off.extend([0] * grow)
            self.key_len.extend([0] * grow)

    def set_key(self, ordinal: int, key: bytes) -> None:
        self.ensure(ordinal)
        if self.key_len[ordinal] and self.key(ordinal) == key:
            return
        self.key_off[ordinal] = len(self.blob)
        self.key_len[ordinal] = len(key)
        self.blob += key

    def key(self, ordinal: int) -> Optional[bytes]:
        if ordinal >= len(self.src) or not self.key_len[ordinal]:
            return None
        off = self.key_off[ordinal]
        return bytes(self.blob[off:off + self.key_len[ordinal]])

    def live(self) -> Iterator[int]:
        for o, src in enumerate(self.src):
            if src:
                yield o

    def resident_bytes(self) -> int:
        return (self.src.itemsize * len(self.src) + self.lsn.itemsize * len(self.lsn)
                + self.key_off.itemsize * len(self.key_off)
                + self.key_len.itemsize * len(self.key_len) + len(self.blob))


def index_entries(doc: dict, fields: dict[str, int]) -> Iterator[bytes]:
    """The general index keys of one document (only fields that have IDs)."""
    kind = kinds.schema_of(doc)
    for path, value in kinds.flatten(doc):
        if path in kinds.READ_ONLY or not kinds.is_indexed(kind, path):
            continue
        fid = fields.get(path)
        if fid is None:
            continue
        try:
            enc = encoding.encode_value(value, date=kinds.root_of(path) in kind.dates)
        except (TypeError, ValueError):
            continue
        yield FID.pack(fid) + enc


def needed_fields(doc: dict) -> set[str]:
    kind = kinds.schema_of(doc)
    return {path for path, _ in kinds.flatten(doc)
            if path not in kinds.READ_ONLY and kinds.is_indexed(kind, path)}


class IndexPart:
    """Builds a generation's general index file from its newest documents."""
    name = "general_index"
    families = {IDX: "idx"}

    def build(self, gen: int, newest: Callable[[int], Iterator[Version]],
              write: Callable[[int, Iterable[Version]], dict],
              read_family: Callable[[int], Iterator[Version]]) -> None:
        fields = {v.key.decode(): FID.unpack(v.value)[0] for v in read_family(FIELDS)}
        table: dict[bytes, list[int]] = {}
        for v in newest(DOCS):
            if v.value is None:
                continue
            ordinal, doc = decode_doc(v.value)
            for entry in set(index_entries(doc, fields)):
                table.setdefault(entry, []).append(ordinal)
        if table:
            write(IDX, (Version(k, 0, postings.encode(sorted(table[k]))) for k in sorted(table)))


class Predicate:
    def __init__(self, path: str, op: str, value: Any = None) -> None:
        if op not in OPS:
            raise DocumentError(f"unknown operator {op!r}")
        if op == "in" and not isinstance(value, list):
            raise DocumentError("`in` takes a list")
        self.path, self.op, self.value = path, op, value

    @classmethod
    def parse(cls, raw) -> "Predicate":
        if not isinstance(raw, (list, tuple)) or len(raw) not in (2, 3):
            raise DocumentError(f"a condition is [path, op, value]: {raw!r}")
        return cls(raw[0], raw[1], raw[2] if len(raw) == 3 else None)

    def _norm(self, x):
        return spoken_words.fold(x) if isinstance(x, str) else x

    def matches(self, doc: dict, date: bool) -> bool:
        values = [v for p, v in kinds.flatten(doc) if p == self.path]
        if self.op == "exists":
            return bool(values) if self.value in (None, True) else not values
        if self.op == "!=":
            return all(not self._eq(v, self.value, date) for v in values)
        return any(self._one(v, date) for v in values)

    def _key(self, x, date):
        if date:
            try:
                return ("d", encoding.date_millis(x))
            except (TypeError, ValueError):
                return None
        if isinstance(x, bool) or x is None:
            return ("b", x)
        if isinstance(x, (int, float)):
            return ("n", x)
        if isinstance(x, str):
            return ("s", self._norm(x))
        return None

    def _eq(self, a, b, date) -> bool:
        ka, kb = self._key(a, date), self._key(b, date)
        return ka is not None and ka == kb

    def _one(self, v, date) -> bool:
        if self.op == "=":
            return self._eq(v, self.value, date)
        if self.op == "in":
            return any(self._eq(v, x, date) for x in self.value)
        if self.op == "prefix":
            return isinstance(v, str) and isinstance(self.value, str) and \
                self._norm(v).startswith(self._norm(self.value))
        kv, kq = self._key(v, date), self._key(self.value, date)
        if kv is None or kq is None or kv[0] != kq[0] or kv[0] == "b":
            return False
        return {"<": kv[1] < kq[1], "<=": kv[1] <= kq[1],
                ">": kv[1] > kq[1], ">=": kv[1] >= kq[1]}[self.op]

    def ranges(self, fid: int, date: bool) -> Optional[list[tuple[bytes, Optional[bytes]]]]:
        """Key ranges of the general index this predicate selects from, or
        None when the index cannot answer it."""
        base = FID.pack(fid)
        if self.op == "!=" or (self.op == "exists" and self.value is False):
            return None
        if self.op == "exists":
            return [(base, FID.pack(fid + 1))]
        try:
            if self.op == "=":
                k = base + encoding.encode_value(self.value, date=date)
                return [(k, k + b"\x00")]
            if self.op == "in":
                out = []
                for x in self.value:
                    k = base + encoding.encode_value(x, date=date)
                    out.append((k, k + b"\x00"))
                return out
            if self.op == "prefix":
                start = base + bytes([encoding.STRING]) + encoding.fold_string(str(self.value))
                return [(start, prefix_end(start))]
            enc = encoding.encode_value(self.value, date=date)
        except (TypeError, ValueError):
            return None
        lo, hi = base + bytes([enc[0]]), base + bytes([enc[0] + 1])
        k = base + enc
        return [{"<": (lo, k), "<=": (lo, prefix_end(k)), ">": (prefix_end(k), hi),
                 ">=": (k, hi)}[self.op]]


class DocumentStore:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.columns = Columns()
        self.fields: dict[str, int] = {}
        self.fields_full = False   # past FIELDS_MAX, new fields go unindexed
        self.next_ord = 1
        self._lock = threading.RLock()
        self._pending: dict[bytes, int] = {}
        self.mem_index: dict[bytes, set[int]] = {}
        self._mem_entries: dict[int, list[bytes]] = {}
        # Parts add operations to a document's write: (raw, stored, ordinal,
        # pending) -> ops for a put, (key, ordinal) -> ops for a delete.
        self.put_hooks: list[Callable[[dict, dict, int, dict], list[Op]]] = []
        self.delete_hooks: list[Callable[[bytes, Optional[int]], list[Op]]] = []
        self.forget_hooks: list[Callable[[set[bytes]], dict[int, set[bytes]]]] = []
        # The index advisor: offered first to answer a set of conditions from
        # an extra index, and told the shape of every query (never its values).
        self.extra_lookup: Optional[Callable[[list, Optional[list[str]]], Optional[tuple]]] = None
        self.shape_hook: Optional[Callable[..., None]] = None
        self.last_read = 0
        self._load()
        store.listeners.append(self._on_apply)
        store.generation_listeners.append(self._on_generation)

    # -- loading ----------------------------------------------------------
    def _load(self) -> None:
        for v in self.store.scan(FIELDS):
            self.fields[v.key.decode()] = FID.unpack(v.value)[0]
        self.fields_full = len(self.fields) >= FIELDS_MAX
        col = self.columns
        key_ord: dict[bytes, int] = {}
        tombstones: list[tuple[bytes, int, int]] = []

        def take(v: Version, src: int) -> None:
            if v.value is None:
                tombstones.append((v.key, v.lsn, src))
                return
            o = DOC_HEAD.unpack_from(v.value, 0)[0]
            key_ord[v.key] = o
            col.ensure(o)
            if v.lsn >= col.lsn[o]:
                col.lsn[o], col.src[o] = v.lsn, src
                col.set_key(o, v.key)
            self.next_ord = max(self.next_ord, o + 1)

        mems, segs = self.store._sources(DOCS)
        for gen, seg in segs:
            for v in seg.scan():
                take(v, gen)
        for mem in mems:
            for v in mem.all_versions(DOCS):
                take(v, MEM)
        for key, lsn, src in tombstones:
            o = key_ord.get(key)
            if o is not None and lsn > col.lsn[o]:
                col.lsn[o], col.src[o] = lsn, 0
        for mem in mems:
            for v in mem.all_versions(DOCS):
                if v.value is not None:
                    o, doc = decode_doc(v.value)
                    if col.src[o] == MEM and col.lsn[o] == v.lsn:
                        self._index_mem(o, doc)

    # -- keeping the columns current -------------------------------------
    def _on_apply(self, lsn: int, ops: list[Op]) -> None:
        with self._lock:
            for op in ops:
                if op.family == FIELDS:
                    self.fields[op.key.decode()] = FID.unpack(op.value)[0]
                elif op.family == DOCS:
                    if op.value is not None:
                        o, doc = decode_doc(op.value)
                        self.columns.ensure(o)
                        self.columns.src[o], self.columns.lsn[o] = MEM, lsn
                        self.columns.set_key(o, op.key)
                        self.next_ord = max(self.next_ord, o + 1)
                        self._index_mem(o, doc)
                    else:
                        o = self._ord_from_store(op.key)
                        if o is not None:
                            self.columns.src[o], self.columns.lsn[o] = 0, lsn
                            self._unindex_mem(o)

    def _index_mem(self, o: int, doc: dict) -> None:
        self._unindex_mem(o)
        entries = list(set(index_entries(doc, self.fields)))
        for e in entries:
            self.mem_index.setdefault(e, set()).add(o)
        self._mem_entries[o] = entries

    def _unindex_mem(self, o: int) -> None:
        for e in self._mem_entries.pop(o, ()):
            group = self.mem_index.get(e)
            if group is not None:
                group.discard(o)
                if not group:
                    del self.mem_index[e]

    def _on_generation(self, gen: int, replaced: list[int]) -> None:
        segs = [s for g, s in self.store.segments.get(DOCS, []) if g == gen]
        col = self.columns
        with self._lock:
            for seg in segs:
                for v in seg.scan():
                    if v.value is None:
                        continue
                    o = DOC_HEAD.unpack_from(v.value, 0)[0]
                    if o < len(col.src) and col.lsn[o] == v.lsn and col.src[o] != 0:
                        if col.src[o] == MEM:
                            self._unindex_mem(o)
                        col.src[o] = gen
            if replaced:
                gone = set(replaced)
                for o, src in enumerate(col.src):
                    if src in gone:
                        col.src[o] = 0

    def _ord_from_store(self, key: bytes) -> Optional[int]:
        for v in self.store.versions(DOCS, key):
            if v.value is not None:
                return DOC_HEAD.unpack_from(v.value, 0)[0]
        return None

    # -- writing ----------------------------------------------------------
    def _check(self, doc: dict) -> dict:
        if not isinstance(doc, dict):
            raise DocumentError("a document is an object")
        key, kind_name = doc.get("_key"), doc.get("_kind")
        if not isinstance(key, str) or not key or len(key.encode()) > 1024:
            raise DocumentError("_key is a non-empty string of at most 1,024 bytes")
        item = kinds.is_item_key(key)
        if item != (kind_name == kinds.ITEM):
            raise DocumentError("a key that is a path holds an item, and only a path does")
        kind = kinds.OPEN if item else kinds.kind_of(kind_name)
        system = kinds.SYSTEM + kinds.ITEM_SYSTEM if item else kinds.SYSTEM
        for name in doc:
            if name in kinds.READ_ONLY:
                raise DocumentError(f"{name} is the engine's own")
            if name.startswith("_") and name not in system:
                raise DocumentError(f"{name}: names starting with _ are reserved")
            if not item and not name.startswith("_") and name not in kind.properties:
                raise DocumentError(f"a {kind.name} has no property {name!r}")
        trust = doc.get("_trust", "own")
        if trust not in kinds.TRUST:
            raise DocumentError("_trust is own or untrusted")
        out = {k: v for k, v in doc.items() if k not in kind.references}
        out.setdefault("_trust", "own")
        out.setdefault("_ns", "local")
        out.setdefault("_owner", "local")
        if "_hash" not in out:
            body = {k: v for k, v in out.items() if not k.startswith("_")}
            out["_hash"] = hashlib.sha256(encoding.canonical(body)).hexdigest()
        for path, value in kinds.flatten(out):
            if isinstance(value, float) and value != value:
                raise DocumentError(f"{path}: NaN")
        return out

    def plan(self, puts: list[dict], deletes: list[str],
             pending: Optional[dict] = None) -> tuple[list[Op], list[bytes]]:
        """The log operations of one transaction: new field IDs first, then
        each document with its ordinal, its reference edges, and deletes.
        Holds new ordinals as pending until `release`."""
        ops: list[Op] = []
        reserved: list[bytes] = []
        with self._lock:
            new_fields: dict[str, int] = {}
            for raw in puts:
                doc = self._check(raw)
                for path in needed_fields(doc):
                    if path not in self.fields and path not in new_fields:
                        if len(self.fields) + len(new_fields) >= FIELDS_MAX:
                            # A write is never refused for it: the field goes
                            # unindexed, and a condition on it is checked
                            # against each document instead.
                            if not self.fields_full:
                                log.warning("the field dictionary is full; new fields go unindexed")
                            self.fields_full = True
                            continue
                        new_fields[path] = len(self.fields) + len(new_fields) + 1
                key = doc["_key"].encode()
                o = self._pending.get(key) or self._ord_from_store(key)
                if o is None:
                    o = self.next_ord
                    self.next_ord += 1
                    self._pending[key] = o
                    reserved.append(key)
                ops.append(Op(DOCS, key, encode_doc(o, doc)))
                for hook in self.put_hooks:
                    ops.extend(hook(raw, doc, o, pending if pending is not None
                                    else pending_of(puts, deletes)))
            for key_text in deletes:
                key = key_text.encode()
                ops.append(Op(DOCS, key, None))
                o = self._ord_from_store(key)
                for hook in self.delete_hooks:
                    ops.extend(hook(key, o))
            field_ops = [Op(FIELDS, p.encode(), FID.pack(f)) for p, f in new_fields.items()]
        return field_ops + ops, reserved

    def release(self, reserved: list[bytes]) -> None:
        with self._lock:
            for key in reserved:
                self._pending.pop(key, None)

    def write(self, puts: list[dict] = (), deletes: list[str] = (),
              extra: Optional[Callable[[dict], list[Op]]] = None) -> int:
        """One transaction. `extra(pending)` adds operations checked against
        the documents this transaction puts and deletes (the graph's edges).
        Planned under the store's commit lock, so new field IDs and every
        check see each earlier write."""
        puts, deletes = list(puts), list(deletes)
        pending = pending_of(puts, deletes)
        reserved: list[bytes] = []

        def prepare(lsn: int) -> list[Op]:
            ops, held = self.plan(puts, deletes, pending)
            reserved.extend(held)
            return ops + (extra(pending) if extra is not None else [])
        try:
            return self.store.commit(prepare=prepare)
        finally:
            self.release(reserved)

    # -- reading ----------------------------------------------------------
    def get(self, key: str, at: Optional[int] = None) -> Optional[dict]:
        v = self.store.get(DOCS, key.encode(), at)
        if v is None or v.value is None:
            return None
        o, doc = decode_doc(v.value)
        doc["_lsn"], doc["_ord"] = v.lsn, o
        return doc

    def key_of(self, ordinal: int) -> Optional[str]:
        k = self.columns.key(ordinal)
        return None if k is None else k.decode()

    def by_ordinal(self, ordinal: int, at: Optional[int] = None) -> Optional[dict]:
        key = self.key_of(ordinal)
        return None if key is None else self.get(key, at)

    def live_ordinals(self) -> Iterator[int]:
        return self.columns.live()

    @staticmethod
    def _schemas(kind_names: Optional[list[str]]) -> list:
        if not kind_names:
            return list(kinds.KINDS.values()) + [kinds.OPEN]
        return [kinds.OPEN if k == kinds.ITEM else kinds.kind_of(k) for k in kind_names]

    def _usable(self, pred: Predicate, kind_names: Optional[list[str]]) -> bool:
        return all(kinds.is_indexed(k, pred.path) for k in self._schemas(kind_names))

    def _is_date(self, pred: Predicate, kind_names: Optional[list[str]]) -> bool:
        return any(kinds.root_of(pred.path) in k.dates for k in self._schemas(kind_names))

    def _range_sources(self, ranges):
        """(count estimate, ordinal set builder) for a set of key ranges."""
        segs = self.store.derived(IDX)
        lists: list[tuple[int, bytes]] = []
        for start, end in ranges:
            for gen, seg in segs:
                for v in seg.scan(start, end):
                    lists.append((gen, v.value))
        mem: list[int] = []
        with self._lock:
            for start, end in ranges:
                if end == start + b"\x00":
                    mem.extend(self.mem_index.get(start, ()))
                else:
                    mem.extend(o for k, group in self.mem_index.items()
                               if k >= start and (end is None or k < end) for o in group)
        estimate = sum(postings.count(data) for _, data in lists) + len(mem)

        def build() -> set[int]:
            src = self.columns.src
            out = {o for o in mem if o < len(src) and src[o] == MEM}
            for gen, data in lists:
                for o in postings.Postings(data).ordinals():
                    if o < len(src) and src[o] == gen:
                        out.add(o)
            return out
        return estimate, build

    def candidates(self, preds: list[Predicate], kind_names: Optional[list[str]]) -> Optional[set[int]]:
        """Probe and intersect: the smallest usable posting set, or None
        when no predicate can use the index."""
        best = self._smallest(preds, kind_names)
        return None if best is None else best[1]()

    def estimate(self, preds: list[Predicate], kind_names: Optional[list[str]]) -> int:
        """How many documents the smallest usable list names (no decoding),
        or every live document when no condition can use the index."""
        best = self._smallest(preds, kind_names)
        return best[0] if best is not None else sum(1 for _ in self.columns.live())

    def _smallest(self, preds: list[Predicate], kind_names: Optional[list[str]]):
        best = None
        if self.extra_lookup is not None:
            best = self.extra_lookup(preds, kind_names)
        for pred in preds:
            if not self._usable(pred, kind_names):
                continue
            fid = self.fields.get(pred.path)
            if fid is None:
                if pred.op in ("!=",) or (pred.op == "exists" and pred.value is False) or self.fields_full:
                    continue
                return (0, set)       # nobody has ever had this property
            ranges = pred.ranges(fid, self._is_date(pred, kind_names))
            if ranges is None:
                continue
            estimate, build = self._range_sources(ranges)
            if best is None or estimate < best[0]:
                best = (estimate, build)
        return best

    def find(self, *, kinds_: Optional[list[str]] = None, where: Iterable = (),
             at: Optional[int] = None, limit: int = 200, order: Optional[tuple[str, str]] = None,
             within: Optional[set[int]] = None) -> list[dict]:
        preds = [Predicate.parse(w) for w in where]
        if len(preds) > WHERE_MAX:
            raise DocumentError(f"at most {WHERE_MAX} conditions")
        if kinds_:
            self._schemas(kinds_)
            preds.append(Predicate("_kind", "in", list(kinds_)))
        out = []
        started = time.perf_counter()
        self.last_read = 0
        for doc in self._matching(preds, kinds_, at, within):
            out.append(doc)
        if self.shape_hook is not None:
            self.shape_hook(kinds_, preds, order, (time.perf_counter() - started) * 1000.0,
                            self.last_read, len(out))
        if order:
            path, direction = order
            out.sort(key=lambda d: _sort_key(d, path), reverse=(direction == "desc"))
        else:
            out.sort(key=lambda d: -d["_lsn"])
        return out[:limit]

    def _matching(self, preds, kind_names, at, within) -> Iterator[dict]:
        dates = {id(p): self._is_date(p, kind_names) for p in preds}
        with self.store.reading(at) as lsn:
            if at is not None and at < self.store.lsn:
                # An older read: the index describes the newest documents,
                # so check every document as it was.
                for v in self.store.scan(DOCS, at=lsn):
                    o, doc = decode_doc(v.value)
                    if within is not None and o not in within:
                        continue
                    doc["_lsn"], doc["_ord"] = v.lsn, o
                    if all(p.matches(doc, dates[id(p)]) for p in preds):
                        yield doc
                return
            found = self.candidates(preds, kind_names)
            if within is not None:
                found = within if found is None else (found & within)
            ordinals = sorted(found) if found is not None else list(self.columns.live())
            self.last_read = len(ordinals)
            for o in ordinals:
                doc = self.by_ordinal(o, lsn)
                if doc is not None and all(p.matches(doc, dates[id(p)]) for p in preds):
                    yield doc

    def resident_bytes(self) -> int:
        return self.columns.resident_bytes()

    def forget_sources(self, sources: list[str]) -> dict:
        """Forget every document whose `_source` is one of `sources`, with
        every edge and vector row that hangs on it (section 7.8)."""
        keys = {d["_key"].encode() for d in self.find(where=[["_source", "in", list(sources)]],
                                                       limit=1 << 30)}
        return self.forget_keys(keys)

    def forget_keys(self, keys: set[bytes]) -> dict:
        families: dict[int, set[bytes]] = {DOCS: set(keys)}
        for hook in self.forget_hooks:
            for family, more in hook(keys).items():
                families.setdefault(family, set()).update(more)
        report = self.store.forget(families)
        report["documents"] = len(keys)
        return report


def pending_of(puts: list, deletes: list) -> dict:
    pending: dict = {d["_key"]: d for d in puts if isinstance(d, dict) and isinstance(d.get("_key"), str)}
    pending.update({k: None for k in deletes})
    return pending


def _sort_key(doc: dict, path: str):
    values = [v for p, v in kinds.flatten(doc) if p == path]
    v = values[0] if values else None
    return (v is None, str(type(v)), v if v is not None else 0)
