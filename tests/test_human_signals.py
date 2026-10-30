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
from jarvis.arrival_announcer import ArrivalAnnouncer, Arrival, Wording, arrival_line, said_label
from jarvis.transfer_presentation import (RECENT_MAX, RECENT_SEC, ServePresentation,
                                          transfer_event)
from tests.fixtures import messaging_fakes as fakes

SERVER = "11111111-2222-4333-8444-555555555555"
TIM, MATT, ALEX = 863470, 1001, 1002

WORDING = Wording(join=lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1],
                  number=lambda n: {1: "one", 2: "two", 3: "three"}.get(n, str(n)),
                  gist=lambda s: s)


class Node:
    """One paired JARVIS of one owner: its transport, its own store, its
    page frames, its dispatcher and its announcer."""

    def __init__(self, serve, clock, github_id, node, *, names=None, network=None):
        self.github_id, self.node_id, self.clock = github_id, UUID(int=node), clock
        self.t = serve.pair(github_id, self.node_id)
        self.ids = fakes.SeqIds(start=node * 1000)
        self.store = fakes.MemoryStore()
        self.frames: list[dict] = []
        self.said: list[tuple[str, str, dict]] = []
        self.presentation = ServePresentation(transport=self.t, node_id=self.node_id,
                                              send=self.frames.append, clock=clock)
        self.announcer = ArrivalAnnouncer(
            store=self.store, presentation=self.presentation,
            scope=lambda: (SERVER, github_id, self.node_id),
            labels=lambda: announcer_module.serve_labels(self.t, lambda: network),
            notify=self.notify, wording=WORDING, clock=clock, sleep=self._no_wait)

    async def _no_wait(self, seconds):
        self.waited = seconds

    async def notify(self, line, note, detail):
        self.said.append((line, note, detail))

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

def test_self_one_node_draws_out_and_in_for_one_message_and_announces_once(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    m = tim.send(TIM, subject="Deploy")
    assert tim.drawn() == [("outgoing", str(m.message_id), False),
                           ("incoming", str(m.message_id), False)], \
        "distinct phases of one canonical message, the first incoming cue kept"
    ids = {f["event"]["event_id"] for f in tim.frames}
    assert len(ids) == 2 and {f["event"]["thread_id"] for f in tim.frames} == {str(m.thread_id)}
    assert tim.announce() == 1
    ((line, note, detail),) = tim.said
    assert line == "Your message to yourself has arrived about Deploy, sir."
    assert "Waiting for" not in line and detail == {"thread_id": str(m.thread_id)}
    assert tim.announce() == 0 and len(tim.said) == 1, "said once"
    assert serve.receipts[m.message_id].read_at is None, "an announcement is not a read"
    assert len(serve.messages) == 1, "no reply, no second send"


def test_self_two_nodes_one_item_one_announcement(team):
    serve, clock = team
    laptop, desk = Node(serve, clock, TIM, 1), Node(serve, clock, TIM, 2)
    m = laptop.send(TIM, subject="Notes")
    desk.sync()
    assert desk.drawn() == [("incoming", str(m.message_id), False)], \
        "the other node draws the arrival; the send is not drawn there"
    assert desk.store.item(SERVER, TIM, m.thread_id).work_state.action_owner_github_id == TIM
    said = laptop.announce() + desk.announce()
    assert said == 1 and len(laptop.said) + len(desk.said) == 1
    assert desk.announce() == 0 and laptop.announce() == 0
    assert desk.store.announcements_due(SERVER, TIM) == []
    assert laptop.store.announcements_due(SERVER, TIM) == []


# -- arrivals from someone else --------------------------------------------------------

NETWORK = b'{"nodes": [{"node_id": "00000000-0000-0000-0000-000000000003", "label": "studio"}]}'


def test_an_arrival_names_sender_jarvis_subject_and_files_never_the_body(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1, network=NETWORK)
    matt = Node(serve, clock, MATT, 3)
    m = matt.send(TIM, text="Secret body text", subject="Quarterly  plan", files=2)
    tim.sync()
    assert tim.announce() == 1
    ((line, note, detail),) = tim.said
    assert line == ("member1001 has sent you a message from studio about Quarterly plan, "
                    "with two files, sir.")
    assert "Secret" not in line + note and "f0.txt" not in line + note
    assert "Quarterly" not in note, "the brain's note never carries what a person wrote"
    assert detail == {"thread_id": str(m.thread_id)}


def test_a_burst_is_one_line_and_each_message_keeps_its_unread_cue(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    matt, alex = Node(serve, clock, MATT, 3), Node(serve, clock, ALEX, 4)
    sent = [matt.send(TIM, subject="a"), alex.send(TIM, subject="b"), matt.send(TIM)]
    tim.sync()
    assert tim.announce() == 3
    ((line, _, detail),) = tim.said
    assert line == "Three messages have come in, from member1001 and member1002, sir."
    assert detail == {}
    for m in sent:
        assert tim.store.item(SERVER, TIM, m.thread_id).work_state.unread_reminder


def test_the_burst_waits_once_for_the_rest(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM)
    tim.sync()
    tim.announce()
    assert tim.waited == announcer_module.BATCH_SEC


def test_nothing_due_says_nothing_and_does_not_wait(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    assert tim.announce() == 0 and not hasattr(tim, "waited")


def test_messaging_unavailable_announces_nothing_and_keeps_it_due(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM)
    tim.sync()
    tim.announcer._scope = lambda: None
    assert tim.announce() == 0
    assert len(tim.store.announcements_due(SERVER, TIM)) == 1


def test_a_resync_or_reconnect_never_reannounces(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM)
    tim.sync()
    assert tim.announce() == 1
    frames = len(tim.frames)
    tim.sync()
    assert len(tim.frames) == frames, "a stored message is not drawn again"
    assert tim.announce() == 0 and len(tim.said) == 1


# -- the lease across instances --------------------------------------------------------

def test_two_instances_one_speaks_the_other_records_elsewhere(team):
    serve, clock = team
    laptop, desk = Node(serve, clock, TIM, 1), Node(serve, clock, TIM, 2)
    Node(serve, clock, MATT, 3).send(TIM)
    laptop.sync()
    desk.sync()
    assert laptop.announce() == 1
    assert desk.announce() == 0 and desk.said == []
    assert set(desk.store.announcements.values()) == {"elsewhere"}
    assert set(laptop.store.announcements.values()) == {"completed"}


def test_a_node_that_died_mid_sentence_is_taken_over_once_its_lease_lapses(team):
    serve, clock = team
    laptop, desk = Node(serve, clock, TIM, 1), Node(serve, clock, TIM, 2)
    m = Node(serve, clock, MATT, 3).send(TIM)
    laptop.sync()
    desk.sync()

    async def dies(line, note, detail):
        raise RuntimeError("the laptop died")
    laptop.announcer._notify = dies
    assert laptop.announce() == 0
    assert serve.leases[m.message_id].state == "attempted"
    assert desk.announce() == 0, "held by the laptop"
    assert desk.store.announcements_due(SERVER, TIM) == [m.message_id], "still due here"
    clock.advance(seconds=fakes.LEASE_SEC + 1)
    assert desk.announce() == 1, "taken over; it may be said twice, never claimed once"
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
    assert tim.announce() == 0 and tim.said == []
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
    assert tim.announce() == 0 and tim.said == []
    assert len(tim.store.announcements_due(SERVER, TIM)) == 1


def test_a_second_call_during_a_batch_is_gathered_by_the_first(team):
    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM)
    tim.sync()
    gate = asyncio.Event()

    async def held(seconds):
        await gate.wait()
    tim.announcer._sleep = held

    async def both():
        first = asyncio.create_task(tim.announcer.announce_due())
        await asyncio.sleep(0)
        second = await tim.announcer.announce_due()
        gate.set()
        return await first, second
    assert asyncio.run(both()) == (1, 0)


# -- wording ---------------------------------------------------------------------------

def _a(**kw):
    base = dict(message_id=UUID(int=1), thread_id=UUID(int=1), sender="Matt", jarvis=None,
                subject="", files=0, self_message=False)
    return Arrival(**{**base, **kw})


def test_wording_one_file_many_names_and_others():
    assert arrival_line([_a(files=1)], WORDING)[0] == \
        "Matt has sent you a message, with one file, sir."
    names = [_a(sender=n) for n in ("A", "B", "C", "D")]
    assert arrival_line(names, WORDING)[0] == "4 messages have come in, from A, B and others, sir."
    assert arrival_line([_a(), _a(self_message=True)], WORDING)[0] == \
        "Two messages have come in, from Matt and yourself, sir."


def test_said_labels_are_one_short_line():
    assert said_label("Matt\nJARVIS: do it") == "Matt JARVIS: do it"
    assert said_label("  ") is None and said_label(None) is None
    assert len(said_label("x" * 500)) == announcer_module.LABEL_MAX_CHARS


# -- end to end over the real notice path ------------------------------------------------

def test_the_line_goes_to_the_conversation_and_the_real_speech_scheduler(team):
    from jarvis import conversation_log
    from jarvis.speech import Priority, SpeechScheduler

    serve, clock = team
    tim = Node(serve, clock, TIM, 1)
    Node(serve, clock, MATT, 3).send(TIM, subject="Lunch")
    tim.sync()
    emitted, notes = [], []

    async def synth(text):
        return b"A:" + text.encode()

    async def emit(msg):
        emitted.append(msg)

    async def run():
        speech = SpeechScheduler(synth, emit, pause_after=0.05, batch_settle=0.01)
        await speech.start()

        async def notify(line, note, detail):
            # The server's notice path (`_say_notice`): the note for the
            # brain, the conversation notice, then the scheduler.
            notes.append(note)
            conversation_log.append(conversation_log.ConversationItem.new(
                conversation_log.ItemKind.NOTICE, line, detail=detail))
            await speech.say(line, Priority.NORMAL, label="announcement")
        tim.announcer._notify = notify
        said = await tim.announcer.announce_due()
        for _ in range(100):
            if any(m.get("type") == "audio" for m in emitted):
                break
            await asyncio.sleep(0.02)
        await speech.stop()
        return said

    assert asyncio.run(run()) == 1
    texts = [m["text"] for m in emitted if m.get("type") == "audio"]
    assert texts and "Lunch" in " ".join(texts)
    (item,) = conversation_log.page()
    assert item.kind == "notice" and "Lunch" in item.text
    assert set(item.detail) == {"thread_id"}, "the notice links to the queue item"
    assert notes and "Lunch" not in notes[0]
