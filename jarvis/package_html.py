"""Install the common observer in served copies, preserving original HTML.

HTMLParser supplies structural tag boundaries; untouched source spans,
including doctypes, raw scripts and comments, are copied byte for byte.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
from jarvis.bundle_readers import _paging

HELPER_PATH = "/api/packages/observe.js"
RESOURCE_ATTRIBUTES = {"src", "href", "poster", "data", "background", "xlink:href"}


def rewrite_css(text, old, new):
    """Walk CSS strings/comments and url() tokens without touching raw text."""
    out, i, in_url, imported = [], 0, False, False
    while i < len(text):
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = len(text) if end < 0 else end + 2
            out.append(text[i:end]); i = end
        elif text[i] in "\"'":
            quote, start = text[i], i
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                elif text[i] == quote:
                    i += 1; break
                else:
                    i += 1
            token = text[start:i]
            out.append(quote + new + token[1 + len(old):-1] + quote
                       if (in_url or imported) and token[1:-1].startswith(old) else token)
            imported = False
        elif text[i:i + 4].lower() == "url(" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_-")):
            out.append(text[i:i + 4]); i += 4; in_url = True
        elif text[i:i + 7].lower() == "@import" and (i + 7 == len(text) or not text[i + 7].isalnum()):
            out.append(text[i:i + 7]); i += 7; imported = True
        elif in_url and text.startswith(old, i):
            out.append(new); i += len(old)
        else:
            if text[i] == ")": in_url = False
            if text[i] == ";": imported = False
            out.append(text[i]); i += 1
    return "".join(out)


class _Adapter(HTMLParser):
    def __init__(self, text, old, new):
        super().__init__(convert_charrefs=False)
        self.text, self.old, self.new = text, old, new
        self.offsets = [0]
        for line in text.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.edits = []
        self.head_end = None
        self.open_raw = []
        self.style_start = None
        self.unsafe = False

    def source_offset(self):
        line, col = self.getpos()
        return self.offsets[line - 1] + col

    def handle_pi(self, data):
        self.unsafe = True

    def handle_starttag(self, tag, attrs):
        start, raw = self.source_offset(), self.get_starttag_text()
        attrs = list(attrs)
        changed = False
        for index, (key, value) in enumerate(attrs):
            if value is None:
                continue
            rewritten = value
            if key in RESOURCE_ATTRIBUTES and value.startswith(self.old):
                rewritten = self.new + value[len(self.old):]
            elif key == "style":
                rewritten = rewrite_css(value, self.old, self.new)
            elif key == "srcset":
                # A srcset is a comma-separated list of URLs and descriptors.
                rewritten = ",".join(self.new + candidate.lstrip()[len(self.old):]
                    if candidate.lstrip().startswith(self.old) else candidate
                    for candidate in value.split(","))
            if rewritten != value:
                attrs[index] = (key, rewritten); changed = True
        if changed:
            closing = "/>" if raw.rstrip().endswith("/>") else ">"
            replacement = "<" + tag + "".join(" " + key + ("" if value is None else
                '="' + html.escape(value, quote=True) + '"') for key, value in attrs) + closing
            self.edits.append((start, start + len(raw), replacement))
        if tag in {"script", "style"}:
            self.open_raw.append(tag)
        if tag == "style":
            self.style_start = start + len(raw)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in {"script", "style"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "head" and self.head_end is None:
            self.head_end = self.source_offset()
        if tag == "style" and self.style_start is not None:
            start, end = self.style_start, self.source_offset()
            value = self.text[start:end]
            rewritten = rewrite_css(value, self.old, self.new)
            if value != rewritten:
                self.edits.append((start, end, rewritten))
            self.style_start = None
        if tag in self.open_raw:
            self.open_raw.remove(tag)


def serve_html(data: bytes, old: str, new: str):
    encoding = _paging.encoding_of(data)
    try:
        text = data.decode(encoding, errors="strict")
        parser = _Adapter(text, old, new)
        parser.feed(text); parser.close()
        if parser.unsafe or parser.open_raw:
            return data, ("observer_unavailable",)
        insertion = parser.head_end
        if insertion is None:
            # Append after the document: HTML parsing places this script in body.
            insertion = len(text)
        helper = f'<script src="{HELPER_PATH}"></script>'
        parser.edits.append((insertion, insertion, helper))
        for start, end, replacement in sorted(parser.edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        return text.encode(encoding), ()
    except (UnicodeError, ValueError):
        return data, ("observer_unavailable",)
