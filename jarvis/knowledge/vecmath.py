"""The vector maths, behind one seam (design sections 7.10 and 18).

Two implementations give the same answers: Apple's Accelerate framework
through `ctypes` (standard library against a system framework, as PDFKit is
called through `osascript`), and a pure-Python reference used by tests and
wherever Accelerate cannot be loaded. `MATH` is the one in force; tests
replace it, as `_quartz.RUNNER` is replaced.

The scan path, named in full: `cblas_sgemv` writes every row's score into
one float buffer; a mask is `vDSP_vadd` of a penalty vector holding 0 for
allowed rows and -1e30 for refused ones (a multiply would leave refused
rows at 0, which outranks every negative cosine); exact top-k then reads
`vDSP_maxvi` of each block of `BLOCK` rows, takes the k-th largest block
maximum as a threshold no top-k row can fall below, and reads in Python only
the blocks whose maximum reaches it.
"""
from __future__ import annotations

import ctypes
import math
import sys
from ctypes import POINTER, byref, c_float, c_int, c_long, c_ulong, c_void_p
from typing import Optional, Protocol

BLOCK = 256
REFUSED = -1e30
_ROW_MAJOR, _NO_TRANS = 101, 111


class VectorMath(Protocol):
    name: str

    def gemv(self, rows, n: int, d: int, q, out) -> None:
        """out[i] = rows[i] . q for n rows of d floats."""

    def add(self, out, penalty, n: int) -> None:
        """out[i] += penalty[i]."""

    def block_max(self, out, start: int, count: int) -> tuple[float, int]:
        """The largest of out[start:start+count] and its offset."""


def _addr(buf) -> int:
    return ctypes.addressof(buf)


class Reference:
    """Pure Python; exact and slow."""
    name = "reference"

    def gemv(self, rows, n: int, d: int, q, out) -> None:
        qq = list(q[:d])
        for i in range(n):
            base = i * d
            out[i] = math.fsum(rows[base + j] * qq[j] for j in range(d))

    def add(self, out, penalty, n: int) -> None:
        for i in range(n):
            out[i] = out[i] + penalty[i]

    def block_max(self, out, start: int, count: int) -> tuple[float, int]:
        best, at = out[start], 0
        for j in range(1, count):
            v = out[start + j]
            if v > best:
                best, at = v, j
        return best, at


class Accelerate:
    name = "accelerate"

    def __init__(self) -> None:
        lib = ctypes.CDLL("/System/Library/Frameworks/Accelerate.framework/Accelerate")
        lib.cblas_sgemv.argtypes = [c_int, c_int, c_int, c_int, c_float, c_void_p, c_int,
                                    c_void_p, c_int, c_float, c_void_p, c_int]
        lib.cblas_sgemv.restype = None
        lib.vDSP_vadd.argtypes = [c_void_p, c_long, c_void_p, c_long, c_void_p, c_long, c_ulong]
        lib.vDSP_vadd.restype = None
        lib.vDSP_maxvi.argtypes = [c_void_p, c_long, POINTER(c_float), POINTER(c_ulong), c_ulong]
        lib.vDSP_maxvi.restype = None
        self.lib = lib
        self._best = c_float()
        self._idx = c_ulong()

    def gemv(self, rows, n: int, d: int, q, out) -> None:
        if n:
            self.lib.cblas_sgemv(_ROW_MAJOR, _NO_TRANS, n, d, 1.0, _addr(rows), d,
                                 _addr(q), 1, 0.0, _addr(out), 1)

    def add(self, out, penalty, n: int) -> None:
        if n:
            self.lib.vDSP_vadd(_addr(out), 1, _addr(penalty), 1, _addr(out), 1, n)

    def block_max(self, out, start: int, count: int) -> tuple[float, int]:
        best, idx = c_float(), c_ulong()
        self.lib.vDSP_maxvi(c_void_p(_addr(out) + 4 * start), 1, byref(best), byref(idx), count)
        return best.value, int(idx.value)


def _load() -> VectorMath:
    if sys.platform == "darwin":
        try:
            return Accelerate()
        except OSError:
            pass
    return Reference()


MATH: VectorMath = _load()


def top_k(out, n: int, k: int, math_: Optional[VectorMath] = None) -> list[tuple[float, int]]:
    """Exact top-k of out[0:n] (score, row), best first, refused rows never
    returned: block maxima, then only the blocks that reach the threshold."""
    m = math_ or MATH
    if n == 0 or k <= 0:
        return []
    maxima = []
    for start in range(0, n, BLOCK):
        best, _ = m.block_max(out, start, min(BLOCK, n - start))
        maxima.append(best)
    # Each of the k best blocks holds a row at least its maximum, so the k
    # best rows are all at or above the k-th best block maximum. With fewer
    # blocks than k, every block is read.
    ranked = sorted(maxima, reverse=True)
    threshold = ranked[k - 1] if len(ranked) >= k else REFUSED
    threshold = max(threshold, REFUSED / 2)
    hits = []
    for b, best in enumerate(maxima):
        if best >= threshold:
            start = b * BLOCK
            for j in range(min(BLOCK, n - start)):
                s = out[start + j]
                if s >= threshold:
                    hits.append((s, start + j))
    hits.sort(key=lambda h: (-h[0], h[1]))
    return hits[:k]
