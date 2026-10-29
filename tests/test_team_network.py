"""The network exchange (team_client.py) and GET /api/team/network (team_api.py).

A stand-in team server answers through httpx.MockTransport, as in
test_team_client: the node update goes out every five seconds, the team
server's snapshot comes back and is stored as it was sent, and the route
returns it unchanged in its envelope. No network, no real Keychain.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jarvis import data_paths, team_api, team_client
from jarvis.team_client import TeamConnection
from tests.test_team_client import FakeServer, connect, public

SELF_ID = "6f0c1d8e-3b7a-4f0e-9b1c-2a4d5e6f7a80"
KEY = "bmV0d29yay1hY2NvdW50LWtleS1mb3ItdGVzdHMtb25seQ"
LOCAL_RESOURCES = {"state": "measured", "scope": "host", "cpu_percent": 12.0,
                   "memory_used_bytes": 1, "memory_total_bytes": 2, "age_ms": 0}


def _health(database="connected"):
    return {"database": database, "database_ping_ms": 9.5 if database == "connected" else None,
            "database_checked_at": "2026-10-28T22:40:01Z", "last_write_ok_at": "2026-10-28T22:40:00Z",
            "last_database_error": None if database == "connected" else "ServerSelectionTimeoutError",
            "database_failures_in_a_row": 0 if database == "connected" else 3, "uptime_sec": 60,
            "version": "1.4.0", "commit": "c",
            "resources": {"state": "measured", "scope": "container", "cpu_percent": 3.0,
                          "memory_used_bytes": 10, "memory_total_bytes": 20, "age_ms": 100},
            "history": []}


def _snapshot_bytes(revision=41, cpu=77.0):
    """A snapshot as the team server writes it: compact, its own key order
    and float spelling, so a re-encoding would show."""
    node = {"node_id": SELF_ID, "owner_github_id": 1, "owner_login": "tistocks",
            "label": "JARVIS on test", "jarvis_version": "0.1.0", "os": "macOS-26",
            "presence": "online", "last_seen_at": "2026-10-28T22:40:03.120000Z",
            "resources": {"state": "measured", "scope": "host", "cpu_percent": cpu,
                          "memory_used_bytes": 5, "memory_total_bytes": 10, "age_ms": 1200},
            "brains": {"configured": 1, "measured": 1, "average_remaining_percent": 50.0,
                       "pace_points": -2.0, "accounts": []},
            "activity_state": "available", "last_human_activity_at": "2026-10-28T22:31:03Z",
            "last_human_activity_source": "gui"}
    return json.dumps({"revision": revision, "server_time": "2026-10-28T22:40:03.5Z",
                       "you_node_id": SELF_ID, "nodes": [node], "server": _health()},
                      separators=(",", ":")).replace("77.0", "77.00").encode()


class NetworkServer(FakeServer):
    """FakeServer with the status's network key and the network route."""

    def __init__(self):
        super().__init__()
        self.updates: list[dict] = []
        self.answer = lambda: httpx.Response(200, content=_snapshot_bytes(),
                                             headers={"content-type": "application/json"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == team_client.NETWORK_PATH and not self.down:
            self.calls.append((request.method, request.url.path))
            if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.valid_tokens:
                return httpx.Response(401, json={"error": {"code": "session_revoked",
                                                           "message": "Sign in again."}})
            self.updates.append(json.loads(request.content))
            return self.answer()
        resp = super().handler(request)
        if request.url.path == "/api/v1/status" and resp.status_code == 200:
            return httpx.Response(200, json={**json.loads(resp.content), "network_account_key": KEY})
        return resp

    def network_calls(self) -> int:
        return sum(1 for c in self.calls if c[1] == team_client.NETWORK_PATH)


@pytest.fixture
def server():
    return NetworkServer()


@pytest.fixture
def keys():
    return []


@pytest.fixture
def conn(server, keys):
    c = TeamConnection(store=team_client.connection.store,
                       transport=httpx.MockTransport(server.handler), resolver=public)

    def node_update(key):
        keys.append(key)
        return {"node_id": SELF_ID, "resources": LOCAL_RESOURCES, "brains": []}

    c._node_update = node_update
    return c


def _status(conn) -> dict:
    return json.loads(conn.network_status())


# -- the cache is the team server's answer ------------------------------------------

@pytest.mark.asyncio
async def test_the_cache_is_the_team_servers_answer_byte_for_byte(conn, server):
    await connect(conn, server)
    await conn.exchange_once()
    assert conn.network.raw == _snapshot_bytes()
    body = conn.network_status()
    # The route's body carries those bytes as they came, not re-encoded.
    assert body.startswith(b'{"snapshot":' + _snapshot_bytes() + b",")
    status = json.loads(body)
    assert set(status) == {"snapshot", "received_at", "stale", "last_error", "round_trip_ms",
                           "last_contact_at", "connection"}
    assert status["stale"] is False and status["last_error"] is None
    assert status["connection"] == "connected"
    assert isinstance(status["round_trip_ms"], int) and status["round_trip_ms"] >= 0
    assert status["received_at"] and status["last_contact_at"]


@pytest.mark.asyncio
async def test_the_self_node_shows_the_stand_ins_values_not_local_ones(conn, server):
    await connect(conn, server)
    await conn.exchange_once()
    sent = server.updates[-1]
    assert sent["resources"]["cpu_percent"] == 12.0          # what this Mac measured
    mine = _status(conn)["snapshot"]["nodes"][0]
    assert mine["resources"]["cpu_percent"] == 77.0          # what the team server said
    assert mine["brains"]["pace_points"] == -2.0


@pytest.mark.asyncio
async def test_nothing_until_the_first_snapshot(conn):
    status = _status(conn)
    assert status["snapshot"] is None and status["stale"] is False
    assert status["received_at"] is None and status["round_trip_ms"] is None
    assert status["connection"] == "disconnected"


@pytest.mark.asyncio
async def test_an_older_revision_never_replaces_a_newer_one(conn, server):
    await connect(conn, server)
    server.answer = lambda: httpx.Response(200, content=_snapshot_bytes(revision=50))
    await conn.exchange_once()
    server.answer = lambda: httpx.Response(200, content=_snapshot_bytes(revision=49, cpu=1.0))
    await conn.exchange_once()
    assert _status(conn)["snapshot"]["revision"] == 50


@pytest.mark.asyncio
async def test_an_unreadable_answer_is_a_failure_not_a_snapshot(conn, server):
    await connect(conn, server)
    await conn.exchange_once()
    server.answer = lambda: httpx.Response(200, content=b'{"revision": "x"}')
    await conn.exchange_once()
    assert conn.network.raw == _snapshot_bytes()
    assert _status(conn)["stale"] is True


# -- failures -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_timeout_keeps_the_last_values_stale_with_no_round_trip(conn, server):
    await connect(conn, server)
    await conn.exchange_once()

    def timed_out():
        raise httpx.ReadTimeout("slow")

    server.answer = timed_out
    await conn.exchange_once()
    status = _status(conn)
    assert conn.network.raw == _snapshot_bytes()
    assert status["stale"] is True and status["round_trip_ms"] is None
    assert "ReadTimeout" in status["last_error"]
    assert status["snapshot"]["nodes"][0]["resources"]["cpu_percent"] == 77.0


@pytest.mark.asyncio
async def test_a_503_with_health_takes_the_health_and_keeps_the_nodes(conn, server):
    await connect(conn, server)
    await conn.exchange_once()
    before = _status(conn)["snapshot"]
    server.answer = lambda: httpx.Response(503, json=_health("unreachable"))
    await conn.exchange_once()
    status = _status(conn)
    assert status["snapshot"]["nodes"] == before["nodes"]
    assert status["snapshot"]["revision"] == before["revision"]
    assert status["snapshot"]["server"] == _health("unreachable")
    assert status["stale"] is True
    assert isinstance(status["round_trip_ms"], int)          # a 503 with health is an answer
    assert status["last_error"]


@pytest.mark.asyncio
async def test_a_503_without_health_is_an_ordinary_failure(conn, server):
    await connect(conn, server)
    await conn.exchange_once()
    server.answer = lambda: httpx.Response(503, json={"error": {"code": "dependency_unavailable",
                                                                "message": "No database."}})
    await conn.exchange_once()
    status = _status(conn)
    assert status["snapshot"]["server"] == _health()
    assert status["stale"] is True and status["round_trip_ms"] is None
    assert status["last_error"] == "No database."


@pytest.mark.asyncio
async def test_an_old_snapshot_is_stale_even_without_a_failure(conn, server, monkeypatch):
    clock = [1_000.0]
    monkeypatch.setattr(team_client, "_now", lambda: clock[0])
    await connect(conn, server)
    await conn.exchange_once()
    clock[0] += team_client.NETWORK_STALE_AFTER_SEC + 1
    assert _status(conn)["stale"] is True


@pytest.mark.asyncio
async def test_backoff_and_retry_after(conn, server):
    await connect(conn, server)
    assert conn.network_delay() == team_client.NETWORK_INTERVAL_SEC
    server.answer = lambda: httpx.Response(429, json={"error": {"code": "rate_limited",
                                                                "message": "Slow down."}},
                                           headers={"Retry-After": "40"})
    await conn.exchange_once()
    assert conn.network_delay() == 40
    server.down = True
    for _ in range(10):
        await conn.exchange_once()
    assert conn.network_delay() == team_client.NETWORK_MAX_BACKOFF_SEC
    server.down = False
    server.answer = lambda: httpx.Response(200, content=_snapshot_bytes())
    await conn.exchange_once()
    assert conn.network_delay() == team_client.NETWORK_INTERVAL_SEC


@pytest.mark.asyncio
async def test_a_401_on_the_exchange_leaves_the_judgement_to_the_status_poll(conn, server):
    await connect(conn, server)
    conn._wake.clear()
    server.valid_tokens.clear()
    await conn.exchange_once()
    assert conn.snapshot()["state"] != "signed_out"
    assert conn._wake.is_set()
    await conn.poll_once()
    assert conn.snapshot()["state"] == "signed_out"


# -- the team's key ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_key_comes_from_the_status_and_stays_in_memory(conn, server, keys):
    await connect(conn, server)
    await conn.exchange_once()
    assert keys == [None]                                    # no status read yet: no brains
    await conn.poll_once()
    await conn.exchange_once()
    assert keys[-1] == KEY
    assert KEY not in data_paths.team_connection_path().read_text()
    assert KEY not in json.dumps(conn.snapshot())
    assert KEY.encode() not in conn.network_status()
    await conn.disconnect()
    assert conn._account_key is None
    assert _status(conn)["snapshot"] is None


# -- the loop ---------------------------------------------------------------------------

@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(team_client, "NETWORK_INTERVAL_SEC", 0.02)
    monkeypatch.setattr(team_client, "NETWORK_STARTUP_JITTER_SEC", 0.0)


async def _until(test, timeout=5.0):
    async def wait():
        while not test():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout)


@pytest.mark.asyncio
async def test_the_loop_runs_with_no_page_open_and_hints_each_snapshot(conn, server, quick):
    await connect(conn, server)
    hints = []
    conn.start(node_update=conn._node_update, on_network=lambda: hints.append(1))
    await _until(lambda: server.network_calls() >= 3)
    assert len(hints) >= 3
    assert _status(conn)["snapshot"] is not None
    await conn.stop()
    assert conn._exchanger is None and conn._poller is None
    calls = server.network_calls()
    await asyncio.sleep(0.1)
    assert server.network_calls() == calls                   # stopped at shutdown


@pytest.mark.asyncio
async def test_pairing_publishes_at_once(conn, server, quick, monkeypatch):
    monkeypatch.setattr(team_client, "NETWORK_INTERVAL_SEC", 60.0)
    conn.start(node_update=conn._node_update)
    await asyncio.sleep(0.05)
    assert server.network_calls() == 0                       # not paired: idle
    await connect(conn, server)
    await _until(lambda: server.network_calls() == 1)
    await conn.stop()


@pytest.mark.asyncio
async def test_signing_out_stops_the_exchange_and_marks_the_values_stale(conn, server, quick):
    await connect(conn, server)
    conn.start(node_update=conn._node_update)
    await _until(lambda: server.network_calls() >= 2)
    server.valid_tokens.clear()
    await conn.poll_once()
    assert conn.snapshot()["state"] == "signed_out"
    await asyncio.sleep(0.05)
    calls = server.network_calls()
    await asyncio.sleep(0.1)
    assert server.network_calls() == calls
    status = _status(conn)
    assert status["stale"] is True and status["connection"] == "signed_out"
    await conn.stop()


# -- the route and the node update ----------------------------------------------------

@pytest.mark.asyncio
async def test_the_route_returns_the_envelope_unchanged(conn, server, monkeypatch):
    from jarvis import server as jarvis_server
    monkeypatch.setattr(team_client, "connection", conn)
    await connect(conn, server)
    await conn.exchange_once()
    transport = httpx.ASGITransport(app=jarvis_server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8340") as client:
        r = await client.get("/api/team/network")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    assert r.content.startswith(b'{"snapshot":' + _snapshot_bytes() + b",")


def test_the_node_update_is_built_from_the_collectors_alone():
    update = team_api.node_update(None)
    assert set(update) == {"node_id", "boot_id", "seq", "label", "jarvis_version", "os",
                           "resources", "brains", "activity"}
    assert update["node_id"] == data_paths.node_id() and update["boot_id"] == data_paths.BOOT_ID
    assert update["brains"] == []                            # no key, no brains
    assert team_api.node_update(None)["seq"] > update["seq"]
    assert 1 <= len(update["label"]) <= 64 and len(update["jarvis_version"]) <= 32
    assert len(update["os"]) <= 40
    assert update["resources"]["state"] in ("measured", "warming_up", "unsupported", "error")
