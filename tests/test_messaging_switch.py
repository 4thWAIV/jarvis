"""This JARVIS's messaging switch (Settings, Team): stored in the messaging
store, off until set, kept across a restart, forced on only by
JARVIS_MESSAGING, and live — the loops start and stop the moment it changes
and every page is told to read the Queue again. Also the team's switch as
this JARVIS forwards it to the team server."""
from __future__ import annotations


import httpx
import pytest
from fastapi import FastAPI

from jarvis import messaging_ports as ports
from jarvis import messaging_runtime, team_api
from jarvis.arrival_announcer import Wording
from jarvis.messaging_store import SqliteMessagingStore

NODE = "00000000-0000-4000-8000-000000007222"


class Unpaired:
    saved = None
    signed_out = False
    generation = 1


class Captures:
    def open_for_review(self, draft, readback, recipient_label) -> int:
        return 1

    def close(self, capture_id) -> None:
        pass


def runtime(forced=lambda: False) -> tuple[messaging_runtime.MessagingRuntime, list[dict]]:
    frames: list[dict] = []

    async def notify(*a, **k):
        pass

    built = messaging_runtime.MessagingRuntime(
        connection=Unpaired(), captures=Captures(), send_frame=frames.append, notify=notify,
        wording=Wording(join=", ".join, number=str, gist=lambda s: s),
        network_raw=lambda: None, enabled=forced, node_id=lambda: NODE)
    return built, frames


def test_the_store_keeps_the_switch_and_it_starts_off(tmp_path):
    store = SqliteMessagingStore(tmp_path / "jarvis.db")
    assert store.switched_on() is False
    store.set_switched_on(True)
    assert SqliteMessagingStore(tmp_path / "jarvis.db").switched_on() is True
    store.set_switched_on(False)
    assert store.switched_on() is False


@pytest.mark.asyncio
async def test_switching_on_and_off_starts_and_stops_the_loops_at_once():
    rt, frames = runtime()
    assert rt.switch() == {"enabled": False, "forced": False}
    assert await rt.delivery.availability() == "switched_off"
    rt.start()
    assert rt._tasks == [] and rt.delivery._task is None, "off: nothing runs"

    assert await rt.set_switch(True) == {"enabled": True, "forced": False}
    assert len(rt._tasks) == 2 and rt.delivery._task is not None
    assert await rt.delivery.availability() == "not_paired"
    assert frames[-1] == {"type": ports.MESSAGES_HINT}

    assert (await rt.set_switch(False))["enabled"] is False
    assert rt._tasks == [] and rt.delivery._task is None
    assert await rt.delivery.availability() == "switched_off"
    assert len(frames) == 2

    await rt.set_switch(True)
    again, _ = runtime()                      # a restart over the same data directory
    assert again.enabled() is True
    await rt.stop()


@pytest.mark.asyncio
async def test_the_variable_forces_it_on_and_the_page_is_told():
    rt, _ = runtime(forced=lambda: True)
    assert rt.switch() == {"enabled": True, "forced": True}
    assert (await rt.set_switch(False)) == {"enabled": True, "forced": True}
    await rt.stop()


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_page_reads_and_sets_the_switch():
    rt, _ = runtime()
    app = FastAPI()
    messaging_runtime.mount(app, lambda: rt)
    async with _client(app) as c:
        assert (await c.get(messaging_runtime.SWITCH_PATH)).json() == {
            "enabled": False, "forced": False}
        r = await c.put(messaging_runtime.SWITCH_PATH, json={"enabled": True})
        assert r.json() == {"enabled": True, "forced": False}
        assert rt.delivery._task is not None
        r = await c.put(messaging_runtime.SWITCH_PATH, json={"enabled": "maybe"})
        assert r.status_code == 422
    await rt.stop()


@pytest.mark.asyncio
async def test_before_startup_the_switch_says_messaging_is_unavailable():
    app = FastAPI()
    messaging_runtime.mount(app, lambda: None)
    async with _client(app) as c:
        r = await c.put(messaging_runtime.SWITCH_PATH, json={"enabled": True})
    assert r.json()["error"]["code"] == "messaging_unavailable"


class Forwarding:
    """The team connection as team_api forwards through it."""

    def __init__(self, status=200):
        self.calls, self.status = [], status

    async def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        return self.status, {"enabled": bool(body and body["enabled"]),
                             "changed_at": None, "changed_by_github_id": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("status, told", [(200, 1), (403, 0)])
async def test_the_team_switch_is_forwarded_and_a_change_is_asked_again(monkeypatch,
                                                                         status, told):
    connection = Forwarding(status)
    monkeypatch.setattr(team_api.team_client, "connection", connection)
    changed = []
    app = FastAPI()
    team_api.mount(app, on_team_messaging=lambda: changed.append(1))
    async with _client(app) as c:
        assert (await c.get("/api/team/messaging/team")).status_code == status
        r = await c.put("/api/team/messaging/team", json={"enabled": True})
    assert r.status_code == status
    assert connection.calls == [("GET", "/api/v1/team/messaging", None),
                                ("PUT", "/api/v1/team/messaging", {"enabled": True})]
    assert len(changed) == told


@pytest.mark.asyncio
async def test_forgetting_capabilities_asks_the_team_server_again():
    rt, _ = runtime()
    rt.delivery._capabilities["server"] = (1, 0.0, ["messaging.v1"])
    rt.delivery.forget_capabilities()
    assert rt.delivery._capabilities == {} and rt.delivery._wake.is_set()
