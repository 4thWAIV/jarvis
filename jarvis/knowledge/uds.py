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

Round 1 (engine-core) built paths, documents of any shape, content with
sections, transactions with conditions, versions with history, `get_at`
and `diff`, ordered lists, counts, the change feed, and hierarchy,
reference and derived links. Round 2 (content-and-search) adds search by
meaning (one whole-item vector and one per section), words and exact
phrase, AccessRights with the gate inside every call (every read names its
caller, whose person's rights apply; an AI's are its person's), and AI link
proposals that wait for the owner's yes.
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
    VECTORS = "vectors"      # one whole-item vector + one per section: embedded or waiting
    RIGHTS = "rights"        # who may read and write which stream (the owner's eyes only)


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
    vectors: Optional[Mapping[str, Any]] = None   # {state: embedded or waiting, sections}
    rights: Optional[Mapping[str, Any]] = None    # {own, inherited}: the grants, for the owner

    @property
    def owner(self) -> Optional[str]:
        return (self.entry or {}).get("by")

    @classmethod
    def from_wire(cls, raw: dict) -> "Item":
        sections = raw.get("sections")
        return cls(path=raw["path"], kind=raw["type"], version=raw["version"], data=raw.get("data"),
                   entry=raw.get("entry"), content=raw.get("content"), lsn=raw.get("lsn"),
                   vectors=raw.get("vectors"), rights=raw.get("rights"),
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
    data: Optional[Mapping[str, Any]] = None   # the fields asked for, as they stood then


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
    meaning: Optional[str] = None           # vector index: ranks
    where: Mapping[str, Any] = field(default_factory=dict)   # data index: field = value, a condition
    phrase: Optional[str] = None            # full-text index: an exact phrase, a condition
    words: Optional[str] = None             # full-text index: keywords, ranks
    linked_to: Optional[Path] = None        # graph index: a condition
    under: Path = "/"
    limit: int = 20
    types: Optional[tuple[str, ...]] = None


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


class Denied(PermissionError):
    """A write the caller has no right to make. A denied read is never
    this: it looks the same as a missing item."""


@dataclass(frozen=True)
class Proposal:
    path: Path                         # /proposals/links/<id>.proposal
    source: Path
    kind: str
    target: Path
    person: str
    model: str
    at: float
    state: str                         # proposed, accepted or refused
    agent: Optional[str] = None
    weight: float = 1.0

    @classmethod
    def from_wire(cls, raw: dict) -> "Proposal":
        return cls(raw["path"], raw["from"], raw["kind"], raw["to"], raw["person"], raw["model"],
                   float(raw["at"]), raw["state"], raw.get("agent"), float(raw.get("weight", 1.0)))


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

    def check(self, path: Path, *, expect_version: Optional[int] = None,
              absent: bool = False) -> "Transaction":
        """A condition alone: the transaction lands only while `path` is at
        `expect_version` (0: not an item), or not taken; nothing written."""
        return self._conditions({"op": "check", "path": path}, expect_version, absent)

    def tag(self, change: Optional[str] = None, summary: Optional[str] = None) -> "Transaction":
        """The change kind and summary of the write just added, in place of
        the transaction's (one item's own event among several)."""
        w = self.writes[-1]
        if change is not None:
            w["change"] = change
        if summary is not None:
            w["summary"] = summary
        return self

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
    return [s.value if isinstance(s, Stream) else str(s) for s in streams]


DEFAULT = frozenset({Stream.ENTRY, Stream.DATA})


class EngineUDS:
    """The UDS interface over the engine's `/v1/items/*` routes."""

    def __init__(self, transport, deadline: float = 5.0) -> None:
        self.transport = transport
        self.deadline = deadline

    def _call(self, route: str, body: dict, deadline: Optional[float] = None) -> tuple[int, Any]:
        from jarvis.knowledge_client import EngineUnavailable
        status, payload = self.transport.request("POST", "/v1/items/" + route, body,
                                                 deadline or self.deadline)
        if status == 409:
            raise Conflict(payload.get("detail", "conflict"), payload.get("path"),
                           payload.get("expected"), payload.get("found"))
        if status == 400:
            raise ValueError((payload or {}).get("detail") or "bad request")
        if status == 403:
            raise Denied((payload or {}).get("detail") or "denied")
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
        return self._get({"caller": caller.by(), "path": path, "streams": _streams(streams),
                          "version": version})

    def get_at(self, caller: Caller, path: Path, when, streams=DEFAULT) -> Optional[Item]:
        at = when.timestamp() if isinstance(when, datetime) else float(when)
        return self._get({"caller": caller.by(), "path": path, "streams": _streams(streams), "at": at})

    def history(self, caller: Caller, path: Path, limit: int = 1000,
                fields: Optional[list[str]] = None) -> list[Version]:
        """Each version, newest first; with `fields`, each carries those
        data fields as they stood then."""
        body: dict = {"caller": caller.by(), "path": path, "limit": limit}
        if fields is not None:
            body["fields"] = list(fields)
        found = self._call("history", body)[1]
        return [Version(**{k: v.get(k) for k in Version.__dataclass_fields__}) for v in found["versions"]]

    def diff(self, caller: Caller, path: Path, a: int, b: Optional[int] = None) -> dict:
        return self._call("diff", {"caller": caller.by(), "path": path, "from": a, "to": b})[1]

    # -- many -----------------------------------------------------------------
    def list(self, caller: Caller, under: Path = "/", *, parent: Optional[Path] = None,
             types: Optional[list[str]] = None, where: Optional[list] = None,
             order: Optional[tuple[str, str]] = None, limit: int = 100, cursor: Optional[str] = None,
             streams=DEFAULT) -> Page:
        body: dict = {"caller": caller.by(), "under": under, "limit": limit, "streams": _streams(streams)}
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
        body: dict = {"caller": caller.by(), "under": under}
        for k, v in (("parent", parent), ("types", types), ("where", where), ("group_by", group_by)):
            if v is not None:
                body[k] = v
        found = self._call("count", body)[1]
        groups = found.get("groups")
        return Counts(found["items"], found["embedded"], found["waiting"],
                      None if groups is None else tuple((g["value"], g["items"]) for g in groups))

    def changes(self, caller: Caller, since: int = 0, *, limit: int = 100,
                under: Optional[Path] = None) -> dict:
        body: dict = {"caller": caller.by(), "since": since, "limit": limit}
        if under is not None:
            body["under"] = under
        return self._call("changes", body)[1]

    def links(self, caller: Caller, path: Path, kind: Optional[str] = None, *,
              direction: str = "both", origins: Optional[list[str]] = None, limit: int = 100) -> list[Link]:
        body: dict = {"caller": caller.by(), "path": path, "direction": direction, "limit": limit}
        if kind is not None:
            body["kind"] = kind
        if origins is not None:
            body["origins"] = origins
        found = self._call("links", body)[1]
        return [Link(k["from"], k["kind"], k["to"], k["origin"], k.get("model"), k.get("at"),
                     float(k.get("weight", 1.0))) for k in found["links"]]

    def search(self, caller: Caller, query: Query, *, budget_ms: Optional[int] = None) -> list[Hit]:
        """One hit per item, from every index the query names: meaning (the
        whole-item vectors, the section vectors saying where), words and an
        exact phrase (full text), field values (data) and links (graph),
        through the caller's rights."""
        return list(self.search_page(caller, query, budget_ms=budget_ms)["hits"])

    def search_page(self, caller: Caller, query: Query, *, budget_ms: Optional[int] = None) -> dict:
        """`search` with whether the answer is partial and why."""
        body: dict = {"caller": caller.by(), "under": query.under, "limit": query.limit}
        for k in ("meaning", "phrase", "words", "linked_to"):
            if getattr(query, k):
                body[k] = getattr(query, k)
        if query.where:
            body["where"] = [[k, "=", v] for k, v in query.where.items()]
        if query.types:
            body["types"] = list(query.types)
        if budget_ms is not None:
            body["budget_ms"] = budget_ms
        found = self._call("search", body)[1]
        return {"hits": tuple(Hit(h["path"], float(h["score"]), tuple(h["sections"]), frozenset(h["matched_by"]))
                              for h in found["hits"]),
                "partial": found.get("partial", False), "notes": tuple(found.get("notes") or ())}

    def propose_link(self, caller: Caller, link: Link, *, why: Optional[str] = None) -> Proposal:
        """An AI's link, kept as a proposal with the agent, the person, the
        model and the time until the owner accepts or refuses it."""
        body: dict = {"by": caller.by(), "from": link.source, "kind": link.kind, "to": link.target,
                      "weight": link.weight}
        if link.model:
            body["model"] = link.model
        if why:
            body["why"] = why
        return Proposal.from_wire(self._call("propose", body)[1])

    def proposals(self, caller: Caller, state: Optional[str] = "proposed") -> list[Proposal]:
        found = self._call("proposals", {"caller": caller.by(), "state": state})[1]
        return [Proposal.from_wire(p) for p in found["proposals"]]

    def answer_proposal(self, caller: Caller, path: Path, action: str) -> dict:
        """The owner's yes (`accept`, which writes the link) or no (`refuse`)."""
        return self._call("proposals/answer", {"by": caller.by(), "path": path, "action": action})[1]

    def forget(self, caller: Caller, paths: list[Path], *, deadline: Optional[float] = None) -> dict:
        """Erase every version of items already deleted (the owner's act):
        see items.Items.forget. It compacts what held them, so it may take
        longer than an ordinary call (`deadline`)."""
        return self._call("forget", {"by": caller.by(), "paths": list(paths)}, deadline)[1]

    def set_rights(self, caller: Caller, path: Path, grants: list) -> dict:
        """The grants on `path` (each: person, data, content, write, hidden),
        inherited below it; the owner's act. An empty list clears them."""
        return self._call("rights", {"by": caller.by(), "path": path, "grants": [dict(g) for g in grants]})[1]


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
