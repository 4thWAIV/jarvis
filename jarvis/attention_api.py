"""The attention record's calls, shared by the page's routes and the brain's
tools (contract section 10: "Review updates / mark seen"), so each gives the
same answer with the same validation.

  GET  /api/attention            what is waiting: the pending count, the
                                 items (pending first, then the last ones
                                 seen or resolved), his availability and
                                 the admission settings
  POST /api/attention/seen       mark items seen ({"ids": [...]}, or every
                                 pending one when ids is absent)

The brain's attention_updates gives `for_brain`, the same items briefer and
in pages; its reading tools mark a source's update seen through
`mark_seen_source` once the reply that read it to him was heard in full.
  GET  /api/attention/settings   the admission policy's trial values
  PUT  /api/attention/settings   change some of them (validated; the saved
                                 values come back)

They sit behind the web boundary like every other route (web_auth.py): a
change needs the page's own origin or the tool token, and no access-key
category covers them. Every committed change is an `attention` hint on
/ws/sessions: the store sends it for an item, `on_change` for settings.

The items carry other people's words (`untrusted`: names, subjects). The
page shows them as text; the brain's tool walls them.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from jarvis import attention_store
from jarvis.attention import Admission
from jarvis.attention_store import AttentionStore

PREFIX = "/api/attention"
# Besides what is pending, the last ones he dealt with, for Review updates.
RECENT_DONE = 20


class Unavailable(Exception):
    """The attention record is not running (outside a server)."""


Running = Callable[[], tuple[Optional[AttentionStore], Optional[Admission]]]


def _overview(store: AttentionStore, admission: Optional[Admission],
              shape: Callable[[attention_store.Item], dict]) -> dict:
    """The count, his availability and the settings, then the items (pending
    first, then the last ones seen or resolved), each in `shape`."""
    pending = store.items((attention_store.PENDING,))
    done = store.items((attention_store.SEEN, attention_store.RESOLVED), limit=RECENT_DONE)
    return {"pending": len(pending),
            "availability": admission.availability() if admission else "unknown",
            "settings": asdict(attention_store.settings()),
            "items": [shape(i) for i in pending + done]}


def overview(store: AttentionStore, admission: Optional[Admission]) -> dict:
    """What the page's Review updates reads."""
    return _overview(store, admission, lambda i: i.public())


def brief(item: attention_store.Item, now: float) -> dict:
    """One item as the brain's tool gives it: what the page shows of it
    (its kind, event, facts and the words it is about, its state) without
    the timestamps, so a page of the tool holds several."""
    out = {"id": item.id, "kind": item.kind, "event": item.event, "state": item.state,
           "minutes_ago": int(max(0.0, now - item.updated_at) // 60)}
    if item.facts:
        out["facts"] = item.facts
    if item.untrusted:
        out["words"] = item.untrusted
    return out


def for_brain(store: AttentionStore, admission: Optional[Admission], now: float) -> dict:
    """The overview with each item brief: the same items, in the same order,
    as the page's, and the count and settings ahead of them."""
    return _overview(store, admission, lambda i: brief(i, now))


def mark_seen_source(store: AttentionStore, kind: str, source_id: str,
                     revision: Optional[int] = None) -> int:
    """He has heard this source read to him in full (the brain's reading
    tools, once the reply that read it was heard): its update is seen."""
    return store.mark_seen_source(kind, str(source_id), revision)


def mark_seen(store: AttentionStore, ids: Optional[list]) -> dict:
    if ids is not None:
        if not isinstance(ids, list) or not all(
                isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise attention_store.Refused("ids is a list of update ids.")
    marked = store.mark_seen(ids)
    return {"marked": marked, "pending": store.pending_count()}


def change_settings(changes: dict, on_change: Callable[[], None]) -> dict:
    saved = asdict(attention_store.change_settings(changes))
    on_change()
    return saved


def mount(app: FastAPI, running: Running, on_change: Callable[[], None]) -> None:
    """`running` gives the server's store and admission, or Nones;
    `on_change` is told after a settings change (the pages' hint)."""

    def store() -> AttentionStore:
        current, _ = running()
        if current is None:
            raise Unavailable()
        return current

    async def answer(work: Callable[[], dict]):
        try:
            return JSONResponse(await asyncio.to_thread(work))
        except Unavailable:
            return JSONResponse({"error": "The attention record is not running."},
                                status_code=503)
        except attention_store.Refused as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def body(request: Request) -> dict:
        try:
            value = await request.json()
        except ValueError:
            value = None
        return value if isinstance(value, dict) else {}

    async def get_overview():
        return await answer(lambda: overview(store(), running()[1]))

    async def post_seen(request: Request):
        ids = (await body(request)).get("ids")
        return await answer(lambda: mark_seen(store(), ids))

    async def get_settings():
        return await answer(lambda: asdict(attention_store.settings()))

    async def put_settings(request: Request):
        changes = await body(request)
        return await answer(lambda: change_settings(changes, on_change))

    app.add_api_route(PREFIX, get_overview, methods=["GET"])
    app.add_api_route(PREFIX + "/seen", post_seen, methods=["POST"])
    app.add_api_route(PREFIX + "/settings", get_settings, methods=["GET"])
    app.add_api_route(PREFIX + "/settings", put_settings, methods=["PUT"])
