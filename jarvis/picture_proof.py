"""Every picture in a deck proven real and drawable, without a browser.

One checker for PNG, GIF, JPEG, WebP and SVG, standard library only. A
picture is read from its bytes (its kind is what its bytes say, never its
name), walked whole, and its size taken from the header readers the bundle
inspection already uses (bundle_readers.image.dimensions), so the size a
deck is proven against is the size the browser will see:

- PNG: the signature, every chunk's length and CRC-32, IHDR first and
  IEND last, the IDAT stream inflated to exactly the size IHDR implies and
  every row's filter byte 0 to 4.
- GIF: the header and logical screen, every block and sub-block walked to
  the trailer, each image inside the screen, its LZW data decoded to at
  least the image's pixel count.
- JPEG: SOI, every marker segment's length, one frame with its size,
  quantisation and Huffman tables, scan data running to EOI. Structure is
  checked; the image data is not decoded.
- WebP: the RIFF length equals the file, every chunk inside it, a VP8,
  VP8L or VP8X chunk giving its size. Structure only, as for JPEG.
- SVG: XML with no DOCTYPE or entity, only the static elements listed
  here, no script, link, style or outside reference, a root width and
  height in pixels, a viewBox (when given) of the same shape, and every
  label's font size known, with no transform on a label but a translate or
  a uniform scale.

How large a picture is drawn is a formula (`drawn`): the box it is given
times min(box width / picture width, box height / picture height, 2), so a
small picture is never stretched past twice its size. Drawn under
layout_spec's picture floor either way, or with a label under the text
floor, it is refused (`drawn_problem`).
"""
from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path

from jarvis import layout_spec
from jarvis.bundle_readers.image import dimensions

# What JARVIS reads of one picture at most, and what a PNG may inflate to.
PICTURE_MAX_BYTES = 32 * 1024 * 1024
INFLATED_MAX_BYTES = 256 * 1024 * 1024
MIME = {"png": "image/png", "gif": "image/gif", "jpeg": "image/jpeg",
        "webp": "image/webp", "svg": "image/svg+xml"}
SVG_ELEMENTS = frozenset({"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
                          "polygon", "text", "tspan", "title", "desc", "defs", "linearGradient",
                          "radialGradient", "stop", "clipPath", "mask"})
SVG_DEFAULT_FONT_PX = 16.0      # a label with no font-size anywhere above it


class PictureProblem(ValueError):
    """Why a picture is refused, in words for the author."""


@dataclass(frozen=True)
class Picture:
    """A proven picture: its kind, its size in pixels and, for an SVG with
    labels, the smallest label's size at the SVG's own size."""
    kind: str
    width: int | float
    height: int | float
    label_px: float | None = None

    @property
    def mime(self) -> str:
        return MIME[self.kind]


def kind_of(raw: bytes) -> str | None:
    """What the bytes are, or None for no picture JARVIS draws."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    head = raw[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<") and re.search(rb"<svg[\s>]", head):
        return "svg"
    return None


def prove(path: Path, mime: str | None = None) -> Picture:
    """The picture at `path`, proven, or PictureProblem saying why not. A
    declared `mime` must be what the bytes are."""
    if path.is_symlink() or not path.is_file():
        raise PictureProblem("it is not a regular file (a link or a folder is never a picture)")
    if path.stat().st_size > PICTURE_MAX_BYTES:
        raise PictureProblem(f"it is over {PICTURE_MAX_BYTES // (1024 * 1024)} MB")
    return prove_bytes(path.read_bytes(), mime)


def prove_bytes(raw: bytes, mime: str | None = None) -> Picture:
    kind = kind_of(raw)
    if kind is None:
        raise PictureProblem("it is not a PNG, GIF, JPEG, WebP or SVG picture")
    if mime is not None and MIME[kind] != mime:
        raise PictureProblem(f"its bytes are {MIME[kind]}, not the declared {mime}")
    if kind == "svg":
        return _svg(raw)
    try:
        _WALK[kind](raw)
    except (struct.error, IndexError) as error:
        raise PictureProblem(f"its {kind.upper()} data ends early ({error})") from None
    size = dimensions(kind, raw)
    if size is None:
        raise PictureProblem(f"its {kind.upper()} header gives no size")
    return Picture(kind, *size)


# -- the drawn size ---------------------------------------------------------------------

def drawn(picture: Picture, box: tuple[float, float]) -> tuple[float, float]:
    """How large the picture is drawn in a box of `box` pixels: contained,
    never stretched past layout_spec's stretch limit."""
    scale = min(box[0] / picture.width, box[1] / picture.height, layout_spec.spec()["picture"]["stretch_max"])
    return picture.width * scale, picture.height * scale


def drawn_problem(picture: Picture, box: tuple[float, float]) -> str:
    """Why the picture cannot be read in `box` ("" when it can)."""
    spec = layout_spec.spec()
    floor, text_floor = spec["picture"]["min_px"], spec["text"]["min_px"]
    width, height = drawn(picture, box)
    if width < floor or height < floor:
        return (f"it is {picture.width:g} x {picture.height:g} px and is drawn at "
                f"{width:.0f} x {height:.0f} px in its {box[0]:.0f} x {box[1]:.0f} px place at the "
                f"smallest frame; under {floor} px either way it cannot be read. Give it a shape "
                f"nearer its place's, or more room")
    if picture.label_px is not None:
        label = picture.label_px * width / picture.width
        if label < text_floor:
            return (f"its smallest label is drawn at {label:.1f} px at the smallest frame; every "
                    f"word is at least {text_floor} px. Make its labels larger or the picture "
                    f"smaller in its own units")
    return ""


# -- PNG --------------------------------------------------------------------------------

_PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
          (0, 1, 1, 2))


def _png(raw: bytes) -> None:
    at, chunks = 8, []
    while at < len(raw):
        if at + 12 > len(raw):
            raise PictureProblem("its PNG chunks end part way through one")
        (length,) = struct.unpack(">I", raw[at:at + 4])
        kind, data = raw[at + 4:at + 8], raw[at + 8:at + 8 + length]
        if len(data) != length or at + 12 + length > len(raw):
            raise PictureProblem(f"its PNG {kind.decode('latin-1')} chunk is cut short")
        (crc,) = struct.unpack(">I", raw[at + 8 + length:at + 12 + length])
        if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
            raise PictureProblem(f"its PNG {kind.decode('latin-1')} chunk fails its CRC")
        chunks.append((kind, data))
        at += 12 + length
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise PictureProblem("its PNG does not begin with IHDR")
    if chunks[-1][0] != b"IEND" or chunks[-1][1]:
        raise PictureProblem("its PNG does not end with IEND")
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if not width or not height or depth not in _PNG_DEPTHS.get(colour, ()) \
            or compression or filtering or interlace not in (0, 1):
        raise PictureProblem("its PNG header is not a valid one")
    kinds = [k for k, _ in chunks]
    if colour == 3 and b"PLTE" not in kinds:
        raise PictureProblem("its PNG has a palette colour type and no palette")
    idat = [n for n, k in enumerate(kinds) if k == b"IDAT"]
    if not idat or idat != list(range(idat[0], idat[-1] + 1)):
        raise PictureProblem("its PNG image data is missing or not in one run")
    bits = depth * _PNG_CHANNELS[colour]
    passes = [(width, height)] if not interlace else [
        ((width - x + dx - 1) // dx, (height - y + dy - 1) // dy) for x, y, dx, dy in _ADAM7]
    rows = [(w, h) for w, h in passes if w and h]
    expected = sum(h * (1 + (w * bits + 7) // 8) for w, h in rows)
    if expected > INFLATED_MAX_BYTES:
        raise PictureProblem("its PNG is too large to prove")
    stream = zlib.decompressobj()
    try:
        pixels = stream.decompress(b"".join(chunks[n][1] for n in idat), expected + 1)
    except zlib.error as error:
        raise PictureProblem(f"its PNG image data does not inflate ({error})") from None
    if len(pixels) != expected or not stream.eof or stream.unconsumed_tail:
        raise PictureProblem(f"its PNG image data inflates to {len(pixels)} bytes, not the "
                             f"{expected} its header gives")
    at = 0
    for w, h in rows:
        stride = 1 + (w * bits + 7) // 8
        for _ in range(h):
            if pixels[at] > 4:
                raise PictureProblem(f"its PNG has a row with filter {pixels[at]} (0 to 4 only)")
            at += stride


# -- GIF --------------------------------------------------------------------------------

def _sub_blocks(raw: bytes, at: int) -> tuple[bytes, int]:
    """The data of the sub-blocks starting at `at`, and where they end."""
    parts = []
    while True:
        size = raw[at]
        at += 1
        if not size:
            return b"".join(parts), at
        if at + size > len(raw):
            raise PictureProblem("its GIF has a block cut short")
        parts.append(raw[at:at + size])
        at += size


def _lzw_count(data: bytes, minimum: int, wanted: int) -> int:
    """How many pixels the LZW data decodes to (stopping once `wanted` is
    reached), counting lengths only."""
    clear, end = 1 << minimum, (1 << minimum) + 1
    lengths: list[int] = []
    size, previous, count, bits, have, at = minimum + 1, None, 0, 0, 0, 0

    def reset():
        lengths[:] = [1] * clear + [0, 0]

    reset()
    while count < wanted:
        while have < size:
            if at >= len(data):
                return count
            bits |= data[at] << have
            have += 8
            at += 1
        code = bits & ((1 << size) - 1)
        bits >>= size
        have -= size
        if code == clear:
            reset()
            size, previous = minimum + 1, None
            continue
        if code == end:
            return count
        if previous is None:
            if code >= clear:
                raise PictureProblem("its GIF image data starts with a code it has not defined")
            count += 1
            previous = code
            continue
        if code < len(lengths):
            count += lengths[code]
        elif code == len(lengths):
            count += lengths[previous] + 1
        else:
            raise PictureProblem("its GIF image data uses a code it has not defined")
        if len(lengths) < 4096:
            lengths.append(lengths[previous] + 1)
            if len(lengths) == 1 << size and size < 12:
                size += 1
        previous = code
    return count


def _gif(raw: bytes) -> None:
    screen_w, screen_h, packed = struct.unpack("<HHB", raw[6:11])
    at = 13 + (3 * (2 << (packed & 7)) if packed & 0x80 else 0)
    images = 0
    while True:
        if at >= len(raw):
            raise PictureProblem("its GIF has no trailer")
        block = raw[at]
        if block == 0x3B:
            if raw[at + 1:].strip(b"\x00"):
                raise PictureProblem("its GIF has bytes after its trailer")
            break
        if block == 0x21:
            _, at = _sub_blocks(raw, at + 2)
            continue
        if block != 0x2C:
            raise PictureProblem(f"its GIF has an unknown block 0x{block:02x}")
        left, top, w, h, flags = struct.unpack("<HHHHB", raw[at + 1:at + 10])
        if not w or not h or left + w > screen_w or top + h > screen_h:
            raise PictureProblem("its GIF has an image outside its screen")
        at += 10 + (3 * (2 << (flags & 7)) if flags & 0x80 else 0)
        minimum = raw[at]
        if not 2 <= minimum <= 8:
            raise PictureProblem("its GIF image data has an invalid code size")
        data, at = _sub_blocks(raw, at + 1)
        if _lzw_count(data, minimum, w * h) < w * h:
            raise PictureProblem("its GIF image data decodes to fewer pixels than the image has")
        images += 1
    if not images:
        raise PictureProblem("its GIF has no image")


# -- JPEG -------------------------------------------------------------------------------

_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
_ARITHMETIC = frozenset({0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def _jpeg(raw: bytes) -> None:
    at, frames, tables, scans = 2, [], set(), 0
    while True:
        if at + 2 > len(raw):
            raise PictureProblem("its JPEG has no end marker (EOI)")
        if raw[at] != 0xFF:
            raise PictureProblem("its JPEG has bytes where a marker belongs")
        marker = raw[at + 1]
        if marker == 0xFF:
            at += 1
            continue
        if marker == 0xD9:
            if raw[at + 2:].strip(b"\x00"):
                raise PictureProblem("its JPEG has bytes after its end marker")
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7 or marker == 0xD8:
            raise PictureProblem("its JPEG has a marker out of place")
        (length,) = struct.unpack(">H", raw[at + 2:at + 4])
        if length < 2 or at + 2 + length > len(raw):
            raise PictureProblem("its JPEG has a segment longer than the file")
        if marker in _SOF:
            frames.append(marker)
            if length < 8 or not all(struct.unpack(">HH", raw[at + 5:at + 9])):
                raise PictureProblem("its JPEG frame has no size")
        elif marker in (0xC4, 0xDB, 0xCC):
            tables.add(marker)
        at += 2 + length
        if marker == 0xDA:
            scans += 1
            # Entropy-coded data runs to the next marker that is not a
            # stuffed byte or a restart.
            while True:
                at = raw.find(b"\xff", at)
                if at < 0 or at + 1 >= len(raw):
                    raise PictureProblem("its JPEG scan data runs past the end with no EOI")
                following = raw[at + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7 or following == 0xFF:
                    at += 1 if following == 0xFF else 2
                    continue
                break
    if len(frames) != 1:
        raise PictureProblem(f"its JPEG has {len(frames)} frames; one is needed")
    if 0xDB not in tables or (frames[0] not in _ARITHMETIC and 0xC4 not in tables):
        raise PictureProblem("its JPEG is missing its quantisation or Huffman tables")
    if not scans:
        raise PictureProblem("its JPEG has no image data")


# -- WebP -------------------------------------------------------------------------------

def _webp(raw: bytes) -> None:
    (length,) = struct.unpack("<I", raw[4:8])
    if length + 8 != len(raw):
        raise PictureProblem(f"its WebP says it is {length + 8} bytes and is {len(raw)}")
    at, kinds = 12, []
    while at < len(raw):
        if at + 8 > len(raw):
            raise PictureProblem("its WebP chunks end part way through one")
        kind = raw[at:at + 4]
        (size,) = struct.unpack("<I", raw[at + 4:at + 8])
        if at + 8 + size > len(raw):
            raise PictureProblem(f"its WebP {kind.decode('latin-1')} chunk is cut short")
        kinds.append(kind)
        at += 8 + size + (size & 1)
    if not kinds or kinds[0] not in (b"VP8 ", b"VP8L", b"VP8X"):
        raise PictureProblem("its WebP does not begin with a VP8, VP8L or VP8X chunk")
    if kinds[0] == b"VP8X" and not {b"VP8 ", b"VP8L", b"ANMF"} & set(kinds):
        raise PictureProblem("its WebP has no image data")


_WALK = {"png": _png, "gif": _gif, "jpeg": _jpeg, "webp": _webp}


# -- SVG --------------------------------------------------------------------------------

_PIXELS = re.compile(r"[ \t]*(\d+(?:\.\d+)?)(?:px)?[ \t]*")   # spaces only: a line separator is no length
_TRANSFORM = re.compile(r"\s*(\w+)\s*\(([^)]*)\)\s*,?")


def _number(text: str) -> float | None:
    found = _PIXELS.fullmatch(text or "")
    return float(found.group(1)) if found else None


def _scale_of(transform: str) -> float:
    """The uniform scale a transform applies, or PictureProblem for one
    that rotates, skews or stretches."""
    scale, at = 1.0, 0
    for found in _TRANSFORM.finditer(transform):
        if found.start() != at:
            break
        at = found.end()
        name, values = found.group(1), [float(v) for v in re.split(r"[\s,]+", found.group(2).strip()) if v]
        if name == "translate" and 1 <= len(values) <= 2:
            continue
        if name == "scale" and len(values) in (1, 2) and (len(values) == 1 or values[0] == values[1]):
            scale *= abs(values[0])
            continue
        raise PictureProblem("a label in it is rotated, skewed or stretched; only translate and "
                             "a uniform scale can be proven")
    if transform[at:].strip():
        raise PictureProblem("a label in it has a transform that cannot be read")
    return scale


def _svg(raw: bytes) -> Picture:
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise PictureProblem("its SVG has a DOCTYPE or an entity")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise PictureProblem(f"its SVG is not well-formed XML ({error})") from None
    if root.tag.split("}")[-1] != "svg":
        raise PictureProblem("its root is not <svg>")
    for element in root.iter():
        if element.tag.split("}")[-1] not in SVG_ELEMENTS:
            raise PictureProblem(f"its SVG has <{element.tag.split('}')[-1]}>, which is not a "
                                 f"static drawing element")
        for key, value in element.attrib.items():
            name = key.split("}")[-1].lower()
            if name.startswith("on") or name in {"href", "style"}:
                raise PictureProblem("its SVG has an executable, linked or styled attribute")
            if "url(" in value.lower() and not re.fullmatch(r"url\(#[a-zA-Z_][\w.-]*\)", value):
                raise PictureProblem("its SVG refers to something outside it")
    width, height = _number(root.attrib.get("width", "")), _number(root.attrib.get("height", ""))
    if not width or not height:
        raise PictureProblem("its <svg> needs width and height in pixels (for example "
                             "width=\"320\" height=\"220\"), or it can draw as nothing")
    view = root.attrib.get("viewBox")
    unit = 1.0
    if view is not None:
        try:
            _, _, view_w, view_h = (float(v) for v in re.split(r"[\s,]+", view.strip()))
        except ValueError:
            raise PictureProblem("its viewBox is not four numbers") from None
        if view_w <= 0 or view_h <= 0 or abs(view_w / view_h - width / height) > 0.01 * width / height:
            raise PictureProblem("its viewBox has a different shape from its width and height")
        unit = width / view_w
    return Picture("svg", width, height, _labels(root, unit))


def _labels(root: ET.Element, unit: float) -> float | None:
    """The smallest label size in the SVG's own pixels, or None with no text."""
    smallest = None

    def walk(element, font, scale):
        nonlocal smallest
        name = element.tag.split("}")[-1]
        given = element.attrib.get("font-size")
        if given is not None:
            font = _number(given)
            if font is None:
                raise PictureProblem("a label's font-size in it is not in pixels")
        transform = element.attrib.get("transform")
        if transform and any(e.tag.split("}")[-1] in ("text", "tspan") and (e.text or "").strip()
                             for e in element.iter()):
            scale *= _scale_of(transform)
        if name in ("text", "tspan") and (element.text or "").strip():
            size = font * scale * unit
            smallest = size if smallest is None else min(smallest, size)
        for child in element:
            walk(child, font, scale)

    walk(root, SVG_DEFAULT_FONT_PX, 1.0)
    return smallest
