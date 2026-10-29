"""The delivery loop and the messaging transport: approved sends go out
through the guard and survive every crash point, arrivals are stored and
presented once, receipts and kept work commands are settled, and an answer
from a previous pairing never touches the new one. The team server here is
the contract's FakeServe; tests/test_messaging_e2e.py runs the same loop
against the real team server and MongoDB."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.messaging_client import Delivery, Pairing, TeamTransport
from jarvis.messaging_store import SqliteMessagingStore
from jarvis.team_client import TeamError
from tests.fixtures import messaging_fakes as fakes

SERVER = "server-1"
TIM, MATT, OUTSIDER = 863470, 1001, 6666


class Connection:
    """The team connection as delivery reads it."""

    def __init__(self, owner: int, server_id: str = SERVER):
        self.saved = {"server_id": server_id, "me": {"github_id": owner}}
        self.signed_out = False
        self.generation = 1


class Lossy:
    """A paired node's transport on which a fault the team server hits
    after committing is what the client sees: no answer."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        method = getattr(self.inner, name)

        async def call(*a, **k):
            try:
                return await method(*a, **k)
            except wire.InjectedFault:
                raise ports.TransportError("transport") from None
        return call


class Jarvis:
    """One JARVIS node: its own database, connection, guard and loop."""

    def __init__(self, serve, owner, node, tmp_path, clock, faults=wire.NO_FAULTS, store=None):
        self.serve, self.owner, self.node, self.clock = serve, owner, UUID(int=node), clock
        self.store = store or SqliteMessagingStore(tmp_path / f"node-{node}.db")
        self.connection = Connection(owner)
        self.transport = Lossy(serve.pair(owner, self.node))
        self.ids = fakes.SeqIds(start=node * 10_000)
        self.guard = fakes.TableGuard(self.store, ids=self.ids, clock=clock)
        self.presentation = fakes.RecordingPresentation(serve.pair(owner, self.node))
        self.hints = 0
        self.faults = faults
        self.delivery = self.restart()

    def restart(self) -> Delivery:
        """A new process over the same database."""
        def hint():
            self.hints += 1
        self.delivery = Delivery(
            store=self.store, connection=self.connection, guard=self.guard,
            presentation=self.presentation, node_id=lambda: self.node, clock=self.clock,
            rand=fakes.SeqRandom(), faults=self.faults, on_change=hint, enabled=lambda: True,
            transport_for=lambda connection, pairing: self.transport)
        return self.delivery

    @property
    def ns(self):
        return SERVER, self.owner

    def approve(self, to, text, reply_to: wire.AcceptedMessage | None = None) -> ports.Draft:
        mid = self.ids.new_id()
        draft = ports.Draft(
            message_id=mid, thread_id=reply_to.thread_id if reply_to else mid,
            in_reply_to_message_id=reply_to.message_id if reply_to else None,
            recipient_github_id=to, draft_revision=1, text=wire.normalize_text(text),
            send=ports.LocalSend(message_id=mid, content_revision=1, state="draft"))
        self.store.create_draft(*self.ns, draft)
        reviewed = self.guard.request_review(*self.ns, mid, 1)
        self.guard.approve(*self.ns, mid, 1, reviewed.send.content_digest, self.node, "gui")
        return self.store.draft(*self.ns, mid)

    def tick(self):
        asyncio.run(self.delivery.tick())

    def send_of(self, draft) -> ports.LocalSend:
        return self.store.send(*self.ns, draft.message_id)

    def incoming(self) -> list[UUID]:
        return [e.message_id for e in self.presentation.presented]


@pytest.fixture
def clock():
    return fakes.FixedClock()


@pytest.fixture
def faults():
    return wire.ArmedFaults()


@pytest.fixture
def serve(clock, faults):
    return fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=clock, faults=faults)


@pytest.fixture
def tim(serve, tmp_path, clock, faults):
    return Jarvis(serve, TIM, 1, tmp_path, clock, faults)


@pytest.fixture
def matt(serve, tmp_path, clock, faults):
    return Jarvis(serve, MATT, 2, tmp_path, clock, faults)


# -- named scenarios -----------------------------------------------------------------

def test_self_one_node_is_one_item_presented_once_and_read_only_when_read(tim, serve):
    draft = tim.approve(TIM, "Remember the deploy.")
    tim.tick()
    assert tim.send_of(draft).state == "accepted"
    assert tim.incoming() == [draft.message_id], "the legitimate first incoming cue"
    facts = serve.receipts[draft.message_id]
    assert facts.received_at is not None and facts.read_at is None
    tim.tick()
    assert tim.incoming() == [draft.message_id], "presented once"
    snap = tim.store.snapshot(*tim.ns, availability="available", stale=False)
    assert len(snap.items) == 1 and snap.items[0].work_state.action_owner_github_id == TIM
    assert len(serve.messages) == 1, "no reply loop"
    tim.store.owe_receipt(*tim.ns, draft.message_id, "read")
    tim.tick()
    assert serve.receipts[draft.message_id].read_at is not None
    assert tim.hints >= 2


def test_two_owners_exchange_and_the_turn_follows_the_messages(tim, matt, serve, clock):
    question = tim.approve(MATT, "Can we move the deploy?")
    tim.tick()
    matt.tick()
    assert matt.incoming() == [question.message_id]
    root = matt.store.message(*matt.ns, question.message_id).message
    tim.tick()
    assert tim.store.message(*tim.ns, question.message_id).receipts.received_at is not None
    assert tim.store.item(*tim.ns, root.thread_id).work_state.action_owner_github_id == MATT
    clock.advance(minutes=1)
    answer = matt.approve(TIM, "Friday works.", reply_to=root)
    matt.tick()
    tim.tick()
    item = tim.store.item(*tim.ns, root.thread_id)
    assert [m.message.message_id for m in item.messages] == [question.message_id,
                                                             answer.message_id]
    assert (item.work_state.action_owner_github_id, item.work_state.unread_reminder) == \
        (TIM, True)
    assert tim.incoming() == [answer.message_id], "his own question is not an arrival"


def test_a_lost_answer_is_retried_under_the_same_key_into_one_message(tim, serve, faults,
                                                                      clock):
    draft = tim.approve(MATT, "Once only.")
    faults.arm("server_after_commit_before_response")
    tim.tick()
    lost = tim.send_of(draft)
    assert (lost.state, lost.failure, lost.attempts) == ("accept_unknown", "transport", 1)
    assert lost.next_attempt_at > clock()
    tim.tick()
    assert tim.send_of(draft).state == "accept_unknown", "not before its time"
    clock.at = lost.next_attempt_at
    tim.tick()
    assert tim.send_of(draft).state == "accepted" and len(serve.messages) == 1


@pytest.mark.parametrize("point", ["client_before_send", "client_before_local_save"])
def test_a_crash_around_the_request_is_an_unknown_answer_after_restart(tim, serve, faults,
                                                                       point):
    draft = tim.approve(MATT, "Survive the crash.")
    faults.arm(point)
    with pytest.raises(wire.InjectedFault):
        tim.tick()
    assert tim.send_of(draft).state == "submitting", "the attempt was stored before it left"
    tim.restart()
    tim.tick()
    assert tim.send_of(draft).state == "accepted"
    assert len(serve.messages) == 1


def test_a_crash_before_the_receipt_is_acknowledged_sends_it_after_restart(tim, matt, serve,
                                                                           faults):
    draft = tim.approve(MATT, "Hello.")
    tim.tick()
    faults.arm("client_before_receipt_ack")
    with pytest.raises(wire.InjectedFault):
        matt.tick()
    assert matt.store.message(*matt.ns, draft.message_id) is not None
    assert serve.receipts[draft.message_id].received_at is None
    matt.restart()
    matt.tick()
    assert serve.receipts[draft.message_id].received_at is not None
    assert matt.incoming() == [draft.message_id], "stored and presented once"
    assert len(matt.store.item(*matt.ns, draft.thread_id).messages) == 1


def test_self_two_nodes_store_one_item_each_and_both_acknowledge(serve, tmp_path, clock, faults):
    laptop = Jarvis(serve, TIM, 1, tmp_path, clock, faults)
    desk = Jarvis(serve, TIM, 3, tmp_path, clock, faults)
    draft = laptop.approve(TIM, "Note to self.")
    laptop.tick()
    clock.advance(minutes=3)                  # the desk was offline
    desk.tick()
    facts = serve.receipts[draft.message_id]
    assert [n.node_id for n in facts.nodes] == [laptop.node, desk.node]
    assert desk.incoming() == [draft.message_id]
    for node in (laptop, desk):
        assert len(node.store.snapshot(*node.ns, availability="available",
                                       stale=False).items) == 1


# -- the guard, refusals and the owner's actions ----------------------------------------

def test_nothing_unapproved_is_sent_and_a_cancel_before_the_attempt_holds(tim, serve):
    mid = tim.ids.new_id()
    tim.store.create_draft(*tim.ns, ports.Draft(
        message_id=mid, thread_id=mid, recipient_github_id=MATT, draft_revision=1, text="x",
        send=ports.LocalSend(message_id=mid, content_revision=1, state="draft")))
    forged = tim.store.send(*tim.ns, mid).model_copy(update={"state": "approved"})
    tim.store.set_send(*tim.ns, forged)        # an outbox row with no approval record
    tim.tick()
    held = tim.store.send(*tim.ns, mid)
    assert (held.state, held.failure) == ("approved", "not_approved") and serve.messages == {}
    tim.tick()
    assert serve.messages == {}, "held until the owner acts"
    draft = tim.approve(MATT, "Never mind.")
    approved = tim.send_of(draft)
    tim.store.transition_send(*tim.ns, approved, approved.model_copy(update={
        "state": ports.next_send_state("approved", "cancel")}))
    tim.tick()
    assert serve.messages == {}


def test_a_refusal_is_failed_and_the_owners_retry_resends_the_same_revision(tim, serve, clock):
    draft = tim.approve(OUTSIDER, "Who are you?")
    tim.tick()
    failed = tim.send_of(draft)
    assert (failed.state, failed.failure) == ("failed", "recipient_not_allowed")
    tim.tick()
    assert tim.send_of(draft) == failed, "a refusal is not retried by itself"
    serve.members.add(OUTSIDER)
    tim.store.transition_send(*tim.ns, failed, failed.model_copy(update={
        "attempts": 0, "next_attempt_at": clock()}))
    tim.tick()
    assert tim.send_of(draft).state == "accepted"
    assert serve.messages[draft.message_id].idempotency_key == \
        tim.store.approved(*tim.ns, draft.message_id, 1).request.idempotency_key


def test_automatic_retries_stop_after_the_last_attempt_and_stay_unknown(tim, serve, clock):
    draft = tim.approve(MATT, "Flaky.")
    real_send = tim.transport.send

    async def every_answer_lost(request):
        await real_send(request)
        raise ports.TransportError("transport")
    tim.transport.send = every_answer_lost
    for _ in range(wire.RETRY_MAX_ATTEMPTS):
        tim.tick()
        send = tim.send_of(draft)
        if send.next_attempt_at:
            clock.at = send.next_attempt_at
    last = tim.send_of(draft)
    assert (last.state, last.attempts, last.next_attempt_at) == \
        ("accept_unknown", wire.RETRY_MAX_ATTEMPTS, None), "never 'failed': it may be accepted"
    assert len(serve.messages) == 1


def test_kept_work_commands_are_replayed_and_a_conflict_keeps_the_latest(tim, matt, serve):
    draft = tim.approve(MATT, "Hello.")
    tim.tick()
    matt.tick()
    item = matt.store.item(*matt.ns, draft.thread_id).work_state
    matt.store.queue_command(*matt.ns, draft.thread_id, wire.WorkCommandRequest(
        change=wire.OpenItem(expected_revision=item.revision)))
    matt.tick()
    assert matt.store.pending_commands(*matt.ns) == []
    opened = matt.store.item(*matt.ns, draft.thread_id).work_state
    assert opened.placement == "active"
    matt.store.queue_command(*matt.ns, draft.thread_id, wire.WorkCommandRequest(
        change=wire.Dismiss(expected_revision=item.revision)))
    matt.tick()
    assert matt.store.pending_commands(*matt.ns) == []
    assert matt.store.item(*matt.ns, draft.thread_id).work_state == opened, "not dismissed"


def test_an_expired_continuation_starts_a_new_scan(tim, matt, serve):
    for n in range(3):
        tim.approve(MATT, f"m{n}")
    tim.tick()
    matt.store.store_page(*matt.ns, matt.node, wire.SyncPage(
        epoch="old", entries=[], continuation=f"e9:{UUID(int=99)}:0", scan_complete=False,
        server_time=fakes.T0))
    matt.tick()
    assert len(matt.incoming()) == 3 and matt.delivery.stale is False


def test_switched_off_or_unsupported_does_nothing_and_keeps_the_outbox(tim, serve):
    draft = tim.approve(MATT, "Wait.")
    tim.delivery.enabled = lambda: False
    tim.tick()
    assert asyncio.run(tim.delivery.availability()) == "switched_off"
    tim.delivery.enabled = lambda: True
    serve.capabilities = []
    tim.restart()
    tim.tick()
    assert asyncio.run(tim.delivery.availability()) == "unsupported"
    assert tim.send_of(draft).state == "approved" and serve.messages == {}
    tim.connection.signed_out = True
    assert tim.delivery.pairing() is None


# -- the transport over the team connection -------------------------------------------------

class Answers:
    """A team connection whose JSON call answers from a script."""

    def __init__(self, *answers, bump=False):
        self.saved = {"server_id": SERVER, "me": {"github_id": TIM}}
        self.signed_out, self.generation = False, 1
        self.answers, self.bump, self.calls = list(answers), bump, []

    async def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.bump:
            self.generation += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def transport(connection) -> TeamTransport:
    return TeamTransport(connection, Pairing(SERVER, TIM, 1))


def _sync_payload():
    return wire.SyncPage(epoch="e", entries=[], scan_complete=True,
                         server_time=fakes.T0).model_dump(mode="json")


def test_an_answer_from_a_previous_pairing_is_discarded():
    conn = Answers((200, _sync_payload()), bump=True)
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(transport(conn).sync(UUID(int=1), None, 5))
    assert e.value.code == "transport"
    conn = Answers((401, {"error": {"code": "session_revoked"}}), bump=True)
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(transport(conn).sync(UUID(int=1), None, 5))
    assert (e.value.code, e.value.session_ended) == ("session_revoked", True)
    conn = Answers()
    conn.generation = 2
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(transport(conn).sync(UUID(int=1), None, 5))
    assert e.value.session_ended and conn.calls == [], "nothing is asked for an old pairing"


def test_the_transports_answers_map_onto_the_contracts_codes():
    latest = {"revision": 4}
    conn = Answers((409, {"error": {"code": "revision_conflict", "latest": latest}}),
                   (429, {"error": {"code": "rate_limited", "retry_after": 7}}),
                   (503, {"database": "unreachable"}),
                   TeamError("down", unreachable=True),
                   (200, {"not": "a page"}),
                   (200, _sync_payload()))
    t = transport(conn)
    for code, check in (("revision_conflict", lambda e: e.latest == latest),
                        ("rate_limited", lambda e: e.retry_after == 7.0 and e.retryable),
                        ("dependency_unavailable", lambda e: e.retryable),
                        ("transport", lambda e: e.retryable),
                        ("transport", lambda e: e.status is None)):
        with pytest.raises(ports.TransportError) as e:
            asyncio.run(t.sync(UUID(int=1), "c/1", 5))
        assert e.value.code == code and check(e.value)
    assert asyncio.run(t.sync(UUID(int=1), "c/1", 5)).scan_complete
    assert conn.calls[-1][1] == (f"/api/v1/messages/sync?node_id={UUID(int=1)}"
                                 "&limit=5&continuation=c%2F1")


def test_the_loop_backs_off_with_jitter_and_stops_cleanly(tim):
    assert tim.delivery.next_delay() == tim.delivery.interval
    tim.delivery.failures = 4
    assert tim.delivery.interval < tim.delivery.next_delay() <= 60

    async def run_and_stop():
        tim.delivery.start()
        tim.delivery.wake()
        await asyncio.sleep(0.05)
        await tim.delivery.stop()
    asyncio.run(run_and_stop())
    assert tim.delivery._task is None
