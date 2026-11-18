"""The bundled font's widths, so text fit is computed rather than measured.

JARVIS draws every deck in one bundled open-licence font (Inter, SIL OFL
1.1, presentation_assets/font/). `read_ttf` reads what a layout needs from
a TrueType file with the standard library alone (the advance width of every
character it maps, its units per em, ascent and descent), once, into
presentation_assets/font-widths.json (layout_spec's generator writes it);
`width` sums a run's advances at a size with kerning and ligatures off, as
the CSS draws it. A character the font does not have is never guessed at:
`missing` names it, and the proof refuses it.
"""
from __future__ import annotations

import functools
import json
import struct
from pathlib import Path

ASSETS = Path(__file__).with_name("presentation_assets")
FONT_DIR = ASSETS / "font"
WIDTHS = ASSETS / "font-widths.json"
FACES = {"regular": "Inter-Regular.ttf", "bold": "Inter-Bold.ttf"}
FAMILY = "JARVIS Deck"
# The characters a deck's words may use: Latin with its accents (English and
# Spanish), general punctuation and currency signs.
RANGES = ((0x20, 0x7E), (0xA0, 0x17F), (0x2010, 0x2027), (0x2030, 0x203A), (0x20A0, 0x20BF))


def _tables(raw: bytes) -> dict[str, bytes]:
    (count,) = struct.unpack(">H", raw[4:6])
    out = {}
    for n in range(count):
        tag, _, offset, length = struct.unpack(">4sIII", raw[12 + 16 * n:28 + 16 * n])
        out[tag.decode("latin-1")] = raw[offset:offset + length]
    return out


def _cmap(table: bytes) -> dict[int, int]:
    """Code point to glyph, from the best Unicode subtable (format 12, else 4)."""
    (count,) = struct.unpack(">H", table[2:4])
    best = {}
    for n in range(count):
        platform, encoding, offset = struct.unpack(">HHI", table[4 + 8 * n:12 + 8 * n])
        if (platform, encoding) in ((3, 10), (0, 4), (3, 1), (0, 3)):
            (fmt,) = struct.unpack(">H", table[offset:offset + 2])
            best.setdefault(fmt, offset)
    mapping: dict[int, int] = {}
    if 12 in best:
        at = best[12]
        (groups,) = struct.unpack(">I", table[at + 12:at + 16])
        for g in range(groups):
            start, end, glyph = struct.unpack(">III", table[at + 16 + 12 * g:at + 28 + 12 * g])
            for code in range(start, end + 1):
                mapping[code] = glyph + code - start
        return mapping
    at = best[4]
    (seg2,) = struct.unpack(">H", table[at + 6:at + 8])
    segs = seg2 // 2
    ends = struct.unpack(f">{segs}H", table[at + 14:at + 14 + seg2])
    starts = struct.unpack(f">{segs}H", table[at + 16 + seg2:at + 16 + 2 * seg2])
    deltas = struct.unpack(f">{segs}h", table[at + 16 + 2 * seg2:at + 16 + 3 * seg2])
    range_at = at + 16 + 3 * seg2
    offsets = struct.unpack(f">{segs}H", table[range_at:range_at + seg2])
    for n in range(segs):
        for code in range(starts[n], ends[n] + 1):
            if code == 0xFFFF:
                continue
            if offsets[n] == 0:
                glyph = (code + deltas[n]) & 0xFFFF
            else:
                where = range_at + 2 * n + offsets[n] + 2 * (code - starts[n])
                (glyph,) = struct.unpack(">H", table[where:where + 2])
                glyph = (glyph + deltas[n]) & 0xFFFF if glyph else 0
            if glyph:
                mapping[code] = glyph
    return mapping


def read_ttf(path: Path) -> dict:
    """{units_per_em, ascent, descent, widths: {code point: advance}} for
    every character in RANGES the font maps."""
    tables = _tables(path.read_bytes())
    (units,) = struct.unpack(">H", tables["head"][18:20])
    ascent, descent = struct.unpack(">hh", tables["hhea"][4:8])
    (metrics,) = struct.unpack(">H", tables["hhea"][34:36])
    advances = struct.unpack(f">{metrics * 2}H", tables["hmtx"][:metrics * 4])[::2]
    glyphs = _cmap(tables["cmap"])
    widths = {}
    for low, high in RANGES:
        for code in range(low, high + 1):
            glyph = glyphs.get(code)
            if glyph is not None:
                widths[str(code)] = advances[min(glyph, metrics - 1)]
    return {"units_per_em": units, "ascent": ascent, "descent": -descent, "widths": widths}


def table() -> dict:
    """The width table for every face, from the TTF files (the generator's
    source of font-widths.json)."""
    return {face: read_ttf(FONT_DIR / name) for face, name in FACES.items()}


@functools.cache
def _loaded() -> dict:
    data = json.loads(WIDTHS.read_text(encoding="utf-8"))
    return {face: (info["units_per_em"], {int(k): v for k, v in info["widths"].items()})
            for face, info in data.items()}


def missing(text: str, face: str = "regular") -> list[str]:
    """The characters in `text` the font cannot draw (whitespace aside)."""
    widths = _loaded()[face][1]
    return sorted({c for c in text if not c.isspace() and ord(c) not in widths})


def width(text: str, px: float, face: str = "regular") -> float:
    """How wide `text` is drawn at `px`, kerning and ligatures off."""
    units, widths = _loaded()[face]
    space = widths[0x20]
    return sum(widths.get(ord(c), space) for c in text) * px / units
