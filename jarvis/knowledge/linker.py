"""The linker: topics followed, mentions found, proposals made (design
sections 7.14 and 9; UDS round 3: over items).

It runs in the engine after each ingest pass (`POST /v1/link`). Topics are
his Markdown files, indexed in place as items under `TOPICS`
(knowledge_content's root over the brain's notes): each topic item's
front matter, read from its content, is the topic. Its words are an item
like any other.

- The topic nodes the graph, the cards and `resolve` know (`topic:<slug>`
  documents: name, status, pinned, aliases, what he turned down) and the
  links he asserted between topics (`relates_to`, `depends_on`,
  `stage_of`, `precedes`, `same_as`, origin `authored`) are made from the
  topic items on every run, so they follow his files; a topic whose item
  is gone loses its node.
- An exact alias (or the topic's name) found as a whole-word sequence in a
  section of a file's content (folded as every matcher folds,
  `spoken_words.folded_words`) makes a derived `mentions` link from that
  item to the topic's item (model `linker`, weight 0.9).
- An ambiguous alias makes a link only when the same item also has an
  exact match for that topic. Otherwise it is evidence for a proposal.
- Candidates come from the keyword index (the items holding every word of
  the alias), so the linker never reads every item.

Proposals are never links. They are recomputed each run, held in memory and
read by the server (`GET /v1/proposals`), which records each as an
attention item; nothing is linked until he says yes, and then it is written
to the topic's front matter (knowledge_links), which the engine follows.
Two kinds:

- `about`: a file that mentions a topic in two or more sections, or only
  through an ambiguous alias, and is not yet one of its `about_sources`.
  Its `source` is the item's content reference (`jref`, as the topic file
  keeps it), its `document` the item's path;
- `same_as`: two topics sharing a name or an alias.

A target he turned down (`not_linked`) is never proposed again. The
linker's own links are recognised by their model, `linker`, and are the
only ones it removes. Records (items without content) are found by their
words in search; the linker neither links nor proposes them.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from jarvis import spoken_words
from jarvis.brain_notes import BRAIN
from jarvis.knowledge import kinds
from jarvis.knowledge import lexical as lx
from jarvis.knowledge.documents import DocumentStore, decode_doc
from jarvis.knowledge.graph import DERIVED, Graph, edge_key, split_key
from jarvis.knowledge.store import CONTENT, DOCS, EDGES_IN, prefix_end

WEIGHT = 0.9
BATCH = 500
ABOUT_MIN_CHUNKS = 2          # sections of one file
TOPICS = BRAIN + "/topics"
MODEL = "linker"
MENTIONS = "mentions"
TOPIC_LINKS = ("relates_to", "depends_on", "stage_of", "precedes", "same_as")
STATUSES = ("implemented", "active", "planned", "retired", "unknown")
OWNER = "tim"


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


def _list(value) -> list[str]:
    return [str(x) for x in value] if isinstance(value, list) else []


class TopicItem:
    """One topic as its item says: the front matter of its content."""

    def __init__(self, path: str, front: dict, body: str) -> None:
        self.path = path
        self.slug = path.rsplit("/", 1)[1].rsplit(".", 1)[0]
        self.key = "topic:" + self.slug
        self.front = front
        self.body = body

    @property
    def name(self) -> str:
        return str(self.front.get("name") or self.slug)

    @property
    def status(self) -> str:
        s = self.front.get("status")
        return s if s in STATUSES else "unknown"

    def aliases(self, cls: str) -> list[str]:
        return _list((self.front.get("aliases") or {}).get(cls) if isinstance(self.front.get("aliases"), dict)
                     else None)

    def links(self, kind: str) -> list[str]:
        return _list(self.front.get(kind))


class Linker:
    def __init__(self, docs: DocumentStore, graph: Graph, lexical: lx.Lexical, write) -> None:
        self.docs = docs
        self.graph = graph
        self.lexical = lexical
        self.write = write                 # the engine's legacy transaction (topic nodes)
        self.items = None                  # the items (set by the engine): links and reads
        self.proposals: list[dict] = []
        self.last: dict = {}

    # -- reading items ------------------------------------------------------
    def _content(self, path: str) -> Optional[dict]:
        v = self.docs.store.get(CONTENT, path.encode())
        return None if v is None or v.value is None else json.loads(v.value)

    def topics(self) -> list[TopicItem]:
        """Every topic item, its front matter read from its content."""
        from jarvis import knowledge_links
        out = []
        prefix = TOPICS + "/"
        for v in self.docs.store.scan(DOCS, prefix.encode(), prefix_end(prefix.encode())):
            if v.value is None:
                continue
            doc = decode_doc(v.value)[1]
            if doc.get("_type") != "md" or doc.get("_parent") != TOPICS or not doc.get("_content"):
                continue
            try:
                knowledge_links.check_slug(doc["_key"].rsplit("/", 1)[1][:-len(".md")])
            except knowledge_links.TopicError:
                continue
            content = self._content(doc["_key"])
            if content is None:
                continue
            front, body = knowledge_links.parse(content.get("text") or "")
            out.append(TopicItem(doc["_key"], front, body))
        return out

    def _candidates(self, alias_words: list[str]) -> set[int]:
        """Items holding every indexed word of the alias."""
        terms = [w for w in alias_words if w in set(lx.terms(" ".join(alias_words)))]
        if not terms:
            return set()
        found: Optional[set[int]] = None
        for term in terms:
            here = self.lexical.holding(term, items=True)
            found = here if found is None else found & here
            if not found:
                return set()
        return found or set()

    def _matches(self, aliases: list[str]) -> dict[str, dict]:
        """path -> {doc, sections}, for files whose sections hold any of the
        aliases whole: the indexes of the sections that do."""
        out: dict[str, dict] = {}
        for alias in aliases:
            needle = words(alias)
            for o in self._candidates(needle):
                doc = self.docs.by_ordinal(o)
                if doc is None or doc.get("_kind") != kinds.ITEM or not doc.get("_content"):
                    continue
                if doc["_key"].startswith(TOPICS + "/"):
                    continue                      # a topic's own words are not a mention of it
                content = self._content(doc["_key"]) or {}
                at = {s.get("index") for s in content.get("sections") or []
                      if contains(words(f"{s.get('heading') or ''}\n{s.get('text') or ''}"), needle)}
                if at:
                    held = out.setdefault(doc["_key"], {"doc": doc, "content": content, "sections": set()})
                    held["sections"] |= at
        return out

    # -- the topic nodes ------------------------------------------------------
    def _sync_nodes(self, topics: list[TopicItem]) -> None:
        """The `topic:<slug>` nodes and the links he asserted between them,
        made from the topic items; nodes whose item is gone are removed."""
        puts = [{"_key": t.key, "_kind": "topic", "_label": t.name, "_status": t.status, "_source": t.path,
                 "_trust": "own", "pinned": t.front.get("pinned") is True,
                 "aliases": {"exact": t.aliases("exact"), "ambiguous": t.aliases("ambiguous")},
                 "front": {"not_linked": t.links("not_linked")}} for t in topics]
        known = {t.key for t in topics}
        gone = sorted(d["_key"] for d in self.docs.find(kinds_=["topic"], limit=1 << 30) if d["_key"] not in known)
        if puts or gone:
            self.write({"puts": puts, "deletes": gone})
        for t in topics:
            want = set()
            for kind in TOPIC_LINKS:
                for target in t.links(kind):
                    if "topic:" + target in known and target != t.slug:
                        want.add((t.key, kind, "topic:" + target))
            have = {(e["from"], e["kind"], e["to"]) for e in self.graph.edges(t.key, "out")
                    if e.get("origin") == "authored"}
            add = [{"from": a, "kind": k, "to": b, "origin": "authored"} for a, k, b in want - have]
            drop = [{"from": a, "kind": k, "to": b} for a, k, b in have - want]
            if add or drop:
                self.write({"edges": add, "edge_deletes": drop})

    # -- one run -------------------------------------------------------------
    def _mentioning(self, topic_path: str) -> set[str]:
        """The items the linker linked as mentioning this topic."""
        prefix = edge_key(topic_path, MENTIONS, "")
        out = set()
        for v in self.docs.store.scan(EDGES_IN, prefix, prefix_end(prefix)):
            body = json.loads(v.value)
            if body.get("origin") == DERIVED and body.get("model") == MODEL:
                out.add(split_key(v.key)[2])
        return out

    def _links(self, writes: list[dict]) -> None:
        for i in range(0, len(writes), BATCH):
            self.items.transact({"by": {"person": OWNER, "model": MODEL}, "writes": writes[i:i + BATCH],
                                 "change": "linked", "summary": "the linker's mentions"})

    def run(self) -> dict:
        topics = self.topics()
        self._sync_nodes(topics)
        added = removed = 0
        proposals: dict[str, dict] = {}
        names: dict[str, list[str]] = {}
        for topic in topics:
            exact = list(dict.fromkeys([topic.name] + topic.aliases("exact")))
            ambiguous = topic.aliases("ambiguous")
            turned_down = set(topic.links("not_linked"))
            linked = set(topic.links("about_sources"))
            names[topic.key] = [" ".join(words(a)) for a in exact + ambiguous if words(a)]
            exact_hits = self._matches(exact)
            ambiguous_hits = {p: h for p, h in self._matches(ambiguous).items() if p not in exact_hits}
            wanted = set(exact_hits)
            have = self._mentioning(topic.path) if self.items is not None else set()
            writes = [{"op": "link", "from": p, "kind": MENTIONS, "to": topic.path, "model": MODEL,
                       "weight": WEIGHT} for p in sorted(wanted - have)]
            writes += [{"op": "unlink", "from": p, "kind": MENTIONS, "to": topic.path}
                       for p in sorted(have - wanted)]
            if writes and self.items is not None:
                self._links(writes)
            added += len(wanted - have)
            removed += len(have - wanted)

            for path, hit in {**ambiguous_hits, **exact_hits}.items():
                n_exact = len(hit["sections"]) if path in exact_hits else 0
                n_ambiguous = len(ambiguous_hits[path]["sections"]) if path in ambiguous_hits else 0
                source = hit["content"].get("ref") or path
                if {source, path} & (linked | turned_down):
                    continue
                if n_exact >= ABOUT_MIN_CHUNKS or (n_ambiguous and not n_exact):
                    pid = proposal_id("about", path, topic.key)
                    data = {k: v for k, v in hit["doc"].items() if not k.startswith("_")}
                    proposals[pid] = {"id": pid, "kind": "about", "topic": topic.key,
                                      "topic_name": topic.name, "document": path, "source": source,
                                      "label": data.get("file") or path.rsplit("/", 1)[1],
                                      "trust": data.get("trust") or "own", "exact": n_exact,
                                      "ambiguous": n_ambiguous}
        by_name: dict[str, list[str]] = {}
        for key, folded in names.items():
            for n in set(folded):
                by_name.setdefault(n, []).append(key)
        by_key = {t.key: t for t in topics}
        for n, keys in by_name.items():
            for i, a in enumerate(sorted(keys)):
                for b in sorted(keys)[i + 1:]:
                    ta, tb = by_key[a], by_key[b]
                    down = set(ta.links("not_linked")) | set(tb.links("not_linked"))
                    if ta.slug in down or tb.slug in down:
                        continue
                    if tb.slug in ta.links("same_as") or ta.slug in tb.links("same_as"):
                        continue
                    pid = proposal_id("same_as", a, b)
                    proposals[pid] = {"id": pid, "kind": "same_as", "topic": a, "topic_name": ta.name,
                                      "other": b, "other_name": tb.name, "shared": n}
        self.proposals = sorted(proposals.values(), key=lambda p: p["id"])
        self.last = {"added": added, "removed": removed, "proposals": len(self.proposals),
                     "topics": len(topics)}
        return self.last

