"""Real, well-formed pictures for tests that need one JARVIS will prove
(picture_proof): built with the standard library, each a few hundred bytes."""
from __future__ import annotations

import struct
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(width: int = 240, height: int = 160) -> bytes:
    """An RGB PNG of one colour."""
    rows = b"".join(b"\x00" + b"\x30\x60\x90" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))


def gif(width: int = 240, height: int = 160) -> bytes:
    """A two-colour GIF whose image is one colour, LZW coded with clear
    codes so the code size never grows."""
    codes, bits, have = [], 0, 0
    pixels = width * height
    run = []
    for n in range(pixels):
        if n % 2 == 0:
            run.append(4)           # clear
        run.append(0)
    run.append(5)                   # end
    out = bytearray()
    for code in run:
        bits |= code << have
        have += 3
        while have >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            have -= 8
    if have:
        out.append(bits & 0xFF)
    blocks = b"".join(bytes([len(out[i:i + 255])]) + bytes(out[i:i + 255]) for i in range(0, len(out), 255))
    return (b"GIF89a" + struct.pack("<HHBBB", width, height, 0x80, 0, 0) + b"\x00\x00\x00\xff\xff\xff"
            + b"\x2c" + struct.pack("<HHHHB", 0, 0, width, height, 0) + b"\x02" + blocks + b"\x00\x3b")


def jpeg(width: int = 240, height: int = 160) -> bytes:
    """A JPEG's structure: tables, one frame, one scan, EOI (its scan data is
    never decoded, so it need not draw)."""
    def segment(marker: int, data: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(data) + 2) + data
    return (b"\xff\xd8" + segment(0xDB, b"\x00" + bytes(64))
            + segment(0xC0, b"\x08" + struct.pack(">HH", height, width) + b"\x01\x01\x11\x00")
            + segment(0xC4, b"\x00" + bytes(16)) + segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
            + b"\x12\x34\xff\x00\x56" + b"\xff\xd9")


def webp(width: int = 240, height: int = 160) -> bytes:
    """A lossless WebP's container and VP8L header."""
    bits = (width - 1) | ((height - 1) << 14)
    body = b"\x2f" + bits.to_bytes(4, "little") + b"\x00" * 5
    chunk = b"VP8L" + struct.pack("<I", len(body)) + body
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def svg(width: int = 240, height: int = 160, label_px: float | None = None, extra: str = "") -> bytes:
    text = f'<text x="10" y="40" font-size="{label_px}">Label</text>' if label_px else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}"><rect width="{width}" height="{height}" fill="#123"/>'
            f'{text}{extra}</svg>').encode()
