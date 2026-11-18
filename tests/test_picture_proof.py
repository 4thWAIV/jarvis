"""picture_proof: every picture proven real and readable without a browser."""
from __future__ import annotations

import struct
import zlib

import pytest

from jarvis import layout_spec, picture_proof
from jarvis.picture_proof import PictureProblem, prove_bytes
from tests import picture_fixtures as pf


@pytest.mark.parametrize("make,kind", [(pf.png, "png"), (pf.gif, "gif"), (pf.jpeg, "jpeg"),
                                       (pf.webp, "webp"), (pf.svg, "svg")])
def test_each_kind_is_proven_with_its_size(make, kind):
    picture = prove_bytes(make(240, 160))
    assert (picture.kind, picture.width, picture.height) == (kind, 240, 160)


def _png_chunks(raw):
    at, out = 8, []
    while at < len(raw):
        (n,) = struct.unpack(">I", raw[at:at + 4])
        out.append((raw[at + 4:at + 8], raw[at + 8:at + 8 + n]))
        at += 12 + n
    return out


def _png_of(chunks):
    return b"\x89PNG\r\n\x1a\n" + b"".join(pf._chunk(k, d) for k, d in chunks)


def _truncated_png():
    return pf.png()[:-30]


def _bad_crc():
    raw = bytearray(pf.png())
    raw[-5] ^= 0xFF        # IEND's CRC
    return bytes(raw)


def _idat_short():
    chunks = _png_chunks(pf.png())
    rows = zlib.decompress(chunks[1][1])[:-1]
    chunks[1] = (b"IDAT", zlib.compress(rows))
    return _png_of(chunks)


def _gif_no_trailer():
    return pf.gif()[:-1]


def _jpeg_no_eoi():
    return pf.jpeg()[:-2]


def _webp_wrong_length():
    raw = bytearray(pf.webp())
    raw[4:8] = struct.pack("<I", len(raw))
    return bytes(raw)


@pytest.mark.parametrize("raw,words", [
    (_truncated_png(), "cut short"),
    (_bad_crc(), "CRC"),
    (_idat_short(), "inflates to"),
    (_gif_no_trailer(), "trailer"),
    (_jpeg_no_eoi(), "EOI"),
    (_webp_wrong_length(), "says it is"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"/>', "width and height in pixels"),
], ids=["png-truncated", "png-crc", "png-idat-short", "gif-trailer", "jpeg-eoi", "webp-riff",
        "svg-no-size"])
def test_a_broken_picture_is_refused_saying_why(raw, words):
    with pytest.raises(PictureProblem, match=words):
        prove_bytes(raw)


def test_a_label_too_small_where_it_is_drawn_is_refused():
    picture = prove_bytes(pf.svg(240, 160, label_px=9))
    box = layout_spec.picture_box("content")
    assert "smallest label" in picture_proof.drawn_problem(picture, (240, 160))
    assert picture_proof.drawn_problem(prove_bytes(pf.svg(240, 160, label_px=24)), box) == ""


def test_a_picture_drawn_under_the_floor_is_refused():
    banner = prove_bytes(pf.png(1200, 60))
    assert "under 60 px" in picture_proof.drawn_problem(banner, layout_spec.picture_box("option", 4))


def test_a_small_picture_is_never_stretched_past_twice_its_size():
    assert picture_proof.drawn(prove_bytes(pf.png(100, 50)), (1000, 1000)) == (200, 100)


def test_a_declared_type_must_be_the_bytes():
    with pytest.raises(PictureProblem, match="not the declared image/jpeg"):
        prove_bytes(pf.png(), "image/jpeg")


def test_a_rotated_label_cannot_be_proven():
    raw = pf.svg(extra='<g transform="rotate(30)"><text font-size="20">x</text></g>')
    with pytest.raises(PictureProblem, match="rotated"):
        prove_bytes(raw)
    assert prove_bytes(pf.svg(extra='<g transform="rotate(30)"><rect width="2" height="2"/></g>'))
