"""The storage core: memtable, segments, reads at an LSN, recovery,
snapshots, compaction and forget (design sections 7.5 to 7.8).

The store is a versioned key-value store of a few families (documents,
the field dictionary, forward and reverse edges, and whatever the parts
add). No version changes in place: each carries the LSN of the transaction
that wrote it, and a read at an LSN sees, per key, the newest version at or
below it, so a traversal or an export never sees half a transaction.

Derived files (the general index, the keyword postings) are not logged:
each part registered with the store builds its files for a generation from
the newest versions in that generation, at flush and again at compaction,
and they go into the manifest with the generation's own segments, so a
crash leaves both or neither (section 7.17.8).

Compaction is size-tiered and works on immutable inputs only, so it can run
in a child process (`python -m jarvis.knowledge compact`) stopped and
continued with the floor (I4). Only the engine changes the manifest (build
plan: one writer of CURRENT).

Every write takes the commit lock: its operations are planned under it
(`commit(prepare=...)`, given the LSN the transaction will have), so a
condition checked there holds when the write lands, and the LSN is known
before the write (the change feed is keyed by it). Item histories are kept:
compaction keeps every version of an item's document and content (keys that
are paths, `HISTORY_FAMILIES`) until it is forgotten.

Forget removes every copy: tombstones at once, a flush that rotates the
log and deletes every file below the checkpoint, a compaction of every
generation that holds the keys with all their versions purged, every
snapshot holding them deleted, and the block pool cleared.
"""
from __future__ import annotations

import bisect
import heapq
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Protocol

from jarvis.knowledge import lanes
from jarvis.knowledge import manifest as mf
from jarvis.knowledge import wal as wal_mod
from jarvis.knowledge.segment import (BlockPool, Segment, SegmentCorrupt, Version,
                                      write_segment)
from jarvis.knowledge.wal import WAL, Op

log = logging.getLogger("jarvis.knowledge.store")

# Families. Codes are written into segment headers and log records.
DOCS = 1
FIELDS = 2
EDGES_OUT = 3
EDGES_IN = 4
META = 6          # ingest watermarks, written with the documents they describe
CONTENT = 7       # an item's content stream: its text, sections and file reference
FEED = 8          # the change feed: one record per item transaction, keyed by its LSN
RIGHTS = 9        # AccessRights: the grants set on a path, keyed by the path
FAMILY_EXT = {DOCS: "docs", FIELDS: "dict", EDGES_OUT: "eout", EDGES_IN: "ein", META: "meta",
              CONTENT: "cont", FEED: "feed", RIGHTS: "rght"}
HISTORY_FAMILIES = (DOCS, CONTENT)

MEMTABLE_MAX_BYTES = 32 << 20
FLUSH_AFTER_SEC = 60.0
SNAPSHOTS_KEPT = 3
TIER_FANOUT = 4
TIER_BASE_BYTES = 1 << 20


class StoreReadOnly(RuntimeError):
    """Writes are refused: the log is corrupt below its durable mark, or the
    store was opened from a snapshot."""


class DerivedPart(Protocol):
    """A part that builds files of its own for each generation."""
    name: str
    families: dict[int, str]           # derived family code -> file extension

    def build(self, gen: int, newest: Callable[[int], Iterator[Version]],
              write: Callable[[int, Iterable[Version]], dict],
              read_family: Callable[[int], Iterator[Version]]) -> None:
        """Write this generation's derived segments from the newest version
        of every key in it (`newest(family)`), each through `write`;
        `read_family` reads a logged family's whole live content (the field
        dictionary, for one)."""


class ExternalFamily(Protocol):
    """A logged family whose generations are files a part writes itself
    (vector rows), not key-value segments. Its manifest entries carry
    `external: true` and the `files` they consist of."""
    name: str
    owns: dict[int, str]

    def write_family(self, directory: Path, gen: int, family: int,
                     versions: Iterator[Version]) -> list[dict]: ...

    def compact_family(self, directory: Path, family: int, inputs: list[dict], out_gen: int,
                       purge: set[bytes], drop_tombstones: bool) -> list[dict]: ...

    def holds(self, directory: Path, entry: dict, keys: set[bytes]) -> bool: ...


def entry_files(directory: Path, entry: dict) -> list[Path]:
    if entry.get("external"):
        return [directory / f for f in entry["files"]]
    return [directory / "seg" / entry["file"]]


def externals_of(parts: Iterable) -> dict[int, "ExternalFamily"]:
    return {family: part for part in parts for family in getattr(part, "owns", {})}


class Memtable:
    def __init__(self) -> None:
        self.versions: dict[int, dict[bytes, list[tuple[int, Optional[bytes]]]]] = {}
        self.sorted_keys: dict[int, list[bytes]] = {}
        self.bytes = 0
        self.lsn_max = 0
        self.first_write: Optional[float] = None

    def apply(self, lsn: int, ops: list[Op]) -> None:
        for op in ops:
            table = self.versions.setdefault(op.family, {})
            entry = table.get(op.key)
            if entry is None:
                table[op.key] = entry = []
                bisect.insort(self.sorted_keys.setdefault(op.family, []), op.key)
            entry.append((lsn, op.value))
            self.bytes += len(op.key) + len(op.value or b"") + 24
        self.lsn_max = max(self.lsn_max, lsn)
        if self.first_write is None:
            self.first_write = time.monotonic()

    def versions_of(self, family: int, key: bytes) -> list[Version]:
        entry = self.versions.get(family, {}).get(key)
        return [Version(key, lsn, value) for lsn, value in reversed(entry)] if entry else []

    def scan(self, family: int, start: bytes, end: Optional[bytes]) -> Iterator[Version]:
        keys = self.sorted_keys.get(family, [])
        lo = bisect.bisect_left(keys, start)
        hi = len(keys) if end is None else bisect.bisect_left(keys, end)
        table = self.versions.get(family, {})
        for key in keys[lo:hi]:
            for lsn, value in reversed(table[key]):
                yield Version(key, lsn, value)

    def all_versions(self, family: int) -> Iterator[Version]:
        return self.scan(family, b"", None)

    def families(self) -> list[int]:
        return sorted(self.versions)


def newest_only(versions: Iterator[Version], at: Optional[int] = None) -> Iterator[Version]:
    """Per key (versions arrive key ascending, newest first), the newest
    version at or below `at`, tombstones included."""
    last = None
    for v in versions:
        if v.key == last:
            continue
        if at is not None and v.lsn > at:
            continue
        last = v.key
        yield v


def merge(sources: list[Iterator[Version]]) -> Iterator[Version]:
    """k-way merge of version streams into key ascending, newest first."""
    return heapq.merge(*sources, key=lambda v: (v.key, -v.lsn))


@dataclass
class Stats:
    flushes: int = 0
    compactions: int = 0
    last_flush: Optional[float] = None
    last_compaction: Optional[float] = None
    forgets_pending: int = 0


class Store:
    def __init__(self, directory: Path, *, parts: Iterable[DerivedPart] = (),
                 pool: Optional[BlockPool] = None, compact_in_child: bool = False) -> None:
        self.directory = directory
        self.seg_dir = directory / "seg"
        self.snap_dir = directory / "snap"
        self.wal_dir = directory / "wal"
        for d in (self.seg_dir, self.snap_dir, self.wal_dir):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.parts = list(parts)
        self.family_ext = dict(FAMILY_EXT)
        for part in self.parts:
            self.family_ext.update(part.families)
        self.external = externals_of(self.parts)
        self.pool = pool or BlockPool()
        self.compact_in_child = compact_in_child
        self.state = "ok"
        self.problems: list[str] = []
        self.writable = True
        self.stats = Stats()
        self._lock = threading.RLock()        # guards mem, frozen, segments, manifest
        self._flush_lock = threading.Lock()
        self._commit_lock = threading.Lock()   # one write planned and logged at a time
        self._compact_lock = threading.Lock()
        self._pins: dict[int, int] = {}
        self._retired: list[Segment] = []
        self.mem = Memtable()
        self.frozen: list[Memtable] = []
        self.segments: dict[int, list[tuple[int, Segment]]] = {}
        self.applied_lsn = 0
        self.listeners: list[Callable[[int, list[Op]], None]] = []
        self.generation_listeners: list[Callable[[int, list[int]], None]] = []
        self.child: Optional[subprocess.Popen] = None
        self.paused = False
        self.engine_id = mf.ensure_format(directory)
        self.manifest = self._load_manifest()
        self._open_segments()
        self.wal: Optional[WAL] = None
        self._recover()

    # -- opening ----------------------------------------------------------
    def _load_manifest(self) -> mf.Manifest:
        try:
            found, problems = mf.load(self.directory)
        except mf.ManifestCorrupt as e:
            self._degrade(f"manifest: {e}")
            return self._from_snapshot() or mf.Manifest()
        self.problems.extend(problems)
        return found or mf.Manifest()

    def _from_snapshot(self) -> Optional[mf.Manifest]:
        for snap in reversed(self.snapshots()):
            try:
                found = mf.read_file(snap / "MANIFEST")
                for entry in found.segments:
                    target = self.seg_dir / entry["file"]
                    if not target.exists():
                        os.link(snap / entry["file"], target)
                log.warning("reading from snapshot %s", snap.name)
                self.writable = False
                return found
            except (OSError, ValueError, mf.ManifestCorrupt) as e:
                self.problems.append(f"snapshot {snap.name}: {e}")
        return None

    def _degrade(self, problem: str) -> None:
        log.error("knowledge store degraded: %s", problem)
        self.state = "degraded"
        self.problems.append(problem)

    def _open_segments(self) -> None:
        segments: dict[int, list[tuple[int, Segment]]] = {}
        try:
            for entry in self.manifest.segments:
                if entry.get("external"):
                    continue
                seg = Segment(self.seg_dir / entry["file"], self.pool, expect_sha256=entry["sha256"])
                segments.setdefault(int(entry["family"]), []).append((int(entry["gen"]), seg))
        except (OSError, SegmentCorrupt) as e:
            for group in segments.values():
                for _, seg in group:
                    seg.close()
            self._degrade(f"segment: {e}")
            found = self._from_snapshot()
            self.manifest = found or mf.Manifest()
            if found is None:
                self.writable = False
                self.segments = {}
                return
            return self._open_segments()
        self.segments = segments

    def _recover(self) -> None:
        checkpoint = self.manifest.checkpoint_lsn
        found = wal_mod.scan(self.wal_dir)
        records = found.records
        if found.bad_at is not None:
            first_bad = wal_mod.first_lsn_of_bad(found, checkpoint)
            durable = wal_mod.max_durable(found)
            path, offset = found.bad_at
            if first_bad <= checkpoint:
                # Below the checkpoint: those records are in segments already.
                records = records + found.later_good
            elif durable >= first_bad:
                self._degrade(f"log record in {path.name} at {offset} is bad at or below "
                              f"the durable LSN {durable}")
                self.writable = False
            else:
                log.warning("cutting the log's torn tail at %s:%s (durable LSN %s)",
                            path.name, offset, durable)
                os.truncate(path, offset)
                for later in wal_mod.wal_files(self.wal_dir):
                    if int(later.stem) > int(path.stem):
                        later.unlink()
        last = checkpoint
        for lsn, ops in wal_mod.committed(records, checkpoint):
            self._apply(lsn, ops)
            last = lsn
        for rec in records:
            last = max(last, rec.lsn)
        self.applied_lsn = max(self.applied_lsn, checkpoint)
        if self.writable:
            # Writes are serialised by the commit lock, so the log never
            # has a group to wait for.
            self.wal = WAL(self.wal_dir, last + 1, self._apply, group_window=0.0)

    # -- writing ----------------------------------------------------------
    def _apply(self, lsn: int, ops: list[Op]) -> None:
        with self._lock:
            self.mem.apply(lsn, ops)
            self.applied_lsn = max(self.applied_lsn, lsn)
        for listener in self.listeners:
            listener(lsn, ops)

    @lanes.foreground()
    def commit(self, ops: Iterable[Op] = (), *, prepare: Optional[Callable[[int], list[Op]]] = None,
               full: bool = False) -> int:
        """Write one transaction; returns its LSN once it is durable against
        a crash of the process and visible to reads. `prepare(lsn)` runs
        under the commit lock, given the LSN this transaction will have, and
        returns more operations (or raises, and nothing is written). `full`
        empties the drive's cache before it returns (F_FULLFSYNC)."""
        if not self.writable or self.wal is None:
            raise StoreReadOnly("; ".join(self.problems) or "read only")
        ops = list(ops)
        with self._commit_lock:
            wal = self.wal
            if wal is None:
                raise StoreReadOnly("closed")
            expected = wal.next_lsn
            if prepare is not None:
                ops += prepare(expected)
            if not ops:
                return self.applied_lsn
            lsn = wal.submit(ops)
            if lsn != expected:
                raise RuntimeError(f"the log gave LSN {lsn}, not {expected}: a write bypassed the commit lock")
            if full:
                wal.full_barrier()
        if self.mem.bytes > MEMTABLE_MAX_BYTES:
            self.flush()
        return lsn

    def maybe_flush(self) -> bool:
        """The time rule: flush when writes have waited `FLUSH_AFTER_SEC`."""
        first = self.mem.first_write
        if first is not None and time.monotonic() - first >= FLUSH_AFTER_SEC:
            self.flush()
            return True
        return False

    # -- reading ----------------------------------------------------------
    @property
    def lsn(self) -> int:
        return self.applied_lsn

    @contextmanager
    def reading(self, at: Optional[int] = None):
        """Pin a snapshot LSN for the length of a read: compaction keeps
        every version a pinned read may need."""
        with self._lock:
            lsn = self.applied_lsn if at is None else min(at, self.applied_lsn)
            self._pins[lsn] = self._pins.get(lsn, 0) + 1
        try:
            yield lsn
        finally:
            with self._lock:
                self._pins[lsn] -= 1
                if not self._pins[lsn]:
                    del self._pins[lsn]
                if not self._pins:
                    self._close_retired()

    def oldest_pin(self) -> int:
        with self._lock:
            return min(self._pins) if self._pins else self.applied_lsn

    def _sources(self, family: int) -> tuple[list[Memtable], list[tuple[int, Segment]]]:
        with self._lock:
            return [self.mem, *self.frozen], list(self.segments.get(family, []))

    def versions(self, family: int, key: bytes) -> list[Version]:
        """Every version of `key` the store holds, newest first."""
        mems, segs = self._sources(family)
        out: list[Version] = []
        for mem in mems:
            with self._lock:
                out.extend(mem.versions_of(family, key))
        for _, seg in segs:
            out.extend(seg.versions(key))
        out.sort(key=lambda v: -v.lsn)
        return out

    def get(self, family: int, key: bytes, at: Optional[int] = None) -> Optional[Version]:
        """The newest version at or below `at`, or None; a tombstone comes
        back as a Version whose value is None."""
        for v in self.versions(family, key):
            if at is None or v.lsn <= at:
                return v
        return None

    def value(self, family: int, key: bytes, at: Optional[int] = None) -> Optional[bytes]:
        v = self.get(family, key, at)
        return None if v is None else v.value

    def scan(self, family: int, start: bytes = b"", end: Optional[bytes] = None,
             at: Optional[int] = None, *, tombstones: bool = False) -> Iterator[Version]:
        """The newest version of every key in [start, end) at `at`."""
        mems, segs = self._sources(family)
        sources: list[Iterator[Version]] = []
        for mem in mems:
            with self._lock:
                sources.append(iter(list(mem.scan(family, start, end))))
        for _, seg in segs:
            sources.append(seg.scan(start, end))
        for v in newest_only(merge(sources), at):
            if v.value is not None or tombstones:
                yield v

    def prefix(self, family: int, prefix: bytes, at: Optional[int] = None) -> Iterator[Version]:
        return self.scan(family, prefix, prefix_end(prefix), at)

    def derived(self, family: int) -> list[tuple[int, Segment]]:
        """A derived family's segments, newest generation first."""
        with self._lock:
            return sorted(self.segments.get(family, []), key=lambda gs: -gs[0])

    # -- flushing ---------------------------------------------------------
    def flush(self) -> Optional[int]:
        """Write the memtable as a new generation; returns it, or None when
        there was nothing to write."""
        if not self.writable or self.wal is None:
            return None
        with lanes.foreground(), self._flush_lock:
            with self.wal.exclusive():
                with self._lock:
                    if not self.mem.versions:
                        return None
                    frozen, self.mem = self.mem, Memtable()
                    self.frozen.append(frozen)
                    checkpoint = self.applied_lsn
                    gen = self.manifest.next_gen
                    self.manifest.next_gen += 1
                self.wal.rotate()
            entries = self._write_generation(gen, lambda family: frozen.all_versions(family),
                                             frozen.families())
            with self._lock:
                new = mf.Manifest(number=self.manifest.number + 1, checkpoint_lsn=checkpoint,
                                  next_gen=self.manifest.next_gen,
                                  segments=self.manifest.segments + entries,
                                  parts=self.manifest.parts)
                mf.install(self.directory, new)
                self.manifest = new
                self._add_segments(entries)
                self.frozen.remove(frozen)
            self.wal.delete_below(checkpoint)
            self._announce(gen, [])
            self.stats.flushes += 1
            self.stats.last_flush = time.time()
            return gen

    def _write_generation(self, gen: int, versions_of: Callable[[int], Iterator[Version]],
                          families: Iterable[int]) -> list[dict]:
        return write_generation(self.seg_dir, gen, versions_of, families, self.parts, self.family_ext,
                                lambda family: self.scan(family))

    def external_entries(self, family: int) -> list[dict]:
        with self._lock:
            return [e for e in self.manifest.segments
                    if e.get("external") and int(e["family"]) == family]

    def _announce(self, gen: int, replaced: list[int]) -> None:
        for listener in self.generation_listeners:
            listener(gen, replaced)

    def _add_segments(self, entries: list[dict]) -> None:
        for entry in entries:
            if entry.get("external"):
                continue
            seg = Segment(self.seg_dir / entry["file"], self.pool)
            self.segments.setdefault(int(entry["family"]), []).append((int(entry["gen"]), seg))

    def _close_retired(self) -> None:
        retired, self._retired = self._retired, []
        for seg in retired:
            seg.close()

    # -- snapshots --------------------------------------------------------
    def snapshot(self) -> Path:
        """Flush, then hard-link the live segments and copy the manifest
        into snap/<checkpoint>/; keep the newest `SNAPSHOTS_KEPT`."""
        self.flush()
        with lanes.foreground(), self._lock:
            current = self.manifest
            target = self.snap_dir / str(current.checkpoint_lsn)
            if not target.exists():
                tmp = self.snap_dir / f".{current.checkpoint_lsn}.partial"
                shutil.rmtree(tmp, ignore_errors=True)
                tmp.mkdir(mode=0o700)
                # Vector rows stay out: they are the largest files and are
                # always made again by embedding (section 7.7).
                kept = [e for e in current.segments if not e.get("external")]
                for entry in kept:
                    os.link(self.seg_dir / entry["file"], tmp / entry["file"])
                mf.write_file(tmp / "MANIFEST", mf.Manifest(
                    number=current.number, checkpoint_lsn=current.checkpoint_lsn,
                    next_gen=current.next_gen, segments=kept, parts=current.parts))
                os.replace(tmp, target)
        snaps = sorted((p for p in self.snap_dir.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
        for old in snaps[:-SNAPSHOTS_KEPT]:
            shutil.rmtree(old, ignore_errors=True)
        return target

    def newest_snapshot_age(self) -> Optional[float]:
        """Seconds since the newest snapshot was made, or None with none."""
        snaps = self.snapshots()
        if not snaps:
            return None
        try:
            return max(0.0, time.time() - snaps[-1].stat().st_mtime)
        except OSError:
            return None

    def drop_external(self, family: int) -> int:
        """Drop every file of an external family (vector rows), so it is
        made again from the documents; returns how many entries went."""
        self.flush()
        with self._lock:
            gone = [e for e in self.manifest.segments if e.get("external") and int(e["family"]) == family]
            if not gone:
                return 0
            new = mf.Manifest(number=self.manifest.number + 1, checkpoint_lsn=self.manifest.checkpoint_lsn,
                              next_gen=self.manifest.next_gen,
                              segments=[e for e in self.manifest.segments if e not in gone],
                              parts=self.manifest.parts)
            mf.install(self.directory, new)
            self.manifest = new
        for entry in gone:
            for path in entry_files(self.directory, entry):
                path.unlink(missing_ok=True)
        return len(gone)

    def snapshots(self) -> list[Path]:
        return sorted((p for p in self.snap_dir.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))

    # -- compaction -------------------------------------------------------
    def generation_sizes(self) -> dict[int, int]:
        sizes: dict[int, int] = {}
        for entry in self.manifest.segments:
            sizes[int(entry["gen"])] = sizes.get(int(entry["gen"]), 0) + int(entry["bytes"])
        return sizes

    def pick_tier(self) -> Optional[list[int]]:
        """Size-tiered: TIER_FANOUT generations of similar size."""
        tiers: dict[int, list[int]] = {}
        for gen, size in sorted(self.generation_sizes().items()):
            tier = 0
            while size > TIER_BASE_BYTES * (TIER_FANOUT ** tier):
                tier += 1
            tiers.setdefault(tier, []).append(gen)
        for tier in sorted(tiers):
            if len(tiers[tier]) >= TIER_FANOUT:
                return tiers[tier][:TIER_FANOUT]
        return None

    def compact(self, gens: Optional[list[int]] = None, *, purge: Optional[dict[int, set[bytes]]] = None) -> Optional[int]:
        """Merge `gens` (or a tier) into one new generation; `purge` drops
        every version of the given keys, whatever pins them."""
        with self._compact_lock:
            with lanes.foreground(), self._lock:
                gens = gens if gens is not None else self.pick_tier()
                if not gens:
                    return None
                gens = sorted(set(gens))
                inputs = [e for e in self.manifest.segments if int(e["gen"]) in gens]
                out_gen = self.manifest.next_gen
                self.manifest.next_gen += 1
                oldest_gen = min(self.manifest.generations())
                plan = {"directory": str(self.directory), "inputs": inputs, "out_gen": out_gen,
                        "live": list(self.manifest.segments),
                        "parts": [part.name for part in self.parts],
                        "oldest_pin": self.oldest_pin(),
                        "drop_tombstones": oldest_gen in gens,
                        "purge": {str(f): sorted(k.hex() for k in keys) for f, keys in (purge or {}).items()}}
            entries = self._run_compaction(plan)
            with lanes.foreground(), self._lock:
                kept = [e for e in self.manifest.segments if int(e["gen"]) not in gens]
                new = mf.Manifest(number=self.manifest.number + 1,
                                  checkpoint_lsn=self.manifest.checkpoint_lsn,
                                  next_gen=self.manifest.next_gen, segments=kept + entries,
                                  parts=self.manifest.parts)
                mf.install(self.directory, new)
                self.manifest = new
                for family, group in list(self.segments.items()):
                    still = []
                    for gen, seg in group:
                        if gen in gens:
                            self._retired.append(seg)
                        else:
                            still.append((gen, seg))
                    self.segments[family] = still
                self._add_segments(entries)
                for entry in inputs:
                    for path in entry_files(self.directory, entry):
                        path.unlink(missing_ok=True)
                if not self._pins:
                    self._close_retired()
            self._announce(out_gen, gens)
            self.stats.compactions += 1
            self.stats.last_compaction = time.time()
            return out_gen

    def _run_compaction(self, plan: dict) -> list[dict]:
        if not self.compact_in_child:
            return compact_plan(plan, self.parts, self.family_ext)
        plan_path = self.seg_dir / f".compact-{plan['out_gen']}.json"
        result_path = plan_path.with_suffix(".result")
        plan_path.write_text(json.dumps(plan))
        try:
            self.child = subprocess.Popen(
                [sys.executable, "-m", "jarvis.knowledge", "compact", str(plan_path), str(result_path)],
                stdin=subprocess.DEVNULL)
            if self.paused:
                self.child.send_signal(signal.SIGSTOP)
            code = self.child.wait()
            if code != 0:
                raise RuntimeError(f"the compaction child exited with {code}")
            return json.loads(result_path.read_text())
        finally:
            self.child = None
            plan_path.unlink(missing_ok=True)
            result_path.unlink(missing_ok=True)

    def set_paused(self, paused: bool) -> None:
        """Hold or free the compaction child with the floor (I4)."""
        self.paused = paused
        child = self.child
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGSTOP if paused else signal.SIGCONT)

    # -- forgetting -------------------------------------------------------
    def forget(self, keys: dict[int, set[bytes]]) -> dict:
        """Remove every copy of `keys` (family -> keys)."""
        self.stats.forgets_pending += 1
        try:
            ops = [Op(family, key, None) for family, ks in keys.items() for key in sorted(ks)]
            self.commit(ops)
            self.flush()   # rotates the log and deletes every file below the checkpoint
            holding = {gen for family, ks in keys.items()
                       for gen, seg in self.segments.get(family, [])
                       if any(seg.might_have(k) and seg.versions(k) for k in ks)}
            for family, ks in keys.items():
                owner = self.external.get(family)
                if owner is not None:
                    holding |= {int(e["gen"]) for e in self.external_entries(family)
                                if owner.holds(self.directory, e, ks)}
            holding = sorted(holding)
            if holding:
                # Every generation holding any version is merged, so the
                # tombstones can go too.
                self.compact(holding, purge=keys)
            removed = []
            for snap in self.snapshots():
                if _snapshot_holds(snap, keys, self.pool):
                    shutil.rmtree(snap, ignore_errors=True)
                    removed.append(snap.name)
            self.pool.clear()
            return {"generations": holding, "snapshots_removed": removed}
        finally:
            self.stats.forgets_pending -= 1

    # -- closing ----------------------------------------------------------
    def close(self) -> None:
        """A clean close writes the memtable as a generation first, so the
        next start opens segments instead of replaying the log (measured on
        a copy of today's records: about 3 s against 9 s)."""
        if self.wal is not None:
            try:
                self.flush()
            except Exception:  # noqa: BLE001 - the log still holds every write
                log.warning("could not flush at close; the log will be replayed", exc_info=True)
            self.wal.close()
            self.wal = None
        with self._lock:
            for group in self.segments.values():
                for _, seg in group:
                    seg.close()
            self.segments = {}
            self._close_retired()

    def health(self) -> dict:
        return {"state": self.state, "writable": self.writable, "problems": self.problems[-5:],
                "engine_id": self.engine_id, "lsn": self.applied_lsn,
                "durable_lsn": self.wal.durable_lsn if self.wal else self.manifest.checkpoint_lsn,
                "checkpoint_lsn": self.manifest.checkpoint_lsn,
                "generations": len(self.manifest.generations()),
                "segments": len(self.manifest.segments),
                "memtable_bytes": self.mem.bytes, "pool_bytes": self.pool.size,
                "pool_hits": self.pool.hits, "pool_misses": self.pool.misses,
                "flushes": self.stats.flushes, "compactions": self.stats.compactions,
                "last_flush": self.stats.last_flush, "last_compaction": self.stats.last_compaction,
                "pending_forgets": self.stats.forgets_pending,
                "snapshots": [p.name for p in self.snapshots()]}


def prefix_end(prefix: bytes) -> Optional[bytes]:
    """The first key after every key starting with `prefix`."""
    b = bytearray(prefix)
    while b:
        if b[-1] < 0xFF:
            b[-1] += 1
            return bytes(b)
        b.pop()
    return None


def segment_name(gen: int, ext: str) -> str:
    return f"{gen:08d}-0.{ext}"


def write_generation(seg_dir: Path, gen: int, versions_of: Callable[[int], Iterator[Version]],
                     families: Iterable[int], parts: Iterable[DerivedPart],
                     family_ext: dict[int, str],
                     read_family: Callable[[int], Iterator[Version]]) -> list[dict]:
    """Write one generation: a segment per logged family, then each part's
    derived segments from the newest versions."""
    entries: list[dict] = []

    def write(family: int, versions: Iterable[Version]) -> dict:
        entry = write_segment(seg_dir / segment_name(gen, family_ext[family]), family, gen, versions)
        entry.update(gen=gen, family=family)
        entries.append(entry)
        return entry

    externals = externals_of(parts)
    for family in families:
        if family in FAMILY_EXT:
            write(family, versions_of(family))
        elif family in externals:
            for entry in externals[family].write_family(seg_dir.parent, gen, family, versions_of(family)):
                entry.update(gen=gen, family=family, external=True)
                entries.append(entry)
    for part in parts:
        part.build(gen, lambda family: newest_only(versions_of(family)), write, read_family)
    return entries


def compact_plan(plan: dict, parts: Iterable[DerivedPart], family_ext: dict[int, str]) -> list[dict]:
    """Merge the plan's input generations into one: per key, every version
    newer than the oldest pin and the newest at or below it; tombstones
    dropped when the oldest generation is in the merge; purged keys gone
    entirely. An item's document and content (a path key in
    `HISTORY_FAMILIES`) keep every version, tombstones too: they are its
    history. Reads only immutable files, so it runs in a child process."""
    directory = Path(plan["directory"])
    seg_dir = directory / "seg"
    pool = BlockPool(8 << 20)
    by_family: dict[int, list[Segment]] = {}
    external_inputs: dict[int, list[dict]] = {}
    for entry in plan["inputs"]:
        family = int(entry["family"])
        if entry.get("external"):
            external_inputs.setdefault(family, []).append(entry)
        elif family in FAMILY_EXT:
            by_family.setdefault(family, []).append(Segment(seg_dir / entry["file"], pool))
    oldest_pin = int(plan["oldest_pin"])
    drop_tombstones = bool(plan["drop_tombstones"])
    purge = {int(f): {bytes.fromhex(k) for k in keys} for f, keys in plan["purge"].items()}

    def kept(family: int) -> Iterator[Version]:
        gone = purge.get(family, set())
        last_key = None
        below_kept = False
        history = family in HISTORY_FAMILIES
        for v in merge([s.scan() for s in by_family.get(family, [])]):
            if v.key in gone:
                continue
            if history and v.key[:1] == b"/":
                yield v
                continue
            if v.key != last_key:
                last_key, below_kept = v.key, False
            if v.lsn > oldest_pin:
                yield v
                continue
            if below_kept:
                continue
            below_kept = True
            if v.value is None and drop_tombstones:
                continue
            yield v

    live: dict[int, list[Segment]] = {}

    def read_family(family: int) -> Iterator[Version]:
        if family not in live:
            live[family] = [Segment(seg_dir / e["file"], pool) for e in plan.get("live", [])
                            if int(e["family"]) == family and not e.get("external")]
        return (v for v in newest_only(merge([s.scan() for s in live[family]])) if v.value is not None)

    try:
        out = write_generation(seg_dir, int(plan["out_gen"]), kept, sorted(by_family), parts,
                               family_ext, read_family)
        externals = externals_of(parts)
        for family, entries in external_inputs.items():
            owner = externals.get(family)
            if owner is None:
                continue
            for entry in owner.compact_family(directory, family, entries, int(plan["out_gen"]),
                                              purge.get(family, set()), drop_tombstones):
                entry.update(gen=int(plan["out_gen"]), family=family, external=True)
                out.append(entry)
        return out
    finally:
        for group in (*by_family.values(), *live.values()):
            for seg in group:
                seg.close()


def _snapshot_holds(snap: Path, keys: dict[int, set[bytes]], pool: BlockPool) -> bool:
    try:
        found = mf.read_file(snap / "MANIFEST")
    except (OSError, mf.ManifestCorrupt):
        return True
    for entry in found.segments:
        family = int(entry["family"])
        if family not in keys or entry.get("external"):
            continue
        seg = Segment(snap / entry["file"], pool)
        try:
            if any(seg.versions(k) for k in keys[family]):
                return True
        finally:
            seg.close()
    return False


def run_compaction_child(plan_path: Path, result_path: Path) -> int:
    """`python -m jarvis.knowledge compact <plan> <result>`, at background
    priority."""
    try:
        os.setpriority(getattr(os, "PRIO_DARWIN_PROCESS", os.PRIO_PROCESS), 0,
                       getattr(os, "PRIO_DARWIN_BG", 19))
    except (OSError, AttributeError):
        pass
    from jarvis.knowledge.engine import parts_named
    plan = json.loads(plan_path.read_text())
    parts = parts_named(plan.get("parts", []))
    family_ext = dict(FAMILY_EXT)
    for part in parts:
        family_ext.update(part.families)
    entries = compact_plan(plan, parts, family_ext)
    result_path.write_text(json.dumps(entries))
    return 0
