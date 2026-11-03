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
