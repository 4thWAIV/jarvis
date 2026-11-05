"""Messages between people have no switch: a JARVIS signed in to a team
server that offers messaging.v1 has messaging, its loops running, with
nothing set anywhere; signed out, it has none and its loops stop. Every
page is told to read the Queue again when they start or stop."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from jarvis import messaging_ports as ports
from jarvis import messaging_runtime, team_api
from jarvis.arrival_announcer import Wording
from jarvis.messaging_wire import CAPABILITY

NODE = "00000000-0000-4000-8000-000000007222"
SERVER = "00000000-0000-4000-8000-0000000000aa"


class Connection:
    """The team connection as messaging sees it: signed in or not. Every
    call to the team server is answered as unreachable."""

    def __init__(self, signed_in: bool):
        self.saved = {"server_id": SERVER, "me": {"github_id": 1}} if signed_in else None
        self.signed_out = False
        self.generation = 1

    async def call(self, method, path, body=None):
        return 503, {}


class Captures:
    def open_for_review(self, draft, readback, recipient_label) -> int:
        return 1

    def close(self, capture_id) -> None:
        pass


def runtime(connection) -> tuple[messaging_runtime.MessagingRuntime, list[dict]]:
    frames: list[dict] = []

    async def notify(*a, **k):
        pass

    built = messaging_runtime.MessagingRuntime(
        connection=connection, captures=Captures(), send_frame=frames.append, notify=notify,
        wording=Wording(join=", ".join, number=str, gist=lambda s: s),
        network_raw=lambda: None, node_id=lambda: NODE)
    return built, frames


def offers_messaging(rt: messaging_runtime.MessagingRuntime) -> None:
    """The team server's last answer to "what do you offer", already known."""
    rt.delivery._capabilities[SERVER] = (1, asyncio.get_running_loop().time(), [CAPABILITY])


@pytest.mark.asyncio
async def test_signed_in_it_has_messaging_with_no_switch_set_and_stops_when_signed_out():
    connection = Connection(signed_in=True)
    rt, frames = runtime(connection)
    offers_messaging(rt)
    assert await rt.delivery.availability() == "available"
    await rt.follow_once()
    assert rt.running() and rt.delivery._task is not None
    assert frames[-1] == {"type": ports.MESSAGES_HINT}

    connection.signed_out = True
    assert await rt.delivery.availability() == "not_paired"
    await rt.follow_once()
    assert not rt.running() and rt.delivery._task is None
    assert frames.count({"type": ports.MESSAGES_HINT}) == 2
    await rt.stop()


@pytest.mark.asyncio
async def test_signed_out_it_has_no_messaging_and_nothing_runs():
    rt, frames = runtime(Connection(signed_in=False))
    assert await rt.delivery.availability() == "not_paired"
    await rt.follow_once()
    assert not rt.running() and rt.delivery._task is None and frames == []
    await rt.stop()


@pytest.mark.asyncio
async def test_signed_in_to_a_server_without_messaging_nothing_runs():
    rt, _ = runtime(Connection(signed_in=True))
    rt.delivery._capabilities[SERVER] = (1, asyncio.get_running_loop().time(), [])
    assert await rt.delivery.availability() == "unsupported"
    await rt.follow_once()
    assert not rt.running()
    await rt.stop()


@pytest.mark.asyncio
async def test_start_follows_the_connection_and_stop_ends_everything():
    rt, _ = runtime(Connection(signed_in=True))
    offers_messaging(rt)
    rt.start()
    for _ in range(100):
        if rt.running():
            break
        await asyncio.sleep(0.01)
    assert rt.running()
    await rt.stop()
    assert not rt.running() and rt.delivery._task is None and rt._follower is None


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_before_startup_the_queue_says_messaging_did_not_start():
    app = FastAPI()
    messaging_runtime.mount(app, lambda: None)
    async with _client(app) as c:
        r = await c.post("/api/team/messages/drafts",
                         json={"recipient_github_id": 2, "text": "x"})
    assert r.json()["error"]["code"] == "messaging_unavailable"
    assert r.json()["error"]["availability"] == "not_started"


@pytest.mark.asyncio
async def test_there_are_no_switch_routes():
    app = FastAPI()
    messaging_runtime.mount(app, lambda: None)
    team_api.mount(app)
    async with _client(app) as c:
        for path in ("/api/team/messaging/local", "/api/team/messaging/team"):
            assert (await c.get(path)).status_code == 404
            assert (await c.put(path, json={"enabled": True})).status_code in (404, 405)
