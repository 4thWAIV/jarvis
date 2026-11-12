"""content_sections: a file's bytes made into extracted text and sections
through JARVIS's own readers, cut to Tim's section-quality bar. PDF goes
through the faked osascript (tests/bundle_fixtures.FakeOsascript)."""
import re

import pytest

from jarvis import content_sections as cs
from jarvis.bundle_readers import _quartz
from tests.bundle_fixtures import (FakeOsascript, docx_bytes, pptx_bytes, scanned_pdf,
                                   text_pdf, xlsx_bytes)


@pytest.fixture
def osascript(monkeypatch):
    fake = FakeOsascript()
    monkeypatch.setattr(_quartz, "RUNNER", fake)
    _quartz.forget_health()
    yield fake
    _quartz.forget_health()


def _extract(data, name, scope="text") -> cs.Extracted:
    found = cs.extract(data, name, scope)
    assert isinstance(found, cs.Extracted), found
    return found


def _words(text: str) -> list[str]:
    return text.split()


# -- scope ---------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["a.md", "b.txt", "c.pdf", "d.docx", "e.xlsx", "f.pptx",
                                  "g.html", "h.htm", "README.markdown"])
def test_documents_are_in_docs_and_text_scope(name):
    assert cs.in_scope(name, "docs") and cs.in_scope(name, "text")
    assert not cs.in_scope(name, "off")


@pytest.mark.parametrize("name", ["data.csv", "data.json", "conf.yaml", "main.py",
                                  "Makefile", "app.ts", "notes.log"])
def test_other_text_is_in_text_scope_only(name):
    assert cs.in_scope(name, "text")
    assert not cs.in_scope(name, "docs")
    assert not cs.in_scope(name, "off")


@pytest.mark.parametrize("name", ["photo.png", "song.mp3", "bundle.zip", "lib.dylib",
                                  "font.woff2", "store.sqlite"])
def test_binary_names_are_in_no_scope(name):
    assert not any(cs.in_scope(name, scope) for scope in cs.SCOPES)


def test_an_unknown_scope_is_refused():
    with pytest.raises(ValueError):
        cs.in_scope("a.md", "everything")


def test_csv_and_json_are_read_in_text_scope_but_not_docs():
    assert cs.extract(b"a,b\n1,2\n", "t.csv", "docs") == cs.Unreadable("out_of_scope")
    assert cs.extract(b'{"a": 1}', "t.json", "docs") == cs.Unreadable("out_of_scope")
    assert _extract(b"a,b\n1,2\n", "t.csv").format == "csv"
    assert _extract(b'{"a": 1}', "t.json").format == "json"
    assert cs.extract(b"# A\n\nb\n", "a.md", "off") == cs.Unreadable("out_of_scope")


# -- Markdown --------------------------------------------------------------------------------

MARKDOWN = b"""Before any heading.

# Handbook

Opening words.

## Setup

Install it.

### On a Mac

Use the installer.

## Use

Run it.

# Appendix

Extra.
"""


def test_markdown_sections_keep_their_heading_paths():
    found = _extract(MARKDOWN, "handbook.md", "docs")
    assert found.format == "markdown" and found.title == "Handbook"
    got = [(s.heading, s.heading_path, s.text) for s in found.sections]
    assert got == [
        ("Handbook", (), "Before any heading."),
        ("Handbook", ("Handbook",), "Opening words."),
        ("Setup", ("Handbook", "Setup"), "Install it."),
        ("On a Mac", ("Handbook", "Setup", "On a Mac"), "Use the installer."),
        ("Use", ("Handbook", "Use"), "Run it."),
        ("Appendix", ("Appendix",), "Extra."),
    ]
    assert [s.index for s in found.sections] == list(range(6))
    assert found.headings == ("Handbook", "Setup", "On a Mac", "Use", "Appendix")


def test_setext_headings_and_a_heading_with_no_text_of_its_own():
    text = b"Title\n=====\n\n## Empty\n\n### Child\n\nBody.\n\nPart\n----\n\nMore.\n"
    found = _extract(text, "x.md")
    assert found.title == "Title"
    got = [(s.heading_path, s.text) for s in found.sections]
    assert got == [(("Title", "Empty", "Child"), "Body."), (("Title", "Part"), "More.")]
    assert all(s.text for s in found.sections)


def test_the_title_falls_back_to_front_matter_then_the_file_name():
    assert _extract(b"---\ntitle: 'Plans'\n---\n\n## Q1\n\nx\n", "p.md").title == "Plans"
    assert _extract(b"## Only a sub\n\nx\n", "notes-2026.md").title == "notes-2026"


def test_a_hash_without_a_space_is_not_a_heading():
    found = _extract(b"#hashtag here\n\nmore\n", "t.md")
    assert found.headings == ()


def _long_prose(sentences: int) -> str:
    return " ".join(f"Sentence number {n} talks about the extraordinarily careful "
                    f"engineering of sections." for n in range(sentences))


def test_a_long_section_is_split_on_sentences_never_mid_word():
    body = _long_prose(120)
    found = _extract(f"# Long\n\n{body}\n".encode(), "long.md")
    assert len(found.sections) > 2
    for section in found.sections:
        assert 0 < len(section.text) <= cs.SECTION_MAX
        assert section.heading_path == ("Long",)
        assert section.text.endswith("sections."), "every cut lands on a sentence end"
    joined = " ".join(s.text for s in found.sections)
    assert _words(joined) == _words(body), "no word lost, none cut"


def test_a_sentence_longer_than_a_section_is_cut_at_words():
    words = [f"word{n}" for n in range(600)]
    body = " ".join(words)
    found = _extract(f"# W\n\n{body}\n".encode(), "w.md")
    assert len(found.sections) > 1
    assert _words(" ".join(s.text for s in found.sections)) == words
    for section in found.sections:
        assert len(section.text) <= cs.SECTION_MAX
        assert re.fullmatch(r"word\d+( word\d+)*", section.text)


def test_paragraphs_are_kept_together_and_split_between():
    paras = [f"Paragraph {n}. " + "filler words here " * 30 for n in range(10)]
    found = _extract(("\n\n".join(paras)).encode(), "plain.txt", "docs")
    for section in found.sections:
        for piece in section.text.split("\n\n"):
            assert piece.startswith("Paragraph"), "a cut falls between paragraphs"
    assert _words(" ".join(s.text for s in found.sections)) == _words(" ".join(paras))


def test_code_fences_and_tables_stay_whole():
    code = "```python\n" + "\n".join(f"x{n} = {n}" for n in range(40)) + "\n```"
    table = "| name | value |\n|---|---|\n" + "\n".join(f"| k{n} | v{n} |" for n in range(30))
    filler = _long_prose(8)
    found = _extract(f"# Code\n\n{filler}\n\n{code}\n\n{table}\n\n{filler}\n".encode(), "c.md")
    texts = [s.text for s in found.sections]
    assert any(code in t for t in texts), "the fence is one section's whole"
    assert any(table in t for t in texts), "the table is one section's whole"
    assert all(len(t) <= cs.SECTION_MAX for t in texts)


def test_a_heading_inside_a_fence_is_not_a_heading():
    found = _extract(b"# Real\n\n```\n# not a heading\n```\n", "f.md")
    assert found.headings == ("Real",)
    assert found.sections[0].text == "```\n# not a heading\n```"


def test_a_fence_longer_than_a_section_is_cut_at_lines():
    lines = [f"line_{n} = compute({n})" for n in range(200)]
    found = _extract(("```\n" + "\n".join(lines) + "\n```\n").encode(), "big.md")
    assert len(found.sections) > 1
    for section in found.sections:
        assert len(section.text) <= cs.SECTION_MAX
        for line in section.text.split("\n"):
            assert line in ("```", *lines), "no line is cut"


# -- the split function and the embedded text ----------------------------------------------

def test_split_by_pages_says_each_page():
    text = "page one words\n\npage two words"
    sections = cs.split(text, title="Doc", markdown=False, pages=[(1, 0), (2, 16)])
    assert [(s.page, s.text, s.heading, s.heading_path) for s in sections] == [
        (1, "page one words", "Doc", ()), (2, "page two words", "Doc", ())]


def test_split_plain_text_has_no_headings():
    sections = cs.split("# not markdown\n\nbody", title="T", markdown=False)
    assert [s.heading_path for s in sections] == [()]
    assert cs.split("# H\n\nbody", title="T", markdown=True)[0].heading_path == ("H",)


def test_embedded_text_carries_the_title_and_heading_path():
    found = _extract(MARKDOWN, "handbook.md")
    mac = next(s for s in found.sections if s.heading == "On a Mac")
    assert cs.embedded_text(found.title, mac) == \
        "Handbook › Setup › On a Mac\nUse the installer."
    other = cs.Section(0, "Q1", ("Q1",), "x")
    assert cs.embedded_text("Plans", other) == "Plans › Q1\nx"
    assert cs.embedded_text("Plans", cs.Section(0, "Plans", (), "y")) == "Plans\ny"


def test_embedded_text_fits_the_budget():
    found = _extract(f"# {'Heading ' * 60}\n\n{_long_prose(80)}\n".encode(), "b.md")
    for section in found.sections:
        assert len(cs.embedded_text(found.title, section)) <= cs.EMBED_MAX


def test_whole_text_is_title_outline_and_start():
    found = _extract(MARKDOWN + _long_prose(100).encode(), "handbook.md")
    whole = cs.whole_text(found)
    assert whole.startswith("Handbook\nSetup; On a Mac; Use; Appendix\n")
    assert "Before any heading." in whole
    assert len(whole) <= cs.EMBED_MAX
    assert len(cs.whole_text(found, limit=200)) <= 200


# -- other formats ---------------------------------------------------------------------------

def test_a_pdf_is_a_section_per_page(osascript):
    found = _extract(text_pdf(["Summary on page one.", "Evidence on page two."]),
                     "report.pdf", "docs")
    assert found.format == "pdf" and found.pages == 2 and found.title == "report"
    assert [(s.page, s.text) for s in found.sections] == [
        (1, "Summary on page one."), (2, "Evidence on page two.")]
    assert osascript.calls, "read through the osascript seam"


def test_the_pdf_copy_is_private_and_removed(osascript, monkeypatch):
    seen = []
    real = osascript.__call__

    def watching(argv, **kw):
        if len(argv) > 6:
            import os
            import stat
            seen.append((argv[6], stat.S_IMODE(os.stat(argv[6]).st_mode)))
        return real(argv, **kw)

    monkeypatch.setattr(_quartz, "RUNNER", watching)
    _extract(text_pdf(["x"]), "x.pdf")
    assert seen and all(mode == 0o600 for _, mode in seen)
    import os
    assert not any(os.path.exists(path) for path, _ in seen)


def test_a_scanned_pdf_has_no_sections_and_says_so(osascript):
    found = _extract(scanned_pdf(3), "scan.pdf", "docs")
    assert found.sections == () and found.pages == 3
    assert "scanned_no_text" in found.notes


def test_a_blank_page_in_a_pdf_with_text_is_not_scanned(osascript):
    found = _extract(text_pdf(["words", ""]), "x.pdf")
    assert "scanned_no_text" not in found.notes
    assert [s.page for s in found.sections] == [1]


def test_an_encrypted_pdf_is_unreadable(osascript):
    from tests.bundle_fixtures import pdf_pages
    data = pdf_pages(["BT (x) Tj ET"], extra_trailer="/Encrypt 9 0 R")
    assert cs.extract(data, "locked.pdf") == cs.Unreadable("encrypted")


def test_word_headings_make_sections_and_the_core_title_is_kept():
    data = docx_bytes(["Lead in.", "Overview", "First words.", "Detail", "Deeper words.",
                       "Next", "Last words."],
                      styles=[None, "Heading1", None, "Heading2", None, "Heading1", None],
                      title="Core Title")
    found = _extract(data, "memo.docx", "docs")
    assert found.format == "docx" and found.title == "Overview"
    assert [(s.heading_path, s.text) for s in found.sections] == [
        ((), "Lead in."), (("Overview",), "First words."),
        (("Overview", "Detail"), "Deeper words."), (("Next",), "Last words.")]
    plain = _extract(docx_bytes(["just text"], title="Core Title"), "memo.docx")
    assert plain.title == "Core Title"
    assert _extract(docx_bytes(["just text"]), "memo.docx").title == "memo"


def test_a_slide_per_section():
    found = _extract(pptx_bytes([["Welcome", "to the deck"], ["Results", "up 20%"]]),
                     "deck.pptx", "docs")
    assert found.pages == 2 and found.headings == ("Welcome", "Results")
    assert [(s.page, s.heading, s.text) for s in found.sections] == [
        (1, "Welcome", "Welcome\nto the deck"), (2, "Results", "Results\nup 20%")]


def test_a_sheet_per_section_rows_whole():
    found = _extract(xlsx_bytes({"Budget": [["item", "cost"], ["rent", 100],
                                            ["total", ("=SUM(B2)", 100)]],
                                 "Notes": [["a note"]]}), "money.xlsx", "docs")
    assert found.title == "money"
    assert [s.heading for s in found.sections] == ["Budget", "Notes"]
    assert found.sections[0].text.split("\n")[1] == "A2=rent\tB2=100"
    assert "formulas_not_evaluated" in found.notes


def test_html_headings_and_title():
    page = (b"<html><head><title>Page Title</title><style>p{}</style></head><body>"
            b"<p>Intro.</p><h2>Part</h2><p>Part words.</p><h3>Sub</h3><p>Sub words.</p>"
            b"<script>alert(1)</script></body></html>")
    found = _extract(page, "site.html", "docs")
    assert found.title == "Page Title"
    assert [(s.heading_path, s.text) for s in found.sections] == [
        ((), "Intro."), (("Part",), "Part words."), (("Part", "Sub"), "Sub words.")]
    assert "alert" not in found.text and "Page Title" not in found.text
    with_h1 = _extract(b"<title>T</title><h1>Main</h1><p>x</p>", "a.htm")
    assert with_h1.title == "Main"


def test_csv_rows_stay_whole():
    rows = [f'{n},"a value, with a comma, number {n}",{n * 3}' for n in range(200)]
    found = _extract(("\n".join(rows) + "\n").encode(), "big.csv")
    assert len(found.sections) > 1
    for section in found.sections:
        assert len(section.text) <= cs.SECTION_MAX
        assert all(line in rows for line in section.text.split("\n"))


def test_a_legacy_encoding_is_noted():
    found = _extract("caf\xe9 cr\xe8me".encode("cp1252"), "menu.txt")
    assert found.text == "café crème"
    assert "legacy_encoding" in found.notes


# -- unreadable ------------------------------------------------------------------------------

def test_binary_oversized_empty_and_unread_formats_are_unreadable():
    assert cs.extract(b"\x00\x01\x02\x03" * 100, "blob.txt") == cs.Unreadable("binary")
    big = b"word " * (cs.FILE_MAX_BYTES // 5 + 1)
    assert cs.extract(big, "huge.md") == cs.Unreadable("too_large")
    assert cs.extract(b"   \n\n", "blank.md") == cs.Unreadable("empty")
    assert cs.extract(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600, "old.doc") == \
        cs.Unreadable("reader_unavailable")
    assert cs.extract(b"PK\x03\x04 not really a zip", "broken.docx") == \
        cs.Unreadable("corrupt")
