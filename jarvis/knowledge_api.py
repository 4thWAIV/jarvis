"""The knowledge calls the page's routes and the brain's tools share (design
section 13), so page and voice do exactly the same thing.

| call            | route                                  | brain tool          | kind            |
|-----------------|----------------------------------------|---------------------|-----------------|
| `status`        | GET  /api/knowledge/health             | knowledge_status    | read            |
| `search`        | POST /api/knowledge/search             | knowledge_search    | read, walled    |
| `entity`        | GET  /api/knowledge/entity             | knowledge_entity    | read, walled    |
| `proposals`     | GET  /api/knowledge/proposals          | knowledge_proposals | read, walled    |
| `topics`        | GET  /api/knowledge/topics             | knowledge_topics    | read            |
| `change_links`  | POST /api/knowledge/links              | link_knowledge      | acting          |
| `change_topics` | POST /api/knowledge/topics             | change_topic        | acting          |
| `maintain`      | POST /api/knowledge/maintenance        | reindex_knowledge   | acting          |
| `indexes`       | GET  /api/knowledge/indexes            | knowledge_indexes   | read            |
| `choose_index`  | POST /api/knowledge/indexes            | change_knowledge_index | acting       |
| `seeds`         | GET  /api/knowledge/seeds              | knowledge_seeds     | read            |
| `work_context`  | POST /api/knowledge/context            | work_context        | read, walled    |
| `answer_seed`   | POST /api/knowledge/seeds/{id}         | change_knowledge_seed | acting        |

Reads that go to the engine need it running (`JARVIS_KNOWLEDGE`); when it
is off or down they refuse with `Unavailable`, and the brain answers from
its notes as before. Topics and links are Markdown files (knowledge_links)
and work without the engine; a change wakes the next ingest pass, and an
accepted or turned-down proposal resolves its attention item at once.

Passages carry their trust; whoever shows them to the brain walls them.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from jarvis import context_service, knowledge_client, knowledge_links, knowledge_seeds

SEARCH_LIMIT_MAX = 50
RETURN = ["_key", "_kind", "_label", "_source", "_trust", "_status", "text", "heading_path"]
SEARCH_DEADLINE_SEC = 0.5


class Unavailable(RuntimeError):
    """The engine is switched off or not answering."""


class Refused(ValueError):
    """A request these calls will not carry out, in words to show."""


class Context:
    """What the calls need from the server: the runtime (None when the engine
    is switched off) and how to resolve a proposal's attention item."""

    def __init__(self, runtime: Callable[[], Any], resolved: Callable[[str], None] = lambda pid: None,
                 seed_resolved: Callable[[str], None] = lambda sid: None) -> None:
        self._runtime = runtime
        self.resolved = resolved
        self.seed_resolved = seed_resolved

    @property
    def runtime(self):
        return self._runtime()

    def client(self) -> knowledge_client.KnowledgeClient:
        rt = self.runtime
        if rt is None:
            raise Unavailable("the knowledge engine is switched off")
        if rt.supervisor.pid() is None:
            raise Unavailable("the knowledge engine is not running")
        return rt.supervisor.client

    def call(self, method: str, path: str, body: Optional[dict] = None,
             deadline: float = SEARCH_DEADLINE_SEC) -> Any:
        try:
            return self.client().call(method, path, body, deadline)
        except knowledge_client.EngineUnavailable as e:
            raise Unavailable(f"the knowledge engine did not answer: {e}") from e
        except knowledge_client.KnowledgeRefused as e:
            raise Refused(str((e.payload or {}).get("detail") or (e.payload or {}).get("error") or e)) from e

    def wake_ingest(self) -> None:
        rt = self.runtime
        if rt is not None:
            rt.ingest.wake()


# --- reads -------------------------------------------------------------------------

def status(ctx: Context) -> dict:
    rt = ctx.runtime
    if rt is None:
        return {"enabled": False, "state": "off"}
    health = rt.health()
    engine = health.get("engine") or {}
    store = engine.get("store") or {}
    return {"enabled": True, "state": health.get("state"), "reason": health.get("reason"),
            "pace": health.get("pace"), "ingest": health.get("ingest"),
            "chunks": (engine.get("keyword") or {}).get("chunks"),
            "documents_bytes": (engine.get("documents") or {}).get("resident_bytes"),
            "embedder": (engine.get("vectors") or {}).get("embedder"),
            "cards": engine.get("cards"), "writable": store.get("writable"),
            "lsn": store.get("lsn"), "generations": store.get("generations"),
            "proposals": len(rt.proposals), "hot_cards": len(rt.hot.cards)}


def _hits(answer: dict) -> list[dict]:
    out = []
    for h in answer.get("hits") or []:
        p = h.get("properties") or {}
        out.append({"key": h["key"], "kind": h["kind"], "label": p.get("_label"), "source": h.get("source"),
                    "trust": h.get("trust"), "status": p.get("_status"), "text": p.get("text"),
                    "heading_path": p.get("heading_path"), "score": h.get("score"), "why": h.get("why")})
    return out


def search(ctx: Context, text: str, *, query_en: Optional[str] = None, include_untrusted: bool = True,
           kinds: Optional[list[str]] = None, limit: int = 10) -> dict:
    """Words found in what JARVIS has read: hybrid where vectors exist,
    keywords and the graph otherwise. `query_en` searches again in English
    (the brain fills it when he speaks Spanish) and the two are merged."""
    text = (text or "").strip()
    if not text:
        raise Refused("search needs words")
    limit = max(1, min(int(limit or 10), SEARCH_LIMIT_MAX))
    body: dict = {"match": text, "near": {"text": text, "k": 50},
                  "return": {"properties": RETURN, "limit": limit}, "budget_ms": 300}
    if kinds:
        body["from"] = {"kinds": list(kinds)}
    if not include_untrusted:
        body["visible"] = {"trust": ["own"]}
    answers = [ctx.call("POST", "/v1/query", body)]
    if query_en and query_en.strip() and query_en.strip() != text:
        answers.append(ctx.call("POST", "/v1/query", {**body, "match": query_en.strip(),
                                                     "near": {"text": query_en.strip(), "k": 50}}))
    merged: dict[str, dict] = {}
    for answer in answers:
        for h in _hits(answer):
            held = merged.get(h["key"])
            if held is None or (h.get("score") or 0) > (held.get("score") or 0):
                merged[h["key"]] = h
    hits = sorted(merged.values(), key=lambda h: -(h.get("score") or 0))[:limit]
    return {"query": text, "query_en": query_en, "hits": hits,
            "partial": any(a.get("partial") for a in answers),
            "notes": sorted({n for a in answers for n in a.get("notes") or []})}


def entity(ctx: Context, *, name: Optional[str] = None, key: Optional[str] = None) -> dict:
    """Every typed match for a name, and the first one's card; or one
    entity's card by key."""
    if key:
        try:
            card = ctx.call("GET", f"/v1/cards/{key}")
        except Refused:
            raise Refused("nothing is known by that key")
        return {"matches": [{"key": key, "kind": card.get("kind"), "label": card.get("label"), "how": "key"}],
                "card": card}
    if not (name or "").strip():
        raise Refused("say a name or give a key")
    rt = ctx.runtime
    hot = rt.hot.find(name) if rt is not None else []
    matches = ctx.call("POST", "/v1/resolve", {"name": name})["matches"]
    card = None
    if matches:
        first = matches[0]["key"]
        card = next((c for c in hot if c.get("key") == first), None) or ctx.call("GET", f"/v1/cards/{first}")
    return {"matches": matches, "card": card}


def proposals(ctx: Context) -> dict:
    found = ctx.call("GET", "/v1/proposals")
    return {"proposals": found.get("proposals") or [], "last": found.get("last") or {}}


def topics(ctx: Context) -> dict:
    return {"topics": [t.json() for t in knowledge_links.topics()]}


# --- changes ----------------------------------------------------------------------

def _slug_of(key_or_slug: str) -> str:
    return knowledge_links.check_slug((key_or_slug or "").removeprefix("topic:"))


def _proposal(ctx: Context, pid: str) -> dict:
    rt = ctx.runtime
    held = (rt.proposals if rt is not None else {}).get(pid)
    if held is None:
        try:
            held = next((p for p in proposals(ctx)["proposals"] if p.get("id") == pid), None)
        except Unavailable:
            held = None
    if held is None:
        raise Refused("that proposal is no longer open")
    topic = knowledge_links.read(_slug_of(held["topic"]))
    if topic is not None:
        target = held.get("source") if held["kind"] == "about" else (held.get("other") or "").removeprefix("topic:")
        answered = topic.links("not_linked") + topic.links("about_sources") + topic.links("same_as")
        if target in answered:
            raise Refused("that proposal has already been answered")
    return held


def change_links(ctx: Context, *, action: str, topic: Optional[str] = None, kind: Optional[str] = None,
                 target: Optional[str] = None, proposal: Optional[str] = None) -> dict:
    """link, unlink, accept or reject (a proposal). Only ever on his word."""
    try:
        if action in ("accept", "reject"):
            p = _proposal(ctx, str(proposal or ""))
            slug = _slug_of(p["topic"])
            if p["kind"] == "about":
                if action == "accept":
                    knowledge_links.link(slug, "about_sources", p["source"])
                else:
                    knowledge_links.turn_down(slug, p["source"])
            elif p["kind"] == "same_as":
                other = _slug_of(p["other"])
                if action == "accept":
                    knowledge_links.link(slug, "same_as", other)
                else:
                    knowledge_links.turn_down(slug, other)
            else:
                raise Refused("JARVIS does not know that kind of proposal")
            rt = ctx.runtime
            if rt is not None:
                rt.proposals.pop(p["id"], None)
            ctx.resolved(p["id"])
            result = {"done": action, "proposal": p["id"], "topic": slug}
        elif action in ("link", "unlink"):
            slug = _slug_of(topic or "")
            if not kind or not target:
                raise Refused("a link needs a kind and a target")
            target = target if kind == "about_sources" else _slug_of(target)
            (knowledge_links.link if action == "link" else knowledge_links.unlink)(slug, kind, target)
            result = {"done": action, "topic": slug, "kind": kind, "target": target}
        else:
            raise Refused("the action is link, unlink, accept or reject")
    except knowledge_links.TopicError as e:
        raise Refused(str(e)) from e
    ctx.wake_ingest()
    return result


def change_topics(ctx: Context, *, action: str, topic: Optional[str] = None, name: Optional[str] = None,
                  status: Optional[str] = None, exact: Optional[list[str]] = None,
                  ambiguous: Optional[list[str]] = None, remove_aliases: Optional[list[str]] = None) -> dict:
    """create, status, aliases, pin or unpin a topic."""
    try:
        if action == "create":
            t = knowledge_links.create_topic(name or "", status=status or "unknown", exact=exact or None,
                                             ambiguous=ambiguous or None)
        elif action == "status":
            t = knowledge_links.change_topic(_slug_of(topic or ""), status=status)
        elif action == "aliases":
            t = knowledge_links.change_topic(_slug_of(topic or ""),
                                             add_aliases={k: v for k, v in (("exact", exact), ("ambiguous", ambiguous)) if v},
                                             remove_aliases=remove_aliases or [])
        elif action in ("pin", "unpin"):
            t = knowledge_links.change_topic(_slug_of(topic or ""), pinned=action == "pin")
        else:
            raise Refused("the action is create, status, aliases, pin or unpin")
    except knowledge_links.TopicError as e:
        raise Refused(str(e)) from e
    ctx.wake_ingest()
    return {"done": action, "topic": t.json()}


def maintain(ctx: Context, *, action: str, source: Optional[str] = None) -> dict:
    """refresh (read everything that moved now) or forget (one source)."""
    if action == "refresh":
        ctx.client()
        ctx.wake_ingest()
        return {"done": "refresh"}
    if action == "forget":
        if not source or not source.startswith("jref:1:"):
            raise Refused("forget takes a source reference (jref:1:...)")
        report = ctx.call("POST", "/v1/forget", {"refs": [source]}, deadline=30.0)
        rt = ctx.runtime
        if rt is not None:
            rt.hot.clear()
        return {"done": "forget", "source": source, "documents": report.get("documents", 0)}
    raise Refused("the action is refresh or forget")


# --- his say over the extra indexes -------------------------------------------------

def indexes(ctx: Context) -> dict:
    """The extra indexes the advisor made and would make, each by its shape
    (kinds and properties, never a value), with his pins and forbids. The
    choices are read from his settings note, so they show with the engine
    off too."""
    choices = knowledge_links.index_choices()
    try:
        found = ctx.call("GET", "/v1/indexes")
    except Unavailable:
        found = {"indexes": [], "candidates": [], "engine": False}
    pinned, forbidden = set(choices["pinned"]), set(choices["forbidden"])
    rows = []
    for d in found.get("indexes") or []:
        rows.append({"key": d["key"], "kinds": d["kinds"], "paths": d["paths"], "uses": d.get("uses", 0),
                     "state": "made", "pinned": d["key"] in pinned, "forbidden": d["key"] in forbidden})
    made = {r["key"] for r in rows}
    for c in found.get("candidates") or []:
        if c["key"] not in made:
            rows.append({"key": c["key"], "kinds": c["kinds"], "paths": c["paths"], "uses": 0,
                         "state": "proposed", "pinned": c["key"] in pinned, "forbidden": c["key"] in forbidden})
            made.add(c["key"])
    for key in sorted((pinned | forbidden) - made):
        k, _, p = key.partition("|")
        rows.append({"key": key, "kinds": k.split(","), "paths": p.split(","), "uses": 0, "state": "chosen",
                     "pinned": key in pinned, "forbidden": key in forbidden})
    return {"indexes": rows, "pinned": sorted(pinned), "forbidden": sorted(forbidden),
            "engine": found.get("engine", True)}


def choose_index(ctx: Context, *, key: str, choice: str) -> dict:
    """Pin, forbid or clear one extra index, on his word: written to his
    settings note, then handed to the engine when it runs."""
    try:
        choices = knowledge_links.choose_index(key, choice)
    except knowledge_links.TopicError as e:
        raise Refused(str(e)) from e
    try:
        ctx.call("POST", "/v1/indexes/choices", choices)
    except Unavailable:
        pass                      # the next ingest pass hands them over
    return {"done": choice, "key": key, **choices}


# --- seeds: topics offered, created only on his yes ---------------------------------

def seeds(ctx: Context) -> dict:
    """What each seed would create, quoted from JARVIS's own README and
    roadmap, and whether it is open, accepted or declined. Needs no engine."""
    return {"seeds": knowledge_seeds.seeds()}


def answer_seed(ctx: Context, *, seed: str, action: str) -> dict:
    """accept or decline a seed, on his word."""
    try:
        result = knowledge_seeds.answer(seed, action)
    except (knowledge_seeds.SeedError, knowledge_links.TopicError) as e:
        raise Refused(str(e)) from e
    ctx.seed_resolved(seed)
    ctx.wake_ingest()
    return result


# --- the context pack ---------------------------------------------------------------

def context_search(ctx: Context) -> Optional[Callable[[str, bool, float], Optional[list[dict]]]]:
    """The engine's search as context_service wants it: None when the engine
    is off, and a search that answers None when late or down."""
    if ctx.runtime is None:
        return None

    def run(text: str, own_only: bool, deadline: float) -> Optional[list[dict]]:
        if deadline <= 0:
            return None
        body: dict = {"match": text, "near": {"text": text, "k": 50},
                      "return": {"properties": RETURN, "limit": 30}, "budget_ms": max(1, int(deadline * 1000))}
        if own_only:
            body["visible"] = {"trust": ["own"]}
        try:
            return _hits(ctx.call("POST", "/v1/query", body, deadline=deadline))
        except (Unavailable, Refused):
            return None
    return run


def _open_decisions(project: str) -> list[str]:
    from jarvis import workflow_store
    out = []
    for w in workflow_store.active():
        if (w.project or "").lower() == project.lower():
            out += list(w.open_decisions)
    return out


def work_context(ctx: Context, *, purpose: str, project: Optional[str] = None,
                 include_received: bool = False,
                 budget: int = context_service.BRAIN_BUDGET) -> dict:
    """The pack the brain reads before starting or briefing work: the same
    one a run gets, at the brain's budget; with `include_received`, other
    people's words too (walled by the caller)."""
    if not (purpose or "").strip():
        raise Refused("say what the work is")
    found = context_service.pack(purpose=purpose, project=project or None, budget_chars=budget,
                                 trust="all" if include_received else "own",
                                 search=context_search(ctx), deadline_s=context_service.BRAIN_DEADLINE_SEC,
                                 decisions=_open_decisions)
    return found.json()
