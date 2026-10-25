"""The capture box: everything sent to an agent is a capture of Tim's words,
made on his turn. Several can be open at once and the brain can merge them.
`read_capture` reads one back inside the brain's own turn, its chunks marked
for the page's capture box; the message tool takes the capture's number,
never free text, and only a capture read back in full and unchanged since.
The voice page sees "capturing", "reading", "transmitting", then nothing."""

import asyncio
import json

import pytest

from tests.test_session_steer import (FakeBrain, FakeSpeech, Harness, _Client,  # noqa: F401
                                      _fake_session_state, _wait_for_receipt,
                                      fake_session, wired)
import claude_provider


@pytest.fixture(autouse=True)
def _agents(wired, monkeypatch):
    """The agents a capture can be for: who it is to is settled when it is
    made, through the agent directory."""
    import agent_directory
    import session_watch as sw
    snap = sw.Snapshot(sessions=[
        sw.SessionState(session_id=sid, cwd=f"/p/{project}", project=project,
                        state="idle", voice_name=name, steerable=True,
                        socket_path=None, pids=[1], primary_pid=1)
        for sid, name, project in (("c", "chitauri", "chitauri"), ("h", "hammer", "hammer"),
                                   ("g", "Agent Guard", "agent-guard"))])
    monkeypatch.setattr(agent_directory, "_snapshot", lambda: snap)


@pytest.fixture
def emitted(wired, monkeypatch):
    sent = []

    async def emit(msg):
        sent.append(msg)

    monkeypatch.setattr(wired, "_voice_emit", emit)
    return sent


def _captures(sent):
    return [(m["id"], m["state"], m.get("text")) for m in sent if m.get("type") == "capture"]


def _read_back(server, number):
    """As `read_capture` leaves a capture it read in full."""
    server._captures[number]["read"] = server._captures[number]["text"]


def _steering(server, monkeypatch, path, speech=None):
    monkeypatch.setattr(server, "speech", speech or FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))


@pytest.mark.asyncio
async def test_a_capture_is_shown_as_capturing_numbered_and_addressed(wired, emitted, monkeypatch):
    monkeypatch.setattr(wired, "_turn_heard", 7)
    out = wired.tool_capture({"text": "  Use Postgres, not SQLite.  ", "to": " Agent\nGuard "})
    await asyncio.sleep(0)
    assert out.startswith("Capture 1 holds it.")
    (msg,) = [m for m in emitted if m["type"] == "capture"]
    assert msg == {"type": "capture", "id": 1, "state": "capturing",
                   "text": "Use Postgres, not SQLite.", "to": "Agent Guard", "heard": [7],
                   "accepted": False}
    assert wired.tool_capture({"text": "   "}) == "There was nothing to capture."


@pytest.mark.asyncio
async def test_several_captures_stay_open_and_a_restatement_keeps_its_number(
        wired, emitted, monkeypatch):
    monkeypatch.setattr(wired, "_turn_heard", 1)
    wired.tool_capture({"text": "first"})
    wired.tool_capture({"text": "second"})
    assert sorted(wired._captures) == [1, 2]

    monkeypatch.setattr(wired, "_turn_heard", 3)
    _read_back(wired, 1)
    wired.tool_capture({"text": "first, and more", "capture": 1, "to": "hammer"})
    one = wired._captures[1]
    assert (one["text"], one["to"], one["heard"]) == ("first, and more", "hammer", [1, 3])
    assert one["read"] is None, "a restated capture has to be heard again"
    assert "no open capture" in wired.tool_capture({"text": "x", "capture": 99}).lower()


@pytest.mark.asyncio
async def test_merging_folds_captures_into_the_first_and_closes_the_rest(wired, emitted, monkeypatch):
    monkeypatch.setattr(wired, "_turn_heard", 1)
    wired.tool_capture({"text": "use Postgres", "to": "hammer"})
    monkeypatch.setattr(wired, "_turn_heard", 2)
    wired.tool_capture({"text": "and drop SQLite"})
    _read_back(wired, 1)
    await asyncio.sleep(0)
    emitted.clear()

    out = wired.tool_merge_captures({"captures": [1, 2], "text": "Use Postgres and drop SQLite."})
    await asyncio.sleep(0)
    assert out.startswith("Merged into capture 1.")
    assert list(wired._captures) == [1]
    merged = wired._captures[1]
    assert (merged["text"], merged["to"], merged["heard"], merged["read"]) == \
        ("Use Postgres and drop SQLite.", "hammer", [1, 2], None)
    assert _captures(emitted) == [(2, None, None), (1, "capturing", "Use Postgres and drop SQLite.")]
    assert "at least two" in wired.tool_merge_captures({"captures": [1], "text": "x"})
    assert "not open" in wired.tool_merge_captures({"captures": [1, 5], "text": "x"})


@pytest.mark.asyncio
@pytest.mark.parametrize("number", [None, "seven", 999])
async def test_the_message_tool_refuses_anything_but_an_open_capture(
        wired, fake_session, monkeypatch, number):
    server, (path, received, _) = wired, fake_session
    _steering(server, monkeypatch, path)
    out = await server.tool_steer_session({"name": "chitauri", "capture": number})
    assert "capture" in out.lower() and server._staged_steers == []


@pytest.mark.asyncio
async def test_a_capture_not_read_back_since_it_changed_is_refused(wired, fake_session, monkeypatch, emitted):
    server, (path, received, _) = wired, fake_session
    _steering(server, monkeypatch, path)
    server.tool_capture({"text": "use Postgres"})
    out = await server.tool_steer_session({"name": "chitauri", "capture": 1})
    assert "read_capture" in out and server._staged_steers == []

    _read_back(server, 1)
    server.tool_capture({"text": "use Postgres today", "capture": 1})
    out = await server.tool_steer_session({"name": "chitauri", "capture": 1})
    assert "read_capture" in out and server._staged_steers == []


@pytest.mark.asyncio
async def test_free_text_is_not_a_way_in(wired, fake_session, monkeypatch):
    server, (path, received, _) = wired, fake_session
    _steering(server, monkeypatch, path)
    await server.tool_steer_session({"name": "chitauri", "prompt": "rm -rf the repo"})
    assert server._staged_steers == []


@pytest.mark.asyncio
async def test_a_send_uses_the_capture_s_text_shows_transmitting_then_clears(
        wired, fake_session, monkeypatch, emitted):
    server, (path, received, _) = wired, fake_session
    speech = FakeSpeech()
    _steering(server, monkeypatch, path, speech)

    server.tool_capture({"text": "Use Postgres, not SQLite."})
    _read_back(server, 1)
    await server.tool_steer_session({"name": "chitauri", "capture": 1})
    await server._perform_staged_steers()
    _wait_for_receipt(received)
    await asyncio.sleep(0)

    assert json.loads(received[0].strip())["message"]["content"] == \
        server.STEER_SIGNATURE + "Use Postgres, not SQLite."
    assert [s for _, s, _ in _captures(emitted)] == ["capturing", "transmitting", None]
    assert speech.said == [], "never read out a second time, and sent is not delivered"
    import run_store
    (m,) = run_store.list_messages()
    assert (m.capture_id, m.body, m.status) == (1, "Use Postgres, not SQLite.", "sent"), \
        "the message records the capture it came from"
    assert server._captures == {}, "sending closes the capture"
    _, problem = server._capture_to_send(1)
    assert problem, "a capture is sent once"


@pytest.mark.asyncio
async def test_a_cancelled_send_keeps_the_capture_open(wired, fake_session, monkeypatch, emitted):
    server, (path, received, _) = wired, fake_session
    _steering(server, monkeypatch, path, FakeSpeech(cancelled=True))
    server.tool_capture({"text": "drop it"})
    _read_back(server, 1)
    await server.tool_steer_session({"name": "chitauri", "capture": 1})
    await server._perform_staged_steers()
    await asyncio.sleep(0)
    assert 1 in server._captures and received == []
    assert [s for _, s, _ in _captures(emitted)] == ["capturing"]


class _ReadingBrain:
    """A brain whose turn captures Tim's words, reads them back with
    read_capture, then asks whether to send."""

    ready = True
    failed = False
    current_origin = "user"
    rotation_pending = False

    def __init__(self, server, text):
        self._server, self._text = server, text
        self.read_result = None

    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        on_delta("Let me capture that. ")          # narration: binned at the tool
        on_tool()
        self._server.tool_capture({"text": self._text, "to": "chitauri"})
        on_tool()
        self.read_result = await self._server.tool_read_capture({"capture": 1})
        on_delta("Shall I send it, sir?")
        import brain
        return brain.TurnResult(origin=origin, text="Shall I send it, sir?",
                                stop_reason="result")


async def _read_turn(server, monkeypatch, emitted, skip=None):
    h = Harness()
    client = _Client(h, skip=skip)
    brain = _ReadingBrain(server, "Use Postgres. Drop SQLite by Friday.")
    monkeypatch.setattr(server, "speech", h.sched)
    monkeypatch.setattr(server, "brain_instance", brain)
    monkeypatch.setattr(server, "READBACK_TIMEOUT", 2.0)
    await h.sched.start()
    client.start()
    try:
        await asyncio.wait_for(server._handle_utterance("tell chitauri", heard=4), timeout=15)
    finally:
        await client.stop()
        await h.sched.stop()
    return h, brain


@pytest.mark.asyncio
async def test_read_capture_reads_it_inside_the_turn_then_the_brain_asks(wired, monkeypatch, emitted):
    h, brain = await _read_turn(wired, monkeypatch, emitted)
    audio = [m for m in h.msgs if m["type"] == "audio"]
    assert [(m["text"], m.get("capture")) for m in audio] == [
        ("Use Postgres.", 1), ("Drop SQLite by Friday.", 1), ("Shall I send it, sir?", None)]
    assert len({m["utt"] for m in audio}) == 1, "one utterance: the brain's own turn"
    assert brain.read_result.startswith("Read back in full.")
    assert wired._captures[1]["read"] == wired._captures[1]["text"]
    assert wired._captures[1]["heard"] == [4]
    assert [s for _, s, _ in _captures(emitted)] == ["capturing", "reading", "capturing"]


@pytest.mark.asyncio
async def test_a_read_back_not_heard_to_the_end_leaves_it_unsendable(wired, monkeypatch, emitted):
    _h, brain = await _read_turn(wired, monkeypatch, emitted,
                                 skip=lambda text: text.startswith("Drop SQLite"))
    assert brain.read_result.startswith("Interrupted")
    assert wired._captures[1]["read"] is None
    _, problem = wired._capture_to_send(1)
    assert "read_capture" in problem



class _TwoTurnBrain(_ReadingBrain):
    """The first turn is still running when Tim says the next thing; the
    second captures his words and reads them back. One turn at a time, as
    brain.Brain's own lock makes it."""

    def __init__(self, server, text):
        super().__init__(server, text)
        self.release = asyncio.Event()
        self._lock = asyncio.Lock()

    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        async with self._lock:
            return await self._turn(text, origin, on_delta, on_tool)

    async def _turn(self, text, origin, on_delta, on_tool):
        if text.endswith("first"):
            await self.release.wait()
            on_delta("Done, sir.")
            import brain
            return brain.TurnResult(origin=origin, text="Done, sir.", stop_reason="result")
        return await super().turn(text, origin, on_delta, on_tool)


@pytest.mark.asyncio
async def test_a_turn_heard_while_another_runs_still_reads_its_capture_back(
        wired, monkeypatch, emitted):
    h = Harness()
    client = _Client(h)
    brain = _TwoTurnBrain(wired, "Use Postgres.")
    monkeypatch.setattr(wired, "speech", h.sched)
    monkeypatch.setattr(wired, "brain_instance", brain)
    monkeypatch.setattr(wired, "READBACK_TIMEOUT", 2.0)
    await h.sched.start()
    client.start()
    try:
        first = asyncio.create_task(wired._handle_utterance("first", heard=1))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(wired._handle_utterance("tell chitauri", heard=2))
        await asyncio.sleep(0.05)
        brain.release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=15)
    finally:
        await client.stop()
        await h.sched.stop()
    assert brain.read_result.startswith("Read back in full."), brain.read_result
    assert wired._captures[1]["read"] == "Use Postgres."
    assert wired._captures[1]["heard"] == [2], "the capture names the turn it was made on"
    audio = [m for m in h.msgs if m["type"] == "audio"]
    reply = [m["utt"] for m in audio if m["text"] in ("Use Postgres.", "Shall I send it, sir?")]
    assert len(reply) == 2 and len(set(reply)) == 1, "read inside the second turn's own reply"


@pytest.mark.asyncio
async def test_read_capture_outside_a_turn_reads_it_on_its_own(wired, monkeypatch, emitted):
    h = Harness()
    client = _Client(h)
    monkeypatch.setattr(wired, "speech", h.sched)
    monkeypatch.setattr(wired, "READBACK_TIMEOUT", 2.0)
    await h.sched.start()
    client.start()
    try:
        wired.tool_capture({"text": "Use Postgres. Drop SQLite.", "to": "chitauri"})
        out = await asyncio.wait_for(wired.tool_read_capture({"capture": 1}), timeout=10)
    finally:
        await client.stop()
        await h.sched.stop()
    assert out.startswith("Read back in full."), out
    assert wired._captures[1]["read"] == wired._captures[1]["text"]
    assert [(m["text"], m.get("capture")) for m in h.msgs if m["type"] == "audio"] == [
        ("Use Postgres.", 1), ("Drop SQLite.", 1)]
    assert [s for _, s, _ in _captures(emitted)] == ["capturing", "reading", "capturing"]

def test_the_capture_tools_are_acting_tools_offered_to_the_brain(wired):
    import jarvis_mcp
    specs = {t["name"]: t for t in jarvis_mcp.TOOL_SPECS}
    for name in ("capture", "read_capture", "merge_captures", "clear_capture"):
        assert name in specs and name in wired.ACTING_TOOLS, name
    assert "list_captures" in specs and "list_captures" in wired.TOOL_HANDLERS
    assert set(specs["capture"]["inputSchema"]["properties"]) == {"text", "to", "capture"}
    steer = specs["steer_session"]["inputSchema"]
    assert steer["required"] == ["name", "capture"] and "prompt" not in steer["properties"]
    assert jarvis_mcp.TIMEOUT_FOR["read_capture"] > wired.READBACK_TIMEOUT


@pytest.mark.asyncio
async def test_a_page_that_connects_gets_the_open_captures(wired, emitted):
    from fastapi.testclient import TestClient
    assert TestClient(wired.app).get("/api/capture").json() == {"captures": []}
    wired.tool_capture({"text": "Use Postgres."})
    wired.tool_capture({"text": "Ship it.", "to": "hammer"})
    body = TestClient(wired.app).get("/api/capture").json()["captures"]
    assert [(c["id"], c["state"], c["text"], c["to"]) for c in body] == [
        (1, "capturing", "Use Postgres.", ""), (2, "capturing", "Ship it.", "hammer")]


@pytest.mark.parametrize("msg, heard", [
    ({"type": "transcript", "id": 12}, 12), ({"type": "transcript"}, None),
    ({"id": "12"}, None), ({"id": True}, None), ({"id": 1.5}, None)])
def test_a_transcript_s_id_is_taken_only_as_a_whole_number(wired, msg, heard):
    assert wired._transcript_id(msg) == heard


@pytest.mark.asyncio
async def test_several_open_captures_are_each_sent_on_their_own_by_number(wired, fake_session, monkeypatch, emitted):
    """Several captures can be open; each is a message of its own. Sending one
    leaves the others open, and they go one at a time."""
    server, (path, received, _) = wired, fake_session
    _steering(server, monkeypatch, path)
    server.tool_capture({"text": "Use Postgres."})
    server.tool_capture({"text": "Add a health check."})
    out = server.tool_capture({"text": "Write the release notes."})
    assert "Open captures: 1, 2, 3." in out and "on its own" in out

    _read_back(server, 2)
    out = await server.tool_steer_session({"name": "chitauri", "capture": 2})
    assert "staged" in out
    assert [s.capture_id for s in server._staged_steers] == [2]
    assert set(server._captures) == {1, 2, 3}       # 2 closes when it is sent

    _read_back(server, 1)
    out = await server.tool_steer_session({"name": "chitauri", "capture": 1})
    assert "staged" in out
    assert [s.capture_id for s in server._staged_steers] == [2, 1]
    assert "not been read back" in await server.tool_steer_session(
        {"name": "chitauri", "capture": 3})

    sent = []
    monkeypatch.setattr(server, "_deliver", lambda item, text: (
        sent.append(text), server.session_steer.SENT)[1])
    await server._perform_staged_steers()
    # One message per turn: the second waits, on record, for the first to be
    # answered, and the session watcher sends it then (test_message_tracking).
    assert sent == [server.STEER_SIGNATURE + "Add a health check."], "one at a time, in order"
    assert [m.status for m in sorted(server.run_store.list_messages(), key=lambda m: m.id)] == \
        ["sent", "queued"]
    assert set(server._captures) == {3}, "the one not sent stays open"


def test_the_brain_has_no_way_to_write_a_message_itself():
    """Every message is a capture: steer_session takes no text, and Claude
    Code's own messaging tools are not among the brain's tools."""
    import brain
    import jarvis_mcp
    steer = {t["name"]: t for t in jarvis_mcp.TOOL_SPECS}["steer_session"]["inputSchema"]
    assert set(steer["properties"]) == {"name", "capture", "priority"}
    assert not {"SendMessage", "ListAgents"} & set(claude_provider.ALLOWED_TOOLS)


@pytest.mark.asyncio
async def test_a_staged_message_does_not_hold_up_a_second_and_an_unsent_one_counts_again(
        wired, fake_session, monkeypatch, emitted):
    server, (path, received, _) = wired, fake_session
    _steering(server, monkeypatch, path, FakeSpeech(cancelled=True))
    server.tool_capture({"text": "use Postgres"})
    _read_back(server, 1)
    assert "staged" in await server.tool_steer_session({"name": "chitauri", "capture": 1})
    server.tool_capture({"text": "ship it"})
    _read_back(server, 2)
    assert "staged" in await server.tool_steer_session({"name": "chitauri", "capture": 2})

    await server._perform_staged_steers()          # he says stop: neither goes
    assert received == [] and server._unsent_captures() == [1, 2]


@pytest.mark.asyncio
async def test_the_brain_can_see_every_capture_box(wired, emitted):
    assert wired.tool_list_captures({}) == "No capture boxes are open."
    wired.tool_capture({"text": "Use Postgres.", "to": "hammer"})
    wired.tool_capture({"text": "Ship it."})
    _read_back(wired, 1)
    assert wired.tool_list_captures({}).splitlines() == [
        "Capture 1 (capturing, read back in full) for hammer: Use Postgres.",
        "Capture 2 (capturing, not read back since it changed): Ship it."]


@pytest.mark.asyncio
async def test_the_brain_can_clear_capture_boxes_and_a_cleared_one_is_never_sent(
        wired, fake_session, monkeypatch, emitted):
    server, (path, received, _) = wired, fake_session
    _steering(server, monkeypatch, path)
    server.tool_capture({"text": "use Postgres"})
    _read_back(server, 1)
    await server.tool_steer_session({"name": "chitauri", "capture": 1})
    server.tool_capture({"text": "ship it"})
    server.tool_capture({"text": "and tell the team"})
    await asyncio.sleep(0)
    emitted.clear()

    assert server.tool_clear_capture({"captures": [1]}) == "Cleared capture 1. 2 still open."
    assert server._staged_steers == [], "a cleared capture is not sent"
    assert server.tool_clear_capture({"all": True}) == \
        "Cleared captures 2, 3. No capture boxes are open."
    await asyncio.sleep(0)
    assert sorted(i for i, s, _ in _captures(emitted) if s is None) == [1, 2, 3]
    assert server.tool_clear_capture({"captures": [9]}) == "There was no open capture to clear."
    await server._perform_staged_steers()
    assert received == []


# --- the capture box's buttons: Accept, Delete, Send ---------------------------

def _press(server, number, action):
    """A button on the capture box, as the page presses it."""
    from fastapi.testclient import TestClient
    import data_paths
    return TestClient(server.app).post(
        f"/api/capture/{number}/{action}",
        headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}"})


@pytest.mark.asyncio
async def test_accept_counts_as_read_back_and_the_brain_is_told(wired, emitted, monkeypatch):
    monkeypatch.setattr(wired, "_notes_for_brain", [])
    wired.tool_capture({"text": "Ship it.", "to": "hammer"})
    _, problem = wired._capture_to_send(1)
    assert problem, "not read back yet"

    r = _press(wired, 1, "accept")
    await asyncio.sleep(0)

    assert r.status_code == 200 and r.json() == {"capture": 1, "accepted": True}
    capture, problem = wired._capture_to_send(1)
    assert problem is None and capture["id"] == 1
    assert wired._capture_message(wired._captures[1], "capturing")["accepted"] is True
    assert "accepted capture 1 on screen" in wired._with_notes_for_brain("go on")
    wired.tool_capture({"text": "Ship it today.", "capture": 1})
    assert wired._capture_to_send(1)[1], "changed after it was accepted: unread again"


@pytest.mark.asyncio
async def test_delete_closes_the_box_and_cancels_a_staged_send(
        wired, fake_session, emitted, monkeypatch):
    server, (path, _received, _) = wired, fake_session
    _steering(server, monkeypatch, path)
    monkeypatch.setattr(server, "_notes_for_brain", [])
    server.tool_capture({"text": "Use Postgres."})
    _read_back(server, 1)
    await server.tool_steer_session({"name": "chitauri", "capture": 1})

    r = _press(server, 1, "delete")

    assert r.status_code == 200
    assert server._captures == {} and server._staged_steers == []
    import run_store
    assert [m.status for m in run_store.list_messages()] == ["cancelled"]
    assert "deleted capture 1" in server._with_notes_for_brain("")
    assert _press(server, 1, "delete").status_code == 404


@pytest.mark.asyncio
async def test_send_accepts_and_sends_to_the_agent_it_is_addressed_to(
        wired, fake_session, emitted, monkeypatch):
    server, (path, _received, _) = wired, fake_session
    _steering(server, monkeypatch, path)
    monkeypatch.setattr(server, "_notes_for_brain", [])
    asked = []
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (asked.append(n), (_fake_session_state(path), None, None))[1])
    sent = []
    monkeypatch.setattr(server, "_deliver", lambda item, text: (
        sent.append(text), server.session_steer.SENT)[1])
    server.tool_capture({"text": "Add a health check.", "to": "chitauri"})

    r = _press(server, 1, "send")
    for _ in range(100):
        if sent:
            break
        await asyncio.sleep(0.01)

    assert r.status_code == 200 and r.json()["capture"] == 1
    assert asked == ["chitauri"], "it goes to the name the box says Send to"
    assert sent == [server.STEER_SIGNATURE + "Add a health check."]
    assert f"as message {r.json()['message']}" in server._with_notes_for_brain("")


@pytest.mark.asyncio
async def test_send_refuses_with_the_reason_and_sends_nothing(
        wired, fake_session, emitted, monkeypatch):
    server, (path, _received, _) = wired, fake_session
    _steering(server, monkeypatch, path)
    server.tool_capture({"text": "Who is this for?"})

    r = _press(server, 1, "send")
    assert r.status_code == 409 and "isn't addressed to anyone" in r.json()["detail"]

    server.tool_capture({"text": "Who is this for?", "capture": 1, "to": "hammer"})
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (None, "There are two hammers: which one?", "ambiguous"))
    r = _press(server, 1, "send")
    assert r.status_code == 409 and r.json()["detail"] == "There are two hammers: which one?"
    assert server._staged_steers == [] and 1 in server._captures
    assert _press(server, 9, "send").status_code == 404
    assert _press(server, 9, "accept").status_code == 404


@pytest.mark.asyncio
async def test_a_stranger_page_cannot_press_the_buttons(wired, emitted):
    from fastapi.testclient import TestClient
    wired.tool_capture({"text": "Ship it."})
    for action in ("accept", "delete", "send"):
        r = TestClient(wired.app).post(f"/api/capture/1/{action}",
                                       headers={"Origin": "http://evil.example"})
        assert r.status_code == 403
    assert 1 in wired._captures


class _ReadingSpeech:
    """Reads aloud until the test says how it ended."""
    def __init__(self):
        self.read = []
        self.ended = asyncio.Event()
        self.heard = True

    def read_aloud(self, text, capture):
        self.read.append((capture, text))
        return object()

    async def wait_for(self, utt, timeout=60.0):
        await self.ended.wait()
        return self.heard


async def _read_aloud(server, monkeypatch, heard=True, change=None):
    speech = _ReadingSpeech()
    speech.heard = heard
    monkeypatch.setattr(server, "speech", speech)
    monkeypatch.setattr(server, "_notes_for_brain", [])
    assert await server.api_capture_read(1) == {"capture": 1, "reading": True}
    assert server._captures[1]["state"] == "reading"
    if change:
        server.tool_capture({"text": change, "capture": 1})
    speech.ended.set()
    for _ in range(5):
        await asyncio.sleep(0)
    return speech


@pytest.mark.asyncio
async def test_read_reads_it_aloud_and_heard_to_the_end_it_counts_as_read_back(
        wired, emitted, monkeypatch):
    wired.tool_capture({"text": "Ship it.", "to": "hammer"})

    speech = await _read_aloud(wired, monkeypatch)

    assert speech.read == [(1, "Ship it.")]
    assert wired._capture_to_send(1)[1] is None
    assert wired._captures[1]["state"] == "capturing"
    assert "read aloud from its box" in wired._with_notes_for_brain("")


@pytest.mark.asyncio
@pytest.mark.parametrize("heard, change", [(False, None), (True, "Ship it today.")])
async def test_a_reading_cut_short_or_overtaken_by_a_change_is_not_read_back(
        wired, emitted, monkeypatch, heard, change):
    wired.tool_capture({"text": "Ship it.", "to": "hammer"})

    await _read_aloud(wired, monkeypatch, heard=heard, change=change)

    assert wired._capture_to_send(1)[1], "not heard in full"
    assert wired._captures[1]["state"] == "capturing"
    assert wired._notes_for_brain == []


@pytest.mark.asyncio
async def test_read_is_refused_while_it_is_being_read_or_sent(wired, emitted, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(wired, "speech", _ReadingSpeech())
    wired.tool_capture({"text": "Ship it.", "to": "hammer"})
    wired._captures[1]["state"] = "reading"
    with pytest.raises(HTTPException) as refused:
        await wired.api_capture_read(1)
    assert refused.value.status_code == 409
    wired._captures[1].update(state="capturing", staged=True)
    with pytest.raises(HTTPException):
        await wired.api_capture_read(1)
    with pytest.raises(HTTPException) as missing:
        await wired.api_capture_read(9)
    assert missing.value.status_code == 404
