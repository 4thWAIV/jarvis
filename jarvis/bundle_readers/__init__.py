"""Format readers for collaboration bundles: the interface each format's
adapter implements, format detection, and the one registry the inspection
service (bundle_inspection.py) and the capability advertisement
(bundle_capabilities.py) read.

A reader only reads. It never runs a member, loads a macro, executes a
script in HTML, follows a link or clears a quarantine mark. Bytes reach it
through a `MemberSource`, which enforces bundle_archive's budgets on what is
actually decompressed; a reader that needs a file on disk (PDFKit through
`osascript -l JavaScript`) asks the source to `materialize` one: a private
0600 copy in the cache's scratch folder, removed when the block ends.

Each adapter is one module beside this one (pdf.py, text.py, image.py,
html.py, office.py), each a `Reader` added to READERS. No new dependencies:
PDF text and page renders through macOS PDFKit/CoreGraphics in JXA, PNG
encoded with zlib; text, Markdown, JSON, CSV/TSV and HTML with the standard
library; Office Open XML (docx/xlsx/pptx) with zipfile and xml.etree through
bundle_archive. Pillow is installed but undeclared and is never imported.
"""
from __future__ import annotations

import re
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional, Protocol, runtime_checkable

from jarvis import bundle_archive
from jarvis.bundle_contract import (Extent, FormatId, Location, Operation, Reason,
                                    RenderedPage, RenderOptions, Support)

# Bytes of a member's head that detection looks at.
SNIFF_BYTES = 8 * 1024


@dataclass(frozen=True)
class Detected:
    """A member's actual format: decided from its content first, then from
    its extension; the sender's declared type is never consulted."""
    format: FormatId
    media_type: str


class MemberSource(Protocol):
    """One member's bytes, bounded. `path` is "" for an attachment that is
    not an archive."""
    attachment_sha256: str
    member_id: str
    path: str
    size: int

    def head(self) -> bytes:
        """The first SNIFF_BYTES."""

    def read(self, limit: Optional[int] = None) -> bytes:
        """The member's bytes, up to `limit`, under the archive budgets
        (bundle_archive.ArchiveError past them)."""

    def materialize(self) -> AbstractContextManager[Path]:
        """A private, read-only, non-executable copy on disk for the block."""


@dataclass(frozen=True)
class Description:
    """What a reader can say about a member without reading all of it."""
    support: Support
    extent: Extent
    reason: Optional[Reason] = None
    operations: frozenset[Operation] = frozenset()


@dataclass(frozen=True)
class TextSlice:
    """Up to `max_chars` of text from `cursor` on: chunks with their
    locations, and the cursor to continue from (None at the end)."""
    chunks: list[tuple[Location, str]]
    next_cursor: Optional[dict]
    notes: tuple[Reason, ...] = ()


@dataclass(frozen=True)
class ReaderHealth:
    """Whether a reader works on this Mac now (e.g. `osascript` answers),
    checked rather than assumed, and which operations it then offers."""
    available: bool
    operations: frozenset[Operation] = frozenset()
    reason: Optional[Reason] = None


class Unsupported(Exception):
    """This reader cannot do that for this member; `reason` says why."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@runtime_checkable
class Reader(Protocol):
    name: str
    version: int                      # bump when output changes: the cache key has it
    formats: frozenset[FormatId]

    def health(self) -> ReaderHealth: ...

    def describe(self, source: MemberSource, detected: Detected) -> Description: ...

    def read_text(self, source: MemberSource, detected: Detected,
                  cursor: Optional[dict], max_chars: int) -> TextSlice: ...

    def render(self, source: MemberSource, detected: Detected, page: int,
               options: RenderOptions) -> RenderedPage:
        """Raises Unsupported when the format has no rendering."""
        ...


def read_bounded(source: MemberSource, limit: int) -> bytes:
    """A member's whole bytes when it is at most `limit`, else
    Unsupported("too_large"); an archive's refusal becomes its reason."""
    if source.size > limit:
        raise Unsupported("too_large")
    try:
        return source.read(limit)
    except bundle_archive.ArchiveError as e:
        raise Unsupported(archive_reason(e)) from None


def archive_reason(error) -> Reason:
    """bundle_archive's code for a refusal (an ArchiveError, or a listing's
    Refused entry: each has `reason` and `kind`), as the contract's Reason."""
    if error.reason == "bad-kind":
        return {"link": "link", "encrypted": "encrypted",
                "device": "special_file"}.get(error.kind, "bad_name")
    return {"bad-name": "bad_name", "outside": "outside", "collision": "duplicate",
            "too-large": "too_large", "too-deep": "budget", "too-many": "budget",
            "ratio": "budget", "too-slow": "budget", "not-zip": "not_zip",
            "unsupported": "reader_unavailable"}.get(error.reason, "corrupt")


from jarvis.bundle_readers import _paging  # noqa: E402

# -- detection ---------------------------------------------------------------------------

MEDIA_TYPES: dict[str, str] = {
    "folder": "inode/directory", "zip": "application/zip", "tar": "application/x-tar",
    "gzip": "application/gzip", "7z": "application/x-7z-compressed",
    "rar": "application/vnd.rar", "pdf": "application/pdf", "text": "text/plain",
    "markdown": "text/markdown", "json": "application/json", "csv": "text/csv",
    "tsv": "text/tab-separated-values", "html": "text/html", "svg": "image/svg+xml",
    "png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
    "heic": "image/heic",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "doc": "application/msword", "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint", "rtf": "application/rtf",
    "audio": "audio/*", "video": "video/*", "unknown": "application/octet-stream",
}

# Formats that are archives: a member in one of them is not opened.
ARCHIVES: frozenset[str] = frozenset({"zip", "tar", "gzip", "7z", "rar"})

_EXTENSIONS: dict[str, str] = {
    "zip": "zip", "tar": "tar", "tgz": "gzip", "gz": "gzip", "7z": "7z", "rar": "rar",
    "pdf": "pdf", "txt": "text", "text": "text", "log": "text", "md": "markdown",
    "markdown": "markdown", "json": "json", "csv": "csv", "tsv": "tsv", "tab": "tsv",
    "html": "html", "htm": "html", "xhtml": "html", "svg": "svg", "png": "png",
    "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp", "heic": "heic",
    "heif": "heic", "docx": "docx", "docm": "docx", "dotx": "docx", "dotm": "docx",
    "xlsx": "xlsx", "xlsm": "xlsx", "xltx": "xlsx", "xltm": "xlsx", "pptx": "pptx",
    "pptm": "pptx", "potx": "pptx", "potm": "pptx", "ppsx": "pptx", "ppsm": "pptx",
    "doc": "doc", "xls": "xls", "ppt": "ppt", "rtf": "rtf",
    "mp3": "audio", "m4a": "audio", "wav": "audio", "aiff": "audio", "aif": "audio",
    "flac": "audio", "ogg": "audio", "opus": "audio", "aac": "audio",
    "mp4": "video", "m4v": "video", "mov": "video", "webm": "video", "mkv": "video",
    "avi": "video",
}
# Extensions whose content is text of a kind the text readers know; a text
# file with any other extension is read as plain text.
_TEXT_FORMATS = frozenset({"text", "markdown", "json", "csv", "tsv", "html", "svg"})
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MULTIPART = re.compile(r"\.(?:z\d{2}|zip\.\d{3}|\d{3}|part\d+\.rar|r\d{2})$", re.I)


def extension_format(name: str) -> Optional[str]:
    ext = PurePosixPath(name.lower()).suffix.lstrip(".")
    return _EXTENSIONS.get(ext)


def multipart(name: str) -> bool:
    """Whether a name is one volume of a split archive (.z01, .zip.001,
    .part1.rar...): never readable on its own."""
    return bool(_MULTIPART.search(name))


def _ftyp(head: bytes) -> Optional[str]:
    if head[4:8] != b"ftyp":
        return None
    brand = head[8:12]
    if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
        return "heic"
    if brand in (b"M4A ", b"M4B ", b"M4P "):
        return "audio"
    return "video"


def _magic(head: bytes) -> Optional[str]:
    if head.lstrip(b"\x00\t\r\n ")[:5] == b"%PDF-":
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"AVI "):
        return "audio" if head[8:12] == b"WAVE" else "video"
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "tar"
    if head.startswith(_OLE):
        return "ole"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith((b"ID3", b"fLaC", b"OggS", b"FORM")) or head[:2] in (b"\xff\xfb",
                                                                           b"\xff\xf3"):
        return "audio"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video"
    return _ftyp(head)


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return True
    if any(head.startswith(bom) for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in head:
        return False
    controls = sum(1 for b in head if b < 32 and b not in (9, 10, 12, 13, 27))
    return controls * 100 <= len(head)


def _text_kind(head: bytes, by_name: Optional[str]) -> str:
    if by_name in _TEXT_FORMATS:
        return by_name
    start = _paging.decode(head[:1024]).lstrip().lower()
    if start.startswith(("<!doctype html", "<html")):
        return "html"
    if start.startswith("<svg") or (start.startswith("<?xml") and "<svg" in start):
        return "svg"
    return "text"


def detect(name: str, head: bytes) -> Detected:
    """A member's format from its first bytes (magic numbers; a zip's
    [Content_Types].xml decides docx/xlsx/pptx) and then its extension.
    A zip's head is only its first entry: the inspection service settles an
    Office document from the whole archive (office.ooxml_format)."""
    by_name = extension_format(name)
    found = _magic(head)
    if found == "zip":
        if by_name in ("docx", "xlsx", "pptx"):
            found = by_name
        elif b"[Content_Types].xml" in head:
            found = next((fmt for fmt, folder in (("docx", b"word/"), ("xlsx", b"xl/"),
                                                  ("pptx", b"ppt/")) if folder in head), "zip")
    elif found == "ole":
        found = by_name if by_name in ("doc", "xls", "ppt") else "unknown"
    elif found is None:
        if _looks_like_text(head):
            found = _text_kind(head, by_name)
        else:
            found = by_name if by_name and by_name not in _TEXT_FORMATS else "unknown"
    return Detected(found, MEDIA_TYPES[found])


# -- the registry ------------------------------------------------------------------------

from jarvis.bundle_readers.html import HtmlReader  # noqa: E402
from jarvis.bundle_readers.image import ImageReader  # noqa: E402
from jarvis.bundle_readers.office import OfficeReader  # noqa: E402
from jarvis.bundle_readers.pdf import PdfReader  # noqa: E402
from jarvis.bundle_readers.text import TextReader  # noqa: E402

READERS: tuple[Reader, ...] = (PdfReader(), TextReader(), HtmlReader(), ImageReader(),
                               OfficeReader())


def reader_for(format: FormatId) -> Optional[Reader]:
    """The registered reader for a format, or None."""
    return next((r for r in READERS if format in r.formats), None)


def health() -> dict[FormatId, ReaderHealth]:
    """Each format's reader health on this Mac, for the capability
    advertisement. Formats with no reader are absent."""
    found: dict[FormatId, ReaderHealth] = {}
    for reader in READERS:
        state = reader.health()
        for fmt in sorted(reader.formats):
            found[fmt] = state
    return found
