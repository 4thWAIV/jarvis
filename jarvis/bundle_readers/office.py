"""Word, Excel and PowerPoint files in Office Open XML (docx, xlsx, pptx and
their macro-enabled kin): their text, read from the XML parts with zipfile
and xml.etree through bundle_archive's checks and budgets. No custom binary
parser and nothing loaded but XML.

A macro project (`vbaProject.bin`) is never read; its presence is the note
`macros_ignored`. A worksheet's formulas are not evaluated: each cell shows
its saved value and, beside it, the formula as written
(`formulas_not_evaluated`). Word is paged by paragraph (`LineLocation`, one
line per paragraph), Excel by sheet and row (`SheetLocation`), PowerPoint
by slide (`SlideLocation`). Rendering is not offered. The old binary
formats (doc, xls, ppt) have no reader here.

An XML part that declares a DOCTYPE or an entity is refused as `corrupt`:
Office never writes one, and refusing it keeps entity expansion out.
"""
from __future__ import annotations

import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Optional

from jarvis import bundle_archive
from jarvis.bundle_contract import (Extent, RenderedPage, RenderOptions,
                                    SheetLocation, SlideLocation)
from jarvis.bundle_readers import (Description, Detected, MemberSource, ReaderHealth,
                                   TextSlice, Unsupported, _paging, read_bounded)

OFFICE_MAX_BYTES = 128 * 1024 * 1024
PART_MAX_BYTES = 64 * 1024 * 1024
# Rows of one sheet read; past them the sheet's text ends with `too_large`.
SHEET_ROWS_MAX = 50_000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"

CONTENT_TYPES = "[Content_Types].xml"
# The main part's content type (any variant: macro-enabled, template, show).
_MAIN = (("docx", ("wordprocessingml.document.main", "wordprocessingml.template.main",
                   "ms-word.document.macroenabled.main", "ms-word.template.macroenabled")),
         ("xlsx", ("spreadsheetml.sheet.main", "spreadsheetml.template.main",
                   "ms-excel.sheet.macroenabled.main", "ms-excel.template.macroenabled")),
         ("pptx", ("presentationml.presentation.main", "presentationml.slideshow.main",
                   "presentationml.template.main", "ms-powerpoint.presentation.macroenabled",
                   "ms-powerpoint.slideshow.macroenabled", "ms-powerpoint.template.macroenabled")))
_MAIN_PART = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml",
              "pptx": "ppt/presentation.xml"}
_UNSAFE_XML = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)


def xml(data: bytes) -> ET.Element:
    if _UNSAFE_XML.search(data):
        raise Unsupported("corrupt")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise Unsupported("corrupt") from None


def ooxml_format(names: set[str], content_types: Optional[bytes]) -> Optional[str]:
    """docx, xlsx or pptx from an archive's [Content_Types].xml (its main
    part's content type), or None when it is not an Office document."""
    if content_types is None:
        return None
    try:
        root = xml(content_types)
    except Unsupported:
        return None
    declared = " ".join((o.get("ContentType") or "").lower()
                        for o in root.iter(f"{_CT}Override"))
    for fmt, kinds in _MAIN:
        if any(kind in declared for kind in kinds):
            return fmt
    for fmt, part in _MAIN_PART.items():
        if part in names:
            return fmt
    return None


class _Package:
    """One OOXML file opened from its bytes, its parts read under the
    archive budgets."""

    def __init__(self, data: bytes):
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(data))
            listing = bundle_archive.list_members(self.archive)
        except bundle_archive.ArchiveError as e:          # before ValueError, its base
            raise Unsupported("encrypted" if e.kind == "encrypted" else "corrupt") from None
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError):
            raise Unsupported("corrupt") from None
        self.files = listing.files()
        self.macros = any(p.rsplit("/", 1)[-1].lower() == "vbaproject.bin" for p in self.files)

    def part(self, path: str) -> Optional[bytes]:
        member = self.files.get(path)
        if member is None:
            return None
        try:
            return bundle_archive.read_member(self.archive, member, limit=PART_MAX_BYTES)
        except bundle_archive.ArchiveError as e:
            raise Unsupported("too_large" if e.reason == "too-large" else "corrupt") from None

    def xml(self, path: str) -> Optional[ET.Element]:
        data = self.part(path)
        return None if data is None else xml(data)

    def related(self, part: str) -> dict[str, str]:
        """A part's relationships: id -> the target part's path."""
        folder, name = posixpath.split(part)
        rels = self.xml(posixpath.join(folder, "_rels", f"{name}.rels"))
        found = {}
        for rel in (rels.iter(f"{_REL}Relationship") if rels is not None else ()):
            target = rel.get("Target") or ""
            if rel.get("TargetMode") == "External" or not target:
                continue
            path = target.lstrip("/") if target.startswith("/") else \
                posixpath.normpath(posixpath.join(folder, target))
            found[rel.get("Id") or ""] = path
        return found


def _docx(pkg: _Package) -> tuple[list[_paging.Unit], Extent]:
    root = pkg.xml("word/document.xml")
    if root is None:
        raise Unsupported("corrupt")
    lines = []
    for para in root.iter(f"{_W}p"):
        words = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                words.append(node.text)
            elif node.tag == f"{_W}tab":
                words.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                words.append(" ")
        lines.append("".join(words))
    units = [_paging.Unit(None, n, n, line + "\n") for n, line in enumerate(lines, start=1)]
    return units, Extent(lines=len(lines))


def _slide_text(root: ET.Element) -> str:
    paras = ["".join(t.text or "" for t in para.iter(f"{_A}t"))
             for para in root.iter(f"{_A}p")]
    return "\n".join(p for p in paras if p.strip())


def _pptx(pkg: _Package) -> tuple[list[_paging.Unit], Extent]:
    pres = pkg.xml("ppt/presentation.xml")
    if pres is None:
        raise Unsupported("corrupt")
    rels = pkg.related("ppt/presentation.xml")
    units = []
    for n, slide in enumerate(pres.iter(f"{_P}sldId"), start=1):
        root = pkg.xml(rels.get(slide.get(f"{_R}id") or "", ""))
        text = _slide_text(root) if root is not None else ""
        units.append(_paging.Unit(n, n, n, text + "\n"))
    return units, Extent(slides=len(units))


def _shared_strings(pkg: _Package, rels: dict[str, str]) -> list[str]:
    path = next((p for p in rels.values() if p.endswith("sharedStrings.xml")),
                "xl/sharedStrings.xml")
    root = pkg.xml(path)
    if root is None:
        return []
    return ["".join(t.text or "" for t in si.iter(f"{_S}t")) for si in root.iter(f"{_S}si")]


def _cell(cell: ET.Element, strings: list[str]) -> tuple[str, bool]:
    """A cell as shown: its saved value, and its formula as written beside
    it. The bool says whether it has a formula."""
    kind = cell.get("t")
    value = cell.findtext(f"{_S}v")
    if kind == "s" and value is not None:
        try:
            value = strings[int(value)]
        except (ValueError, IndexError):
            value = ""
    elif kind == "inlineStr":
        value = "".join(t.text or "" for t in cell.iter(f"{_S}t"))
    elif kind == "b" and value is not None:
        value = "TRUE" if value == "1" else "FALSE"
    formula = cell.findtext(f"{_S}f")
    shown = (value or "").replace("\t", " ").replace("\n", " ")
    if formula:
        return f"{shown} [={formula}]", True
    return shown, False


def _xlsx(pkg: _Package) -> tuple[list[_paging.Unit], Extent, tuple[str, ...]]:
    book = pkg.xml("xl/workbook.xml")
    if book is None:
        raise Unsupported("corrupt")
    rels = pkg.related("xl/workbook.xml")
    strings = _shared_strings(pkg, rels)
    units, names, notes = [], [], []
    for sheet in book.iter(f"{_S}sheet"):
        name = sheet.get("name") or f"Sheet{len(names) + 1}"
        names.append(name)
        root = pkg.xml(rels.get(sheet.get(f"{_R}id") or "", ""))
        rows = list(root.iter(f"{_S}row")) if root is not None else []
        if len(rows) > SHEET_ROWS_MAX:
            rows = rows[:SHEET_ROWS_MAX]
            notes.append("too_large")
        for at, row in enumerate(rows, start=1):
            number = int(row.get("r") or at) if (row.get("r") or "").isdigit() else at
            cells = []
            for column, cell in enumerate(row.iter(f"{_S}c"), start=1):
                shown, formula = _cell(cell, strings)
                if formula:
                    notes.append("formulas_not_evaluated")
                if shown:
                    cells.append(f"{cell.get('r') or f'{column}:{number}'}={shown}")
            if cells:
                units.append(_paging.Unit(name, number, number, "\t".join(cells) + "\n"))
    return units, Extent(sheets=names[:100]), tuple(dict.fromkeys(notes))


def _sheet(section, start: int, end: int):
    return SheetLocation(sheet=section, start_row=start, end_row=end)


def _slide(section, start: int, end: int):
    return SlideLocation(slide=section)


class OfficeReader:
    name = "office"
    version = 1
    formats = frozenset({"docx", "xlsx", "pptx"})

    def health(self) -> ReaderHealth:
        return ReaderHealth(True, frozenset({"read_text"}))

    def _read(self, source: MemberSource, fmt: str):
        """(units, extent, notes, locate) for the whole document."""
        pkg = _Package(read_bounded(source, OFFICE_MAX_BYTES))
        notes: tuple[str, ...] = ("macros_ignored",) if pkg.macros else ()
        if fmt == "docx":
            units, extent = _docx(pkg)
            return units, extent, notes, _paging.lines
        if fmt == "pptx":
            units, extent = _pptx(pkg)
            return units, extent, notes, _slide
        units, extent, more = _xlsx(pkg)
        return units, extent, notes + more, _sheet

    def describe(self, source: MemberSource, detected: Detected) -> Description:
        try:
            _, extent, notes, _ = self._read(source, detected.format)
        except Unsupported as e:
            return Description("unsupported", Extent(), e.reason)
        partial = "too_large" in notes
        return Description("partial" if partial else "readable", extent,
                           "too_large" if partial else (notes[0] if notes else None),
                           frozenset({"read_text"}))

    def read_text(self, source: MemberSource, detected: Detected, cursor: Optional[dict],
                  max_chars: int) -> TextSlice:
        units, _, notes, locate = self._read(source, detected.format)
        chunks, after = _paging.page(units, cursor, max_chars, locate)
        return TextSlice(chunks, after, notes)

    def render(self, source: MemberSource, detected: Detected, page: int,
               options: RenderOptions) -> RenderedPage:
        raise Unsupported("reader_unavailable")
