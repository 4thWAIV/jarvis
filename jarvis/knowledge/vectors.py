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
from jarvis.knowledge import kinds, lanes, vecmath
from jarvis.knowledge.documents import DOC_HEAD, DocumentStore, decode_doc
from jarvis.knowledge.segment import Version
from jarvis.knowledge.store import DOCS, Store, newest_only
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


def vec_key(space: str, ordinal: int, prop: str) -> bytes:
    return space.encode() + b"\x00" + ORD.pack(ordinal) + prop.encode()


def parse_vec_key(key: bytes) -> tuple[str, int, str]:
    space, _, rest = key.partition(b"\x00")
    return space.decode(), ORD.unpack_from(rest, 0)[0], rest[4:].decode()


def prop_id(prop: str) -> int:
    return int.from_bytes(hashlib.blake2b(prop.encode(), digest_size=4).digest(), "little") & 0x7FFFFFFF


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

    def warm(self) -> None:
        if self.mm is not None and hasattr(mmap, "MADV_WILLNEED"):
            try:
                self.mm.madvise(mmap.MADV_WILLNEED)
            except OSError:
                pass


class Space:
    """The resident map of one space: ordinal -> (slot << 32 | row) + 1,
    with the LSN of that row, and the files by slot."""

    def __init__(self, name: str, dim: int = 0) -> None:
        self.name = name
        self.dim = dim
        self.where = array("Q", [0])
        self.lsn = array("Q", [0])
        self.files: dict[int, SpaceFile] = {}
        self.mem: dict[int, tuple[int, bytes]] = {}       # ordinal -> (lsn, vector) unflushed

    def _ensure(self, o: int) -> None:
        if o >= len(self.where):
            grow = o + 1 - len(self.where)
            self.where.extend([0] * grow)
            self.lsn.extend([0] * grow)

    def _refuse_old(self, o: int) -> None:
        loc = self.where[o] if o < len(self.where) else 0
        if loc:
            slot, row = (loc - 1) >> 32, (loc - 1) & 0xFFFFFFFF
            f = self.files.get(slot)
            if f is not None and row < f.n:
                f.penalty[row] = vecmath.REFUSED
        self.where[o] = 0

    def set_mem(self, o: int, lsn: int, vector: Optional[bytes]) -> None:
        self._ensure(o)
        if lsn < self.lsn[o]:
            return
        self._refuse_old(o)
        self.lsn[o] = lsn
        if vector is None:
            self.mem.pop(o, None)
        else:
            self.mem[o] = (lsn, vector)
            self.dim = len(vector) // 4

    def add_file(self, f: SpaceFile) -> None:
        self.files[f.gen] = f
        if f.d:
            self.dim = f.d
        for row in range(f.n):
            o, lsn = f.ordinals[row], f.lsns[row]
            self._ensure(o)
            if lsn < self.lsn[o] or (lsn == self.lsn[o] and self.where[o]):
                continue           # a newer row is current, or this one is placed
            self._refuse_old(o)
            self.lsn[o] = lsn
            held = self.mem.get(o)
            if held is not None and held[0] <= lsn:
                del self.mem[o]    # flushed: the file row takes over
            if not f.flags[row] & DELETED:
                self.where[o] = ((f.gen << 32) | row) + 1
                f.penalty[row] = 0.0

    def drop_files(self, gens: Iterable[int]) -> None:
        for gen in gens:
            f = self.files.pop(gen, None)
            if f is None:
                continue
            for row in range(f.n):
                o = f.ordinals[row]
                if o < len(self.where) and self.where[o] and ((self.where[o] - 1) >> 32) == gen:
                    self.where[o] = 0

    def rows(self) -> int:
        return sum(1 for loc in self.where if loc) + len(self.mem)


class VectorIndex:
    def __init__(self, docs: DocumentStore, embedder: Optional[emb.Embedder] = None) -> None:
        self.docs = docs
        self.store: Store = docs.store
        self.embedder = embedder
        self.queue = emb.EmbeddingQueue(embedder) if embedder is not None else None
        self.spaces: dict[str, Space] = {}
        self._lock = threading.RLock()
        self._todo: dict[int, tuple[str, str]] = {}       # ordinal -> (key, _hash) waiting to embed
        self._todo_cond = threading.Condition()
        self.paused = False
        self._stop = False
        self.embedded = 0
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
                space, o, _ = parse_vec_key(v.key)
                self._space(space).set_mem(o, v.lsn, v.value)

    def _on_apply(self, lsn: int, ops: list[Op]) -> None:
        with self._lock:
            for op in ops:
                if op.family == VECS:
                    space, o, _ = parse_vec_key(op.key)
                    self._space(space).set_mem(o, lsn, op.value)
            # Documents after the rows: a changed text's rows are deleted in
            # the same transaction, so it is seen as missing and queued.
            for op in ops:
                if op.family == DOCS and op.value is not None and self.queue is not None:
                    o, doc = decode_doc(op.value)
                    prop = embedded_prop(doc["_kind"])
                    space = self.spaces.get(self.space_id)
                    has = space is not None and o < len(space.where) and (space.where[o] or o in space.mem)
                    if prop and doc.get(prop) and not has:
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
        prop = embedded_prop(doc["_kind"]) if doc else None
        return self._tombstones(ordinal, prop) if prop else []

    def _tombstones(self, ordinal: int, prop: str) -> list[Op]:
        with self._lock:
            return [Op(VECS, vec_key(name, ordinal, prop), None) for name, space in self.spaces.items()
                    if ordinal < len(space.where) and (space.where[ordinal] or ordinal in space.mem)]

    def _forget_keys(self, keys: set[bytes]) -> dict[int, set[bytes]]:
        out: set[bytes] = set()
        for key in keys:
            o = self.docs._ord_from_store(key)
            if o is None:
                continue
            for v in self.store.versions(DOCS, key):
                if v.value is not None:
                    prop = embedded_prop(decode_doc(v.value)[1]["_kind"])
                    if prop:
                        out |= {vec_key(name, o, prop) for name in self.spaces}
                        if self.space_id:
                            out.add(vec_key(self.space_id, o, prop))
                    break
        return {VECS: out}

    # -- embedding in the background --------------------------------------------
    def _queue_missing(self) -> None:
        """At start, every live document with embedded text and no row in the
        current space is queued."""
        space = self.spaces.get(self.space_id)
        for kind in kinds.KINDS.values():
            if not kind.embedded:
                continue
            for doc in self.docs.find(kinds_=[kind.name], limit=1 << 30):
                o = doc["_ord"]
                if doc.get(kind.embedded[0]) and not (space and o < len(space.where)
                                                       and (space.where[o] or o in space.mem)):
                    self._todo[o] = (doc["_key"], doc["_hash"])

    def set_paused(self, paused: bool) -> None:
        with self._todo_cond:
            self.paused = paused
            self._todo_cond.notify_all()

    def pending(self) -> int:
        return len(self._todo)

    def _embed_loop(self) -> None:
        lanes.background()
        while True:
            with self._todo_cond:
                while not self._stop and (self.paused or not self._todo):
                    self._todo_cond.wait(1.0)
                if self._stop:
                    return
                batch = []
                for o in list(self._todo)[:emb.INGEST_BATCH]:
                    batch.append((o, *self._todo.pop(o)))
            try:
                self._embed_batch(batch)
            except Exception:
                log.exception("embedding a batch failed")

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
            if not self._todo and (self.queue is None or self.queue._q.empty()):
                time.sleep(0.05)
                if not self._todo:
                    return True
            time.sleep(0.02)
        return False

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

    def _score_mem(self, mem: dict, q, d: int, within: Optional[set[int]]) -> list[tuple[float, int]]:
        items = [(o, vec) for o, (_, vec) in mem.items() if within is None or o in within]
        if not items:
            return []
        buf = (c_float * (len(items) * d))()
        for i, (_, vec) in enumerate(items):
            ctypes.memmove(ctypes.addressof(buf) + 4 * d * i, vec, 4 * d)
        scores = (c_float * len(items))()
        vecmath.MATH.gemv(buf, len(items), d, q, scores)
        return [(scores[i], o) for i, (o, _) in enumerate(items)]

    def _gathered(self, space: Space, files: list[SpaceFile], mem: dict, q, d: int,
                  within: set[int], k: int) -> list[tuple[float, int]]:
        """A small allowed set: copy its current rows into one block and
        score only those."""
        by_gen = {f.gen: f for f in files}
        picked: list[tuple[int, object, int]] = []
        for o in within:
            if o in mem:
                continue
            loc = space.where[o] if o < len(space.where) else 0
            if not loc:
                continue
            f = by_gen.get((loc - 1) >> 32)
            if f is not None and f.rows is not None:
                picked.append((o, f, (loc - 1) & 0xFFFFFFFF))
        out = self._score_mem(mem, q, d, within)
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
                "spaces": {name: {"dim": s.dim, "rows": s.rows(), "files": len(s.files)}
                           for name, s in self.spaces.items()},
                "waiting": self.pending(), "embedded": self.embedded}

    def close(self) -> None:
        with self._todo_cond:
            self._stop = True
            self._todo_cond.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=5)
        if self.queue is not None:
            self.queue.close()
