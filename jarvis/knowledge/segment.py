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
import hashlib
import os
import struct
import threading
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from jarvis.knowledge.wal import full_fsync

MAGIC = b"KSEG"
FORMAT = 1
HEAD = struct.Struct("<4sHBI")            # magic, format, family, generation
BLOCK_HEAD = struct.Struct("<BIII")       # compressed, stored, raw, crc
REC = struct.Struct("<HQBI")              # key length, lsn, flags, value length
TRAILER = struct.Struct("<QII4s")         # footer offset, footer length, footer crc, magic
TOMBSTONE = 1
BLOCK_BYTES = 64 << 10
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


def _bloom_positions(key: bytes, bits: int) -> list[int]:
    digest = hashlib.blake2b(key, digest_size=16).digest()
    h1, h2 = struct.unpack("<QQ", digest)
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
    fd = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
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
        self.fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        size = os.fstat(self.fd).st_size
        if size < HEAD.size + TRAILER.size:
            raise SegmentCorrupt(f"{path.name}: too short")
        magic, fmt, self.family, self.generation = HEAD.unpack(os.pread(self.fd, HEAD.size, 0))
        if magic != MAGIC or fmt != FORMAT:
            raise SegmentCorrupt(f"{path.name}: not a segment of this format")
        foff, flen, fcrc, magic2 = TRAILER.unpack(os.pread(self.fd, TRAILER.size, size - TRAILER.size))
        footer = os.pread(self.fd, flen, foff)
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
        data = os.pread(self.fd, end - off, off)
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
        if not self.first_keys or not self.might_have(key):
            return []
        index = max(0, bisect.bisect_right(self.first_keys, key) - 1)
        # The writer never splits one key's versions across blocks.
        versions, keys = self._block(index)
        lo = bisect.bisect_left(keys, key)
        hi = bisect.bisect_right(keys, key, lo)
        return versions[lo:hi]

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
