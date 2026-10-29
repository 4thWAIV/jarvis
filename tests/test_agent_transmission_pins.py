"""Today's agent transmission, pinned end to end before it is shared.

Human messaging reuses the way a message to an agent is accepted, staged,
held for the turn, offered a cancel window and settled
(human-messaging-build-instructions.md, "Existing agent-transmission flow is
the implementation reference"). The send orchestration is extracted from
`server.py` into `transmission.py` for that; these tests were written against
the code BEFORE the extraction and must pass unchanged after it. Each drives
the server's own entry points (the brain's tools, the capture box's buttons
and the staged-send drain), with the provider faked at `_deliver`:

    voice read-back      read_capture heard in full -> steer_session stages
                         -> the drain sends exactly the capture's text
    changed capture      a capture changed after its read-back is refused
    GUI acceptance       Accept counts as read back; Send accepts and sends
    cancellation         the cancel window, and Delete while staged
    turn deferral        a send staged mid-turn waits for the turn to end
    queue                a busy session queues it; priority skips the queue
    outcome              sent / not live / failed / raised, each said and
                         recorded once, and only "sent" draws the ray
"""

import asyncio

import pytest

from jarvis import session_steer
from jarvis import session_watch as sw

SID = "11111111-2222-3333-4444-555555555555"


class _Utterance:
    was_cancelled = False


class _Speech:
    """The scheduler as the transmission path uses it: read-back, the cancel
    window and the spoken outcome. `heard` decides whether a read-back
    reached its end."""

    def __init__(self, cancelled=False, heard=True):
        self.said, self.read, self.windows = [], [], 0
        self.cancelled, self.heard = cancelled, heard

    async def say(self, text, *a, **k):
        self.said.append(text)
        return _Utterance()

    def read_aloud(self, text, capture):
        self.read.append((capture, text))
        return _Utterance()

    async def wait_for(self, utt, timeout=60.0):
        return self.heard

    async def open_cancel_window(self, *a, **k):
        self.windows += 1
        return self.cancelled


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from jarvis import server as server_module
    importlib.reload(server_module)
    server_module.run_store.init_db()
    monkeypatch.setattr(server_module, "_inbound_accepted", lambda: True)
    monkeypatch.setattr(server_module, "_notes_for_brain", [])

    async def quiet(msg):
        pass

    monkeypatch.setattr(server_module, "_voice_emit", quiet)
    return server_module


def _guard(state="idle", steerable=True):
    return sw.SessionState(session_id=SID, cwd="/p/guard", project="guard", state=state,
                           voice_name="guard", steerable=steerable, socket_path="/x.sock",
                           pids=[1], primary_pid=1, between_turns=state == "idle")


@pytest.fixture
def wire(server, monkeypatch):
    """The session the capture is for, the provider faked at `_deliver`, and
    the voice page's hints. `wire.outcome` is what the provider answers."""

    class Wire:
        outcome = session_steer.SENT
        sent: list = []
        hints: list = []
        speech = _Speech()

    w = Wire()
    w.sent, w.hints = [], []

    def deliver(item, text):
        w.sent.append((item.session_id, text, item.priority))
        if isinstance(w.outcome, Exception):
            raise w.outcome
        return w.outcome

    monkeypatch.setattr(server, "_deliver", deliver)
    monkeypatch.setattr(server, "_send_to_session_clients", w.hints.append)
    monkeypatch.setattr(server, "_resolve_or_explain", lambda n: (_guard(), None, None))
    monkeypatch.setattr(server, "_snapshot_or_empty", lambda: sw.Snapshot(sessions=[_guard()]))
    monkeypatch.setattr(server, "speech", w.speech)
    return w


def _capture(server, text, to="guard"):
    server.tool_capture({"text": text})
    number = max(server._captures)
    server._captures[number]["to"] = to
    return number


def _messages(server):
    return server.run_store.list_messages()


def _drew(wire):
    return [h for h in wire.hints if h.get("type") == "agent_message"]


# --- voice read-back ---------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_read_back_then_steer_sends_exactly_the_heard_text_once(server, wire):
    n = _capture(server, "Use Postgres, not SQLite.")
    out = await server.tool_read_capture({"capture": n})
    assert out.startswith("Read back in full")
    assert wire.speech.read == [(n, "Use Postgres, not SQLite.")]

    staged = await server.tool_steer_session({"name": "guard", "capture": n})
    (m,) = _messages(server)
    assert f"staged as message {m.id}" in staged
    assert m.status == "staged" and m.capture_id == n, "the record is written when staged"
    assert wire.sent == [], "staging sends nothing"
    assert server._captures[n]["staged"] is True

    await server._perform_staged_steers()

    assert wire.sent == [(SID, server.STEER_SIGNATURE + "Use Postgres, not SQLite.", False)]
    assert wire.speech.windows == 1, "one cancel window, after the turn"
    assert wire.speech.said == [], "not read out again, and sent is not delivered"
    m = server.run_store.get_message(m.id)
    assert (m.status, m.body) == ("sent", "Use Postgres, not SQLite.")
    assert m.sent_at is not None
    assert n not in server._captures, "sending closes the capture"
    assert _drew(wire) == [{"type": "agent_message", "session_id": SID}]
    await server._perform_staged_steers()
    assert len(wire.sent) == 1, "performed exactly once"


@pytest.mark.asyncio
async def test_a_read_back_cut_short_leaves_it_unsendable(server, wire):
    wire.speech.heard = False
    n = _capture(server, "Ship it.")
    out = await server.tool_read_capture({"capture": n})
    assert "cannot be sent until it is read back again" in out
    refused = await server.tool_steer_session({"name": "guard", "capture": n})
    assert "has not been read back to him in full" in refused
    assert _messages(server) == [] and server._staged_steers == []


# --- changed capture ---------------------------------------------------------

@pytest.mark.asyncio
async def test_a_capture_changed_after_its_read_back_is_refused(server, wire):
    n = _capture(server, "Use Postgres.")
    await server.tool_read_capture({"capture": n})
    server.tool_capture({"text": "Use MySQL.", "capture": n})
    assert server._captures[n]["read"] is None

    refused = await server.tool_steer_session({"name": "guard", "capture": n})

    assert "has not been read back to him in full since it last changed" in refused
    assert _messages(server) == [] and server._staged_steers == [] and wire.sent == []
    assert [s["outcome"] for s in server.run_store.list_steers()] == ["no_capture"]


@pytest.mark.asyncio
async def test_the_same_text_restated_keeps_its_read_back(server, wire):
    n = _capture(server, "Use Postgres.")
    await server.tool_read_capture({"capture": n})
    server.tool_capture({"text": "Use Postgres.", "capture": n})
    assert server._captures[n]["read"] == "Use Postgres."


# --- GUI acceptance ----------------------------------------------------------

@pytest.mark.asyncio
async def test_accept_on_screen_counts_as_read_back_for_the_voice_path(server, wire):
    n = _capture(server, "Add a health check.")
    assert await server.api_capture_accept(n) == {"capture": n, "accepted": True}
    assert server._captures[n]["read"] == "Add a health check."
    assert "accepted capture" in server._with_notes_for_brain("")

    await server.tool_steer_session({"name": "guard", "capture": n})
    await server._perform_staged_steers()

    assert wire.sent == [(SID, server.STEER_SIGNATURE + "Add a health check.", False)]
    assert wire.speech.read == [], "accepted on screen: never read aloud"


@pytest.mark.asyncio
async def test_send_on_screen_accepts_stages_and_sends_now_outside_a_turn(server, wire):
    n = _capture(server, "Add a health check.")
    assert server._turn_utt is None

    r = await server.api_capture_send(n)
    for _ in range(100):
        if wire.sent:
            break
        await asyncio.sleep(0.01)

    (m,) = _messages(server)
    assert r == {"capture": n, "message": m.id}
    assert wire.sent == [(SID, server.STEER_SIGNATURE + "Add a health check.", False)]
    assert server.run_store.get_message(m.id).status == "sent"
    assert f"as message {m.id}" in server._with_notes_for_brain("")


@pytest.mark.asyncio
async def test_send_on_screen_refuses_with_409_and_stages_nothing(server, wire):
    from fastapi import HTTPException
    n = _capture(server, "Who is this for?", to="")
    with pytest.raises(HTTPException) as e:
        await server.api_capture_send(n)
    assert e.value.status_code == 409 and "isn't addressed to anyone" in e.value.detail

    server._captures[n]["to"] = "guard"
    server._captures[n]["staged"] = True
    with pytest.raises(HTTPException) as e:
        await server.api_capture_send(n)
    assert e.value.status_code == 409 and "already being sent" in e.value.detail
    assert _messages(server) == [] and wire.sent == []


# --- cancellation ------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_in_the_cancel_window_cancels_and_reopens_the_capture(server, wire):
    wire.speech.cancelled = True
    n = _capture(server, "Drop the table.")
    await server.api_capture_accept(n)
    await server.tool_steer_session({"name": "guard", "capture": n})
    await server._perform_staged_steers()

    (m,) = _messages(server)
    assert m.status == "cancelled" and wire.sent == []
    assert wire.speech.said == ["Cancelled, sir."]
    assert n in server._captures and server._captures[n]["staged"] is False, \
        "not sent: the capture is his to deal with again"
    assert _drew(wire) == []


@pytest.mark.asyncio
async def test_delete_while_staged_cancels_the_send(server, wire):
    n = _capture(server, "Drop the table.")
    await server.api_capture_accept(n)
    await server.tool_steer_session({"name": "guard", "capture": n})

    await server.api_capture_delete(n)
    await server._perform_staged_steers()

    (m,) = _messages(server)
    assert m.status == "cancelled" and wire.sent == [] and server._staged_steers == []


# --- turn deferral -----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_send_pressed_during_a_turn_waits_for_the_turn_to_end(server, wire, monkeypatch):
    monkeypatch.setattr(server, "_turn_utt", object())
    n = _capture(server, "Add a health check.")

    await server.api_capture_send(n)
    for _ in range(5):
        await asyncio.sleep(0)

    (m,) = _messages(server)
    assert m.status == "staged" and wire.sent == [], "held while the turn is under way"
    assert len(server._staged_steers) == 1

    await server._perform_staged_steers()            # what the turn's end does
    assert server.run_store.get_message(m.id).status == "sent" and len(wire.sent) == 1


@pytest.mark.asyncio
async def test_the_tool_only_stages_and_says_nothing(server, wire):
    n = _capture(server, "Add a health check.")
    await server.api_capture_accept(n)
    await server.tool_steer_session({"name": "guard", "capture": n})
    assert wire.sent == [] and wire.speech.said == [] and wire.speech.windows == 0


# --- queue -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_session_mid_turn_gets_it_queued_on_record(server, wire, monkeypatch):
    monkeypatch.setattr(server, "_snapshot_or_empty",
                        lambda: sw.Snapshot(sessions=[_guard(state="working")]))
    n = _capture(server, "Add a health check.")
    await server.api_capture_accept(n)
    await server.tool_steer_session({"name": "guard", "capture": n})
    await server._perform_staged_steers()

    (m,) = _messages(server)
    assert m.status == "queued" and wire.sent == []
    assert wire.speech.said == ["guard is in the middle of a turn, sir. Your message waits "
                                "its turn and goes as soon as it is free."]
    assert n not in server._captures, "queued counts as gone: the capture closes"
    assert _drew(wire) == []


@pytest.mark.asyncio
async def test_priority_skips_the_queue(server, wire, monkeypatch):
    monkeypatch.setattr(server, "_snapshot_or_empty",
                        lambda: sw.Snapshot(sessions=[_guard(state="working")]))
    n = _capture(server, "Stop everything.")
    await server.api_capture_accept(n)
    await server.tool_steer_session({"name": "guard", "capture": n, "priority": True})
    await server._perform_staged_steers()

    (m,) = _messages(server)
    assert m.status == "sent"
    assert wire.sent == [(SID, server.STEER_SIGNATURE + "Stop everything.", True)]


# --- outcome -----------------------------------------------------------------

async def _send_one(server, text="Add a health check."):
    n = _capture(server, text)
    await server.api_capture_accept(n)
    await server.tool_steer_session({"name": "guard", "capture": n})
    await server._perform_staged_steers()
    (m,) = _messages(server)
    return n, server.run_store.get_message(m.id)


@pytest.mark.asyncio
async def test_not_live_is_failed_said_and_draws_nothing(server, wire):
    wire.outcome = session_steer.NOT_LIVE
    n, m = await _send_one(server)
    assert (m.status, m.failure) == ("failed", "not_live")
    assert wire.speech.said == ["guard didn't answer its socket, sir — it may have just exited."]
    assert _drew(wire) == [] and n not in server._captures


@pytest.mark.asyncio
async def test_a_refused_send_is_failed_with_its_reason(server, wire):
    wire.outcome = session_steer.failed("connection reset")
    n, m = await _send_one(server)
    assert m.status == "failed" and m.failure == session_steer.failed("connection reset")
    assert wire.speech.said == ["I couldn't deliver that to guard, sir."]


@pytest.mark.asyncio
async def test_a_provider_that_raises_never_leaves_the_record_staged(server, wire):
    wire.outcome = RuntimeError("boom")
    n, m = await _send_one(server)
    assert (m.status, m.failure) == ("failed", "failed")
    assert n not in server._captures


@pytest.mark.asyncio
async def test_sent_to_a_session_that_will_ask_first_says_so(server, wire, monkeypatch):
    monkeypatch.setattr(server, "_inbound_accepted", lambda: False)
    _n, m = await _send_one(server)
    assert m.status == "sent"
    assert len(wire.speech.said) == 1
    assert wire.speech.said[0].startswith("Passed to guard, sir. It'll ask you to approve it first")


@pytest.mark.asyncio
async def test_no_voice_at_send_time_fails_without_sending(server, wire, monkeypatch):
    n = _capture(server, "Add a health check.")
    await server.api_capture_accept(n)
    await server.tool_steer_session({"name": "guard", "capture": n})
    monkeypatch.setattr(server, "speech", None)
    await server._perform_staged_steers()
    (m,) = _messages(server)
    assert (m.status, m.failure) == ("failed", "no_voice") and wire.sent == []
    assert server._captures[n]["staged"] is False


@pytest.mark.asyncio
async def test_no_voice_at_staging_refuses_and_records_it_failed(server, wire, monkeypatch):
    n = _capture(server, "Add a health check.")
    await server.api_capture_accept(n)
    monkeypatch.setattr(server, "speech", None)
    refused = await server.tool_steer_session({"name": "guard", "capture": n})
    assert "won't send it to guard unheard" in refused
    (m,) = _messages(server)
    assert (m.status, m.failure) == ("failed", "no_voice") and server._staged_steers == []


@pytest.mark.asyncio
async def test_a_session_that_cannot_take_it_is_refused_and_recorded(server, wire, monkeypatch):
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_guard(steerable=False), None, None))
    n = _capture(server, "Add a health check.")
    await server.api_capture_accept(n)
    refused = await server.tool_steer_session({"name": "guard", "capture": n})
    assert "has no inbox socket" in refused
    (m,) = _messages(server)
    assert (m.status, m.failure) == ("failed", "not_steerable")
    assert server._captures[n].get("staged") is not True


@pytest.mark.asyncio
async def test_two_staged_in_one_turn_go_in_order(server, wire):
    for text in ("first", "second"):
        n = _capture(server, text)
        await server.api_capture_accept(n)
        await server.tool_steer_session({"name": "guard", "capture": n, "priority": True})
    await server._perform_staged_steers()
    assert [t for _, t, _ in wire.sent] == [server.STEER_SIGNATURE + "first",
                                            server.STEER_SIGNATURE + "second"]
