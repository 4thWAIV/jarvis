"""The vector index: exact scan through Accelerate (design sections 7.5,
7.10, 7.11 and 7.17.4).

One space per embedder ID. A vector row belongs to one document's one
embedded property and refers to it by ordinal. Rows are logged like any
write (family `VECS`, key `<space>\\0<ordinal><property>`, raw float32), and
each generation's rows are two files of their own, not key-value segments:
`vec/<space>/<gen>-0.f32` (L2-normalised float32 rows, so a dot product is
the cosine) and `.ids` (per row: ordinal, property, flags and LSN, 16
bytes). Only the `.f32` files are memory-mapped, `ACCESS_COPY` because
`ctypes` refuses a read-only buffer; nothing writes through the mapping.

Each space keeps a resident map from ordinal to the one row that is that
document's current vector (8 bytes a row, rebuilt at start from the `.ids`
files), and each file a penalty vector: 0 for current rows, -1e30 for rows
a newer version, a delete or a changed text has made stale. A search adds
the penalty to the scores, so a stale row is never returned; compaction
drops them.

A document whose embedded text changes has its rows deleted in the same
transaction, then embedded again in the background: a vector never answers
for text it was not made from. With no embedder (the default until one is
chosen) nothing is embedded and a search says so.

The vector search has its own deadline after the query embedding is ready;
past it the answer is marked partial, and the remaining files are still
scanned in the background, which leaves their pages warm.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import mmap
import os
import struct
import threading
import time
from array import array
from ctypes import c_float
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from jarvis.knowledge import embedder as emb
from jarvis.knowledge import items as item_texts
from jarvis.knowledge import kinds, lanes, vecmath
from jarvis.knowledge.documents import DOC_HEAD, DocumentStore, decode_doc
from jarvis.knowledge.segment import Version
from jarvis.knowledge.store import CONTENT, DOCS, Store, newest_only
from jarvis.knowledge.wal import Op, fsync_dir, full_fsync

log = logging.getLogger("jarvis.knowledge.vectors")

VECS = 5
IDS = struct.Struct("<IIQ")              # ordinal, property id | flags, lsn
DELETED = 0x80000000
ORD = struct.Struct(">I")
GATHER_MAX = 4096
DEADLINE_SEC = 0.100
QUERY_EMBED_SEC = 0.150
NONE = 0
WHOLE = 0                       # an item's whole-item row; section i is prop i + 1
ITEM_SPACE_SUFFIX = "#items"
SECTIONS_EMBEDDED_MAX = 2000    # past this many sections an item's later ones get no vector
WHERE_SECTIONS = 3              # the sections a vector hit names as where it matched


def vec_key(space: str, ordinal: int, prop: str) -> bytes:
    return space.encode() + b"\x00" + ORD.pack(ordinal) + prop.encode()


def parse_vec_key(key: bytes) -> tuple[str, int, str]:
    space, _, rest = key.partition(b"\x00")
    return space.decode(), ORD.unpack_from(rest, 0)[0], rest[4:].decode()


def prop_id(prop: str) -> int:
    """A row's prop as stored: an item row's own number (`#0` the whole
    item, `#<i + 1>` section i), a chunk-era property's hash."""
    if prop[:1] == "#" and prop[1:].isdigit():
        return int(prop[1:])
    return int.from_bytes(hashlib.blake2b(prop.encode(), digest_size=4).digest(), "little") & 0x7FFFFFFF


def item_prop(p: int) -> str:
    return f"#{p}"


def item_space(embedder_id: str) -> str:
    """The space an embedder's item rows live in, apart from chunk rows."""
    return embedder_id + ITEM_SPACE_SUFFIX


def is_item_space(name: str) -> bool:
    return name.endswith(ITEM_SPACE_SUFFIX)


def embedded_prop(kind_name: str) -> Optional[str]:
    """The one embedded property of a kind (the schema keeps it to one).
    An item has none yet: its whole-item and section vectors are round 2."""
    if kind_name == kinds.ITEM:
        return None
    found = kinds.kind_of(kind_name).embedded
    return found[0] if found else None


def _safe_space(space: str) -> str:
    if not space or "/" in space or space.startswith(".") or "\x00" in space:
        raise ValueError(f"not a space name: {space!r}")
    return space


def _write_file(path: Path, data_parts: Iterable[bytes]) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    sha = hashlib.sha256()
    fd = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        for data in data_parts:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            sha.update(data)
        full_fsync(fd)
    finally:
        os.close(fd)
    os.replace(partial, path)
    fsync_dir(path.parent)
    return sha.hexdigest()


@dataclass
class Row:
    ordinal: int
    prop: int
    lsn: int
    vector: bytes            # float32 little-endian; zeros when deleted
    deleted: bool


def _rows_of(versions: Iterator[Version]) -> dict[str, list[Row]]:
    """The newest row of each key, by space."""
    out: dict[str, list[Row]] = {}
    for v in newest_only(versions):
        space, o, prop = parse_vec_key(v.key)
        out.setdefault(space, []).append(Row(o, prop_id(prop), v.lsn, v.value or b"", v.value is None))
    return out


def _write_rows(directory: Path, gen: int, space: str, rows: list[Row], dim: int) -> dict:
    rel = Path("vec") / _safe_space(space)
    zero = bytes(4 * dim)
    rows.sort(key=lambda r: (r.ordinal, r.prop))
    f32 = _write_file(directory / rel / f"{gen:08d}-0.f32",
                      (r.vector if not r.deleted else zero for r in rows))
    ids = _write_file(directory / rel / f"{gen:08d}-0.ids",
                      (IDS.pack(r.ordinal, r.prop | (DELETED if r.deleted else 0), r.lsn) for r in rows))
    return {"files": [str(rel / f"{gen:08d}-0.f32"), str(rel / f"{gen:08d}-0.ids")],
            "space": space, "rows": len(rows), "dim": dim, "sha256": f32, "ids_sha256": ids,
            "bytes": len(rows) * (4 * dim + IDS.size)}


def _read_rows(directory: Path, entry: dict) -> list[Row]:
    f32 = (directory / entry["files"][0]).read_bytes()
    ids = (directory / entry["files"][1]).read_bytes()
    dim = int(entry["dim"])
    out = []
    for i in range(int(entry["rows"])):
        o, p, lsn = IDS.unpack_from(ids, i * IDS.size)
        out.append(Row(o, p & ~DELETED, lsn, f32[i * 4 * dim:(i + 1) * 4 * dim], bool(p & DELETED)))
    return out


class VectorPart:
    """Owns the `VECS` family's files at flush and compaction."""
    name = "vectors"
    families: dict = {}
    owns = {VECS: "vec"}

    def build(self, gen, newest, write, read_family) -> None:
        return None

    def write_family(self, directory: Path, gen: int, family: int,
                     versions: Iterator[Version]) -> list[dict]:
        entries = []
        for space, rows in _rows_of(versions).items():
            live = [r for r in rows if not r.deleted]
            dim = len(live[0].vector) // 4 if live else 0
            entries.append(_write_rows(directory, gen, space, rows, dim))
        return entries

    def compact_family(self, directory: Path, family: int, inputs: list[dict], out_gen: int,
                       purge: set[bytes], drop_tombstones: bool) -> list[dict]:
        by_space: dict[str, dict[tuple[int, int], Row]] = {}
        dims: dict[str, int] = {}
        gone = {(s, o, prop_id(p)) for s, o, p in (parse_vec_key(k) for k in purge)}
        for entry in inputs:
            space = entry["space"]
            if entry["dim"]:
                dims[space] = int(entry["dim"])
            table = by_space.setdefault(space, {})
            for row in _read_rows(directory, entry):
                if (space, row.ordinal, row.prop) in gone:
                    continue
                held = table.get((row.ordinal, row.prop))
                if held is None or row.lsn > held.lsn:
                    table[(row.ordinal, row.prop)] = row
        out = []
        for space, table in by_space.items():
            rows = [r for r in table.values() if not (r.deleted and drop_tombstones)]
            out.append(_write_rows(directory, out_gen, space, rows, dims.get(space, 0)))
        return out

    def holds(self, directory: Path, entry: dict, keys: set[bytes]) -> bool:
        wanted = {(s, o, prop_id(p)) for s, o, p in (parse_vec_key(k) for k in keys)}
        if not any(s == entry["space"] for s, _, _ in wanted):
            return False
        ids = (directory / entry["files"][1]).read_bytes()
        for i in range(int(entry["rows"])):
            o, p, _ = IDS.unpack_from(ids, i * IDS.size)
            if (entry["space"], o, p & ~DELETED) in wanted:
                return True
        return False


class SpaceFile:
    """One generation's rows of one space, mapped."""

    def __init__(self, directory: Path, entry: dict) -> None:
        self.gen = int(entry["gen"])
        self.n = int(entry["rows"])
        self.d = int(entry["dim"])
        raw = (directory / entry["files"][1]).read_bytes()
        self.ordinals = array("I", (IDS.unpack_from(raw, i * IDS.size)[0] for i in range(self.n)))
        self.flags = array("I", (IDS.unpack_from(raw, i * IDS.size)[1] for i in range(self.n)))
        self.lsns = array("Q", (IDS.unpack_from(raw, i * IDS.size)[2] for i in range(self.n)))
        self.penalty = (c_float * max(self.n, 1))(*([vecmath.REFUSED] * self.n))
        self._fh = None
        self.mm = None
        self.rows = None
        if self.n and self.d:
            self._fh = open(directory / entry["files"][0], "rb")
            self.mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_COPY)
            self.rows = (c_float * (self.n * self.d)).from_buffer(self.mm)

    def whole_mask(self):
        """0 for whole-item rows, refused for section rows: an item search
        ranks items by their whole-item vectors alone."""
        if getattr(self, "_whole", None) is None:
            self._whole = (c_float * max(self.n, 1))(
                *[0.0 if (self.flags[i] & ~DELETED) == WHOLE else vecmath.REFUSED for i in range(self.n)])
        return self._whole

    def warm(self) -> None:
        if self.mm is not None and hasattr(mmap, "MADV_WILLNEED"):
            try:
                self.mm.madvise(mmap.MADV_WILLNEED)
            except OSError:
                pass


class Space:
    """The resident map of one space: (ordinal, prop) -> (slot << 32 | row)
    + 1, with the LSN of that row, the props each ordinal has a current row
    for, and the files by slot. A chunk-era document has one prop; an item
    has its whole-item row (prop 0) and one per section (prop i + 1)."""

    def __init__(self, name: str, dim: int = 0) -> None:
        self.name = name
        self.dim = dim
        self.where: dict[tuple[int, int], int] = {}
        self.lsn: dict[tuple[int, int], int] = {}
        self.props: dict[int, set[int]] = {}
        self.files: dict[int, SpaceFile] = {}
        self.mem: dict[tuple[int, int], tuple[int, bytes]] = {}   # unflushed rows

    def has(self, o: int, p: int) -> bool:
        return (o, p) in self.where or (o, p) in self.mem

    def has_any(self, o: int) -> bool:
        return bool(self.props.get(o))

    def _mark(self, key: tuple[int, int]) -> None:
        o, p = key
        if key in self.where or key in self.mem:
            self.props.setdefault(o, set()).add(p)
        else:
            found = self.props.get(o)
            if found is not None:
                found.discard(p)
                if not found:
                    del self.props[o]

    def _refuse_old(self, key: tuple[int, int]) -> None:
        loc = self.where.pop(key, 0)
        if loc:
            slot, row = (loc - 1) >> 32, (loc - 1) & 0xFFFFFFFF
            f = self.files.get(slot)
            if f is not None and row < f.n:
                f.penalty[row] = vecmath.REFUSED

    def set_mem(self, o: int, p: int, lsn: int, vector: Optional[bytes]) -> None:
        key = (o, p)
        if lsn < self.lsn.get(key, 0):
            return
        self._refuse_old(key)
        self.lsn[key] = lsn
        if vector is None:
            self.mem.pop(key, None)
        else:
            self.mem[key] = (lsn, vector)
            self.dim = len(vector) // 4
        self._mark(key)

    def add_file(self, f: SpaceFile) -> None:
        self.files[f.gen] = f
        if f.d:
            self.dim = f.d
        for row in range(f.n):
            key = (f.ordinals[row], f.flags[row] & ~DELETED)
            lsn = f.lsns[row]
            held_lsn = self.lsn.get(key, 0)
            if lsn < held_lsn or (lsn == held_lsn and key in self.where):
                continue           # a newer row is current, or this one is placed
            self._refuse_old(key)
            self.lsn[key] = lsn
            held = self.mem.get(key)
            if held is not None and held[0] <= lsn:
                del self.mem[key]  # flushed: the file row takes over
            if not f.flags[row] & DELETED:
                self.where[key] = ((f.gen << 32) | row) + 1
                f.penalty[row] = 0.0
            self._mark(key)

    def drop_files(self, gens: Iterable[int]) -> None:
        for gen in gens:
            f = self.files.pop(gen, None)
            if f is None:
                continue
            for row in range(f.n):
                key = (f.ordinals[row], f.flags[row] & ~DELETED)
                loc = self.where.get(key)
                if loc and ((loc - 1) >> 32) == gen:
                    del self.where[key]
                    self._mark(key)

    def rows(self) -> int:
        return len(self.where) + sum(1 for k in self.mem if k not in self.where)

    def items(self) -> int:
        """Ordinals with a whole-item row: the items embedded."""
        return sum(1 for props in self.props.values() if WHOLE in props)


class VectorIndex:
    def __init__(self, docs: DocumentStore, embedder: Optional[emb.Embedder] = None) -> None:
        self.docs = docs
        self.store: Store = docs.store
        self.embedder = embedder
        self.queue = emb.EmbeddingQueue(embedder) if embedder is not None else None
        self.spaces: dict[str, Space] = {}
        self._lock = threading.RLock()
        self._todo: dict[int, tuple[str, str]] = {}       # ordinal -> (key, _hash) waiting to embed
        self._items_todo: dict[int, tuple[str, str]] = {}  # item ordinal -> (path, content sha256)
        self._todo_cond = threading.Condition()
        self.paused = False
        self._stop = False
        self.embedded = 0
        self.items_embedded = 0
        self._working = False
        self._load()
        self.store.listeners.append(self._on_apply)
        self.store.generation_listeners.append(self._on_generation)
        docs.put_hooks.append(self._put_ops)
        docs.delete_hooks.append(self._delete_ops)
        docs.forget_hooks.append(self._forget_keys)
        self._worker = None
        if self.queue is not None:
            self._start()

    def _start(self) -> None:
        self._worker = threading.Thread(target=self._embed_loop, name="knowledge-vectors", daemon=True)
        self._worker.start()
        self._queue_missing()

    def attach(self, embedder: Optional[emb.Embedder]) -> bool:
        """Start embedding with `embedder` once one exists (a model installed
        while the engine runs): every document waiting is queued and taken
        under the pace, as at start. True when it was attached now."""
        if embedder is None or self.embedder is not None or self._stop:
            return False
        with self._lock:
            self.embedder = embedder
            self.queue = emb.EmbeddingQueue(embedder)
        self._start()
        return True

    @property
    def space_id(self) -> Optional[str]:
        return self.embedder.id if self.embedder is not None else None

    @property
    def item_space_id(self) -> Optional[str]:
        return item_space(self.embedder.id) if self.embedder is not None else None

    def _space(self, name: str) -> Space:
        if name not in self.spaces:
            self.spaces[name] = Space(name)
        return self.spaces[name]

    # -- the resident maps ---------------------------------------------------
    def _load(self) -> None:
        for entry in sorted(self.store.external_entries(VECS), key=lambda e: int(e["gen"])):
            self._space(entry["space"]).add_file(SpaceFile(self.store.directory, entry))
        mems, _ = self.store._sources(VECS)
        for mem in mems:
            for v in newest_only(mem.all_versions(VECS)):
                space, o, prop = parse_vec_key(v.key)
                self._space(space).set_mem(o, prop_id(prop), v.lsn, v.value)

    def _on_apply(self, lsn: int, ops: list[Op]) -> None:
        with self._lock:
            for op in ops:
                if op.family == VECS:
                    space, o, prop = parse_vec_key(op.key)
                    self._space(space).set_mem(o, prop_id(prop), lsn, op.value)
            if self.queue is None:
                return
            # Documents after the rows: a changed text's rows are deleted in
            # the same transaction, so it is seen as missing and queued.
            space = self.spaces.get(self.space_id)
            items = self.spaces.get(self.item_space_id)
            for op in ops:
                if op.family != DOCS or op.value is None:
                    continue
                o, doc = decode_doc(op.value)
                if doc["_kind"] == kinds.ITEM:
                    summary = item_texts.embedded_summary(doc)
                    if summary and not (items is not None and items.has(o, WHOLE)):
                        with self._todo_cond:
                            self._items_todo[o] = (doc["_key"], summary["sha256"])
                            self._todo_cond.notify()
                    continue
                prop = embedded_prop(doc["_kind"])
                if prop and doc.get(prop) and not (space is not None and space.has_any(o)):
                    with self._todo_cond:
                        self._todo[o] = (doc["_key"], doc["_hash"])
                        self._todo_cond.notify()

    def _on_generation(self, gen: int, replaced: list[int]) -> None:
        entries = [e for e in self.store.external_entries(VECS) if int(e["gen"]) == gen]
        with self._lock:
            for space in self.spaces.values():
                space.drop_files(replaced)
            for entry in entries:
                self._space(entry["space"]).add_file(SpaceFile(self.store.directory, entry))

    # -- keeping vectors true to their text -------------------------------------
    def _put_ops(self, raw: dict, stored: dict, ordinal: int, pending: dict) -> list[Op]:
        prop = embedded_prop(stored["_kind"])
        if not prop:
            return []
        before = self.docs.get(stored["_key"])
        if before is not None and before.get("_hash") == stored.get("_hash"):
            return []
        return self._tombstones(ordinal, prop)

    def _delete_ops(self, key: bytes, ordinal: Optional[int]) -> list[Op]:
        if ordinal is None:
            return []
        doc = None
        for v in self.store.versions(DOCS, key):
            if v.value is not None:
                doc = decode_doc(v.value)[1]
                break
        if doc is not None and doc["_kind"] == kinds.ITEM:
            return self.item_tombstones(ordinal)
        prop = embedded_prop(doc["_kind"]) if doc else None
        return self._tombstones(ordinal, prop) if prop else []

    def _tombstones(self, ordinal: int, prop: str) -> list[Op]:
        p = prop_id(prop)
        with self._lock:
            return [Op(VECS, vec_key(name, ordinal, prop), None) for name, space in self.spaces.items()
                    if not is_item_space(name) and space.has(ordinal, p)]

    def item_tombstones(self, ordinal: int) -> list[Op]:
        """Every row an item has in every item space: written with a change
        of its content (or its delete), so an old vector never answers for
        new text, and the item counts as waiting until it is embedded again."""
        with self._lock:
            return [Op(VECS, vec_key(name, ordinal, item_prop(p)), None)
                    for name, space in self.spaces.items() if is_item_space(name)
                    for p in sorted(space.props.get(ordinal, ()))]

    def item_embedded(self, ordinal: int) -> bool:
        """Whether the item's vectors exist for its current content."""
        items = self.spaces.get(self.item_space_id or "")
        return items is not None and items.has(ordinal, WHOLE)

    def _forget_keys(self, keys: set[bytes]) -> dict[int, set[bytes]]:
        out: set[bytes] = set()
        for key in keys:
            o = self.docs._ord_from_store(key)
            if o is None:
                continue
            for v in self.store.versions(DOCS, key):
                if v.value is not None:
                    kind_name = decode_doc(v.value)[1]["_kind"]
                    if kind_name == kinds.ITEM:
                        out |= {vec_key(name, o, item_prop(p)) for name, space in self.spaces.items()
                                if is_item_space(name) for p in space.props.get(o, ())}
                        break
                    prop = embedded_prop(kind_name)
                    if prop:
                        out |= {vec_key(name, o, prop) for name in self.spaces if not is_item_space(name)}
                        if self.space_id:
                            out.add(vec_key(self.space_id, o, prop))
                    break
        return {VECS: out}

    # -- embedding in the background --------------------------------------------
    def _queue_missing(self) -> None:
        """At start, every live document with embedded text and no row in the
        current space is queued, and every item with content (or a record's
        words) and no whole-item row."""
        space = self.spaces.get(self.space_id)
        for kind in kinds.KINDS.values():
            if not kind.embedded:
                continue
            for doc in self.docs.find(kinds_=[kind.name], limit=1 << 30):
                o = doc["_ord"]
                if doc.get(kind.embedded[0]) and not (space and space.has_any(o)):
                    self._todo[o] = (doc["_key"], doc["_hash"])
        items = self.spaces.get(self.item_space_id)
        with self.store.reading() as lsn:
            for v in self.store.scan(DOCS, b"/", b"0", lsn):
                if v.value is None:
                    continue
                o, doc = decode_doc(v.value)
                summary = item_texts.embedded_summary(doc)
                if doc.get("_kind") == kinds.ITEM and summary and not (items and items.has(o, WHOLE)):
                    self._items_todo[o] = (doc["_key"], summary["sha256"])

    def set_paused(self, paused: bool) -> None:
        with self._todo_cond:
            self.paused = paused
            self._todo_cond.notify_all()

    def pending(self) -> int:
        return len(self._todo) + len(self._items_todo)

    def _embed_loop(self) -> None:
        lanes.background()
        while True:
            with self._todo_cond:
                while not self._stop and (self.paused or not (self._todo or self._items_todo)):
                    self._todo_cond.wait(1.0)
                if self._stop:
                    return
                item = None
                batch = []
                if self._items_todo:
                    o = next(iter(self._items_todo))
                    item = (o, *self._items_todo.pop(o))
                else:
                    for o in list(self._todo)[:emb.INGEST_BATCH]:
                        batch.append((o, *self._todo.pop(o)))
                self._working = True
            try:
                if item is not None:
                    self._embed_item(*item)
                else:
                    self._embed_batch(batch)
            except Exception:
                log.exception("embedding failed")
            finally:
                self._working = False

    def _embed_texts(self, texts: list[str]) -> list[array]:
        """Every text's vector, as ingest batches behind any query, waiting
        while the pace holds; `_Closing` when the engine closes meanwhile."""
        out: list[array] = []
        for i in range(0, len(texts), emb.INGEST_BATCH):
            box: dict = {}
            done = threading.Event()

            def finished(vectors, error, box=box, done=done):
                box["v"], box["e"] = vectors, error
                done.set()
            self.queue.submit(texts[i:i + emb.INGEST_BATCH], emb.INGEST, "passage", finished)
            done.wait()
            if box.get("e") is not None:
                raise box["e"]
            out.extend(box["v"])
            with self._todo_cond:
                while self.paused and not self._stop:
                    self._todo_cond.wait(1.0)
                if self._stop:
                    raise _Closing()
        return out

    def _embed_item(self, o: int, path: str, sha: str) -> None:
        """An item's whole-item vector and one per section, committed in one
        write only while its content (a record's words) is still what they
        were made from; one changed meanwhile is queued again by its write,
        holding only its newest text (`_items_todo` is by item), so many
        changes before the background lane reaches it cost one embedding."""
        doc = self.docs.get(path)
        summary = item_texts.embedded_summary(doc or {}) or {}
        if doc is None or doc["_ord"] != o or summary.get("sha256") != sha:
            return
        if doc.get("_content") is None:
            texts = [emb.prepare(t) for t in item_texts.record_texts(doc)]
        else:
            raw = self.store.get(CONTENT, path.encode())
            if raw is None or raw.value is None:
                return
            content = json.loads(raw.value)
            title = item_texts.title_of(doc, content)
            sections = (content.get("sections") or [])[:SECTIONS_EMBEDDED_MAX]
            texts = [emb.prepare(item_texts.whole_text(title, content))]
            texts += [emb.prepare(item_texts.embedded_text(title, s)) for s in sections]
        if not texts:
            return
        try:
            vectors = self._embed_texts(texts)
        except _Closing:
            return
        for vector in vectors:
            if len(vector) != self.embedder.dim:
                raise ValueError(f"the embedder gave {len(vector)} dimensions, not {self.embedder.dim}")
        space = self.item_space_id

        def prepare(lsn: int) -> list[Op]:
            now = self.docs.get(path)
            if now is None or now["_ord"] != o or ((item_texts.embedded_summary(now) or {}).get("sha256")) != sha:
                return []
            return [Op(VECS, vec_key(space, o, item_prop(p)), v.tobytes()) for p, v in enumerate(vectors)]
        with lanes.foreground():
            self.store.commit(prepare=prepare)
        if self.item_embedded(o):
            self.items_embedded += 1

    def _embed_batch(self, batch: list[tuple[int, str, str]]) -> None:
        docs = []
        for o, key, digest in batch:
            doc = self.docs.get(key)
            prop = embedded_prop(doc["_kind"]) if doc else None
            if doc is None or prop is None or doc.get("_hash") != digest or doc["_ord"] != o:
                continue
            docs.append((o, prop, digest, key, emb.prepare(str(doc.get(prop) or ""))))
        if not docs:
            return
        box: dict = {}
        done = threading.Event()

        def finished(vectors, error):
            box["v"], box["e"] = vectors, error
            done.set()
        self.queue.submit([d[4] for d in docs], emb.INGEST, "passage", finished)
        done.wait()
        if box.get("e") is not None:
            raise box["e"]
        ops = []
        for (o, prop, digest, key, _), vector in zip(docs, box["v"]):
            if len(vector) != self.embedder.dim:
                raise ValueError(f"the embedder gave {len(vector)} dimensions, not {self.embedder.dim}")
            current = self.docs.get(key)
            if current is None or current.get("_hash") != digest:
                continue             # the text changed meanwhile; it is queued again
            ops.append(Op(VECS, vec_key(self.space_id, o, prop), vector.tobytes()))
        if ops:
            self.store.commit(ops)
            self.embedded += len(ops)

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """For tests and rebuilds: until nothing waits to be embedded."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self._todo and not self._items_todo and (self.queue is None or self.queue._q.empty()):
                time.sleep(0.05)
                if not self._todo and not self._items_todo and not self._busy():
                    return True
            time.sleep(0.02)
        return False

    def _busy(self) -> bool:
        return self._working or (self.queue is not None and not self.queue._q.empty())

    # -- searching -----------------------------------------------------------
    def embed_query(self, text: str, timeout: float = QUERY_EMBED_SEC) -> Optional[array]:
        if self.queue is None:
            return None
        found = self.queue.embed_now([text], timeout)
        return found[0] if found else None

    def search(self, query: array, k: int = 50, within: Optional[set[int]] = None,
               deadline_s: float = DEADLINE_SEC, space_name: Optional[str] = None) -> dict:
        """The top `k` ordinals by cosine to `query` in one space; refused
        and stale rows never come back."""
        space = self.spaces.get(space_name or self.space_id or "")
        if space is None or not space.dim:
            return {"hits": [], "partial": False, "space": space_name or self.space_id}
        end = time.monotonic() + deadline_s
        d = space.dim
        q = (c_float * d)(*query)
        hits: list[tuple[float, int]] = []
        with self._lock:
            files = list(space.files.values())
            mem = dict(space.mem)
        if within is not None and len(within) <= GATHER_MAX:
            hits = self._gathered(space, files, mem, q, d, within, k)
            return {"hits": [(o, s) for s, o in hits], "partial": False, "space": space.name}
        partial = False
        late: list[SpaceFile] = []
        for f in files:
            if not f.n or f.rows is None:
                continue
            if time.monotonic() > end:
                partial = True
                late.append(f)
                continue
            scores = (c_float * f.n)()
            vecmath.MATH.gemv(f.rows, f.n, d, q, scores)
            if within is None:
                vecmath.MATH.add(scores, f.penalty, f.n)
            else:
                mask = (c_float * f.n)(*f.penalty)
                for row in range(f.n):
                    if f.ordinals[row] not in within:
                        mask[row] = vecmath.REFUSED
                vecmath.MATH.add(scores, mask, f.n)
            for s, row in vecmath.top_k(scores, f.n, k):
                hits.append((s, f.ordinals[row]))
        if mem:
            hits.extend(self._score_mem(mem, q, d, within))
        if late:
            threading.Thread(target=self._finish_late, args=(late, q, d), daemon=True).start()
        hits.sort(key=lambda h: (-h[0], h[1]))
        return {"hits": [(o, s) for s, o in hits[:k]], "partial": partial, "space": space.name}

    def search_items(self, query: array, k: int = 50, within: Optional[set[int]] = None,
                     deadline_s: float = DEADLINE_SEC) -> dict:
        """Items by meaning: ranked by their whole-item vectors, one hit an
        item, each naming the sections whose vectors come closest (where in
        the item it matched): (ordinal, score, [section index, ...])."""
        space = self.spaces.get(self.item_space_id or "")
        if space is None or not space.dim:
            return {"hits": [], "partial": False, "space": self.item_space_id}
        end = time.monotonic() + deadline_s
        d = space.dim
        q = (c_float * d)(*query)
        with self._lock:
            files = list(space.files.values())
            mem = dict(space.mem)
        whole_mem = {key: v for key, v in mem.items() if key[1] == WHOLE}
        ranked: list[tuple[float, int]] = []
        partial = False
        if within is not None and len(within) <= GATHER_MAX:
            ranked = self._gathered(space, files, whole_mem, q, d, within, k, props=(WHOLE,))
        else:
            for f in files:
                if not f.n or f.rows is None:
                    continue
                if time.monotonic() > end:
                    partial = True
                    continue
                scores = (c_float * f.n)()
                vecmath.MATH.gemv(f.rows, f.n, d, q, scores)
                vecmath.MATH.add(scores, f.penalty, f.n)
                vecmath.MATH.add(scores, f.whole_mask(), f.n)
                if within is not None:
                    mask = (c_float * f.n)()
                    for row in range(f.n):
                        if f.ordinals[row] not in within:
                            mask[row] = vecmath.REFUSED
                    vecmath.MATH.add(scores, mask, f.n)
                for s, row in vecmath.top_k(scores, f.n, k):
                    ranked.append((s, f.ordinals[row]))
            ranked.extend(self._score_mem(whole_mem, q, d, within))
        ranked.sort(key=lambda h: (-h[0], h[1]))
        hits = []
        for s, o in ranked[:k]:
            if s <= vecmath.REFUSED / 2:
                continue
            where = sorted(self._section_scores(space, files, mem, q, d, o), key=lambda h: (-h[0], h[1]))
            hits.append((o, s, [p - 1 for _, p in where[:WHERE_SECTIONS]]))
        return {"hits": hits, "partial": partial, "space": space.name}

    def _section_scores(self, space: Space, files: list[SpaceFile], mem: dict, q, d: int,
                        o: int) -> list[tuple[float, int]]:
        """One item's section rows scored against the query: (score, prop)."""
        by_gen = {f.gen: f for f in files}
        picked: list[tuple[int, object, int]] = []
        held: list[tuple[int, bytes]] = []
        for p in space.props.get(o, ()):
            if p == WHOLE:
                continue
            if (o, p) in mem:
                held.append((p, mem[(o, p)][1]))
                continue
            loc = space.where.get((o, p), 0)
            f = by_gen.get((loc - 1) >> 32) if loc else None
            if f is not None and f.rows is not None:
                picked.append((p, f, (loc - 1) & 0xFFFFFFFF))
        n = len(picked) + len(held)
        if not n:
            return []
        buf = (c_float * (n * d))()
        for i, (_, f, row) in enumerate(picked):
            ctypes.memmove(ctypes.addressof(buf) + 4 * d * i, ctypes.addressof(f.rows) + 4 * d * row, 4 * d)
        for j, (_, vec) in enumerate(held):
            ctypes.memmove(ctypes.addressof(buf) + 4 * d * (len(picked) + j), vec, 4 * d)
        scores = (c_float * n)()
        vecmath.MATH.gemv(buf, n, d, q, scores)
        props = [p for p, _, _ in picked] + [p for p, _ in held]
        return [(scores[i], props[i]) for i in range(n)]

    def _score_mem(self, mem: dict, q, d: int, within: Optional[set[int]]) -> list[tuple[float, int]]:
        items = [(o, vec) for (o, _), (_, vec) in mem.items() if within is None or o in within]
        if not items:
            return []
        buf = (c_float * (len(items) * d))()
        for i, (_, vec) in enumerate(items):
            ctypes.memmove(ctypes.addressof(buf) + 4 * d * i, vec, 4 * d)
        scores = (c_float * len(items))()
        vecmath.MATH.gemv(buf, len(items), d, q, scores)
        return [(scores[i], o) for i, (o, _) in enumerate(items)]

    def _gathered(self, space: Space, files: list[SpaceFile], mem: dict, q, d: int,
                  within: set[int], k: int, props: Optional[tuple] = None) -> list[tuple[float, int]]:
        """A small allowed set: copy its current rows into one block and
        score only those (all of an ordinal's props, or the ones named)."""
        by_gen = {f.gen: f for f in files}
        picked: list[tuple[int, object, int]] = []
        for o in within:
            for p in (props if props is not None else space.props.get(o, ())):
                if (o, p) in mem:
                    continue
                loc = space.where.get((o, p), 0)
                if not loc:
                    continue
                f = by_gen.get((loc - 1) >> 32)
                if f is not None and f.rows is not None:
                    picked.append((o, f, (loc - 1) & 0xFFFFFFFF))
        out = self._score_mem({key: v for key, v in mem.items()
                               if props is None or key[1] in props}, q, d, within)
        if picked:
            buf = (c_float * (len(picked) * d))()
            for i, (_, f, row) in enumerate(picked):
                ctypes.memmove(ctypes.addressof(buf) + 4 * d * i,
                               ctypes.addressof(f.rows) + 4 * d * row, 4 * d)
            scores = (c_float * len(picked))()
            vecmath.MATH.gemv(buf, len(picked), d, q, scores)
            out.extend((scores[i], o) for i, (o, _, _) in enumerate(picked))
        out.sort(key=lambda h: (-h[0], h[1]))
        return out[:k]

    def _finish_late(self, files: list[SpaceFile], q, d: int) -> None:
        for f in files:
            scores = (c_float * f.n)()
            vecmath.MATH.gemv(f.rows, f.n, d, q, scores)

    def warm(self) -> None:
        for space in self.spaces.values():
            for f in space.files.values():
                f.warm()

    def health(self) -> dict:
        return {"embedder": self.space_id, "math": vecmath.MATH.name,
                "spaces": {name: {"dim": s.dim, "rows": s.rows(), "files": len(s.files),
                                  **({"items": s.items()} if is_item_space(name) else {})}
                           for name, s in self.spaces.items()},
                "waiting": self.pending(), "embedded": self.embedded,
                "items_embedded": self.items_embedded}

    def close(self) -> None:
        with self._todo_cond:
            self._stop = True
            self._todo_cond.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=5)
        if self.queue is not None:
            self.queue.close()


class _Closing(Exception):
    """The engine closed while an item was being embedded."""
