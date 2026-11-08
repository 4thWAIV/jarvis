"""The cross-engine query (design section 7.17.5).

One bounded JSON request with up to five clauses, answered with ranked
documents. It is a small pattern, not a query language:

    {"from":   {"kinds": [...], "where": [[path, op, value], ...]},
     "walk":   {"start": key, "steps": [...]},
     "near":   {"text": "...", "k": 50},
     "match":  "words",
     "about":  {"text": "...", "k": 200},
     "weights": {"match": 1, "near": 1, "about": 1},
     "visible": {"trust": ["own"], "namespaces": ["local"]},
     "return": {"properties": [...], "limit": 20},
     "at": null, "budget_ms": 200}

Each clause gives a set of ordinals: `where` from the general index,
`walk` from the graph, `match` from the keyword index, `near` from the
vector index. The planner estimates each first (posting counts for
`where`, the walk's reach, `k` for the rankings), runs the smallest, and
applies it to the rest: a small `where` or a walk becomes the filter of the
keyword and vector searches (the vector mask of section 7.10), a large
`where` is checked on the ranked candidates instead. What the caller may
see (`visible`) is applied before planning, so nothing it may not see is
ever ranked.

`about` is the graph's ranking: the agents, people, projects and topics
the text names (entities.py), and the chunks the records say they said
(`said_by`, newest first), then the chunks that mention them (`mentions`,
by keyword rank, then newest first).

Ranking is weighted reciprocal rank fusion of `match`, `near` and `about`
(k 60, each list's share times its weight, `DEFAULT_WEIGHTS` unless the
request gives its own), each hit reached through the walk raised by the
same share at the rank of its step;
with neither, results come in the requested order, newest first by
default. Every hit carries its key, source, trust, namespace and `why`.

`match` and `near` read the newest state; a request with `at` may use only
`from` and `walk`, which read exactly at that LSN. Past `budget_ms` the
answer is returned with what is done and marked partial (I3).

`/v1/search` is a fixed form of this.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from jarvis.knowledge import kinds
from jarvis.knowledge.documents import WHERE_MAX, DocumentError, Predicate

RRF_K = 60
# The fusion's weights, chosen on the trial's questions (phase5/results.md).
DEFAULT_WEIGHTS = {"match": 1.0, "near": 1.0, "about": 2.0}
K_MAX = 200
LIMIT_MAX = 200
BUDGET_MS_MAX = 500
SMALL_WHERE = 20000
HEADER = ("_key", "_kind", "_label", "_status", "_trust", "_ns", "_owner", "_source", "_lsn")


def _list(value, name) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DocumentError(f"{name} is a list")
    return value


def run(engine, body: dict) -> dict:
    if not isinstance(body, dict):
        raise DocumentError("a query is an object")
    unknown = set(body) - {"from", "walk", "near", "match", "about", "weights", "visible", "return", "at",
                           "budget_ms"}
    if unknown:
        raise DocumentError(f"unknown clauses: {', '.join(sorted(unknown))}")
    if not any(body.get(c) for c in ("from", "walk", "near", "match", "about")):
        raise DocumentError("a query has at least one of from, walk, near, match and about")
    start = time.monotonic()
    budget = min(int(body.get("budget_ms") or 200), BUDGET_MS_MAX) / 1000.0
    deadline = start + budget
    source = body.get("from") or {}
    kind_names = _list(source.get("kinds"), "kinds") or None
    for k in kind_names or []:
        kinds.kind_of(k)
    preds = [Predicate.parse(w) for w in _list(source.get("where"), "where")]
    if len(preds) > WHERE_MAX:
        raise DocumentError(f"at most {WHERE_MAX} conditions")
    visible = body.get("visible") or {}
    if visible.get("trust"):
        preds.append(Predicate("_trust", "in", _list(visible["trust"], "trust")))
    if visible.get("namespaces"):
        preds.append(Predicate("_ns", "in", _list(visible["namespaces"], "namespaces")))
    if kind_names:
        preds.append(Predicate("_kind", "in", kind_names))
    near = body.get("near")
    match = body.get("match")
    if match is not None and not isinstance(match, str):
        raise DocumentError("match is words")
    if near is not None and (not isinstance(near, dict) or not isinstance(near.get("text"), str)):
        raise DocumentError("near has text")
    about = body.get("about")
    if about is not None and (not isinstance(about, dict) or not isinstance(about.get("text"), str)):
        raise DocumentError("about has text")
    weights = dict(DEFAULT_WEIGHTS)
    given = body.get("weights")
    if given is not None:
        if not isinstance(given, dict) or set(given) - set(DEFAULT_WEIGHTS) or \
                not all(isinstance(v, (int, float)) and 0 <= v <= 10 for v in given.values()):
            raise DocumentError("weights are match, near and about, each 0 to 10")
        weights.update({k: float(v) for k, v in given.items()})
    at = body.get("at")
    if at is not None and (near or match or about):
        raise DocumentError("match, near and about read the newest state; at goes with from and walk only")
    ret = body.get("return") or {}
    limit = int(ret.get("limit") or 20)
    if not 0 < limit <= LIMIT_MAX:
        raise DocumentError(f"limit is 1 to {LIMIT_MAX}")
    props = _list(ret.get("properties"), "properties") or list(HEADER)
    k_match = min(int(body.get("k") or K_MAX), K_MAX)
    k_near = min(int((near or {}).get("k") or 50), K_MAX)

    docs, graph = engine.docs, engine.graph
    notes: list[str] = []
    partial = False
    dates = {id(p): docs._is_date(p, kind_names) for p in preds}
    with engine.store.reading(at) as lsn:
        # The walk: a set, and for each node the step that reached it.
        walk_set: Optional[set[int]] = None
        walk_why: dict[int, dict] = {}
        if body.get("walk") is not None:
            spec = dict(body["walk"])
            spec["at"] = lsn
            spec["budget_ms"] = max(1, int((deadline - time.monotonic()) * 1000))
            walked = graph.walk(spec)
            partial = partial or walked["partial"]
            walk_set = set()
            steps = walked["steps"]
            for depth, level in enumerate(steps, start=1):
                if depth != len(steps):
                    continue
                for reached in level:
                    o = reached["node"]["_ord"]
                    walk_set.add(o)
                    walk_why[o] = {"step": depth, "edge": reached["via"]["kind"],
                                   "origin": reached["via"]["origin"], "weight": reached["via"]["weight"]}

        # Where: materialised when it is small or nothing else ranks;
        # otherwise checked on the ranked candidates.
        where_set: Optional[set[int]] = None
        check_where = False
        if preds:
            older = at is not None and at < engine.store.lsn
            small = older or docs.estimate(preds, kind_names) <= SMALL_WHERE
            if small or not (near or match or about):
                found = docs.find(kinds_=None, where=[[p.path, p.op, p.value] for p in preds],
                                  at=lsn, limit=1 << 30, within=walk_set)
                where_set = {d["_ord"] for d in found}
            else:
                check_where = True
        allowed = where_set if where_set is not None else walk_set
        if where_set is not None and walk_set is not None:
            allowed = where_set & walk_set

        rankings: list[tuple[str, list[tuple[int, float, Any]]]] = []
        if match:
            if time.monotonic() > deadline:
                partial = True
            else:
                hits = engine.lexical.search(match, k_match, within=allowed)
                rankings.append(("match", [(o, s, w) for o, s, w in hits]))
        if near:
            vectors = engine.vectors
            if vectors.space_id is None:
                notes.append("near: no embedder, so vectors were not used")
            else:
                left = deadline - time.monotonic()
                q = vectors.embed_query(near["text"], timeout=max(0.0, min(left, 0.150)))
                if q is None:
                    partial = True
                    notes.append("near: the query embedding was late")
                else:
                    found = vectors.search(q, k_near, within=allowed,
                                           deadline_s=max(0.0, min(deadline - time.monotonic(), 0.100)))
                    partial = partial or found["partial"]
                    rankings.append(("near", [(o, s, None) for o, s in found["hits"]]))

        if about:
            if time.monotonic() > deadline:
                partial = True
            else:
                lexical_rank = {o: r for r, (o, _s, _w) in enumerate(next(
                    (h for n, h in rankings if n == "match"), []))}
                found = _about(engine, about["text"], allowed, lexical_rank,
                               min(int(about.get("k") or K_MAX), K_MAX), lsn)
                if found:
                    rankings.append(("about", found))

        # Asked to rank: only what a ranking found is returned, nothing when
        # none found anything.
        ranked = bool(match or near or about)
        scored: dict[int, float] = {}
        why: dict[int, dict] = {}
        if ranked:
            for name, hits in rankings:
                share = weights.get(name, 1.0)
                for rank, (o, score, detail) in enumerate(hits, start=1):
                    scored[o] = scored.get(o, 0.0) + share / (RRF_K + rank)
                    entry = {"rank": rank, "score": round(score, 6)}
                    if isinstance(detail, dict):
                        entry.update(detail)
                    elif detail:
                        entry["terms"] = detail
                    why.setdefault(o, {})[name] = entry
            for o, info in walk_why.items():
                if o in scored:
                    scored[o] += 1.0 / (RRF_K + info["step"])
            order = sorted(scored, key=lambda o: (-scored[o], o))
        else:
            pool = allowed if allowed is not None else set(docs.live_ordinals())
            order = list(pool)

        out = []
        for o in order:
            if len(out) >= (limit if ranked else 1 << 30):
                break
            if time.monotonic() > deadline and out:
                partial = True
                break
            doc = docs.by_ordinal(o, lsn)
            if doc is None:
                continue
            if check_where and not all(p.matches(doc, dates[id(p)]) for p in preds):
                continue
            hit = {"key": doc["_key"], "kind": doc["_kind"], "source": doc.get("_source"),
                   "trust": doc.get("_trust"), "namespace": doc.get("_ns"),
                   "properties": {p: doc[p] for p in props if p in doc},
                   "why": dict(why.get(o, {}))}
            if o in walk_why:
                hit["why"]["walk"] = walk_why[o]
            if ranked:
                hit["score"] = round(scored[o], 6)
            out.append((doc, hit))
        if not ranked:
            order_by = ret.get("order")
            if order_by:
                from jarvis.knowledge.documents import _sort_key
                out.sort(key=lambda dh: _sort_key(dh[0], order_by[0]), reverse=order_by[1] == "desc")
            else:
                out.sort(key=lambda dh: -dh[0]["_lsn"])
            out = out[:limit]
        return {"hits": [h for _, h in out], "partial": partial, "lsn": lsn, "notes": notes,
                "took_ms": round((time.monotonic() - start) * 1000, 2)}


def _about(engine, text: str, allowed: Optional[set[int]], lexical_rank: dict[int, int],
           k: int, lsn: int) -> list[tuple[int, float, Any]]:
    """The graph's ranking for `about` (see the module's docstring)."""
    keys = engine.entities.named_in(text)
    if not keys:
        return []
    docs, graph = engine.docs, engine.graph
    strong: dict[int, tuple[float, str]] = {}
    weak: dict[int, tuple[float, str]] = {}
    for key in keys:
        for edge_kind, into in (("said_by", strong), ("mentions", weak)):
            for e in graph.edges(key, "in", edge_kind, lsn):
                doc = docs.get(e["from"], lsn)
                if doc is None:
                    continue
                o = doc["_ord"]
                if (allowed is not None and o not in allowed) or o in strong:
                    continue
                into[o] = (float(doc.get("at") or 0.0), key)
    ranked = sorted(strong, key=lambda o: -strong[o][0])
    ranked += sorted((o for o in weak if o not in strong),
                     key=lambda o: (lexical_rank.get(o, 1 << 30), -weak[o][0]))
    out = []
    for o in ranked[:k]:
        edge, key = ("said_by", strong[o][1]) if o in strong else ("mentions", weak[o][1])
        out.append((o, 0.0, {"edge": edge, "entity": key}))
    return out


def search(engine, body: dict) -> dict:
    """`/v1/search`: hybrid search: words matched, the entities they name
    through the graph, and, when an embedder exists, near, over the
    documents `kinds` and `where` allow."""
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise DocumentError("search has text")
    query: dict = {"match": text, "near": {"text": text, "k": int(body.get("k") or 50)},
                   "about": {"text": text}, "return": {"limit": int(body.get("limit") or 20)},
                   "budget_ms": body.get("budget_ms") or 200}
    if body.get("weights") is not None:
        query["weights"] = body["weights"]
    if body.get("kinds") or body.get("where"):
        query["from"] = {"kinds": body.get("kinds"), "where": body.get("where")}
    if body.get("visible"):
        query["visible"] = body["visible"]
    return run(engine, query)
