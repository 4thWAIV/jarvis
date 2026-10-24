import asyncio
import json
import os
import socket
import threading
import time

import pytest

import session_steer
from tests.fixtures.captures import captured


def _wait_for_receipt(received, timeout=2.0):
    """`post_to_session` returns as soon as `sendall` completes, not once the
    fixture's server thread has looped back around, decoded, and appended to
    `received` — that append genuinely races the test's own assertion.
    Confirmed 100% reproducible on this machine without this wait: every
    `received[0]` access failed with IndexError/JSONDecodeError."""
    deadline = time.monotonic() + timeout
    while not received and time.monotonic() < deadline:
        time.sleep(0.01)


@pytest.fixture
def fake_session(tmp_path, monkeypatch):
    """A Unix socket that accepts one line, exactly as a live session does.

    AF_UNIX's sun_path is capped at 104 bytes on macOS. pytest's default
    tmp_path (/private/var/folders/.../pytest-of-<user>/pytest-NNN/<test
    name>0/...) routinely runs 130+ bytes, so binding a socket at a full
    tmp_path-qualified name raises `OSError: AF_UNIX path too long` before
    the code under test ever runs. chdir into tmp_path and bind a short
    relative filename instead — still an isolated socket inside tmp_path,
    just addressed relatively.
    """
    monkeypatch.chdir(tmp_path)
    # This agent itself runs inside a live Claude Code session, which sets
    # CLAUDE_CODE_MESSAGING_TOKEN in its own environment (confirmed present
    # ambiently: `env | grep CLAUDE_CODE_MESSAGING` shows it set to a real
    # token). Tests that assume no auth line must not inherit it.
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_TOKEN", raising=False)
    path = "s.sock"
    received = []
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def serve():
        try:
            conn, _ = srv.accept()
            with conn:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                received.append(data.decode())
        except OSError:
            pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield path, received, srv
    srv.close()


def test_a_prompt_is_delivered_as_one_json_line(fake_session):
    path, received, _ = fake_session

    outcome = session_steer.post_to_session(path, "carry on with the redirect")

    assert outcome == "sent"
    _wait_for_receipt(received)
    msg = json.loads(received[0].strip())
    assert msg == {"type": "user",
                   "message": {"role": "user", "content": "carry on with the redirect"}}


def test_a_missing_socket_is_not_live_not_a_crash(tmp_path):
    assert session_steer.post_to_session(str(tmp_path / "nope.sock"), "hi") == "not_live"


def test_a_socket_that_refuses_the_connection_is_not_live(tmp_path, monkeypatch):
    """A stale .sock file left behind by a dead process."""
    monkeypatch.chdir(tmp_path)   # see fake_session: AF_UNIX path-length cap
    path = "stale.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    s.close()                     # the file remains, nothing listens
    assert session_steer.post_to_session(path, "hi") == "not_live"


def test_an_empty_prompt_is_refused_before_it_reaches_the_socket(fake_session):
    path, received, _ = fake_session
    assert session_steer.post_to_session(path, "   ") == "refused"
    assert received == []


def test_an_auth_token_is_sent_first_when_the_environment_has_one(fake_session, monkeypatch):
    path, received, _ = fake_session
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "tok")

    assert session_steer.post_to_session(path, "hello") == "sent"

    _wait_for_receipt(received)
    lines = [l for l in received[0].split("\n") if l.strip()]
    assert json.loads(lines[0]) == {"type": "auth", "token": "tok"}
    assert json.loads(lines[1])["message"]["content"] == "hello"


# --- the tool, with its read-back policy ------------------------------------

@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    import server as server_module
    importlib.reload(server_module)
    # Reloading the module does NOT run FastAPI's lifespan (that only fires
    # for a started app, e.g. under TestClient) — and run_store.init_db() is
    # only ever called from lifespan. Without this, `steers` (along with
    # `runs`/`run_events`) never exists and every record_steer() call in this
    # file fails with `sqlite3.OperationalError: no such table: steers`.
    server_module.run_store.init_db()
    # These tests must never read the developer's own ~/.claude/settings.json:
    # whether `crossSessionInbound` happens to be "accept" there decides
    # whether the spoken outcome carries the approval caveat, and a test that
    # depends on that passes on one machine and fails on the next. The
    # caveat has its own tests below.
    monkeypatch.setattr(server_module, "_inbound_accepted", lambda: True)
    return server_module


class FakeUtterance:
    """Stands in for speech.Utterance: only the field tool_steer_session
    actually reads."""
    def __init__(self, cancelled=False):
        self.was_cancelled = cancelled


class FakeSpeech:
    """Models the read-back contract (say -> wait_for -> open_cancel_window),
    not just the old say-then-window shape. Defaults model the happy path:
    the read-back is heard in full, and nothing is cancelled."""
    def __init__(self, cancelled=False, readback_cancelled=False, readback_heard=True):
        self.said = []
        self.cancelled = cancelled                    # cancel-window outcome
        self.readback_cancelled = readback_cancelled   # barge-in during the read-back
        self.readback_heard = readback_heard           # False models a wedged/abandoned read-back

    async def say(self, text, *a, **k):
        self.said.append(text)
        return FakeUtterance(cancelled=self.readback_cancelled)

    async def wait_for(self, utt, timeout=60.0):
        if not self.readback_heard:
            return False
        return not utt.was_cancelled

    async def open_cancel_window(self, *a, **k):
        return self.cancelled


class FakeBrain:
    current_origin = "user"


@pytest.mark.asyncio
async def test_the_tool_stages_and_speaks_nothing_at_all(wired, fake_session,
                                                         monkeypatch):
    """The tool call itself must not touch the mouth or the socket. It runs
    mid-turn, with the turn utterance still open; anything it said would be
    queued behind the very turn waiting for it to return."""
    server, (path, received, _) = wired, fake_session
    speech = FakeSpeech()
    monkeypatch.setattr(server, "speech", speech)
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))

    result = await server.tool_steer_session(captured(server, {"name": "chitauri",
                                              "prompt": "use Postgres"}))

    assert speech.said == [], "the read-back may not happen inside the tool call"
    assert received == [], "nothing may be sent from inside the tool call"
    assert "staged" in result.lower()
    assert len(server._staged_steers) == 1
    assert server._staged_steers[0].prompt == "use Postgres"
    import run_store
    assert run_store.list_steers(limit=50) == [], "a message is not a steers row"
    (m,) = _messages()
    assert (m.status, m.body) == ("staged", "use Postgres"), \
        "the record is written when it is staged, before anything is sent"
    assert server._staged_steers[0].message_id == m.id


@pytest.mark.asyncio
async def test_a_staged_steer_sends_the_heard_capture_without_reading_it_again(
        wired, fake_session, monkeypatch):
    """The brain read the capture back with read_capture before staging it;
    the server says only the outcome, never the message a second time."""
    server, (path, received, _) = wired, fake_session
    speech = FakeSpeech()
    monkeypatch.setattr(server, "speech", speech)
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))

    await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "use Postgres"}))
    await server._perform_staged_steers()

    assert speech.said == [], "sent is not delivered: the watcher says that"
    _wait_for_receipt(received)
    assert json.loads(received[0].strip())["message"]["content"] == \
        server.STEER_SIGNATURE + "use Postgres"
    assert server._staged_steers == [], "a performed steer must be off the list"
    (m,) = _messages()
    assert m.status == "sent" and m.sent_at is not None
    assert m.text == server.STEER_SIGNATURE + "use Postgres", "exactly what was sent"


@pytest.mark.asyncio
async def test_saying_stop_during_the_window_cancels_the_send(wired, fake_session,
                                                              monkeypatch):
    server, (path, received, _) = wired, fake_session
    speech = FakeSpeech(cancelled=True)
    monkeypatch.setattr(server, "speech", speech)
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))

    await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "drop it"}))
    await server._perform_staged_steers()

    (m,) = _messages()
    assert m.status == "cancelled" and m.sent_at is None
    assert received == [], "nothing may reach the session after a cancel"


@pytest.mark.asyncio
async def test_a_session_waiting_on_a_permission_prompt_is_refused_honestly(
        wired, monkeypatch):
    """The socket carries peer authority: it cannot dismiss a permission
    prompt. Pretending otherwise would send a message into the void."""
    server = wired
    speech = FakeSpeech()
    monkeypatch.setattr(server, "speech", speech)
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    state = _fake_session_state("/nonexistent.sock")
    state.needs = "permission prompt"
    state.state = "needs_you"
    monkeypatch.setattr(server, "_resolve_or_explain", lambda n: (state, None, None))

    result = await server.tool_steer_session(captured(server, {"name": "x", "prompt": "yes"}))

    assert "keystroke" in result.lower() or "yourself" in result.lower()
    assert "permission" in result.lower()


@pytest.mark.asyncio
async def test_an_unsteerable_session_says_so_rather_than_failing_silently(
        wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    state = _fake_session_state(None)
    state.steerable = False
    monkeypatch.setattr(server, "_resolve_or_explain", lambda n: (state, None, None))

    result = await server.tool_steer_session(captured(server, {"name": "x", "prompt": "hi"}))

    assert "cannot" in result.lower() or "can't" in result.lower()


@pytest.mark.asyncio
async def test_every_steer_is_recorded_whatever_the_outcome(wired, fake_session,
                                                            monkeypatch):
    server, (path, _, _) = wired, fake_session
    monkeypatch.setattr(server, "speech", FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))

    await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "go on"}))
    await server._perform_staged_steers()

    (m,) = _messages()
    assert (m.body, m.status, m.agent_name, m.session_id) == ("go on", "sent", "chitauri", "sid")


def _messages():
    """Every message on record, newest first."""
    import run_store
    return run_store.list_messages(limit=50)


def _fake_session_state(socket_path):
    import session_watch as sw
    return sw.SessionState(
        session_id="sid", cwd="/p/chitauri", project="chitauri",
        state="idle", voice_name="chitauri", steerable=socket_path is not None,
        socket_path=socket_path, pids=[1], primary_pid=1)


# --- Finding 4: speech is None must refuse to steer, not skip the gate -----

@pytest.mark.asyncio
async def test_no_voice_refuses_to_steer_rather_than_sending_unheard(
        wired, fake_session, monkeypatch):
    server, (path, received, _) = wired, fake_session
    monkeypatch.setattr(server, "speech", None)
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))

    result = await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "go on"}))

    assert received == [], "nothing may be sent when there is no voice to read it back"
    (m,) = _messages()
    assert (m.status, m.failure, m.body) == ("failed", "no_voice", "go on")


# --- Finding 5: the no-socket case must not share not_live's label ---------

@pytest.mark.asyncio
async def test_no_socket_is_recorded_as_not_steerable_not_not_live(wired, monkeypatch):
    """server.py's own 'never had a socket' case must be distinguishable in
    the audit trail from session_steer.NOT_LIVE (a dead/missing socket found
    at send time) — same string, two different causes, was the bug."""
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    state = _fake_session_state(None)
    state.steerable = False
    monkeypatch.setattr(server, "_resolve_or_explain", lambda n: (state, None, None))

    await server.tool_steer_session(captured(server, {"name": "x", "prompt": "hi"}))

    (m,) = _messages()
    assert (m.status, m.failure) == ("failed", "not_steerable")


# --- Finding 3: every terminal outcome gets exactly one audit row ----------

@pytest.mark.asyncio
async def test_empty_prompt_is_recorded_exactly_once(wired, fake_session, monkeypatch):
    server, (path, _, _) = wired, fake_session
    monkeypatch.setattr(server, "speech", FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))
    import run_store
    before = len(run_store.list_steers(limit=50))

    result = await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "   "}))

    rows = run_store.list_steers(limit=50)
    assert len(rows) == before + 1
    assert rows[0]["outcome"] == "no_capture"
    assert "capture" in result.lower()


@pytest.mark.asyncio
async def test_unresolved_name_is_recorded_exactly_once(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(
        server, "_resolve_or_explain",
        lambda n: (None, "I don't see a session called bogus.", "unresolved"))
    import run_store
    before = len(run_store.list_steers(limit=50))

    result = await server.tool_steer_session(captured(server, {"name": "bogus", "prompt": "go"}))

    rows = run_store.list_steers(limit=50)
    assert len(rows) == before + 1
    assert rows[0]["outcome"] == "unresolved"
    assert rows[0]["voice_name"] == "bogus"
    assert rows[0]["session_id"] == ""
    assert "don't see" in result.lower()


@pytest.mark.asyncio
async def test_ambiguous_name_is_recorded_exactly_once(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(
        server, "_resolve_or_explain",
        lambda n: (None, "There are 2: chitauri and hammer. Which one?", "ambiguous"))
    import run_store
    before = len(run_store.list_steers(limit=50))

    result = await server.tool_steer_session(captured(server, {"name": "co", "prompt": "go"}))

    rows = run_store.list_steers(limit=50)
    assert len(rows) == before + 1
    assert rows[0]["outcome"] == "ambiguous"
    assert rows[0]["session_id"] == ""
    assert "which one" in result.lower()


def test_resolve_or_explain_reports_unresolved_and_ambiguous_reasons(monkeypatch):
    """End-to-end check of the real resolver's third element, not just the
    tool's handling of a monkeypatched stand-in."""
    import server
    import session_watch as sw

    empty = sw.Snapshot()

    class _EmptyWatcher:
        snapshot = empty

    monkeypatch.setattr(server, "session_watcher", _EmptyWatcher())
    session, problem, reason = server._resolve_or_explain("nobody")
    assert session is None and reason == "unresolved"

    dupes = sw.Snapshot(sessions=[
        sw.SessionState(session_id="a", cwd="/p/hammer", project="hammer",
                        state="idle", voice_name="hammer", pids=[1], primary_pid=1),
        sw.SessionState(session_id="b", cwd="/p/hammer2", project="hammer2",
                        state="idle", voice_name="hammer", pids=[2], primary_pid=2),
    ])

    class _DupeWatcher:
        snapshot = dupes

    monkeypatch.setattr(server, "session_watcher", _DupeWatcher())
    session, problem, reason = server._resolve_or_explain("hammer")
    assert session is None and reason == "ambiguous"


@pytest.mark.asyncio
async def test_needs_a_human_hand_and_not_steerable_and_sent_are_each_recorded_once(
        wired, fake_session, monkeypatch):
    """Rounds out Finding 3's exactly-one-row list for outcomes already
    reachable before this fix: needs_a_human_hand, not_steerable, sent."""
    server, (path, received, _) = wired, fake_session
    import run_store

    # needs_a_human_hand
    monkeypatch.setattr(server, "speech", FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    hand_state = _fake_session_state(path)
    hand_state.needs = "permission prompt"
    hand_state.state = "needs_you"
    monkeypatch.setattr(server, "_resolve_or_explain", lambda n: (hand_state, None, None))
    before = len(_messages())
    await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "go"}))
    rows = _messages()
    assert len(rows) == before + 1 and rows[0].failure == "needs_a_human_hand"

    # not_steerable
    unsteerable = _fake_session_state(None)
    unsteerable.steerable = False
    monkeypatch.setattr(server, "_resolve_or_explain", lambda n: (unsteerable, None, None))
    before = len(_messages())
    await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "go"}))
    rows = _messages()
    assert len(rows) == before + 1 and rows[0].failure == "not_steerable"

    # sent
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))
    before = len(_messages())
    await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "go"}))
    await server._perform_staged_steers()
    rows = _messages()
    assert len(rows) == before + 1 and rows[0].status == "sent"
    assert run_store.list_steers(limit=50) == [], "no message is a steers row"




# --- Criticals 1 & 2: the read-back must not be queued behind its own turn --
#
# `tool_steer_session` is called BY THE BRAIN, MID-TURN, through the MCP
# child. At that moment `speech.begin_turn()`'s utterance is open, and the
# scheduler will not advance past an open utterance. A read-back said from
# inside the tool call is therefore queued behind the very turn that is
# blocked waiting for the call to return — a deadlock broken only by the MCP
# child's 20s timeout, after which the brain is told the server is
# unreachable and the server sends the message anyway. JARVIS announced a
# failure and then did it: the read-back-then-cancel gate defeated entirely.
#
# Neither FakeSpeech (which returns instantly from everything) nor the
# scheduler tests (which never combine begin_turn with a steer) could see
# that. These drive the REAL SpeechScheduler through the REAL
# `server._handle_utterance`, with a brain that calls the tool mid-turn.

from tests.test_speech import Harness  # noqa: E402


@pytest.fixture
def socket_factory(tmp_path, monkeypatch):
    """Make as many one-shot inbox sockets as a test needs.

    chdir + short relative names: AF_UNIX's sun_path is capped at 104 bytes
    on macOS and pytest's tmp_path alone routinely exceeds it. Never point
    any of this at /tmp/cc-socks — those are the user's live sessions.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_TOKEN", raising=False)
    servers = []

    def make(name):
        received = []
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(name)
        srv.listen(1)
        servers.append(srv)

        def serve():
            try:
                conn, _ = srv.accept()
                with conn:
                    data = b""
                    while b"\n" not in data:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    received.append((time.monotonic(), data.decode()))
            except OSError:
                pass

        threading.Thread(target=serve, daemon=True).start()
        return name, received

    yield make
    for srv in servers:
        srv.close()


class _Client:
    """A browser that plays every chunk the instant it arrives, in the
    background — so a turn and the read-back that follows it both complete.
    `skip` models a chunk that never finishes playing."""

    def __init__(self, h, skip=None):
        self.h = h
        self.skip = skip or (lambda text: False)
        self.acked = []                      # (monotonic, text), in play order
        self._stop = asyncio.Event()
        self._task = None

    def start(self):
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        while not self._stop.is_set():
            for m in list(self.h.msgs):
                if (m["type"] == "audio" and not m.get("_acked")
                        and not m.get("_dropped") and not self.skip(m.get("text", ""))):
                    await self.h.ack(m["utt"], m["idx"])
                    self.acked.append((time.monotonic(), m["text"]))
            await asyncio.sleep(0.01)

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    def acked_at(self, prefix):
        for when, text in self.acked:
            if text.startswith(prefix):
                return when
        return None


class ScriptedBrain:
    """A brain that calls tools mid-turn, exactly where the real one does."""

    ready = True
    failed = False
    current_origin = "user"
    rotation_pending = False        # the real Brain's; nothing to rotate here

    def __init__(self, server, calls, reply="Right away, sir.",
                 stop_reason="result", boom=False):
        self._server = server
        self._calls = calls
        self._reply = reply
        self._stop_reason = stop_reason
        self._boom = boom
        self.tool_results = []
        self.tool_seconds = []

    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        for args in self._calls:
            t0 = time.monotonic()
            # The real brain fires this as the CLI reports each tool_use; the
            # server uses it to bin narration written before the tool ran.
            if on_tool:
                on_tool()
            self.tool_results.append(await self._server.tool_steer_session(captured(self._server, args)))
            self.tool_seconds.append(time.monotonic() - t0)
        if on_delta:
            on_delta(self._reply)
        if self._boom:
            raise RuntimeError("the brain fell over")
        import brain
        return brain.TurnResult(origin=origin, text=self._reply,
                                stop_reason=self._stop_reason)


class TurnRig:
    """The real scheduler + the real _handle_utterance + a scripted brain."""

    def __init__(self, server, monkeypatch, calls, *, skip_ack=None,
                 cancel_window=0.05, **brain_kw):
        self.server = server
        self.h = Harness()
        self.client = _Client(self.h, skip=skip_ack)
        self.brain = ScriptedBrain(server, calls, **brain_kw)
        monkeypatch.setattr(server, "speech", self.h.sched)
        monkeypatch.setattr(server, "brain_instance", self.brain)
        monkeypatch.setattr(server, "STEER_CANCEL_WINDOW", cancel_window)

    async def __aenter__(self):
        await self.h.sched.start()
        self.client.start()
        return self

    async def __aexit__(self, *exc):
        await self.client.stop()
        await self.h.sched.stop()

    def start(self, text="tell chitauri to use postgres"):
        self.task = asyncio.create_task(self.server._handle_utterance(text))
        return self.task

    async def wait_until_speaking(self, prefix, timeout=5.0):
        """Wait until a chunk starting with `prefix` has been SENT to the client."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for m in self.h.msgs:
                if m["type"] == "audio" and m["text"].startswith(prefix):
                    return m
            await asyncio.sleep(0.01)
        pytest.fail(f"never spoke a chunk starting with {prefix!r}")

    def audio(self):
        return [m for m in self.h.msgs if m["type"] == "audio"]

    def spoken(self):
        return [m["text"] for m in self.audio()]


def _steer_args(name="chitauri", prompt="use Postgres"):
    return {"name": name, "prompt": prompt}


@pytest.mark.asyncio
async def test_the_send_happens_after_the_turn_has_been_heard(
        wired, socket_factory, monkeypatch):
    """(a) and (b). The tool call only stages; the cancel window and the send
    come once the turn has finished speaking, and the message is not read
    out again (read_capture already did that, inside the turn)."""
    server = wired
    path, received = socket_factory("a.sock")
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))

    async with TurnRig(server, monkeypatch, [_steer_args()]) as rig:
        await asyncio.wait_for(rig.start(), timeout=15)

    # (b) the tool call itself is fast: it validates and stages, nothing more.
    assert rig.brain.tool_seconds[0] < 0.5, rig.brain.tool_seconds
    assert "staged" in rig.brain.tool_results[0].lower()

    # (a) the message is not read out by the server
    assert not any("use Postgres" in t for t in rig.spoken()), rig.spoken()
    assert "Right away, sir." in rig.spoken()

    # nothing reached the socket until the turn had been heard
    _wait_for_receipt(received)
    assert received, "the steer must actually be delivered"
    heard_at = rig.client.acked_at("Right away")
    assert heard_at is not None
    assert received[0][0] > heard_at, "delivery must follow the turn"
    assert json.loads(received[0][1].strip())["message"]["content"] == \
        server.STEER_SIGNATURE + "use Postgres"
    assert not any(t.startswith("Passed to") for t in rig.spoken()), \
        "sent is not delivered: the watcher says that once it is seen"

    rows = _messages()
    assert len(rows) == 1 and rows[0].status == "sent"      # (f)


@pytest.mark.asyncio
async def test_a_cancel_word_in_the_window_after_the_readback_blocks_the_send(
        wired, socket_factory, monkeypatch):
    """(c)"""
    server = wired
    path, received = socket_factory("b.sock")
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))

    async with TurnRig(server, monkeypatch, [_steer_args(prompt="drop it")],
                       cancel_window=2.0) as rig:
        task = rig.start()
        s = rig.h.sched
        for _ in range(500):                     # wait for the window to open
            if s.classify("wait") == "cancel":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("the cancel window never opened")
        assert await s.user_final("wait") == "cancel"
        await asyncio.wait_for(task, timeout=15)

    assert received == [], "the cancel word must block delivery"
    rows = _messages()
    assert len(rows) == 1 and rows[0].status == "cancelled"


@pytest.mark.asyncio
async def test_two_steers_staged_in_one_turn_are_both_performed_in_order(
        wired, socket_factory, monkeypatch):
    """(g)"""
    server = wired
    first, first_rx = socket_factory("e1.sock")
    second, second_rx = socket_factory("e2.sock")
    states = {"chitauri": _fake_session_state(first),
              "hammer": _fake_session_state(second)}
    states["hammer"].session_id = "sid2"
    states["hammer"].voice_name = "hammer"
    states["hammer"].project = "hammer"
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (states[n], None, None))

    calls = [_steer_args("chitauri", "use Postgres"),
             _steer_args("hammer", "revert the schema")]
    async with TurnRig(server, monkeypatch, calls) as rig:
        await asyncio.wait_for(rig.start(), timeout=20)

    _wait_for_receipt(first_rx)
    _wait_for_receipt(second_rx)
    assert first_rx and second_rx, "both steers must be delivered"
    assert first_rx[0][0] < second_rx[0][0], "staged order must be preserved"

    rows = _messages()                              # (f), one record each
    assert len(rows) == 2
    assert {r.status for r in rows} == {"sent"}
    assert [r.agent_name for r in rows] == ["hammer", "chitauri"]   # newest first


@pytest.mark.asyncio
async def test_a_staged_steer_still_happens_when_the_turn_ends_badly(
        wired, socket_factory, monkeypatch):
    """The user asked for it; the read-back and the cancel window still give
    him the last word. A brain that fell over must not silently swallow it."""
    server = wired
    path, received = socket_factory("f.sock")
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))

    async with TurnRig(server, monkeypatch, [_steer_args(prompt="use Postgres")],
                       boom=True) as rig:
        await asyncio.wait_for(rig.start(), timeout=15)

    assert "I lost my train of thought, sir." in rig.spoken()
    _wait_for_receipt(received)
    assert received, "a staged steer survives a failed turn"
    rows = _messages()
    assert len(rows) == 1 and rows[0].status == "sent"


@pytest.mark.asyncio
async def test_a_staged_steer_is_never_performed_twice(wired, socket_factory,
                                                       monkeypatch):
    """The list is drained before anything is performed, so a failure part
    way through can never leave a steer behind to be sent again."""
    server = wired
    path, received = socket_factory("g.sock")
    monkeypatch.setattr(server, "speech", FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))
    import run_store

    await server.tool_steer_session(captured(server, _steer_args(prompt="use Postgres")))
    boom = asyncio.Event()

    async def explode(*a, **k):
        boom.set()
        raise RuntimeError("the mouth fell off")

    monkeypatch.setattr(server.speech, "open_cancel_window", explode)
    await server._perform_staged_steers()          # must not raise out

    assert boom.is_set()
    assert server._staged_steers == []
    assert received == []
    rows = _messages()
    assert len(rows) == 1 and (rows[0].status, rows[0].failure) == ("failed", "failed"), \
        "the record is never left staged"

    await server._perform_staged_steers()          # nothing left to do
    assert received == []
    assert len(_messages()) == 1


@pytest.mark.asyncio
async def test_a_sent_message_tells_the_voice_page_which_agent_got_it(
        wired, fake_session, monkeypatch):
    """The page draws the message going from the orb to that agent."""
    server, (path, received, _) = wired, fake_session
    hints = []
    monkeypatch.setattr(server, "_send_to_session_clients", hints.append)
    monkeypatch.setattr(server, "speech", FakeSpeech())
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    session = _fake_session_state(path)
    monkeypatch.setattr(server, "_resolve_or_explain", lambda n: (session, None, None))

    await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "use Postgres"}))
    await server._perform_staged_steers()

    assert {"type": "agent_message", "session_id": session.session_id} in hints
    # It moved up the agents list first, so the page has the new order to
    # draw the ray to.
    assert hints.index({"type": "agents"}) < hints.index(
        {"type": "agent_message", "session_id": session.session_id})


@pytest.mark.asyncio
async def test_a_cancelled_message_draws_nothing(wired, fake_session, monkeypatch):
    server, (path, received, _) = wired, fake_session
    hints = []
    monkeypatch.setattr(server, "_send_to_session_clients", hints.append)
    monkeypatch.setattr(server, "speech", FakeSpeech(cancelled=True))
    monkeypatch.setattr(server, "brain_instance", FakeBrain())
    monkeypatch.setattr(server, "_resolve_or_explain",
                        lambda n: (_fake_session_state(path), None, None))

    await server.tool_steer_session(captured(server, {"name": "chitauri", "prompt": "drop it"}))
    await server._perform_staged_steers()

    assert not [h for h in hints if h.get("type") == "agent_message"]
