"""HTML and SVG: their text with `html.parser`, and what a static preview
of them would try to load from elsewhere.

Nothing here runs a script. The page's preview is static: the file and the
files it names are served only from the same archive
(BundleInspection.html_asset), with PREVIEW_HEADERS, whose CSP sandbox has
no `allow-scripts` and whose `default-src 'none'` loads nothing from the
web; the page frames it with `sandbox=""` too. `resources` lists what the
document would load from outside the archive (and every script, which is
never run), so the preview can say what it left out.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

from jarvis.bundle_contract import BLOCKED_RESOURCES_MAX, Extent, RenderedPage, RenderOptions
from jarvis.bundle_readers import (Description, Detected, MemberSource, ReaderHealth,
                                   TextSlice, Unsupported, _paging, read_bounded)

HTML_MAX_BYTES = 16 * 1024 * 1024

# The headers every file of a static preview is served with (the bindings
# lane's web_auth applies them to the bundle html routes, as
# web_auth.BUNDLE_PREVIEW_HEADERS): no script runs,
# nothing loads from outside JARVIS, and only JARVIS's own page may frame it.
PREVIEW_CSP = ("default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
               "font-src 'self'; frame-ancestors 'self'; sandbox")
PREVIEW_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"content-security-policy", PREVIEW_CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
)

# Elements whose text is not the document's words.
_SKIPPED = {"script", "style", "noscript", "template"}
# Elements that start a new line of text.
_BLOCKS = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
           "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
           "title", "dt", "dd", "figcaption", "hr", "text", "tspan", "td", "th", "nav",
           "aside", "main", "address", "caption", "summary", "details", "form", "label"}
# Attributes that load something when the document is shown.
_LOADS = {"src", "srcset", "poster", "data", "background", "xlink:href", "href"}
_LOADING_HREF = {"link", "image", "use", "feimage"}
_CSS_URL = re.compile(r"""url\(\s*['"]?([^'")\s]+)|@import\s+['"]([^'"]+)""", re.I)
_EXTERNAL = re.compile(r"^\s*(?:[a-z][a-z0-9+.-]*:|//)", re.I)


def external(url: str) -> bool:
    """Whether a reference leaves the archive: any scheme (http, ftp,
    javascript, file...) or a network path; `data:` stays inside."""
    return bool(_EXTERNAL.match(url)) and not url.strip().lower().startswith("data:")


class _Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = [""]
        self.skipping = 0
        self.resources: list[str] = []
        self._in_style = False
        self.headings = []
        self.links = []
        self.references = []
        self._heading = None
        self._link = None

    def _add(self, url: str) -> None:
        url = url.strip()
        if url and url not in self.resources and len(self.resources) < BLOCKED_RESOURCES_MAX:
            self.resources.append(url[:1024])

    def _css(self, css: str) -> None:
        for match in _CSS_URL.finditer(css):
            url = match.group(1) or match.group(2)
            if external(url):
                self._add(url)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        values = dict(attrs)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading = {"level": int(tag[1]), "element_id": values.get("id"), "text": ""}
            self.headings.append(self._heading)
        if tag == "a" and values.get("href"):
            self._link = {"href": values["href"], "text": ""}
            self.links.append(self._link)
        for key, value in attrs:
            if value and key in {"src", "href", "poster", "data", "xlink:href"}:
                self.references.append(value)
        if tag in _SKIPPED:
            self.skipping += 1
        if tag == "style":
            self._in_style = True
        if tag in _BLOCKS:
            self.lines.append("")
        for name, value in attrs:
            name = (name or "").lower()
            if value is None:
                continue
            if name == "style":
                self._css(value)
            elif name == "src" and tag == "script":
                self._add(value)            # never run, wherever it is
            elif name in _LOADS and (name != "href" or tag in _LOADING_HREF):
                for url in (value.split(",") if name == "srcset" else [value]):
                    url = url.strip().split(" ")[0]
                    if external(url):
                        self._add(url)
            elif name == "content" and tag == "meta" and "url=" in value.lower():
                self._add(value[value.lower().index("url=") + 4:])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() in _SKIPPED:
            self.skipping -= 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading = None
        if tag == "a":
            self._link = None
        if tag in _SKIPPED and self.skipping:
            self.skipping -= 1
        if tag == "style":
            self._in_style = False
        if tag in _BLOCKS:
            self.lines.append("")

    def handle_data(self, data):
        if self._in_style:
            self._css(data)
        if self.skipping:
            return
        self.lines[-1] += data
        if self._heading is not None:
            self._heading["text"] += data
        if self._link is not None:
            self._link["text"] += data


def parse(data: bytes) -> tuple[str, list[str]]:
    """(the document's text, one line per block, blank runs dropped; what it
    would load from outside)."""
    parser = _Reader()
    parser.feed(_paging.decode(data))
    parser.close()
    lines = [" ".join(line.split()) for line in parser.lines]
    text = "\n".join(line for line in lines if line)
    return (text + "\n" if text else ""), parser.resources


class HtmlReader:
    name = "html"
    version = 1
    formats = frozenset({"html", "svg"})

    def health(self) -> ReaderHealth:
        return ReaderHealth(True, frozenset({"read_text"}))

    def parsed(self, source: MemberSource) -> tuple[str, list[str]]:
        return parse(read_bounded(source, HTML_MAX_BYTES))

    def outline(self, source: MemberSource) -> dict:
        parser = _Reader()
        parser.feed(_paging.decode(read_bounded(source, HTML_MAX_BYTES)))
        parser.close()
        return {"headings": parser.headings, "links": parser.links,
                "references": list(dict.fromkeys(parser.references))}

    def describe(self, source: MemberSource, detected: Detected) -> Description:
        try:
            text, resources = self.parsed(source)
        except Unsupported as e:
            return Description("unsupported", Extent(), e.reason)
        return Description("readable", Extent(lines=_paging.line_count(text)),
                           "external_resources_blocked" if resources else None,
                           frozenset({"read_text"}))

    def read_text(self, source: MemberSource, detected: Detected, cursor: Optional[dict],
                  max_chars: int) -> TextSlice:
        text, _ = self.parsed(source)
        chunks, after = _paging.page(_paging.line_units(text), cursor, max_chars)
        return TextSlice(chunks, after)

    def render(self, source: MemberSource, detected: Detected, page: int,
               options: RenderOptions) -> RenderedPage:
        raise Unsupported("reader_unavailable")
