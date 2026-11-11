"""The page's knowledge routes, /api/knowledge. Thin: each runs one of
knowledge_api's calls (the same the brain's tools run) off the event loop
and returns its answer; `Refused` is 400, `Unavailable` 503. They sit behind
the web boundary like every route (web_auth.py): a change needs the page's
origin or the tool token, and no access-key category covers them. A change
is followed by a `knowledge` hint on /ws/sessions (`on_change`).

  GET  /api/knowledge/health         the engine's state, ingest and counts
  POST /api/knowledge/search         {text, phrase, query_en, include_untrusted, kinds, limit}
  GET  /api/knowledge/entity         ?name= typed matches and a card, or ?key=
  GET  /api/knowledge/proposals      what the linker proposes, waiting for his yes
  POST /api/knowledge/links          {action: link|unlink|accept|reject, topic, kind, target, proposal}
  GET  /api/knowledge/topics         the topic files
  POST /api/knowledge/topics         {action: create|status|aliases|pin|unpin, ...}
  POST /api/knowledge/maintenance    {action: refresh|forget, source}
  GET  /api/knowledge/indexes        the extra indexes, with his pins and forbids
  POST /api/knowledge/indexes        {key, choice: pin|forbid|clear}
  GET  /api/knowledge/seeds          topics JARVIS offers to create, and what each holds
  POST /api/knowledge/seeds/{id}     {action: accept|decline}
  POST /api/knowledge/context        {purpose, project, include_received}: the pack
  GET  /api/knowledge/embedder       the embedding model: installed, runnable, embedded so far
  POST /api/knowledge/embedder/install   download, check and install it (in the background)
  GET  /api/knowledge/folder-counts  each registered folder's document count; never 503 (`available`)
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from jarvis import knowledge_api as ka

PREFIX = "/api/knowledge"
_SEARCH = {"text", "phrase", "query_en", "include_untrusted", "kinds", "limit"}
_LINKS = {"action", "topic", "kind", "target", "proposal"}
_TOPICS = {"action", "topic", "name", "status", "exact", "ambiguous", "remove_aliases"}
_MAINTAIN = {"action", "source"}
_INDEX = {"key", "choice"}
_SEED = {"action"}
_CONTEXT = {"purpose", "project", "include_received"}


def mount(app: FastAPI, ctx: ka.Context, on_change: Callable[[], None] = lambda: None) -> None:

    async def run(work: Callable[[], object], changes: bool = False):
        try:
            result = await asyncio.to_thread(work)
        except ka.Refused as e:
            return JSONResponse({"error": "refused", "message": str(e)}, status_code=400)
        except ka.Unavailable as e:
            return JSONResponse({"error": "unavailable", "message": str(e)}, status_code=503)
        if changes:
            on_change()
        return JSONResponse(result)

    async def fields(request: Request, allowed: set[str]) -> Optional[dict]:
        try:
            body = await request.json()
        except ValueError:
            return None
        if not isinstance(body, dict) or set(body) - allowed:
            return None
        return body

    def bad() -> JSONResponse:
        return JSONResponse({"error": "refused", "message": "that request is not one these routes take"},
                            status_code=400)

    async def health():
        return await run(lambda: ka.status(ctx))

    async def search(request: Request):
        b = await fields(request, _SEARCH)
        if b is None:
            return bad()
        return await run(lambda: ka.search(ctx, b.get("text") or "", query_en=b.get("query_en"),
                                           include_untrusted=b.get("include_untrusted", True) is not False,
                                           kinds=b.get("kinds"), limit=b.get("limit") or 10,
                                           phrase=b.get("phrase")))

    async def entity(name: Optional[str] = None, key: Optional[str] = None):
        return await run(lambda: ka.entity(ctx, name=name, key=key))

    async def proposals():
        return await run(lambda: ka.proposals(ctx))

    async def links(request: Request):
        b = await fields(request, _LINKS)
        if b is None:
            return bad()
        return await run(lambda: ka.change_links(ctx, **b), changes=True)

    async def topics():
        return await run(lambda: ka.topics(ctx))

    async def change_topics(request: Request):
        b = await fields(request, _TOPICS)
        if b is None:
            return bad()
        return await run(lambda: ka.change_topics(ctx, **b), changes=True)

    async def maintenance(request: Request):
        b = await fields(request, _MAINTAIN)
        if b is None:
            return bad()
        return await run(lambda: ka.maintain(ctx, **b), changes=True)

    async def indexes():
        return await run(lambda: ka.indexes(ctx))

    async def choose_index(request: Request):
        b = await fields(request, _INDEX)
        if b is None:
            return bad()
        return await run(lambda: ka.choose_index(ctx, key=str(b.get("key") or ""),
                                                 choice=str(b.get("choice") or "")), changes=True)

    async def seeds():
        return await run(lambda: ka.seeds(ctx))

    async def answer_seed(seed_id: str, request: Request):
        b = await fields(request, _SEED)
        if b is None:
            return bad()
        return await run(lambda: ka.answer_seed(ctx, seed=seed_id, action=str(b.get("action") or "")),
                         changes=True)

    async def context(request: Request):
        b = await fields(request, _CONTEXT)
        if b is None:
            return bad()
        return await run(lambda: ka.work_context(ctx, purpose=str(b.get("purpose") or ""),
                                                 project=b.get("project"),
                                                 include_received=b.get("include_received") is True))

    async def embedder():
        return await run(lambda: ka.embedding_model(ctx))

    async def folder_counts():
        return await run(lambda: ka.folder_counts(ctx))

    async def install_embedder():
        try:
            return JSONResponse(ka.install_embedding_model(ctx))
        except ka.Unavailable as e:
            return JSONResponse({"error": "unavailable", "message": str(e)}, status_code=503)

    app.add_api_route(f"{PREFIX}/embedder", embedder, methods=["GET"])
    app.add_api_route(f"{PREFIX}/folder-counts", folder_counts, methods=["GET"])
    app.add_api_route(f"{PREFIX}/embedder/install", install_embedder, methods=["POST"])
    app.add_api_route(f"{PREFIX}/context", context, methods=["POST"])
    app.add_api_route(f"{PREFIX}/seeds", seeds, methods=["GET"])
    app.add_api_route(f"{PREFIX}/seeds/{{seed_id}}", answer_seed, methods=["POST"])
    app.add_api_route(f"{PREFIX}/indexes", indexes, methods=["GET"])
    app.add_api_route(f"{PREFIX}/indexes", choose_index, methods=["POST"])
    app.add_api_route(f"{PREFIX}/health", health, methods=["GET"])
    app.add_api_route(f"{PREFIX}/search", search, methods=["POST"])
    app.add_api_route(f"{PREFIX}/entity", entity, methods=["GET"])
    app.add_api_route(f"{PREFIX}/proposals", proposals, methods=["GET"])
    app.add_api_route(f"{PREFIX}/links", links, methods=["POST"])
    app.add_api_route(f"{PREFIX}/topics", topics, methods=["GET"])
    app.add_api_route(f"{PREFIX}/topics", change_topics, methods=["POST"])
    app.add_api_route(f"{PREFIX}/maintenance", maintenance, methods=["POST"])
