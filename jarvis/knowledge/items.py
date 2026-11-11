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

from jarvis.knowledge import encoding, kinds, paths
from jarvis.knowledge.documents import (DOC_HEAD, DocumentError, DocumentStore, Predicate,
                                        decode_doc, _sort_key)
from jarvis.knowledge.graph import (DERIVED, HIERARCHY, REFERENCE, SEP, Graph, edge_key,
                                    split_key)
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
STREAMS = ("entry", "data", "content", "sections")
DEFAULT_STREAMS = ("entry", "data")
OPS = ("put", "merge", "delete", "link", "unlink")
LINK_KIND_MAX = 200
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
    for k in ("ref", "sha256", "media_type"):
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
            out["sections"].append({"index": i, "heading": heading, "text": s["text"]})
    out.setdefault("sha256", hashlib.sha256(text.encode("utf-8")).hexdigest())
    return out


def _content_summary(content: dict) -> dict:
    summary = {"sha256": content["sha256"], "chars": len(content["text"]),
               "sections": len(content["sections"])}
    for k in ("ref", "media_type"):
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
    def __init__(self, docs: DocumentStore, graph: Graph) -> None:
        self.docs = docs
        self.graph = graph
        self.store = docs.store
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
            if w["op"] in ("put", "merge", "delete"):
                path = paths.check(w.get("path"))
                if path in seen:
                    raise DocumentError(f"{path} is written once a transaction")
                seen.add(path)
        result: dict = {}

        def prepare(lsn: int) -> list[Op]:
            ops, done = self._plan(writes, writer, change, summary, lsn)
            result.update(done)
            return ops

        lsn = self.store.commit(prepare=prepare, full=durable == "full")
        if not result:
            return {"lsn": lsn, "at": None, "items": [], "links": 0}
        return {"lsn": lsn, **result}

    def _plan(self, writes: list[dict], writer: dict, change, summary, lsn: int) -> tuple[list[Op], dict]:
        at = self._now()
        puts: list[dict] = []
        deletes: list[str] = []
        content_ops: list[Op] = []
        changed: list[dict] = []
        made: dict[str, Optional[dict]] = {}     # path -> its document after this write
        for w in writes:
            op = w["op"]
            if op not in ("put", "merge", "delete"):
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
            if change is not None:
                new["_change"] = change
            if summary is not None:
                new["_summary"] = summary
            if "content" in w:
                content = _content(w["content"])
                if content is not None and kind == paths.FOLDER:
                    raise DocumentError(f"{path} is a folder, and a folder has no content")
                if content is None:
                    if doc is not None and doc.get("_content") is not None:
                        content_ops.append(Op(CONTENT, path.encode(), None))
                else:
                    new["_content"] = _content_summary(content)
                    content_ops.append(Op(CONTENT, path.encode(), encoding.canonical(content)))
            elif doc is not None and doc.get("_content") is not None:
                new["_content"] = doc["_content"]
            if len(encoding.canonical(new)) > DOC_MAX_BYTES:
                raise DocumentError(f"{path}: an item's document is at most {DOC_MAX_BYTES >> 20} MB")
            puts.append(new)
            made[path] = new
            changed.append({"path": path, "op": op, "version": number + 1, "type": kind})
        link_ops, links = self._link_ops(writes, writer, at, made)
        if not changed and not links:
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
        feed = Op(FEED, feed_key(lsn), encoding.canonical(record))
        return ops + content_ops + link_ops + [feed], {"at": at, "items": changed, "links": len(links)}

    def _is_item(self, path: str, made: dict) -> bool:
        if path in made:
            return made[path] is not None
        v = self.store.get(DOCS, path.encode())
        return v is not None and v.value is not None

    def _link_ops(self, writes: list[dict], writer: dict, at: float, made: dict) -> tuple[list[Op], list[dict]]:
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
            if w["op"] == "unlink":
                ops += self.graph._edge_ops(src, kind, dst, None)
                links.append({"op": "unlink", "from": src, "kind": kind, "to": dst})
                continue
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
            ops += self.graph._edge_ops(src, kind, dst, value)
            links.append({"op": "link", "from": src, "kind": kind, "to": dst, "model": model})
        return ops, links

    # -- reading one item -------------------------------------------------------
    def get(self, path: str, *, version: Optional[int] = None, at: Optional[float] = None,
            streams=None) -> Optional[dict]:
        """The item now, at a version, or as it stood at a time; None when
        it is not (or was not then) an item."""
        paths.check(path)
        streams = _streams(streams)
        if version is not None and at is not None:
            raise DocumentError("a version or a time, not both")
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
            if doc.get("_content") is not None and ("content" in streams or "sections" in streams):
                c = self.store.get(CONTENT, path.encode(), found.lsn)
                content = None if c is None or c.value is None else json.loads(c.value)
            return item_view(doc, content, streams)

    def history(self, path: str, *, limit: int = 100, before: Optional[int] = None) -> dict:
        """Each version, newest first: its number, when, by whom, what kind
        of change and its summary; a delete is a version too."""
        paths.check(path)
        if not 0 < limit <= HISTORY_LIMIT_MAX:
            raise DocumentError(f"limit is 1 to {HISTORY_LIMIT_MAX}")
        out = []
        for v in self._versions(path):
            if v.value is None:
                entry = self._tombstone_entry(path, v.lsn)
            else:
                doc = decode_doc(v.value)[1]
                entry = {"version": doc.get("_version"), "lsn": v.lsn, "at": doc.get("_at"),
                         "by": doc.get("_by"), "agent": doc.get("_agent"), "model": doc.get("_model"),
                         "change": doc.get("_change"), "summary": doc.get("_summary"), "op": "put"}
            if before is not None and (entry.get("version") or 0) >= before:
                continue
            out.append(entry)
            if len(out) > limit:
                break
        more = len(out) > limit
        out = out[:limit]
        return {"path": path, "versions": out,
                "before": out[-1]["version"] if more and out else None}

    def diff(self, path: str, a: int, b: Optional[int] = None) -> dict:
        """What changed in the item's data between version `a` and version
        `b` (the current one when left out), and whether its content did."""
        first = self.get(path, version=a, streams=["entry", "data"])
        second = self.get(path, streams=["entry", "data"]) if b is None else \
            self.get(path, version=b, streams=["entry", "data"])
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
                types: Optional[list[str]], lsn: int, start_after: Optional[str] = None) -> Iterator[dict]:
        """Every live item under `prefix` matching the conditions; in path
        order when read by path, else in the index's order."""
        def keep(doc: dict) -> bool:
            if not doc["_key"].startswith(prefix):
                return False
            if parent is not None and doc.get("_parent") != parent:
                return False
            if types is not None and doc.get("_type") not in types:
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
        page."""
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
                for doc in self._select(preds, prefix, parent, types, lsn, after):
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
                docs = list(self._select(preds, prefix, parent, types, lsn))

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
                content = None
                if doc.get("_content") is not None and ("content" in streams or "sections" in streams):
                    c = self.store.get(CONTENT, doc["_key"].encode(), lsn)
                    content = None if c is None or c.value is None else json.loads(c.value)
                items.append(item_view(doc, content, streams))
        return {"items": items, "cursor": next_cursor, "lsn": lsn}

    def count(self, body: dict) -> dict:
        """How many items match, each counted once; with `group_by`, how
        many have each value of that field (an item counted once per
        distinct value it holds). Items with content and no vectors yet
        count as waiting (vectors are round 2)."""
        preds, prefix, parent, types = self._preds(body)
        group_by = body.get("group_by")
        if group_by is not None and (not isinstance(group_by, str) or not group_by):
            raise DocumentError("group_by is a field")
        total = with_content = 0
        groups: dict[str, list] = {}
        with self.store.reading() as lsn:
            for doc in self._select(preds, prefix, parent, types, lsn):
                total += 1
                if doc.get("_content") is not None:
                    with_content += 1
                if group_by is not None:
                    values = {encoding.canonical(v): v for p, v in kinds.flatten(doc) if p == group_by}
                    for enc, v in values.items():
                        groups.setdefault(enc.decode(), [v, 0])[1] += 1
        out = {"items": total, "embedded": 0, "waiting": with_content, "lsn": lsn}
        if group_by is not None:
            out["groups"] = sorted(({"value": v, "items": n} for v, n in groups.values()),
                                   key=lambda g: (-g["items"], json.dumps(g["value"], sort_keys=True)))
        return out

    def changes(self, body: dict) -> dict:
        """Every item transaction after the log number `since`, oldest
        first, at most `limit`; `next` is where the following read starts."""
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
        with self.store.reading() as lsn:
            for v in self.store.scan(FEED, feed_key(since + 1), None, lsn):
                scanned += 1
                record = json.loads(v.value)
                nxt = FEED_KEY.unpack(v.key)[0]
                if prefix is not None:
                    items = [i for i in record.get("items") or [] if i["path"].startswith(prefix)]
                    links = [k for k in record.get("links") or []
                             if k["from"].startswith(prefix) or k["to"].startswith(prefix)]
                    if not items and not links:
                        if scanned >= CHANGES_SCAN_MAX:
                            break
                        continue
                    record = {**record, "items": items, **({"links": links} if links else {})}
                out.append(record)
                if len(out) >= limit or scanned >= CHANGES_SCAN_MAX:
                    break
        return {"changes": out, "next": nxt, "lsn": lsn}

    # -- links ----------------------------------------------------------------------
    def links(self, body: dict) -> dict:
        """The item's links, each with its origin: its parent and children
        (hierarchy), and the references and derived links either way whose
        other end is an item now."""
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
                v = self.store.get(DOCS, p.encode(), lsn)
                return v is not None and v.value is not None

            if not is_item(path):
                return {"path": path, "links": [], "lsn": lsn}
            parent = paths.parent(path)
            if direction in ("out", "both") and parent != "/" and wanted(HIERARCHY, "parent") and is_item(parent):
                out.append({"from": path, "kind": "parent", "to": parent, "origin": HIERARCHY})
            if direction in ("in", "both") and wanted(HIERARCHY, "parent"):
                for doc in self._select([Predicate("_parent", "=", path)], path + "/", path, None, lsn):
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

    # -- what the engine's health says ------------------------------------------
    def health(self) -> dict:
        return {"last_write_at": self.last_at or None}
