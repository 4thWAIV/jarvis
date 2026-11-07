"""The typed graph (design sections 7.4, 7.12 and 7.17.4).

A graph node is a document: the same key names both. An edge is directed
and typed, written twice, forward (`src, kind, dst`) and reverse (`dst,
kind, src`), so it is reachable in both directions without a search. Every
edge kind has an allowed list of source and target kinds, enforced on
write; an edge to a document that does not exist is refused. Every edge
carries its origin (`record`, `authored`, `extracted`, `imported`), a weight
(1.0 for record and authored), the digest of the source that produced it,
and its LSN (the version's).

A document property that names another document is not kept as a property:
its kind's schema maps it to an edge kind, and the engine writes those
edges with origin `record` in the same transaction, replacing the ones an
earlier version wrote. Deleting a document deletes every edge on it.

The walk is a small JSON pattern, bounded by construction: a start key, at
most four steps, each an edge kind, a direction, node kinds, conditions on
the nodes and edges, and a limit, with `max_nodes` and `budget_ms`.
"""
from __future__ import annotations

import json
import time
from typing import Iterable, Iterator, Optional

from jarvis.knowledge import encoding, kinds
from jarvis.knowledge.documents import DocumentError, DocumentStore, Predicate
from jarvis.knowledge.store import DOCS, EDGES_IN, EDGES_OUT, prefix_end
from jarvis.knowledge.wal import Op

SEP = b"\x00"
ORIGINS = ("record", "authored", "extracted", "imported")
STEPS_MAX = 4
STEP_LIMIT_MAX = 2000
MAX_NODES_MAX = 5000
BUDGET_MS_MAX = 500

_RECORD_KINDS = ("project", "work", "topic")
EDGE_KINDS: dict[str, tuple[frozenset, frozenset]] = {
    "has_folder": ({"project"}, {"folder"}),
    "involves": ({"work"}, {"project", "folder"}),
    "achieved": ({"project", "work"}, {"accomplishment"}),
    "evidenced_by": ({"accomplishment"}, {"run", "document", "message"}),
    "ran_in": ({"run", "agent"}, {"folder"}),
    "for_work": ({"run", "agent", "workflow", "package"}, {"work"}),
    "in_document": ({"chunk"}, {"document"}),
    "in_folder": ({"document"}, {"folder"}),
    "mentions": ({"chunk"}, {"topic", "project", "person", "folder"}),
    "about": ({"document", "project", "work"}, {"topic"}),
    "relates_to": (set(_RECORD_KINDS), set(_RECORD_KINDS)),
    "depends_on": (set(_RECORD_KINDS), set(_RECORD_KINDS)),
    "stage_of": ({"topic"}, {"topic"}),
    "precedes": ({"topic"}, {"topic"}),
    "same_as": (set(), set()),            # any two of one kind
    "possibly_same": (set(), set()),      # any two of one kind; extracted only
    "decided_in": ({"decision"}, {"work", "project"}),
    "sent_as": ({"work", "document"}, {"package"}),
    "packed_in": ({"work", "document"}, {"package"}),
    "imported_from": ({"remote"}, {"package"}),
    "supersedes": ({"document"}, {"document"}),
}
EDGE_KINDS = {k: (frozenset(a), frozenset(b)) for k, (a, b) in EDGE_KINDS.items()}
SAME_KIND = ("same_as", "possibly_same")


def edge_key(a: str, kind: str, b: str) -> bytes:
    return a.encode() + SEP + kind.encode() + SEP + b.encode()


def split_key(key: bytes) -> tuple[str, str, str]:
    a, kind, b = key.split(SEP, 2)
    return a.decode(), kind.decode(), b.decode()


def _family_kind(doc: dict) -> str:
    """The kind an edge rule sees: an imported node answers for the kind it
    carries."""
    if doc.get("_kind") == "remote" and doc.get("inner_kind"):
        return str(doc["inner_kind"])
    return doc["_kind"]


def check_edge(kind: str, src: dict, dst: dict, origin: str) -> None:
    if kind not in EDGE_KINDS:
        raise DocumentError(f"unknown edge kind {kind!r}")
    if origin not in ORIGINS:
        raise DocumentError(f"origin is one of {', '.join(ORIGINS)}")
    a, b = _family_kind(src), _family_kind(dst)
    if kind in SAME_KIND:
        if a != b:
            raise DocumentError(f"{kind} joins two of one kind, not a {a} and a {b}")
        if kind == "possibly_same" and origin != "extracted":
            raise DocumentError("possibly_same is only ever extracted, a proposal")
        return
    sources, targets = EDGE_KINDS[kind]
    if a not in sources or b not in targets:
        raise DocumentError(f"{kind} does not go from a {a} to a {b}")


class Graph:
    def __init__(self, docs: DocumentStore) -> None:
        self.docs = docs
        self.store = docs.store
        docs.reference_hook = self._reference_ops
        docs.delete_hook = self._delete_ops
        docs.forget_hooks.append(self._forget_keys)

    # -- writing ----------------------------------------------------------
    def _edge_ops(self, src: str, kind: str, dst: str, value: Optional[dict]) -> list[Op]:
        body = None if value is None else encoding.canonical(value)
        return [Op(EDGES_OUT, edge_key(src, kind, dst), body),
                Op(EDGES_IN, edge_key(dst, kind, src), body)]

    def _node(self, key: str, pending: dict[str, Optional[dict]]) -> dict:
        found = pending[key] if key in pending else self.docs.get(key)
        if found is None:
            raise DocumentError(f"no document {key!r} for the edge")
        return found

    def _reference_ops(self, raw: dict, stored: dict, pending: dict[str, Optional[dict]]) -> list[Op]:
        """The record edges a document's reference properties make, and
        deletes for those an earlier version made and this one does not."""
        kind = kinds.kind_of(stored["_kind"])
        if not kind.references:
            return []
        key = stored["_key"]
        ops: list[Op] = []
        for prop, edge_kind in kind.references.items():
            wanted = raw.get(prop)
            targets = [wanted] if isinstance(wanted, str) else list(wanted or [])
            for t in targets:
                if not isinstance(t, str):
                    raise DocumentError(f"{prop} names documents by key")
                check_edge(edge_kind, stored, self._node(t, pending), "record")
            existing = {split_key(v.key)[2] for v in self.store.prefix(EDGES_OUT, edge_key(key, edge_kind, ""))
                        if json.loads(v.value).get("origin") == "record"}
            for t in existing - set(targets):
                ops.extend(self._edge_ops(key, edge_kind, t, None))
            for t in targets:
                ops.extend(self._edge_ops(key, edge_kind, t,
                                          {"origin": "record", "weight": 1.0, "digest": stored["_hash"]}))
        return ops

    def _delete_ops(self, key: bytes) -> list[Op]:
        node = key.decode()
        ops: list[Op] = []
        for v in self.store.prefix(EDGES_OUT, key + SEP):
            _, kind, dst = split_key(v.key)
            ops.extend(self._edge_ops(node, kind, dst, None))
        for v in self.store.prefix(EDGES_IN, key + SEP):
            _, kind, src = split_key(v.key)
            ops.extend(self._edge_ops(src, kind, node, None))
        return ops

    def _forget_keys(self, keys: set[bytes]) -> dict[int, set[bytes]]:
        out, inn = set(), set()
        for key in keys:
            for family in (EDGES_OUT, EDGES_IN):
                for v in self.store.prefix(family, key + SEP):
                    a, kind, b = split_key(v.key)
                    fwd = edge_key(a, kind, b) if family == EDGES_OUT else edge_key(b, kind, a)
                    out.add(fwd)
                    inn.add(_reverse(fwd))
        return {EDGES_OUT: out, EDGES_IN: inn}

    def ops(self, edges: Iterable[dict], deletes: Iterable[dict],
            pending: dict[str, Optional[dict]]) -> list[Op]:
        """Validated log operations for edges given in a transaction.
        `pending` maps keys this transaction puts (or deletes, None) to their
        documents, so an edge may join documents made in the same write."""
        def node(key: str) -> dict:
            return self._node(key, pending)

        ops: list[Op] = []
        for e in edges:
            if not isinstance(e, dict):
                raise DocumentError("an edge is an object")
            src, kind, dst = e.get("from"), e.get("kind"), e.get("to")
            if not all(isinstance(x, str) and x for x in (src, kind, dst)) or SEP.decode() in src + dst:
                raise DocumentError("an edge has from, kind and to")
            origin = e.get("origin", "extracted")
            check_edge(kind, node(src), node(dst), origin)
            weight = float(e.get("weight", 1.0 if origin in ("record", "authored") else 0.5))
            if not 0.0 <= weight <= 1.0:
                raise DocumentError("weight is 0 to 1")
            value = {"origin": origin, "weight": weight}
            if e.get("digest"):
                value["digest"] = str(e["digest"])
            if e.get("props"):
                if not isinstance(e["props"], dict):
                    raise DocumentError("edge props are an object")
                value["props"] = e["props"]
            ops.extend(self._edge_ops(src, kind, dst, value))
        for d in deletes:
            ops.extend(self._edge_ops(d["from"], d["kind"], d["to"], None))
        return ops

    # -- reading ----------------------------------------------------------
    def edges(self, key: str, direction: str = "out", kind: Optional[str] = None,
              at: Optional[int] = None, after: Optional[str] = None) -> Iterator[dict]:
        family = EDGES_OUT if direction == "out" else EDGES_IN
        prefix = key.encode() + SEP + ((kind.encode() + SEP) if kind else b"")
        # `after` is what follows `key\0` in the last edge seen: kind\0other.
        start = prefix if after is None else max(prefix, key.encode() + SEP + after.encode() + SEP)
        for v in self.store.scan(family, start, prefix_end(prefix), at):
            a, k, b = split_key(v.key)
            body = json.loads(v.value)
            if direction == "out":
                yield {"from": a, "kind": k, "to": b, "lsn": v.lsn, **body}
            else:
                yield {"from": b, "kind": k, "to": a, "lsn": v.lsn, **body}

    def walk(self, spec: dict) -> dict:
        start = spec.get("start")
        steps = spec.get("steps") or []
        if not isinstance(start, str) or not start:
            raise DocumentError("a walk has a start key")
        if not isinstance(steps, list) or not 0 < len(steps) <= STEPS_MAX:
            raise DocumentError(f"a walk has 1 to {STEPS_MAX} steps")
        max_nodes = min(int(spec.get("max_nodes") or 500), MAX_NODES_MAX)
        budget = min(int(spec.get("budget_ms") or 200), BUDGET_MS_MAX) / 1000.0
        deadline = time.monotonic() + budget
        parsed = [self._step(s) for s in steps]
        at = spec.get("at")
        partial = False
        with self.store.reading(at) as lsn:
            start_doc = self.docs.get(start, lsn)
            if start_doc is None:
                return {"start": None, "steps": [], "partial": False, "lsn": lsn}
            frontier = [start]
            seen = {start}
            levels = []
            total = 1
            for step in parsed:
                reached: list[dict] = []
                for key in frontier:
                    if time.monotonic() > deadline or total >= max_nodes:
                        partial = True
                        break
                    taken = 0
                    for edge in self.edges(key, step["dir"], step["edge"], lsn):
                        if taken >= step["limit"] or total >= max_nodes:
                            partial = partial or taken >= step["limit"] or total >= max_nodes
                            break
                        if step["origins"] and edge["origin"] not in step["origins"]:
                            continue
                        if edge["weight"] < step["min_weight"]:
                            continue
                        other = edge["to"] if step["dir"] == "out" else edge["from"]
                        if other in seen:
                            continue
                        doc = self.docs.get(other, lsn)
                        if doc is None:
                            continue
                        if step["kinds"] and doc["_kind"] not in step["kinds"]:
                            continue
                        if not all(p.matches(doc, False) for p in step["where"]):
                            continue
                        seen.add(other)
                        taken += 1
                        total += 1
                        reached.append({"node": _header(doc), "via": edge})
                levels.append(reached)
                frontier = [r["node"]["_key"] for r in reached]
                if partial:
                    break
        return {"start": _header(start_doc), "steps": levels, "partial": partial, "lsn": lsn}

    def _step(self, s: dict) -> dict:
        if not isinstance(s, dict):
            raise DocumentError("a step is an object")
        edge = s.get("edge")
        if edge is not None and edge not in EDGE_KINDS:
            raise DocumentError(f"unknown edge kind {edge!r}")
        direction = s.get("dir", "out")
        if direction not in ("out", "in"):
            raise DocumentError("dir is out or in")
        limit = int(s.get("limit") or 50)
        if not 0 < limit <= STEP_LIMIT_MAX:
            raise DocumentError(f"a step's limit is 1 to {STEP_LIMIT_MAX}")
        for k in s.get("kinds") or []:
            kinds.kind_of(k)
        return {"edge": edge, "dir": direction, "kinds": set(s.get("kinds") or []), "limit": limit,
                "where": [Predicate.parse(w) for w in s.get("where") or []],
                "origins": set(s.get("origins") or []), "min_weight": float(s.get("min_weight") or 0.0)}


def _reverse(forward: bytes) -> bytes:
    a, kind, b = split_key(forward)
    return edge_key(b, kind, a)


def _header(doc: dict) -> dict:
    return {k: doc[k] for k in ("_key", "_kind", "_label", "_status", "_trust", "_ns", "_source", "_ord", "_lsn")
            if k in doc}
