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
| `folder_counts` | GET  /api/knowledge/folder-counts      | folder_document_counts | read         |

Reads need the storage engine running; when it is down they refuse with
`Unavailable`. Pausing indexing in Settings leaves stored data searchable. Topics and links are Markdown files (knowledge_links)
and work without the engine; a change wakes the next ingest pass, and an
accepted or turned-down proposal resolves its attention item at once.

Passages carry their trust; whoever shows them to the brain walls them.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Iterable, Optional

from jarvis import context_service, knowledge_client, knowledge_links, knowledge_seeds, workspace_ref

SEARCH_LIMIT_MAX = 50
SEARCH_DEADLINE_SEC = 0.5
FOLDER_COUNTS_DEADLINE_SEC = 2.0
FOLDER_COUNTS_MAX = 500
TIM = "tim"


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

    def uds(self, deadline: float = SEARCH_DEADLINE_SEC):
        """The UDS interface over the running engine's transport, for calls
        made as a `Caller` (uds.py); raises `Unavailable` as `client` does."""
        from jarvis.knowledge.uds import EngineUDS
        return EngineUDS(self.client().transport, deadline=deadline)

    def wake_ingest(self) -> None:
        rt = self.runtime
        if rt is not None:
            rt.ingest.wake()


def settings(ctx: Context) -> dict:
    from jarvis import knowledge_settings
    return {**knowledge_settings.read(), "health": status(ctx)}


def change_settings(ctx: Context, enabled: bool) -> dict:
    from jarvis import knowledge_settings
    try:
        knowledge_settings.change(enabled)
    except ValueError as e:
        raise Refused(str(e)) from e
    rt = ctx.runtime
    if rt is not None:
        if enabled:
            rt.ingest.wake()
        else:
            rt.ingest.stop()
            rt.content.stop()
        # Applying the existing pace also reapplies the indexing gate.
        ctx.client().pace(rt.pace.mode)
    return settings(ctx)


# --- reads -------------------------------------------------------------------------

def status(ctx: Context) -> dict:
    rt = ctx.runtime
    if rt is None:
        return {"enabled": False, "state": "off"}
    health = rt.health()
    engine = health.get("engine") or {}
    store = engine.get("store") or {}
    return {"enabled": knowledge_client.enabled(), "state": health.get("state"), "reason": health.get("reason"),
            "pace": health.get("pace"), "ingest": health.get("ingest"),
            "chunks": (engine.get("keyword") or {}).get("chunks"),
            "documents_bytes": (engine.get("documents") or {}).get("resident_bytes"),
            "embedder": (engine.get("vectors") or {}).get("embedder"),
            "cards": engine.get("cards"), "writable": store.get("writable"),
            "lsn": store.get("lsn"), "generations": store.get("generations"),
            "proposals": len(rt.proposals), "hot_cards": len(rt.hot.cards),
            "items": _items_counted(ctx), "content": health.get("content")}


def _items_counted(ctx: Context) -> Optional[dict]:
    """Every item, counted once, with its vectors as coverage (embedded and
    waiting); None when the engine does not answer."""
    from jarvis.knowledge.uds import Caller
    try:
        found = ctx.uds().count(Caller(TIM))
    except Exception:  # noqa: BLE001 - the status says the rest
        return None
    return {"items": found.items, "embedded": found.embedded, "waiting": found.waiting}


def embedding_model(ctx: Context) -> dict:
    """The embedding model vector search runs on (embedding_models.py):
    whether it is installed and runnable, how an install is going, and,
    while the engine runs, how much it has embedded and how much waits."""
    from jarvis import embedding_models
    out = embedding_models.status()
    rt = ctx.runtime
    vectors = ((rt.health().get("engine") or {}).get("vectors") or {}) if rt is not None else {}
    out["engine"] = {"running": rt is not None, "space": vectors.get("embedder"),
                     "embedded": vectors.get("embedded"), "waiting": vectors.get("waiting"),
                     "rows": sum(int(s.get("rows") or 0) for s in (vectors.get("spaces") or {}).values())}
    return out


def install_embedding_model(ctx: Context) -> dict:
    """Start installing the embedding model (on the event loop: the download
    runs in the background); the engine picks it up when it is in place."""
    from jarvis import embedding_models
    embedding_models.start_install()
    return embedding_model(ctx)


def _active_folder_ids() -> list[str]:
    from jarvis import folder_store
    return [f.id for f in folder_store.FolderStore().list(limit=FOLDER_COUNTS_MAX)]


def folder_counts(ctx: Context, folder_ids: Optional[Iterable[str]] = None,
                  deadline: float = FOLDER_COUNTS_DEADLINE_SEC) -> dict:
    """Each active registered folder's document count in the engine, read
    as Tim through `EngineUDS.count` under `/folders/<folder id>`: `items`
    (each file once), `embedded` and `waiting` (its vectors). One request a
    folder, all within `deadline`; a folder not reached by then is left out
    and the answer says `partial`. The engine switched off or down is said
    (`available` false and why), never raised, so the Folders view is never
    failed by it."""
    from jarvis.knowledge.uds import Caller
    from jarvis.knowledge_client import EngineUnavailable
    ids = list(_active_folder_ids() if folder_ids is None else folder_ids)
    try:
        uds = ctx.uds()
    except Unavailable as e:
        return {"available": False, "reason": str(e), "folders": {}}
    caller, ends = Caller(TIM), time.monotonic() + deadline
    out: dict[str, dict] = {}
    for fid in ids:
        left = ends - time.monotonic()
        if left <= 0:
            return {"available": True, "partial": True, "folders": out}
        uds.deadline = min(SEARCH_DEADLINE_SEC, left)
        try:
            c = uds.count(caller, under=f"/folders/{fid}")
        except (EngineUnavailable, ValueError) as e:
            return {"available": False, "reason": f"the knowledge engine did not answer: {e}", "folders": {}}
        out[fid] = {"items": c.items, "embedded": c.embedded, "waiting": c.waiting}
    return {"available": True, "partial": False, "folders": out}


ITEM_TEXT_MAX = 600
OWN_TURNS = ("heard", "typed", "capture")


def item_trust(kind: str, data: dict) -> str:
    """Whose words an item holds: a file says so in its data (`trust`, from
    its folder); a turn of the conversation is his own when he said, typed
    or captured it, and JARVIS's own when its turn was not tainted; every
    other record (an agent's message, a run's result, a workflow's events)
    holds other people's words."""
    if data.get("trust") in ("own", "untrusted"):
        return data["trust"]
    if kind == "turn":
        said = str(data.get("kind") or "")
        detail = data.get("detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = {}
        if said in OWN_TURNS or (said == "jarvis" and isinstance(detail, dict) and detail.get("tainted") is False):
            return "own"
    return "untrusted"


def _passage(data: dict, text: str) -> Optional[str]:
    """A record's words to show for a search: its text field holding most
    of the words searched for, else its first."""
    from jarvis.knowledge.lexical import RECORD_FIELD_WORDS, terms
    from jarvis.knowledge.kinds import flatten
    want = set(terms(text or ""))
    best, score = None, -1
    for _, value in flatten(data):
        if not isinstance(value, str) or " " not in value:
            continue
        found = len(want & set(terms(value[:RECORD_FIELD_WORDS])))
        if found > score:
            best, score = value, found
    return None if best is None else best[:ITEM_TEXT_MAX]


def _item_hits(ctx: Context, text: str, *, phrase: Optional[str] = None, own_only: bool = False,
               limit: int = 10, deadline: float = SEARCH_DEADLINE_SEC,
               types: Optional[list[str]] = None) -> list[dict]:
    """Items by meaning and words, or holding an exact phrase, one hit an
    item, read as Tim (the brain's rights are his): a file or note with
    the text of the section where it matched, a record (a turn, a message,
    a run...) with its words that matched. Each carries whose words it
    holds (`item_trust`); `own_only` keeps his own."""
    from jarvis.knowledge.uds import Caller, Query, Stream
    uds = ctx.uds(deadline)
    caller = Caller(TIM, agent="brain")
    query = Query(meaning=text or None, words=text or None, phrase=phrase or None,
                  types=tuple(types) if types else None, limit=min(SEARCH_LIMIT_MAX * 4, limit * 4))
    found = uds.search_page(caller, query, budget_ms=max(1, int(deadline * 1000)))
    out = []
    for h in found["hits"]:
        item = uds.get(caller, h.path, {Stream.DATA, Stream.SECTIONS})
        if item is None:
            continue
        data = dict(item.data or {})
        trust = item_trust(item.kind, data)
        if own_only and trust != "own":
            continue
        sections = list(item.sections or ())
        if sections:
            at = next((s for s in sections if s.index in h.sections), sections[0])
            words, heading = at.text[:ITEM_TEXT_MAX], ([at.heading] if at.heading else None)
        else:
            words, heading = _passage(data, phrase or text), None
        out.append({"key": h.path, "kind": item.kind,
                    "label": data.get("title") or data.get("file") or h.path.rsplit("/", 1)[-1],
                    "source": data.get("file") or h.path, "trust": trust, "status": data.get("state"),
                    "text": words, "heading_path": heading,
                    "sections": list(h.sections), "score": h.score, "why": sorted(h.matched_by)})
        if len(out) >= limit:
            break
    return out


def search(ctx: Context, text: str, *, query_en: Optional[str] = None, include_untrusted: bool = True,
           kinds: Optional[list[str]] = None, limit: int = 10, phrase: Optional[str] = None) -> dict:
    """Search over what JARVIS holds, as items (UDS round 3): the files and
    notes indexed in place (meaning, words, an exact `phrase`, one hit an
    item, with the section where it matched) and the records, each found by
    its own words (turns, messages, runs, workflows...). `kinds` keeps the
    item types named (`md`, `turn`, `run`...). `query_en` searches again in
    English (the brain fills it when he speaks Spanish) and the answers are
    merged."""
    text = (text or "").strip()
    phrase = (phrase or "").strip() or None
    if not text and not phrase:
        raise Refused("search needs words")
    limit = max(1, min(int(limit or 10), SEARCH_LIMIT_MAX))
    merged: dict[str, dict] = {}
    partial = False
    for words in dict.fromkeys(w for w in (text, (query_en or "").strip()) if w or phrase):
        try:
            found = _item_hits(ctx, words, phrase=phrase, own_only=not include_untrusted, limit=limit,
                               types=kinds or None)
        except knowledge_client.EngineUnavailable as e:
            raise Unavailable(f"the knowledge engine did not answer: {e}") from e
        except ValueError as e:
            raise Refused(str(e)) from e
        for h in found:
            held = merged.get(h["key"])
            if held is None or (h.get("score") or 0) > (held.get("score") or 0):
                merged[h["key"]] = h
        if phrase:
            break
    hits = sorted(merged.values(), key=lambda h: -(h.get("score") or 0))[:limit]
    return {"query": text, "query_en": query_en, "phrase": phrase, "hits": hits,
            "partial": partial, "notes": []}


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
        try:
            workspace_ref.parse(source or "")
        except workspace_ref.RefError:
            raise Refused("forget takes a source reference (jref:1:...)") from None
        report = ctx.call("POST", "/v1/forget", {"refs": [source]}, deadline=30.0)
        documents = int(report.get("documents", 0)) + _forget_items(ctx, source)
        rt = ctx.runtime
        if rt is not None:
            rt.hot.clear()
        return {"done": "forget", "source": source, "documents": documents}
    raise Refused("the action is refresh or forget")


def _forget_items(ctx: Context, source: str) -> int:
    """The items indexed from one source (a file whose content's reference
    it is): taken out of the engine. The file stays; it is indexed again
    when it changes or the next backup scan finds it."""
    from jarvis.knowledge.uds import Caller, Stream, Transaction
    uds = ctx.uds(30.0)
    caller = Caller(TIM)
    found = [i.path for i in uds.scan(caller, "/", where=[["_content.ref", "=", source]],
                                      streams={Stream.ENTRY}, limit=1000)]
    found = [p for p in found if (uds.get(caller, p, {Stream.CONTENT}).content or {}).get("ref") == source]
    for i in range(0, len(found), 500):
        tx = Transaction()
        for p in found[i:i + 500]:
            tx.delete(p)
        uds.transaction(caller, tx, change="forgotten", summary=source[:200])
    return len(found)


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
        try:
            found = _item_hits(ctx, text, own_only=own_only, limit=30, deadline=deadline)
        except (Unavailable, Refused, knowledge_client.EngineUnavailable, ValueError):
            return None
        return sorted(found, key=lambda h: -(h.get("score") or 0))
    return run


def _open_decisions(project: str) -> list[str]:
    from jarvis import workflow_store
    out = []
    for w in workflow_store.active():
        if (w.project or "").lower() == project.lower():
            out += list(w.open_decisions)
    return out


def run_pack(ctx: Context, *, purpose: str, project: Optional[str] = None,
             scope: Optional[dict] = None) -> dict:
    """The pack a run is given, exactly: his own words only, at the run's
    budget and deadline."""
    return context_service.pack(purpose=purpose, project=project or None, scope=scope, trust="own",
                                search=context_search(ctx)).json()


def work_context(ctx: Context, *, purpose: str, project: Optional[str] = None,
                 include_received: bool = False, scope: Optional[dict] = None,
                 budget: int = context_service.BRAIN_BUDGET) -> dict:
    """The pack the brain reads before starting or briefing work: the same
    one a run gets, at the brain's budget; with `include_received`, other
    people's words too (walled by the caller). `scope` is the project
    records (`ProjectService.brief`); `project` a legacy name without one."""
    if not (purpose or "").strip():
        raise Refused("say what the work is")
    found = context_service.pack(purpose=purpose, project=project or None, scope=scope, budget_chars=budget,
                                 trust="all" if include_received else "own",
                                 search=context_search(ctx), deadline_s=context_service.BRAIN_DEADLINE_SEC,
                                 decisions=_open_decisions)
    return found.json()
