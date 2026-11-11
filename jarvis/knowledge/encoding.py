"""Order-preserving value encoding and canonical JSON (design section
7.17.2).

A value's encoding starts with a type tag, so values of different types
never compare equal, and its bytes sort in value order: numbers as
big-endian float64 with the sign flipped (negatives inverted), dates as
epoch milliseconds the same way, strings folded by `spoken_words.fold` and
cut to `STRING_MAX` bytes. Byte order being value order is what lets the
general index answer ranges as well as equality. A cut string can match
more than it should, so an index answer is checked against the document
itself unless it is exact (`is_cut`: a key shorter than `STRING_MAX` bytes
of text was never cut). Only a bounded prefix of a long string is ever
folded (`FOLD_FIRST` characters, more only while the fold is still short),
so a record field holding 100 KB of text costs the index what 128 bytes
cost: folding is per character, so the fold of a prefix is a prefix of the
fold.
"""
from __future__ import annotations

import json
import math
import struct
from datetime import datetime, timezone
from typing import Any

from jarvis import spoken_words

NULL = 0x01
FALSE = 0x02
TRUE = 0x03
NUMBER = 0x04
DATE = 0x05
STRING = 0x06
STRING_MAX = 128
FOLD_FIRST = 4 * STRING_MAX      # characters folded first; more only while that folds short


def canonical(obj: Any) -> bytes:
    """Sorted keys, no spaces, UTF-8: the one byte form of a document."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sortable_float(x: float) -> bytes:
    bits = struct.unpack(">Q", struct.pack(">d", float(x)))[0]
    bits = bits ^ 0xFFFFFFFFFFFFFFFF if bits >> 63 else bits | (1 << 63)
    return struct.pack(">Q", bits)


def fold_string(s: str) -> bytes:
    """The string's index bytes: its fold, cut to `STRING_MAX` bytes,
    folding only as much of it as that needs."""
    n = FOLD_FIRST
    while True:
        out = spoken_words.fold(s[:n]).encode("utf-8")
        if len(out) >= STRING_MAX or n >= len(s):
            return out[:STRING_MAX]
        n *= 4


def is_cut(folded: bytes) -> bool:
    """Whether a string's index bytes may be a cut of a longer string."""
    return len(folded) >= STRING_MAX


def decode_number(payload: bytes) -> float:
    """The number a NUMBER encoding's eight bytes hold."""
    bits = struct.unpack(">Q", payload[:8])[0]
    bits = bits ^ (1 << 63) if bits >> 63 else bits ^ 0xFFFFFFFFFFFFFFFF
    return struct.unpack(">d", struct.pack(">Q", bits))[0]


def date_millis(value: Any) -> float:
    """Epoch milliseconds from an ISO 8601 string or a number."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp() * 1000.0


def encode_value(value: Any, *, date: bool = False) -> bytes:
    if value is None:
        return bytes([NULL])
    if value is True:
        return bytes([TRUE])
    if value is False:
        return bytes([FALSE])
    if date:
        return bytes([DATE]) + _sortable_float(date_millis(value))
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            raise ValueError("NaN cannot be indexed")
        return bytes([NUMBER]) + _sortable_float(value)
    if isinstance(value, str):
        return bytes([STRING]) + fold_string(value)
    raise TypeError(f"cannot encode a {type(value).__name__}")


def type_range(tag: int) -> tuple[bytes, bytes]:
    """[start, end) of every encoding with one type tag."""
    return bytes([tag]), bytes([tag + 1])
