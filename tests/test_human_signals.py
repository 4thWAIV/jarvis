"""Human transfers on the page and arrival announcements (signals stream):
`jarvis/transfer_presentation.py` and `jarvis/arrival_announcer.py`.

The team server is the contract's in-memory one (`FakeServe`, with the real
lease rule `apply_announcement`); each node keeps its own `MemoryStore`.
The notice path in the end-to-end cases is the real one underneath: the
conversation record (SQLite in the test's own data directory) and the real
speech scheduler, with only the synthesizer and the page faked.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest

from jarvis import arrival_announcer as announcer_module
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.arrival_announcer import ArrivalAnnouncer, Arrival, said_label
from jarvis.transfer_presentation import (RECENT_MAX, RECENT_SEC, ServePresentation,
                                          transfer_event)
from tests.fixtures import messaging_fakes as fakes

SERVER = "11111111-2222-4333-8444-555555555555"
TIM, MATT, ALEX = 863470, 1001, 1002



class Node:
    """One paired JARVIS of one owner: its transport, its own store, its
    page frames, its dispatcher and its announcer."""

    def __init__(self, serve, clock, github_id, node, *, names=None, network=None):
        self.github_id, self.node_id, self.clock = github_id, UUID(int=node), clock
        self.t = serve.pair(github_id, self.node_id)
        self.ids = fakes.SeqIds(start=node * 1000)
        self.store = fakes.MemoryStore()
        self.frames: list[dict] = []
        self.attended: list[tuple[Arrival, bool]] = []
        self.engaged = True
        self.presentation = ServePresentation(transport=self.t, node_id=self.node_id,
                                              send=self.frames.append, clock=clock)
        self.announcer = ArrivalAnnouncer(
            store=self.store, presentation=self.presentation,
            scope=lambda: (SERVER, github_id, self.node_id),
            labels=lambda: announcer_module.serve_labels(self.t, lambda: network),
            attend=self.attend, engaged=lambda: self.engaged, clock=clock)

    def attend(self, arrival, audible):
        self.attended.append((arrival, audible))

    def spoken(self):
        """The arrivals this node took as its own to speak of."""
        return [a for a, audible in self.attended if audible]

    def shown(self):
        return [a for a, audible in self.attended if not audible]

    def send(self, to, text="Body words that are never said.", subject="", files=0):
        mid = self.ids.new_id()
        refs = [self._file(mid, i) for i in range(files)]
        content = wire.MessageContent(
            message_id=mid, thread_id=mid, recipient_github_id=to, content_revision=1,
            subject=wire.normalize_subject(subject), text=wire.normalize_text(text),
            attachments=refs)
        request = wire.SendRequest(**content.model_dump(), source_node_id=self.node_id,
                                   idempotency_key=self.ids.new_id(),
                                   content_digest=wire.content_digest(
                                       content, server_id=SERVER, sender_github_id=self.github_id))
        result = asyncio.run(self.t.send(request))
        # What delivery does with the answer: one transaction, and the
        # outgoing transfer drawn on its successful hand-over.
        incoming = self.store.record_accepted(SERVER, self.github_id, result)
        self.presentation.present(transfer_event(self.github_id, result.message, "outgoing",
                                                 self.clock()))
        if incoming:
            self.presentation.present(transfer_event(self.github_id, result.message,
                                                     "incoming", self.clock()))
        return result.message

    def _file(self, mid, i):
        import hashlib
        data = f"file {i}".encode()
        ref = wire.AttachmentRef(attachment_id=self.ids.new_id(), filename=f"f{i}.txt",
                                 media_type="text/plain", size_bytes=len(data),
                                 sha256=hashlib.sha256(data).hexdigest())
        asyncio.run(self.t.create_attachment(wire.AttachmentCreate(
            attachment_id=ref.attachment_id, message_id=mid, size_bytes=ref.size_bytes,
            sha256=ref.sha256)))

        async def chunks():
            yield data
        asyncio.run(self.t.upload_attachment(ref.attachment_id, chunks()))
        asyncio.run(self.t.finalize_attachment(ref.attachment_id))
        return ref

    def sync(self):
        """A full scan, stored page by page; each newly held incoming is drawn."""
        continuation = None
        while True:
            page = asyncio.run(self.t.sync(self.node_id, continuation, wire.SYNC_PAGE_DEFAULT))
            for mid in self.store.store_page(SERVER, self.github_id, self.node_id, page):
                local = self.store.message(SERVER, self.github_id, mid)
                self.presentation.present(transfer_event(self.github_id, local.message,
                                                         "incoming", self.clock()))
            if page.scan_complete:
                return
            continuation = page.continuation

    def announce(self):
        return asyncio.run(self.announcer.announce_due())

    def drawn(self):
        return [(f["event"]["direction"], f["event"]["message_id"], f["event"]["replay"])
                for f in self.frames]


@pytest.fixture
def team():
    clock = fakes.FixedClock()
    serve = fakes.FakeServe(server_id=SERVER, members={TIM, MATT, ALEX}, clock=clock)
    return serve, clock


# -- the presentation event -------------------------------------------------------

def test_every_frame_is_the_contract_s_human_transfer_frame(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    tim.send(MATT, subject="Budget")
    (frame,) = tim.frames
    parsed = ports.HumanTransferFrame.model_validate(frame)
    assert frame["type"] == ports.TRANSFER_FRAME == "human_transfer"
    assert parsed.event.event_id.startswith("human:") and parsed.event.replay is False
    assert "Budget" not in str(frame) and "Body" not in str(frame), "a frame carries no content"


def test_an_outgoing_event_at_hand_over_is_the_same_event_as_after_acceptance(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    m = tim.send(MATT)
    envelope = wire.SendRequest(
        schema_version=1, message_id=m.message_id, thread_id=m.thread_id,
        in_reply_to_message_id=None, related_message_ids=[], recipient_github_id=MATT,
        content_revision=1, subject="", text="x", attachments=[],
        source_node_id=tim.node_id, idempotency_key=UUID(int=9), content_digest="sha256:" + "0" * 64)
    at_hand_over = transfer_event(TIM, envelope, "outgoing", clock())
    accepted = transfer_event(TIM, m, "outgoing", clock())
    assert at_hand_over == accepted


def test_a_repeat_of_the_same_event_is_drawn_once(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    m = tim.send(MATT)
    tim.presentation.present(transfer_event(TIM, m, "outgoing", clock()))
    assert len(tim.frames) == 1


def test_a_page_that_reconnects_gets_recent_transfers_as_replay_and_nothing_older(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    first = tim.send(MATT)
    clock.advance(seconds=RECENT_SEC + 1)
    second = tim.send(MATT)
    replay = tim.presentation.replay_frames()
    assert [(f["event"]["message_id"], f["event"]["replay"]) for f in replay] == \
        [(str(second.message_id), True)], "only what is still on screen, never animated"
    assert ports.HumanTransferFrame.model_validate(replay[0]).event.replay
    clock.advance(seconds=RECENT_SEC + 1)
    assert tim.presentation.replay_frames() == []
    assert first.message_id != second.message_id


def test_the_recent_list_is_bounded(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    m = tim.send(MATT)
    for i in range(RECENT_MAX + 10):
        tim.presentation.present(transfer_event(TIM, m.model_copy(update={
            "message_id": UUID(int=10_000 + i)}), "outgoing", clock()))
    assert len(tim.presentation.replay_frames()) == RECENT_MAX


def test_a_page_that_cannot_be_told_loses_a_ray_never_raises(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)

    def broken(frame):
        raise ConnectionError("gone")
    tim.presentation._send = broken
    tim.send(MATT)                                        # no exception


# -- self-send ------------------------------------------------------------------------

def test_self_one_node_draws_out_and_in_for_one_message_and_attends_once(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    m = tim.send(TIM, subject="Deploy")
    assert tim.drawn() == [("outgoing", str(m.message_id), False),
                           ("incoming", str(m.message_id), False)], \
        "distinct phases of one canonical message, the first incoming cue kept"
    ids = {f["event"]["event_id"] for f in tim.frames}
    assert len(ids) == 2 and {f["event"]["thread_id"] for f in tim.frames} == {str(m.thread_id)}
    assert tim.announce() == 1
    (shown,), (spoken,) = tim.shown(), tim.spoken()
    assert shown == spoken and spoken.self_message and spoken.subject == "Deploy"
    assert spoken.attention()["facts"] == {"thread_id": str(m.thread_id), "files": 0,
                                           "self_message": True}
    assert tim.announce() == 0 and len(tim.attended) == 2, "attended once"
    assert serve.receipts[m.message_id].read_at is None, "attending is not a read"
    assert len(serve.messages) == 1, "no reply, no second send"


def test_self_two_nodes_one_item_one_node_speaks_of_it(team):
    serve, clock = team
    laptop, desk = Node(serve, clock, TIM, 1), Node(serve, clock, TIM, 2)
    m = laptop.send(TIM, subject="Notes")
    desk.sync()
    assert desk.drawn() == [("incoming", str(m.message_id), False)], \
        "the other node draws the arrival; the send is not drawn there"
    assert desk.store.item(SERVER, TIM, m.thread_id).work_state.action_owner_github_id == TIM
    claimed = laptop.announce() + desk.announce()
    assert claimed == 1 and len(laptop.spoken()) + len(desk.spoken()) == 1
    assert desk.announce() == 0 and laptop.announce() == 0
    assert desk.store.announcements_due(SERVER, TIM) == []
    assert laptop.store.announcements_due(SERVER, TIM) == []


# -- arrivals from someone else --------------------------------------------------------

NETWORK = b'{"nodes": [{"node_id": "00000000-0000-0000-0000-000000000003", "label": "studio"}]}'


def test_an_arrival_carries_sender_jarvis_subject_and_files_never_the_body(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1, network=NETWORK)
    matt = Node(serve, clock, MATT, 3)
    m = matt.send(TIM, text="Secret body text", subject="Quarterly  plan", files=2)
    tim.sync()
    assert tim.announce() == 1
    (arrival,) = tim.spoken()
    recorded = arrival.attention()
    assert recorded["untrusted"] == {"sender": "member1001", "from_jarvis": "studio",
                                     "subject": "Quarterly plan"}
    assert recorded["facts"] == {"thread_id": str(m.thread_id), "files": 2,
                                 "self_message": False}
    assert "Secret" not in str(recorded) and "f0.txt" not in str(recorded)


def test_a_burst_is_one_item_each_and_each_message_keeps_its_unread_cue(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    matt, alex = Node(serve, clock, MATT, 3), Node(serve, clock, ALEX, 4)
    sent = [matt.send(TIM, subject="a"), alex.send(TIM, subject="b"), matt.send(TIM)]
    tim.sync()
    assert tim.announce() == 3
    assert {a.message_id for a in tim.spoken()} == {m.message_id for m in sent}
    for m in sent:
        assert tim.store.item(SERVER, TIM, m.thread_id).work_state.unread_reminder


def test_nothing_due_attends_to_nothing(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    assert tim.announce() == 0 and tim.attended == []


def test_messaging_unavailable_attends_to_nothing_and_keeps_it_due(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM)
    tim.sync()
    tim.announcer._scope = lambda: None
    assert tim.announce() == 0 and tim.attended == []
    assert len(tim.store.announcements_due(SERVER, TIM)) == 1


def test_a_resync_or_reconnect_never_attends_again(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM)
    tim.sync()
    assert tim.announce() == 1
    frames = len(tim.frames)
    tim.sync()
    assert len(tim.frames) == frames, "a stored message is not drawn again"
    assert tim.announce() == 0 and len(tim.attended) == 2


def test_away_or_unknown_shows_it_and_holds_no_lease(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    m = Node(serve, clock, MATT, 3).send(TIM, subject="Lunch")
    tim.sync()
    tim.engaged = False
    assert tim.announce() == 0
    assert [a.message_id for a in tim.shown()] == [m.message_id], "visible at once"
    assert tim.spoken() == []
    assert m.message_id not in serve.leases, "no lease held through an away period"
    assert tim.store.announcements_due(SERVER, TIM) == [m.message_id]
    assert tim.announce() == 0 and len(tim.shown()) == 1, "shown once"
    tim.engaged = True
    assert tim.announce() == 1 and len(tim.spoken()) == 1


# -- the lease across instances --------------------------------------------------------

def test_two_instances_one_speaks_of_it_the_other_records_elsewhere(team):
    serve, clock = team
    laptop, desk = Node(serve, clock, TIM, 1), Node(serve, clock, TIM, 2)
    Node(serve, clock, MATT, 3).send(TIM)
    laptop.sync()
    desk.sync()
    assert laptop.announce() == 1
    assert desk.announce() == 0 and desk.spoken() == []
    assert len(desk.shown()) == 1, "the other node still shows it"
    assert set(desk.store.announcements.values()) == {"elsewhere"}
    assert set(laptop.store.announcements.values()) == {"completed"}


def test_a_node_that_died_mid_way_is_taken_over_once_its_lease_lapses(team):
    serve, clock = team
    laptop, desk = Node(serve, clock, TIM, 1), Node(serve, clock, TIM, 2)
    m = Node(serve, clock, MATT, 3).send(TIM)
    laptop.sync()
    desk.sync()

    def dies(arrival, audible):
        if audible:
            raise RuntimeError("the laptop died")
    laptop.announcer._attend = dies
    assert laptop.announce() == 0
    assert serve.leases[m.message_id].state == "attempted"
    assert desk.announce() == 0, "held by the laptop"
    assert desk.store.announcements_due(SERVER, TIM) == [m.message_id], "still due here"
    clock.advance(seconds=fakes.LEASE_SEC + 1)
    assert desk.announce() == 1, "taken over; it may be spoken of twice, never claimed once"
    assert serve.leases[m.message_id].state == "completed"


def test_an_unreachable_team_server_keeps_it_due(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM)
    tim.sync()

    async def unreachable(*a, **k):
        raise ports.TransportError("transport")
    real = tim.t.announce
    tim.t.announce = unreachable
    assert tim.announce() == 0 and tim.spoken() == []
    assert len(tim.shown()) == 1, "shown while the lease cannot be had"
    assert len(tim.store.announcements_due(SERVER, TIM)) == 1
    tim.t.announce = real
    assert tim.announce() == 1


def test_a_refused_attempted_step_leaves_it_due(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM)
    tim.sync()

    async def refuse(event, token):
        raise ports.TransportError("internal", status=500)
    tim.presentation.attempted = refuse
    assert tim.announce() == 0 and tim.spoken() == []
    assert len(tim.store.announcements_due(SERVER, TIM)) == 1


def test_a_second_call_while_one_is_under_way_is_gathered_by_the_first(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM)
    tim.sync()
    gate = asyncio.Event()
    labels = tim.announcer._labels

    async def held():
        await gate.wait()
        return await labels()
    tim.announcer._labels = held

    async def both():
        first = asyncio.create_task(tim.announcer.announce_due())
        await asyncio.sleep(0)
        second = await tim.announcer.announce_due()
        gate.set()
        return await first, second
    assert asyncio.run(both()) == (1, 0)


def test_said_labels_are_one_short_line():
    assert said_label("Matt\nJARVIS: do it") == "Matt JARVIS: do it"
    assert said_label("  ") is None and said_label(None) is None
    assert len(said_label("x" * 500)) == announcer_module.LABEL_MAX_CHARS


# -- end to end into the attention record ------------------------------------------------

def test_an_arrival_is_an_attention_item_and_nothing_is_said(team):
    from jarvis.attention_store import AttentionStore

    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    m = Node(serve, clock, MATT, 3).send(TIM, subject="Lunch")
    tim.sync()
    store = AttentionStore(node_id=lambda: str(tim.node_id))

    def attend(arrival, audible):
        store.record("arrival", str(arrival.message_id), "arrived", audible=audible,
                     **arrival.attention())
    tim.announcer._attend = attend
    assert tim.announce() == 1
    (item,) = store.items()
    assert item.kind == "arrival" and item.source_id == str(m.message_id)
    assert item.audible and item.revision == 1, "being allowed to speak of it is not news"
    assert item.untrusted["subject"] == "Lunch" and "Lunch" not in str(item.facts)


