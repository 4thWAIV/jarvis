"""Human messaging end to end, as it runs: the stage 7 integration scenarios.

Nothing between the parts is faked. The team server is the real app,
served over HTTP by uvicorn on an ephemeral loopback port against the test
MongoDB (each test its own `hm_int_*` database, dropped afterwards), where
messaging is always on. Each JARVIS is the real server app with its
messaging wired as startup wires it (`messaging_runtime`), reaching the
team server through `team_client.TeamConnection` over HTTP, which
JARVIS_TEAM_DEV_SERVERS allows for that loopback origin:

    in-process   this test's own JARVIS (`server`, its data directory the
                 test's): driven through its HTTP routes with its tool
                 token, its delivery passes and announcements run by the
                 test so every step is observed in order.
    a process    a second JARVIS (`tests/messaging_node.py`) in its own data
                 directory on an ephemeral port, running its own loops, as
                 another of the owner's nodes or another member's.

Device sessions are issued in the team server's database directly (as its
own tests do): sign-in and pairing are not what is tested here, and the
session credential stays in memory. Faults are the contract's injected
`ArmedFaults` given to the team server's service; there is no fault route.

Needs TEAM_SERVER_TEST_MONGODB_URI (unset: skipped; unreachable: fails).
Evidence records ids, states and counts only, never a message's words.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from uuid import UUID

import httpx
import pytest
import pytest_asyncio

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from tests.fixtures.memory_secrets import MemorySecrets

MONGO = os.environ.get("TEAM_SERVER_TEST_MONGODB_URI")
if not MONGO:
    pytest.skip("needs TEAM_SERVER_TEST_MONGODB_URI and the team server's packages",
                allow_module_level=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "team_server"))
import uvicorn  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from teamserver import attachments as ts_attachments  # noqa: E402
from teamserver import clock as ts_clock  # noqa: E402
from teamserver import messaging as ts_messaging  # noqa: E402
from teamserver import sessions as ts_sessions  # noqa: E402
from teamserver import team as ts_team  # noqa: E402
from teamserver.app import create_app  # noqa: E402
from teamserver.config import Config  # noqa: E402
from teamserver.github import Identity  # noqa: E402
from tests.test_message_approvals_server import _Speech  # noqa: E402

TIM = Identity(id=863470, login="tistocks", name="Tim")
MATT = Identity(id=1001, login="matt", name="Matt")
BOB = Identity(id=1002, login="bob", name="Bob")
P = "/api/team/messages"
CONVERGE_SEC = 30.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- the team server ------------------------------------------------------------------

class LocalTeamServer:
    """The real team server app on an ephemeral loopback port, in a thread
    with its own event loop. `call` runs a coroutine on that loop (the
    database client belongs to it)."""

    def __init__(self, dbname: str):
        self.dbname = dbname
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.config = Config(port=self.port, public_base_url=self.url, mongodb_uri=MONGO,
                             mongodb_database=dbname, github_client_id="cid",
                             github_client_secret="not-a-secret",
                             team_name="Integration Team", initial_admin_github_id=TIM.id,
                             dev_insecure=True)
        self.app = create_app(self.config, github=object())
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1",
                                                    port=self.port, log_level="warning"))
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.faults = wire.ArmedFaults()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 30
        while not (self.server.started and getattr(self.app.state, "initialized", False)
                   and getattr(self.app.state, "attachments", None) is not None):
            assert time.monotonic() < deadline, "the team server did not start"
            time.sleep(0.05)
        st = self.app.state
        self.server_id = st.server_id
        # The test's faults, given to the service as the app builds it.
        st.messaging = ts_messaging.Messaging(ts_messaging.MongoMessages(st.db),
                                              attachments=st.attachment_storage,
                                              faults=self.faults)
        st.attachments = ts_attachments.AttachmentService(
            st.attachment_storage, ts_messaging.MongoMessages(st.db), faults=self.faults)

    def _run(self):
        async def serve():
            self.loop = asyncio.get_running_loop()
            await self.server.serve()
        asyncio.run(serve())

    async def call(self, make):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(make(), self.loop))

    async def member(self, who: Identity) -> None:
        db, st = self.app.state.db, self.app.state

        async def go():
            if who.id != TIM.id:
                await ts_team.mutate(db, ts_team.change_member(TIM.id, who.id, who, None, None))
            await ts_team.admit(db, st.config, st.server_id, who)
        await self.call(go)

    async def device(self, who: Identity) -> str:
        db = self.app.state.db

        async def go():
            team = await ts_team.get(db)
            member = team["members"][ts_team.key(who.id)]
            token, _ = await ts_sessions.issue(db, team, member, "device",
                                               auth_at=ts_clock.now(),
                                               device_name="JARVIS integration")
            return token
        return await self.call(go)

    async def suspend(self, who: Identity) -> None:
        db = self.app.state.db
        await self.call(lambda: ts_team.mutate(
            db, ts_team.change_member(TIM.id, who.id, None, None, "suspended")))

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.fixture
def mongo():
    client = MongoClient(MONGO, serverSelectionTimeoutMS=2000)
    yield client
    client.close()


@pytest.fixture
def team_server(mongo):
    name = "hm_int_" + uuid.uuid4().hex[:12]
    ts = LocalTeamServer(name)
    yield ts
    ts.stop()
    assert name.startswith("hm_int_")
    mongo.drop_database(name)


def saved_connection(ts: LocalTeamServer, who: Identity) -> dict:
    return {"server_url": ts.url, "server_id": ts.server_id,
            "me": {"github_id": who.id, "login": who.login, "name": who.name,
                   "role": "admin" if who.id == TIM.id else "member"},
            "team": {"name": "Integration Team", "server_id": ts.server_id},
            "connected_at": time.time(), "last_contact_at": time.time()}


# -- this test's own JARVIS ----------------------------------------------------------

class Here:
    """The in-process JARVIS: the server module, its routes over HTTP with
    the tool token, and its messaging runtime ticked by the test."""

    def __init__(self, server, who: Identity, ts: LocalTeamServer):
        self.server, self.who, self.ts = server, who, ts
        from jarvis import data_paths
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://127.0.0.1:8340",
            headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}"})
        self.said: list[tuple[str, dict]] = []

    @property
    def rt(self):
        return self.server.messaging

    @property
    def ns(self):
        return self.ts.server_id, self.who.id

    async def tick(self, times: int = 1):
        for _ in range(times):
            await self.rt.delivery.tick()

    async def ok(self, method: str, path: str, **kw):
        r = await self.http.request(method, path, **kw)
        assert r.status_code < 300, (method, path, r.status_code, r.text[:300])
        return r.json() if r.content else None

    async def draft(self, to: Identity, text: str, **kw) -> dict:
        return await self.ok("POST", P + "/drafts",
                             json={"recipient_github_id": to.id, "text": text, **kw})

    async def review(self, draft: dict) -> dict:
        return await self.ok("POST", f"{P}/drafts/{draft['message_id']}/review",
                             json={"expected_draft_revision": draft["draft_revision"]})

    async def send(self, reviewed: dict) -> None:
        """The owner presses Send on the review's capture; the turn ends."""
        await self.ok("POST", f"/api/capture/{reviewed['capture_id']}/send")
        await self.server._perform_staged_steers()

    async def approved(self, to: Identity, text: str, **kw) -> dict:
        reviewed = await self.review(await self.draft(to, text, **kw))
        await self.send(reviewed)
        return reviewed

    def send_state(self, message_id) -> ports.LocalSend:
        return self.rt.store.send(*self.ns, UUID(str(message_id)))

    async def snapshot(self) -> dict:
        return await self.ok("GET", P)


@pytest.fixture
def server(monkeypatch, tmp_path):
    """The server module, reloaded over this test's data directory, with
    its messaging loops left to the test."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from jarvis import messaging_runtime
    from jarvis import server as server_module
    importlib.reload(server_module)
    server_module.run_store.init_db()
    monkeypatch.setattr(server_module, "_notes_for_brain", [])
    monkeypatch.setattr(server_module, "speech", _Speech())
    monkeypatch.setattr(messaging_runtime.MessagingRuntime, "start", lambda self: None)

    async def quiet(msg):
        pass
    monkeypatch.setattr(server_module, "_voice_emit", quiet)
    yield server_module
    asyncio.run(server_module._stop_messaging()) if server_module.messaging else None


async def here(server, ts: LocalTeamServer, who: Identity, monkeypatch) -> Here:
    from jarvis import team_client
    monkeypatch.setenv("JARVIS_TEAM_DEV_SERVERS", ts.url)
    await ts.member(who)
    token = await ts.device(who)
    team_client.connection.saved = saved_connection(ts, who)
    team_client.connection.store.set(ts.server_id, token)
    # Its node on the team's network, as the network exchange publishes it
    # at startup: the team server binds a sending node to its member by it.
    from jarvis import team_api
    status, _ = await team_client.connection.call("POST", team_client.NETWORK_PATH,
                                                  team_api.node_update(None))
    assert status == 200
    server._start_messaging()
    h = Here(server, who, ts)

    async def notify(line, note, detail):
        h.said.append((line, detail))
    h.rt.announcer._notify = notify
    assert await h.rt.delivery.availability() == "available"
    await h.rt.local.recipients()
    return h


# -- another JARVIS, as its own process ----------------------------------------------

class Node:
    """A JARVIS backend process paired as `who`, in its own data directory,
    running its own delivery, announcer and network loops."""

    def __init__(self, ts: LocalTeamServer, who: Identity, token: str, root: Path):
        self.ts, self.who = ts, who
        data = root / "data"
        (data / "jarvis").mkdir(parents=True)
        (data / "team-connection.json").write_text(json.dumps(saved_connection(ts, who)))
        tool = secrets.token_urlsafe(32)
        fd = os.open(data / "jarvis" / "tool-token", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     0o600)
        os.write(fd, tool.encode())
        os.close(fd)
        (root / ".env").write_text("")
        self.port = _free_port()
        self.log = open(root / "node.log", "w")
        env = {**os.environ, "JARVIS_DATA_DIR": str(data), "JARVIS_ENV_FILE": str(root / ".env"),
               "JARVIS_PORT": str(self.port),
               "JARVIS_BRAIN_AUTOSTART": "0", "JARVIS_TEAM_DEV_SERVERS": ts.url}
        self.proc = subprocess.Popen([sys.executable, "-m", "tests.messaging_node"],
                                     cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        self.proc.stdin.write((token + "\n").encode())
        self.proc.stdin.close()
        self.url = f"http://127.0.0.1:{self.port}"
        self.http = httpx.AsyncClient(base_url=self.url, timeout=10,
                                      headers={"Authorization": f"Bearer {tool}"})

    async def wait_available(self):
        deadline = time.monotonic() + 60
        while True:
            try:
                r = await self.http.get(P)
                if r.status_code == 200 and r.json()["availability"] == "available":
                    return
            except httpx.HTTPError:
                pass
            assert self.proc.poll() is None, "the JARVIS process exited"
            assert time.monotonic() < deadline, "the JARVIS process never offered messaging"
            await asyncio.sleep(0.25)

    async def until(self, path: str, test, what: str):
        deadline = time.monotonic() + CONVERGE_SEC
        while True:
            r = await self.http.get(path)
            body = r.json() if r.status_code == 200 else None
            if body is not None and test(body):
                return body
            assert time.monotonic() < deadline, f"never: {what}"
            await asyncio.sleep(0.25)

    async def ok(self, method: str, path: str, **kw):
        r = await self.http.request(method, path, **kw)
        assert r.status_code < 300, (method, path, r.status_code, r.text[:300])
        return r.json() if r.content else None

    async def stop(self):
        await self.http.aclose()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()


@pytest_asyncio.fixture
async def nodes(tmp_path):
    started: list[Node] = []

    async def start(ts, who, name) -> Node:
        await ts.member(who)
        node = Node(ts, who, await ts.device(who), tmp_path / name)
        started.append(node)
        await node.wait_available()
        return node
    yield start
    for node in started:
        await node.stop()


def _items(snapshot: dict) -> list[dict]:
    return snapshot["items"]


def _item(snapshot: dict, thread_id) -> Optional[dict]:
    return next((i for i in snapshot["items"]
                 if i["work_state"]["thread_id"] == str(thread_id)), None)


# -- self-one-node --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_self_one_node(server, team_server, mongo, monkeypatch):
    """Explicit approval, acceptance by the team server, received back on
    the same node: one item, read only on open, no reply of its own."""
    tim = await here(server, team_server, TIM, monkeypatch)
    frames = []
    tim.rt.presentation._send = frames.append
    draft = await tim.draft(TIM, "Note to self.")
    assert tim.send_state(draft["message_id"]).state == "draft"
    await tim.tick()
    assert mongo[team_server.dbname].messages.count_documents({}) == 0, \
        "a draft never reaches the team server"
    reviewed = await tim.review(draft)
    assert reviewed["send"]["state"] == "awaiting_approval"
    assert server._captures[reviewed["capture_id"]]["text"].startswith("To Me.")
    await tim.tick()
    assert mongo[team_server.dbname].messages.count_documents({}) == 0, \
        "a review approves nothing"
    await tim.send(reviewed)
    assert tim.send_state(draft["message_id"]).state == "approved"
    await tim.tick()
    assert tim.send_state(draft["message_id"]).state == "accepted", tim.send_state(draft["message_id"])
    assert mongo[team_server.dbname].messages.count_documents({}) == 1
    snap = await tim.snapshot()
    assert len(_items(snap)) == 1
    item = _items(snap)[0]
    assert item["work_state"]["placement"] == "queued"
    assert item["work_state"]["action_owner_github_id"] == TIM.id
    (message,) = item["messages"]
    assert message["self_message"] and message["direction"] == "outgoing"
    assert message["receipts"]["read_at"] is None, "not read until he opens it"
    transfers = [(f["event"]["direction"]) for f in frames if f.get("type") == "human_transfer"]
    assert sorted(transfers) == ["incoming", "outgoing"], "drawn once each way"
    assert await tim.rt.announcer.announce_due() == 1
    assert len(tim.said) == 1 and tim.said[0][1] == {"thread_id": draft["thread_id"]}
    assert await tim.rt.announcer.announce_due() == 0, "said once"
    await tim.ok("POST", f"{P}/{draft['message_id']}/read")
    await tim.tick()
    snap = await tim.snapshot()
    assert _items(snap)[0]["messages"][0]["receipts"]["read_at"] is not None
    assert mongo[team_server.dbname].messages.count_documents({}) == 1, "no reply of its own"


# -- lost acceptance response, restart, retries -------------------------------------------

@pytest.mark.asyncio
async def test_lost_accept_response_then_restart_settles_on_one_message(
        server, team_server, mongo, monkeypatch):
    tim = await here(server, team_server, TIM, monkeypatch)
    await team_server.member(MATT)
    reviewed = await tim.approved(MATT, "Once only.")
    mid = reviewed["message_id"]
    team_server.faults.arm("server_after_commit_before_response")
    await tim.tick()
    lost = tim.send_state(mid)
    assert lost.state == "accept_unknown" and lost.failure == "internal"
    assert lost.next_attempt_at is not None, "retried by itself, with backoff"
    delay = (lost.next_attempt_at - tim.rt.delivery.clock()).total_seconds()
    assert 0 <= delay <= wire.RETRY_MAX_SEC, "full-jitter backoff, capped"
    approved = tim.rt.store.approved(*tim.ns, UUID(mid), 1)
    # A restart before the retry: the runtime is built again over the same
    # data, and the same approved envelope goes again under its key.
    await server._stop_messaging()
    server._start_messaging()
    tim.rt.store.transition_send(*tim.ns, tim.send_state(mid), tim.send_state(mid).model_copy(
        update={"next_attempt_at": tim.rt.delivery.clock()}))
    await tim.tick()
    assert tim.send_state(mid).state == "accepted"
    again = tim.rt.store.approved(*tim.ns, UUID(mid), 1)
    assert again.request.idempotency_key == approved.request.idempotency_key
    assert mongo[team_server.dbname].messages.count_documents({}) == 1


@pytest.mark.asyncio
async def test_a_crash_mid_attempt_is_unknown_after_restart_and_settles_once(
        server, team_server, mongo, monkeypatch):
    tim = await here(server, team_server, TIM, monkeypatch)
    await team_server.member(MATT)
    reviewed = await tim.approved(MATT, "Through a crash.")
    mid = reviewed["message_id"]
    faults = wire.ArmedFaults()
    tim.rt.delivery.faults = faults
    faults.arm("client_before_local_save")
    with pytest.raises(wire.InjectedFault):
        await tim.tick()
    assert tim.send_state(mid).state == "submitting", "accepted, but never saved here"
    assert mongo[team_server.dbname].messages.count_documents({}) == 1
    await server._stop_messaging()
    server._start_messaging()
    await tim.tick()                               # recovery: unknown, retried under its key
    if tim.send_state(mid).state != "accepted":
        s = tim.send_state(mid)
        tim.rt.store.transition_send(*tim.ns, s, s.model_copy(
            update={"next_attempt_at": tim.rt.delivery.clock()}))
        await tim.tick()
    assert tim.send_state(mid).state == "accepted"
    assert mongo[team_server.dbname].messages.count_documents({}) == 1


# -- edits, altered payloads and the guard ---------------------------------------------------

@pytest.mark.asyncio
async def test_edit_after_review_and_altered_payload_cannot_send(
        server, team_server, mongo, monkeypatch):
    tim = await here(server, team_server, TIM, monkeypatch)
    await team_server.member(MATT)
    draft = await tim.draft(MATT, "First words.")
    reviewed = await tim.review(draft)
    edited = await tim.ok("PUT", f"{P}/drafts/{draft['message_id']}", json={
        "expected_draft_revision": reviewed["draft_revision"],
        "recipient_github_id": MATT.id, "text": "Second words."})
    assert edited["send"]["content_revision"] == 2 and edited["capture_id"] is None
    assert reviewed["capture_id"] not in server._captures, "the old review closed"
    r = await tim.http.post(f"/api/capture/{reviewed['capture_id']}/send")
    assert r.status_code >= 400, "the stale review's Send is gone"
    await server._perform_staged_steers()
    await tim.tick()
    assert mongo[team_server.dbname].messages.count_documents({}) == 0

    # A double Send on one review sends one message.
    second = await tim.review(edited)
    await tim.ok("POST", f"/api/capture/{second['capture_id']}/send")
    await tim.http.post(f"/api/capture/{second['capture_id']}/send")
    await server._perform_staged_steers()
    await tim.tick(2)
    assert mongo[team_server.dbname].messages.count_documents({}) == 1

    # An approved envelope altered on disk is never released.
    third = await tim.approved(MATT, "Untouched.")
    approved = tim.rt.store.approved(*tim.ns, UUID(third["message_id"]), 1)
    altered = approved.model_copy(update={"request": approved.request.model_copy(
        update={"text": "Tampered."})})
    with tim.rt.store._tx() as conn:
        assert conn.execute("UPDATE human_approved SET body=? WHERE message_id=?",
                            (altered.model_dump_json(), third["message_id"])).rowcount == 1
    await tim.tick()
    state = tim.send_state(third["message_id"])
    assert state.state == "approved" and state.failure == "not_approved"
    assert mongo[team_server.dbname].messages.count_documents({}) == 1

    # The same key with a changed digest: the team server refuses it.
    changed = approved.request.model_copy(update={"text": "Changed."})
    changed = changed.model_copy(update={"content_digest": wire.content_digest(
        wire.content_of(changed), server_id=team_server.server_id, sender_github_id=TIM.id)})
    first = tim.rt.store.approved(*tim.ns, UUID(second["message_id"]), 2)
    reused = changed.model_copy(update={"idempotency_key": first.request.idempotency_key})
    from jarvis.messaging_client import TeamTransport
    raw = TeamTransport(team_server_connection(), tim.rt.delivery.pairing())
    with pytest.raises(ports.TransportError) as e:
        await raw.send(reused)
    assert e.value.code == "idempotency_conflict"
    assert mongo[team_server.dbname].messages.count_documents({}) == 1


def team_server_connection():
    from jarvis import team_client
    return team_client.connection


# -- a member's JARVIS with nothing but its session ------------------------------------

class Raw:
    """A member's paired session and node, speaking to the team server
    through JARVIS's own transport, with no store or guard: what a forged or
    curious client could send."""

    def __init__(self, ts: LocalTeamServer, who: Identity, token: str):
        from jarvis import team_client
        from jarvis.messaging_client import TeamTransport, pairing_of

        self.ts, self.who = ts, who
        self.connection = team_client.TeamConnection(store=MemorySecrets())
        self.connection.saved = saved_connection(ts, who)
        self.connection.store.set(ts.server_id, token)
        self.node = uuid.uuid4()
        self.t = TeamTransport(self.connection, pairing_of(self.connection))

    async def publish(self):
        status, body = await self.connection.call("POST", "/api/v1/network/nodes/self", {
            "node_id": str(self.node), "boot_id": str(uuid.uuid4()), "seq": 1,
            "label": "RAW NODE", "jarvis_version": "1", "os": "macOS",
            "resources": {"state": "measured", "scope": "host", "cpu_percent": 1.0,
                          "memory_used_bytes": 1, "memory_total_bytes": 2, "age_ms": 0},
            "brains": [], "activity": None})
        assert status == 200, body

    def request(self, to: int, text: str, *, node=None, related=(), key=None,
                ) -> wire.SendRequest:
        mid = uuid.uuid4()
        content = wire.MessageContent(message_id=mid, thread_id=mid, recipient_github_id=to,
                                      content_revision=1, text=wire.normalize_text(text),
                                      related_message_ids=list(related))
        return wire.SendRequest(**content.model_dump(), source_node_id=node or self.node,
                                idempotency_key=key or uuid.uuid4(),
                                content_digest=wire.content_digest(
                                    content, server_id=self.ts.server_id,
                                    sender_github_id=self.who.id))


async def raw(ts: LocalTeamServer, who: Identity) -> Raw:
    await ts.member(who)
    r = Raw(ts, who, await ts.device(who))
    await r.publish()
    return r


async def refused(call) -> str:
    with pytest.raises(ports.TransportError) as e:
        await call
    return e.value.code


# -- two owners: offline recipient, receipts, a reply, and isolation ------------------------

@pytest.mark.asyncio
async def test_two_owner_exchange_offline_recipient_and_isolation(
        server, team_server, mongo, nodes, monkeypatch):
    db = mongo[team_server.dbname]
    tim = await here(server, team_server, TIM, monkeypatch)
    await team_server.member(MATT)
    await tim.rt.local.recipients()                      # Matt is on the roster now

    # offline-recipient: accepted before Matt's JARVIS exists.
    sent = await tim.approved(MATT, "Can we move the deploy?", subject="Deploy")
    await tim.tick()
    assert tim.send_state(sent["message_id"]).state == "accepted"
    mine = _item(await tim.snapshot(), sent["thread_id"])
    assert mine["work_state"]["placement"] == "active", "Outbox"
    assert mine["work_state"]["action_owner_github_id"] == MATT.id, "Waiting on Matt"
    assert mine["messages"][0]["receipts"]["received_at"] is None

    matt = await nodes(team_server, MATT, "matt")          # comes online, catches up
    theirs = await matt.until(P, lambda s: _item(s, sent["thread_id"]) is not None,
                              "Matt's JARVIS holds the message")
    item = _item(theirs, sent["thread_id"])
    assert item["work_state"]["placement"] == "queued" and item["messages"][0][
        "direction"] == "incoming"
    deadline = time.monotonic() + CONVERGE_SEC
    while True:
        await tim.tick()
        facts = _item(await tim.snapshot(), sent["thread_id"])["messages"][0]["receipts"]
        if facts["received_at"]:
            break
        assert time.monotonic() < deadline, "Tim never learned it was received"
        await asyncio.sleep(0.5)
    assert facts["read_at"] is None, "received is not read"

    await matt.ok("POST", f"{P}/{sent['message_id']}/read")
    while not _item(await tim.snapshot(), sent["thread_id"])["messages"][0]["receipts"][
            "read_at"]:
        assert time.monotonic() < deadline + CONVERGE_SEC, "never read"
        await asyncio.sleep(0.5)
        await tim.tick()
    assert _item(await tim.snapshot(), sent["thread_id"])["work_state"][
        "action_owner_github_id"] == MATT.id, "a receipt is not a reply"

    # Matt replies through his own review and Send.
    reply = await matt.ok("POST", P + "/drafts", json={
        "recipient_github_id": TIM.id, "text": "Friday works.", "thread_id": sent["thread_id"],
        "in_reply_to_message_id": sent["message_id"]})
    reviewed = await matt.ok("POST", f"{P}/drafts/{reply['message_id']}/review",
                             json={"expected_draft_revision": reply["draft_revision"]})
    await matt.ok("POST", f"/api/capture/{reviewed['capture_id']}/send")
    deadline = time.monotonic() + CONVERGE_SEC
    while db.messages.count_documents({"_id": reply["message_id"]}) == 0 and \
            db.messages.count_documents({"body.message_id": reply["message_id"]}) == 0:
        assert time.monotonic() < deadline, "Matt's reply never went"
        await asyncio.sleep(0.5)
    await tim.tick()
    item = _item(await tim.snapshot(), sent["thread_id"])
    assert [m["message"]["message_id"] for m in item["messages"]] == [
        sent["message_id"], reply["message_id"]]
    assert item["work_state"]["action_owner_github_id"] == TIM.id, "his turn again"
    assert item["work_state"]["unread_reminder"], "the reply is unread"
    assert item["work_state"]["placement"] == "active", "an open exchange stays where it is"

    # two-owner-isolation: Bob sees none of it, whatever he asks.
    bob = await raw(team_server, BOB)
    assert await refused(bob.t.get(UUID(sent["message_id"]))) == "message_not_found"
    page = await bob.t.sync(bob.node, None, wire.SYNC_PAGE_MAX)
    assert page.entries == []
    assert await refused(bob.t.change_work_state(UUID(sent["thread_id"]), wire.WorkCommandRequest(
        change=wire.PutBack(expected_revision=1)))) in ("message_not_found",
                                                           "revision_conflict")
    assert db.message_work.count_documents({"owner_github_id": BOB.id}) == 0
    # A forged source node, recipient or relation is refused.
    forged = bob.request(MATT.id, "Hi", node=matt_node_id(db, MATT))
    assert await refused(bob.t.send(forged)) == "source_node_not_owned"
    assert await refused(bob.t.send(bob.request(6666, "Hi"))) == "recipient_not_allowed"
    assert await refused(bob.t.send(bob.request(
        MATT.id, "Hi", related=[UUID(sent["message_id"])]))) == "relation_not_allowed"
    # The admin (Tim) cannot read Bob and Matt's message either.
    theirs = await bob.t.send(bob.request(MATT.id, "Just us."))
    timraw = Raw(team_server, TIM, team_client_token())
    assert await refused(timraw.t.get(theirs.message.message_id)) == "message_not_found"
    # Suspended, Bob's session can do nothing.
    await team_server.suspend(BOB)
    assert await refused(bob.t.sync(bob.node, None, 1)) in ("membership_suspended",
                                                            "session_revoked")
    assert db.messages.count_documents({}) == 3


def matt_node_id(db, who: Identity) -> UUID:
    doc = db.network_nodes.find_one({"owner_github_id": who.id})
    return UUID(doc["_id"])


def team_client_token() -> str:
    from jarvis import team_client
    return team_client.connection.store.get(team_client.connection.saved["server_id"])


# -- self-two-nodes: one owner, two JARVIS, one announcement, failover ---------------------

@pytest.mark.asyncio
async def test_self_two_nodes_one_state_one_announcement_and_failover(
        server, team_server, mongo, nodes, monkeypatch):
    db = mongo[team_server.dbname]
    tim = await here(server, team_server, TIM, monkeypatch)
    desk = await nodes(team_server, TIM, "desk")
    sent = await tim.approved(TIM, "Note across my machines.")
    await tim.tick()
    assert tim.send_state(sent["message_id"]).state == "accepted"
    # This node claims the announcement first, then dies before saying it.
    local = tim.rt.store.message(*tim.ns, UUID(sent["message_id"]))
    from jarvis.transfer_presentation import transfer_event
    event = transfer_event(TIM.id, local.message, "incoming", tim.rt.delivery.clock())
    token = await tim.rt.presentation.reserve_announcement(event)
    assert token is not None
    claimed_at = time.monotonic()

    # The other node holds the same one item and acknowledges receipt itself.
    theirs = await desk.until(P, lambda s: _item(s, sent["thread_id"]) is not None,
                              "the desk JARVIS holds it")
    assert len(theirs["items"]) == 1
    deadline = time.monotonic() + CONVERGE_SEC
    while True:
        await tim.tick()
        nodes_acked = _item(await tim.snapshot(), sent["thread_id"])["messages"][0][
            "receipts"]["nodes"]
        if len(nodes_acked) == 2:
            break
        assert time.monotonic() < deadline, "the desk never acknowledged"
        await asyncio.sleep(0.5)
    assert len((await tim.snapshot())["items"]) == 1, "one item, whatever the receipts"

    # Failover: the lease lapses (30 s) and the desk says it, once.
    lease = db.message_announcements.find_one({})["body"]
    assert (lease["state"], lease["node_id"]) == ("claimed", str(tim.rt.node))
    deadline = claimed_at + ports_lease_sec() + CONVERGE_SEC
    while db.message_announcements.find_one({})["body"]["state"] != "completed":
        assert time.monotonic() < deadline, "the desk never took the announcement over"
        await asyncio.sleep(1)
    done = db.message_announcements.find_one({})["body"]
    assert done["node_id"] == desk_node(desk), "said by the node that took it over"
    # This node, back, finds it said elsewhere and says nothing.
    assert await tim.rt.announcer.announce_due() == 0
    assert tim.said == []
    # One owner state on both.
    mine = _item(await tim.snapshot(), sent["thread_id"])["work_state"]
    desk_state = _item(await desk.ok("GET", P), sent["thread_id"])["work_state"]
    assert mine == desk_state


def ports_lease_sec() -> float:
    from teamserver.messaging_ports import ANNOUNCEMENT_LEASE_SEC
    return ANNOUNCEMENT_LEASE_SEC


def desk_node(node: Node) -> str:
    return (Path(node.log.name).parent / "data" / "node-id").read_text().strip()


# -- queue work across the owner's nodes, history and failed sends -----------------------

@pytest.mark.asyncio
async def test_queue_work_conflicts_history_and_a_failed_send_stays_visible(
        server, team_server, mongo, nodes, monkeypatch):
    tim = await here(server, team_server, TIM, monkeypatch)
    desk = await nodes(team_server, TIM, "desk")
    a = await tim.approved(TIM, "First.")
    b = await tim.approved(TIM, "Second.")
    await tim.tick(2)
    snap = await tim.snapshot()
    arrivals = [i["work_state"]["arrival_at"] for i in snap["items"]]
    assert {i["work_state"]["thread_id"] for i in snap["items"]} == {a["thread_id"],
                                                                     b["thread_id"]}
    assert arrivals == sorted(arrivals, reverse=True), "newest arrival first"
    ws = _item(snap, a["thread_id"])["work_state"]
    opened = await tim.ok("PUT", f"{P}/threads/{a['thread_id']}/work-state", json={
        "change": {"command": "open", "expected_revision": ws["revision"]}})
    assert opened["work_state"]["placement"] == "active"
    # The desk changes it from the revision it last saw: a conflict, with the latest.
    await desk.until(P, lambda s: _item(s, a["thread_id"]) is not None, "desk has it")
    r = await desk.http.put(f"{P}/threads/{a['thread_id']}/work-state", json={
        "change": {"command": "put_back", "expected_revision": ws["revision"]}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "revision_conflict"
    assert r.json()["error"]["latest"]["placement"] == "active"
    # Dismissed keeps its history; restore brings it back.
    latest = opened["work_state"]
    gone = await tim.ok("PUT", f"{P}/threads/{a['thread_id']}/work-state", json={
        "change": {"command": "dismiss", "expected_revision": latest["revision"]}})
    snap = await tim.snapshot()
    assert _item(snap, a["thread_id"]) is None and snap["history_count"] == 1
    history = await tim.ok("GET", P + "/history")
    assert [i["work_state"]["thread_id"] for i in history["items"]] == [a["thread_id"]]
    assert len(history["items"][0]["messages"]) == 1
    await tim.ok("PUT", f"{P}/threads/{a['thread_id']}/work-state", json={
        "change": {"command": "restore", "expected_revision": gone["work_state"]["revision"]}})
    assert _item(await tim.snapshot(), a["thread_id"]) is not None
    # A send the team server refuses stays on screen as failed, with Retry.
    await team_server.member(MATT)
    c = await tim.approved(MATT, "To someone suspended.")
    await team_server.suspend(MATT)
    await tim.tick()
    assert tim.send_state(c["message_id"]).state == "failed"
    unsent = (await tim.snapshot())["unsent"]
    assert [(d["message_id"], d["send"]["state"]) for d in unsent] == [
        (c["message_id"], "failed")]


# -- files: interrupted upload, verified download, a missing copy -------------------------

async def _attach(tim: Here, draft: dict, name: str, data: bytes) -> dict:
    added = await tim.ok("POST", f"{P}/drafts/{draft['message_id']}/files", content=data,
                         headers={"X-Filename": name, "Content-Type": "application/pdf"})
    return await tim.ok("PUT", f"{P}/drafts/{draft['message_id']}", json={
        "expected_draft_revision": draft["draft_revision"],
        "recipient_github_id": draft["recipient_github_id"], "text": draft["text"],
        "attachment_ids": [added["attachment_id"]]})


@pytest.mark.asyncio
async def test_files_interrupted_upload_verified_download_and_a_missing_copy(
        server, team_server, mongo, nodes, monkeypatch, tmp_path):
    db = mongo[team_server.dbname]
    tim = await here(server, team_server, TIM, monkeypatch)
    matt = await nodes(team_server, MATT, "matt")
    await tim.rt.local.recipients()
    data = os.urandom(3 * 1024 * 1024 + 17)            # several chunks, past every body gate
    draft = await _attach(tim, await tim.draft(MATT, "The plan, attached."), "plan.pdf", data)
    assert [f["state"] for f in draft["files"]] == ["staged"]
    reviewed = await tim.review(draft)
    assert "plan.pdf" in server._captures[reviewed["capture_id"]]["text"], "named in review"
    await tim.send(reviewed)

    team_server.faults.arm("server_before_attachment_finalize")
    await tim.tick()                                   # the upload is cut off before it is kept
    held = tim.send_state(reviewed["message_id"])
    assert held.state == "approved" and held.next_attempt_at is not None, held
    assert db.messages.count_documents({}) == 0, "nothing visible from a partial upload"
    staging = db.attachment_staging.find_one({})
    assert staging["state"] == "uploading"

    tim.rt.store.transition_send(*tim.ns, held, held.model_copy(
        update={"next_attempt_at": tim.rt.delivery.clock()}))
    await tim.tick()                                   # again from byte 0, verified, accepted
    assert tim.send_state(reviewed["message_id"]).state == "accepted"
    assert db.messages.count_documents({}) == 1
    assert db.attachment_staging.find_one({})["state"] in ("pinned", "published")

    item = await matt.until(P, lambda s: _item(s, reviewed["thread_id"]) is not None,
                            "Matt's JARVIS holds the message and its manifest")
    (message,) = _item(item, reviewed["thread_id"])["messages"]
    (ref,) = message["message"]["attachments"]
    assert ref["filename"] == "plan.pdf" and ref["size_bytes"] == len(data)
    got = await matt.ok("POST", f"{P}/{reviewed['message_id']}/files/"
                                f"{ref['attachment_id']}/download")
    assert got["state"] == "verified"
    kept = tmp_path / "matt" / "data" / "messaging" / "downloads" / ref["attachment_id"] / \
        "plan.pdf"
    assert kept.read_bytes() == data, "the verified bytes are the approved bytes"
    assert oct(kept.parent.stat().st_mode & 0o777) == "0o700"

    # A private copy that has gone: the yes cannot approve it, nothing goes.
    second = await _attach(tim, await tim.draft(MATT, "Another."), "other.pdf", b"x" * 10)
    reviewed2 = await tim.review(second)
    from jarvis import data_paths
    (data_paths.messaging_snapshots_dir() / second["attachments"][0]["attachment_id"]).unlink()
    await tim.send(reviewed2)
    assert tim.send_state(reviewed2["message_id"]).state == "awaiting_approval"
    assert tim.rt.store.approved(*tim.ns, UUID(reviewed2["message_id"]), 1) is None
    await tim.tick()
    assert db.messages.count_documents({}) == 1


# -- duplicate and reordered events ----------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicates_and_a_rescan_present_nothing_twice(
        server, team_server, mongo, monkeypatch):
    tim = await here(server, team_server, TIM, monkeypatch)
    frames: list[dict] = []
    tim.rt.presentation._send = frames.append
    sent = await tim.approved(TIM, "Once on screen.")
    await tim.tick(3)
    before = _item(await tim.snapshot(), sent["thread_id"])
    received = before["messages"][0]["receipts"]["received_at"]
    assert received
    # A full rescan from the start (a lost continuation) and repeated passes.
    tim.rt.store.restart_sync(*tim.ns, tim.rt.node)
    await tim.tick(2)
    after = await tim.snapshot()
    assert len(after["items"]) == 1
    assert _item(after, sent["thread_id"])["messages"][0]["receipts"]["received_at"] == \
        received, "a receipt time never moves"
    assert _item(after, sent["thread_id"])["work_state"]["revision"] >= \
        before["work_state"]["revision"], "revisions never regress"
    live = [f["event"]["direction"] for f in frames if f["type"] == "human_transfer"]
    assert sorted(live) == ["incoming", "outgoing"], "each drawn once"
    replay = tim.rt.replay_frames()
    assert replay and all(f["event"]["replay"] for f in replay), "a reconnect only reconciles"
    assert await tim.rt.announcer.announce_due() == 1
    tim.rt.store.restart_sync(*tim.ns, tim.rt.node)
    await tim.tick()
    assert await tim.rt.announcer.announce_due() == 0, "never announced again"


# -- notification isolation ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_arrival_never_disturbs_what_he_is_writing_or_reviewing(
        server, team_server, mongo, nodes, monkeypatch):
    from jarvis import conversation_log
    tim = await here(server, team_server, TIM, monkeypatch)
    matt = await nodes(team_server, MATT, "matt")
    await tim.rt.local.recipients()
    mine = await tim.review(await tim.draft(MATT, "Half way through this."))
    capture = dict(server._captures[mine["capture_id"]])
    # Matt sends Tim a message through his own JARVIS.
    draft = await matt.ok("POST", P + "/drafts",
                          json={"recipient_github_id": TIM.id, "text": "Quick question."})
    r = await matt.ok("POST", f"{P}/drafts/{draft['message_id']}/review",
                      json={"expected_draft_revision": draft["draft_revision"]})
    await matt.ok("POST", f"/api/capture/{r['capture_id']}/send")
    deadline = time.monotonic() + CONVERGE_SEC
    while _item(await tim.snapshot(), draft["thread_id"]) is None:
        assert time.monotonic() < deadline, "never arrived"
        await asyncio.sleep(0.5)
        await tim.tick()
    monkeypatch.setattr(server, "speech", None)       # muted: no voice at all
    tim.rt.announcer._notify = server._say_arrival     # the server's own notice path
    assert await tim.rt.announcer.announce_due() == 1
    notices = [i for i in conversation_log.page()
               if i.kind == conversation_log.ItemKind.NOTICE
               and i.detail.get("thread_id") == draft["thread_id"]]
    assert len(notices) == 1, "the visible cue stays when nothing can speak"
    assert "Quick question" not in notices[0].text, "the body is never said"
    assert server._captures[mine["capture_id"]] == capture, "his review is untouched"
    kept = tim.rt.store.draft(*tim.ns, UUID(mine["message_id"]))
    assert (kept.text, kept.send.state) == ("Half way through this.", "awaiting_approval")
    incoming = _item(await tim.snapshot(), draft["thread_id"])
    assert incoming["work_state"]["unread_reminder"] and \
        incoming["messages"][0]["receipts"]["read_at"] is None, "speech is not reading"


# -- the page, on the real routes ---------------------------------------------------------

@pytest.mark.page
@pytest.mark.asyncio
async def test_the_page_opens_an_item_on_the_real_routes_and_only_then_reads_it(
        server, team_server, nodes, monkeypatch):
    """The built page served by a JARVIS process, its Queue on that
    process's real routes and store: the arrival is listed, opening the item
    shows the body, and only then is the read receipt made."""
    from tests.test_agents_list_fits import async_api
    from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
    if not (ROOT / "jarvis" / "web" / "index.html").is_file():
        pytest.skip("build the page first (cd frontend && npm run build)")
    tim = await here(server, team_server, TIM, monkeypatch)
    desk = await nodes(team_server, TIM, "desk")
    sent = await tim.approved(TIM, "Read me on the desk.", subject="On the desk")
    await tim.tick()
    await desk.until(P, lambda s: _item(s, sent["thread_id"]) is not None, "desk has it")
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        # Not first-run setup: the voice key is said to be there (nothing speaks).
        await page.route("**/api/settings/status", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({
                "claude_code_installed": True, "server_port": desk.port, "uptime_seconds": 1,
                "env_keys_set": {"fish_audio": True, "fish_voice_id": True,
                                 "user_name": "Tim"}})))
        reads = []
        page.on("request", lambda req: reads.append(req.url)
                if req.method == "POST" and req.url.endswith("/read") else None)
        await page.goto(desk.url + "/")
        await page.click(".right-tabs [aria-label='Queue']")
        row = f'.queue-view li[data-thread-id="{sent["thread_id"]}"] .queue-row'
        await page.wait_for_selector(row, timeout=20000)
        assert "On the desk" in await page.locator(row).inner_text()
        assert reads == [], "listing is not reading"
        await page.click(row)
        await page.wait_for_selector(".message-text", timeout=20000)
        assert "Read me on the desk." in await page.locator(".message-text").first.inner_text()
        await desk.until(P, lambda s: _item(s, sent["thread_id"])["messages"][0]["receipts"][
            "read_at"] is not None, "the read receipt, once the body was on screen")
        assert len(reads) == 1
        await browser.close()
