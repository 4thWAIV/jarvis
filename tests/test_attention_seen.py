"""Seen is his act: his click, his mark, or hearing it read in full.

An update the brain reads to him on a turn he drove (an agent's answer with
read_message_reply, a session's message with read_session_message, a
person's message with mark_people_message_read) is seen once the reply of
that turn has been heard in full, at the revision that was read, and never
on the tool call alone: a reply cut short, a turn he did not drive, or a
newer revision leaves it pending."""

import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.test_session_steer import Harness, _Client, wired  # noqa: F401  (a fixture)


SID = "s-guard"


@pytest.fixture
def record(wired, monkeypatch):
    from jarvis import attention_store
    store = attention_store.AttentionStore()
    monkeypatch.setattr(wired, "attention_record", store)
    return store


def _answered(server, reply="Postgres it is."):
    rs = server.run_store
    mid = rs.create_message(SID, "anthropic", "guard", "guard", server.STEER_SIGNATURE + "go")
    now = time.time()
    rs.update_message(mid, status="answered", sent_at=now - 60, delivered_at=now - 59,
                      started_at=now - 59, answered_at=now - 30, reply=reply)
    return mid


class _ReadingBrain:
    """A turn that reads one thing to him with a reading tool, then says it."""

    ready = True
    failed = False
    rotation_pending = False

    def __init__(self, read, origin="user"):
        self._read = read
        self.current_origin = origin

    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        on_tool()
        result = self._read()
        if asyncio.iscoroutine(result):
            await result
        on_delta("Guard says Postgres it is.")
        from jarvis import brain
        return brain.TurnResult(origin=origin, text="Guard says Postgres it is.",
                                stop_reason="result")


async def _turn(server, monkeypatch, brain, skip=None):
    """One turn of his, its reply played to the page; a chunk `skip` names
    is not played and he presses Skip to end on it."""
    h = Harness()
    skipped = []

    def skipping(text):
        if skip is None or not skip(text):
            return False
        if not skipped:
            skipped.append(asyncio.ensure_future(h.sched.skip_reading()))
        return True
    client = _Client(h, skip=skipping)
    monkeypatch.setattr(server, "speech", h.sched)
    monkeypatch.setattr(server, "brain_instance", brain)

    async def emit(msg):
        pass
    monkeypatch.setattr(server, "_voice_emit", emit)
    await h.sched.start()
    client.start()
    try:
        await asyncio.wait_for(server._handle_utterance("what did guard say?", heard=1),
                               timeout=15)
    finally:
        await client.stop()
        await h.sched.stop()
    if skip is not None:
        assert skipped, "the reply was skipped"


@pytest.mark.asyncio
async def test_an_answer_read_and_heard_in_full_is_seen(wired, record, monkeypatch):
    mid = _answered(wired)
    item = record.record("agent_message", str(mid), "answered", facts={"message_id": mid})
    await _turn(wired, monkeypatch,
                _ReadingBrain(lambda: wired.tool_read_message_reply({"message": mid})))
    after = record.get("agent_message", str(mid))
    assert after.state == "seen" and after.revision == item.revision
    assert wired._turn_reads is None, "the turn's reads end with the turn"


@pytest.mark.asyncio
async def test_a_reply_cut_short_leaves_it_pending(wired, record, monkeypatch):
    mid = _answered(wired)
    record.record("agent_message", str(mid), "answered", facts={"message_id": mid})
    await _turn(wired, monkeypatch,
                _ReadingBrain(lambda: wired.tool_read_message_reply({"message": mid})),
                skip=lambda text: text.startswith("Guard says"))
    assert record.get("agent_message", str(mid)).state == "pending", \
        "the tool call alone is not hearing it"


@pytest.mark.asyncio
async def test_a_session_message_read_and_heard_is_seen(wired, record, monkeypatch):
    rs = wired.run_store
    number = rs.create_session_message("guard", "guard", "Phase 2", "It is done.")
    record.record("session_message", str(number), "arrived", facts={"message_id": number})
    await _turn(wired, monkeypatch,
                _ReadingBrain(lambda: wired.tool_read_session_message({"message": number})))
    assert record.get("session_message", str(number)).state == "seen"


def test_only_a_turn_he_drove_and_only_a_pending_revision_is_noted(wired, record,
                                                                   monkeypatch):
    record.record("agent_message", "5", "answered")
    monkeypatch.setattr(wired, "_turn_reads", [])
    monkeypatch.setattr(wired, "brain_instance", SimpleNamespace(current_origin="system"))
    wired._read_to_him("agent_message", 5)
    assert wired._turn_reads == [], "not on a turn he drove"
    monkeypatch.setattr(wired, "brain_instance", SimpleNamespace(current_origin="user"))
    wired._read_to_him("agent_message", 5)
    wired._read_to_him("agent_message", 6)                  # no such update
    assert wired._turn_reads == [("agent_message", "5", 1)]


def test_a_newer_revision_than_the_one_read_stays_pending(wired, record):
    record.record("agent_message", "5", "undelivered")
    reads = [("agent_message", "5", 1)]
    record.record("agent_message", "5", "answered")         # news he has not heard
    utt = SimpleNamespace(chunks=["one"])
    wired._seen_once_heard(reads, utt, True)
    assert record.get("agent_message", "5").state == "pending"


def test_nothing_heard_or_nothing_said_marks_nothing(wired, record):
    record.record("agent_message", "5", "answered")
    reads = [("agent_message", "5", 1)]
    wired._seen_once_heard(reads, SimpleNamespace(chunks=["one"]), False)
    wired._seen_once_heard(reads, SimpleNamespace(chunks=[]), True)
    assert record.get("agent_message", "5").state == "pending"
    wired._seen_once_heard(reads, SimpleNamespace(chunks=["one"]), True)
    assert record.get("agent_message", "5").state == "seen"


@pytest.mark.asyncio
async def test_a_person_s_message_marked_read_is_noted(wired, record, monkeypatch):
    message_id = uuid4()
    record.record("arrival", str(message_id), "arrived")
    got = SimpleNamespace(direction="incoming", receipts=SimpleNamespace(read_at=None))

    async def call(work):
        return got, None
    monkeypatch.setattr(wired, "_messaging_call", call)
    monkeypatch.setattr(wired, "_turn_reads", [])
    monkeypatch.setattr(wired, "brain_instance", SimpleNamespace(current_origin="user"))
    out = await wired.tool_mark_people_message_read({"message": str(message_id)})
    assert out.startswith("Marked read")
    assert wired._turn_reads == [("arrival", str(message_id), 1)]
    assert record.get("arrival", str(message_id)).state == "pending", "not until heard"
