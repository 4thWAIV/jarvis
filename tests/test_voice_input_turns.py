"""What the voice socket does with his stop settings' turn frames: a stop
under Discard throws away what was being gathered from the page, so nothing
he was saying is sent; without it, the gathered words go as before."""

from tests.test_voice_ws import _drain_until, client  # noqa: F401  (the fixture)


def test_a_discarded_turn_is_never_sent(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "delete the whole branch", "isFinal": True,
                      "clientId": "page-a", "captureEpoch": 3})
        ws.send_json({"type": "turn_discard", "clientId": "page-a"})
        # Typing sends what was gathered first: after the discard there is none.
        ws.send_json({"type": "transcript", "text": "typed words", "isFinal": True, "typed": True})
        _drain_until(ws, lambda m: m.get("text") == "Echo: typed words.", limit=30)
        assert server.brain_instance.turns == [("typed words", "user")]


def test_typed_words_are_sent_alone_and_the_voice_words_go_as_their_own_turn(client):
    """Typed Send is his whole message: the voice words being gathered are
    not merged into it, nor lost; they are committed on their own hold."""
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "what time is it", "isFinal": True,
                      "clientId": "page-a", "captureEpoch": 3})
        ws.send_json({"type": "transcript", "text": "typed words", "isFinal": True, "typed": True})
        _drain_until(ws, lambda m: m.get("text") == "Echo: what time is it.", limit=40)
        assert [t for t, _ in server.brain_instance.turns] == ["typed words", "what time is it"]


def test_a_stale_epoch_s_words_are_never_gathered(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "turn_discard", "clientId": "page-a", "captureEpoch": 5})
        ws.send_json({"type": "transcript", "text": "late words", "isFinal": True,
                      "clientId": "page-a", "captureEpoch": 4})
        ws.send_json({"type": "transcript", "text": "fresh words", "isFinal": True,
                      "clientId": "page-a", "captureEpoch": 5})
        seen = _drain_until(ws, lambda m: m.get("text") == "Echo: fresh words.", limit=30)
        assert server.brain_instance.turns == [("fresh words", "user")]
        gathered = next(m for m in seen if m.get("type") == "turn" and m["state"] == "gathering")
        assert gathered["text"] == "fresh words", "the late words were never gathered"
        assert {"type": "turn", "state": "committed", "turnId": gathered["turnId"]} in seen


def test_a_closed_socket_sends_nothing_it_was_gathering(client):
    import time
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "half a", "isFinal": True,
                      "clientId": "page-a", "captureEpoch": 1})
    time.sleep(2.0)                                 # longer than the hold
    assert server.brain_instance.turns == []


# ── speech before the reply is heard ─────────────────────────────────────

import asyncio  # noqa: E402

import pytest  # noqa: E402

from tests.test_speech import Harness  # noqa: E402


@pytest.mark.asyncio
async def test_speech_after_the_commit_stops_the_reply_s_reading_before_it_is_heard(monkeypatch):
    """He goes on while the brain or synthesis is still working: the reply
    is not read (its text stays), and nothing is undone."""
    from jarvis import server
    h = Harness(end_of_turn=0.5)
    await h.sched.start()
    try:
        monkeypatch.setattr(server, "speech", h.sched)
        await h.sched.user_final("book a table")          # he spoke a moment ago
        reply = h.sched.begin_turn()
        h.sched.feed(reply, "Booked for seven, sir.")
        await h.sched.end_turn(reply)
        await asyncio.sleep(0.1)
        assert h.audio() == [], "the reply waits for his quiet"
        await server._take_back_the_floor(1)
        await asyncio.sleep(0.8)
        assert h.audio() == []
        assert {"type": "reading_skipped", "utt": reply.id} in h.msgs
    finally:
        await h.sched.stop()


@pytest.mark.asyncio
async def test_a_reply_already_being_heard_is_left_to_barge_in(monkeypatch):
    from jarvis import server
    h = Harness()
    await h.sched.start()
    try:
        monkeypatch.setattr(server, "speech", h.sched)
        reply = h.sched.begin_turn()
        h.sched.feed(reply, "It is sunny, sir.")
        await h.sched.end_turn(reply)
        await asyncio.sleep(0.2)
        assert h.audio(), "playing"
        await server._take_back_the_floor(1)
        assert {"type": "reading_skipped", "utt": reply.id} not in h.msgs
    finally:
        await h.sched.stop()


# ── what the page is told it has said, and a turn that cannot finish ─────

def test_the_page_is_told_only_the_words_accepted_as_his(client):
    """The gathering frame carries what the server gathered: words heard
    as JARVIS's own echo are never in it, so a dropped socket cannot turn
    them into his draft."""
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        server.speech._last_spoken = None
        ws.send_json({"type": "transcript", "text": "check the build", "isFinal": True,
                      "clientId": "page-a", "captureEpoch": 1})
        seen = _drain_until(ws, lambda m: m.get("type") == "turn" and m.get("state") == "gathering")
        assert seen[-1]["text"] == "check the build"


@pytest.fixture
def stalls(client, monkeypatch):
    """A recogniser that never catches up with page-a."""
    c, server = client
    monkeypatch.setattr(server, "_capture_coverage", lambda page: (40, 10))
    return c, server


def _stall(ws):
    ws.receive_json(); ws.receive_json()
    ws.send_json({"type": "transcript", "text": "transfer the", "isFinal": True,
                  "clientId": "page-a", "captureEpoch": 1})
    return _drain_until(ws, lambda m: m.get("type") == "turn" and m.get("state") == "stalled",
                        limit=40)[-1]


def test_a_stalled_turn_kept_as_a_draft_goes_back_to_the_page_unsent(stalls):
    c, server = stalls
    with c.websocket_connect("/ws/voice") as ws:
        stalled = _stall(ws)
        assert stalled["text"] == "transfer the"
        ws.send_json({"type": "turn_keep_draft"})
        draft = _drain_until(ws, lambda m: m.get("type") == "turn" and m.get("state") == "draft")
        assert draft[-1]["text"] == "transfer the" and draft[-1]["turnId"] == stalled["turnId"]
    assert server.brain_instance.turns == []


def test_send_now_on_a_stalled_turn_sends_it_as_heard(stalls):
    c, server = stalls
    with c.websocket_connect("/ws/voice") as ws:
        _stall(ws)
        ws.send_json({"type": "turn_send_now"})
        _drain_until(ws, lambda m: m.get("text") == "Echo: transfer the.", limit=40)
    assert server.brain_instance.turns == [("transfer the", "user")]


def test_the_brain_s_voice_turn_is_the_page_s_keep_as_draft(stalls):
    c, server = stalls
    with c.websocket_connect("/ws/voice") as ws:
        _stall(ws)
        said = c.portal.call(server.tool_voice_turn, {"action": "keep_as_draft"})
        assert "nothing was sent" in said
        draft = _drain_until(ws, lambda m: m.get("type") == "turn" and m.get("state") == "draft")
        assert draft[-1]["text"] == "transfer the"
        assert "Nothing was done" in c.portal.call(server.tool_voice_turn,
                                                   {"action": "retry"})
        assert "Nothing was done" in c.portal.call(server.tool_voice_turn, {"action": "explode"})
    assert server.brain_instance.turns == []
