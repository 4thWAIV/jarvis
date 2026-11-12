"""Items at paths: the engine's side of the UDS interface (round 1 of the
plan in docs/planning/knowledge-engine/plan-uds.md).

An item lives at one path (`paths.py`) and the path is the key to all of
its streams. Its document (family DOCS, key the path) holds its path entry
and versioning fields (`kinds.ITEM_SYSTEM`) and its data: fields of any
shape, every one indexed. Its content stream (family CONTENT, same key)
holds its text, its sections and the reference to its file; the file's
bytes stay where they are. Both are written in one log transaction, so
they share one commit order and become visible together.

Every write is a version. A version records who wrote it (`_by`, the person
whose rights apply; `_agent` and `_model` when an AI wrote for him), what
kind of change it was (`_change`) and its summary, and when (`_at`, seconds,
never earlier than any write before it). Compaction keeps every version of
an item (store.HISTORY_FAMILIES), so `get` takes a version or a time and
`history` and `diff` read them; a delete is a version too (a tombstone) and
keeps the history; only `forget` erases it. Old versions are never in an
index: the indexes describe the newest state only.

A transaction writes several items at once, with conditions checked under
the store's commit lock: a write may name the version it expects (`0`:
the path is not taken) or say the path must not be taken (`absent`). A
condition that does not hold refuses the whole transaction
(`ConditionFailed`) and nothing is written. Each transaction that touches
an item adds one record to the change feed (family FEED, keyed by its LSN):
what changed, by whom and why, so a reader follows every write since a log
number.

Links: hierarchy (parent and children) is worked out from the paths and
never stored; a field whose value is an item path is a reference link,
written with the item (`graph.Graph._item_reference_ops`); a derived link is
written by a transaction's `link` operation and records the model that made
it and when. A link is shown only while both its ends are items.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import threading
import time
from typing import Any, Iterator, Optional

from jarvis import spoken_words
from jarvis.knowledge import encoding, kinds, paths, rights
from jarvis.knowledge.documents import (DOC_HEAD, FID, DocumentError, DocumentStore, Predicate,
                                        decode_doc, _sort_key)
from jarvis.knowledge.graph import (DERIVED, HIERARCHY, REFERENCE, SEP, Graph, edge_key,
                                    split_key)
from jarvis.knowledge.lexical import RECORD_TYPES as lexical_record_types, phrase_in as lexical_phrase_in, \
    record_parts as lexical_record_parts, record_text as lexical_record_text, terms as lexical_terms
from jarvis.knowledge.rights import FULL, OWNER, Access, Denied, Rights, caller_of, without_hidden
from jarvis.knowledge.store import CONTENT, DOCS, EDGES_IN, EDGES_OUT, FEED, prefix_end
from jarvis.knowledge.wal import Op

FEED_KEY = struct.Struct(">Q")
WRITES_MAX = 1000
DOC_MAX_BYTES = 16 << 20
TEXT_MAX_BYTES = 32 << 20
LIST_LIMIT_MAX = 1000
CHANGES_LIMIT_MAX = 1000
CHANGES_SCAN_MAX = 20000
LINKS_LIMIT_MAX = 1000
HISTORY_LIMIT_MAX = 10000
INDEX_PATH_MAX = 50000       # past this many candidates, a path scan is cheaper
WORDS_MAX = 2000             # a change kind or a summary
STREAMS = ("entry", "data", "content", "sections", "vectors", "rights")
DEFAULT_STREAMS = ("entry", "data")
OPS = ("put", "merge", "delete", "check", "link", "unlink")
ITEM_OPS = ("put", "merge", "delete", "check")
LINK_KIND_MAX = 200
SEARCH_LIMIT_MAX = 200
SEARCH_BUDGET_MS = 2000
QUERY_TEXT_MAX = 2000
RRF_K = 60
PROPOSALS = "/proposals/links"
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


class ConditionFailed(DocumentError):
    """A write's condition did not hold; nothing in its transaction was
    written. Answered 409 with the path and the version it found."""

    def __init__(self, path: str, expected: Any, found: int, words: str) -> None:
        super().__init__(words)
        self.path, self.expected, self.found = path, expected, found


def feed_key(lsn: int) -> bytes:
    return FEED_KEY.pack(lsn)


def split_sections(text: str) -> list[dict]:
    """The text cut at its Markdown headings, each section with its heading;
    text before the first heading is a section with no heading. Round 2
    makes sections for every readable format; this is the simple case."""
    sections: list[dict] = []
    heading, lines = "", []

    def close() -> None:
        body = "\n".join(lines).strip("\n")
        if body.strip() or heading:
            sections.append({"index": len(sections), "heading": heading, "text": body})

    for line in text.splitlines():
        found = _HEADING.match(line)
        if found:
            close()
            heading, lines = found.group(2), []
        else:
            lines.append(line)
    close()
    return sections


def _words(value, what: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > WORDS_MAX:
        raise DocumentError(f"{what} is text of at most {WORDS_MAX} characters")
    return value


def _writer(body: dict) -> dict:
    by = body.get("by")
    if not isinstance(by, dict) or not isinstance(by.get("person"), str) or not by["person"].strip():
        raise DocumentError("a write says whose it is: by.person")
    out = {"person": by["person"]}
    for k in ("agent", "model"):
        if by.get(k) is not None:
            if not isinstance(by[k], str) or not by[k]:
                raise DocumentError(f"by.{k} is text")
            out[k] = by[k]
    return out


def _data(value, what: str = "data") -> dict:
    if not isinstance(value, dict):
        raise DocumentError(f"{what} is an object")
    for k in value:
        if not isinstance(k, str) or not k:
            raise DocumentError(f"{what} field names are non-empty text")
        if k.startswith("_"):
            raise DocumentError(f"{k}: field names starting with _ are the engine's")
    return value


def _content(value) -> Optional[dict]:
    """A content stream as given: text (the extracted text the indexes
    read), optional sections, the file's reference, hash and media type."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DocumentError("content is an object with text")
    text = value.get("text")
    if not isinstance(text, str):
        raise DocumentError("content.text is the item's text")
    if len(text.encode("utf-8")) > TEXT_MAX_BYTES:
        raise DocumentError(f"content.text is at most {TEXT_MAX_BYTES >> 20} MB")
    out: dict = {"text": text}
    for k in ("ref", "sha256", "media_type", "title", "format"):
        if value.get(k) is not None:
            if not isinstance(value[k], str) or not value[k]:
                raise DocumentError(f"content.{k} is text")
            out[k] = value[k]
    sections = value.get("sections")
    if sections is None:
        out["sections"] = split_sections(text)
    else:
        if not isinstance(sections, list):
            raise DocumentError("content.sections is a list")
        out["sections"] = []
        for i, s in enumerate(sections):
            if not isinstance(s, dict) or not isinstance(s.get("text"), str):
                raise DocumentError("a section has text")
            heading = s.get("heading") or ""
            if not isinstance(heading, str):
                raise DocumentError("a section's heading is text")
            section = {"index": i, "heading": heading, "text": s["text"]}
            trail = s.get("heading_path")
            if trail is not None:
                if not isinstance(trail, list) or not all(isinstance(h, str) for h in trail):
                    raise DocumentError("a section's heading_path is a list of headings")
                section["heading_path"] = trail
            page = s.get("page")
            if page is not None:
                if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                    raise DocumentError("a section's page is a page number")
                section["page"] = page
            out["sections"].append(section)
    out.setdefault("sha256", hashlib.sha256(text.encode("utf-8")).hexdigest())
    return out


WHOLE_TEXT_MAX = 1501        # a section's 1,300 characters and its 200-character heading line:
HEADING_LINE_MAX = 200       # about multilingual-e5-small's 512 tokens
_PATH_JOIN = " › "


def title_of(doc: dict, content: dict) -> str:
    """An item's title: its data's, its content's, else its name."""
    for found in (doc.get("title"), content.get("title")):
        if isinstance(found, str) and found.strip():
            return found.strip()
    name = doc.get("_name") or ""
    stem = name.rpartition(".")[0]
    return stem or name


def heading_trail(section: dict) -> list[str]:
    """A section's heading path; a section that names none has its heading."""
    if "heading_path" in section:
        return [h for h in section["heading_path"] or () if h]
    return [section["heading"]] if section.get("heading") else []


def clip(text: str, limit: int) -> str:
    """At most `limit` characters, ending at a word's end."""
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    return (cut[:space] if space > 0 else text[:limit]).rstrip()


def _heading_line(title: str, trail) -> str:
    parts = [title] if title else []
    parts += [p for n, p in enumerate(trail) if p and not (n == 0 and p == title)]
    return clip(" ".join(_PATH_JOIN.join(parts).split()), HEADING_LINE_MAX)


def embedded_text(title: str, section: dict, limit: int = WHOLE_TEXT_MAX) -> str:
    """The text a section's vector is made from: "Title › H1 › H2" (the
    title not said twice), then its text, within one embedding's budget.
    The one definition: content_sections gives the same for a file."""
    head = _heading_line(title, heading_trail(section))
    text = str(section.get("text") or "")
    return clip(f"{head}\n{text}" if head else text, limit)


def whole_text(title: str, content: dict, limit: int = WHOLE_TEXT_MAX) -> str:
    """The text the whole-item vector is made from: its title, the outline
    of its headings (`headings` when the content names them, else each
    section's), then the start of its text, within `limit`."""
    title = " ".join((title or "").split())
    headings = content.get("headings")
    if headings is None:
        headings = list(dict.fromkeys(h for s in content.get("sections") or () for h in heading_trail(s)[-1:]))
    outline = clip("; ".join(" ".join(h.split()) for h in headings if h and h != title), limit // 3)
    head = "\n".join(p for p in (title, outline) if p)
    room = limit - len(head) - 1
    start = clip((content.get("text") or "").strip(), room) if room > 0 else ""
    return clip("\n".join(p for p in (head, start) if p), limit)


RECORD_SECTION_MAX = 1300     # a record part longer than this is cut into sections of it


def record_texts(doc: dict) -> list[str]:
    """The texts a record's vectors are made from (a record of a type that
    holds people's words, `lexical.RECORD_TYPES`, which has no content):
    the whole record first (its title, then its words from the start),
    then, when it has several parts or a part too long for one, one per
    section: each part (`lexical.record_parts`), a long one cut at word
    ends into sections of at most `RECORD_SECTION_MAX` characters. Empty
    for any other item, or a record with no words."""
    if not is_record(doc):
        return []
    parts = lexical_record_parts(doc)
    if not parts:
        return []
    title = doc.get("title") if isinstance(doc.get("title"), str) else ""
    sections: list[dict] = []
    for heading, text in parts:
        while text:
            piece = clip(text, RECORD_SECTION_MAX) or text[:RECORD_SECTION_MAX]
            sections.append({"heading": heading, "text": piece})
            text = text[len(piece):].lstrip()
    whole = whole_text(title, {"headings": [], "text": "\n".join(t for _, t in parts)})
    if len(sections) < 2:
        return [whole]
    return [whole] + [embedded_text(title, s) for s in sections]


def is_record(doc: dict) -> bool:
    """An item of a type that holds people's words, with no content."""
    return doc.get("_content") is None and doc.get("_type") in lexical_record_types


def record_words_summary(texts: list[str]) -> dict:
    """What a record's document keeps of the texts its vectors are made
    from (`_words`): their hash, so a change of them makes the old vectors
    stale in the same write, and how many sections they hold; `none` for a
    record with no words, so a count tells it from one written before."""
    if not texts:
        return {"none": True}
    return {"sha256": hashlib.sha256(encoding.canonical(texts)).hexdigest(), "sections": len(texts) - 1}


def words_summary(doc: dict) -> Optional[dict]:
    """A record's `_words` when it has words to embed, else None; for one
    written before records had vectors (no `_words` yet), worked out from
    it as it is, so it is embedded and its first new write sees what its
    vectors were made from."""
    if not is_record(doc):
        return None
    found = doc.get("_words")
    if found is None:
        found = record_words_summary(record_texts(doc))
    return found if found and found.get("sha256") else None


def embedded_summary(doc: dict) -> Optional[dict]:
    """What an item's vectors are made from: its content's summary
    (`_content`), or a record's words' (`words_summary`); None when it
    has neither."""
    return doc.get("_content") or words_summary(doc)


def _content_summary(content: dict) -> dict:
    summary = {"sha256": content["sha256"], "chars": len(content["text"]),
               "sections": len(content["sections"])}
    for k in ("ref", "media_type", "format"):
        if k in content:
            summary[k] = content[k]
    return summary


def _condition(w: dict) -> tuple[Optional[int], bool]:
    expect = w.get("expect_version")
    if expect is not None and (isinstance(expect, bool) or not isinstance(expect, int) or expect < 0):
        raise DocumentError("expect_version is a version number, 0 for a path not yet taken")
    absent = w.get("absent", False)
    if not isinstance(absent, bool):
        raise DocumentError("absent is true or false")
    return expect, absent


def item_view(doc: dict, content: Optional[dict], streams) -> dict:
    """An item as a reader gets it: the streams asked for."""
    out = {"path": doc["_key"], "type": doc["_type"], "version": doc["_version"], "lsn": doc.get("_lsn")}
    if "entry" in streams:
        out["entry"] = {"path": doc["_key"], "parent": doc["_parent"], "name": doc["_name"],
                        "type": doc["_type"], "ancestors": list(doc.get("_ancestors") or []),
                        "created": doc.get("_created"), "at": doc.get("_at"), "by": doc.get("_by"),
                        "agent": doc.get("_agent"), "model": doc.get("_model"),
                        "change": doc.get("_change"), "summary": doc.get("_summary"),
                        "content": doc.get("_content")}
    if "data" in streams:
        out["data"] = {k: v for k, v in doc.items() if not k.startswith("_")}
    if "content" in streams:
        out["content"] = None if content is None else {k: content[k] for k in content if k != "sections"}
    if "sections" in streams:
        out["sections"] = None if content is None else content.get("sections") or []
    return out


def _streams(raw) -> tuple:
    if raw is None:
        return DEFAULT_STREAMS
    if not isinstance(raw, list) or not all(s in STREAMS for s in raw):
        raise DocumentError(f"streams are some of {', '.join(STREAMS)}")
    return tuple(raw)


def _encode_cursor(value: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def _decode_cursor(cursor) -> Any:
    try:
        return json.loads(base64.urlsafe_b64decode(str(cursor).encode()))
    except (ValueError, TypeError):
        raise DocumentError("not a cursor this engine gave")


def diff_values(before: Any, after: Any, at: str = "") -> list[dict]:
    """Field by field: objects by name, lists by position."""
    if isinstance(before, dict) and isinstance(after, dict):
        out = []
        for k in sorted(set(before) | set(after)):
            p = f"{at}.{k}" if at else k
            if k not in before:
                out.append({"field": p, "after": after[k]})
            elif k not in after:
                out.append({"field": p, "before": before[k]})
            else:
                out.extend(diff_values(before[k], after[k], p))
        return out
    if isinstance(before, list) and isinstance(after, list):
        out = []
        for i in range(max(len(before), len(after))):
            p = f"{at}[{i}]"
            if i >= len(before):
                out.append({"field": p, "after": after[i]})
            elif i >= len(after):
                out.append({"field": p, "before": before[i]})
            else:
                out.extend(diff_values(before[i], after[i], p))
        return out
    if before == after and type(before) is type(after):
        return []
    return [{"field": at, "before": before, "after": after}]


class Items:
    def __init__(self, docs: DocumentStore, graph: Graph, rights: Rights) -> None:
        self.docs = docs
        self.graph = graph
        self.rights = rights
        self.store = docs.store
        self.vectors = None          # the vector index, set by the engine once it is open
        self.lexical = None          # the keyword index, likewise
        self._clock_lock = threading.Lock()
        self.last_at = 0.0
        newest = self._newest_feed()
        if newest is not None:
            self.last_at = float(newest.get("at") or 0.0)

    # -- the clock ------------------------------------------------------------
    def _now(self) -> float:
        """Write time, never earlier than any write before it, so a time
        names one point in every item's history."""
        with self._clock_lock:
            self.last_at = max(round(time.time(), 6), self.last_at)
            return self.last_at

    def _newest_feed(self) -> Optional[dict]:
        last = None
        for v in self.store.scan(FEED):
            last = v
        return None if last is None else json.loads(last.value)

    # -- the gate ---------------------------------------------------------------
    def _reader(self, body: dict) -> str:
        """The person whose rights a read applies: the caller's (an AI's is
        its person's, never wider)."""
        return caller_of(body.get("caller"))["person"]

    def access(self, person: str, path: str) -> Access:
        return self.rights.access(person, path)

    def _shown(self, doc: dict, content: Optional[dict], streams, access: Access) -> dict:
        """An item as this reader may see it: hidden fields out; content,
        sections and the content summary only with the content right."""
        view = item_view(doc, content if access.content else None, streams)
        if "data" in view and access.hidden:
            view["data"] = without_hidden(view["data"], access)
        if "entry" in view and not access.content:
            view["entry"]["content"] = None
        if "rights" in streams:
            view["rights"] = self.rights.on_path(doc["_key"]) if access is FULL else None
        return view

    # -- one item's versions --------------------------------------------------
    def _versions(self, path: str) -> list:
        """Every version of the item's document, newest first."""
        return self.store.versions(DOCS, path.encode())

    def _feed(self, lsn: int) -> Optional[dict]:
        v = self.store.get(FEED, feed_key(lsn))
        return None if v is None or v.value is None else json.loads(v.value)

    def _tombstone_entry(self, path: str, lsn: int) -> dict:
        record = self._feed(lsn) or {}
        for it in record.get("items") or []:
            if it.get("path") == path:
                return {"version": it.get("version"), "lsn": lsn, "at": record.get("at"),
                        "by": record.get("by"), "agent": record.get("agent"),
                        "model": record.get("model"), "change": record.get("change"),
                        "summary": record.get("summary"), "op": "delete"}
        return {"version": None, "lsn": lsn, "at": record.get("at"), "op": "delete"}

    def _current(self, path: str) -> tuple[Optional[dict], int]:
        """The live document and the item's newest version number (a
        tombstone's included), under the commit lock."""
        versions = self._versions(path)
        if not versions:
            return None, 0
        top = versions[0]
        if top.value is not None:
            o, doc = decode_doc(top.value)
            doc["_lsn"], doc["_ord"] = top.lsn, o
            return doc, int(doc.get("_version") or 0)
        number = self._tombstone_entry(path, top.lsn).get("version")
        if number is None:
            number = max((int(decode_doc(v.value)[1].get("_version") or 0)
                          for v in versions if v.value is not None), default=0) + 1
        return None, int(number)

    # -- writing ----------------------------------------------------------------
    def transact(self, body: dict) -> dict:
        """One atomic transaction of puts, merges, deletes and links, with
        their conditions checked under the commit lock."""
        if not isinstance(body, dict):
            raise DocumentError("a transaction is an object")
        writer = _writer(body)
        change = _words(body.get("change"), "change")
        summary = _words(body.get("summary"), "summary")
        writes = body.get("writes")
        if not isinstance(writes, list) or not 0 < len(writes) <= WRITES_MAX:
            raise DocumentError(f"a transaction has 1 to {WRITES_MAX} writes")
        durable = body.get("durable")
        if durable not in (None, "full"):
            raise DocumentError("durable is full, or left out")
        seen: set[str] = set()
        for w in writes:
            if not isinstance(w, dict) or w.get("op") not in OPS:
                raise DocumentError(f"a write's op is one of {', '.join(OPS)}")
            if w["op"] in ITEM_OPS:
                path = paths.check(w.get("path"))
                if path in seen:
                    raise DocumentError(f"{path} is written once a transaction")
                seen.add(path)
                if w["op"] == "check":
                    if not self.access(writer["person"], path).data:
                        raise Denied(f"{path} is not {writer['person']}'s to read")
                elif not self.access(writer["person"], path).write:
                    raise Denied(f"{path} is not {writer['person']}'s to write")
                _words(w.get("change"), "a write's change")
                _words(w.get("summary"), "a write's summary")
        return self._commit(writes, writer, change, summary, durable == "full")

    def _commit(self, writes: list[dict], writer: dict, change, summary, full: bool,
                proposal: Optional[dict] = None) -> dict:
        result: dict = {}

        def prepare(lsn: int) -> list[Op]:
            ops, done = self._plan(writes, writer, change, summary, lsn, proposal)
            result.update(done)
            return ops

        lsn = self.store.commit(prepare=prepare, full=full)
        if not result:
            return {"lsn": lsn, "at": None, "items": [], "links": 0}
        return {"lsn": lsn, **result}

    def _plan(self, writes: list[dict], writer: dict, change, summary, lsn: int,
              proposal: Optional[dict] = None) -> tuple[list[Op], dict]:
        at = self._now()
        puts: list[dict] = []
        deletes: list[str] = []
        content_ops: list[Op] = []
        vector_ops: list[Op] = []
        changed: list[dict] = []
        made: dict[str, Optional[dict]] = {}     # path -> its document after this write
        for w in writes:
            op = w["op"]
            if op not in ITEM_OPS:
                continue
            path = w["path"]
            expect, absent = _condition(w)
            doc, number = self._current(path)
            live_version = number if doc is not None else 0
            if expect is not None and live_version != expect:
                raise ConditionFailed(path, expect, live_version,
                                      f"{path} is at version {live_version}, not {expect}")
            if absent and doc is not None:
                raise ConditionFailed(path, 0, live_version, f"{path} is taken (version {live_version})")
            if op == "check":
                continue
            if op == "delete":
                if doc is None:
                    continue
                deletes.append(path)
                if doc.get("_content") is not None:
                    content_ops.append(Op(CONTENT, path.encode(), None))
                changed.append({"path": path, "op": "delete", "version": number + 1, "type": doc["_type"]})
                made[path] = None
                continue
            if op == "put":
                fields = dict(_data(w.get("data", {})))
            else:
                if doc is None:
                    raise ConditionFailed(path, "an item", 0, f"{path} is not an item to merge into")
                fields = {k: v for k, v in doc.items() if not k.startswith("_")}
                fields.update(_data(w.get("set", {}), "set"))
                unset = w.get("unset") or []
                if not isinstance(unset, list) or not all(isinstance(k, str) for k in unset):
                    raise DocumentError("unset is a list of field names")
                for k in unset:
                    fields.pop(k, None)
            kind = paths.type_of(path)
            new = {"_key": path, "_kind": kinds.ITEM, "_type": kind, "_parent": paths.parent(path),
                   "_name": paths.name(path), "_ancestors": paths.ancestors(path),
                   "_depth": paths.depth(path), "_version": number + 1,
                   "_created": doc.get("_created", at) if doc is not None else at,
                   "_at": at, "_by": writer["person"], **fields}
            for k in ("agent", "model"):
                if k in writer:
                    new["_" + k] = writer[k]
            # A write may name its own change and summary (one item's event
            # in a transaction that writes several); else the transaction's.
            own_change = w["change"] if w.get("change") is not None else change
            own_summary = w["summary"] if w.get("summary") is not None else summary
            if own_change is not None:
                new["_change"] = own_change
            if own_summary is not None:
                new["_summary"] = own_summary
            before = (doc or {}).get("_content")
            if "content" in w:
                content = _content(w["content"])
                if content is not None and kind == paths.FOLDER:
                    raise DocumentError(f"{path} is a folder, and a folder has no content")
                if content is None:
                    if before is not None:
                        content_ops.append(Op(CONTENT, path.encode(), None))
                else:
                    new["_content"] = _content_summary(content)
                    content_ops.append(Op(CONTENT, path.encode(), encoding.canonical(content)))
            elif before is not None:
                # Kept content is written again beside the new document, so
                # an item's document and content always share a commit (and
                # a generation, which the keyword index reads them by).
                new["_content"] = before
                held = self.store.get(CONTENT, path.encode())
                if held is not None and held.value is not None:
                    content_ops.append(Op(CONTENT, path.encode(), held.value))
            after = new.get("_content")
            if before is not None and (after is None or after["sha256"] != before["sha256"]):
                vector_ops += self._stale_vectors(path)
            # A record's words are embedded as an item's content is: the
            # old vectors go in the same write as a change of its words.
            if is_record(new):
                new["_words"] = record_words_summary(record_texts(new))
            held_words = words_summary(doc) if doc is not None else None
            if held_words is not None and held_words != words_summary(new):
                vector_ops += self._stale_vectors(path)
            if len(encoding.canonical(new)) > DOC_MAX_BYTES:
                raise DocumentError(f"{path}: an item's document is at most {DOC_MAX_BYTES >> 20} MB")
            puts.append(new)
            made[path] = new
            changed.append({"path": path, "op": op, "version": number + 1, "type": kind})
        link_ops, links = self._link_ops(writes, writer, at, made, proposal)
        rights_ops = []
        for w in writes:
            if w["op"] == "rights":
                rights_ops += self.rights.set_ops(w["path"], w.get("grants") or [], writer["person"], at)
        if not changed and not links and not rights_ops:
            return [], {}
        ops, reserved = self.docs.plan(puts, deletes)
        self.docs.release(reserved)
        record = {"lsn": lsn, "at": at, "by": writer["person"], "items": changed}
        for k in ("agent", "model"):
            if k in writer:
                record[k] = writer[k]
        if change is not None:
            record["change"] = change
        if summary is not None:
            record["summary"] = summary
        if links:
            record["links"] = links
        if rights_ops:
            record["rights"] = [w["path"] for w in writes if w["op"] == "rights"]
        feed = Op(FEED, feed_key(lsn), encoding.canonical(record))
        return (vector_ops + ops + content_ops + link_ops + rights_ops + [feed],
                {"at": at, "items": changed, "links": len(links)})

    def _stale_vectors(self, path: str) -> list[Op]:
        """The item's vector rows, deleted with a change of its content."""
        if self.vectors is None:
            return []
        o = self.docs._ord_from_store(path.encode())
        return [] if o is None else self.vectors.item_tombstones(o)

    def _is_item(self, path: str, made: dict) -> bool:
        if path in made:
            return made[path] is not None
        v = self.store.get(DOCS, path.encode())
        return v is not None and v.value is not None

    def _link_ops(self, writes: list[dict], writer: dict, at: float, made: dict,
                  proposal: Optional[dict] = None) -> tuple[list[Op], list[dict]]:
        ops: list[Op] = []
        links: list[dict] = []
        for w in writes:
            if w["op"] not in ("link", "unlink"):
                continue
            src, dst, kind = w.get("from"), w.get("to"), w.get("kind")
            paths.check(src)
            paths.check(dst)
            if not isinstance(kind, str) or not kind or len(kind) > LINK_KIND_MAX or "\x00" in kind:
                raise DocumentError(f"a link's kind is text of at most {LINK_KIND_MAX} characters")
            mine = self.access(writer["person"], src)
            if not mine.write or not self.access(writer["person"], dst).data:
                raise Denied(f"a link from {src} to {dst} is not {writer['person']}'s to write")
            if w["op"] == "unlink":
                ops += self.graph._edge_ops(src, kind, dst, None)
                links.append({"op": "unlink", "from": src, "kind": kind, "to": dst})
                continue
            if "agent" in writer and proposal is None:
                raise DocumentError("a link an AI makes is a proposal until the owner accepts it: "
                                    "propose it")
            origin = w.get("origin", DERIVED)
            if origin != DERIVED:
                raise DocumentError("a written link is derived; references come from fields and "
                                    "hierarchy from paths")
            model = w.get("model") or writer.get("model")
            if not isinstance(model, str) or not model:
                raise DocumentError("a derived link records the model that made it")
            for end in (src, dst):
                if not self._is_item(end, made):
                    raise ConditionFailed(end, "an item", 0, f"{end} is not an item to link")
            weight = w.get("weight", 1.0)
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0.0 <= weight <= 1.0:
                raise DocumentError("weight is 0 to 1")
            value = {"origin": DERIVED, "weight": float(weight), "model": model, "at": at,
                     "by": writer["person"]}
            if "agent" in writer:
                value["agent"] = writer["agent"]
            if proposal is not None:
                value.update({"proposal": proposal["path"], "proposed_at": proposal["at"],
                              "proposed_by": proposal["person"]})
                if proposal.get("agent"):
                    value["agent"] = proposal["agent"]
            ops += self.graph._edge_ops(src, kind, dst, value)
            links.append({"op": "link", "from": src, "kind": kind, "to": dst, "model": model})
        return ops, links

    def forget(self, body: dict) -> dict:
        """Erase every version of items already deleted, as if they had
        never been written (the owner's act): their documents and content
        with every edge and vector row that hangs on them. An item still
        live is refused, so its indexes are always cleared by its delete
        first. Used to clear a records move stopped part way
        (knowledge_move.clear_interrupted), whose half-written histories
        must not stay behind a new copy."""
        writer = _writer(body)
        if writer["person"] != OWNER:
            raise Denied("only the owner forgets items")
        wanted = body.get("paths")
        if not isinstance(wanted, list) or not all(isinstance(p, str) for p in wanted):
            raise DocumentError("paths is a list of item paths")
        for path in wanted:
            paths.check(path)
            if self._current(path)[0] is not None:
                raise DocumentError(f"{path} is an item; delete it before forgetting it")
        keys = {p.encode() for p in wanted}
        if not keys:
            return {"documents": 0}
        return self.docs.forget_keys(keys, also={CONTENT: set(keys)})

    def set_rights(self, body: dict) -> dict:
        """The grants on one path, set by the owner (empty clears them)."""
        writer = _writer(body)
        path = body.get("path")
        if path != "/":
            paths.check(path)
        if writer["person"] != OWNER:
            raise Denied("only the owner sets rights")
        rights.check_grants(body.get("grants") or [])
        return self._commit([{"op": "rights", "path": path, "grants": body.get("grants") or []}],
                            writer, _words(body.get("change"), "change") or "rights",
                            _words(body.get("summary"), "summary"), False)

    # -- reading one item -------------------------------------------------------
    def get(self, path: str, *, version: Optional[int] = None, at: Optional[float] = None,
            streams=None, person: str = OWNER) -> Optional[dict]:
        """The item now, at a version, or as it stood at a time; None when
        it is not (or was not then) an item, or the reader may not see it."""
        paths.check(path)
        streams = _streams(streams)
        if version is not None and at is not None:
            raise DocumentError("a version or a time, not both")
        access = self.access(person, path)
        if not access.data:
            return None
        with self.store.reading() as now:
            found = None
            for v in self._versions(path):
                if v.lsn > now:
                    continue
                if version is None and at is None:
                    found = v
                    break
                if v.value is None:
                    entry = self._tombstone_entry(path, v.lsn)
                    if version is not None and entry.get("version") == version:
                        return None
                    if at is not None and entry.get("at") is not None and entry["at"] <= at:
                        return None
                    continue
                doc = decode_doc(v.value)[1]
                if version is not None and doc.get("_version") == version:
                    found = v
                    break
                if at is not None and float(doc.get("_at") or 0) <= at:
                    found = v
                    break
            if found is None or found.value is None:
                return None
            o, doc = decode_doc(found.value)
            doc["_lsn"], doc["_ord"] = found.lsn, o
            content = None
            if doc.get("_content") is not None and access.content and \
                    ("content" in streams or "sections" in streams):
                c = self.store.get(CONTENT, path.encode(), found.lsn)
                content = None if c is None or c.value is None else json.loads(c.value)
            view = self._shown(doc, content, streams, access)
            if "vectors" in streams:
                view["vectors"] = self._vector_state(o, doc, access) if version is None and at is None \
                    else None
            return view

    def _vector_state(self, o: int, doc: dict, access: Access) -> Optional[dict]:
        """Whether the item's vectors exist for its current content: the
        whole-item vector and one per section, or waiting."""
        summary = embedded_summary(doc)
        if summary is None or not (access.content if doc.get("_content") is not None else access.data):
            return None
        embedded = self.vectors is not None and self.vectors.item_embedded(o)
        return {"state": "embedded" if embedded else "waiting",
                "sections": summary.get("sections", 0) if embedded else 0}

    def history(self, path: str, *, limit: int = 100, before: Optional[int] = None,
                person: str = OWNER, fields: Optional[list] = None) -> dict:
        """Each version, newest first: its number, when, by whom, what kind
        of change and its summary; a delete is a version too. With
        `fields`, each version also carries those top-level fields of its
        data as they stood then (`data`), so a record's events are read
        from its history in one call."""
        paths.check(path)
        if not 0 < limit <= HISTORY_LIMIT_MAX:
            raise DocumentError(f"limit is 1 to {HISTORY_LIMIT_MAX}")
        if fields is not None and (not isinstance(fields, list) or not all(
                isinstance(f, str) and f and not f.startswith("_") for f in fields)):
            raise DocumentError("fields is a list of data field names")
        access = self.access(person, path)
        if not access.data:
            return {"path": path, "versions": [], "before": None}
        out = []
        for v in self._versions(path):
            if v.value is None:
                entry = self._tombstone_entry(path, v.lsn)
            else:
                doc = decode_doc(v.value)[1]
                entry = {"version": doc.get("_version"), "lsn": v.lsn, "at": doc.get("_at"),
                         "by": doc.get("_by"), "agent": doc.get("_agent"), "model": doc.get("_model"),
                         "change": doc.get("_change"), "summary": doc.get("_summary"), "op": "put"}
                if fields is not None:
                    shown = {f: doc[f] for f in fields if f in doc}
                    entry["data"] = without_hidden(shown, access) if access.hidden else shown
            if before is not None and (entry.get("version") or 0) >= before:
                continue
            out.append(entry)
            if len(out) > limit:
                break
        more = len(out) > limit
        out = out[:limit]
        return {"path": path, "versions": out,
                "before": out[-1]["version"] if more and out else None}

    def diff(self, path: str, a: int, b: Optional[int] = None, person: str = OWNER) -> dict:
        """What changed in the item's data between version `a` and version
        `b` (the current one when left out), and whether its content did."""
        first = self.get(path, version=a, streams=["entry", "data"], person=person)
        second = self.get(path, streams=["entry", "data"], person=person) if b is None else \
            self.get(path, version=b, streams=["entry", "data"], person=person)
        if first is None or second is None:
            raise DocumentError(f"{path} has no such version")
        content_a = (first["entry"].get("content") or {}).get("sha256")
        content_b = (second["entry"].get("content") or {}).get("sha256")
        return {"path": path, "from": first["version"], "to": second["version"],
                "changes": diff_values(first["data"], second["data"]),
                "content_changed": content_a != content_b}

    # -- reading many -------------------------------------------------------------
    def _preds(self, body: dict) -> tuple[list[Predicate], str, Optional[str], Optional[list[str]]]:
        where = body.get("where") or []
        if isinstance(where, dict):
            where = [[k, "=", v] for k, v in where.items()]
        if not isinstance(where, list):
            raise DocumentError("where is a list of [field, op, value]")
        preds = [Predicate.parse(w) for w in where]
        under = body.get("under") or "/"
        prefix = paths.under_prefix(under)
        parent = body.get("parent")
        if parent is not None:
            if parent != "/":
                paths.check(parent)
            preds.append(Predicate("_parent", "=", parent))
        types = body.get("types")
        if types is not None:
            if not isinstance(types, list) or not all(isinstance(t, str) and t for t in types):
                raise DocumentError("types is a list of type suffixes")
            types = [t.lower() for t in types]
            preds.append(Predicate("_type", "in", types))
        return preds, prefix, parent, types

    def _select(self, preds: list[Predicate], prefix: str, parent: Optional[str],
                types: Optional[list[str]], lsn: int, start_after: Optional[str] = None,
                person: str = OWNER) -> Iterator[dict]:
        """Every live item under `prefix` matching the conditions that the
        reader may see; in path order when read by path, else in the
        index's order. A condition on a field hidden from the reader
        matches nothing."""
        user_fields = [p.path for p in preds if not p.path.startswith("_")]

        def keep(doc: dict) -> bool:
            if not doc["_key"].startswith(prefix):
                return False
            if parent is not None and doc.get("_parent") != parent:
                return False
            if types is not None and doc.get("_type") not in types:
                return False
            if person != OWNER:
                access = self.access(person, doc["_key"])
                if not access.data:
                    return False
                if access.hidden and not all(access.sees_field(f) for f in user_fields):
                    return False
            return all(p.matches(doc, False) for p in preds)

        estimate = self.docs.estimate(preds, [kinds.ITEM]) if preds else None
        if estimate is not None and estimate <= INDEX_PATH_MAX:
            found = self.docs.candidates(preds, [kinds.ITEM])
            if found is not None:
                docs = []
                for o in found:
                    doc = self.docs.by_ordinal(o, lsn)
                    if doc is not None and doc.get("_kind") == kinds.ITEM and keep(doc):
                        doc.setdefault("_ord", o)
                        docs.append(doc)
                docs.sort(key=lambda d: d["_key"].encode())
                for doc in docs:
                    if start_after is None or doc["_key"].encode() > start_after.encode():
                        yield doc
                return
        start = prefix.encode() if start_after is None else max(prefix.encode(), start_after.encode() + b"\x00")
        for v in self.store.scan(DOCS, start, prefix_end(prefix.encode()), lsn):
            o, doc = decode_doc(v.value)
            if doc.get("_kind") != kinds.ITEM:
                continue
            doc["_lsn"], doc["_ord"] = v.lsn, o
            if keep(doc):
                yield doc

    def list(self, body: dict) -> dict:
        """A page of items under a folder (or with a parent), matching the
        conditions, in path order or by a field, with a cursor for the next
        page; only the items the reader may see."""
        person = self._reader(body)
        preds, prefix, parent, types = self._preds(body)
        limit = body.get("limit", 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= LIST_LIMIT_MAX:
            raise DocumentError(f"limit is 1 to {LIST_LIMIT_MAX}")
        streams = _streams(body.get("streams"))
        order = body.get("order")
        cursor = body.get("cursor")
        out: list[dict] = []
        with self.store.reading() as lsn:
            if order is None:
                after = _decode_cursor(cursor) if cursor else None
                if after is not None and not isinstance(after, str):
                    raise DocumentError("not a cursor this engine gave")
                for doc in self._select(preds, prefix, parent, types, lsn, after, person):
                    out.append(doc)
                    if len(out) > limit:
                        break
                more = len(out) > limit
                out = out[:limit]
                next_cursor = _encode_cursor(out[-1]["_key"]) if more and out else None
            else:
                if (not isinstance(order, list) or len(order) != 2 or not isinstance(order[0], str)
                        or order[1] not in ("asc", "desc")):
                    raise DocumentError("order is [field, asc or desc]")
                field, direction = order
                docs = list(self._select(preds, prefix, parent, types, lsn, person=person))
                if person != OWNER:
                    docs = [d if self.access(person, d["_key"]).sees_field(field)
                            else {k: v for k, v in d.items() if k != field.split(".")[0].split("[")[0]}
                            for d in docs]

                def key(d: dict):
                    return (_sort_key(d, field), d["_key"])
                docs.sort(key=key, reverse=direction == "desc")
                if cursor:
                    after = _decode_cursor(cursor)
                    if not isinstance(after, list) or len(after) != 2:
                        raise DocumentError("not a cursor this engine gave")
                    mark = (_sort_key({field: after[0]} if after[0] is not None else {}, field), after[1])
                    docs = [d for d in docs if (key(d) > mark if direction == "asc" else key(d) < mark)]
                more = len(docs) > limit
                out = docs[:limit]
                if more and out:
                    values = [v for p, v in kinds.flatten(out[-1]) if p == field]
                    next_cursor = _encode_cursor([values[0] if values else None, out[-1]["_key"]])
                else:
                    next_cursor = None
            items = []
            for doc in out:
                access = self.access(person, doc["_key"])
                content = None
                if doc.get("_content") is not None and access.content and \
                        ("content" in streams or "sections" in streams):
                    c = self.store.get(CONTENT, doc["_key"].encode(), lsn)
                    content = None if c is None or c.value is None else json.loads(c.value)
                view = self._shown(doc, content, streams, access)
                if "vectors" in streams:
                    view["vectors"] = self._vector_state(doc["_ord"], doc, access)
                items.append(view)
        return {"items": items, "cursor": next_cursor, "lsn": lsn}

    def count(self, body: dict) -> dict:
        """How many items match, each counted once; with `group_by`, how
        many have each value of that field (an item counted once per
        distinct value it holds; values `where` holds equal, text by its
        fold, are one group). Of the items with content the reader may
        read, `embedded` have their vectors for their current content and
        `waiting` do not yet.

        For the owner (every right, nothing hidden) the answer comes from
        the general index's postings and the resident key column, never
        decoding an item but the few a condition the index cannot answer
        exactly must be checked against (`_count_by_index`). Any other
        reader's count reads each matching item, so its rights and hidden
        fields apply to every one (`_count_each`)."""
        person = self._reader(body)
        preds, prefix, parent, types = self._preds(body)
        group_by = body.get("group_by")
        if group_by is not None and (not isinstance(group_by, str) or not group_by):
            raise DocumentError("group_by is a field")
        with self.store.reading() as lsn:
            if person == OWNER:
                user = preds[:len(preds) - (parent is not None) - (types is not None)]
                out = self._count_by_index(user, prefix, parent, types, group_by, lsn)
                if out is not None:
                    out["lsn"] = lsn
                    return out
            return self._count_each(preds, prefix, parent, types, group_by, person, lsn)

    def _count_each(self, preds, prefix, parent, types, group_by, person, lsn) -> dict:
        """The count read item by item, with the reader's rights on each."""
        total = embedded = waiting = 0
        groups: dict[str, list] = {}
        for doc in self._select(preds, prefix, parent, types, lsn, person=person):
            total += 1
            access = self.access(person, doc["_key"])
            if (doc.get("_content") is not None and access.content) or words_summary(doc) is not None:
                if self.vectors is not None and self.vectors.item_embedded(doc["_ord"]):
                    embedded += 1
                else:
                    waiting += 1
            if group_by is not None and access.sees_field(group_by):
                _add_groups(groups, doc, group_by)
        out = {"items": total, "embedded": embedded, "waiting": waiting, "lsn": lsn}
        if group_by is not None:
            out["groups"] = _ordered_groups(groups.values())
        return out

    def _count_by_index(self, preds: list[Predicate], prefix: str, parent: Optional[str],
                        types: Optional[list[str]], group_by: Optional[str], lsn: int) -> Optional[dict]:
        """The owner's count from the index alone: each condition's posting
        set (a `!=` or `exists: false` as what its opposite leaves), the
        item's place and type checked exactly from its path in the resident
        key column, `embedded` from the vector index's own state. Only items
        meeting a condition the index answers inexactly (a value cut to the
        index's bound, a date) are read to check it. None when the index
        cannot narrow at all."""
        docs = self.docs
        item_kind = [kinds.ITEM]
        sets: list[set[int]] = []
        minus: list[set[int]] = []
        check: list[Predicate] = []
        for p in preds:
            if p.op == "!=" or (p.op == "exists" and p.value is False):
                opposite = Predicate(p.path, "=", p.value) if p.op == "!=" else Predicate(p.path, "exists", True)
                found = docs.answer(opposite, item_kind)
                if found is not None and found[1]:
                    minus.append(found[0])
                else:
                    check.append(p)
                continue
            found = docs.answer(p, item_kind)
            if found is None:
                check.append(p)
                continue
            sets.append(found[0])
            if not found[1]:
                check.append(p)
        # Where the items are: the index's own path fields narrow (they are
        # folded, so every path is checked exactly below).
        place = [Predicate("_kind", "=", kinds.ITEM)]
        if prefix != "/":
            place.append(Predicate("_ancestors[]", "=", prefix[:-1]))
        if parent is not None:
            place.append(Predicate("_parent", "=", parent))
        if types is not None:
            place.append(Predicate("_type", "in", types))
        for p in place:
            found = docs.answer(p, item_kind)
            if found is not None:
                sets.append(found[0])
        if not sets:
            return None
        sets.sort(key=len)
        found = set(sets[0])
        for other in sets[1:]:
            if not found:
                break
            found &= other
        for other in minus:
            found -= other
        if prefix == "/" and parent is None and types is None:
            matched = found          # every item, by the exact `_kind` posting: nothing to check
        else:
            matched = self._placed(found, prefix, parent, types)
        held: dict[int, dict] = {}
        if check:
            kept = set()
            for o in matched:
                doc = docs.by_ordinal(o, lsn)
                if doc is not None and all(p.matches(doc, False) for p in check):
                    kept.add(o)
                    held[o] = doc
            matched = kept
        out: dict = {"items": len(matched)}
        with_content: set[int] = set()
        for field in ("_content.sha256", "_words.sha256"):
            found = docs.answer(Predicate(field, "exists"), item_kind)
            if found is not None:
                with_content |= matched & found[0]
        # Records written before records had vectors hold no `_words` yet:
        # read one by one (none once each has been written again).
        records = docs.answer(Predicate("_type", "in", sorted(lexical_record_types)), item_kind)
        wordless = docs.answer(Predicate("_words.none", "exists"), item_kind)
        for o in (matched & records[0]) - with_content - (wordless[0] if wordless else set()) \
                if records is not None else ():
            doc = held.get(o) or docs.by_ordinal(o, lsn)
            if doc is not None and words_summary(doc) is not None:
                with_content.add(o)
        embedded = 0
        if self.vectors is not None and with_content:
            embedded = sum(1 for o in with_content if self.vectors.item_embedded(o))
        out["embedded"], out["waiting"] = embedded, len(with_content) - embedded
        if group_by is not None:
            out["groups"] = self._groups_by_index(group_by, matched, held, lsn)
        return out

    def _placed(self, found: set[int], prefix: str, parent: Optional[str],
                types: Optional[list[str]]) -> set[int]:
        """Of `found`, the items under `prefix`, with `parent` and of
        `types`, checked exactly on the paths in the resident key column
        (bytes, as `paths.parent` and `paths.type_of` read them)."""
        wanted_prefix = prefix.encode()
        parent_b = None if parent is None else (b"" if parent == "/" else parent.encode())
        type_set = None if types is None else set(types)
        key_of = self.docs.columns.key
        out: set[int] = set()
        for o in found:
            key = key_of(o)
            if key is None or not key.startswith(wanted_prefix):
                continue
            if parent_b is not None or type_set is not None:
                cut = key.rfind(b"/")
                if parent_b is not None and key[:cut] != parent_b:
                    continue
                if type_set is not None:
                    stem, dot, suffix = key[cut + 1:].rpartition(b".")
                    word = suffix.decode()
                    kind = word.lower() if dot and stem and word and len(word) <= paths.TYPE_MAX \
                        else paths.FOLDER
                    if kind not in type_set:
                        continue
            out.add(o)
        return out

    def _groups_by_index(self, field: str, matched: set[int], held: dict[int, dict], lsn: int) -> list[dict]:
        """`group_by` from the field's index keys: each key's postings
        within the matching items. A number, bool or null is read back from
        its key; text is folded in the index, so one item of its group is
        read to show the value as written, and a key cut to the index's
        bound is split by reading its items."""
        docs = self.docs
        if not matched:
            return []
        groups: dict[str, list] = {}
        fid = docs.fields.get(field)
        if fid is None or not kinds.is_indexed(kinds.OPEN, field):
            if fid is None and not docs.fields_full and kinds.is_indexed(kinds.OPEN, field):
                return []                       # nobody has ever had this field
            for o in sorted(matched):
                _add_groups(groups, held.get(o) or docs.by_ordinal(o, lsn) or {}, field)
            return _ordered_groups(groups.values())

        def read(o: int) -> dict:
            if o not in held:
                held[o] = docs.by_ordinal(o, lsn) or {}
            return held[o]

        for key, ords in docs.by_key(FID.pack(fid), FID.pack(fid + 1)).items():
            ords = ords & matched
            if not ords:
                continue
            tag, payload = key[FID.size], key[FID.size + 1:]
            if tag == encoding.STRING:
                def same(v) -> bool:
                    return isinstance(v, str) and encoding.fold_string(v) == payload
                if encoding.is_cut(payload):
                    for o in sorted(ords):
                        _add_groups(groups, read(o), field, same)
                    continue
                value = None
                for o in sorted(ords):
                    value = next((v for p, v in kinds.flatten(read(o)) if p == field and same(v)), None)
                    if value is not None:
                        break
                if value is None:
                    continue
            elif tag == encoding.NUMBER:
                value = encoding.decode_number(payload)
                if value.is_integer() and abs(value) < 2 ** 53:
                    value = int(value)
            elif tag in (encoding.TRUE, encoding.FALSE):
                value = tag == encoding.TRUE
            elif tag == encoding.NULL:
                value = None
            else:
                continue
            groups.setdefault(_group_key(value), [value, 0])[1] += len(ords)
        return _ordered_groups(groups.values())

    def changes(self, body: dict) -> dict:
        """Every item transaction after the log number `since`, oldest
        first, at most `limit`; `next` is where the following read starts.
        A reader sees only the items it may read, and links whose two ends
        it may read."""
        person = self._reader(body)
        since = body.get("since", 0)
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise DocumentError("since is a log number")
        limit = body.get("limit", 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= CHANGES_LIMIT_MAX:
            raise DocumentError(f"limit is 1 to {CHANGES_LIMIT_MAX}")
        under = body.get("under")
        prefix = paths.under_prefix(under) if under else None
        out: list[dict] = []
        nxt = since
        scanned = 0

        def readable(path: str) -> bool:
            return (prefix is None or path.startswith(prefix)) and \
                (person == OWNER or self.access(person, path).data)

        with self.store.reading() as lsn:
            for v in self.store.scan(FEED, feed_key(since + 1), None, lsn):
                scanned += 1
                record = json.loads(v.value)
                nxt = FEED_KEY.unpack(v.key)[0]
                if prefix is not None or person != OWNER:
                    items = [i for i in record.get("items") or [] if readable(i["path"])]
                    links = [k for k in record.get("links") or []
                             if (prefix is None or k["from"].startswith(prefix) or k["to"].startswith(prefix))
                             and (person == OWNER or (self.access(person, k["from"]).data
                                                      and self.access(person, k["to"]).data))]
                    if not items and not links:
                        if scanned >= CHANGES_SCAN_MAX:
                            break
                        continue
                    record = {k: v for k, v in record.items() if k != "rights" or person == OWNER}
                    record = {**record, "items": items, **({"links": links} if links else {})}
                    if not links:
                        record.pop("links", None)
                out.append(record)
                if len(out) >= limit or scanned >= CHANGES_SCAN_MAX:
                    break
        return {"changes": out, "next": nxt, "lsn": lsn}

    # -- links ----------------------------------------------------------------------
    def links(self, body: dict) -> dict:
        """The item's links, each with its origin: its parent and children
        (hierarchy), and the references and derived links either way whose
        other end is an item now; only links whose two ends the reader may
        read."""
        person = self._reader(body)
        path = paths.check(body.get("path"))
        direction = body.get("direction", "both")
        if direction not in ("out", "in", "both"):
            raise DocumentError("direction is out, in or both")
        kind = body.get("kind")
        origins = body.get("origins")
        if origins is not None and (not isinstance(origins, list) or
                                    not all(o in (HIERARCHY, REFERENCE, DERIVED) for o in origins)):
            raise DocumentError("origins are some of hierarchy, reference, derived")
        limit = body.get("limit", 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= LINKS_LIMIT_MAX:
            raise DocumentError(f"limit is 1 to {LINKS_LIMIT_MAX}")
        out: list[dict] = []

        def wanted(origin: str, k: str) -> bool:
            return (origins is None or origin in origins) and (kind is None or k == kind)

        with self.store.reading() as lsn:
            def is_item(p: str) -> bool:
                if person != OWNER and not self.access(person, p).data:
                    return False
                v = self.store.get(DOCS, p.encode(), lsn)
                return v is not None and v.value is not None

            if not is_item(path):
                return {"path": path, "links": [], "lsn": lsn}
            parent = paths.parent(path)
            if direction in ("out", "both") and parent != "/" and wanted(HIERARCHY, "parent") and is_item(parent):
                out.append({"from": path, "kind": "parent", "to": parent, "origin": HIERARCHY})
            if direction in ("in", "both") and wanted(HIERARCHY, "parent"):
                for doc in self._select([Predicate("_parent", "=", path)], path + "/", path, None, lsn,
                                        person=person):
                    out.append({"from": doc["_key"], "kind": "parent", "to": path, "origin": HIERARCHY})
                    if len(out) >= limit:
                        break
            for fam, d in ((EDGES_OUT, "out"), (EDGES_IN, "in")):
                if direction not in (d, "both") or len(out) >= limit:
                    continue
                prefix = path.encode() + SEP
                for v in self.store.scan(fam, prefix, prefix_end(prefix), lsn):
                    _, k, other = split_key(v.key)
                    body_ = json.loads(v.value)
                    origin = body_.get("origin")
                    if origin not in (REFERENCE, DERIVED) or not wanted(origin, k):
                        continue
                    if not kinds.is_item_key(other) or not is_item(other):
                        continue
                    link = {"from": path if d == "out" else other, "kind": k,
                            "to": other if d == "out" else path, **body_}
                    out.append(link)
                    if len(out) >= limit:
                        break
        return {"path": path, "links": out[:limit], "lsn": lsn}

    # -- search: four indexes, one hit an item -----------------------------------
    def search(self, body: dict) -> dict:
        """Items by meaning (the whole-item vectors, the sections saying
        where), by words and exact phrase (the full-text index over their
        content), by field values (the data index) and by links (the
        graph): one hit an item, `matched_by` naming every index that found
        it. `where`, `phrase`, `linked_to`, `under` and `types` are
        conditions every hit meets; `meaning` and `words` rank. Vectors and
        full text need the content right; a hidden field matches nothing."""
        person = self._reader(body)
        limit = body.get("limit", 20)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= SEARCH_LIMIT_MAX:
            raise DocumentError(f"limit is 1 to {SEARCH_LIMIT_MAX}")
        meaning, phrase, words = (_query_text(body.get(k), k) for k in ("meaning", "phrase", "words"))
        linked_to = body.get("linked_to")
        if linked_to is not None:
            paths.check(linked_to)
        budget = body.get("budget_ms", SEARCH_BUDGET_MS)
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 < budget <= 30000:
            raise DocumentError("budget_ms is 1 to 30000")
        end = time.monotonic() + budget / 1000.0
        preds, prefix, _, types = self._preds(body)
        has_where = bool(body.get("where"))
        matched: dict[str, set] = {}
        where: dict[str, set] = {}
        ords: dict[str, int] = {}
        partial = False
        notes: list[str] = []
        with self.store.reading() as lsn:
            cond: Optional[set[str]] = None
            if preds or not (meaning or words or phrase or linked_to):
                cond = set()
                for doc in self._select(preds, prefix, None, types, lsn, person=person):
                    cond.add(doc["_key"])
                    ords[doc["_key"]] = doc["_ord"]
                    if has_where:
                        matched.setdefault(doc["_key"], set()).add("data")
            if linked_to is not None:
                found = {(k["to"] if k["from"] == linked_to else k["from"])
                         for k in self.links({"caller": {"person": person}, "path": linked_to,
                                              "limit": LINKS_LIMIT_MAX})["links"]}
                found = {p for p in found if p.startswith(prefix)}
                cond = found if cond is None else cond & found
                for p in found:
                    matched.setdefault(p, set()).add("graph")

            def readable(doc: Optional[dict]) -> bool:
                """An item found by its content (the content right), or a
                record found by its own words (the data right)."""
                if doc is None or doc.get("_kind") != kinds.ITEM or not doc["_key"].startswith(prefix):
                    return False
                if types is not None and doc.get("_type") not in types:
                    return False
                if cond is not None and doc["_key"] not in cond:
                    return False
                access = self.access(person, doc["_key"])
                if doc.get("_content") is not None:
                    return access.content
                return access.data and doc.get("_type") in lexical_record_types

            def record_words(doc: dict) -> str:
                """A record's words as this reader may see them."""
                access = self.access(person, doc["_key"])
                return lexical_record_text(doc, access.sees_field if access.hidden else None)

            def sections_of(path: str) -> list[dict]:
                c = self.store.get(CONTENT, path.encode(), lsn)
                return [] if c is None or c.value is None else json.loads(c.value).get("sections") or []

            if phrase:
                hits = set()
                candidates = self.lexical.phrase_candidates(phrase) if self.lexical is not None else None
                docs: Iterator[dict]
                if candidates is None:
                    notes.append("the phrase has no indexed word: items were read one by one")
                    docs = (d for d in self._select([], prefix, None, types, lsn, person=person))
                else:
                    docs = (self.docs.by_ordinal(o, lsn) for o in sorted(candidates))
                for doc in docs:
                    if time.monotonic() > end:
                        partial = True
                        break
                    if not readable(doc):
                        continue
                    if doc.get("_content") is None:
                        found_in = lexical_phrase_in(phrase, record_words(doc))
                        at = []
                    else:
                        at = [s["index"] for s in sections_of(doc["_key"])
                              if lexical_phrase_in(phrase, f"{s.get('heading') or ''}\n{s.get('text') or ''}")]
                        found_in = bool(at)
                    if found_in:
                        hits.add(doc["_key"])
                        ords[doc["_key"]] = doc.get("_ord", self.docs._ord_from_store(doc["_key"].encode()))
                        where.setdefault(doc["_key"], set()).update(at)
                        matched.setdefault(doc["_key"], set()).add("fulltext")
                cond = hits if cond is None else cond & hits

            rankings: list[list[str]] = []
            if words and self.lexical is not None:
                ranked = []
                query_terms = set(lexical_terms(words))
                for o, _, _ in self.lexical.search(words, k=max(limit * 5, 50), items=True):
                    doc = self.docs.by_ordinal(o, lsn)
                    if not readable(doc):
                        continue
                    if doc.get("_content") is None and person != OWNER and \
                            not query_terms & set(lexical_terms(record_words(doc))):
                        continue                  # found only by a field hidden from this reader
                    path = doc["_key"]
                    ords[path] = o
                    ranked.append(path)
                    matched.setdefault(path, set()).add("fulltext")
                    for s in sections_of(path):
                        if query_terms & set(lexical_terms(f"{s.get('heading') or ''} {s.get('text') or ''}")):
                            where.setdefault(path, set()).add(s["index"])
                rankings.append(ranked)
            if meaning:
                if self.vectors is None or self.vectors.embedder is None:
                    notes.append("no embedding model: nothing was searched by meaning")
                else:
                    vector = self.vectors.embed_query(meaning, timeout=max(end - time.monotonic(), 0.05))
                    if vector is None:
                        partial = True
                        notes.append("the question was not embedded in time")
                    else:
                        within = None
                        if cond is not None:
                            within = {ords.get(p) or self.docs._ord_from_store(p.encode()) for p in cond}
                            within.discard(None)
                        elif person != OWNER or prefix != "/" or types is not None:
                            within = {d["_ord"] for d in self._select([], prefix, None, types, lsn, person=person)
                                      if embedded_summary(d) is not None and readable(d)}
                        found = self.vectors.search_items(vector, k=max(limit * 5, 50), within=within)
                        partial |= bool(found.get("partial"))
                        ranked = []
                        for o, _, at in found["hits"]:
                            doc = self.docs.by_ordinal(o, lsn)
                            if not readable(doc):
                                continue
                            if doc.get("_content") is None and self.access(person, doc["_key"]).hidden:
                                continue          # a record's vectors hold every field, hidden ones too
                            path = doc["_key"]
                            ranked.append(path)
                            matched.setdefault(path, set()).add("vector")
                            where.setdefault(path, set()).update(at)
                        rankings.append(ranked)
            scores: dict[str, float] = {}
            if rankings:
                for ranked in rankings:
                    for rank, path in enumerate(ranked):
                        scores[path] = scores.get(path, 0.0) + 1.0 / (RRF_K + rank + 1)
                if cond is not None:
                    scores = {p: s for p, s in scores.items() if p in cond}
            else:
                scores = {p: 0.0 for p in (cond or set())}
            for p in scores:
                scores[p] += len(matched.get(p, ())) * 1e-6
            best = sorted(scores.items(), key=lambda ps: (-ps[1], ps[0]))[:limit]
        return {"hits": [{"path": p, "score": round(s, 6), "sections": sorted(where.get(p, ())),
                          "matched_by": sorted(matched.get(p, ()))} for p, s in best],
                "partial": partial, "notes": notes, "lsn": lsn}

    # -- proposals: what an AI suggests waits for the owner's yes ----------------
    def propose_link(self, body: dict) -> dict:
        """An AI's link, kept as a proposal (an item under `/proposals/links`)
        with the agent, the person, the model and the time, until the owner
        accepts or refuses it. It proposes only between items its person
        may read."""
        caller = caller_of(body.get("by"), "by")
        src, dst, kind = body.get("from"), body.get("to"), body.get("kind")
        paths.check(src)
        paths.check(dst)
        if not isinstance(kind, str) or not kind or len(kind) > LINK_KIND_MAX:
            raise DocumentError(f"a link's kind is text of at most {LINK_KIND_MAX} characters")
        model = body.get("model") or caller.get("model")
        if not isinstance(model, str) or not model:
            raise DocumentError("a proposal records the model that made it")
        weight = body.get("weight", 1.0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0.0 <= weight <= 1.0:
            raise DocumentError("weight is 0 to 1")
        for end in (src, dst):
            if self.get(end, person=caller["person"]) is None:
                raise ConditionFailed(end, "an item", 0, f"{end} is not an item to link")
        ident = hashlib.sha256(f"{src}\x00{kind}\x00{dst}\x00{time.time_ns()}".encode()).hexdigest()[:16]
        path = f"{PROPOSALS}/{ident}.proposal"
        data = {"from": src, "kind": kind, "to": dst, "weight": float(weight), "model": model,
                "person": caller["person"], "state": "proposed", "at": self._now()}
        if caller.get("agent"):
            data["agent"] = caller["agent"]
        if caller.get("model") and caller["model"] != model:
            data["agent_model"] = caller["model"]
        why = _words(body.get("why"), "why")
        if why:
            data["why"] = why
        # The proposal is the record of the AI's act, written for it by the
        # engine whoever may write under /proposals.
        done = self._commit([{"op": "put", "path": path, "data": data, "absent": True}], caller,
                            "proposal", f"a {kind} link proposed", False)
        return {"path": path, "lsn": done["lsn"], **data}

    def proposals(self, body: dict) -> dict:
        """The proposals the reader may see (both ends readable), newest
        first, waiting ones unless `state` says otherwise."""
        person = self._reader(body)
        state = body.get("state", "proposed")
        out = []
        with self.store.reading() as lsn:
            for doc in self._select([Predicate("state", "=", state)] if state else [],
                                    PROPOSALS + "/", None, None, lsn, person=person):
                if person != OWNER and not (self.access(person, doc["from"]).data
                                            and self.access(person, doc["to"]).data):
                    continue
                out.append({"path": doc["_key"], **{k: v for k, v in doc.items() if not k.startswith("_")}})
        out.sort(key=lambda p: -float(p.get("at") or 0))
        return {"proposals": out, "lsn": lsn}

    def answer_proposal(self, body: dict) -> dict:
        """The owner's yes or no to a proposal: a yes writes the link,
        recording the model, the agent and the person that proposed it."""
        writer = _writer(body)
        if writer["person"] != OWNER:
            raise Denied("only the owner accepts or refuses a proposal")
        path = paths.check(body.get("path"))
        action = body.get("action")
        if action not in ("accept", "refuse"):
            raise DocumentError("action is accept or refuse")
        current = self.get(path)
        if current is None or not path.startswith(PROPOSALS + "/"):
            raise ConditionFailed(path, "a proposal", 0, f"{path} is not a proposal")
        p = current["data"]
        if p.get("state") != "proposed":
            raise ConditionFailed(path, "proposed", current["version"], f"{path} is already {p.get('state')}")
        writes = [{"op": "merge", "path": path, "set": {"state": "accepted" if action == "accept" else "refused",
                                                         "answered_at": self._now()},
                   "expect_version": current["version"]}]
        if action == "accept":
            writes.append({"op": "link", "from": p["from"], "kind": p["kind"], "to": p["to"],
                           "model": p["model"], "weight": p.get("weight", 1.0)})
        return self._commit(writes, writer, "proposal " + action, None, False,
                            proposal={"path": path, "at": p.get("at"), "person": p.get("person"),
                                      "agent": p.get("agent")})

    # -- what the engine's health says ------------------------------------------
    def health(self) -> dict:
        return {"last_write_at": self.last_at or None, "rights_paths": len(self.rights.grants)}


def _group_key(value: Any) -> str:
    """Values `where` holds equal are one group: text by its fold, a number
    by its value (3 and 3.0 alike)."""
    if isinstance(value, str):
        return "s" + spoken_words.fold(value)
    if isinstance(value, bool) or value is None:
        return "b" + json.dumps(value)
    if isinstance(value, (int, float)):
        return "n" + repr(float(value))
    return "o" + encoding.canonical(value).decode()


def _add_groups(groups: dict, doc: dict, field: str, keep=None) -> None:
    """The distinct values (by `_group_key`) the item holds at `field`, each
    counting it once."""
    values: dict[str, Any] = {}
    for p, v in kinds.flatten(doc):
        if p == field and (keep is None or keep(v)):
            values.setdefault(_group_key(v), v)
    for g, v in values.items():
        groups.setdefault(g, [v, 0])[1] += 1


def _ordered_groups(groups) -> list[dict]:
    return sorted(({"value": v, "items": n} for v, n in groups),
                  key=lambda g: (-g["items"], json.dumps(g["value"], sort_keys=True)))


def _query_text(value, what: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > QUERY_TEXT_MAX:
        raise DocumentError(f"{what} is text of at most {QUERY_TEXT_MAX} characters")
    return value
