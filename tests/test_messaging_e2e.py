"""Delivery end to end, with nothing faked between the two sides: the real
team server app (its own device sessions, routes and MongoDB repository, in
this process and event loop), the real `team_client.TeamConnection` over
it, the real transport and delivery loop behind the approval guard
(`message_approvals.MessageApprovals`, with `GuardedTransport` as the
sender's transport), and each JARVIS node's own SQLite store.

Needs the test MongoDB: set TEAM_SERVER_TEST_MONGODB_URI (an unreachable
one fails). Without it the module skips, as the team server's suite does.
Every database it makes is named hm_delivery_* and dropped afterwards.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis import team_client
from jarvis.message_approvals import GuardedTransport, MessageApprovals
from jarvis.messaging_client import Delivery, TeamTransport
from jarvis.messaging_store import SqliteMessagingStore
from tests.fixtures import messaging_fakes as fakes
from tests.fixtures.memory_secrets import MemorySecrets

MONGO = os.environ.get("TEAM_SERVER_TEST_MONGODB_URI")
if not MONGO:
    pytest.skip("needs TEAM_SERVER_TEST_MONGODB_URI and the team server's packages",
                allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "team_server"))
from teamserver import clock as ts_clock  # noqa: E402
from teamserver import messaging as ts_messaging  # noqa: E402
from teamserver import sessions as ts_sessions  # noqa: E402
from teamserver import team as ts_team  # noqa: E402
from teamserver.app import create_app  # noqa: E402
from teamserver.config import Config  # noqa: E402
from teamserver.github import Identity  # noqa: E402

BASE = "https://team.test"
TIM = Identity(id=863470, login="tistocks", name="Tim")
MATT = Identity(id=1001, login="matt", name="Matt")
BOB = Identity(id=1002, login="bob", name="Bob")


class Recorder:
    def __init__(self):
        self.presented: list[ports.TransferEvent] = []

    def present(self, event):
        self.presented.append(event)


def now() -> datetime:
    return datetime.now(timezone.utc)


class Node:
    """One JARVIS backend paired as `identity`: its data, its loop."""

    def __init__(self, server, identity: Identity, token: str, path: Path):
        self.server, self.identity = server, identity
        self.connection = team_client.TeamConnection(store=MemorySecrets(), transport=server.asgi)
        self.connection.saved = {"server_url": BASE, "server_id": server.server_id,
                                 "me": {"github_id": identity.id}}
        self.connection.store.set(server.server_id, token)
        self.node = uuid.uuid4()
        self.store = SqliteMessagingStore(path)
        self.guard = MessageApprovals(self.store, fakes.StaticSnapshots(), ids=ports.RandomIds(), clock=now)
        self.seen = Recorder()
        self.delivery = Delivery(store=self.store, connection=self.connection, guard=self.guard,
                                 presentation=self.seen, node_id=lambda: self.node, clock=now,
                                 transport_for=lambda c, p: GuardedTransport(
                                     TeamTransport(c, p), self.guard, p.server_id,
                                     p.owner_github_id))

    @property
    def ns(self):
        return self.server.server_id, self.identity.id

    async def publish(self):
        status, body = await self.connection.call("POST", team_client.NETWORK_PATH, {
            "node_id": str(self.node), "boot_id": str(uuid.uuid4()), "seq": 1,
            "label": "TEST NODE", "jarvis_version": "1", "os": "macOS",
            "resources": {"state": "measured", "scope": "host", "cpu_percent": 1.0,
                          "memory_used_bytes": 1, "memory_total_bytes": 2, "age_ms": 0},
            "brains": [], "activity": None})
        assert status == 200, body

    def approve(self, to: Identity, text: str, reply_to=None) -> ports.Draft:
        mid = uuid.uuid4()
        self.store.create_draft(*self.ns, ports.Draft(
            message_id=mid, thread_id=reply_to.thread_id if reply_to else mid,
            in_reply_to_message_id=reply_to.message_id if reply_to else None,
            recipient_github_id=to.id, draft_revision=1, text=wire.normalize_text(text),
            send=ports.LocalSend(message_id=mid, content_revision=1, state="draft")))
        reviewed = self.guard.request_review(*self.ns, mid, 1)
        self.guard.approve(*self.ns, mid, 1, reviewed.send.content_digest, self.node, "voice")
        return self.store.draft(*self.ns, mid)

    def state(self, draft) -> str:
        return self.store.send(*self.ns, draft.message_id).state


class TeamServer:
    def __init__(self, app, faults):
        self.app, self.faults = app, faults
        self.asgi = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        self.server_id = None

    async def member(self, identity: Identity, tmp_path: Path, admin: Identity = TIM) -> Node:
        st = self.app.state
        if identity != admin:
            await ts_team.mutate(st.db, ts_team.change_member(admin.id, identity.id, identity,
                                                              None, None))
        team, member = await ts_team.admit(st.db, st.config, st.server_id, identity)
        return await self.device(identity, team, member, tmp_path)

    async def device(self, identity, team, member, tmp_path) -> Node:
        token, _ = await ts_sessions.issue(self.app.state.db, team, member, "device",
                                           auth_at=ts_clock.now(), device_name="JARVIS test")
        node = Node(self, identity, token, tmp_path / f"{identity.login}-{uuid.uuid4().hex}.db")
        await node.publish()
        return node


@pytest.fixture
def dbname():
    from pymongo import MongoClient
    name = "hm_delivery_" + uuid.uuid4().hex[:12]
    yield name
    client = MongoClient(MONGO, serverSelectionTimeoutMS=2000)
    try:
        assert name.startswith("hm_delivery_")
        client.drop_database(name)
    finally:
        client.close()


def run_with_team_server(dbname, scenario, faults=wire.NO_FAULTS):
    """`scenario(server)` with a started team server, its messaging
    mounted by the app's own wiring; the
    service is given the test's faults (the production app gets none)."""
    config = Config(port=8080, public_base_url=BASE, mongodb_uri=MONGO,
                    mongodb_database=dbname, github_client_id="cid",
                    github_client_secret="not-a-secret", team_name="Test Team",
                    initial_admin_github_id=TIM.id)

    async def go():
        app = create_app(config, github=object())
        async with app.router.lifespan_context(app):
            for _ in range(200):
                if getattr(app.state, "attachments", None) is not None:
                    break
                await asyncio.sleep(0.05)
            assert app.state.initialized and app.state.attachments is not None
            app.state.messaging = ts_messaging.Messaging(ts_messaging.MongoMessages(app.state.db),
                                                         faults=faults)
            server = TeamServer(app, faults)
            server.server_id = app.state.server_id
            await scenario(server)
    asyncio.run(go())


def test_self_send_and_a_two_owner_exchange_with_receipts_and_a_reply(dbname, tmp_path):
    async def scenario(server: TeamServer):
        tim = await server.member(TIM, tmp_path)
        matt = await server.member(MATT, tmp_path)
        bob = await server.member(BOB, tmp_path)

        note = tim.approve(TIM, "Remember the deploy.")
        await tim.delivery.tick()
        assert tim.state(note) == "accepted"
        assert [e.message_id for e in tim.seen.presented] == [note.message_id]
        local = tim.store.message(*tim.ns, note.message_id)   # received in the same pass
        assert local.self_message and local.receipts.received_at is not None
        assert local.receipts.read_at is None, "read only when he reads it"

        question = tim.approve(MATT, "Can we move the deploy?")
        await tim.delivery.tick()
        await matt.delivery.tick()
        assert [e.message_id for e in matt.seen.presented] == [question.message_id]
        root = matt.store.message(*matt.ns, question.message_id)
        assert root.direction == "incoming"
        matt.store.owe_receipt(*matt.ns, question.message_id, "read")
        await matt.delivery.tick()
        await tim.delivery.tick()
        facts = tim.store.message(*tim.ns, question.message_id).receipts
        assert facts.received_at is not None and facts.read_at is not None
        item = tim.store.item(*tim.ns, question.thread_id).work_state
        assert item.action_owner_github_id == MATT.id, "a receipt is not a reply"

        answer = matt.approve(TIM, "Friday works.", reply_to=root.message)
        await matt.delivery.tick()
        await tim.delivery.tick()
        item = tim.store.item(*tim.ns, question.thread_id)
        assert [m.message.message_id for m in item.messages] == [question.message_id,
                                                                 answer.message_id]
        assert item.work_state.action_owner_github_id == TIM.id
        assert len(tim.store.snapshot(*tim.ns, availability="available",
                                      stale=False).items) == 2

        await bob.delivery.tick()
        assert bob.store.snapshot(*bob.ns, availability="available", stale=False).items == []
        assert bob.seen.presented == [], "nothing of Tim's and Matt's reaches Bob"

    run_with_team_server(dbname, scenario)


def test_a_lost_answer_from_the_real_server_is_one_message(dbname, tmp_path):
    faults = wire.ArmedFaults()

    async def scenario(server: TeamServer):
        tim = await server.member(TIM, tmp_path)
        matt = await server.member(MATT, tmp_path)
        draft = tim.approve(MATT, "Once only.")
        faults.arm("server_after_commit_before_response")
        await tim.delivery.tick()
        lost = tim.store.send(*tim.ns, draft.message_id)
        assert (lost.state, lost.failure) == ("accept_unknown", "internal")
        tim.store.transition_send(*tim.ns, lost, lost.model_copy(update={
            "next_attempt_at": now()}))
        await tim.delivery.tick()
        assert tim.state(draft) == "accepted"
        await matt.delivery.tick()
        assert [e.message_id for e in matt.seen.presented] == [draft.message_id]
        assert await server.app.state.db.messages.count_documents({}) == 1

    run_with_team_server(dbname, scenario, faults)


def test_self_two_nodes_converge_and_each_acknowledges(dbname, tmp_path):
    async def scenario(server: TeamServer):
        laptop = await server.member(TIM, tmp_path)
        team = await ts_team.get(server.app.state.db)
        desk = await server.device(TIM, team, team["members"][ts_team.key(TIM.id)], tmp_path)
        note = laptop.approve(TIM, "Note to self.")
        await laptop.delivery.tick()
        await laptop.delivery.tick()
        await desk.delivery.tick()
        await desk.delivery.tick()
        facts = desk.store.message(*desk.ns, note.message_id).receipts
        assert [n.node_id for n in facts.nodes] == [laptop.node, desk.node]
        assert [e.message_id for e in desk.seen.presented] == [note.message_id]
        for node in (laptop, desk):
            snap = node.store.snapshot(*node.ns, availability="available", stale=False)
            assert len(snap.items) == 1
            assert snap.items[0].work_state.action_owner_github_id == TIM.id

    run_with_team_server(dbname, scenario)


def test_a_new_pairing_never_takes_an_answer_meant_for_the_old_one(dbname, tmp_path):
    async def scenario(server: TeamServer):
        tim = await server.member(TIM, tmp_path)
        draft = tim.approve(TIM, "Sent before the switch.")
        real_call = tim.connection.call

        async def re_paired_meanwhile(method, path, body=None):
            answer = await real_call(method, path, body)
            if method == "POST" and path == "/api/v1/messages":
                tim.connection.generation += 1          # paired again while it was out
            return answer
        tim.connection.call = re_paired_meanwhile
        await tim.delivery.tick()
        send = tim.store.send(*tim.ns, draft.message_id)
        assert (send.state, send.failure) == ("accept_unknown", "transport")
        assert tim.store.message(*tim.ns, draft.message_id) is None, "the answer was discarded"
        tim.connection.call = real_call
        tim.store.transition_send(*tim.ns, send, send.model_copy(update={
            "next_attempt_at": now()}))
        await tim.delivery.tick()
        assert tim.state(draft) == "accepted", "the same key settles it under the new pairing"

    run_with_team_server(dbname, scenario)
