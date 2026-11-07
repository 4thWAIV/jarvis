"""The linker: mentions found, proposals made (design sections 7.14 and 9).

It runs in the engine after each ingest pass (`POST /v1/link`) over every
topic:

- An exact alias (or the topic's name) found in a chunk as a whole-word
  sequence, folded as every matcher folds (`spoken_words.folded_words`),
  makes a `mentions` edge from the chunk to the topic, origin `extracted`,
  weight 0.9.
- An ambiguous alias makes an edge only when the same document also has an
  exact match for that topic. Otherwise it is evidence for a proposal.
- Candidates come from the keyword index (the chunks holding every word of
  the alias), so the linker never reads every chunk.

Proposals are never edges. They are recomputed each run, held in memory and
read by the server (`GET /v1/proposals`), which records each as an
attention item; nothing is linked until he says yes, and then it is written
to the topic's front matter (knowledge_links), which the engine follows.
Two kinds:

- `about`: a document that mentions a topic in two or more chunks, or only
  through an ambiguous alias, and is not yet linked to it;
- `same_as`: two topics sharing a name or an alias.

A target he turned down (`not_linked`) is never proposed again. The
linker's own edges are recognised by `by: linker` and are the only ones it
removes, so record and authored edges are never touched.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from jarvis import spoken_words
from jarvis.knowledge import lexical as lx
from jarvis.knowledge.documents import DocumentStore
from jarvis.knowledge.graph import Graph

WEIGHT = 0.9
BATCH = 500
ABOUT_MIN_CHUNKS = 2


def words(text: str) -> list[str]:
    return spoken_words.folded_words(text or "")


def contains(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    if not n:
        return False
    first = needle[0]
    for i, w in enumerate(haystack):
        if w == first and haystack[i:i + n] == needle:
            return True
    return False


def proposal_id(kind: str, a: str, b: str) -> str:
    return hashlib.sha256(f"{kind}\x00{a}\x00{b}".encode()).hexdigest()[:24]


class Linker:
    def __init__(self, docs: DocumentStore, graph: Graph, lexical: lx.Lexical, write) -> None:
        self.docs = docs
        self.graph = graph
        self.lexical = lexical
        self.write = write                 # the engine's transaction
        self.proposals: list[dict] = []
        self.last: dict = {}

    def _candidates(self, alias_words: list[str]) -> set[int]:
        """Chunks holding every indexed word of the alias."""
        terms = [w for w in alias_words if w in set(lx.terms(" ".join(alias_words)))]
        if not terms:
            return set()
        src = self.docs.columns.src
        found: Optional[set[int]] = None
        for term in terms:
            lists, mem = self.lexical._lists(term)
            here = {o for o in mem if o < len(src) and src[o] == 0xFFFFFFFF}
            for gen, plist in lists:
                here |= {o for o in plist.ordinals() if o < len(src) and src[o] == gen}
            found = here if found is None else found & here
            if not found:
                return set()
        return found or set()

    def _matches(self, aliases: list[str]) -> dict[int, dict]:
        """ordinal -> chunk, for chunks holding any of the aliases whole."""
        out: dict[int, dict] = {}
        for alias in aliases:
            needle = words(alias)
            for o in self._candidates(needle):
                if o in out:
                    continue
                chunk = self.docs.by_ordinal(o)
                if chunk is None or chunk.get("_kind") != "chunk":
                    continue
                if contains(words(chunk.get("text") or ""), needle):
                    out[o] = chunk
        return out

    def _document_of(self, chunk_key: str) -> Optional[str]:
        for e in self.graph.edges(chunk_key, "out", "in_document"):
            return e["to"]
        return None

    def run(self) -> dict:
        topics = self.docs.find(kinds_=["topic"], limit=1 << 30)
        added = removed = 0
        proposals: dict[str, dict] = {}
        names: dict[str, list[str]] = {}
        for topic in topics:
            key = topic["_key"]
            aliases = topic.get("aliases") or {}
            exact = list(dict.fromkeys([topic.get("_label") or key.split(":", 1)[1]]
                                       + list(aliases.get("exact") or [])))
            ambiguous = list(aliases.get("ambiguous") or [])
            turned_down = set((topic.get("front") or {}).get("not_linked") or [])
            names[key] = [" ".join(words(a)) for a in exact + ambiguous if words(a)]
            exact_hits = self._matches(exact)
            ambiguous_hits = {o: c for o, c in self._matches(ambiguous).items() if o not in exact_hits}
            doc_of = {o: self._document_of(c["_key"]) for o, c in {**exact_hits, **ambiguous_hits}.items()}
            exact_docs = {d for o, d in doc_of.items() if o in exact_hits and d}
            wanted = {c["_key"]: c for o, c in exact_hits.items()}
            wanted.update({c["_key"]: c for o, c in ambiguous_hits.items() if doc_of[o] in exact_docs})
            have = {e["from"] for e in self.graph.edges(key, "in", "mentions")
                    if (e.get("props") or {}).get("by") == "linker"}
            add = [{"from": k, "kind": "mentions", "to": key, "origin": "extracted", "weight": WEIGHT,
                    "digest": c.get("_hash"), "props": {"by": "linker"}}
                   for k, c in wanted.items() if k not in have]
            drop = [{"from": k, "kind": "mentions", "to": key} for k in have - set(wanted)]
            for i in range(0, max(len(add), len(drop)), BATCH):
                self.write({"edges": add[i:i + BATCH], "edge_deletes": drop[i:i + BATCH]})
            added += len(add)
            removed += len(drop)

            linked = {e["from"] for e in self.graph.edges(key, "in", "about")}
            counts: dict[str, list[int]] = {}
            for o, d in doc_of.items():
                if d:
                    counts.setdefault(d, [0, 0])[0 if o in exact_hits else 1] += 1
            for d, (n_exact, n_ambiguous) in counts.items():
                if d in linked:
                    continue
                doc = self.docs.get(d)
                if doc is None or doc.get("_source") in turned_down:
                    continue
                if n_exact >= ABOUT_MIN_CHUNKS or (n_ambiguous and not n_exact):
                    pid = proposal_id("about", d, key)
                    proposals[pid] = {"id": pid, "kind": "about", "topic": key,
                                      "topic_name": topic.get("_label"), "document": d,
                                      "source": doc.get("_source"), "label": doc.get("_label"),
                                      "trust": doc.get("_trust"), "exact": n_exact,
                                      "ambiguous": n_ambiguous}
        by_name: dict[str, list[str]] = {}
        for key, folded in names.items():
            for n in set(folded):
                by_name.setdefault(n, []).append(key)
        topic_docs = {t["_key"]: t for t in topics}
        for n, keys in by_name.items():
            for i, a in enumerate(sorted(keys)):
                for b in sorted(keys)[i + 1:]:
                    ta, tb = topic_docs[a], topic_docs[b]
                    down = set((ta.get("front") or {}).get("not_linked") or []) | \
                        set((tb.get("front") or {}).get("not_linked") or [])
                    if a.split(":", 1)[1] in down or b.split(":", 1)[1] in down:
                        continue
                    if any(e["to"] == b for e in self.graph.edges(a, "out", "same_as")) or \
                            any(e["to"] == a for e in self.graph.edges(b, "out", "same_as")):
                        continue
                    pid = proposal_id("same_as", a, b)
                    proposals[pid] = {"id": pid, "kind": "same_as", "topic": a, "topic_name": ta.get("_label"),
                                      "other": b, "other_name": tb.get("_label"), "shared": n}
        self.proposals = sorted(proposals.values(), key=lambda p: p["id"])
        self.last = {"added": added, "removed": removed, "proposals": len(self.proposals),
                     "topics": len(topics)}
        return self.last
