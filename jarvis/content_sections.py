"""A file's Content for the knowledge engine: its bytes made into extracted
text and sections (requirements-uds `sections`, `section-quality`,
`content-formats`), on JARVIS's side; nothing in jarvis/knowledge/ imports
it. The content ingest calls `in_scope`, `extract`, `embedded_text` and
`whole_text`.

Every format is read by JARVIS's own readers (bundle_readers): `detect`
decides the format from the bytes, then the text reader (text, Markdown,
JSON, CSV, TSV), html.py (HTML and SVG, with its headings and <title>),
office.py (Word with its heading styles, Excel by sheet, PowerPoint by
slide) or pdf.py (PDFKit through osascript, page by page, on a private
0600 copy in a scratch folder removed after). Nothing here parses a file
format of its own; nothing is run, loaded or followed.

Sections are cut at headings (Markdown ATX and setext headings; HTML
h1-h6; Word's heading styles), at a PDF's pages, a deck's slides and a
workbook's sheets. Each keeps its heading and its whole heading path; text
before the first heading has the title as its heading and an empty path.
A section longer than SECTION_MAX is split at paragraphs, then sentences
(lines, for code and data), then words, never inside a word unless one
word alone is longer than a section. A fenced code block, a pipe table and
a CSV row stay whole while they fit. No section is empty.

The folder's index scope (project_store.INDEX_SCOPES) is decided from the
file name alone (`in_scope`); `extract` refuses a name outside it.
"""
from __future__ import annotations

import contextlib
import io
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator, Optional, Union

from jarvis import bundle_readers
from jarvis.bundle_contract import PageLocation, SheetLocation, SlideLocation
from jarvis.bundle_readers import Detected, Unsupported, html, office
from jarvis.bundle_readers import text as text_reader
from jarvis.knowledge import items as item_texts

SCOPES = ("docs", "text", "off")

# A section's own text, in characters. multilingual-e5-small reads 512
# tokens; at three or more characters a token (less for CJK, which packs
# tighter in characters) a section and its heading line fit in it.
SECTION_MAX = 1_300
# The heading line (title and path) put ahead of a section's text.
HEADING_LINE_MAX = 200
# The whole text a vector is made from: heading line and section.
EMBED_MAX = SECTION_MAX + HEADING_LINE_MAX + 1
# A file larger than this is not read (the text reader's own limit).
FILE_MAX_BYTES = 32 * 1024 * 1024
# How many characters one reader call may hand back: past any section.
_READ_ALL = 1 << 30


@dataclass(frozen=True)
class Section:
    """One section of an item. `page` is the PDF page or the slide."""
    index: int
    heading: str
    heading_path: tuple[str, ...]
    text: str
    page: Optional[int] = None


@dataclass(frozen=True)
class Extracted:
    format: str
    media_type: str
    title: str
    text: str
    sections: tuple[Section, ...]
    headings: tuple[str, ...]
    pages: Optional[int]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Unreadable:
    """Why a file gives no Content: out_of_scope, too_large, empty, binary,
    reader_unavailable (a format with no reader here), encrypted, corrupt,
    or another reader's reason (bundle_contract.Reason)."""
    reason: str


# -- scope ---------------------------------------------------------------------------------

_DOC_SUFFIXES = frozenset({
    "md", "markdown", "txt", "text", "pdf",
    "docx", "docm", "dotx", "dotm", "doc",
    "xlsx", "xlsm", "xltx", "xltm", "xls",
    "pptx", "pptm", "potx", "potm", "ppsx", "ppsm", "ppt",
    "html", "htm", "xhtml"})
# Names that are never text: pictures, sound, video, archives, programs,
# fonts and databases. Any other name is text scope's to try.
_BINARY_SUFFIXES = frozenset({
    "png", "jpg", "jpeg", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff", "ico",
    "icns", "psd", "ai", "sketch", "raw", "cr2", "nef",
    "mp3", "m4a", "wav", "aiff", "aif", "flac", "ogg", "opus", "aac", "mid", "midi",
    "mp4", "m4v", "mov", "webm", "mkv", "avi", "wmv",
    "zip", "tar", "tgz", "gz", "bz2", "xz", "zst", "7z", "rar", "dmg", "iso", "pkg",
    "jar", "war", "whl", "egg", "apk", "ipa",
    "exe", "dll", "so", "dylib", "o", "a", "lib", "obj", "class", "pyc", "pyo", "wasm",
    "bin", "dat", "node",
    "woff", "woff2", "ttf", "otf", "eot",
    "sqlite", "sqlite3", "db", "mdb", "realm", "parquet", "arrow", "feather", "npy",
    "npz", "pkl", "pickle", "onnx", "pt", "safetensors", "gguf",
    "key", "numbers", "pages", "rtf", "odt", "ods", "odp", "epub"})
# Text that is prose: split at sentences. Other text (code, data, logs) at lines.
_PROSE_SUFFIXES = frozenset({"", "txt", "text", "md", "markdown", "rst", "adoc", "org"})


def _suffix(name: str) -> str:
    return PurePosixPath(name.lower()).suffix.lstrip(".")


def in_scope(name: str, scope: str) -> bool:
    """Whether a folder with this index scope indexes a file by this name.
    docs: Markdown, plain text, PDF, Word, Excel, PowerPoint and HTML;
    text: those and any other name that may be text (code, config, JSON,
    CSV, YAML...), never a known binary kind; off: nothing."""
    if scope not in SCOPES:
        raise ValueError(f"the scope is one of {', '.join(SCOPES)}")
    suffix = _suffix(name)
    if scope == "docs":
        return suffix in _DOC_SUFFIXES
    if scope == "text":
        return suffix in _DOC_SUFFIXES or suffix not in _BINARY_SUFFIXES
    return False


# -- the pieces a section is made of -------------------------------------------------------

@dataclass(frozen=True)
class _Heading:
    level: int
    text: str


@dataclass(frozen=True)
class _Page:
    """A new page or slide: it ends the heading path. `heading` is a
    slide's title ("" for a PDF page)."""
    number: int
    heading: str = ""


@dataclass(frozen=True)
class _Block:
    """A run of text kept together. `parts` are what it may be cut between
    (its lines, or rows) when it is longer than a section; a `whole` block
    (code, a table, rows) is never joined to a neighbour's piece across a
    cut of its own. `prose` blocks are cut at sentences, others at lines."""
    parts: tuple[str, ...]
    sep: str = "\n"
    whole: bool = False
    prose: bool = True

    @property
    def text(self) -> str:
        return self.sep.join(self.parts).strip()


_Item = Union[_Heading, _Page, _Block]


# -- Markdown -------------------------------------------------------------------------------

_ATX = re.compile(r" {0,3}(#{1,6})(?:[ \t]+([^\r\n\v\f\x1c-\x1e\x85\u2028\u2029]*?))?(?:[ \t]+#+)?[ \t]*")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SETEXT = re.compile(r" {0,3}(=+|-+)[ \t]*")
_TABLE_RULE = re.compile(r"[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*")


def _table_starts(lines: list[str], i: int) -> bool:
    return ("|" in lines[i] and i + 1 < len(lines) and "|" in lines[i + 1]
            and bool(_TABLE_RULE.fullmatch(lines[i + 1]))) or lines[i].lstrip().startswith("|")


def _markdown_items(text: str, prose: bool = True) -> tuple[list[_Item], Optional[str]]:
    """Markdown as headings and blocks; and the front matter's `title:`."""
    lines = text.split("\n")
    items: list[_Item] = []
    para: list[str] = []
    front_title: Optional[str] = None
    i = 0

    def flush() -> None:
        if para:
            items.append(_Block(tuple(para), prose=prose))
            para.clear()

    if lines and lines[0].strip() == "---":
        end = next((n for n in range(1, len(lines)) if lines[n].strip() in ("---", "...")), None)
        if end is not None:
            front = lines[:end + 1]
            for line in front[1:-1]:
                key, _, value = line.partition(":")
                if key.strip().lower() == "title" and value.strip():
                    front_title = value.strip().strip("'\"").strip() or None
            items.append(_Block(tuple(front), whole=True, prose=False))
            i = end + 1
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            flush()
            mark = fence.group(1)
            end = next((n for n in range(i + 1, len(lines))
                        if lines[n].strip().startswith(mark[0] * len(mark))
                        and not lines[n].strip().strip(mark[0])), len(lines) - 1)
            items.append(_Block(tuple(lines[i:end + 1]), whole=True, prose=False))
            i = end + 1
            continue
        atx = _ATX.fullmatch(line)
        if atx:
            flush()
            items.append(_Heading(len(atx.group(1)), (atx.group(2) or "").strip()))
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        if para and _SETEXT.fullmatch(line) and not (line.strip().startswith("-")
                                                  and len(line.strip()) < 2):
            heading = " ".join(" ".join(para).split())
            para.clear()
            items.append(_Heading(1 if line.strip().startswith("=") else 2, heading))
            i += 1
            continue
        if not para and _table_starts(lines, i):
            end = i
            while end + 1 < len(lines) and "|" in lines[end + 1] and lines[end + 1].strip():
                end += 1
            items.append(_Block(tuple(lines[i:end + 1]), whole=True, prose=False))
            i = end + 1
            continue
        para.append(line)
        i += 1
    flush()
    return items, front_title


def _paragraph_items(text: str, prose: bool) -> list[_Item]:
    """Plain text as blocks at its blank lines."""
    return [_Block(tuple(chunk.split("\n")), prose=prose)
            for chunk in re.split(r"\n[ \t]*\n", text) if chunk.strip()]


# -- cutting long text ----------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?…。！？])\s+")


def _pack(units: list[str], sep: str, limit: int) -> list[str]:
    """Units (each at most `limit`) joined into as few pieces as fit."""
    pieces: list[str] = []
    current = ""
    for unit in units:
        if not unit:
            continue
        if current and len(current) + len(sep) + len(unit) <= limit:
            current += sep + unit
        else:
            if current:
                pieces.append(current)
            current = unit
    if current:
        pieces.append(current)
    return pieces


def _words(text: str, limit: int) -> list[str]:
    """Cut at spaces; a single word longer than a section, and only that,
    is cut where it must be."""
    units: list[str] = []
    for word in text.split():
        units.extend(word[n:n + limit] for n in range(0, len(word), limit))
    return _pack(units, " ", limit)


def _sentences(text: str, limit: int) -> list[str]:
    units: list[str] = []
    for sentence in _SENTENCE_END.split(" ".join(text.split())):
        units.extend([sentence] if len(sentence) <= limit else _words(sentence, limit))
    return _pack(units, " ", limit)


def _lines(parts: tuple[str, ...], sep: str, limit: int, prose: bool) -> list[str]:
    units: list[str] = []
    for part in parts:
        part = part.rstrip("\n")
        if len(part) <= limit:
            units.append(part)
        else:
            units.extend(_sentences(part, limit) if prose else _words(part, limit))
    return _pack(units, sep, limit)


def _cut(block: _Block, limit: int) -> list[str]:
    """A block longer than a section, in pieces that fit."""
    if block.prose and not block.whole:
        return _sentences(block.text, limit)
    return [piece for piece in (p.strip("\n") for p in _lines(block.parts, block.sep, limit,
                                                                  block.prose)) if piece.strip()]


def _body(blocks: list[_Block], limit: int) -> list[str]:
    """A section's blocks packed into pieces of at most `limit`, a block
    never split while it fits in one."""
    pieces: list[str] = []
    current = ""
    for block in blocks:
        text = block.text
        if not text:
            continue
        if current and len(current) + 2 + len(text) <= limit:
            current += "\n\n" + text
            continue
        if current:
            pieces.append(current)
            current = ""
        if len(text) <= limit:
            current = text
        else:
            cut = _cut(block, limit)
            pieces.extend(cut[:-1])
            current = cut[-1] if cut else ""
    if current:
        pieces.append(current)
    return pieces


def _sections(items: list[_Item], title: str, limit: int = SECTION_MAX) -> list[Section]:
    """Items as sections: one per heading (or page), cut to `limit`."""
    found: list[Section] = []
    path: list[tuple[int, str]] = []
    page: Optional[int] = None
    blocks: list[_Block] = []

    def close() -> None:
        heading = path[-1][1] if path else title
        for text in _body(blocks, limit):
            found.append(Section(len(found), heading, tuple(t for _, t in path), text, page))
        blocks.clear()

    for item in items:
        if isinstance(item, _Block):
            blocks.append(item)
            continue
        close()
        if isinstance(item, _Page):
            page = item.number
            path = [(1, item.heading)] if item.heading else []
        elif item.text:
            while path and path[-1][0] >= item.level:
                path.pop()
            path.append((item.level, item.text))
    close()
    return found


def _headings(items: list[_Item]) -> tuple[str, ...]:
    return tuple(item.text if isinstance(item, _Heading) else item.heading
                 for item in items
                 if (isinstance(item, _Heading) and item.text)
                 or (isinstance(item, _Page) and item.heading))


def _first_h1(items: list[_Item]) -> Optional[str]:
    return next((i.text for i in items if isinstance(i, _Heading) and i.level == 1 and i.text),
                None)


def split(text: str, *, title: str, markdown: bool,
          pages: Optional[list[tuple[int, int]]] = None) -> list[Section]:
    """Text as sections. `pages` is each page's (number, the offset its text
    starts at in `text`), in order: a section never crosses a page, and
    each says its page. Otherwise Markdown is cut at its headings, and
    plain text is one section cut at paragraphs, sentences and words."""
    if pages:
        items: list[_Item] = []
        for n, (number, start) in enumerate(pages):
            end = pages[n + 1][1] if n + 1 < len(pages) else len(text)
            items.append(_Page(number))
            items.extend(_paragraph_items(text[start:end], prose=True))
        return _sections(items, title)
    if markdown:
        return _sections(_markdown_items(text)[0], title)
    return _sections(_paragraph_items(text, prose=True), title)


# -- the text a vector is made from ---------------------------------------------------------

def section_json(section: Section) -> dict:
    """A section as the engine's content stream holds it."""
    out = {"heading": section.heading, "heading_path": list(section.heading_path), "text": section.text}
    if section.page is not None:
        out["page"] = section.page
    return out


def embedded_text(title: str, section: Section) -> str:
    """What a section's vector is made from: "Title › H1 › H2", then its
    text; within EMBED_MAX. The engine's one definition
    (`knowledge.items.embedded_text`), which embeds it."""
    return item_texts.embedded_text(title, section_json(section), EMBED_MAX)


def whole_text(extracted: Extracted, limit: int = EMBED_MAX) -> str:
    """What the whole item's vector is made from: its title, its heading
    outline, then the start of its text, within `limit` (the engine's one
    definition, `knowledge.items.whole_text`)."""
    return item_texts.whole_text(extracted.title, {"text": extracted.text, "headings": list(extracted.headings),
                                                   "sections": [section_json(s) for s in extracted.sections]},
                                 limit)


# -- reading a file through bundle_readers ---------------------------------------------------

class _BytesSource:
    """A MemberSource over a file's bytes already in hand."""

    def __init__(self, data: bytes, name: str, scratch: Path):
        self._data, self._scratch = data, scratch
        self.attachment_sha256, self.member_id, self.path = "", name, ""
        self.size = len(data)

    def head(self) -> bytes:
        return self._data[:bundle_readers.SNIFF_BYTES]

    def read(self, limit: Optional[int] = None) -> bytes:
        return self._data

    @contextlib.contextmanager
    def materialize(self) -> Iterator[Path]:
        yield from bundle_readers.private_copy(self._scratch, lambda out: out.write(self._data))


def _read_all(source, detected: Detected) -> tuple[list[tuple[object, str]], tuple[str, ...]]:
    """Every chunk the format's reader gives, with its location, and its notes."""
    reader = bundle_readers.reader_for(detected.format)
    if reader is None:
        raise Unsupported("reader_unavailable")
    chunks: list[tuple[object, str]] = []
    notes: list[str] = []
    cursor = None
    while True:
        part = reader.read_text(source, detected, cursor, _READ_ALL)
        chunks.extend(part.chunks)
        notes.extend(part.notes)
        if part.next_cursor is None or part.next_cursor == cursor:
            break
        cursor = part.next_cursor
    return chunks, tuple(dict.fromkeys(notes))


def _settle(data: bytes, detected: Detected) -> Detected:
    """A zip that is an Office document by its [Content_Types].xml."""
    if detected.format != "zip":
        return detected
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            types = (archive.read(office.CONTENT_TYPES)
                     if office.CONTENT_TYPES in names else None)
    except (zipfile.BadZipFile, OSError, ValueError, KeyError, RuntimeError):
        return detected
    found = office.ooxml_format(names, types)
    return Detected(found, bundle_readers.MEDIA_TYPES[found]) if found else detected


_TEXT_FORMATS = frozenset({"text", "markdown", "json", "csv", "tsv"})


def _csv_items(text: str, delimiter: str) -> list[_Item]:
    """Each row whole (the text reader's rows: a quoted line break keeps a
    row going), rows packed together."""
    rows = text_reader.csv_rows(text, delimiter)
    if rows is None:
        return _paragraph_items(text, prose=False)
    return [_Block(tuple(r.text.rstrip("\n") for r in rows if r.text.strip()),
                   whole=True, prose=False)]


def _text_content(chunks, fmt: str, name: str) -> tuple[str, list[_Item], Optional[str]]:
    text = "".join(t for _, t in chunks)
    prose = _suffix(name) in _PROSE_SUFFIXES
    if fmt == "markdown":
        items, front_title = _markdown_items(text)
        return text, items, front_title
    if fmt in ("csv", "tsv"):
        return text, _csv_items(text, "\t" if fmt == "tsv" else ","), None
    return text, _paragraph_items(text, prose=prose and fmt == "text"), None


def _pdf_content(chunks) -> tuple[str, list[_Item], int]:
    pages: dict[int, str] = {}
    for location, text in chunks:
        if isinstance(location, PageLocation):
            pages[location.page] = pages.get(location.page, "") + text
    items: list[_Item] = []
    for number in sorted(pages):
        items.append(_Page(number))
        items.extend(_paragraph_items(pages[number], prose=True))
    text = "\n\n".join(pages[n].strip() for n in sorted(pages) if pages[n].strip())
    return text, items, max(pages, default=0)


def _slides_content(chunks) -> tuple[str, list[_Item], int]:
    slides: dict[int, str] = {}
    for location, text in chunks:
        if isinstance(location, SlideLocation):
            slides[location.slide] = slides.get(location.slide, "") + text
    items: list[_Item] = []
    for number in sorted(slides):
        lines = [line for line in slides[number].split("\n") if line.strip()]
        items.append(_Page(number, lines[0].strip() if lines else ""))
        if lines:
            items.append(_Block(tuple(lines), prose=True))
    text = "\n\n".join(slides[n].strip() for n in sorted(slides) if slides[n].strip())
    return text, items, len(slides)


def _sheets_content(chunks) -> tuple[str, list[_Item]]:
    sheets: dict[str, list[str]] = {}
    for location, text in chunks:
        if isinstance(location, SheetLocation):
            sheets.setdefault(location.sheet, []).extend(
                line for line in text.split("\n") if line.strip())
    items: list[_Item] = []
    for name, rows in sheets.items():
        items.append(_Heading(1, name))
        items.append(_Block(tuple(rows), whole=True, prose=False))
    text = "\n\n".join(f"{name}\n" + "\n".join(rows) for name, rows in sheets.items())
    return text, items


def _outline_items(blocks: list[tuple[str, Optional[int]]]) -> list[_Item]:
    """Paragraphs with heading levels (Word, HTML) as items."""
    return [_Heading(level, text.strip()) if level else _Block((text,), prose=True)
            for text, level in blocks if text.strip()]


def _structured(data: bytes, detected: Detected, chunks, name: str):
    """(text, items, the document's own title, pages) for one format."""
    fmt = detected.format
    if fmt in _TEXT_FORMATS:
        text, items, own = _text_content(chunks, fmt, name)
        return text, items, own, None
    if fmt == "pdf":
        text, items, pages = _pdf_content(chunks)
        return text, items, None, pages
    if fmt in ("html", "svg"):
        blocks, own = html.outline(data)
        return "\n".join(t for t, _ in blocks), _outline_items(blocks), own, None
    pkg = office.open_package(data)
    own = office.core_title(pkg)
    if fmt == "docx":
        paragraphs = office.paragraphs(pkg)
        text = "\n".join(t for t, _ in paragraphs if t.strip())
        return text, _outline_items(paragraphs), own, None
    if fmt == "pptx":
        text, items, slides = _slides_content(chunks)
        return text, items, own, slides
    text, items = _sheets_content(chunks)
    return text, items, own, None


_READABLE = _TEXT_FORMATS | {"pdf", "html", "svg", "docx", "xlsx", "pptx"}
_H1_FORMATS = frozenset({"markdown", "html", "docx"})


def extract(data: bytes, name: str, scope: str = "text") -> Union[Extracted, Unreadable]:
    """A file's extracted text and sections, read through bundle_readers,
    or why it has none. A scanned PDF is Extracted with no sections and the
    note scanned_no_text. Nothing here raises for a file's content."""
    if not in_scope(name, scope):
        return Unreadable("out_of_scope")
    if len(data) > FILE_MAX_BYTES:
        return Unreadable("too_large")
    if not data.strip():
        return Unreadable("empty")
    detected = _settle(data, bundle_readers.detect(name, data[:bundle_readers.SNIFF_BYTES]))
    if detected.format == "unknown":
        return Unreadable("binary")
    if detected.format not in _READABLE:
        return Unreadable("reader_unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-sections-") as scratch:
            source = _BytesSource(data, name, Path(scratch))
            chunks, notes = _read_all(source, detected)
            text, items, own, pages = _structured(data, detected, chunks, name)
    except Unsupported as e:
        return Unreadable(e.reason)
    except (ValueError, OSError, RecursionError):
        return Unreadable("corrupt")
    if text.strip():
        notes = tuple(n for n in notes if n != "scanned_no_text")   # a blank page or two
    elif "scanned_no_text" not in notes:
        return Unreadable("empty")
    title = ((_first_h1(items) if detected.format in _H1_FORMATS else None) or own
             or PurePosixPath(name).stem or name)
    title = " ".join(title.split())
    return Extracted(format=detected.format, media_type=detected.media_type, title=title,
                     text=text, sections=tuple(_sections(items, title)),
                     headings=_headings(items), pages=pages, notes=notes)
