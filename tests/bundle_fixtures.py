"""Synthetic review bundles for tests, made in code: never real review
material. `review_bundle` is shaped like the zip whose wrapper folder once
stopped a teammate's JARVIS from opening it: one folder holding a two-page
PDF summary, its HTML version, a review brief, a skill, package.json and
some evidence files."""
from __future__ import annotations

import io
import json
import zipfile
from typing import Optional

WRAPPER = "example-summary-review"
REVIEW_MANIFEST = {"name": "example-summary-review", "version": "1.0.0",
                   "purpose": "Review a two-page summary against its evidence and send notes.",
                   "skill": "SKILL.md", "returns": ["results/*.md"]}


def two_page_pdf(lines: tuple[str, str] = ("Summary, page one.", "Evidence, page two.")) -> bytes:
    """A valid two-page PDF with one line of text on each page, its cross
    reference table worked out from the bytes it writes."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>",
               "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>"]
    pages, streams = [], []
    for n, line in enumerate(lines):
        content = f"BT /F1 18 Tf 72 720 Td ({line}) Tj ET"
        pages.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                     f"/Resources << /Font << /F1 7 0 R >> >> /Contents {5 + n} 0 R >>")
        streams.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objects += pages + streams + ["<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def review_files(manifest: Optional[dict] = REVIEW_MANIFEST) -> dict[str, bytes]:
    """The bundle's files by path inside its folder."""
    files = {
        "summary.pdf": two_page_pdf(),
        "summary.html": b"<!doctype html><title>Summary</title><h1>Summary</h1><p>Claims.</p>",
        "REVIEW-BRIEF.md": b"# Review brief\n\nCheck each claim against evidence/. Notes only.\n",
        "SKILL.md": b"# Review\n\nRead summary.pdf, then the evidence, then write notes.\n",
        "evidence/README.md": b"# Evidence\n\nSources for the summary's claims.\n",
        "evidence/figures.csv": b"metric,value\nusers,120\n",
        "evidence/notes/interview.txt": b"Interview notes, synthetic.\n",
    }
    if manifest is not None:
        files["package.json"] = json.dumps(manifest, indent=2).encode()
    return files


def zip_bytes(files: dict[str, bytes], folder: str = "", directories: bool = True,
              extra: Optional[dict[str, bytes]] = None) -> bytes:
    """A zip of `files`, under `folder/` when given (with that folder's own
    entry, as Finder's Compress writes it), plus `extra` entries as named."""
    out = io.BytesIO()
    prefix = f"{folder}/" if folder else ""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if folder and directories:
            z.writestr(zipfile.ZipInfo(prefix), b"")
        for name, data in files.items():
            info = zipfile.ZipInfo(prefix + name)
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return out.getvalue()


def review_bundle(wrapper: bool = True, manifest: Optional[dict] = REVIEW_MANIFEST,
                  mac_metadata: bool = False) -> bytes:
    """The synthetic review bundle: in one wrapper folder by default, with
    macOS's __MACOSX and .DS_Store beside it when `mac_metadata`."""
    extra = {}
    if mac_metadata:
        extra = {f"__MACOSX/{WRAPPER}/._summary.pdf": b"\x00\x05\x16\x07",
                 f"{WRAPPER}/.DS_Store": b"\x00\x00\x00\x01Bud1"}
    return zip_bytes(review_files(manifest), WRAPPER if wrapper else "", extra=extra)


# -- entries exactly as given ------------------------------------------------------------

FILE_MODE = 0o100644


def raw_zip(entries: list[tuple[str, bytes, int]], create_system: int = 3) -> bytes:
    """A zip whose entries are exactly as given: (name, data, unix mode).
    `create_system` 0 is what Windows writes (MS-DOS attributes, no mode)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = create_system
            info.external_attr = (mode << 16) if create_system == 3 else 0x20
            z.writestr(info, data)
    return out.getvalue()


def encrypt_entries(data: bytes, count: Optional[int] = None) -> bytes:
    """`data` with the encrypted bit set on its first `count` entries (all by
    default) in both the local and the central headers."""
    out = bytearray(data)
    for signature, flags_at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at, done = 0, 0
        while (at := out.find(signature, at)) != -1 and (count is None or done < count):
            out[at + flags_at] |= 0x1
            at += 4
            done += 1
    return bytes(out)


def windows_zip(files: dict[str, bytes]) -> bytes:
    """A zip as Windows' Compress writes it: MS-DOS attributes and names in
    code page 437 without the UTF-8 flag. Written with same-length ASCII
    stand-in names, which are then swapped for the cp437 bytes."""
    out = io.BytesIO()
    swaps = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n, (name, data) in enumerate(files.items()):
            encoded = name.encode("cp437")
            stand_in = f"~{n:03d}".encode() + b"_" * (len(encoded) - 4)
            swaps.append((stand_in, encoded))
            info = zipfile.ZipInfo(stand_in.decode())
            info.create_system = 0
            info.external_attr = 0x20
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    raw = out.getvalue()
    for stand_in, encoded in swaps:
        raw = raw.replace(stand_in, encoded)
    return raw


# -- documents -------------------------------------------------------------------------------

def pdf_pages(contents: list[str], extra_trailer: str = "") -> bytes:
    """A PDF whose pages carry the given content streams, cross references
    worked out from the bytes it writes."""
    n = len(contents)
    kids = " ".join(f"{3 + i} 0 R" for i in range(n))
    objects = ["<< /Type /Catalog /Pages 2 0 R >>",
               f"<< /Type /Pages /Kids [{kids}] /Count {n} >>"]
    font = 3 + 2 * n
    objects += [f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {3 + n + i} 0 R >>"
                for i in range(n)]
    objects += [f"<< /Length {len(c)} >>\nstream\n{c}\nendstream" for c in contents]
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R {extra_trailer}>>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def text_pdf(lines: list[str]) -> bytes:
    return pdf_pages([f"BT /F1 18 Tf 72 720 Td ({line}) Tj ET" for line in lines])


def scanned_pdf(pages: int = 2) -> bytes:
    """Pages that are pictures only: a filled box each, no text."""
    return pdf_pages(["0.5 g 72 72 468 648 re f"] * pages)


def _xml_part(body: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body).encode()


_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"


def _ooxml(main: str, main_type: str, parts: dict[str, bytes], macros: bool) -> bytes:
    files = {
        "[Content_Types].xml": _xml_part(
            f'<Types xmlns="{_CT_NS}"><Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/{main}" ContentType="{main_type}"/></Types>'),
        "_rels/.rels": _xml_part(
            f'<Relationships xmlns="{_REL_NS}"><Relationship Id="rId1" Type="{_DOC_REL}" '
            f'Target="{main}"/></Relationships>'),
        **parts,
    }
    if macros:
        files[main.split("/")[0] + "/vbaProject.bin"] = b"\xd0\xcf\x11\xe0 macros never read"
    return zip_bytes(files)


def _rels(targets: dict[str, str]) -> bytes:
    return _xml_part(f'<Relationships xmlns="{_REL_NS}">' + "".join(
        f'<Relationship Id="{rid}" Type="x" Target="{t}"/>' for rid, t in targets.items())
        + "</Relationships>")


def docx_bytes(paragraphs: list[str], macros: bool = False) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{_esc(p)}</w:t></w:r></w:p>"
                   for p in paragraphs)
    return _ooxml("word/document.xml",
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                  {"word/document.xml": _xml_part(f'<w:document xmlns:w="{w}"><w:body>{body}'
                                                  f'</w:body></w:document>')}, macros)


def pptx_bytes(slides: list[list[str]], macros: bool = False) -> bytes:
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i + 1}"/>' for i in range(len(slides)))
    parts = {"ppt/presentation.xml": _xml_part(
        f'<p:presentation xmlns:p="{p}" xmlns:r="{_R_NS}"><p:sldIdLst>{ids}</p:sldIdLst>'
        f'</p:presentation>'),
        "ppt/_rels/presentation.xml.rels": _rels(
            {f"rId{i + 1}": f"slides/slide{i + 1}.xml" for i in range(len(slides))})}
    for i, paras in enumerate(slides):
        text = "".join(f"<a:p><a:r><a:t>{_esc(t)}</a:t></a:r></a:p>" for t in paras)
        parts[f"ppt/slides/slide{i + 1}.xml"] = _xml_part(
            f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree><p:sp><p:txBody>{text}'
            f'</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    return _ooxml("ppt/presentation.xml",
                  "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
                  parts, macros)


def xlsx_bytes(sheets: dict[str, list[list]], macros: bool = False) -> bytes:
    """Sheets of rows; a cell is a number, a string (shared) or ("=F", saved)."""
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    strings: list[str] = []
    parts = {}
    entries, targets = [], {}
    for n, (name, rows) in enumerate(sheets.items(), start=1):
        entries.append(f'<sheet name="{_esc(name)}" sheetId="{n}" r:id="rId{n}"/>')
        targets[f"rId{n}"] = f"worksheets/sheet{n}.xml"
        xml_rows = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, value in enumerate(row):
                ref = f"{chr(65 + c)}{r}"
                if isinstance(value, tuple):
                    cells.append(f'<c r="{ref}"><f>{_esc(value[0])}</f><v>{value[1]}</v></c>')
                elif isinstance(value, str):
                    strings.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
                elif value is not None:
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        parts[f"xl/worksheets/sheet{n}.xml"] = _xml_part(
            f'<worksheet xmlns="{s}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    targets["rIdS"] = "sharedStrings.xml"
    parts["xl/workbook.xml"] = _xml_part(
        f'<workbook xmlns="{s}" xmlns:r="{_R_NS}"><sheets>{"".join(entries)}</sheets></workbook>')
    parts["xl/_rels/workbook.xml.rels"] = _rels(targets)
    parts["xl/sharedStrings.xml"] = _xml_part(
        f'<sst xmlns="{s}">' + "".join(f"<si><t>{_esc(t)}</t></si>" for t in strings) + "</sst>")
    return _ooxml("xl/workbook.xml",
                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                  parts, macros)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(
        '"', "&quot;")


# -- images: headers enough to size them; never decoded ----------------------------------------

def png_bytes(width: int = 3, height: int = 2) -> bytes:
    """A real PNG, all white."""
    from jarvis.bundle_readers._quartz import png
    return png(width, height, b"\xff" * (width * height * 4))


def jpeg_header(width: int, height: int, exif_bytes: int = 0) -> bytes:
    """SOI, an APP1 of `exif_bytes` filler, then a baseline SOF0."""
    out = b"\xff\xd8"
    if exif_bytes:
        out += b"\xff\xe1" + (exif_bytes + 2).to_bytes(2, "big") + b"\x00" * exif_bytes
    return out + b"\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") \
        + b"\x03" + b"\x00" * 9 + b"\xff\xd9"


def gif_header(width: int, height: int) -> bytes:
    return b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00" * 8


def webp_header(width: int, height: int) -> bytes:
    """An extended (VP8X) WebP header."""
    body = b"VP8X" + (10).to_bytes(4, "little") + b"\x00" * 4 + \
        (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


HOSTILE_HTML = (b'<!doctype html><html><head><title>Report</title>'
                b'<link rel="stylesheet" href="style.css">'
                b'<link rel="stylesheet" href="https://cdn.example.com/x.css">'
                b'<script src="https://evil.example.com/x.js"></script>'
                b'<script>document.title = "ran"</script>'
                b'<style>body { background: url(https://track.example.com/p.png) }</style>'
                b'</head><body><h1>Findings</h1><p>First claim.</p>'
                b'<img src="img/chart.png"><img src="//cdn.example.com/a.png">'
                b'<a href="https://example.com/read">a link, not a load</a></body></html>')


# -- the subprocess seam: osascript, faked --------------------------------------------------------

import re as _re  # noqa: E402
import subprocess as _subprocess  # noqa: E402


class FakeOsascript:
    """Stands in for `osascript -l JavaScript` running the PDF reader's
    script: reads the synthetic PDFs above for their pages and their text
    (`(...) Tj` in each page's stream), answers `encrypted` for a PDF with
    an /Encrypt entry, and draws a render as a white RGBA bitmap. `calls`
    records every argv; `crash` makes it exit as a SIGSEGV would."""

    def __init__(self, crash: bool = False, slow: bool = False):
        self.calls: list[list[str]] = []
        self.crash, self.slow = crash, slow

    def __call__(self, argv, capture_output=True, timeout=None, check=False):
        self.calls.append(list(argv))
        if self.slow:
            raise _subprocess.TimeoutExpired(argv, timeout)
        if self.crash:
            return _subprocess.CompletedProcess(argv, -11, b"", b"")
        args = argv[5:]
        if not args:
            return self._done(argv, {"pdfkit": True, "bitmap": True})
        mode, path = args[0], args[1]
        data = open(path, "rb").read()
        if not data.startswith(b"%PDF-"):
            return self._done(argv, {"error": "corrupt"})
        if b"/Encrypt" in data:
            return self._done(argv, {"error": "encrypted"})
        streams = _re.findall(rb"stream\n(.*?)\nendstream", data, _re.S)
        pages = len(streams)
        if mode == "text":
            start, count = int(args[2]), int(args[3])
            texts = [" ".join(t.decode("latin-1") for t in _re.findall(rb"\((.*?)\) Tj", s))
                     for s in streams[start:start + count]]
            return self._done(argv, {"pages": pages, "texts": texts})
        index, edge, out = int(args[2]), int(args[3]), args[4]
        if index >= pages:
            return self._done(argv, {"error": "page_out_of_range"})
        width, height = round(612 * edge / 792), edge
        with open(out, "wb") as f:
            f.write(b"\xff" * (width * height * 4))
        return self._done(argv, {"pages": pages, "width": width, "height": height})

    @staticmethod
    def _done(argv, answer: dict):
        return _subprocess.CompletedProcess(argv, 0, json.dumps(answer).encode(), b"")
