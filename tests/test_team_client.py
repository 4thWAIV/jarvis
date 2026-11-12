"""The team-server connection (team_client.py, team_api.py).

A fake team server answers through httpx.MockTransport, DNS is a fake
resolver, and the Keychain is the in-memory stand-in from conftest — no
network, no `security`, no real Keychain.
"""
from __future__ import annotations

import asyncio
import sys
import base64
import hashlib
import json

import httpx
import pytest

from jarvis import data_paths, team_client
from jarvis.team_client import TeamConnection, TeamError
from tests.fixtures.waiting import until_async

URL = "https://team.example"
SERVER_ID = "srv-1"


class FakeServer:
    def __init__(self):
        self.server_id = SERVER_ID
        self.challenge = None
        self.approved = False
        self.denied = False
        self.token = "session-token-abc"
        self.valid_tokens = {self.token}
        self.logged_out: list[str] = []
        self.down = False
        self.calls: list[tuple[str, str]] = []
        self.redirect = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.down:
            raise httpx.ConnectError("down", request=request)
        if self.redirect:
            return httpx.Response(302, headers={"location": "https://evil.example/"})
        path = request.url.path
        auth = request.headers.get("authorization", "")
        if path == "/version":
            return httpx.Response(200, json={"service": "jarvis-team-server", "api_version": 1,
                                             "server_id": self.server_id, "version": "0.1.0",
                                             "commit": "c", "capabilities": []})
        if path == "/api/v1/device-authorizations":
            self.challenge = json.loads(request.content)["code_challenge"]
            return httpx.Response(200, json={
                "user_code": "BCDF-GHJK", "verification_uri": URL + "/device",
                "verification_uri_complete": URL + "/device?code=BCDF-GHJK",
                "poll_token": "poll-secret", "expires_at": "2099-01-01T00:00:00+00:00",
                "interval": 2})
        if path == "/api/v1/device-authorizations/exchange":
            body = json.loads(request.content)
            if self.denied:
                return httpx.Response(403, json={"error": {"code": "access_denied", "message": "no"}})
            if not self.approved:
                return httpx.Response(202, json={"status": "authorization_pending", "interval": 2})
            digest = base64.urlsafe_b64encode(hashlib.sha256(
                body["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            assert digest == self.challenge and body["poll_token"] == "poll-secret"
            return httpx.Response(200, json={
                "status": "approved", "session_token": self.token,
                "session_expires_at": "2099-01-01T00:00:00+00:00", "server_id": self.server_id,
                "team": {"name": "Tokens", "server_id": self.server_id, "member_count": 2,
                         "member_limit": 100},
                "me": {"github_id": 1, "login": "tistocks", "role": "admin", "state": "active"}})
        token = auth.removeprefix("Bearer ")
        if token not in self.valid_tokens:
            return httpx.Response(401, json={"error": {"code": "session_revoked",
                                                       "message": "Sign in again."}})
        if path == "/api/v1/status":
            return httpx.Response(200, json={
                "server_time": "2099-01-01T00:00:00+00:00", "server_id": self.server_id,
                "session_expires_at": "2099-01-01T00:00:00+00:00", "session_kind": "device",
                "team": {"name": "Tokens", "server_id": self.server_id, "member_count": 3,
                         "member_limit": 100},
                "me": {"github_id": 1, "login": "tistocks", "role": "admin", "state": "active"}})
        if path == "/api/v1/logout":
            self.logged_out.append(token)
            self.valid_tokens.discard(token)
            return httpx.Response(200, json={"revoked": True})
        if path == "/api/v1/team/members":
            return httpx.Response(200, json={"revision": 3, "member_limit": 100, "members": []})
        if path.startswith("/api/v1/team/members/"):
            return httpx.Response(409, json={"error": {"code": "last_admin", "message": "keep one"}})
        return httpx.Response(404)


def public(host, port):
    return ["140.82.112.3"]


@pytest.fixture
def server():
    return FakeServer()


@pytest.fixture
def conn(server):
    c = TeamConnection(store=team_client.connection.store,
                       transport=httpx.MockTransport(server.handler), resolver=public)
    return c


async def connect(conn, server):
    snap = await conn.begin_pairing(URL, "JARVIS on test")
    assert snap["state"] == "pairing"
    assert snap["pairing"]["user_code"] == "BCDF-GHJK"
    server.approved = True
    await asyncio.wait_for(conn.pairing.task, timeout=10)
    return conn.snapshot()


# -- URL rules ------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["http://team.example", "team.example", "https://team.example/x",
                                 "https://u:p@team.example", "https://team.example?a=1", ""])
def test_urls_must_be_https_origins(bad):
    with pytest.raises(TeamError):
        team_client.normalize_url(bad)


def test_url_normalized():
    assert team_client.normalize_url(" https://Team.Example/ ") == "https://team.example"


def test_dev_exception_is_explicit(monkeypatch):
    with pytest.raises(TeamError):
        team_client.normalize_url("http://127.0.0.1:18080")
    monkeypatch.setenv("JARVIS_TEAM_DEV_SERVERS", "http://127.0.0.1:18080")
    assert team_client.normalize_url("http://127.0.0.1:18080") == "http://127.0.0.1:18080"


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.8", "192.168.1.4", "169.254.169.254",
                                     "::1", "fe80::1", "fd00::1", "0.0.0.0"])
async def test_private_destinations_refused(address):
    with pytest.raises(TeamError, match="private"):
        await team_client.check_destination(URL, resolver=lambda h, p: [address])


@pytest.mark.asyncio
async def test_mixed_resolution_refused():
    with pytest.raises(TeamError):
        await team_client.check_destination(URL, resolver=lambda h, p: ["140.82.112.3", "10.0.0.1"])


# -- pairing --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pairing_stores_session_in_keychain_and_never_on_disk(conn, server, monkeypatch):
    monkeypatch.setattr(team_client, "_now", lambda: 1_000.0)
    snap = await connect(conn, server)
    assert snap["state"] == "connected"
    assert snap["team"]["name"] == "Tokens" and snap["me"]["role"] == "admin"
    assert conn.store.get(SERVER_ID) == server.token
    on_disk = data_paths.team_connection_path().read_text()
    assert server.token not in on_disk and "poll-secret" not in on_disk
    assert json.loads(on_disk)["server_id"] == SERVER_ID
    shown = json.dumps(snap)
    assert server.token not in shown and "poll-secret" not in shown


@pytest.mark.asyncio
async def test_pairing_denied(conn, server):
    await conn.begin_pairing(URL, "JARVIS on test")
    server.denied = True
    await asyncio.wait_for(conn.pairing.task, timeout=10)
    snap = conn.snapshot()
    assert snap["state"] == "disconnected" and "denied" in snap["last_error"]
    assert snap["last_error_said"] == {"code": "pairingDenied", "values": {}, "detail": ""}
    assert conn.store.get(SERVER_ID) is None


@pytest.mark.asyncio
async def test_the_address_is_offered_again_after_a_pairing_ends(conn, server):
    await conn.begin_pairing(URL, "JARVIS on test")
    await conn.cancel_pairing()
    snap = conn.snapshot()
    assert snap["state"] == "disconnected" and snap["server_url"] is None
    assert snap["last_server_url"] == URL


@pytest.mark.asyncio
async def test_not_a_team_server(conn, server):
    server.server_id = ""
    with pytest.raises(TeamError):
        await conn.begin_pairing(URL, "JARVIS")


@pytest.mark.asyncio
async def test_redirects_are_refused(conn, server):
    server.redirect = True
    with pytest.raises(TeamError, match="redirect"):
        await conn.begin_pairing(URL, "JARVIS")


@pytest.mark.asyncio
async def test_pinned_server_id(conn, server):
    await connect(conn, server)
    server.server_id = "someone-else"
    with pytest.raises(TeamError, match="identity changed"):
        await conn.begin_pairing(URL, "JARVIS")
    await conn.poll_once()
    assert conn.snapshot()["state"] == "signed_out"
    assert conn.store.get(SERVER_ID) is None


# -- status polling -------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_poll_updates_last_contact(conn, server, monkeypatch):
    await connect(conn, server)
    monkeypatch.setattr(team_client, "_now", lambda: 5_000_000_000.0)
    await conn.poll_once()
    snap = conn.snapshot()
    assert snap["state"] == "connected" and snap["team"]["member_count"] == 3
    assert snap["last_contact_at"].startswith("2128")


@pytest.mark.asyncio
async def test_outage_keeps_credential_backs_off_and_goes_stale(conn, server, monkeypatch):
    clock = [1_000.0]
    monkeypatch.setattr(team_client, "_now", lambda: clock[0])
    await connect(conn, server)
    server.down = True
    clock[0] += 100
    for _ in range(3):
        await conn.poll_once()
    snap = conn.snapshot()
    assert snap["state"] == "stale" and "didn't answer" in snap["last_error"]
    assert snap["last_error_said"]["code"] == "noAnswer"
    assert conn.store.get(SERVER_ID) == server.token
    assert conn.next_delay() == 120
    for _ in range(10):
        await conn.poll_once()
    assert conn.next_delay() == team_client.MAX_BACKOFF_SEC
    server.down = False
    await conn.poll_once()
    assert conn.snapshot()["state"] == "connected" and conn.next_delay() == 30


@pytest.mark.asyncio
async def test_revoked_session_stops_and_asks_for_sign_in(conn, server):
    await connect(conn, server)
    server.valid_tokens.clear()
    await conn.poll_once()
    snap = conn.snapshot()
    assert snap["state"] == "signed_out" and snap["last_error"] == "Sign in again."
    assert snap["last_error_said"] == {"code": "sessionEnded", "values": {}, "detail": "Sign in again."}
    assert conn.store.get(SERVER_ID) is None
    calls = len(server.calls)
    await conn.poll_once()
    assert len(server.calls) == calls  # stopped polling


@pytest.mark.asyncio
async def test_poll_loop_runs_on_its_own_task_and_stops(conn, server, monkeypatch):
    monkeypatch.setattr(team_client, "STATUS_INTERVAL_SEC", 0.01)
    await connect(conn, server)
    conn.start()
    assert await until_async(
        lambda: sum(1 for c in server.calls if c[1] == "/api/v1/status") >= 2), server.calls
    await conn.stop()
    assert conn._poller is None


# -- disconnect -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disconnect_revokes_remotely(conn, server):
    await connect(conn, server)
    result = await conn.disconnect()
    assert result["remote"] == "revoked" and server.logged_out == [server.token]
    assert conn.store.get(SERVER_ID) is None
    assert not data_paths.team_connection_path().exists()
    assert conn.snapshot()["state"] == "disconnected"
    assert conn.snapshot()["last_server_url"] == URL


@pytest.mark.asyncio
async def test_offline_disconnect_still_deletes_the_credential(conn, server):
    await connect(conn, server)
    server.down = True
    result = await conn.disconnect()
    assert result["remote"] == "unreachable" and "12 hours" in result["message"]
    assert conn.store.get(SERVER_ID) is None


# -- administrator calls ----------------------------------------------------------

@pytest.mark.asyncio
async def test_member_calls_are_forwarded_with_the_session(conn, server):
    await connect(conn, server)
    status, body = await conn.call("GET", "/api/v1/team/members")
    assert status == 200 and body["revision"] == 3
    status, body = await conn.call("PUT", "/api/v1/team/members/5", {"expected_revision": 3})
    assert status == 409 and body["error"]["code"] == "last_admin"


@pytest.mark.asyncio
async def test_member_calls_need_a_connection(conn):
    with pytest.raises(TeamError):
        await conn.call("GET", "/api/v1/team/members")


# -- routes ---------------------------------------------------------------------------

def test_routes_over_the_server(server, monkeypatch):
    from fastapi.testclient import TestClient
    from jarvis import server as jarvis_server
    c = TeamConnection(store=team_client.connection.store,
                       transport=httpx.MockTransport(server.handler), resolver=public)
    monkeypatch.setattr(team_client, "connection", c)
    client = TestClient(jarvis_server.app)
    origin = {"Origin": "http://localhost:5173"}
    assert client.get("/api/team").json()["state"] == "disconnected"
    # Mutations need the page's Origin (web_auth.OriginGuard), like every route.
    assert client.post("/api/team/connect", json={"server_url": URL}).status_code == 403
    r = client.post("/api/team/connect", json={"server_url": "http://x"}, headers=origin)
    assert r.status_code == 400 and "https" in r.json()["error"]["message"]
    # Beside the English, as before: its code, for the page to word.
    assert r.json()["error"]["said"] == {"code": "addressNotHttps", "values": {}, "detail": ""}
    r = client.post("/api/team/connect", json={"server_url": URL}, headers=origin)
    assert r.status_code == 200 and r.json()["pairing"]["user_code"] == "BCDF-GHJK"
    assert client.post("/api/team/cancel", headers=origin).json()["state"] == "disconnected"
    r = client.get("/api/team/members")
    assert r.status_code == 409


def test_every_team_problem_is_in_the_page_catalog_with_the_same_english():
    """The page says each problem by its code (frontend/src/messages/
    shell.en.ts, `team.error.<code>`, and shell.es.ts in Spanish). Its
    English is team_client's, word for word and placeholder for
    placeholder, so the two never drift apart."""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed to read the page's catalog")
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e",
                          "import { shellEn } from './frontend/src/messages/shell.en.ts';"
                          "console.log(JSON.stringify(shellEn))"],
                         capture_output=True, text=True, cwd=root, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    catalog = json.loads(out.stdout)
    said = team_client.TEAM_SAID
    assert {code: catalog.get(f"team.error.{code}") for code in said} == said


def test_a_problem_keeps_its_english_and_the_team_servers_own_words():
    p = team_client.problem("pairingRefused", status=400)
    assert p.english == "The team server refused to start pairing (400)."
    assert p.to_json() == {"code": "pairingRefused", "values": {"status": 400}, "detail": ""}
    # The team server's words, when it gave some, are the English, as before.
    p = team_client.problem("pairingRefused", "Too many devices.", status=400)
    assert p.english == "Too many devices." and p.to_json()["detail"] == "Too many devices."
    # Bare English (another caller's) has no code: the page shows it as it came.
    e = TeamError("down", unreachable=True)
    assert str(e) == "down" and e.problem.to_json() is None and e.unreachable


def test_device_name():
    from jarvis import team_api
    name = team_api.device_name()
    assert name.startswith("JARVIS on ") and len(name) <= 64


# -- Windows Credential Manager, its three advapi32 calls faked ----------------

# The real class, taken before conftest swaps the module's name for an
# in-memory store in every test.
RealCredentialStore = team_client.CredentialManagerStore


class FakeCredentials:
    """Generic credentials by target, as advapi32 keeps them, with a failure
    to give for the next call when asked."""

    def __init__(self, monkeypatch):
        self.saved: dict[str, tuple[str, bytes]] = {}
        self.fail: dict[str, int] = {}
        for name in ("read", "write", "delete"):
            monkeypatch.setattr(team_client, f"_cred_{name}", getattr(self, name))

    def _failing(self, call):
        if call in self.fail:
            raise OSError(None, "store error", None, self.fail.pop(call))

    def read(self, target):
        self._failing("read")
        return self.saved[target][1] if target in self.saved else None

    def write(self, target, user, blob):
        self._failing("write")
        self.saved[target] = (user, blob)

    def delete(self, target):
        self._failing("delete")
        self.saved.pop(target, None)


def test_a_session_is_one_credential_per_server_named_by_its_id(monkeypatch):
    creds = FakeCredentials(monkeypatch)
    store = RealCredentialStore()
    store.set("srv-1", "tok-one")
    assert creds.saved == {f"{team_client.KEYCHAIN_SERVICE}/srv-1": ("srv-1", b"tok-one")}
    assert store.get("srv-1") == "tok-one"


def test_servers_and_services_never_see_each_other_s_sessions(monkeypatch):
    FakeCredentials(monkeypatch)
    one, other = RealCredentialStore(), RealCredentialStore(service="another service")
    one.set("srv-1", "tok-one")
    one.set("srv-2", "tok-two")
    assert one.get("srv-1") == "tok-one" and one.get("srv-2") == "tok-two"
    assert other.get("srv-1") is None
    one.delete("srv-1")
    assert one.get("srv-1") is None and one.get("srv-2") == "tok-two"


def test_nothing_saved_is_none_and_deleting_it_is_nothing(monkeypatch):
    FakeCredentials(monkeypatch)
    store = RealCredentialStore()
    assert store.get("srv-1") is None
    store.delete("srv-1")


def test_a_store_error_is_raised_never_taken_for_an_empty_store(monkeypatch):
    creds = FakeCredentials(monkeypatch)
    store = RealCredentialStore()
    store.set("srv-1", "tok-one")
    creds.fail["read"] = 5
    with pytest.raises(OSError):
        store.get("srv-1")
    creds.fail["delete"] = 5
    with pytest.raises(OSError):
        store.delete("srv-1")
    assert store.get("srv-1") == "tok-one"


@pytest.mark.parametrize("fail", ["write", "read"])
def test_a_save_that_fails_or_does_not_read_back_is_refused(monkeypatch, fail):
    creds = FakeCredentials(monkeypatch)
    creds.fail[fail] = 1312
    with pytest.raises(TeamError) as refused:
        RealCredentialStore().set("srv-1", "tok-one")
    assert refused.value.problem.said.code == "keychainSaveFailed"


def test_a_save_that_reads_back_different_bytes_is_refused(monkeypatch):
    creds = FakeCredentials(monkeypatch)
    monkeypatch.setattr(team_client, "_cred_read", lambda target: b"something else")
    with pytest.raises(TeamError) as refused:
        RealCredentialStore().set("srv-1", "tok-one")
    assert refused.value.problem.said.code == "keychainSaveFailed"


@pytest.mark.parametrize("secret,code", [
    ("", "sessionUnusable"), ("has space", "sessionUnusable"), ('q"uote', "sessionUnusable"),
    ("x" * (5 * 512 + 1), "keychainSaveFailed"),
])
def test_a_session_the_store_cannot_hold_is_refused_before_writing(monkeypatch, secret, code):
    creds = FakeCredentials(monkeypatch)
    with pytest.raises(TeamError) as refused:
        RealCredentialStore().set("srv-1", secret)
    assert refused.value.problem.said.code == code
    assert creds.saved == {}


def test_windows_chooses_the_credential_manager_and_elsewhere_the_keychain(monkeypatch):
    class Windows: pass
    class Mac: pass
    monkeypatch.setattr(team_client, "CredentialManagerStore", Windows)
    monkeypatch.setattr(team_client, "KeychainStore", Mac)
    monkeypatch.setattr(team_client.sys, "platform", "win32")
    assert isinstance(TeamConnection().store, Windows)
    monkeypatch.setattr(team_client.sys, "platform", "darwin")
    assert isinstance(TeamConnection().store, Mac)


def test_a_failed_read_is_logged_and_the_connection_has_no_session(monkeypatch, caplog):
    creds = FakeCredentials(monkeypatch)
    store = RealCredentialStore()
    store.set("srv-1", "tok-one")
    conn = TeamConnection(store=store)
    conn.saved = {"server_url": "https://team.example", "server_id": "srv-1"}
    creds.fail["read"] = 5
    assert asyncio.run(conn._secret()) is None
    # winerror 5 is PermissionError only where Python maps Windows errors.
    expected = "PermissionError" if sys.platform == "win32" else "OSError"
    assert f"keychain read failed ({expected})" in caplog.text
