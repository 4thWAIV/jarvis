"""Plain text, Markdown, JSON, CSV and TSV, with the standard library.

The text is decoded once (`_paging.decode`: UTF-8, a byte order mark, else
Windows-1252) and paged by lines; CSV and TSV are paged by whole rows, so a
quoted value with a line break in it is never cut between pages. JSON that
does not parse, or CSV the csv module refuses, is still read as text, with
the note `corrupt`. A member over TEXT_MAX_BYTES is not read at all
(`too_large`), never read in part.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Optional

from jarvis.bundle_contract import Extent, RenderedPage, RenderOptions
from jarvis.bundle_readers import (Description, Detected, MemberSource, ReaderHealth,
                                   TextSlice, Unsupported, _paging, read_bounded)

TEXT_MAX_BYTES = 32 * 1024 * 1024


def _units(fmt: str, text: str) -> tuple[list[_paging.Unit], tuple[str, ...]]:
    """The units a page may end on, and any note about the content."""
    if fmt == "json":
        try:
            json.loads(text)
        except ValueError:
            return _paging.line_units(text), ("corrupt",)
    if fmt in ("csv", "tsv"):
        rows = csv_rows(text, "\t" if fmt == "tsv" else ",")
        if rows is None:
            return _paging.line_units(text), ("corrupt",)
        return rows, ()
    return _paging.line_units(text), ()


def csv_rows(text: str, delimiter: str) -> Optional[list[_paging.Unit]]:
    """Each row's lines as one unit, or None when the csv module refuses it."""
    lines = text.splitlines(keepends=True)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    units, done = [], 0
    try:
        for _ in reader:
            end = min(reader.line_num, len(lines))
            if end > done:
                units.append(_paging.Unit(None, done + 1, end, "".join(lines[done:end])))
                done = end
    except csv.Error:
        return None
    if done < len(lines):
        units.append(_paging.Unit(None, done + 1, len(lines), "".join(lines[done:])))
    return units


class TextReader:
    name = "text"
    version = 1
    formats = frozenset({"text", "markdown", "json", "csv", "tsv"})

    def health(self) -> ReaderHealth:
        return ReaderHealth(True, frozenset({"read_text"}))

    def _text(self, source: MemberSource) -> str:
        return _paging.decode(read_bounded(source, TEXT_MAX_BYTES))

    def describe(self, source: MemberSource, detected: Detected) -> Description:
        try:
            text = self._text(source)
        except Unsupported as e:
            return Description("unsupported", Extent(), e.reason)
        return Description("readable", Extent(lines=_paging.line_count(text)),
                           operations=frozenset({"read_text"}))

    def read_text(self, source: MemberSource, detected: Detected, cursor: Optional[dict],
                  max_chars: int) -> TextSlice:
        data = read_bounded(source, TEXT_MAX_BYTES)
        units, notes = _units(detected.format, _paging.decode(data))
        if _paging.encoding_of(data) == "cp1252":
            notes = (*notes, "legacy_encoding")
        chunks, after = _paging.page(units, cursor, max_chars)
        return TextSlice(chunks, after, notes)

    def render(self, source: MemberSource, detected: Detected, page: int,
               options: RenderOptions) -> RenderedPage:
        raise Unsupported("reader_unavailable")
