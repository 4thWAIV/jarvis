"""The brain's UI requests (jarvis/ui_control.py): one page per request, the
page his turn's words came from; an epoch per connection; answered by that
page's own result, never by sending, and an indication only at arrival; and
every way it can go wrong answered truthfully (no page, several pages, an
ambiguous turn, an unsupported protocol or operation, a stale epoch, another
page's answer, a page that goes away or reconnects, a page that never
answers, an invalid request, a claim of success without arrival).
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis import ui_contract, ui_control

NAV = {"kind": "navigate", "commandId": "right.tab.select", "args": {"tab": "network"}}
CPU = {"kind": "indicate", "reveal": "navigate",
       "target": {"region": "main", "view": "main.network", "kind": "node", "key": "n1", "part": "cpu"}}
JOURNEY = {"kind": "journey", "steps": [NAV, CPU]}
ALL = ["map", "navigate", "indicate", "clear", "journey"]


class Ws:
    def __init__(self, name):
        self.name, self.frames = name, []

    def requests(self):
        return [f for f in self.frames if f["type"] == "ui_request"]


def hello(clients, ws, client_id, **extra):
    return clients.hello(ws, {"type": "ui_hello", "protocol": ui_contract.load().version,
                              "clientId": client_id, "capabilities": ALL,
                              "navigationRevision": 0, "layoutRevision": 0, "visible": True, **extra})


def make():
    clients = ui_control.UiClients(lambda ws, frame: ws.frames.append(frame) is None)
    return clients


async def _answer_when_sent(ws, status="completed", before=0, **extra):
    while len(ws.requests()) <= before:
        await asyncio.sleep(0)
    req = ws.requests()[-1]
    return req, {"type": "ui_result", "requestId": req["requestId"], "clientId": req["clientId"],
                 "epoch": req["epoch"], "status": status, "navigationRevision": 1,
                 "layoutRevision": 3, "effects": [], **extra}


async def _ask(clients, ws, operation, answer=None, heard=None, **result):
    """Send `operation` on his turn, answer it from `ws` as `answer` does."""
    before = len(ws.requests())
    task = asyncio.create_task(clients.request(operation, client=None, origin="user", heard=heard))
    req, res = await _answer_when_sent(ws, before=before, **result)
    if answer is not None:
        answer(req)
    clients.result(ws, res)
    return req, await task


# -- the handshake ------------------------------------------------------------------


def test_a_page_gets_an_epoch_and_a_new_one_on_reconnect():
    clients = make()
    a = Ws("a")
    first = hello(clients, a, "page-1")
    second = hello(clients, a, "page-1")
    assert first["type"] == "ui_welcome" and second["epoch"] > first["epoch"]
    assert hello(clients, Ws("b"), "") is None


def test_a_page_of_another_protocol_is_refused_and_named_as_the_reason():
    async def run():
        clients = make()
        old = Ws("old")
        assert hello(clients, old, "page-old", protocol=99) is None
        out = await clients.request(NAV, client=None, origin="user")
        assert out["status"] == "unavailable" and "protocol 99" in out["reason"]
        assert not old.requests()
        clients.gone(old)
        out = await clients.request(NAV, client=None, origin="user")
        assert out["reason"] == "no JARVIS page is open"
    asyncio.run(run())


def test_the_same_page_on_a_new_connection_ends_the_old_one():
    async def run():
        clients = make()
        a1, a2 = Ws("a1"), Ws("a2")
        first = hello(clients, a1, "page-a")
        clients.note_turn(a1, 5)
        task = asyncio.create_task(clients.request(CPU, client=None, origin="user", heard=5))
        await _answer_when_sent(a1)
        second = hello(clients, a2, "page-a")
        out = await task
        assert out["status"] == "cancelled" and out["delivery"] == "unknown"
        assert second["epoch"] > first["epoch"] and [p.ws for p in clients.pages()] == [a2]
    asyncio.run(run())


def test_an_operation_the_page_does_not_offer_is_not_sent():
    async def run():
        clients = make()
        a = Ws("a")
        hello(clients, a, "page-a", capabilities=["navigate", "map"])
        clients.note_turn(a, 1)
        out = await clients.request(JOURNEY, client=None, origin="user", heard=1)
        assert out["status"] == "unavailable" and "indicate, journey" in out["reason"]
        assert not a.requests()
    asyncio.run(run())


# -- the page of this turn -------------------------------------------------------


def test_a_request_goes_to_the_page_of_this_turn_only():
    async def run():
        clients = make()
        a, b = Ws("a"), Ws("b")
        hello(clients, a, "page-a")
        hello(clients, b, "page-b")
        clients.note_turn(a, 3)
        clients.note_turn(b, 9)
        req, out = await _ask(clients, b, NAV, heard=9)
        assert not a.requests()
        assert req["clientId"] == "page-b" and req["operation"] == NAV and req["expiresAt"] > 0
        assert out["status"] == "completed"
    asyncio.run(run())


def test_words_from_another_page_during_the_turn_do_not_take_it_over():
    async def run():
        clients = make()
        a, b = Ws("a"), Ws("b")
        hello(clients, a, "page-a")
        hello(clients, b, "page-b")
        clients.note_turn(a, 7)          # the turn being answered
        clients.note_turn(b, 1)          # said on another page meanwhile
        clients.note_turn(a, 8)          # and more from his own
        await _ask(clients, a, NAV, heard=7)
        assert not b.requests()
    asyncio.run(run())


def test_two_pages_that_both_said_the_turn_s_id_are_not_guessed_between():
    async def run():
        clients = make()
        a, b = Ws("a"), Ws("b")
        hello(clients, a, "page-a")
        hello(clients, b, "page-b")
        clients.note_turn(a, 4)
        clients.note_turn(b, 4)
        out = await clients.request(NAV, client=None, origin="user", heard=4)
        assert out["status"] == "unavailable"
        assert "page-a" in out["reason"] and "page-b" in out["reason"]
        assert not a.requests() and not b.requests()
    asyncio.run(run())


def test_without_a_bound_page_the_pages_are_listed_not_guessed():
    async def run():
        clients = make()
        a, b = Ws("a"), Ws("b")
        hello(clients, a, "page-a")
        hello(clients, b, "page-b", visible=False)
        clients.note_turn(a, 2)
        out = await clients.request(NAV, client=None, origin="system", heard=2)
        assert out["status"] == "unavailable"
        assert "page-a" in out["reason"] and "page-b (hidden" in out["reason"]
        assert not a.requests() and not b.requests()
        none = await make().request(NAV, client=None, origin="user")
        assert none["reason"] == "no JARVIS page is open"
    asyncio.run(run())


def test_a_named_page_is_used_and_an_unknown_one_is_refused():
    async def run():
        clients = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        missing = await clients.request(NAV, client="page-z", origin="system")
        assert missing["status"] == "unavailable" and "page-z" in missing["reason"]
        task = asyncio.create_task(clients.request(NAV, client="page-a", origin="system"))
        req, res = await _answer_when_sent(a)
        clients.result(a, res)
        assert (await task)["status"] == "completed"
    asyncio.run(run())


# -- answers -----------------------------------------------------------------------


def test_an_answer_from_another_page_or_epoch_is_ignored():
    async def run():
        clients = make()
        a, b = Ws("a"), Ws("b")
        hello(clients, a, "page-a")
        hello(clients, b, "page-b")
        clients.note_turn(a, 1)
        task = asyncio.create_task(clients.request(CPU, client=None, origin="user", heard=1))
        req, result = await _answer_when_sent(a)
        clients.result(b, {**result, "clientId": "page-b"})        # another page
        clients.result(a, {**result, "epoch": req["epoch"] + 7})   # a stale epoch
        clients.result(a, {**result, "status": "shown"})           # not a status
        clients.event(b, {"requestId": req["requestId"], "clientId": "page-b",
                          "epoch": req["epoch"], "event": "arrived"})
        await asyncio.sleep(0)
        assert not task.done()
        for event in ("resolving", "emitting", "travelling", "arrived"):
            clients.event(a, {"type": "ui_event", "requestId": req["requestId"], "clientId": "page-a",
                              "epoch": req["epoch"], "event": event})
        clients.result(a, {**result, "arrival": "animated"})
        clients.result(a, {**result, "status": "cancelled"})       # a duplicate answer
        out = await task
        assert out["status"] == "completed" and out["arrival"] == "animated"
        assert out["events"] == ["resolving", "emitting", "travelling", "arrived"]
        page = clients.pages()[0]
        assert (page.navigation_revision, page.layout_revision) == (1, 3)
    asyncio.run(run())


def test_an_indication_is_not_complete_without_arrival():
    async def run():
        clients = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a, 1)
        _, out = await _ask(clients, a, CPU, heard=1)
        assert out["status"] == "blocked" and out["claimed"] == "completed"
        _, out = await _ask(clients, a, CPU, heard=1, arrival="static-reduced-motion")
        assert out["status"] == "completed"
    asyncio.run(run())


def test_a_journey_is_complete_only_when_every_step_is_and_every_signal_arrived():
    async def run():
        clients = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a, 1)
        nav, cpu = {"status": "completed", "effects": []}, {"status": "completed", "effects": []}
        _, out = await _ask(clients, a, JOURNEY, heard=1, steps=[nav, cpu])
        assert out["status"] == "blocked"
        _, out = await _ask(clients, a, JOURNEY, heard=1, steps=[nav])
        assert out["status"] == "blocked"
        _, out = await _ask(clients, a, JOURNEY, heard=1, steps=[nav, {**cpu, "arrival": "animated"}])
        assert out["status"] == "completed"
        _, out = await _ask(clients, a, JOURNEY, heard=1, status="cancelled",
                            steps=[nav], reason="a person navigated")
        assert out["status"] == "cancelled" and out["reason"] == "a person navigated"
    asyncio.run(run())


def test_the_page_s_own_refusals_pass_through_unchanged():
    async def run():
        clients = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a, 1)
        for status, reason in (("expired", "expired"), ("stale", "the page has moved on"),
                               ("not_found", "not-mounted"), ("busy", "source-busy"),
                               ("not_visible", "the page is hidden (another tab or minimised)")):
            _, out = await _ask(clients, a, CPU, heard=1, status=status, reason=reason)
            assert (out["status"], out["reason"]) == (status, reason)
    asyncio.run(run())


def test_a_page_going_away_is_not_success_and_says_whether_it_had_started():
    async def run():
        clients = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a, 1)
        task = asyncio.create_task(clients.request(CPU, client=None, origin="user", heard=1))
        req, _ = await _answer_when_sent(a)
        clients.event(a, {"requestId": req["requestId"], "clientId": "page-a",
                          "epoch": req["epoch"], "event": "emitting"})
        clients.gone(a)
        out = await task
        assert out["status"] == "cancelled" and out["delivery"] == "started"
        assert out["events"] == ["emitting"]
    asyncio.run(run())


def test_a_page_that_never_answers_times_out_at_the_request_s_deadline(monkeypatch):
    async def run():
        clients = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a, 1)
        monkeypatch.setitem(ui_contract.load().protocol["limits"], "requestTtlMs", 20)
        monkeypatch.setattr(ui_control, "ANSWER_GRACE_SEC", 0.01)
        out = await clients.request(NAV, client=None, origin="user", heard=1)
        assert out["status"] == "timeout" and out["delivery"] == "unknown"
        (req,) = a.requests()
        clients.result(a, {"requestId": req["requestId"], "clientId": "page-a",
                           "epoch": req["epoch"], "status": "completed"})   # too late: ignored
    asyncio.run(run())


def test_an_unreachable_page_is_said_so():
    async def run():
        clients = ui_control.UiClients(lambda ws, frame: False)
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a, 1)
        out = await clients.request(NAV, client=None, origin="user", heard=1)
        assert out == {"status": "unavailable", "reason": "the page could not be reached"}
    asyncio.run(run())


@pytest.mark.parametrize("operation", [
    {"kind": "navigate", "commandId": "page.eval", "args": {}},
    {"kind": "navigate", "commandId": "right.tab.select", "args": {"tab": "network", "selector": "#x"}},
    {"kind": "indicate", "target": {"region": "main", "view": "main.network", "kind": "node"}},
    {"kind": "indicate", "target": {"region": "right", "view": "main.network", "kind": "node", "key": "n1"}},
    {"kind": "journey", "steps": [{"kind": "clear"}]},
    {"kind": "journey", "steps": [NAV] * 7},
    {"kind": "click", "selector": "#send"},
])
def test_an_invalid_request_never_reaches_a_page(operation):
    async def run():
        clients = make()
        a = Ws("a")
        hello(clients, a, "page-a")
        clients.note_turn(a, 1)
        out = await clients.request(operation, client=None, origin="user", heard=1)
        assert out["status"] == "invalid"
        assert not a.requests()
    asyncio.run(run())


# -- the map ---------------------------------------------------------------------------


def _map(n):
    return {"shown": {"right": "right.network", "main": "main.conversation"},
            "commands": [{"id": "right.tab.select", "args": ["tab"]},
                         {"id": "network.close", "args": [], "available": False, "reason": "no-entity"},
                         {"id": "page.eval", "args": []}],                       # not declared: dropped
            "targets": [{"ref": {"region": "right", "view": "right.network", "kind": "node", "key": f"n{i}"},
                         "label": f"Mini {i}\nline", "visibility": "visible"} for i in range(n)]
                       + [{"ref": {"view": "nowhere", "kind": "node"}, "label": "x"}]}


def test_the_map_names_commands_and_targets_with_their_parts_from_the_contract():
    lines = ui_control.map_lines(_map(1))
    assert lines[0] == "command right.tab.select(tab)"
    assert lines[1] == "command network.close() unavailable:no-entity"
    assert lines[2] == ("target right right.network node key=n0 parts=name,presence,human,cpu,memory,pace "
                        "visible label=Mini 0 line")
    assert len(lines) == 3
    assert ui_control.map_lines("nonsense") == []


def test_a_long_map_comes_a_page_at_a_time_within_its_budget():
    whole = _map(60)
    first = ui_control.map_page(whole, 0, 900)
    assert len(first) <= 900 and first.startswith("shown: right.network main.conversation")
    more = first.rsplit("\n", 1)[1]
    assert more.startswith("more: ") and "cursor" in more
    cursor = int(more.split("cursor ")[1].split()[0])
    second = ui_control.map_page(whole, cursor, 900)
    assert f"key=n{cursor - 2}" in second and f"key=n{cursor - 3} " not in second
    last = ui_control.map_page(whole, len(ui_control.map_lines(whole)) - 1, 900)
    assert "more:" not in last
    assert "past the end" in ui_control.map_page(whole, 999, 900)
