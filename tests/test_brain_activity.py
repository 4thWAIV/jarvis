"""The brain activity feed (jarvis/brain_activity.py): every turn the brain
takes, from start through its streaming output to its end, every tool call,
every message to and from agents and people, each attention digest and each
thing spoken, as events of counts and timings with no words, kept in memory
to a fixed size and read at /api/brain/activity."""

import asyncio
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from jarvis import brain_activity
from jarvis.brain_activity import ActivityFeed
from tests.fixtures.waiting import until_async

FAKE = Path(__file__).parent / "fixtures" / "fake_brain.py"
SECRET = "words-that-must-never-leave"


class Mono:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _events(since, kind):
    return [e for e in brain_activity.feed.since(since)["events"] if e["kind"] == kind]


def _no_words(since, *secrets):
    text = json.dumps(brain_activity.feed.since(since))
    for s in secrets:
        assert s not in text, f"{s!r} reached the feed"
    for e in brain_activity.feed.since(since)["events"]:
        assert set(e) <= brain_activity.FIELDS


# ── the record ────────────────────────────────────────────────────────────

def test_the_record_is_bounded_and_says_what_it_still_holds():
    feed = ActivityFeed(keep=3)
    for i in range(5):
        feed.record(brain_activity.DIGEST, "started", id=str(i))
    body = feed.since(0)
    assert [e["seq"] for e in body["events"]] == [3, 4, 5]
    assert body["seq"] == 5 and body["oldest"] == 3
    assert [e["seq"] for e in feed.since(4)["events"]] == [5]


def test_a_field_that_could_carry_words_is_refused():
    feed = ActivityFeed()
    with pytest.raises(ValueError):
        feed.record(brain_activity.TURN, "started", text=SECRET)
    assert feed.since()["events"] == []


def test_every_event_names_the_brain_in_force():
    feed = ActivityFeed()
    feed.provider = "openai"
    feed.record(brain_activity.SESSION_MESSAGE, "arrived", counterpart="hammer")
    (e,) = feed.since()["events"]
    assert (e["provider"], e["counterpart"], e["kind"], e["stage"]) == \
        ("openai", "hammer", "session_message", "arrived")


def test_streaming_progress_is_at_most_one_event_a_quarter_second():
    mono = Mono()
    feed = ActivityFeed(monotonic=mono)
    turn = feed.turn_started("user", provider="anthropic")
    feed.turn_output(turn, 5)                         # first_output
    for _ in range(100):                              # a burst within 0.1 s
        mono.now += 0.001
        feed.turn_output(turn, 3)
    mono.now += 0.2
    feed.turn_output(turn, 3)                         # 0.3 s since first: one event
    mono.now += 0.1
    feed.turn_output(turn, 3)                         # 0.1 s since: none
    mono.now += 0.2
    feed.turn_output(turn, 6)                         # 0.3 s since: one
    feed.turn_ended(turn, brain_activity.COMPLETED)
    feed.turn_ended(turn, brain_activity.FAILED)      # a second ending is ignored
    stages = [e["stage"] for e in feed.since()["events"]]
    assert stages == ["started", "first_output", "streaming", "streaming", "completed"]
    streaming = [e for e in feed.since()["events"] if e["stage"] == "streaming"]
    assert streaming[0]["chars"] == 5 + 300 + 3
    assert streaming[0]["chars_per_sec"] == pytest.approx(303 / 0.3, rel=0.01)
    assert streaming[1]["chars_per_sec"] == pytest.approx(9 / 0.3, rel=0.01)
    ended = feed.since()["events"][-1]
    assert ended["chars"] == 317 and ended["duration_ms"] == pytest.approx(600, abs=1)
    assert {e["provider"] for e in feed.since()["events"]} == {"anthropic"}


def test_a_turn_ends_only_with_an_ending():
    feed = ActivityFeed()
    turn = feed.turn_started("user")
    with pytest.raises(ValueError):
        feed.turn_ended(turn, "streaming")


# ── turns, through the real brain over the fake CLI ───────────────────────

def _config(tmp_path, **kw):
    from jarvis import brain
    return brain.BrainConfig(home=tmp_path / "jarvis",
                             claude_path=f"{sys.executable} {FAKE}",
                             turn_timeout=kw.pop("turn_timeout", 5.0),
                             warmup_timeout=kw.pop("warmup_timeout", 10.0), **kw)


def _turn_events(since, origin):
    starts = [e for e in _events(since, "turn")
              if e["stage"] == "started" and e["origin"] == origin]
    assert len(starts) == 1, starts
    return [e for e in _events(since, "turn") if e["id"] == starts[0]["id"]]


@pytest.mark.asyncio
async def test_a_turn_is_started_streams_and_completes(tmp_path):
    from jarvis import brain
    b = brain.Brain(_config(tmp_path))
    before = brain_activity.feed.since()["seq"]
    await b.start()
    try:
        assert [e["stage"] for e in _turn_events(before, "warmup")][-1] == "completed"
        since = brain_activity.feed.since()["seq"]
        r = await b.turn(f"hello {SECRET}", origin="watcher")
        assert r.stop_reason == "result"
        events = _turn_events(since, "watcher")
        assert [e["stage"] for e in events][0:2] == ["started", "first_output"]
        assert events[-1]["stage"] == "completed"
        assert events[-1]["chars"] == len(r.text)
        assert {e["provider"] for e in events} == {b.provider.name}
        _no_words(since, SECRET, "Echo")
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_a_turn_that_errors_or_times_out_ends_failed(tmp_path):
    from jarvis import brain
    b = brain.Brain(_config(tmp_path, turn_timeout=0.5))
    await b.start()
    try:
        since = brain_activity.feed.since()["seq"]
        r = await b.turn("SLOW:3 never")
        assert r.stop_reason == "timeout"
        assert [e["stage"] for e in _turn_events(since, "user")][-1] == "failed"
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_a_turn_cancelled_by_its_caller_ends_cancelled(tmp_path):
    from jarvis import brain
    b = brain.Brain(_config(tmp_path))
    await b.start()
    try:
        since = brain_activity.feed.since()["seq"]
        task = asyncio.create_task(b.turn("SLOW:2 wait"))
        assert await until_async(lambda: any(
            e["stage"] == "started" and e["origin"] == "user" for e in _events(since, "turn"))), \
            "the turn never started"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [e["stage"] for e in _turn_events(since, "user")] == ["started", "cancelled"]
    finally:
        await b.stop()


# ── communications ────────────────────────────────────────────────────────

@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import run_store
    run_store.init_db()
    return run_store


def test_a_message_to_an_agent_is_each_status_it_takes(store):
    since = brain_activity.feed.since()["seq"]
    mid = store.create_message("sess-1", "anthropic", "hammer", "proj", SECRET)
    store.update_message(mid, status=store.MessageStatus.SENT, sent_at=1.0)
    store.update_message(mid, status=store.MessageStatus.SENT, sent_at=2.0)   # no change
    store.update_message(mid, delivered_at=3.0)                               # no status
    store.update_message(mid, status="delivered")
    store.update_message(mid, status="answered", reply=SECRET + " reply")
    events = _events(since, "agent_message")
    assert [e["stage"] for e in events] == ["staged", "sent", "delivered", "answered"]
    assert {(e["id"], e["session_id"], e["counterpart"]) for e in events} == \
        {(str(mid), "sess-1", "hammer")}
    _no_words(since, SECRET)


def test_a_session_message_arriving_names_the_agent_only(store):
    since = brain_activity.feed.since()["seq"]
    sid = store.create_session_message("proj", "anvil", SECRET + " subject", SECRET)
    (e,) = _events(since, "session_message")
    assert (e["stage"], e["counterpart"], e["id"]) == ("arrived", "anvil", str(sid))
    _no_words(since, SECRET)


def _transfer(direction):
    from jarvis import messaging_ports as ports
    mid = UUID(int=7 if direction == "incoming" else 8)
    return ports.TransferEvent(
        event_id=ports.transfer_event_id(mid, direction), message_id=mid,
        thread_id=mid, direction=direction, owner_github_id=1,
        source_node_id=UUID(int=2), sender_github_id=1, recipient_github_id=3,
        occurred_at=datetime.now(timezone.utc), replay=False)


def test_a_person_message_sent_and_arriving_is_a_person_never_who():
    from jarvis.transfer_presentation import ServePresentation
    frames = []
    p = ServePresentation(transport=None, node_id=UUID(int=2), send=frames.append,
                          clock=lambda: datetime.now(timezone.utc))
    since = brain_activity.feed.since()["seq"]
    p.present(_transfer("outgoing"))
    p.present(_transfer("outgoing"))                  # a repeat is drawn once
    p.present(_transfer("incoming"))
    events = _events(since, "person_message")
    assert [e["stage"] for e in events] == ["sent", "arrived"]
    assert {e["counterpart"] for e in events} == {"a person"}
    assert len(frames) == 2


def test_a_person_message_the_team_server_took_is_accepted(tmp_path):
    from tests.test_messaging_client import Jarvis, SERVER, TIM, MATT
    from tests.fixtures import messaging_fakes as fakes
    from jarvis import messaging_wire as wire
    clock = fakes.FixedClock()
    serve = fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=clock,
                            faults=wire.ArmedFaults())
    tim = Jarvis(serve, TIM, 1, tmp_path, clock)
    since = brain_activity.feed.since()["seq"]
    draft = tim.approve(MATT, SECRET)
    tim.tick()
    events = _events(since, "person_message")
    assert [(e["stage"], e["id"]) for e in events] == [("accepted", str(draft.message_id))]
    _no_words(since, SECRET)


def test_a_digest_is_started_then_its_outcome():
    from jarvis.attention_store import AttentionStore, PLAYED, QUEUED
    store = AttentionStore(node_id=lambda: "node-a")
    store.record("run", "r1", "succeeded", untrusted={"project": SECRET})
    since = brain_activity.feed.since()["seq"]
    digest = store.begin_digest(store.items())
    store.finish_digest(digest, QUEUED, text=SECRET)
    store.finish_digest(digest, PLAYED)
    events = _events(since, "digest")
    assert [(e["stage"], e["id"]) for e in events] == \
        [("started", str(digest)), ("queued", str(digest)), ("played", str(digest))]
    _no_words(since, SECRET)


@pytest.mark.asyncio
async def test_speech_starts_when_it_sounds_and_ends_with_its_outcome():
    from tests.test_speech import Harness
    h = Harness()
    await h.sched.start()
    try:
        since = brain_activity.feed.since()["seq"]
        u = h.sched.begin_turn()
        h.sched.feed(u, f"{SECRET} one. Two.")
        await h.sched.end_turn(u)
        assert await until_async(lambda: u.outcomes()[0] == "queued"), u.outcomes()
        assert _events(since, "speech") == [], "sent, but nothing has sounded yet"
        h.sched.playback_started(u.id, 0)
        h.sched.playback_started(u.id, 0)
        await h.ack_all()
        assert await until_async(lambda: len(_events(since, "speech")) == 2), _events(since, "speech")
        events = _events(since, "speech")
        assert [(e["stage"], e["label"]) for e in events] == [("started", "reply"),
                                                               ("ended", "reply")]
        assert events[1]["outcome"] == "played" and events[1]["duration_ms"] >= 0
        _no_words(since, SECRET)

        since = brain_activity.feed.since()["seq"]
        v = await h.sched.say("Alpha one. Beta two. Gamma three.")
        assert await until_async(lambda: v.outcomes()[0] == "queued"), v.outcomes()
        h.sched.playback_started(v.id, 0)
        await h.sched.barge_in("test")
        assert await until_async(lambda: len(_events(since, "speech")) == 2), _events(since, "speech")
        assert [e.get("outcome") for e in _events(since, "speech")] == [None, "interrupted"]

        since = brain_activity.feed.since()["seq"]
        w = await h.sched.say("Delta one. Echo two. Foxtrot three.")
        assert await until_async(lambda: w.outcomes()[0] == "queued"), w.outcomes()
        h.sched.playback_started(w.id, 0)
        assert await h.sched.skip_reading(w.id)
        assert [e.get("outcome") for e in _events(since, "speech")] == [None, "skipped"]
    finally:
        await h.sched.stop()


# ── the route ─────────────────────────────────────────────────────────────

def test_the_route_reads_every_kind_and_the_tool_route_only_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    from jarvis import server as server_module, tool_activity
    importlib.reload(server_module)
    with TestClient(server_module.app, headers={"Origin": "http://localhost:5173"}) as client:
        start = brain_activity.feed.since()["seq"]
        call = tool_activity.activity.start("recall")
        tool_activity.activity.end(call, tool_activity.COMPLETED)
        brain_activity.note(brain_activity.SESSION_MESSAGE, "arrived", counterpart="anvil")
        body = client.get(f"/api/brain/activity?since={start}").json()
        assert [(e["kind"], e["stage"]) for e in body["events"]] == [
            ("tool", "started"), ("tool", "completed"), ("session_message", "arrived")]
        assert body["generation"] == brain_activity.feed.generation
        tools = client.get(f"/api/tools/activity?since={start}").json()
        assert [e["stage"] for e in tools["events"]] == ["started", "completed"]
        assert tools["generation"] == body["generation"]
        assert client.get("/api/brain/activity?since=-1").status_code == 422
