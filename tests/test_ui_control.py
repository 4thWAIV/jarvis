"""The brain's UI requests (jarvis/ui_control.py): one page per request, the
page of this turn; an epoch per connection; answered by that page's own
result, never by sending; and every way it can go wrong answered truthfully
(no page, several pages, a stale epoch, another page's answer, a page that
goes away, a page that never answers, an invalid request).
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis import ui_contract, ui_control

NAV = {"kind": "navigate", "commandId": "right.tab.select", "args": {"tab": "network"}}
CPU = {"kind": "indicate", "reveal": "navigate",
       "target": {"region": "main", "view": "main.network", "kind": "node", "key": "n1", "part": "cpu"}}


class Ws:
    def __init__(self, name):
        self.name, self.frames = name, []


def hello(clients, ws, client_id, **extra):
    return clients.hello(ws, {"type": "ui_hello", "protocol": ui_contract.load().version,
                              "clientId": client_id, "capabilities": ["navigate"],
                              "navigationRevision": 0, "visible": True, **extra})


def make():
    sent = []
    clients = ui_control.UiClients(lambda ws, frame: (ws.frames.append(frame), sent.append((ws, frame)))[-1] is None)
    return clients, sent


async def _answer_when_sent(ws, status="completed", **extra):
    while not ws.frames or ws.frames[-1].get("type") != "ui_request":
        await asyncio.sleep(0)
    req = ws.frames[-1]
    return req, {"type": "ui_result", "requestId": req["requestId"], "clientId": req["clientId"],
                 "epoch": req["epoch"], "status": status, "navigationRevision": 1, **extra}


def test_a_page_gets_an_epoch_and_a_new_one_on_reconnect():
    clients, _ = make()
    a = Ws("a")
    first = hello(clients, a, "page-1")
    second = hello(clients, a, "page-1")
    assert first["type"] == "ui_welcome" and second["epoch"] > first["epoch"]
    assert hello(clients, a, "page-1", protocol=99) is None
    assert hello(clients, Ws("b"), "") is None


def test_a_request_goes_to_the_page_of_this_turn_only():
    async def run():
        clients, sent = make()
        a, b = Ws("a"), Ws("b")
        hello(clients, a, "page-a")
        hello(clients, b, "page-b")
        clients.note_turn(b)
        task = asyncio.create_task(clients.request(NAV, client=None, origin="user"))
        req, result = await _answer_when_sent(b)
        assert not [f for f in a.frames if f["type"] == "ui_request"]
        assert req["clientId"] == "page-b" and req["operation"] == NAV
        assert req["expiresAt"] > 0
        clients.result(b, result)
        out = await task
        assert out["status"] == "completed"
    asyncio.run(run())


def test_without_a_bound_page_the_pages_are_listed_not_guessed():
    async def run():
        clients, _ = make()
        a, b = Ws("a"), Ws("b")
        hello(clients, a, "page-a")
        hello(clients, b, "page-b")
        out = await clients.request(NAV, client=None, origin="system")
        assert out["status"] == "unavailable"
        assert "page-a" in out["reason"] and "page-b" in out["reason"]
        assert not [f for ws in (a, b) for f in ws.frames if f["type"] == "ui_request"]
        none = await ui_control.UiClients(lambda ws, f: True).request(NAV, client=None, origin="user")
        assert none["reason"] == "no JARVIS page is open"
    asyncio.run(run())


def test_an_answer_from_another_page_or_epoch_is_ignored():
    async def run():
        clients, _ = make()
        a, b = Ws("a"), Ws("b")
        hello(clients, a, "page-a")
        hello(clients, b, "page-b")
        clients.note_turn(a)
        task = asyncio.create_task(clients.request(CPU, client=None, origin="user"))
        req, result = await _answer_when_sent(a)
        clients.result(b, {**result, "clientId": "page-b"})        # another page
        clients.result(a, {**result, "epoch": req["epoch"] + 7})   # a stale epoch
        clients.result(a, {**result, "status": "shown"})           # not a status
        await asyncio.sleep(0)
        assert not task.done()
        clients.event(a, {"type": "ui_event", "requestId": req["requestId"], "event": "arrived"})
        clients.result(a, {**result, "arrival": "animated"})
        out = await task
        assert out["arrival"] == "animated" and out["events"] == ["arrived"]
    asyncio.run(run())


def test_a_page_going_away_is_not_success():
    async def run():
        clients, _ = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a)
        task = asyncio.create_task(clients.request(CPU, client=None, origin="user"))
        await _answer_when_sent(a)
        clients.gone(a)
        out = await task
        assert out["status"] == "cancelled" and out["delivery"] == "unknown"
    asyncio.run(run())


def test_a_page_that_never_answers_times_out(monkeypatch):
    async def run():
        clients, _ = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a)
        limits = ui_contract.load().protocol["limits"]
        monkeypatch.setitem(limits, "resultWaitMs", 20)
        out = await clients.request(NAV, client=None, origin="user")
        assert out["status"] == "timeout" and out["delivery"] == "unknown"
    asyncio.run(run())


@pytest.mark.parametrize("operation", [
    {"kind": "navigate", "commandId": "page.eval", "args": {}},
    {"kind": "indicate", "target": {"region": "main", "view": "main.network", "kind": "node"}},
    {"kind": "click", "selector": "#send"},
])
def test_an_invalid_request_never_reaches_a_page(operation):
    async def run():
        clients, _ = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a)
        out = await clients.request(operation, client=None, origin="user")
        assert out["status"] == "invalid"
        assert not [f for f in a.frames if f["type"] == "ui_request"]
    asyncio.run(run())
