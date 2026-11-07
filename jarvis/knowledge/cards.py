"""Entity cards, importance and the resident tier (design section 7.13).

A card is a bounded summary of one entity, the unit the cache keeps and the
brain sees first: at most `CARD_MAX` characters for a topic, project, work,
folder or document — its label, kind, status, aliases, a one-paragraph
summary (its first own-trust words), its top `TOP_EDGES` edges with the
other end's label (record and authored before extracted), and its source.

"The most important values" is one score per entity, computed here:

    importance = 0.35 x personalised PageRank + 0.25 x recency
               + 0.20 x use + 0.20 x standing

- PageRank runs from the pinned topics (and, once they exist, active
  projects and open works), `PAGERANK_ITERATIONS` iterations at most two
  hops wide;
- recency is how new its last version is against the store's newest LSN,
  halved every `RECENCY_HALF_LSN` transactions (the engine keeps no clock
  of its own, and LSNs order every change);
- use is how often it was returned by a search or a card read, halved every
  `USE_HALF_LIFE_SEC`;
- standing is 1 for a pinned topic, else 0.

The resident tier holds the cards of the top `RESIDENT_CARDS` entities and
every pinned one. A write drops the cards of the documents it touched and of
their neighbours, and they are made again when next asked for. `focus`
loads a scope's two-hop neighbourhood into the block pool, so the first
answer about it comes from memory. The keys of the resident cards are
written to `cache.warm` at close and warmed at the next start.
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Optional

from jarvis.knowledge.store import DOCS, EDGES_IN, EDGES_OUT
from jarvis.knowledge.wal import Op

CARD_MAX = 1200
TOP_EDGES = 8
SUMMARY_MAX = 400
CARD_KINDS = ("topic", "project", "work", "folder", "document", "person", "workflow", "run")
RESIDENT_CARDS = 2000
PAGERANK_ITERATIONS = 20
DAMPING = 0.85
RECENCY_HALF_LSN = 5000
USE_HALF_LIFE_SEC = 7 * 86400
WEIGHTS = {"pagerank": 0.35, "recency": 0.25, "use": 0.20, "standing": 0.20}
_ORIGIN_RANK = {"record": 0, "authored": 1, "imported": 2, "extracted": 3}


class Cards:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.docs = engine.docs
        self.graph = engine.graph
        self.store = engine.store
        self._lock = threading.RLock()
        self.resident: dict[str, dict] = {}
        self.scores: dict[str, float] = {}
        self._use: dict[str, tuple[float, float]] = {}       # key -> (count, at)
        self.hits = 0
        self.misses = 0
        self.store.listeners.append(self._on_apply)

    # -- keeping cards true ----------------------------------------------------
    def _on_apply(self, lsn: int, ops: list[Op]) -> None:
        touched: set[str] = set()
        for op in ops:
            if op.family == DOCS:
                touched.add(op.key.decode())
            elif op.family in (EDGES_OUT, EDGES_IN):
                a, _, rest = op.key.partition(b"\x00")
                _, _, b = rest.partition(b"\x00")
                touched.update((a.decode(), b.decode()))
        with self._lock:
            for key in touched:
                self.resident.pop(key, None)

    # -- the card --------------------------------------------------------------
    def _summary(self, doc: dict) -> str:
        for e in self.graph.edges(doc["_key"], "in", "in_document"):
            chunk = self.docs.get(e["from"])
            if chunk and chunk.get("_trust") == "own" and (chunk.get("text") or "").strip():
                return " ".join(chunk["text"].split())[:SUMMARY_MAX]
        if doc["_kind"] == "topic":
            body = self.docs.find(kinds_=["document"], where=[["_source", "=", doc.get("_source")]], limit=1)
            if body:
                return self._summary(body[0])
        return ""

    def make(self, key: str) -> Optional[dict]:
        doc = self.docs.get(key)
        if doc is None:
            return None
        edges = []
        for direction in ("out", "in"):
            for e in self.graph.edges(key, direction):
                if e["kind"] in ("in_document",) and direction == "in":
                    continue                     # a document's own chunks are its text, not links
                other = e["to"] if direction == "out" else e["from"]
                edges.append((_ORIGIN_RANK.get(e["origin"], 9), -e["weight"], direction, e["kind"], other,
                              e["origin"]))
                if len(edges) > 200:
                    break
        edges.sort()
        top = []
        for _, _, direction, kind, other, origin in edges[:TOP_EDGES]:
            o = self.docs.get(other)
            top.append({"dir": direction, "kind": kind, "key": other, "origin": origin,
                        "label": (o or {}).get("_label"), "other_kind": (o or {}).get("_kind"),
                        "trust": (o or {}).get("_trust")})
        card = {"key": key, "kind": doc["_kind"], "label": doc.get("_label"), "status": doc.get("_status"),
                "trust": doc.get("_trust"), "source": doc.get("_source"),
                "aliases": doc.get("aliases") or {}, "pinned": bool(doc.get("pinned")),
                "summary": self._summary(doc), "edges": top}
        return _bounded(card)

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            found = self.resident.get(key)
        if found is not None:
            self.hits += 1
            self.used(key)
            return found
        self.misses += 1
        card = self.make(key)
        if card is not None:
            self.used(key)
            with self._lock:
                if len(self.resident) < RESIDENT_CARDS or key in self._pinned():
                    self.resident[key] = card
        return card

    # -- importance ---------------------------------------------------------------
    def used(self, key: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        count, at = self._use.get(key, (0.0, now))
        self._use[key] = (count * 0.5 ** ((now - at) / USE_HALF_LIFE_SEC) + 1.0, now)

    def _use_score(self, key: str, now: float) -> float:
        count, at = self._use.get(key, (0.0, now))
        decayed = count * 0.5 ** ((now - at) / USE_HALF_LIFE_SEC)
        return 1.0 - math.exp(-decayed / 3.0)

    def _pinned(self) -> set[str]:
        return {d["_key"] for d in self.docs.find(kinds_=["topic"], where=[["pinned", "=", True]], limit=200)}

    def pagerank(self, seeds: set[str]) -> dict[str, float]:
        """Personalised PageRank from `seeds` over their two-hop neighbourhood."""
        if not seeds:
            return {}
        nodes = set(seeds)
        frontier = set(seeds)
        adjacency: dict[str, set[str]] = {}
        for _ in range(2):
            nxt = set()
            for key in frontier:
                around = {e["to"] for e in self.graph.edges(key, "out")} | \
                         {e["from"] for e in self.graph.edges(key, "in")}
                adjacency.setdefault(key, set()).update(around)
                for other in around:
                    adjacency.setdefault(other, set()).add(key)
                nxt |= around - nodes
            nodes |= nxt
            frontier = nxt
        teleport = {k: (1.0 / len(seeds) if k in seeds else 0.0) for k in nodes}
        rank = dict(teleport)
        for _ in range(PAGERANK_ITERATIONS):
            new = {k: (1 - DAMPING) * teleport[k] for k in nodes}
            for k, r in rank.items():
                out = adjacency.get(k) or set()
                if out:
                    share = DAMPING * r / len(out)
                    for o in out:
                        if o in new:
                            new[o] += share
            rank = new
        top = max(rank.values()) or 1.0
        return {k: v / top for k, v in rank.items()}

    def refresh(self, now: Optional[float] = None) -> dict:
        """Score every card kind's documents and keep the top ones resident."""
        now = time.time() if now is None else now
        pinned = self._pinned()
        ranks = self.pagerank(pinned)
        newest = max(self.store.lsn, 1)
        scores: dict[str, float] = {}
        for kind in CARD_KINDS:
            for doc in self.docs.find(kinds_=[kind], limit=1 << 30):
                key = doc["_key"]
                recency = 0.5 ** ((newest - doc["_lsn"]) / RECENCY_HALF_LSN)
                standing = 1.0 if key in pinned else 0.0
                scores[key] = (WEIGHTS["pagerank"] * ranks.get(key, 0.0) + WEIGHTS["recency"] * recency
                               + WEIGHTS["use"] * self._use_score(key, now) + WEIGHTS["standing"] * standing)
        keep = set(sorted(scores, key=lambda k: -scores[k])[:RESIDENT_CARDS]) | pinned
        with self._lock:
            self.scores = scores
            self.resident = {k: v for k, v in self.resident.items() if k in keep}
        for key in sorted(keep, key=lambda k: -scores.get(k, 0)):
            with self._lock:
                if key in self.resident:
                    continue
            card = self.make(key)
            if card is not None:
                with self._lock:
                    self.resident[key] = card
        return {"scored": len(scores), "resident": len(self.resident), "pinned": len(pinned)}

    def hot(self) -> list[dict]:
        """The cards JARVIS's server keeps in its own memory: pinned topics
        first, then the most important, within HotCards' budget."""
        pinned = self._pinned()
        keys = sorted(pinned) + [k for k in sorted(self.scores, key=lambda k: -self.scores[k])
                                 if k not in pinned][:200]
        out = []
        for key in keys:
            card = self.get(key)
            if card is not None:
                out.append({**card, "importance": round(self.scores.get(key, 0.0), 4)})
        return out

    # -- focus and warm start ----------------------------------------------------------
    def focus(self, key: str) -> dict:
        if self.docs.get(key) is None:
            return {"key": key, "nodes": 0, "card": False}
        seen, frontier = {key}, [key]
        for _ in range(2):
            nxt = []
            for k in frontier:
                for direction in ("out", "in"):
                    for e in self.graph.edges(k, direction):
                        other = e["to"] if direction == "out" else e["from"]
                        if other not in seen and len(seen) < 2000:
                            seen.add(other)
                            nxt.append(other)
            frontier = nxt
        for k in list(seen)[:500]:
            self.docs.get(k)                     # into the block pool
        card = self.get(key)
        return {"key": key, "nodes": len(seen), "card": card is not None}

    def save_warm(self, path: Path) -> None:
        with self._lock:
            keys = list(self.resident)
        try:
            path.write_text(json.dumps({"engine_id": self.store.engine_id, "keys": keys}))
        except OSError:
            pass

    def load_warm(self, path: Path) -> int:
        try:
            body = json.loads(path.read_text())
        except (OSError, ValueError):
            return 0
        if body.get("engine_id") != self.store.engine_id:
            return 0
        n = 0
        for key in body.get("keys") or []:
            if self.get(key) is not None:
                n += 1
        return n

    def health(self) -> dict:
        return {"resident": len(self.resident), "hits": self.hits, "misses": self.misses,
                "scored": len(self.scores)}


def _bounded(card: dict) -> dict:
    """At most CARD_MAX characters as JSON: edges go first, then the
    summary is shortened."""
    while len(json.dumps(card, ensure_ascii=False)) > CARD_MAX and card["edges"]:
        card["edges"] = card["edges"][:-1]
    while len(json.dumps(card, ensure_ascii=False)) > CARD_MAX and card["summary"]:
        card["summary"] = card["summary"][: max(0, len(card["summary"]) - 100)]
    return card
