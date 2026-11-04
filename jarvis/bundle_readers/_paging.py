"""Paging text for the readers that produce lines (text, CSV, HTML, Word):
one way to cut a member's text into pages of at most `max_chars`, each
saying which lines it covers, with a cursor to continue from. A page ends
on a unit's end (a line, or a whole CSV row) unless one unit alone is
longer than a page, which is then cut by characters and continued.

Also the one text decoder: UTF-8, UTF-8 or UTF-16/32 with a byte order mark,
else Windows-1252 (what a Windows program that wrote no BOM most likely
used), with undecodable bytes replaced.
"""
from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import Any, Callable, Optional

from jarvis.bundle_contract import LineLocation, Location


@dataclass(frozen=True)
class Unit:
    """What a page may end after: a line, a CSV row, a sheet's row, a PDF
    page or a slide. `section` groups units into one chunk (None for plain
    lines; a page, slide or sheet otherwise); `start` and `end` are the
    unit's own positions in its section (lines or rows)."""
    section: Any
    start: int
    end: int
    text: str


# Turns (section, first position, last position) into a chunk's location.
Locate = Callable[[Any, int, int], Location]


def lines(section: Any, start: int, end: int) -> Location:
    return LineLocation(start=start, end=end)


def cursor_int(cursor: Optional[dict], key: str) -> int:
    """A cursor's field as a non-negative int (0 when there is no cursor);
    ValueError for anything a reader did not write."""
    if cursor is None:
        return 0
    value = cursor.get(key, 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"bad cursor field {key}")
    return value


_BOMS = ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
         (codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
         (codecs.BOM_UTF16_BE, "utf-16"))


def encoding_of(data: bytes) -> str:
    """The encoding `decode` uses for these bytes."""
    for bom, name in _BOMS:
        if data.startswith(bom):
            return name
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def decode(data: bytes) -> str:
    """Bytes as text, line endings made "\\n"."""
    text = data.decode(encoding_of(data), errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def line_units(text: str) -> list[Unit]:
    return [Unit(None, n, n, line)
            for n, line in enumerate(text.splitlines(keepends=True), start=1)]


def line_count(text: str) -> int:
    return len(text.splitlines())


def page(units: list[Unit], cursor: Optional[dict], max_chars: int,
         locate: Locate = lines) -> tuple[list[tuple[Location, str]], Optional[dict]]:
    """One page from `cursor` on, as chunks (one per section it touches),
    and the next cursor (None at the end). Whole units while they fit; a
    unit longer than a whole page is cut by characters and continued."""
    index, col = cursor_int(cursor, "unit"), cursor_int(cursor, "col")
    if index > len(units) or (index < len(units) and col > len(units[index].text)):
        raise ValueError("cursor past the end")
    chunks: list[tuple[Location, str]] = []
    open_section: list = []           # [section, start, end, parts] of the chunk being built
    used = 0

    def close() -> None:
        if open_section:
            section, start, end, parts = open_section
            chunks.append((locate(section, start, end), "".join(parts)))
            open_section.clear()

    while index < len(units):
        unit = units[index]
        rest, room = unit.text[col:], max_chars - used
        if len(rest) <= room:
            take, next_index, next_col = rest, index + 1, 0
        elif used == 0:
            take, next_index, next_col = rest[:room], index, col + room
        else:
            break
        if open_section and open_section[0] != unit.section:
            close()
        if open_section:
            open_section[2] = unit.end
            open_section[3].append(take)
        else:
            open_section.extend([unit.section, unit.start, unit.end, [take]])
        used += len(take)
        index, col = next_index, next_col
        if col:
            break
    close()
    return chunks, ({"unit": index, "col": col} if index < len(units) else None)
