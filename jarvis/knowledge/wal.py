"""The write-ahead log (design sections 7.5 and 7.6).

A record is `u32 length | u32 crc32 | u64 lsn | u8 type | payload`,
little-endian, the CRC (`zlib.crc32`) over LSN, type and payload, and
`length` the payload's length. A payload over 4 KiB is zlib-compressed and
its type carries `COMPRESSED`.

One LSN names one transaction: `TXN_BEGIN`, its puts and deletes, and
`TXN_COMMIT` all carry it, so a version's LSN is its transaction's and a
read at an LSN sees whole transactions only. Recovery applies a
transaction only when its commit is present.

Durability is stated plainly. A group commit ends with `fsync`, which
survives a crash of the process. While writes flow, the writer issues
`F_FULLFSYNC` every `BARRIER_EVERY_SEC` and then writes a `MARK` naming the
LSN that barrier made durable; a clean close ends with a barrier, a mark
and another barrier. The last valid mark is the durable LSN recovery
trusts: a bad record past it is a torn tail and is cut, even when good
records follow it (a power cut after a plain `fsync` leaves exactly that);
a bad record at or below it is corruption.

Files are `wal/<first lsn>.log`, rotated past `FILE_MAX_BYTES`.
"""
from __future__ import annotations

import logging
import os
import struct
import threading
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from jarvis.knowledge import oscompat

log = logging.getLogger("jarvis.knowledge.wal")

HEADER = struct.Struct("<IIQB")          # length, crc, lsn, type
CRC_PART = struct.Struct("<QB")          # what the CRC covers before the payload
OP_KEY = struct.Struct("<BH")            # family, key length
MARK_BODY = struct.Struct("<Q")

PUT = 1
DEL = 2
TXN_BEGIN = 9
TXN_COMMIT = 10
MARK = 11
COMPRESSED = 0x80
TYPES = frozenset({PUT, DEL, TXN_BEGIN, TXN_COMMIT, MARK})

COMPRESS_OVER = 4096
FILE_MAX_BYTES = 64 << 20
GROUP_WINDOW_SEC = 0.005
BARRIER_EVERY_SEC = 10.0
RECORD_MAX_BYTES = 64 << 20


EPHEMERAL_ENV = "JARVIS_ENGINE_EPHEMERAL"


def ephemeral() -> bool:
    """A throwaway engine (the test suite's, `JARVIS_ENGINE_EPHEMERAL=1`)
    never waits for the drive: every fsync is skipped. Never set in use."""
    from jarvis import env_names
    return env_names.read(EPHEMERAL_ENV) == "1"


def fsync(fd: int) -> None:
    if not ephemeral():
        os.fsync(fd)


def full_fsync(fd: int) -> None:
    """`F_FULLFSYNC`, which empties the drive's cache on macOS; plain fsync
    where it does not exist."""
    if ephemeral():
        return
    oscompat.full_fsync(fd)


def fsync_dir(directory: Path) -> None:
    if ephemeral():
        return
    oscompat.fsync_dir(directory)


@dataclass(frozen=True)
class Op:
    """One put (value bytes) or delete (value None) in one family."""
    family: int
    key: bytes
    value: Optional[bytes]


def encode(lsn: int, rtype: int, payload: bytes) -> bytes:
    if rtype in (PUT, DEL) and len(payload) > COMPRESS_OVER:
        packed = zlib.compress(payload, 1)
        if len(packed) < len(payload):
            payload, rtype = packed, rtype | COMPRESSED
    crc = zlib.crc32(payload, zlib.crc32(CRC_PART.pack(lsn, rtype)))
    return HEADER.pack(len(payload), crc, lsn, rtype) + payload


def op_payload(op: Op) -> bytes:
    return OP_KEY.pack(op.family, len(op.key)) + op.key + (op.value or b"")


def parse_op(rtype: int, payload: bytes) -> Op:
    family, klen = OP_KEY.unpack_from(payload, 0)
    start = OP_KEY.size
    key = bytes(payload[start:start + klen])
    value = bytes(payload[start + klen:]) if rtype == PUT else None
    return Op(family, key, value)


@dataclass
class Record:
    lsn: int
    rtype: int
    payload: bytes
    offset: int
    end: int


def _record_at(buf: memoryview, pos: int, prev_lsn: int = -1) -> Optional[Record]:
    """The record starting at `pos`, or None when it is short or fails its
    CRC (or, with `prev_lsn`, is not plausibly the next record)."""
    if pos + HEADER.size > len(buf):
        return None
    length, crc, lsn, rtype = HEADER.unpack_from(buf, pos)
    if (rtype & ~COMPRESSED) not in TYPES or length > RECORD_MAX_BYTES:
        return None
    end = pos + HEADER.size + length
    if end > len(buf):
        return None
    if prev_lsn >= 0 and not (prev_lsn <= lsn < prev_lsn + (1 << 32)):
        return None
    payload = buf[pos + HEADER.size:end]
    if zlib.crc32(payload, zlib.crc32(CRC_PART.pack(lsn, rtype))) != crc:
        return None
    data = bytes(payload)
    if rtype & COMPRESSED:
        try:
            data = zlib.decompress(data)
        except zlib.error:
            return None
    return Record(lsn, rtype & ~COMPRESSED, data, pos, end)


def wal_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.log"), key=lambda p: int(p.stem))


@dataclass
class Scan:
    """What one pass over the log found."""
    records: list[Record] = field(default_factory=list)   # every good record, in order, with its file
    files: list[tuple[Path, list[Record]]] = field(default_factory=list)
    durable_lsn: int = 0
    bad_at: Optional[tuple[Path, int]] = None             # the first bad record
    bad_after_lsn: int = -1                                # the LSN of the good record before it
    bad_after_type: int = 0                                # and its type
    later_good: list[Record] = field(default_factory=list) # good records found past the bad one


def scan(directory: Path) -> Scan:
    """Read every log file in order, stopping at the first bad record; then
    look past it for later good records, which says whether any mark puts
    the bad record below the durable LSN."""
    result = Scan()
    last_lsn = -1
    last_type = 0
    for path in wal_files(directory):
        data = path.read_bytes()
        buf = memoryview(data)
        pos = 0
        good: list[Record] = []
        while pos < len(buf):
            rec = _record_at(buf, pos)
            if rec is None:
                result.bad_at = (path, pos)
                result.bad_after_lsn = last_lsn
                result.bad_after_type = last_type
                break
            good.append(rec)
            if rec.rtype == MARK:
                result.durable_lsn = max(result.durable_lsn, MARK_BODY.unpack_from(rec.payload)[0])
            last_lsn, last_type = rec.lsn, rec.rtype
            pos = rec.end
        result.files.append((path, good))
        result.records.extend(good)
        if result.bad_at is not None:
            result.later_good = _resync(directory, path, pos + 1, last_lsn)
            break
    return result


def first_lsn_of_bad(found: Scan, checkpoint_lsn: int) -> int:
    """The lowest LSN the first bad record can belong to: the transaction
    of the good record before it, or the next one when that record closed a
    transaction or was a mark."""
    if found.bad_after_lsn < 0:
        return checkpoint_lsn + 1
    if found.bad_after_type in (TXN_COMMIT, MARK):
        return found.bad_after_lsn + 1
    return found.bad_after_lsn


def max_durable(found: Scan) -> int:
    """The durable LSN from every readable mark, past the bad record too."""
    durable = found.durable_lsn
    for rec in found.later_good:
        if rec.rtype == MARK:
            durable = max(durable, MARK_BODY.unpack_from(rec.payload)[0])
    return durable


def _resync(directory: Path, path: Path, start: int, prev_lsn: int) -> list[Record]:
    """Good records past a bad one: from `start` in `path`, scanning byte by
    byte for a plausible record, then every later file."""
    found: list[Record] = []
    files = wal_files(directory)
    index = files.index(path)
    for i, p in enumerate(files[index:]):
        buf = memoryview(p.read_bytes())
        pos = start if i == 0 else 0
        floor = max(prev_lsn, 0)
        while pos < len(buf):
            rec = _record_at(buf, pos, floor)
            if rec is None:
                pos += 1
                continue
            found.append(rec)
            floor = rec.lsn
            pos = rec.end
    return found


class WAL:
    """The single writer. `submit(ops)` blocks until its transaction is
    written and `fsync`ed with any others that arrived within the group
    window, then applied through `apply`; it returns the LSN."""

    def __init__(self, directory: Path, next_lsn: int,
                 apply: Callable[[int, list[Op]], None],
                 *, clock: Callable[[], float] = time.monotonic,
                 group_window: float = GROUP_WINDOW_SEC) -> None:
        self.directory = directory
        self.group_window = group_window
        directory.mkdir(parents=True, exist_ok=True)
        self.next_lsn = next_lsn
        self._apply = apply
        self._clock = clock
        self._cond = threading.Condition()
        self._queue: list[tuple[list[Op], dict]] = []
        self._closed = False
        self._fd: Optional[int] = None
        self._path: Optional[Path] = None
        self._size = 0
        self.durable_lsn = next_lsn - 1
        self.last_barrier = clock()
        self._unbarriered = False
        self._open_tail()
        self._thread = threading.Thread(target=self._run, name="knowledge-wal", daemon=True)
        self._thread.start()

    # -- files --------------------------------------------------------
    def _open_tail(self) -> None:
        files = wal_files(self.directory)
        if files:
            self._path = files[-1]
            self._fd = os.open(str(self._path), os.O_WRONLY | os.O_APPEND | oscompat.O_BINARY)
            self._size = os.fstat(self._fd).st_size
        else:
            self._new_file()

    def _new_file(self) -> None:
        if self._fd is not None:
            full_fsync(self._fd)
            os.close(self._fd)
        self._path = self.directory / f"{self.next_lsn:020d}.log"
        self._fd = os.open(str(self._path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | oscompat.O_BINARY, 0o600)
        self._size = 0
        fsync_dir(self.directory)

    @contextmanager
    def exclusive(self):
        """Hold the writer: no batch is written or applied meanwhile."""
        with self._cond:
            yield

    def rotate(self) -> None:
        """Start a new file now, so every older file can be deleted once a
        checkpoint passes it."""
        with self._cond:
            self._new_file()

    def delete_below(self, checkpoint_lsn: int) -> list[Path]:
        """Delete every file whose records all lie at or below the
        checkpoint: a file is wholly below when the next file starts at or
        below checkpoint + 1."""
        with self._cond:
            files = wal_files(self.directory)
            gone = []
            for path, nxt in zip(files, files[1:]):
                if int(nxt.stem) <= checkpoint_lsn + 1 and path != self._path:
                    path.unlink()
                    gone.append(path)
            return gone

    # -- writing ------------------------------------------------------
    def submit(self, ops: list[Op]) -> int:
        slot: dict = {}
        with self._cond:
            if self._closed:
                raise RuntimeError("the log is closed")
            self._queue.append((ops, slot))
            self._cond.notify_all()
            while "lsn" not in slot and "error" not in slot:
                self._cond.wait()
        if "error" in slot:
            raise slot["error"]
        return slot["lsn"]

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._closed:
                    if not self._cond.wait(timeout=1.0):
                        self._maybe_barrier()
                if self._closed and not self._queue:
                    return
            if self.group_window > 0:
                time.sleep(self.group_window)
            with self._cond:
                batch, self._queue = self._queue, []
                try:
                    written = self._write_batch(batch)
                except BaseException as e:  # noqa: BLE001 - every waiter must hear
                    for _, slot in batch:
                        slot["error"] = e
                    self._cond.notify_all()
                    continue
                for (ops, slot), lsn in zip(batch, written):
                    try:
                        self._apply(lsn, ops)
                        slot["lsn"] = lsn
                    except BaseException as e:  # noqa: BLE001
                        slot["error"] = e
                self._maybe_barrier()
                self._cond.notify_all()

    def _write_batch(self, batch: list[tuple[list[Op], dict]]) -> list[int]:
        chunks: list[bytes] = []
        lsns = []
        for ops, _ in batch:
            lsn = self.next_lsn
            self.next_lsn += 1
            lsns.append(lsn)
            chunks.append(encode(lsn, TXN_BEGIN, b""))
            for op in ops:
                chunks.append(encode(lsn, PUT if op.value is not None else DEL, op_payload(op)))
            chunks.append(encode(lsn, TXN_COMMIT, b""))
        data = b"".join(chunks)
        if self._size and self._size + len(data) > FILE_MAX_BYTES:
            self._new_file()
        self._write(data)
        fsync(self._fd)
        self._unbarriered = True
        return lsns

    def _write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            n = os.write(self._fd, view)
            view = view[n:]
            self._size += n

    def _maybe_barrier(self, force: bool = False) -> None:
        if not self._unbarriered:
            return
        if not force and self._clock() - self.last_barrier < BARRIER_EVERY_SEC:
            return
        self.barrier()

    def barrier(self) -> None:
        """`F_FULLFSYNC`, then a mark naming the LSN it made durable. Called
        with the condition held, or from close."""
        full_fsync(self._fd)
        durable = self.next_lsn - 1
        self._write(encode(durable, MARK, MARK_BODY.pack(durable)))
        self.durable_lsn = durable
        self.last_barrier = self._clock()
        self._unbarriered = False

    def full_barrier(self) -> None:
        """Make everything written so far survive a power cut now: what a
        write carrying his answer waits for before it is acknowledged."""
        with self._cond:
            if self._fd is not None:
                self._unbarriered = True
                self.barrier()

    def close(self) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._cond.notify_all()
        self._thread.join()
        with self._cond:
            if self._fd is not None:
                self._unbarriered = True
                self.barrier()
                full_fsync(self._fd)
                os.close(self._fd)
                self._fd = None


def committed(records: Iterable[Record], after_lsn: int) -> Iterator[tuple[int, list[Op]]]:
    """The transactions above `after_lsn` whose commit is present, in order."""
    open_lsn: Optional[int] = None
    ops: list[Op] = []
    for rec in records:
        if rec.lsn <= after_lsn:
            continue
        if rec.rtype == TXN_BEGIN:
            open_lsn, ops = rec.lsn, []
        elif rec.rtype in (PUT, DEL) and open_lsn == rec.lsn:
            ops.append(parse_op(rec.rtype, rec.payload))
        elif rec.rtype == TXN_COMMIT and open_lsn == rec.lsn:
            yield rec.lsn, ops
            open_lsn, ops = None, []
