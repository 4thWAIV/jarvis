"""team_reminder.py — recording once that this JARVIS is not signed in.

A JARVIS that is not signed in to a team server works as before; it only
reminds Tim. The page shows a reminder for as long as it is not signed in
(TeamSignInReminder.tsx). This is its attention item: once per process,
after startup has settled (SETTLE_SEC, so a saved connection has had its
first status read), one item in the attention record (attention_store.py),
which nothing speaks by itself; the one admission policy and digest decide
whether it is ever mentioned. Signed in by then, nothing is recorded; once
it is signed in later, the item is resolved. Nothing is recorded again in
this process whatever happens later.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional, Protocol

# How long after startup the connection is looked at.
SETTLE_SEC = 30.0
# How often, once recorded, the connection is looked at again to resolve it.
WATCH_SEC = 30.0
KIND, SOURCE, EVENT = "sign_in", "team", "not_signed_in"


class Attention(Protocol):
    """The attention record, as far as this needs it."""

    def record(self, kind: str, source_id: str, event: str, **kw) -> object: ...

    def resolve(self, kind: str, source_id: str) -> bool: ...


_said = False


async def remind_once(attention: Attention, signed_in: Callable[[], bool],
                      settle_sec: Optional[float] = None,
                      watch_sec: Optional[float] = None) -> None:
    """Wait for startup to settle (SETTLE_SEC unless given), then record the
    item unless this JARVIS is signed in or it has already been recorded in
    this process; then resolve it once it is signed in."""
    global _said
    await asyncio.sleep(SETTLE_SEC if settle_sec is None else settle_sec)
    if _said or signed_in():
        return
    _said = True
    attention.record(KIND, SOURCE, EVENT)
    while not signed_in():
        await asyncio.sleep(WATCH_SEC if watch_sec is None else watch_sec)
    attention.resolve(KIND, SOURCE)
