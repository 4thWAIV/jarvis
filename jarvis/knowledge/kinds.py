"""Each document kind's small schema (design sections 7.4 and 7.17.1), and
the open schema of an item at a path (UDS).

Two kinds of document share the store while the engine moves to UDS. A
document keyed by a path (`/work/w-1.work`, see `paths.py`) is an item: its
`_kind` is always `ITEM`, its type is its path's suffix (`_type`), and it
holds fields of any shape, every one indexed (`OPEN`). Any other key is a
legacy document of today's ingest, held to its kind's schema below until
round 2 replaces that ingest: which properties a kind may hold, which of
them name other documents (written as graph edges, never kept as
properties), which are dates, which are embedded into vectors, and, for a
kind written often and queried by few properties, the short list it
indexes instead of every one.

`schema_of(doc)` is the one way to the schema that applies to a document.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SYSTEM = ("_key", "_kind", "_label", "_status", "_trust", "_owner", "_ns",
          "_source", "_rev", "_hash")
# An item's own system fields: its path entry (`_type`, `_parent`, `_name`,
# `_ancestors`, `_depth`), its version and who wrote it and why (`_version`,
# `_created`, `_at`, `_by`, `_agent`, `_model`, `_change`, `_summary`), and
# what its content stream holds (`_content`: hash, size, reference).
ITEM_SYSTEM = ("_type", "_parent", "_name", "_ancestors", "_depth", "_version", "_created",
               "_at", "_by", "_agent", "_model", "_change", "_summary", "_content")
ITEM = "item"
READ_ONLY = ("_lsn", "_ord")
TRUST = ("own", "untrusted")
INDEXED_SYSTEM = tuple(p for p in SYSTEM) + tuple(p for p in ITEM_SYSTEM if p != "_content") + (
    "_ancestors[]", "_content.sha256", "_content.ref")


@dataclass(frozen=True)
class Kind:
    name: str
    properties: frozenset = frozenset()
    references: dict = field(default_factory=dict)   # property -> edge kind
    dates: frozenset = frozenset()
    embedded: tuple = ()
    indexed: Optional[tuple] = None                  # None: every property


def _kind(name, props=(), refs=None, dates=(), embedded=(), indexed=None) -> Kind:
    refs = dict(refs or {})
    return Kind(name, frozenset(props) | frozenset(refs), refs, frozenset(dates),
                tuple(embedded), None if indexed is None else tuple(indexed))


KINDS: dict[str, Kind] = {k.name: k for k in (
    _kind("workspace", ("node_id",)),
    _kind("person", ("github_id", "login", "aliases")),
    _kind("project", ("aliases", "spoken_key", "summary"), {"folders": "has_folder"}),
    _kind("folder", ("role", "label", "available")),
    _kind("work", ("outcome",), {"projects": "involves"}),
    _kind("accomplishment", ("title",), {"evidence": "evidenced_by"}, dates=("confirmed_at",)),
    _kind("topic", ("aliases", "pinned", "stages", "front")),
    _kind("decision", ("question", "options", "answered"), {"decided_in": "decided_in"}),
    _kind("document", ("path", "media_type", "language", "bytes", "modified", "headings",
                       "format", "pages", "blocked_resources"),
          {"folder": "in_folder"}, dates=("modified",)),
    # `at`: when what it holds was said or written (epoch seconds), for
    # "newest first" and "before then"; `agent`: who said it.
    _kind("chunk", ("text", "span", "heading_path", "language", "at"),
          {"document": "in_document", "agent": "said_by"}, embedded=("text",),
          indexed=("language", "heading_path[]", "at")),
    _kind("agent", ("name", "aliases"), {"folder": "ran_in", "work": "for_work"}),
    _kind("run", ("state",), {"folder": "ran_in", "work": "for_work"}),
    _kind("workflow", ("state",), {"work": "for_work"}),
    _kind("package", ("name", "version", "runs", "references")),
    _kind("message", ("direction",)),
    _kind("remote", ("carrier", "origin", "inner_kind")),
)}


OPEN = Kind(ITEM)
"""An item's schema: no property list, every field indexed, no reference
properties (an item's references are found in its fields, `items.py`)."""


class SchemaError(ValueError):
    pass


def kind_of(name: str) -> Kind:
    try:
        return KINDS[name]
    except KeyError:
        raise SchemaError(f"unknown kind {name!r}")


def is_item_key(key) -> bool:
    """Whether a document key is an item's path (bytes or text)."""
    return (key[:1] == b"/") if isinstance(key, (bytes, bytearray)) else str(key).startswith("/")


def schema_of(doc: dict) -> Kind:
    """The schema that applies to `doc`: the open one for an item, its
    kind's for a legacy document."""
    if doc.get("_kind") == ITEM:
        return OPEN
    return kind_of(doc["_kind"])


def flatten(value, path: str = ""):
    """Every (path, scalar) in a property value: nested objects by full
    path (`front.status`), array elements under the array's path
    (`headings[]`)."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from flatten(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for v in value:
            yield from flatten(v, f"{path}[]")
    else:
        yield path, value


def root_of(path: str) -> str:
    return path.split(".", 1)[0].split("[", 1)[0]


def is_indexed(kind: Kind, path: str) -> bool:
    if path.startswith("_"):
        return path in INDEXED_SYSTEM
    if kind.indexed is None:
        return True
    return path in kind.indexed or root_of(path) in kind.indexed
