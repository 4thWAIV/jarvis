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

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

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


# The registry: one reader per format. Filled by the readers lane.
READERS: tuple[Reader, ...] = ()


def detect(name: str, head: bytes) -> Detected:
    """A member's format from its first bytes (magic numbers; a zip's
    [Content_Types].xml decides docx/xlsx/pptx) and then its extension."""
    raise NotImplementedError("readers lane")


def reader_for(format: FormatId) -> Optional[Reader]:
    """The registered reader for a format, or None."""
    return next((r for r in READERS if format in r.formats), None)


def health() -> dict[FormatId, ReaderHealth]:
    """Each format's reader health on this Mac, for the capability
    advertisement. Formats with no reader are absent."""
    raise NotImplementedError("readers lane")
