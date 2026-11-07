"""Posting lists in fixed-width blocks (design sections 7.5 and 7.17.2).

Each block is 128 ordinals in one `array('I')` and, for the keyword index,
128 term frequencies in one `array('H')`, decoded with `frombytes` at C
speed; varint and delta decoding in Python would cost about a microsecond
a posting. A skip entry per block holds its last ordinal and its largest
term frequency, which is what WAND needs to skip it.

Layout: `u32 count | u16 blocks | u8 has_tf`, the skip table
(`u32 last | u16 max_tf` per block), then the blocks. The count is read
without decoding anything, which is what the planner estimates from.
"""
from __future__ import annotations

import struct
import sys
from array import array
from typing import Iterable, Iterator, Optional

BLOCK = 128
HEAD = struct.Struct("<IHB")
SKIP = struct.Struct("<IH")
_SWAP = sys.byteorder != "little"


def _bytes(arr: array) -> bytes:
    if _SWAP:
        arr = array(arr.typecode, arr)
        arr.byteswap()
    return arr.tobytes()


def _array(code: str, data: bytes) -> array:
    arr = array(code)
    arr.frombytes(data)
    if _SWAP:
        arr.byteswap()
    return arr


def encode(ordinals: Iterable[int], tfs: Optional[Iterable[int]] = None) -> bytes:
    """Ordinals ascending, without repeats; term frequencies beside them."""
    ords = array("I", ordinals)
    freqs = array("H", (min(t, 0xFFFF) for t in tfs)) if tfs is not None else None
    blocks = (len(ords) + BLOCK - 1) // BLOCK
    out = bytearray(HEAD.pack(len(ords), blocks, 1 if freqs is not None else 0))
    for b in range(blocks):
        lo, hi = b * BLOCK, min(len(ords), (b + 1) * BLOCK)
        out += SKIP.pack(ords[hi - 1], max(freqs[lo:hi]) if freqs is not None else 0)
    for b in range(blocks):
        lo, hi = b * BLOCK, min(len(ords), (b + 1) * BLOCK)
        out += _bytes(ords[lo:hi])
        if freqs is not None:
            out += _bytes(freqs[lo:hi])
    return bytes(out)


def count(data: bytes) -> int:
    return HEAD.unpack_from(data, 0)[0]


class Postings:
    """One decoded-on-demand posting list."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.count, self.blocks, has_tf = HEAD.unpack_from(data, 0)
        self.has_tf = bool(has_tf)
        pos = HEAD.size
        self.skips = [SKIP.unpack_from(data, pos + i * SKIP.size) for i in range(self.blocks)]
        self._base = pos + self.blocks * SKIP.size
        self.max_tf = max((s[1] for s in self.skips), default=0)

    def block(self, b: int) -> tuple[array, Optional[array]]:
        n = min(BLOCK, self.count - b * BLOCK)
        width = 4 + (2 if self.has_tf else 0)
        start = self._base + b * BLOCK * width
        ords = _array("I", self.data[start:start + 4 * n])
        tfs = _array("H", self.data[start + 4 * n:start + 4 * n + 2 * n]) if self.has_tf else None
        return ords, tfs

    def __iter__(self) -> Iterator[tuple[int, int]]:
        for b in range(self.blocks):
            ords, tfs = self.block(b)
            if tfs is None:
                for o in ords:
                    yield o, 1
            else:
                yield from zip(ords, tfs)

    def ordinals(self) -> array:
        out = array("I")
        for b in range(self.blocks):
            out.extend(self.block(b)[0])
        return out

    def contains(self, ordinal: int) -> bool:
        """Skip to the one block that can hold `ordinal`."""
        for b, (last, _) in enumerate(self.skips):
            if ordinal <= last:
                return ordinal in self.block(b)[0]
        return False
