"""PNG, JPEG, GIF and WebP: never decoded. Their size comes from their
headers, read with the standard library, and their bytes are served as
they are (the preview's image is the file itself, through
BundleInspection.html_asset).

Each is also its own rendered page (page 1), exactly as sent with its own
media type, at its own size whatever `max_edge` asks: shrinking it or
turning it into a PNG would mean decoding it, which on this Mac goes
through ImageIO (its GIF reader crashes here).
"""
from __future__ import annotations

import struct
from typing import Optional

from jarvis.bundle_contract import (Extent, RenderedPage, RenderedPageInfo, RenderOptions)
from jarvis.bundle_readers import (Description, Detected, MemberSource, ReaderHealth,
                                   TextSlice, Unsupported, read_bounded)

IMAGE_MAX_BYTES = 64 * 1024 * 1024


def _png(data: bytes) -> Optional[tuple[int, int]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR" or len(data) < 24:
        return None
    return struct.unpack(">II", data[16:24])


def _gif(data: bytes) -> Optional[tuple[int, int]]:
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 10:
        return None
    return struct.unpack("<HH", data[6:10])


def _jpeg(data: bytes) -> Optional[tuple[int, int]]:
    """The first start-of-frame marker's size, walking the segments."""
    if data[:2] != b"\xff\xd8":
        return None
    at = 2
    while at + 4 <= len(data):
        if data[at] != 0xFF:
            return None
        marker = data[at + 1]
        if marker == 0xFF:              # fill byte
            at += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        (length,) = struct.unpack(">H", data[at + 2:at + 4])
        if length < 2:
            return None
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD,
                      0xCE, 0xCF):
            if at + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[at + 5:at + 9])
            return width, height
        if marker == 0xD9:
            return None
        at += 2 + length
    return None


def _webp(data: bytes) -> Optional[tuple[int, int]]:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP" or len(data) < 30:
        return None
    kind = data[12:16]
    if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if kind == b"VP8L" and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        return (int.from_bytes(data[24:27], "little") + 1,
                int.from_bytes(data[27:30], "little") + 1)
    return None


_SIZES = {"png": _png, "gif": _gif, "jpeg": _jpeg, "webp": _webp}
_MEDIA = {"png": "image/png", "gif": "image/gif", "jpeg": "image/jpeg", "webp": "image/webp"}


def dimensions(fmt: str, data: bytes) -> Optional[tuple[int, int]]:
    """(width, height) from the header, or None when it is not a sound one."""
    try:
        size = _SIZES[fmt](data)
    except (KeyError, struct.error, IndexError):
        return None
    return size if size and size[0] > 0 and size[1] > 0 else None


class ImageReader:
    name = "image"
    version = 1
    formats = frozenset({"png", "jpeg", "gif", "webp"})

    def health(self) -> ReaderHealth:
        return ReaderHealth(True, frozenset({"render"}))

    def size(self, source: MemberSource, detected: Detected) -> tuple[int, int]:
        size = dimensions(detected.format, read_bounded(source, IMAGE_MAX_BYTES))
        if size is None:
            raise Unsupported("corrupt")
        return size

    def describe(self, source: MemberSource, detected: Detected) -> Description:
        try:
            self.size(source, detected)
        except Unsupported as e:
            return Description("unsupported", Extent(), e.reason)
        return Description("readable", Extent(pages=1), operations=frozenset({"render"}))

    def read_text(self, source: MemberSource, detected: Detected, cursor: Optional[dict],
                  max_chars: int) -> TextSlice:
        raise Unsupported("reader_unavailable")     # OCR is its own operation, not offered

    def render(self, source: MemberSource, detected: Detected, page: int,
               options: RenderOptions) -> RenderedPage:
        data = read_bounded(source, IMAGE_MAX_BYTES)
        size = dimensions(detected.format, data)
        if size is None:
            raise Unsupported("corrupt")
        return RenderedPage(RenderedPageInfo(member_id=source.member_id, page=1, page_count=1,
                                             width=size[0], height=size[1],
                                             media_type=_MEDIA[detected.format]), data)
