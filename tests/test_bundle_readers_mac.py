"""The PDF reader against macOS's real PDFKit and CoreGraphics, through the
real osascript: a two-page PDF's text and a rendered page, a scanned one,
and one PDFKit itself locks with a password. Marked `osascript`; skipped
off macOS."""
from __future__ import annotations

import contextlib
import subprocess
import sys
import zlib

import pytest

from jarvis import bundle_readers as br
from jarvis.bundle_contract import RenderOptions
from jarvis.bundle_readers import _quartz
from jarvis.bundle_readers.image import dimensions
from jarvis.bundle_readers.pdf import PdfReader
from tests.bundle_fixtures import scanned_pdf, two_page_pdf

pytestmark = [pytest.mark.osascript,
              pytest.mark.skipif(not _quartz.available_here(), reason="needs macOS osascript")]

PDF = br.Detected("pdf", "application/pdf")


class Source:
    def __init__(self, data: bytes, scratch):
        self.data, self.scratch = data, scratch
        self.attachment_sha256, self.member_id, self.path = "a" * 64, "m" + "0" * 24, ""
        self.size = len(data)

    def read(self, limit=None):
        return self.data

    def head(self):
        return self.data[:br.SNIFF_BYTES]

    @contextlib.contextmanager
    def materialize(self):
        path = self.scratch / "copy.pdf"
        path.write_bytes(self.data)
        path.chmod(0o600)
        try:
            yield path
        finally:
            path.unlink()


@pytest.fixture(autouse=True)
def real_osascript(monkeypatch):
    monkeypatch.setattr(_quartz, "RUNNER", subprocess.run)
    _quartz.forget_health()
    yield
    _quartz.forget_health()


def _locked_pdf(tmp_path) -> bytes:
    """A PDF PDFKit itself writes with a user password."""
    plain, locked = tmp_path / "plain.pdf", tmp_path / "locked.pdf"
    plain.write_bytes(two_page_pdf())
    script = """
ObjC.import('Foundation'); ObjC.import('PDFKit');
function run(argv) {
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  var opts = $.NSMutableDictionary.dictionary;
  opts.setObjectForKey($('secret'), $.PDFDocumentUserPasswordOption);
  opts.setObjectForKey($('owner'), $.PDFDocumentOwnerPasswordOption);
  return doc.writeToFileWithOptions(argv[1], opts) ? 'ok' : 'no';
}"""
    done = subprocess.run(["osascript", "-l", "JavaScript", "-e", script, str(plain),
                           str(locked)], capture_output=True, timeout=30)
    if done.stdout.strip() != b"ok":
        pytest.skip("PDFKit would not write a locked PDF here")
    return locked.read_bytes()


def test_the_real_pdfkit_reads_and_renders_a_two_page_pdf(tmp_path):
    assert PdfReader().health().available
    source = Source(two_page_pdf(), tmp_path)
    described = PdfReader().describe(source, PDF)
    assert (described.support, described.extent.pages) == ("readable", 2)
    got = PdfReader().read_text(source, PDF, None, 10_000)
    assert [(loc.page, text.strip()) for loc, text in got.chunks] == [
        (1, "Summary, page one."), (2, "Evidence, page two.")]
    page = PdfReader().render(source, PDF, 1, RenderOptions(max_edge=400))
    assert dimensions("png", page.png) == (page.info.width, page.info.height) == (309, 400)
    idat = page.png[page.png.index(b"IDAT") + 4:page.png.index(b"IEND") - 8]
    rows = zlib.decompress(idat)
    stride = 1 + page.info.width * 4
    dark = sum(1 for y in range(page.info.height) for x in range(1, stride, 4)
               if rows[y * stride + x] < 128)
    assert dark > 0, "the page's text is drawn"
    top_half = sum(1 for y in range(page.info.height // 2) for x in range(1, stride, 4)
                   if rows[y * stride + x] < 128)
    assert top_half == dark, "the text near the page's top is at the image's top"
    assert list(tmp_path.iterdir()) == []


def test_the_real_pdfkit_finds_a_scanned_pdf_has_no_text(tmp_path):
    got = PdfReader().describe(Source(scanned_pdf(2), tmp_path), PDF)
    assert (got.support, got.reason) == ("partial", "scanned_no_text")


def test_the_real_pdfkit_says_a_locked_pdf_is_encrypted(tmp_path):
    data = _locked_pdf(tmp_path)
    got = PdfReader().describe(Source(data, tmp_path), PDF)
    assert (got.support, got.reason) == ("unsupported", "encrypted")


def test_a_damaged_pdf_is_corrupt_with_the_real_pdfkit(tmp_path):
    got = PdfReader().describe(Source(b"%PDF-1.4\nnot really\n", tmp_path), PDF)
    assert got.support == "unsupported" and got.reason in ("corrupt", "page_failed")
    assert sys.platform == "darwin"
