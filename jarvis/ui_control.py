"""The brain's UI requests, carried to one page and answered by it.

Every voice page announces itself on /ws/voice (`ui_hello`: its client ID,
the contract's protocol version, capabilities and revisions); each
connection gets an epoch (`ui_welcome`), so a page that reconnects is a new
epoch and nothing meant for the old one is replayed. A page speaking another
protocol is refused and remembered, so a request can say why it has no page.

The page a request goes to is the page the turn's words came from. The
voice handler notes every utterance it accepts with the id the page gave
it (`note_turn(ws, heard)`); the turn being answered knows that id
(`server._turn_heard`), and only the page that recently said it is bound.
If two open pages both recently used that id, nothing is guessed: the
request is refused with the pages listed. A turn that is not his (an
announcement, a session's note) has no page; it may name one explicitly,
or is refused with the eligible pages listed. Never broadcast.

`request` validates the operation against the contract
(jarvis.ui_contract), checks the page offers that operation, sends one
`ui_request` to that page alone, and waits for that page's `ui_result`.
The request carries one deadline (`expiresAt`, the contract's
`requestTtlMs`): the page never starts after it and abandons work still
running at it, and the server waits until it plus `ANSWER_GRACE_SEC` for
the answer in transit, so nothing happens on the page after the server has
given up. An indication is complete only at arrival (animated, or the
reduced-motion static arrival); a page claiming completion without one is
not believed. A page that goes away, an expired request or a result from
the wrong page or epoch never counts as success, and whether the page had
started (`delivery`) is said, not assumed.

Pure bookkeeping around a `send(ws, frame) -> bool` the server supplies;
no FastAPI, so it is tested directly.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from jarvis import ui_contract

log = logging.getLogger("jarvis.ui")

_PROTOCOL = ui_contract.load().protocol
STATUSES = frozenset(_PROTOCOL["statuses"])
LIFECYCLE = frozenset(_PROTOCOL["lifecycle"])
ARRIVALS = frozenset(_PROTOCOL["arrivals"])
EVENTS_KEPT = 50
# The utterance ids each page said most recently: enough to cover words he
# says while a turn is still being answered, few enough that two pages
# rarely share one.
HEARD_KEPT = 4
# How long past a request's deadline its answer may still be in transit.
ANSWER_GRACE_SEC = _PROTOCOL["limits"]["answerGraceMs"] / 1000

# What the brain is told about a request's progress when no answer came:
# the page had started it (it reported a lifecycle step), or nothing is
# known beyond the frame leaving.
DELIVERY_STARTED, DELIVERY_UNKNOWN = "started", "unknown"


@dataclass
class Page:
    ws: Any
    client_id: str
    epoch: int
    capabilities: frozenset[str]
    visible: bool
    reduced_motion: bool
    navigation_revision: int
    layout_revision: int
    connected_at: float = field(default_factory=time.time)
    heard: deque = field(default_factory=lambda: deque(maxlen=HEARD_KEPT))
    # Whether he has spoken or typed from this page at all; an utterance
    # without an id still binds a page he has used.
    spoke: bool = False


@dataclass
class Pending:
    request_id: str
    page: Page
    operation: dict
    future: asyncio.Future
    expires_at: float
    events: list[str] = field(default_factory=list)


def _revision(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class UiClients:
    def __init__(self, send: Callable[[Any, dict], bool], clock: Callable[[], float] = time.time):
        self._send = send
        self._clock = clock
        self._pages: dict[Any, Page] = {}
        self._refused: dict[Any, str] = {}
        self._epochs = itertools.count(1)
        self._pending: dict[str, Pending] = {}

    # -- pages ---------------------------------------------------------------

    def hello(self, ws, msg: dict) -> dict | None:
        """A page announced itself: its welcome, or None when it is refused
        (another protocol, or a malformed hello)."""
        limits = _PROTOCOL["limits"]
        client = msg.get("clientId")
        if msg.get("protocol") != ui_contract.load().version:
            self._refused[ws] = f"a page speaks UI protocol {msg.get('protocol')!r}, not {ui_contract.load().version}"
            self.gone(ws, keep_refusal=True)
            return None
        if not isinstance(client, str) or not 0 < len(client) <= limits["idMax"]:
            self._refused[ws] = "a page announced itself without a usable client ID"
            self.gone(ws, keep_refusal=True)
            return None
        self._refused.pop(ws, None)
        caps = msg.get("capabilities")
        old = self._pages.get(ws)
        page = Page(ws=ws, client_id=client, epoch=next(self._epochs),
                    capabilities=frozenset(c for c in caps if isinstance(c, str)) if isinstance(caps, list) else frozenset(),
                    visible=msg.get("visible") is True,
                    reduced_motion=msg.get("reducedMotion") is True,
                    navigation_revision=_revision(msg.get("navigationRevision")) or 0,
                    layout_revision=_revision(msg.get("layoutRevision")) or 0)
        # Another connection already using this client ID is the same page
        # reloaded or reconnected: it is gone, and so is its epoch.
        for other_ws, other in list(self._pages.items()):
            if other_ws is not ws and other.client_id == client:
                self.gone(other_ws)
        self._pages[ws] = page
        if old is not None:
            self._fail_page(old, "the page reconnected")
        return {"type": "ui_welcome", "clientId": client, "epoch": page.epoch}

    def gone(self, ws, keep_refusal: bool = False) -> None:
        if not keep_refusal:
            self._refused.pop(ws, None)
        page = self._pages.pop(ws, None)
        if page is not None:
            self._fail_page(page, "the page disconnected")

    def note_turn(self, ws, heard: int | None = None) -> None:
        """He spoke or typed from this page; `heard` is the id the page gave
        those words. The turn that answers them is bound to this page."""
        page = self._pages.get(ws)
        if page is not None:
            page.spoke = True
            page.heard.append(heard)

    def pages(self) -> list[Page]:
        return list(self._pages.values())

    def describe_pages(self) -> str:
        listed = [f"{p.client_id} ({'visible' if p.visible else 'hidden'} when it connected)"
                  for p in self._pages.values()]
        listed += sorted(set(self._refused.values()))
        return "; ".join(listed) or "none"

    def bound_page(self, heard: int | None) -> tuple[Page | None, str | None]:
        """The page this turn's words came from, or why there is not one."""
        spoken = [p for p in self._pages.values() if p.spoke and heard in p.heard]
        if len(spoken) == 1:
            return spoken[0], None
        if len(spoken) > 1:
            return None, ("more than one page said words with this turn's id, so none is guessed; "
                          f"name one with client. Connected pages: {self.describe_pages()}")
        return None, None

    # -- results and events ----------------------------------------------------

    def _pending_for(self, ws, msg: dict) -> Pending | None:
        pending = self._pending.get(msg.get("requestId"))
        page = self._pages.get(ws)
        if pending is None or page is None or pending.page is not page:
            return None                             # not ours, or another page's
        if msg.get("clientId") != page.client_id or msg.get("epoch") != page.epoch:
            return None
        return pending

    def result(self, ws, msg: dict) -> None:
        pending = self._pending_for(ws, msg)
        if pending is None or msg.get("status") not in STATUSES:
            return
        page = pending.page
        nav, layout = _revision(msg.get("navigationRevision")), _revision(msg.get("layoutRevision"))
        if nav is not None:
            page.navigation_revision = nav
        if layout is not None:
            page.layout_revision = layout
        self._pending.pop(pending.request_id, None)
        answer = {**msg, "events": list(pending.events)}
        problem = self._unbelievable(pending.operation, msg)
        if problem:
            log.warning("ui %s: %s", pending.request_id, problem)
            answer = {**answer, "status": "blocked", "reason": problem, "claimed": msg.get("status")}
        log.info("ui %s %s: %s", pending.request_id, pending.operation["kind"], answer["status"])
        if not pending.future.done():
            pending.future.set_result(answer)

    @staticmethod
    def _unbelievable(operation: dict, msg: dict) -> str | None:
        """Why a page's answer cannot be taken as it says, or None: an
        indication, alone or as a journey's step, is complete only once it
        has arrived, and a journey only once every step is."""
        if msg.get("status") != "completed":
            return None
        if operation["kind"] == "indicate" and msg.get("arrival") not in ARRIVALS:
            return "the page said it was done without the signal arriving"
        if operation["kind"] == "journey":
            steps = msg.get("steps")
            if not isinstance(steps, list) or len(steps) != len(operation["steps"]):
                return "the page said the journey was done without all of its steps"
            for asked, step in zip(operation["steps"], steps):
                if not isinstance(step, dict) or step.get("status") != "completed":
                    return "the page said the journey was done with a step that was not"
                if asked["kind"] == "indicate" and step.get("arrival") not in ARRIVALS:
                    return "the page said the journey was done without a signal arriving"
        return None

    def event(self, ws, msg: dict) -> None:
        pending = self._pending_for(ws, msg)
        if pending is None:
            return
        if msg.get("event") in LIFECYCLE and len(pending.events) < EVENTS_KEPT:
            pending.events.append(msg["event"])
            log.info("ui %s %s", pending.request_id, msg["event"])

    def _fail_page(self, page: Page, why: str) -> None:
        for rid, pending in list(self._pending.items()):
            if pending.page is page:
                self._pending.pop(rid, None)
                if not pending.future.done():
                    pending.future.set_result({"status": "cancelled", "reason": why,
                                               "delivery": self._delivery(pending),
                                               "events": list(pending.events)})

    @staticmethod
    def _delivery(pending: Pending) -> str:
        return DELIVERY_STARTED if pending.events else DELIVERY_UNKNOWN

    # -- requests --------------------------------------------------------------

    def target_page(self, client: str | None, origin: str | None,
                    heard: int | None = None) -> tuple[Page | None, str | None]:
        """The one page a request goes to, or why there is none."""
        if client:
            for page in self._pages.values():
                if page.client_id == client:
                    return page, None
            return None, f"no page {client!r} is connected; connected pages: {self.describe_pages()}"
        if not self._pages:
            if self._refused:
                return None, f"no JARVIS page can take it: {self.describe_pages()}"
            return None, "no JARVIS page is open"
        if origin == "user":
            page, why = self.bound_page(heard)
            if page is not None or why is not None:
                return page, why
        return None, ("no page is bound to this turn; name one with client. "
                      f"Connected pages: {self.describe_pages()}")

    @staticmethod
    def _unsupported(page: Page, operation: dict) -> str | None:
        kinds = {operation["kind"]} | {s["kind"] for s in operation.get("steps", [])}
        missing = sorted(kinds - page.capabilities)
        if missing:
            return f"page {page.client_id} does not offer {', '.join(missing)}"
        return None

    async def request(self, operation: dict, *, client: str | None, origin: str | None,
                      heard: int | None = None, turn: str | None = None,
                      expected_revision: int | None = None) -> dict:
        problem = ui_contract.operation_problem(operation)
        if problem:
            return {"status": "invalid", "reason": problem}
        page, why = self.target_page(client, origin, heard)
        if page is None:
            return {"status": "unavailable", "reason": why}
        unsupported = self._unsupported(page, operation)
        if unsupported:
            return {"status": "unavailable", "reason": unsupported}
        now = self._clock()
        rid = "ui-" + secrets.token_hex(8)
        expires = now + _PROTOCOL["limits"]["requestTtlMs"] / 1000
        future = asyncio.get_running_loop().create_future()
        pending = Pending(rid, page, operation, future, expires)
        self._pending[rid] = pending
        frame = {"type": "ui_request", "protocol": ui_contract.load().version, "requestId": rid,
                 "clientId": page.client_id, "epoch": page.epoch, "turn": turn,
                 "expectedNavigationRevision": expected_revision,
                 "expiresAt": int(expires * 1000), "operation": operation}
        if not self._send(page.ws, frame):
            self._pending.pop(rid, None)
            return {"status": "unavailable", "reason": "the page could not be reached"}
        log.info("ui %s %s sent to %s epoch %s (turn %s)", rid, operation["kind"],
                 page.client_id, page.epoch, turn)
        try:
            return await asyncio.wait_for(asyncio.shield(future),
                                          max(0.0, expires - now) + ANSWER_GRACE_SEC)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return {"status": "timeout", "reason": "the page did not answer before the request expired",
                    "delivery": self._delivery(pending), "events": list(pending.events)}


# -- the map, as the brain reads it ----------------------------------------------


def _clip(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def map_lines(page_map: Any) -> list[str]:
    """The page's map as one line per command and target, each target with
    the parts the contract gives it in that view. Labels are the page's
    (other people's words); the caller walls the whole thing."""
    if not isinstance(page_map, dict):
        return []
    c = ui_contract.load()
    limits = _PROTOCOL["limits"]
    lines: list[str] = []
    for cmd in page_map.get("commands") or []:
        if not isinstance(cmd, dict) or cmd.get("id") not in c.commands:
            continue
        args = ",".join(a for a in (cmd.get("args") or []) if isinstance(a, str))
        line = f"command {cmd['id']}({args})"
        if cmd.get("available") is False:
            line += f" unavailable:{_clip(cmd.get('reason') or '', 32)}"
        lines.append(line)
    for target in page_map.get("targets") or []:
        ref = target.get("ref") if isinstance(target, dict) else None
        if not isinstance(ref, dict) or ref.get("kind") not in c.kinds or ref.get("view") not in c.views:
            continue
        kind = c.kinds[ref["kind"]]
        pres = next((p for p in kind["presentations"] if p["view"] == ref["view"]), None)
        parts = pres.get("parts", kind["parts"]) if pres else []
        line = f"target {c.views[ref['view']]['region']} {ref['view']} {ref['kind']}"
        if ref.get("key") is not None:
            line += f" key={_clip(ref['key'], limits['keyMax'])}"
        if parts:
            line += f" parts={','.join(parts)}"
        line += f" {_clip(target.get('visibility') or '', 16)} label={_clip(target.get('label') or '', limits['labelMax'])}"
        lines.append(line)
    return lines


def map_page(page_map: Any, cursor: int, budget: int) -> str:
    """One page of the map that fits `budget` characters, from `cursor`,
    and where the next page starts if there is more."""
    shown = page_map.get("shown") if isinstance(page_map, dict) else None
    head = "shown: " + (" ".join(_clip(v, 64) for v in shown.values()) if isinstance(shown, dict) else "unknown")
    lines = map_lines(page_map)
    out, used, n = [head], len(head), max(0, cursor)
    while n < len(lines) and used + len(lines[n]) + 1 <= budget - 60:
        out.append(lines[n])
        used += len(lines[n]) + 1
        n += 1
    if n < len(lines):
        out.append(f"more: {len(lines) - n} more lines; ask ui_map again with cursor {n} "
                   "(the page may have changed meanwhile)")
    elif cursor > len(lines):
        out.append(f"cursor {cursor} is past the end ({len(lines)} lines)")
    return "\n".join(out)
