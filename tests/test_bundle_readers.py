"""The bundle format readers (jarvis/bundle_readers/): format detection from
content before extension, and each reader's description, paged text and
rendering, on synthetic inputs including malformed, encrypted, long,
Unicode and Windows-made ones. PDF goes through a fake osascript at the
subprocess seam (tests/bundle_fixtures.FakeOsascript); the real one is
tests/test_bundle_readers_mac.py."""
from __future__ import annotations

import codecs
import contextlib
import struct
import zlib
from pathlib import Path
from typing import Optional

import pytest

from jarvis import bundle_archive
from jarvis import bundle_readers as br
from jarvis.bundle_contract import RenderOptions, TextChunk
from jarvis.bundle_readers import _paging, _quartz
from jarvis.bundle_readers.html import PREVIEW_CSP, HtmlReader, parse
from jarvis.bundle_readers.image import ImageReader, dimensions
from jarvis.bundle_readers.office import OfficeReader, ooxml_format
from jarvis.bundle_readers.pdf import PDF_PAGES_PER_CALL, PdfReader
from jarvis.bundle_readers.text import TEXT_MAX_BYTES, TextReader
from tests.bundle_fixtures import (HOSTILE_HTML, FakeOsascript, docx_bytes, gif_header,
                                   jpeg_header, png_bytes, pptx_bytes, review_bundle,
                                   scanned_pdf, text_pdf, two_page_pdf, webp_header,
                                   xlsx_bytes, zip_bytes)


class Source:
    """A MemberSource over bytes in memory; `materialize` writes a private
    copy under `scratch`."""

    def __init__(self, data: bytes, scratch: Optional[Path] = None, path: str = "m"):
        self.data, self.scratch = data, scratch
        self.attachment_sha256, self.member_id, self.path = "a" * 64, "m" + "0" * 24, path
        self.size = len(data)
        self.materialized: list[Path] = []

    def head(self) -> bytes:
        return self.data[:br.SNIFF_BYTES]

    def read(self, limit=None) -> bytes:
        if limit is not None and len(self.data) > limit:
            raise bundle_archive.ArchiveError("too-large", "too large")
        return self.data

    @contextlib.contextmanager
    def materialize(self):
        path = self.scratch / f"copy-{len(self.materialized)}"
        path.write_bytes(self.data)
        path.chmod(0o600)
        self.materialized.append(path)
        try:
            yield path
        finally:
            path.unlink()


def detected(name: str, data: bytes) -> br.Detected:
    return br.detect(name, data[:br.SNIFF_BYTES])


def read_all(reader, source, fmt: str, max_chars: int = 50) -> list[tuple[dict, str]]:
    """Every page in turn, as (location, text) pairs, checking each page
    keeps within max_chars."""
    found, cursor = [], None
    d = br.Detected(fmt, br.MEDIA_TYPES[fmt])
    for _ in range(10_000):
        got = reader.read_text(source, d, cursor, max_chars)
        assert sum(len(t) for _, t in got.chunks) <= max_chars
        found += [(TextChunk(location=loc, text=t).model_dump()["location"], t)
                  for loc, t in got.chunks]
        cursor = got.next_cursor
        if cursor is None:
            return found
    raise AssertionError("never ended")


# -- detection -----------------------------------------------------------------------------

@pytest.mark.parametrize("name,data,fmt", [
    ("summary.pdf", two_page_pdf(), "pdf"),
    ("misnamed.txt", two_page_pdf(), "pdf"),             # content before extension
    ("photo.png", png_bytes(), "png"),
    ("photo.bin", jpeg_header(10, 20), "jpeg"),
    ("a.gif", gif_header(4, 4), "gif"),
    ("a", webp_header(5, 6), "webp"),
    ("bundle.zip", review_bundle(), "zip"),
    ("report.docx", docx_bytes(["x"]), "docx"),
    ("unnamed", docx_bytes(["x"]), "docx"),              # [Content_Types].xml in the head
    ("deck.pptx", pptx_bytes([["x"]]), "pptx"),
    ("book.xlsm", xlsx_bytes({"S": [[1]]}), "xlsx"),
    ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100, "doc"),
    ("mystery.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100, "unknown"),
    ("a.7z", b"7z\xbc\xaf\x27\x1c\x00\x04", "7z"),
    ("a.rar", b"Rar!\x1a\x07\x01\x00", "rar"),
    ("a.tar.gz", b"\x1f\x8b\x08\x00", "gzip"),
    ("a.tar", b"x" * 257 + b"ustar\x0000" + b"\x00" * 50, "tar"),
    ("README.md", b"# Title\n", "markdown"),
    ("data.json", b'{"a": 1}', "json"),
    ("rows.csv", b"a,b\n1,2\n", "csv"),
    ("rows.tsv", b"a\tb\n", "tsv"),
    ("page", b"<!DOCTYPE html><p>x", "html"),
    ("pic", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', "svg"),
    ("notes", b"just words\n", "text"),
    ("empty.txt", b"", "text"),
    ("utf16.txt", codecs.BOM_UTF16_LE + "hi".encode("utf-16-le"), "text"),
    ("song.mp3", b"ID3\x04\x00", "audio"),
    ("clip.mov", b"\x00\x00\x00\x14ftypqt  ", "video"),
    ("photo.heic", b"\x00\x00\x00\x18ftypheic", "heic"),
    ("blob", b"\x00\x01\x02\x03\x04", "unknown"),
    ("letter.rtf", b"{\\rtf1\\ansi hello}", "rtf"),
])
def test_a_format_is_told_from_content_first_then_extension(name, data, fmt):
    assert detected(name, data).format == fmt


def test_split_archive_volumes_are_recognised_by_name():
    for name in ("a.z01", "a.zip.001", "a.part1.rar", "a.r00", "b.007"):
        assert br.multipart(name), name
    assert not br.multipart("report-2026.pdf")


def test_every_format_with_a_reader_reports_its_health():
    health = br.health()
    assert {"pdf", "text", "markdown", "json", "csv", "tsv", "html", "svg", "png", "jpeg",
            "gif", "webp", "docx", "xlsx", "pptx"} <= set(health)
    assert "doc" not in health and "zip" not in health
    assert health["text"].operations == {"read_text"}


def test_pdf_health_is_asked_not_assumed(monkeypatch):
    _quartz.forget_health()
    monkeypatch.setattr(_quartz, "RUNNER", FakeOsascript(crash=True))
    assert br.health()["pdf"].available is False
    _quartz.forget_health()
    monkeypatch.setattr(_quartz, "RUNNER", FakeOsascript())
    assert br.health()["pdf"].available is True
    _quartz.forget_health()


# -- paging ------------------------------------------------------------------------------

def test_a_long_text_is_paged_without_losing_or_repeating_a_character():
    text = "".join(f"line {n} " + "x" * (n % 37) + "\n" for n in range(1, 400))
    pieces = read_all(TextReader(), Source(text.encode()), "text", max_chars=101)
    assert "".join(t for _, t in pieces) == text
    lines = [loc for loc, _ in pieces]
    assert lines[0]["start"] == 1 and lines[-1]["end"] == 399


def test_one_line_longer_than_a_page_is_cut_and_continued_on_the_same_line():
    text = "a" * 250 + "\nend\n"
    pieces = read_all(TextReader(), Source(text.encode()), "text", max_chars=100)
    assert [len(t) for _, t in pieces] == [100, 100, 55]
    assert [(loc["start"], loc["end"]) for loc, _ in pieces] == [(1, 1), (1, 1), (1, 2)]
    assert "".join(t for _, t in pieces) == text


def test_a_cursor_not_made_by_the_reader_is_refused():
    with pytest.raises(ValueError):
        _paging.page(_paging.line_units("a\n"), {"unit": "x"}, 10)
    with pytest.raises(ValueError):
        _paging.page(_paging.line_units("a\n"), {"unit": 5}, 10)
    with pytest.raises(ValueError):
        _paging.page(_paging.line_units("a\n"), {"unit": 0, "col": -1}, 10)


# -- text --------------------------------------------------------------------------------

@pytest.mark.parametrize("data,text", [
    ("café ✓\n".encode("utf-8"), "café ✓\n"),
    (codecs.BOM_UTF8 + "café\n".encode("utf-8"), "café\n"),
    (codecs.BOM_UTF16_LE + "Résumé\r\nnext\r\n".encode("utf-16-le"), "Résumé\nnext\n"),
    (codecs.BOM_UTF16_BE + "naïve\n".encode("utf-16-be"), "naïve\n"),
    ("Windows “quotes” — and café\r\n".encode("cp1252"), "Windows “quotes” — and café\n"),
    (b"old mac\rline two\r", "old mac\nline two\n"),
])
def test_text_is_decoded_from_unicode_or_windows_encodings(data, text):
    pieces = read_all(TextReader(), Source(data), "text", max_chars=1000)
    assert "".join(t for _, t in pieces) == text


def test_markdown_describes_its_lines():
    got = TextReader().describe(Source(b"# T\n\nbody\n"), detected("a.md", b"# T"))
    assert (got.support, got.extent.lines, got.operations) == ("readable", 3, {"read_text"})


def test_malformed_json_is_still_read_with_a_note():
    reader, d = TextReader(), br.Detected("json", "application/json")
    assert reader.read_text(Source(b'{"a": 1}'), d, None, 100).notes == ()
    got = reader.read_text(Source(b'{"a": 1,,'), d, None, 100)
    assert got.notes == ("corrupt",) and got.chunks[0][1] == '{"a": 1,,'


def test_csv_is_paged_by_whole_rows_even_when_a_value_spans_lines():
    data = b'id,note\n1,"first\nsecond"\n2,plain\n3,"a, b"\n'
    pieces = read_all(TextReader(), Source(data), "csv", max_chars=22)
    assert [t for _, t in pieces] == ["id,note\n", '1,"first\nsecond"\n', "2,plain\n3,\"a, b\"\n"]
    assert pieces[1][0] == {"kind": "lines", "start": 2, "end": 3}


def test_tsv_is_paged_by_rows():
    data = b"a\tb\n" + b"".join(b"%d\t%d\n" % (n, n) for n in range(100))
    pieces = read_all(TextReader(), Source(data), "tsv", max_chars=40)
    assert "".join(t for _, t in pieces) == data.decode()
    assert all(t.endswith("\n") for _, t in pieces)


def test_csv_the_csv_module_refuses_is_read_as_lines_with_a_note():
    got = TextReader().read_text(Source(b'a,"b\n'), br.Detected("csv", "text/csv"), None, 100)
    assert got.chunks[0][1] == 'a,"b\n'


def test_a_text_member_too_large_to_read_is_said_never_cut():
    big = Source(b"x" * (TEXT_MAX_BYTES + 1))
    got = TextReader().describe(big, br.Detected("text", "text/plain"))
    assert (got.support, got.reason) == ("unsupported", "too_large")
    with pytest.raises(br.Unsupported) as e:
        TextReader().read_text(big, br.Detected("text", "text/plain"), None, 100)
    assert e.value.reason == "too_large"


def test_text_has_no_rendered_page():
    with pytest.raises(br.Unsupported):
        TextReader().render(Source(b"x"), br.Detected("text", "text/plain"), 1, RenderOptions())


# -- html --------------------------------------------------------------------------------

def test_html_text_leaves_out_scripts_and_styles_and_lists_what_it_would_load():
    text, resources = parse(HOSTILE_HTML)
    assert text == "Report\nFindings\nFirst claim.\na link, not a load\n"
    assert "ran" not in text and "background" not in text
    assert resources == ["https://cdn.example.com/x.css", "https://evil.example.com/x.js",
                         "https://track.example.com/p.png", "//cdn.example.com/a.png"]


def test_html_resources_inside_the_bundle_are_not_blocked():
    _, resources = parse(b'<img src="a.png"><img src="data:image/png;base64,AA">'
                         b'<img srcset="b.png 1x, https://x.example/c.png 2x">'
                         b'<meta http-equiv="refresh" content="0; url=https://x.example/go">'
                         b'<iframe src="javascript:alert(1)"></iframe>')
    assert resources == ["https://x.example/c.png", "https://x.example/go", "javascript:alert(1)"]


def test_html_describe_and_read():
    reader, d = HtmlReader(), br.Detected("html", "text/html")
    got = reader.describe(Source(HOSTILE_HTML), d)
    assert (got.support, got.reason, got.extent.lines) == \
        ("readable", "external_resources_blocked", 4)
    assert reader.describe(Source(b"<p>hi</p>"), d).reason is None
    assert read_all(reader, Source(b"<p>caf&eacute; &amp; tea</p>"), "html") == \
        [({"kind": "lines", "start": 1, "end": 1}, "café & tea\n")]


def test_svg_text_is_read():
    data = (b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script>'
            b'<text x="1">Axis label</text><text>Total</text></svg>')
    assert parse(data)[0] == "Axis label\nTotal\n"


def test_malformed_html_is_still_read():
    assert parse(b"<p>open <b>never closed <div>x</p></span>")[0] == "open never closed\nx\n"


def test_the_preview_csp_runs_no_script_and_loads_nothing_from_outside():
    assert "default-src 'none'" in PREVIEW_CSP and "script-src" not in PREVIEW_CSP
    assert PREVIEW_CSP.endswith("sandbox") and "allow-scripts" not in PREVIEW_CSP


# -- images ------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt,data,size", [
    ("png", png_bytes(31, 17), (31, 17)),
    ("jpeg", jpeg_header(640, 480), (640, 480)),
    ("jpeg", jpeg_header(4000, 3000, exif_bytes=60_000), (4000, 3000)),
    ("gif", gif_header(12, 34), (12, 34)),
    ("webp", webp_header(1920, 1080), (1920, 1080)),
])
def test_image_size_comes_from_the_header(fmt, data, size):
    assert dimensions(fmt, data) == size


@pytest.mark.parametrize("fmt,data", [
    ("png", b"\x89PNG\r\n\x1a\n"), ("jpeg", b"\xff\xd8\xff\xe0\x00"), ("gif", b"GIF89a"),
    ("webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "), ("jpeg", b"\xff\xd8" + b"\x00" * 50),
])
def test_a_truncated_image_header_is_corrupt_not_a_crash(fmt, data):
    assert dimensions(fmt, data) is None
    got = ImageReader().describe(Source(data), br.Detected(fmt, br.MEDIA_TYPES[fmt]))
    assert (got.support, got.reason) == ("unsupported", "corrupt")


def test_an_image_is_its_own_rendered_page_as_sent_and_never_decoded():
    png = png_bytes(5, 4)
    page = ImageReader().render(Source(png), br.Detected("png", "image/png"), 1,
                                RenderOptions(max_edge=256))
    assert page.data == png and (page.info.width, page.info.height) == (5, 4)
    assert page.info.media_type == "image/png"
    jpeg = jpeg_header(5, 3)
    page = ImageReader().render(Source(jpeg), br.Detected("jpeg", "image/jpeg"), 1,
                                RenderOptions())
    assert page.data == jpeg and page.info.media_type == "image/jpeg"
    assert (page.info.width, page.info.height) == (5, 3)
    assert ImageReader().describe(Source(jpeg),
                                  br.Detected("jpeg", "image/jpeg")).operations == {"render"}
    with pytest.raises(br.Unsupported):
        ImageReader().read_text(Source(png), br.Detected("png", "image/png"), None, 100)


# -- office ------------------------------------------------------------------------------

def test_docx_paragraphs_are_its_lines():
    data = docx_bytes(["Title", "", "Café <b> & “quotes”", "Last"])
    reader, d = OfficeReader(), br.Detected("docx", br.MEDIA_TYPES["docx"])
    got = reader.describe(Source(data), d)
    assert (got.support, got.extent.lines, got.reason) == ("readable", 4, None)
    pieces = read_all(reader, Source(data), "docx", max_chars=1000)
    assert pieces == [({"kind": "lines", "start": 1, "end": 4},
                       "Title\n\nCafé <b> & “quotes”\nLast\n")]


def test_a_macro_project_is_never_read_and_is_noted():
    data = docx_bytes(["x"], macros=True)
    got = OfficeReader().read_text(Source(data), br.Detected("docx", ""), None, 100)
    assert got.notes == ("macros_ignored",)


def test_pptx_is_read_slide_by_slide():
    data = pptx_bytes([["Intro", "Why"], ["Numbers"], []])
    reader, d = OfficeReader(), br.Detected("pptx", "")
    assert reader.describe(Source(data), d).extent.slides == 3
    pieces = read_all(reader, Source(data), "pptx", max_chars=1000)
    assert pieces == [({"kind": "slide", "slide": 1}, "Intro\nWhy\n"),
                      ({"kind": "slide", "slide": 2}, "Numbers\n"),
                      ({"kind": "slide", "slide": 3}, "\n")]


def test_xlsx_cells_show_saved_values_and_formulas_as_written():
    data = xlsx_bytes({"Data": [["metric", "value"], ["users", 120], ["double", ("B2*2", 240)]],
                       "Notes": [["café"]]})
    reader, d = OfficeReader(), br.Detected("xlsx", "")
    got = reader.describe(Source(data), d)
    assert got.extent.sheets == ["Data", "Notes"] and got.reason == "formulas_not_evaluated"
    sliced = reader.read_text(Source(data), d, None, 1000)
    assert "formulas_not_evaluated" in sliced.notes
    pieces = [(TextChunk(location=loc, text=t).model_dump()["location"], t)
              for loc, t in sliced.chunks]
    assert pieces == [
        ({"kind": "sheet", "sheet": "Data", "start_row": 1, "end_row": 3},
         "A1=metric\tB1=value\nA2=users\tB2=120\nA3=double\tB3=240 [=B2*2]\n"),
        ({"kind": "sheet", "sheet": "Notes", "start_row": 1, "end_row": 1}, "A1=café\n")]


def test_a_large_sheet_is_paged_by_rows():
    rows = [[f"r{n}", n] for n in range(300)]
    data = xlsx_bytes({"Big": rows})
    pieces = read_all(OfficeReader(), Source(data), "xlsx", max_chars=200)
    assert len(pieces) > 10
    assert pieces[0][0]["start_row"] == 1 and pieces[-1][0]["end_row"] == 300


@pytest.mark.parametrize("data", [
    zip_bytes({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<not xml"}),
    zip_bytes({"word/document.xml": b'<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'}),
    b"PK\x03\x04 truncated",
])
def test_a_malformed_office_file_is_corrupt(data):
    got = OfficeReader().describe(Source(data), br.Detected("docx", ""))
    assert (got.support, got.reason) == ("unsupported", "corrupt")


def test_an_encrypted_office_file_says_so():
    from tests.bundle_fixtures import encrypt_entries
    got = OfficeReader().describe(Source(encrypt_entries(docx_bytes(["x"]))),
                                  br.Detected("docx", ""))
    assert (got.support, got.reason) == ("unsupported", "encrypted")


def test_the_main_part_decides_which_office_document_a_zip_is():
    import io
    import zipfile
    for data, fmt in ((docx_bytes(["x"]), "docx"), (xlsx_bytes({"S": [[1]]}), "xlsx"),
                      (pptx_bytes([["x"]]), "pptx"), (zip_bytes({"a.txt": b"x"}), None)):
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
        types = z.read("[Content_Types].xml") if "[Content_Types].xml" in names else None
        assert ooxml_format(names, types) == fmt


# -- pdf, through the fake osascript --------------------------------------------------------

@pytest.fixture
def fake_osascript(monkeypatch):
    fake = FakeOsascript()
    monkeypatch.setattr(_quartz, "RUNNER", fake)
    _quartz.forget_health()
    yield fake
    _quartz.forget_health()


PDF = br.Detected("pdf", "application/pdf")


def test_pdf_pages_and_text(fake_osascript, tmp_path):
    source = Source(two_page_pdf(), tmp_path)
    got = PdfReader().describe(source, PDF)
    assert (got.support, got.extent.pages, got.operations) == \
        ("readable", 2, {"read_text", "render"})
    pieces = read_all(PdfReader(), source, "pdf", max_chars=1000)
    assert pieces == [({"kind": "page", "page": 1}, "Summary, page one."),
                      ({"kind": "page", "page": 2}, "Evidence, page two.")]
    assert all(p.parent == tmp_path for p in source.materialized)
    assert list(tmp_path.iterdir()) == [], "every private copy is removed"


def test_a_long_pdf_is_read_a_bounded_number_of_pages_per_call(fake_osascript, tmp_path):
    source = Source(text_pdf([f"Page {n} words" for n in range(1, 26)]), tmp_path)
    pieces = read_all(PdfReader(), source, "pdf", max_chars=100_000)
    assert [loc["page"] for loc, _ in pieces] == list(range(1, 26))
    texts = [c for c in fake_osascript.calls if c[5] == "text"]
    assert all(int(c[8]) <= PDF_PAGES_PER_CALL for c in texts) and len(texts) == 3


def test_a_pdf_page_longer_than_a_read_is_continued(fake_osascript, tmp_path):
    source = Source(text_pdf(["x" * 30, "y" * 5]), tmp_path)
    pieces = read_all(PdfReader(), source, "pdf", max_chars=12)
    assert pieces == [({"kind": "page", "page": 1}, "x" * 12), ({"kind": "page", "page": 1},
                      "x" * 12), ({"kind": "page", "page": 1}, "x" * 6),
                      ({"kind": "page", "page": 2}, "y" * 5)]


def test_a_scanned_pdf_says_so(fake_osascript, tmp_path):
    source = Source(scanned_pdf(3), tmp_path)
    got = PdfReader().describe(source, PDF)
    assert (got.support, got.reason, got.operations) == ("partial", "scanned_no_text",
                                                          {"render"})
    assert PdfReader().read_text(source, PDF, None, 100).notes == ("scanned_no_text",)


def test_an_encrypted_or_damaged_pdf_says_why(fake_osascript, tmp_path):
    locked = Source(text_pdf(["x"]).replace(b"/Root 1 0 R", b"/Root 1 0 R /Encrypt 9 0 R"),
                    tmp_path)
    assert PdfReader().describe(locked, PDF).reason == "encrypted"
    with pytest.raises(br.Unsupported) as e:
        PdfReader().read_text(locked, PDF, None, 100)
    assert e.value.reason == "encrypted"
    assert PdfReader().describe(Source(b"not a pdf", tmp_path), PDF).reason == "corrupt"


def test_a_crash_or_a_hang_is_a_failed_page_never_an_exception(monkeypatch, tmp_path):
    for fake in (FakeOsascript(crash=True), FakeOsascript(slow=True)):
        monkeypatch.setattr(_quartz, "RUNNER", fake)
        with pytest.raises(br.Unsupported) as e:
            PdfReader().read_text(Source(two_page_pdf(), tmp_path), PDF, None, 100)
        assert e.value.reason == "page_failed"
        assert list(tmp_path.iterdir()) == []


def test_a_rendered_pdf_page_is_a_png_from_a_raw_bitmap(fake_osascript, tmp_path):
    page = PdfReader().render(Source(two_page_pdf(), tmp_path), PDF, 2,
                              RenderOptions(max_edge=300))
    assert (page.info.page, page.info.page_count, page.info.height) == (2, 2, 300)
    assert page.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert dimensions("png", page.data) == (page.info.width, page.info.height)
    call = fake_osascript.calls[-1]
    assert call[:4] == [_quartz.OSASCRIPT, "-l", "JavaScript", "-e"]
    assert "ImageIO" not in call[4] and "NSBitmapImageRep" not in call[4]
    assert "CGBitmapContextCreate" in call[4]
    assert list(tmp_path.iterdir()) == [], "the raw bitmap and the copy are removed"
    with pytest.raises(br.Unsupported) as e:
        PdfReader().render(Source(two_page_pdf(), tmp_path), PDF, 3, RenderOptions())
    assert e.value.reason == "page_out_of_range"


def test_png_encoding_round_trips_through_zlib():
    rgba = bytes(range(256)) * 3          # 4x48... any 4-byte pixels
    width, height = 8, len(rgba) // 32
    data = _quartz.png(width, height, rgba[:width * height * 4])
    idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
    rows = zlib.decompress(idat)
    assert len(rows) == height * (1 + width * 4)
    assert struct.unpack(">II", data[16:24]) == (width, height)
    with pytest.raises(ValueError):
        _quartz.png(2, 2, b"\x00" * 3)


# -- integration notes ---------------------------------------------------------------------

def test_text_that_is_not_utf8_says_it_was_read_as_windows_1252():
    reader, d = TextReader(), br.Detected("text", "text/plain")
    assert reader.read_text(Source("café\n".encode("utf-8")), d, None, 100).notes == ()
    got = reader.read_text(Source("café\n".encode("cp1252")), d, None, 100)
    assert got.notes == ("legacy_encoding",) and got.chunks[0][1] == "café\n"


def test_a_device_or_pipe_entry_is_a_special_file_not_a_bad_name():
    class Refused:
        reason, kind = "bad-kind", "device"
    assert br.archive_reason(Refused()) == "special_file"
    Refused.kind = "link"
    assert br.archive_reason(Refused()) == "link"
