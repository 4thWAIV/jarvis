"""The brain's UI requests, carried to one page and answered by it.

Every voice page announces itself on /ws/voice (`ui_hello`: its client ID,
the contract's protocol version, capabilities and revisions); each
connection gets an epoch (`ui_welcome`), so a page that reconnects is a new
epoch and nothing meant for the old one is replayed. The page that
originated the human turn is the bound page (`note_turn`, from the voice
handler when it takes his words); a brain request goes to that page alone,
never to every page. A request without a bound page names a page
explicitly, or is refused with the eligible pages listed.

`request` validates the operation against the contract (jarvis.ui_contract),
sends one `ui_request` to the one page, and waits for that page's
`ui_result` with the contract's bounded wait: an indication is answered at
arrival, not when sent. A page that goes away, an expired request or a
result from the wrong page or epoch never counts as success.

Pure bookkeeping around a `send(ws, frame) -> bool` the server supplies;
no FastAPI, so it is tested directly.
"""
from __future__ import annotations

import asyncio
import itertools
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from jarvis import ui_contract

STATUSES = frozenset(ui_contract.load().protocol["statuses"])
LIFECYCLE = frozenset(ui_contract.load().protocol["lifecycle"])
EVENTS_KEPT = 50


@dataclass
class Page:
    ws: Any
    client_id: str
    epoch: int
    capabilities: frozenset[str]
    visible: bool
    navigation_revision: int
    connected_at: float = field(default_factory=time.time)


@dataclass
class Pending:
    request_id: str
    page: Page
    future: asyncio.Future
    expires_at: float
    events: list[str] = field(default_factory=list)


class UiClients:
    def __init__(self, send: Callable[[Any, dict], bool], clock: Callable[[], float] = time.time):
        self._send = send
        self._clock = clock
        self._pages: dict[Any, Page] = {}
        self._epochs = itertools.count(1)
        self._bound: Page | None = None
        self._pending: dict[str, Pending] = {}

    # -- pages ---------------------------------------------------------------

    def hello(self, ws, msg: dict) -> dict | None:
        """A page announced itself: its welcome, or None when it is refused
        (another protocol, or a malformed hello)."""
        limits = ui_contract.load().protocol["limits"]
        client = msg.get("clientId")
        if msg.get("protocol") != ui_contract.load().version:
            return None
        if not isinstance(client, str) or not 0 < len(client) <= limits["idMax"]:
            return None
        caps = msg.get("capabilities")
        old = self._pages.get(ws)
        page = Page(ws=ws, client_id=client, epoch=next(self._epochs),
                    capabilities=frozenset(c for c in caps if isinstance(c, str)) if isinstance(caps, list) else frozenset(),
                    visible=msg.get("visible") is True,
                    navigation_revision=int(msg.get("navigationRevision") or 0))
        self._pages[ws] = page
        if old is not None and self._bound is old:
            self._bound = page
        if old is not None:
            self._fail_page(old, "the page reconnected")
        return {"type": "ui_welcome", "clientId": client, "epoch": page.epoch}

    def gone(self, ws) -> None:
        page = self._pages.pop(ws, None)
        if page is None:
            return
        if self._bound is page:
            self._bound = None
        self._fail_page(page, "the page disconnected")

    def note_turn(self, ws) -> None:
        """The page this turn's words came from: the brain's requests in
        this turn go to it."""
        page = self._pages.get(ws)
        if page is not None:
            self._bound = page

    def pages(self) -> list[Page]:
        return list(self._pages.values())

    def describe_pages(self) -> str:
        return "; ".join(f"{p.client_id} ({'visible' if p.visible else 'hidden'})"
                         for p in self._pages.values()) or "none"

    # -- results and events ----------------------------------------------------

    def result(self, ws, msg: dict) -> None:
        pending = self._pending.get(msg.get("requestId"))
        page = self._pages.get(ws)
        if pending is None or page is None or pending.page is not page:
            return                              # not ours, or another page's
        if msg.get("clientId") != page.client_id or msg.get("epoch") != page.epoch:
            return
        if msg.get("status") not in STATUSES:
            return
        if isinstance(msg.get("navigationRevision"), int):
            page.navigation_revision = msg["navigationRevision"]
        self._pending.pop(pending.request_id, None)
        if not pending.future.done():
            pending.future.set_result({**msg, "events": list(pending.events)})

    def event(self, ws, msg: dict) -> None:
        pending = self._pending.get(msg.get("requestId"))
        if pending is None or self._pages.get(ws) is not pending.page:
            return
        if msg.get("event") in LIFECYCLE and len(pending.events) < EVENTS_KEPT:
            pending.events.append(msg["event"])

    def _fail_page(self, page: Page, why: str) -> None:
        for rid, pending in list(self._pending.items()):
            if pending.page is page:
                self._pending.pop(rid, None)
                if not pending.future.done():
                    pending.future.set_result({"status": "cancelled", "reason": why,
                                               "delivery": "unknown", "events": list(pending.events)})

    # -- requests --------------------------------------------------------------

    def target_page(self, client: str | None, origin: str | None) -> tuple[Page | None, str | None]:
        """The one page a request goes to, or why there is none."""
        if client:
            for page in self._pages.values():
                if page.client_id == client:
                    return page, None
            return None, f"no page {client!r} is connected; connected pages: {self.describe_pages()}"
        if origin == "user" and self._bound is not None and self._bound in self._pages.values():
            return self._bound, None
        if not self._pages:
            return None, "no JARVIS page is open"
        return None, ("no page is bound to this turn; name one with client. "
                      f"Connected pages: {self.describe_pages()}")

    async def request(self, operation: dict, *, client: str | None, origin: str | None,
                      turn: str | None = None, expected_revision: int | None = None) -> dict:
        problem = ui_contract.operation_problem(operation)
        if problem:
            return {"status": "invalid", "reason": problem}
        page, why = self.target_page(client, origin)
        if page is None:
            return {"status": "unavailable", "reason": why}
        limits = ui_contract.load().protocol["limits"]
        now = self._clock()
        rid = "ui-" + secrets.token_hex(8)
        expires = now + limits["requestTtlMs"] / 1000
        future = asyncio.get_running_loop().create_future()
        self._pending[rid] = Pending(rid, page, future, expires)
        frame = {"type": "ui_request", "protocol": ui_contract.load().version, "requestId": rid,
                 "clientId": page.client_id, "epoch": page.epoch, "turn": turn,
                 "expectedNavigationRevision": expected_revision,
                 "expiresAt": int(expires * 1000), "operation": operation}
        if not self._send(page.ws, frame):
            self._pending.pop(rid, None)
            return {"status": "unavailable", "reason": "the page could not be reached"}
        try:
            return await asyncio.wait_for(asyncio.shield(future), limits["resultWaitMs"] / 1000)
        except asyncio.TimeoutError:
            pending = self._pending.pop(rid, None)
            return {"status": "timeout", "reason": "the page did not answer in time",
                    "delivery": "unknown", "events": list(pending.events) if pending else []}
