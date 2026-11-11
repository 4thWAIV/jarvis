"""The UDS interface in Python: Tim's approved logical design
(PYTHON-DESIGN.md of the package uds-logical-data-capabilities, section 2)
over the knowledge engine.

One item lives at one path, and its data, content, sections and vectors are
streams of that one item, counted once. `UDS` is the protocol; `EngineUDS`
is the one implementation, which reaches the engine through a
`knowledge_client` transport: `HTTPTransport` to the engine process, or
`InProcessTransport` over an engine opened in this process
(`in_process`), which runs exactly the routes HTTP runs, so a test of the
interface is a test of the wire.

Round 1 (engine-core) builds paths, documents of any shape, content with
sections, transactions with conditions, versions with history, `get_at`
and `diff`, ordered lists, counts, the change feed, and hierarchy,
reference and derived links. Search by meaning, phrase and words, vectors,
AccessRights and the security gate, and AI proposals are round 2: until
then every caller reads as Tim, and the calls that need them say so.
"""
from __future__ import annotations

import contextlib
import enum
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path as FsPath
from typing import Any, Iterator, Mapping, Optional, Protocol

Path = str
KEEP = object()
"""`put`'s content left as it is: the item's current content stays."""


class Stream(enum.Enum):
    ENTRY = "entry"          # path, parent, type, version, who wrote it and why
    DATA = "data"            # structured fields about the item
    CONTENT = "content"      # its text and file reference; folders have none
    SECTIONS = "sections"    # the content split at headings; belongs to the item
    VECTORS = "vectors"      # one whole-item vector + one per section (round 2)
    RIGHTS = "rights"        # who may read and write which stream (round 2)


class Right(enum.Enum):
    READ_DATA = "read_data"
    READ_CONTENT = "read_content"
    WRITE = "write"


@dataclass(frozen=True)
class Caller:
    person: str                       # whose rights apply, and who wrote it
    agent: Optional[str] = None       # set when an AI acts for the person
    model: Optional[str] = None

    def by(self) -> dict:
        out = {"person": self.person}
        if self.agent:
            out["agent"] = self.agent
        if self.model:
            out["model"] = self.model
        return out


@dataclass(frozen=True)
class Section:
    index: int
    heading: str
    text: str


@dataclass(frozen=True)
class Item:
    path: Path
    kind: str                          # the path's type suffix; "folder" for none
    version: int
    data: Optional[Mapping[str, Any]] = None
    entry: Optional[Mapping[str, Any]] = None
    content: Optional[Mapping[str, Any]] = None   # text, ref, sha256, media_type
    sections: Optional[tuple[Section, ...]] = None
    lsn: Optional[int] = None

    @property
    def owner(self) -> Optional[str]:
        return (self.entry or {}).get("by")

    @classmethod
    def from_wire(cls, raw: dict) -> "Item":
        sections = raw.get("sections")
        return cls(path=raw["path"], kind=raw["type"], version=raw["version"], data=raw.get("data"),
                   entry=raw.get("entry"), content=raw.get("content"), lsn=raw.get("lsn"),
                   sections=None if sections is None else tuple(
                       Section(s["index"], s.get("heading") or "", s["text"]) for s in sections))


@dataclass(frozen=True)
class Counts:
    items: int
    embedded: int
    waiting: int
    groups: Optional[tuple[tuple[Any, int], ...]] = None


@dataclass(frozen=True)
class Version:
    version: Optional[int]
    lsn: int
    at: Optional[float]
    op: str                            # put, merge or delete
    by: Optional[str] = None
    agent: Optional[str] = None
    model: Optional[str] = None
    change: Optional[str] = None
    summary: Optional[str] = None


@dataclass(frozen=True)
class Link:
    source: Path
    kind: str
    target: Path
    origin: str                        # hierarchy, reference or derived
    model: Optional[str] = None
    at: Optional[float] = None
    weight: float = 1.0


@dataclass(frozen=True)
class Page:
    items: tuple[Item, ...]
    cursor: Optional[str]
    lsn: int


@dataclass(frozen=True)
class Query:
    meaning: Optional[str] = None           # vector index (round 2)
    where: Mapping[str, Any] = field(default_factory=dict)   # data index: field = value
    phrase: Optional[str] = None            # full-text index (round 2)
    words: Optional[str] = None             # full-text index (round 2)
    linked_to: Optional[Path] = None        # graph index
    under: Path = "/"
    limit: int = 20


@dataclass(frozen=True)
class Hit:
    path: Path
    score: float
    sections: tuple[int, ...]
    matched_by: frozenset


class Conflict(RuntimeError):
    """A transaction's condition did not hold; nothing was written."""

    def __init__(self, detail: str, path: Optional[str], expected: Any, found: Optional[int]) -> None:
        super().__init__(detail)
        self.path, self.expected, self.found = path, expected, found


class NotYet(NotImplementedError):
    """A UDS capability a later round of the plan builds."""


class Transaction:
    """Writes gathered for one atomic commit: `uds.transaction(caller)`."""

    def __init__(self) -> None:
        self.writes: list[dict] = []

    def put(self, path: Path, data: Mapping[str, Any], *, content: Any = KEEP,
            expect_version: Optional[int] = None, absent: bool = False) -> "Transaction":
        w = {"op": "put", "path": path, "data": dict(data)}
        if content is not KEEP:
            w["content"] = content
        return self._conditions(w, expect_version, absent)

    def merge(self, path: Path, set_: Optional[Mapping[str, Any]] = None, unset: tuple = (), *,
              expect_version: Optional[int] = None) -> "Transaction":
        return self._conditions({"op": "merge", "path": path, "set": dict(set_ or {}),
                                 "unset": list(unset)}, expect_version, False)

    def delete(self, path: Path, *, expect_version: Optional[int] = None) -> "Transaction":
        return self._conditions({"op": "delete", "path": path}, expect_version, False)

    def link(self, source: Path, kind: str, target: Path, *, model: str, weight: float = 1.0) -> "Transaction":
        self.writes.append({"op": "link", "from": source, "kind": kind, "to": target,
                            "model": model, "weight": weight})
        return self

    def unlink(self, source: Path, kind: str, target: Path) -> "Transaction":
        self.writes.append({"op": "unlink", "from": source, "kind": kind, "to": target})
        return self

    def _conditions(self, w: dict, expect_version, absent) -> "Transaction":
        if expect_version is not None:
            w["expect_version"] = expect_version
        if absent:
            w["absent"] = True
        self.writes.append(w)
        return self


class UDS(Protocol):
    def put(self, caller: Caller, path: Path, *, data: Mapping[str, Any], content: Any = KEEP,
            expect_version: Optional[int] = None) -> Item: ...
    def get(self, caller: Caller, path: Path, streams: set[Stream] = ...,
            version: Optional[int] = None) -> Optional[Item]: ...
    def get_at(self, caller: Caller, path: Path, when: datetime | float,
               streams: set[Stream] = ...) -> Optional[Item]: ...
    def history(self, caller: Caller, path: Path) -> list[Version]: ...
    def diff(self, caller: Caller, path: Path, a: int, b: Optional[int] = None) -> dict: ...
    def delete(self, caller: Caller, path: Path, *, expect_version: Optional[int] = None) -> None: ...
    def count(self, caller: Caller, under: Path = "/", **kw) -> Counts: ...
    def list(self, caller: Caller, under: Path = "/", **kw) -> Page: ...
    def changes(self, caller: Caller, since: int = 0, **kw) -> dict: ...
    def search(self, caller: Caller, query: Query) -> list[Hit]: ...
    def links(self, caller: Caller, path: Path, kind: Optional[str] = None) -> list[Link]: ...
    def propose_link(self, caller: Caller, link: Link) -> Any: ...


def _streams(streams) -> list[str]:
    return [s.value if isinstance(s, Stream) else str(s) for s in streams
            if (s.value if isinstance(s, Stream) else s) not in ("vectors", "rights")]


DEFAULT = frozenset({Stream.ENTRY, Stream.DATA})


class EngineUDS:
    """The UDS interface over the engine's `/v1/items/*` routes."""

    def __init__(self, transport, deadline: float = 5.0) -> None:
        self.transport = transport
        self.deadline = deadline

    def _call(self, route: str, body: dict) -> tuple[int, Any]:
        from jarvis.knowledge_client import EngineUnavailable
        status, payload = self.transport.request("POST", "/v1/items/" + route, body, self.deadline)
        if status == 409:
            raise Conflict(payload.get("detail", "conflict"), payload.get("path"),
                           payload.get("expected"), payload.get("found"))
        if status == 400:
            raise ValueError((payload or {}).get("detail") or "bad request")
        if status >= 500:
            raise EngineUnavailable(f"engine answered {status}: {payload}")
        return status, payload

    # -- writing ------------------------------------------------------------
    def transaction(self, caller: Caller, tx: Transaction, *, change: Optional[str] = None,
                    summary: Optional[str] = None, full_sync: bool = False) -> dict:
        body = {"by": caller.by(), "writes": tx.writes}
        if change is not None:
            body["change"] = change
        if summary is not None:
            body["summary"] = summary
        if full_sync:
            body["durable"] = "full"
        return self._call("txn", body)[1]

    def put(self, caller: Caller, path: Path, *, data: Mapping[str, Any], content: Any = KEEP,
            expect_version: Optional[int] = None, change: Optional[str] = None,
            summary: Optional[str] = None) -> Item:
        self.transaction(caller, Transaction().put(path, data, content=content, expect_version=expect_version),
                         change=change, summary=summary)
        return self.get(caller, path, {Stream.ENTRY, Stream.DATA, Stream.CONTENT, Stream.SECTIONS})

    def delete(self, caller: Caller, path: Path, *, expect_version: Optional[int] = None,
               change: Optional[str] = None, summary: Optional[str] = None) -> None:
        self.transaction(caller, Transaction().delete(path, expect_version=expect_version),
                         change=change, summary=summary)

    # -- one item -------------------------------------------------------------
    def _get(self, body: dict) -> Optional[Item]:
        status, payload = self._call("get", body)
        return None if status == 404 else Item.from_wire(payload)

    def get(self, caller: Caller, path: Path, streams=DEFAULT, version: Optional[int] = None) -> Optional[Item]:
        return self._get({"path": path, "streams": _streams(streams), "version": version})

    def get_at(self, caller: Caller, path: Path, when, streams=DEFAULT) -> Optional[Item]:
        at = when.timestamp() if isinstance(when, datetime) else float(when)
        return self._get({"path": path, "streams": _streams(streams), "at": at})

    def history(self, caller: Caller, path: Path, limit: int = 1000) -> list[Version]:
        found = self._call("history", {"path": path, "limit": limit})[1]
        return [Version(**{k: v.get(k) for k in Version.__dataclass_fields__}) for v in found["versions"]]

    def diff(self, caller: Caller, path: Path, a: int, b: Optional[int] = None) -> dict:
        return self._call("diff", {"path": path, "from": a, "to": b})[1]

    # -- many -----------------------------------------------------------------
    def list(self, caller: Caller, under: Path = "/", *, parent: Optional[Path] = None,
             types: Optional[list[str]] = None, where: Optional[list] = None,
             order: Optional[tuple[str, str]] = None, limit: int = 100, cursor: Optional[str] = None,
             streams=DEFAULT) -> Page:
        body: dict = {"under": under, "limit": limit, "streams": _streams(streams)}
        for k, v in (("parent", parent), ("types", types), ("where", where), ("cursor", cursor)):
            if v is not None:
                body[k] = v
        if order is not None:
            body["order"] = list(order)
        found = self._call("list", body)[1]
        return Page(tuple(Item.from_wire(i) for i in found["items"]), found["cursor"], found["lsn"])

    def scan(self, caller: Caller, under: Path = "/", **kw) -> Iterator[Item]:
        """Every page of `list`, one item at a time."""
        cursor = None
        while True:
            page = self.list(caller, under, cursor=cursor, **kw)
            yield from page.items
            if page.cursor is None:
                return
            cursor = page.cursor

    def count(self, caller: Caller, under: Path = "/", *, parent: Optional[Path] = None,
              types: Optional[list[str]] = None, where: Optional[list] = None,
              group_by: Optional[str] = None) -> Counts:
        body: dict = {"under": under}
        for k, v in (("parent", parent), ("types", types), ("where", where), ("group_by", group_by)):
            if v is not None:
                body[k] = v
        found = self._call("count", body)[1]
        groups = found.get("groups")
        return Counts(found["items"], found["embedded"], found["waiting"],
                      None if groups is None else tuple((g["value"], g["items"]) for g in groups))

    def changes(self, caller: Caller, since: int = 0, *, limit: int = 100,
                under: Optional[Path] = None) -> dict:
        body: dict = {"since": since, "limit": limit}
        if under is not None:
            body["under"] = under
        return self._call("changes", body)[1]

    def links(self, caller: Caller, path: Path, kind: Optional[str] = None, *,
              direction: str = "both", origins: Optional[list[str]] = None, limit: int = 100) -> list[Link]:
        body: dict = {"path": path, "direction": direction, "limit": limit}
        if kind is not None:
            body["kind"] = kind
        if origins is not None:
            body["origins"] = origins
        found = self._call("links", body)[1]
        return [Link(k["from"], k["kind"], k["to"], k["origin"], k.get("model"), k.get("at"),
                     float(k.get("weight", 1.0))) for k in found["links"]]

    def search(self, caller: Caller, query: Query) -> list[Hit]:
        """One hit per item. Round 1 answers by data (`where`, `under`) and
        the graph (`linked_to`); meaning, phrase and words are round 2."""
        if query.meaning or query.phrase or query.words:
            raise NotYet("search by meaning, phrase and words is round 2")
        hits: dict[str, set] = {}
        if query.where or not query.linked_to:
            where = [[k, "=", v] for k, v in query.where.items()]
            for item in self.list(caller, query.under, where=where or None, limit=1000,
                                  streams={Stream.ENTRY}).items:
                hits.setdefault(item.path, set()).add("data")
        if query.linked_to:
            found = {lk.target if lk.source == query.linked_to else lk.source
                     for lk in self.links(caller, query.linked_to, limit=1000)}
            from jarvis.knowledge import paths
            prefix = paths.under_prefix(query.under)
            graph = {p for p in found if p.startswith(prefix)}
            if query.where:
                hits = {p: m | {"graph"} for p, m in hits.items() if p in graph}
            else:
                for p in graph:
                    hits.setdefault(p, set()).add("graph")
        ranked = sorted(hits.items(), key=lambda pm: (-len(pm[1]), pm[0]))[:query.limit]
        return [Hit(p, float(len(m)), (), frozenset(m)) for p, m in ranked]

    def propose_link(self, caller: Caller, link: Link) -> Any:
        raise NotYet("AI link proposals wait for the owner's yes: round 2, with rights")


@contextlib.contextmanager
def in_process(directory: FsPath) -> Iterator[tuple[Any, EngineUDS]]:
    """An engine opened in this process over `directory`, and the UDS
    interface over it: the in-process engine for tests. Compaction runs in
    process; nothing is spawned."""
    from jarvis.knowledge.engine import Engine
    from jarvis.knowledge_client import InProcessTransport
    engine = Engine.open(FsPath(directory))
    engine.compact_in_child = False
    engine.store.compact_in_child = False
    try:
        yield engine, EngineUDS(InProcessTransport(engine))
    finally:
        engine.close()
