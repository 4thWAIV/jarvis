"""Immutable, checksummed segment files (design section 7.5).

A segment holds versions sorted by (key ascending, LSN descending): one
record is `u16 key length | u64 lsn | u8 flags | u32 value length | key |
value`, flag 1 a tombstone.

Layout: a header (`KSEG`, format, family, generation), then blocks, each
`u8 compressed | u32 stored | u32 raw | u32 crc32(stored)` and its bytes,
holding at most `BLOCK_BYTES` of records (one record larger than that has
a block of its own), compressed with zlib level 1 when that saves a
quarter; then a footer (the sparse index: each block's first key, offset
and stored size; a bloom filter over keys at 10 bits per key with
`blake2b`; the LSN range and the record count), and a trailer with the
footer's offset, length and CRC and the magic again.

Every block is checked against its CRC each time it is read into the block
pool (`BlockPool`, a 2Q cache: a probation FIFO and a protected LRU, so a
long scan cannot flush the hot set). Files are written as `.partial`,
`F_FULLFSYNC`ed and renamed, so a crash leaves a whole file or none.
"""
from __future__ import annotations

import bisect
import functools
import hashlib
import os
import struct
import threading
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from jarvis.knowledge.oscompat import O_BINARY, O_NOFOLLOW, pread
from jarvis.knowledge.wal import full_fsync

MAGIC = b"KSEG"
FORMAT = 1
HEAD = struct.Struct("<4sHBI")            # magic, format, family, generation
BLOCK_HEAD = struct.Struct("<BIII")       # compressed, stored, raw, crc
REC = struct.Struct("<HQBI")              # key length, lsn, flags, value length
TRAILER = struct.Struct("<QII4s")         # footer offset, footer length, footer crc, magic
TOMBSTONE = 1
BLOCK_BYTES = 64 << 10
# A block larger than this holds one key's long history; reading that
# key's newest version streams it (`Segment.newest`) instead of decoding it.
STREAM_OVER_BYTES = 4 * BLOCK_BYTES
BLOOM_BITS_PER_KEY = 10
BLOOM_HASHES = 7


class SegmentCorrupt(RuntimeError):
    pass


@dataclass(frozen=True)
class Version:
    key: bytes
    lsn: int
    value: Optional[bytes]   # None: a tombstone


def encode_records(versions: Iterable[Version]) -> Iterator[bytes]:
    for v in versions:
        value = v.value or b""
        yield REC.pack(len(v.key), v.lsn, TOMBSTONE if v.value is None else 0, len(value)) + v.key + value


def decode_block(raw: bytes) -> list[Version]:
    out = []
    pos = 0
    view = memoryview(raw)
    while pos < len(raw):
        klen, lsn, flags, vlen = REC.unpack_from(view, pos)
        pos += REC.size
        key = bytes(view[pos:pos + klen])
        pos += klen
        value = None if flags & TOMBSTONE else bytes(view[pos:pos + vlen])
        pos += vlen
        out.append(Version(key, lsn, value))
    return out


def _wanted(v: Version, at: Optional[int], live: bool) -> bool:
    return (at is None or v.lsn <= at) and not (live and v.value is None)


class _RecordStream:
    """A block's records read from the front, decompressing only as far as
    a read asks and never more: `take` returns the next bytes, `skip`
    passes over them, decompressed and dropped, without keeping them."""

    SKIP_STEP = 1 << 20
    TAKE_STEP = 64 << 10
    # Input is fed in pieces: a bounded decompress keeps what it did not
    # use as a copy (`unconsumed_tail`), so the whole input at once would
    # be copied again on every call.
    IN_STEP = 256 << 10

    def __init__(self, body: bytes, compressed: bool) -> None:
        self._z = zlib.decompressobj() if compressed else None
        self._in = memoryview(body)
        self._in_pos = 0
        self._tail = b""
        self._spill = b""
        self._buf = b"" if compressed else body
        self._pos = 0

    def _more(self, n: int) -> bytes:
        """Up to `n` more decompressed bytes after the buffer; empty only at
        the end of the block."""
        if self._z is None or n <= 0:
            return b""
        if self._spill:
            out, self._spill = self._spill[:n], self._spill[n:]
            return out
        while not self._z.eof:
            if self._tail:
                piece = self._tail
            elif self._in_pos < len(self._in):
                piece = self._in[self._in_pos:self._in_pos + self.IN_STEP]
                self._in_pos += len(piece)
            else:
                rest = self._z.flush()
                out, self._spill = rest[:n], rest[n:]
                return out
            out = self._z.decompress(piece, n)
            self._tail = self._z.unconsumed_tail
            if out:
                return out
        return b""

    def take(self, n: int) -> Optional[bytes]:
        """The next `n` bytes, or None at the end of the block. The buffer
        is refilled `TAKE_STEP` at a time: asking zlib for exactly the 15
        bytes of each record's head made one call per record, which walked
        a block of small records several times slower than decoding it."""
        have = len(self._buf) - self._pos
        if have >= n:
            out = self._buf[self._pos:self._pos + n]
            self._pos += n
            return out
        parts = [self._buf[self._pos:]]
        while have < n:
            more = self._more(max(n - have, self.TAKE_STEP))
            if not more:
                return None
            parts.append(more)
            have += len(more)
        self._buf, self._pos = b"".join(parts), n
        return self._buf[:n]

    def skip(self, n: int) -> None:
        have = len(self._buf) - self._pos
        if have >= n:
            self._pos += n
            return
        need = n - have
        self._buf, self._pos = b"", 0
        while need > 0:
            more = self._more(min(need, self.SKIP_STEP))
            if not more:
                return
            need -= len(more)


def _first_in_records(stream: _RecordStream, key: bytes, at: Optional[int],
                      live: bool) -> Optional[Version]:
    """The first version of `key` in a block's records (sorted by key,
    newest first within one) that is wanted; values of other records are
    skipped, never kept."""
    while True:
        head = stream.take(REC.size)
        if head is None:
            return None
        klen, lsn, flags, vlen = REC.unpack(head)
        found = stream.take(klen)
        if found is None:
            return None
        if found > key:
            return None
        tomb = bool(flags & TOMBSTONE)
        if found == key and (at is None or lsn <= at) and not (live and tomb):
            value = None if tomb else stream.take(vlen)
            return Version(found, lsn, value)
        stream.skip(vlen)


@functools.lru_cache(maxsize=4096)
def _bloom_hash(key: bytes) -> tuple[int, int]:
    """A key's two bloom hashes, the same for every segment: one read asks
    every segment about the same key, so it is hashed once."""
    return struct.unpack("<QQ", hashlib.blake2b(key, digest_size=16).digest())


def _bloom_positions(key: bytes, bits: int) -> list[int]:
    h1, h2 = _bloom_hash(key)
    return [(h1 + i * h2) % bits for i in range(BLOOM_HASHES)]


def write_segment(path: Path, family: int, generation: int, versions: Iterable[Version]) -> dict:
    """Write versions (already sorted by key, newest first within a key) to
    `path` through a `.partial` file; returns the manifest entry facts."""
    partial = path.with_name(path.name + ".partial")
    first_keys: list[bytes] = []
    offsets: list[int] = []
    keys: list[bytes] = []
    lsn_min, lsn_max, count = None, None, 0
    sha = hashlib.sha256()
    fd = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_BINARY, 0o600)
    pos = 0

    def put(data: bytes) -> None:
        nonlocal pos
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        sha.update(data)
        pos += len(data)

    def flush_block(records: list[bytes], first: bytes) -> None:
        raw = b"".join(records)
        packed = zlib.compress(raw, 1)
        compressed = len(packed) <= len(raw) * 3 // 4
        stored = packed if compressed else raw
        first_keys.append(first)
        offsets.append(pos)
        put(BLOCK_HEAD.pack(1 if compressed else 0, len(stored), len(raw), zlib.crc32(stored)) + stored)

    try:
        put(HEAD.pack(MAGIC, FORMAT, family, generation))
        block: list[bytes] = []
        block_size = 0
        block_first = b""
        last_key = None
        for rec, v in ((r, v) for v in versions for r in encode_records((v,))):
            if block and block_size + len(rec) > BLOCK_BYTES and v.key != last_key:
                flush_block(block, block_first)
                block, block_size = [], 0
            if not block:
                block_first = v.key
            block.append(rec)
            block_size += len(rec)
            if v.key != last_key:
                keys.append(v.key)
            last_key = v.key
            count += 1
            lsn_min = v.lsn if lsn_min is None else min(lsn_min, v.lsn)
            lsn_max = v.lsn if lsn_max is None else max(lsn_max, v.lsn)
        if block:
            flush_block(block, block_first)
        bits = max(64, len(keys) * BLOOM_BITS_PER_KEY)
        bloom = bytearray((bits + 7) // 8)
        for k in keys:
            for p in _bloom_positions(k, bits):
                bloom[p >> 3] |= 1 << (p & 7)
        footer = bytearray()
        footer += struct.pack("<IQQQI", len(first_keys), lsn_min or 0, lsn_max or 0, count, bits)
        for k, off in zip(first_keys, offsets):
            footer += struct.pack("<HQ", len(k), off) + k
        footer += bloom
        footer_offset = pos
        put(bytes(footer))
        put(TRAILER.pack(footer_offset, len(footer), zlib.crc32(bytes(footer)), MAGIC))
        full_fsync(fd)
    finally:
        os.close(fd)
    os.replace(partial, path)
    return {"file": path.name, "sha256": sha.hexdigest(), "bytes": pos,
            "lsn_min": lsn_min or 0, "lsn_max": lsn_max or 0, "count": count, "keys": len(keys)}


def file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


class BlockPool:
    """2Q: new blocks enter a probation FIFO (a quarter of the budget); a
    block read again while there moves to the protected LRU. A block asked
    for as `pinned` enters protected directly."""

    def __init__(self, budget_bytes: int = 32 << 20) -> None:
        self.budget = budget_bytes
        self._probation: OrderedDict = OrderedDict()
        self._protected: OrderedDict = OrderedDict()
        self._size_prob = 0
        self._size_prot = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, ident):
        with self._lock:
            if ident in self._protected:
                self._protected.move_to_end(ident)
                self.hits += 1
                return self._protected[ident][0]
            if ident in self._probation:
                entry = self._probation.pop(ident)
                self._size_prob -= entry[1]
                self._protected[ident] = entry
                self._size_prot += entry[1]
                self._trim()
                self.hits += 1
                return entry[0]
            self.misses += 1
            return None

    def put(self, ident, versions, size: int, pinned: bool = False) -> None:
        with self._lock:
            if ident in self._protected or ident in self._probation:
                return
            if pinned:
                self._protected[ident] = (versions, size)
                self._size_prot += size
            else:
                self._probation[ident] = (versions, size)
                self._size_prob += size
            self._trim()

    def _trim(self) -> None:
        while self._size_prob > self.budget // 4 and self._probation:
            _, (_, size) = self._probation.popitem(last=False)
            self._size_prob -= size
        while self._size_prob + self._size_prot > self.budget and self._protected:
            _, (_, size) = self._protected.popitem(last=False)
            self._size_prot -= size

    def drop_file(self, file_id: str) -> None:
        with self._lock:
            for table, attr in ((self._probation, "_size_prob"), (self._protected, "_size_prot")):
                for ident in [i for i in table if i[0] == file_id]:
                    setattr(self, attr, getattr(self, attr) - table.pop(ident)[1])

    def clear(self) -> None:
        with self._lock:
            self._probation.clear()
            self._protected.clear()
            self._size_prob = self._size_prot = 0

    @property
    def size(self) -> int:
        return self._size_prob + self._size_prot


class Segment:
    """One open segment: its sparse index and bloom filter resident, its
    blocks read with `pread` through the pool."""

    def __init__(self, path: Path, pool: BlockPool, *, expect_sha256: Optional[str] = None) -> None:
        self.path = path
        self.pool = pool
        self.file_id = str(path)
        if expect_sha256 is not None and file_sha256(path) != expect_sha256:
            raise SegmentCorrupt(f"{path.name}: SHA-256 does not match the manifest")
        self.fd = os.open(str(path), os.O_RDONLY | O_NOFOLLOW | O_BINARY)
        size = os.fstat(self.fd).st_size
        if size < HEAD.size + TRAILER.size:
            raise SegmentCorrupt(f"{path.name}: too short")
        magic, fmt, self.family, self.generation = HEAD.unpack(pread(self.fd, HEAD.size, 0))
        if magic != MAGIC or fmt != FORMAT:
            raise SegmentCorrupt(f"{path.name}: not a segment of this format")
        foff, flen, fcrc, magic2 = TRAILER.unpack(pread(self.fd, TRAILER.size, size - TRAILER.size))
        footer = pread(self.fd, flen, foff)
        if magic2 != MAGIC or len(footer) != flen or zlib.crc32(footer) != fcrc:
            raise SegmentCorrupt(f"{path.name}: footer fails its CRC")
        nblocks, self.lsn_min, self.lsn_max, self.count, self.bloom_bits = struct.unpack_from("<IQQQI", footer, 0)
        pos = struct.calcsize("<IQQQI")
        self.first_keys: list[bytes] = []
        self.offsets: list[int] = []
        for _ in range(nblocks):
            klen, off = struct.unpack_from("<HQ", footer, pos)
            pos += 10
            self.first_keys.append(bytes(footer[pos:pos + klen]))
            pos += klen
            self.offsets.append(off)
        self.ends = self.offsets[1:] + [foff]
        self.bloom = bytes(footer[pos:])
        self.bytes = size
        self._raw_sizes: dict[int, int] = {}

    def close(self) -> None:
        self.pool.drop_file(self.file_id)
        try:
            os.close(self.fd)
        except OSError:
            pass

    def might_have(self, key: bytes) -> bool:
        return all(self.bloom[p >> 3] & (1 << (p & 7)) for p in _bloom_positions(key, self.bloom_bits))

    def _block(self, index: int) -> tuple[list[Version], list[bytes]]:
        """A block's versions and, beside them, their keys for bisection."""
        ident = (self.file_id, index)
        cached = self.pool.get(ident)
        if cached is not None:
            return cached
        off, end = self.offsets[index], self.ends[index]
        data = pread(self.fd, end - off, off)
        comp, stored, raw, crc = BLOCK_HEAD.unpack_from(data, 0)
        body = data[BLOCK_HEAD.size:BLOCK_HEAD.size + stored]
        if len(body) != stored or zlib.crc32(body) != crc:
            raise SegmentCorrupt(f"{self.path.name}: block {index} fails its CRC")
        raw_bytes = zlib.decompress(body) if comp else body
        versions = decode_block(raw_bytes)
        entry = (versions, [v.key for v in versions])
        self.pool.put(ident, entry, raw)
        return entry

    def block(self, index: int) -> list[Version]:
        return self._block(index)[0]

    def versions(self, key: bytes) -> list[Version]:
        """Every version of `key` here, newest first."""
        # The writer never splits one key's versions across blocks.
        return list(self.iter_versions(key))

    def newest(self, key: bytes, at: Optional[int] = None, live: bool = False) -> Optional[Version]:
        """The newest version of `key` here at or below `at` (with `live`,
        not a tombstone), or None. An ordinary block is searched decoded and
        kept in the pool as any read keeps it. A block over
        `STREAM_OVER_BYTES` holds one key's long history (one key's versions
        are never split across blocks, so a document rewritten thousands of
        times fills a block of hundreds of megabytes): it is never decoded
        whole. Its first read walks it once and keeps only each key's newest
        and newest live version (`_heads`), the answer whenever that version
        is at or below `at`; a read at an earlier `at` walks the block to
        the version it wants."""
        if not self.first_keys or not self.might_have(key):
            return None
        index = max(0, bisect.bisect_right(self.first_keys, key) - 1)
        cached = self._decoded(index)
        if cached is not None:
            versions, keys = cached
            lo = bisect.bisect_left(keys, key)
            hi = bisect.bisect_right(keys, key, lo)
            return next((v for v in versions[lo:hi] if _wanted(v, at, live)), None)
        heads = self._heads(index).get(key)
        if heads is None:
            return None
        newest = heads[1 if live else 0]
        if newest is None or at is None or newest.lsn <= at:
            return newest
        return _first_in_records(self._records(index), key, at, live)

    def _raw_size(self, index: int) -> int:
        size = self._raw_sizes.get(index)
        if size is None:
            size = self._raw_sizes[index] = BLOCK_HEAD.unpack(
                pread(self.fd, BLOCK_HEAD.size, self.offsets[index]))[2]
        return size

    def _decoded(self, index: int) -> Optional[tuple[list[Version], list[bytes]]]:
        """The block decoded (from the pool, or read and kept there), or
        None for a block over `STREAM_OVER_BYTES` not already held, which
        is streamed instead."""
        cached = self.pool.get((self.file_id, index))
        if cached is None and self._raw_size(index) <= STREAM_OVER_BYTES:
            cached = self._block(index)
        return cached

    def iter_versions(self, key: bytes) -> Iterator[Version]:
        """Every version of `key` here, newest first, decoded one at a time
        as the caller takes them: a reader that wants the newest few of a
        long history stops early, and an oversized block is streamed, never
        decoded whole."""
        if not self.first_keys or not self.might_have(key):
            return
        index = max(0, bisect.bisect_right(self.first_keys, key) - 1)
        cached = self._decoded(index)
        if cached is not None:
            versions, keys = cached
            lo = bisect.bisect_left(keys, key)
            yield from versions[lo:bisect.bisect_right(keys, key, lo)]
            return
        stream = self._records(index)
        while True:
            head = stream.take(REC.size)
            if head is None:
                return
            klen, lsn, flags, vlen = REC.unpack(head)
            found = stream.take(klen)
            if found is None or found > key:
                return
            if found < key:
                stream.skip(vlen)
                continue
            yield Version(found, lsn, None if flags & TOMBSTONE else stream.take(vlen))

    def scan_newest(self, start: bytes = b"", end: Optional[bytes] = None,
                    at: Optional[int] = None) -> Iterator[Version]:
        """Per key with start <= key < end, in key order, its newest version
        here at or below `at` (a tombstone included): what a read of the
        newest versions needs from this segment, and no more. An item's
        long history is never decoded: an ordinary block is searched as the
        pool holds it, an oversized one through its heads (`_heads`), and
        walked only for a key whose newest version is above `at`."""
        if not self.first_keys or (at is not None and self.lsn_min > at):
            return
        index = max(0, bisect.bisect_right(self.first_keys, start) - 1)
        for i in range(index, len(self.first_keys)):
            if end is not None and self.first_keys[i] >= end:
                return
            cached = self._decoded(i)
            if cached is not None:
                versions, keys = cached
                pos = bisect.bisect_left(keys, start) if i == index else 0
                last = None
                for v in versions[pos:] if pos else versions:
                    if v.key == last:
                        continue
                    if end is not None and v.key >= end:
                        return
                    if at is not None and v.lsn > at:
                        continue
                    last = v.key
                    yield v
                continue
            for k, (newest, _live) in self._heads(i).items():
                if k < start:
                    continue
                if end is not None and k >= end:
                    return
                if at is None or newest.lsn <= at:
                    yield newest
                else:
                    found = _first_in_records(self._records(i), k, at, False)
                    if found is not None:
                        yield found

    def _records(self, index: int) -> "_RecordStream":
        off, end = self.offsets[index], self.ends[index]
        data = pread(self.fd, end - off, off)
        comp, stored, _raw, crc = BLOCK_HEAD.unpack_from(data, 0)
        body = data[BLOCK_HEAD.size:BLOCK_HEAD.size + stored]
        if len(body) != stored or zlib.crc32(body) != crc:
            raise SegmentCorrupt(f"{self.path.name}: block {index} fails its CRC")
        return _RecordStream(body, bool(comp))

    def _heads(self, index: int) -> dict[bytes, tuple[Version, Optional[Version]]]:
        """Each key of an oversized block with its newest version and its
        newest live one, from one walk of the block, kept in the pool."""
        ident = (self.file_id, index, "heads")
        found = self.pool.get(ident)
        if found is not None:
            return found
        heads: dict[bytes, tuple[Version, Optional[Version]]] = {}
        stream = self._records(index)
        while True:
            head = stream.take(REC.size)
            if head is None:
                break
            klen, lsn, flags, vlen = REC.unpack(head)
            k = stream.take(klen)
            if k is None:
                break
            tomb = bool(flags & TOMBSTONE)
            have = heads.get(k)
            if have is None:
                v = Version(k, lsn, None if tomb else stream.take(vlen))
                heads[k] = (v, None if tomb else v)
            elif have[1] is None and not tomb:
                heads[k] = (have[0], Version(k, lsn, stream.take(vlen)))
            else:
                stream.skip(vlen)
        size = sum(len(k) + len(v.value or b"") + (len(lv.value or b"") if lv is not None and lv is not v else 0)
                   for k, (v, lv) in heads.items())
        # Straight to the protected part of the pool: these few versions
        # stand for a walk of the whole block, which a scan's churn through
        # probation would otherwise make again on every read.
        self.pool.put(ident, heads, size, pinned=True)
        return heads

    def scan(self, start: bytes = b"", end: Optional[bytes] = None) -> Iterator[Version]:
        """Every version with start <= key < end, in key order, newest first."""
        if not self.first_keys:
            return
        index = max(0, bisect.bisect_right(self.first_keys, start) - 1)
        for i in range(index, len(self.first_keys)):
            if end is not None and self.first_keys[i] >= end:
                return
            versions, keys = self._block(i)
            lo = bisect.bisect_left(keys, start) if i == index else 0
            for v in versions[lo:]:
                if end is not None and v.key >= end:
                    return
                yield v
