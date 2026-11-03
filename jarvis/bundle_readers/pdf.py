"""PDF through macOS's own PDFKit and CoreGraphics (`_quartz`): the page
count, whether it is locked, each page's text, and a page drawn as PNG.

Every call is one `osascript` run on a private copy of the member
(MemberSource.materialize), bounded in time (PDF_SECONDS_BASE plus
PDF_SECONDS_PER_PAGE for each page it reads) and in pages
(PDF_PAGES_PER_CALL). A page whose text cannot be read is `page_failed`; a
PDF whose pages have no text at all is `scanned_no_text` (OCR is a
separate operation, not offered); one that needs a password is
`encrypted`. The text is what PDFKit finds; it says nothing of layout,
which only a rendered page shows.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from jarvis.bundle_contract import (Extent, PageLocation, RenderedPage, RenderedPageInfo,
                                    RenderOptions)
from jarvis.bundle_readers import (Description, Detected, MemberSource, ReaderHealth,
                                   TextSlice, Unsupported, _paging, _quartz)

PDF_PAGES_PER_CALL = 10
PDF_SECONDS_BASE = 10.0
PDF_SECONDS_PER_PAGE = 5.0
PDF_RENDER_SECONDS = 30.0
# Pages describe looks at for text before calling a PDF scanned.
PDF_SCAN_PAGES = 20

_SCRIPT = r"""
ObjC.import('Foundation'); ObjC.import('PDFKit'); ObjC.import('CoreGraphics');
function open(path) {
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(path));
  if (doc.isNil()) return {error: 'corrupt'};
  if (doc.isLocked && !doc.unlockWithPassword('')) return {error: 'encrypted'};
  return {doc: doc, pages: Number(doc.pageCount)};
}
function pageText(doc, i) {
  try {
    var page = doc.pageAtIndex(i);
    if (page.isNil()) return null;
    var s = page.string;
    return s.isNil() ? '' : s.js;
  } catch (e) { return null; }
}
function run(argv) {
  var mode = argv[0], got = open(argv[1]);
  if (got.error) return JSON.stringify({error: got.error});
  var doc = got.doc, out = {pages: got.pages};
  if (mode === 'text') {
    var start = Number(argv[2]), count = Number(argv[3]);
    out.texts = [];
    for (var i = start; i < Math.min(got.pages, start + count); i++)
      out.texts.push(pageText(doc, i));
  } else if (mode === 'render') {
    var index = Number(argv[2]), edge = Number(argv[3]);
    if (index < 0 || index >= got.pages) return JSON.stringify({error: 'page_out_of_range'});
    var page = doc.pageAtIndex(index);
    var box = page.boundsForBox($.kPDFDisplayBoxCropBox);
    var rotate = Number(page.rotation) % 180 !== 0;
    var pw = rotate ? box.size.height : box.size.width;
    var ph = rotate ? box.size.width : box.size.height;
    if (!(pw > 0 && ph > 0)) return JSON.stringify({error: 'page_failed'});
    var scale = edge / Math.max(pw, ph);
    var w = Math.max(1, Math.round(pw * scale)), h = Math.max(1, Math.round(ph * scale));
    var ctx = $.CGBitmapContextCreate(null, w, h, 8, w * 4, $.CGColorSpaceCreateDeviceRGB(), 1);
    $.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1);
    $.CGContextFillRect(ctx, $.CGRectMake(0, 0, w, h));
    $.CGContextScaleCTM(ctx, w / pw, h / ph);
    page.drawWithBoxToContext($.kPDFDisplayBoxCropBox, ctx);
    var data = $.NSData.dataWithBytesLength($.CGBitmapContextGetData(ctx), w * h * 4);
    if (!data.writeToFileAtomically(argv[4], false)) return JSON.stringify({error: 'page_failed'});
    out.width = w; out.height = h;
  }
  return JSON.stringify(out);
}
"""


def _call(path: Path, args: list[str], timeout: float) -> dict:
    """One osascript run on the copy at `path`; its answer, or Unsupported."""
    try:
        answer = _quartz.run(_SCRIPT, [args[0], str(path), *args[1:]], timeout)
    except _quartz.QuartzFailed as e:
        raise Unsupported("reader_unavailable" if e.reason == "unavailable"
                          else "page_failed") from None
    error = answer.get("error")
    if error:
        raise Unsupported(error if error in ("corrupt", "encrypted", "page_out_of_range")
                          else "page_failed")
    pages = answer.get("pages")
    if not isinstance(pages, int) or isinstance(pages, bool) or pages < 0:
        raise Unsupported("page_failed")
    return answer


def _texts(source: MemberSource, start: int, count: int) -> tuple[int, list[Optional[str]]]:
    count = max(1, min(count, PDF_PAGES_PER_CALL))
    with source.materialize() as path:
        answer = _call(path, ["text", str(start), str(count)],
                       PDF_SECONDS_BASE + PDF_SECONDS_PER_PAGE * count)
    texts = answer.get("texts")
    if not isinstance(texts, list):
        raise Unsupported("page_failed")
    return answer["pages"], [t if isinstance(t, str) or t is None else None for t in texts]


class PdfReader:
    name = "pdf"
    version = 1
    formats = frozenset({"pdf"})

    def health(self) -> ReaderHealth:
        if _quartz.healthy():
            return ReaderHealth(True, frozenset({"read_text", "render"}))
        return ReaderHealth(False, reason="reader_unavailable")

    def describe(self, source: MemberSource, detected: Detected) -> Description:
        try:
            pages, texts = _texts(source, 0, min(PDF_SCAN_PAGES, PDF_PAGES_PER_CALL))
            looked = len(texts)
            while not any(t and t.strip() for t in texts) and looked < min(pages, PDF_SCAN_PAGES):
                _, more = _texts(source, looked, PDF_PAGES_PER_CALL)
                if not more:
                    break
                texts += more
                looked += len(more)
        except Unsupported as e:
            return Description("unsupported", Extent(), e.reason)
        extent = Extent(pages=pages)
        if pages and not any(t and t.strip() for t in texts):
            return Description("partial", extent, "scanned_no_text", frozenset({"render"}))
        if any(t is None for t in texts):
            return Description("partial", extent, "page_failed",
                               frozenset({"read_text", "render"}))
        return Description("readable", extent, operations=frozenset({"read_text", "render"}))

    def read_text(self, source: MemberSource, detected: Detected, cursor: Optional[dict],
                  max_chars: int) -> TextSlice:
        """From page `page` (0-based) character `col` on, whole pages while
        they fit, a page longer than a whole read cut and continued."""
        index, col = _paging.cursor_int(cursor, "page"), _paging.cursor_int(cursor, "col")
        pages, texts = _texts(source, index, PDF_PAGES_PER_CALL)
        if index > pages:
            raise ValueError("cursor past the end")
        units = [_paging.Unit(index + n + 1, index + n + 1, index + n + 1, text or "")
                 for n, text in enumerate(texts)]
        notes = tuple(dict.fromkeys(
            "page_failed" if text is None else "scanned_no_text"
            for text in texts if text is None or not text.strip()))
        chunks, after = _paging.page(units, {"unit": 0, "col": col}, max_chars,
                                     lambda page, start, end: PageLocation(page=page))
        if after is not None:
            return TextSlice(chunks, {"page": index + after["unit"], "col": after["col"]}, notes)
        following = index + len(texts)
        return TextSlice(chunks, {"page": following, "col": 0} if following < pages else None,
                         notes)

    def render(self, source: MemberSource, detected: Detected, page: int,
               options: RenderOptions) -> RenderedPage:
        with source.materialize() as path:
            raw = path.with_name(path.name + ".rgba")
            try:
                answer = _call(path, ["render", str(page - 1), str(options.max_edge), str(raw)],
                               PDF_RENDER_SECONDS)
                width, height = answer.get("width"), answer.get("height")
                if not isinstance(width, int) or not isinstance(height, int):
                    raise Unsupported("page_failed")
                try:
                    png = _quartz.png(width, height, raw.read_bytes())
                except (OSError, ValueError):
                    raise Unsupported("page_failed") from None
            finally:
                raw.unlink(missing_ok=True)
        return RenderedPage(RenderedPageInfo(member_id=source.member_id, page=page,
                                             page_count=answer["pages"], width=width,
                                             height=height), png)
