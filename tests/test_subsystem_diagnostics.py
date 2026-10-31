"""The Diagnostics tab's subsystems (subsystem_diagnostics.py): each probe's
state rules, above all every way agent communications are down and how they
come back; the heartbeats and outcomes the server notes; the route; and
JARVIS saying once when communications go down and once when they are back."""

import asyncio
import importlib
import os
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis import subsystem_diagnostics as sd
from jarvis.run_store import Message

NOW = 1_000_000.0
ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture(autouse=True)
def _fresh_notes():
    """The heartbeats and outcomes are the server's memory: each test starts
    with none."""
    sd.beats.clear()
    sd.voice_output_calls.items.clear()
    sd.tool_calls.items.clear()
    yield
    sd.beats.clear()
    sd.voice_output_calls.items.clear()
    sd.tool_calls.items.clear()


def watcher(**kw):
    fields = dict(running=True, poll_failures=0, last_poll_error=None, last_poll_ok_at=NOW - 2,
                  snapshot=SimpleNamespace(sessions=[SimpleNamespace(state="idle"),
                                                     SimpleNamespace(state="gone")]))
    fields.update(kw)
    return SimpleNamespace(**fields)


_ids = iter(range(1, 10_000))


def message(status, session="s1", at=NOW - 120, failure=None, delivered=None):
    """A message sent `at`, newest-first lists are built by the caller."""
    return Message(id=next(_ids), session_id=session, provider="anthropic", agent_name="hammer",
                   project="p", capture_id=None, text="x", status=status, staged_at=at - 5,
                   sent_at=None if status in ("failed", "queued", "staged") else at,
                   delivered_at=delivered, failure=failure)


def live_beat(name, every=5.0, ok_ago=1.0, started_ago=100.0):
    b = sd.beat(name, every, now=NOW - started_ago)
    if ok_ago is not None:
        b.ok(now=NOW - ok_ago)
    return b


def healthy_beats():
    live_beat(sd.NEEDS_LOOP)
    live_beat(sd.SESSION_MESSAGES_LOOP)
    return dict(sd.beats)


def comms(w=None, messages=(), beats=None, waiting=0, started_ago=600.0, queued=()):
    return sd.communications(w if w is not None else watcher(), list(messages),
                             healthy_beats() if beats is None else beats, waiting,
                             NOW, NOW - started_ago, list(queued))


# ── the session watcher ───────────────────────────────────────────────────

def test_a_watcher_polling_is_ok_and_counts_the_live_sessions():
    c = sd.watcher_check(watcher(), NOW, NOW - 600)
    assert c.state == sd.OK and c.detail.startswith("Watching 1 session;")


@pytest.mark.parametrize("w, started_ago, state", [
    (None, 5, sd.UNKNOWN),                                   # the server is starting
    (None, 600, sd.DOWN),                                    # never started
    (watcher(running=False), 600, sd.DOWN),                  # its task ended
    (watcher(last_poll_ok_at=None), 5, sd.UNKNOWN),          # first poll under way
    (watcher(last_poll_ok_at=None), 600, sd.DOWN),           # no poll ever finished
    (watcher(poll_failures=3, last_poll_error="OSError: x"), 600, sd.DOWN),
    (watcher(poll_failures=1, last_poll_error="OSError: x"), 600, sd.DEGRADED),
    (watcher(last_poll_ok_at=NOW - 61), 600, sd.DOWN),       # stopped polling
    (watcher(last_poll_ok_at=NOW - 20), 600, sd.DEGRADED),   # polling late
])
def test_the_watcher_s_states(w, started_ago, state):
    assert sd.watcher_check(w, NOW, NOW - started_ago).state == state


def test_the_real_watcher_notes_each_poll(monkeypatch):
    """SessionWatcher's own loop keeps what the probe reads: a poll that
    raises counts in a row with its error, and one that finishes clears it."""
    from jarvis.session_watch import SessionWatcher
    w = SessionWatcher(interval=0.01, providers=[])
    outcomes = iter([RuntimeError("roster unreadable"), RuntimeError("again"), None])

    def poll(now=None):
        failure = next(outcomes, None)
        if failure:
            raise failure

    monkeypatch.setattr(w, "poll_once", poll)

    async def run():
        assert not w.running
        await w.start()
        assert w.running
        for _ in range(200):
            if w.poll_failures == 2:
                assert w.last_poll_error == "RuntimeError: again" and w.last_poll_ok_at is None
            if w.last_poll_ok_at is not None:
                break
            await asyncio.sleep(0.01)
        await w.stop()

    asyncio.run(run())
    assert w.last_poll_ok_at is not None and w.poll_failures == 0 and w.last_poll_error is None
    assert not w.running


# ── messages to sessions ─────────────────────────────────────────────────

def test_no_messages_in_the_hour_is_idle():
    old = message("answered", at=NOW - 2 * 3600)
    assert sd.outgoing_check([old], NOW).state == sd.IDLE


def test_messages_that_reached_their_agents_are_ok():
    c = sd.outgoing_check([message("answered"), message("delivered"),
                           message("undelivered", at=NOW - 600)], NOW)
    assert c.state == sd.OK and c.detail == "2 reached their agents in the last hour, 1 did not"


def test_one_agent_not_picking_up_is_degraded_not_down():
    c = sd.outgoing_check([message("undelivered", "s1", NOW - 100),
                           message("undelivered", "s1", NOW - 200),
                           message("answered", "s2", NOW - 300)], NOW)
    assert c.state == sd.DEGRADED
    assert c.detail == ("The last 2 messages to hammer did not reach it: "
                        "it never showed up in the agent's conversation")


def test_a_lost_message_says_what_the_channel_said():
    c = sd.outgoing_check([message("failed", "s1", NOW - 100,
                                   failure="failed: codex queue exited 1: no such thread"),
                           message("answered", "s2", NOW - 300)], NOW)
    assert c.state == sd.DEGRADED
    assert c.detail == "The last message to hammer did not reach it: codex queue exited 1: no such thread"


def test_lost_messages_to_two_agents_in_a_row_is_down():
    c = sd.outgoing_check([message("undelivered", "s1", NOW - 100),
                           message("failed", "s2", NOW - 200, failure="not_live"),
                           message("answered", "s3", NOW - 300)], NOW)
    assert c.state == sd.DOWN
    assert c.detail == ("The last 2 messages, to 2 different agents, did not reach them; "
                        "the latest: it never showed up in the agent's conversation")


def test_a_message_reaching_its_agent_brings_it_back():
    lost = [message("undelivered", "s1", NOW - 100), message("failed", "s2", NOW - 200,
                                                             failure="failed")]
    assert sd.outgoing_check(lost, NOW).state == sd.DOWN
    assert sd.outgoing_check([message("delivered", "s3", NOW - 10)] + lost, NOW).state == sd.OK


def test_a_message_sent_and_never_followed_is_lost():
    """Still `sent` well past the delivery timeout: the watcher that marks
    it never got to it, which is worked out here at read time."""
    late = NOW - sd.UNFOLLOWED_AFTER_SEC - 1
    assert sd.message_outcome(message("sent", at=late), NOW) == "lost"
    assert sd.message_outcome(message("sent", at=NOW - 10), NOW) is None
    c = sd.outgoing_check([message("sent", "s1", late), message("sent", "s2", late - 5)], NOW)
    assert c.state == sd.DOWN


@pytest.mark.parametrize("failure", ["needs_a_human_hand", "not_steerable", "no_voice",
                                     "its session ended before it was its turn"])
def test_a_message_jarvis_chose_not_to_send_is_not_a_lost_one(failure):
    assert sd.message_outcome(message("failed", failure=failure), NOW) is None


@pytest.mark.parametrize("status", ["queued", "staged", "cancelled"])
def test_waiting_and_cancelled_messages_are_not_judged(status):
    assert sd.message_outcome(message(status), NOW) is None


def test_a_message_delivered_but_failed_later_reached_its_agent():
    m = message("failed", failure="not answered within a day", delivered=NOW - 50)
    assert sd.message_outcome(m, NOW) == "reached"


def test_no_queued_message_is_idle_and_a_young_one_is_ok():
    assert sd.queued_check([], watcher(), NOW).state == sd.IDLE
    c = sd.queued_check([message("queued", at=NOW - 60)], watcher(), NOW)
    assert c.state == sd.OK and "1 message waiting for its turn" in c.detail


def test_a_message_held_in_the_queue_says_what_jarvis_takes_its_session_for():
    """Found live: a session idle for two hours read as mid-turn, so two
    messages to it waited and nothing said so."""
    held = message("queued", session="s9", at=NOW - 3000)
    sessions = [SimpleNamespace(session_id="s9", state="working")]
    snap = SimpleNamespace(sessions=sessions,
                           by_id=lambda sid: next((s for s in sessions if s.session_id == sid), None))
    s = comms(w=watcher(snapshot=snap), queued=[message("queued", at=NOW - 30), held])
    c = next(c for c in s.checks if c.id == "queued")
    assert c.state == sd.DEGRADED and s.state == sd.DEGRADED
    assert c.detail.startswith("A message to hammer has waited ")
    assert c.detail.endswith("for its turn; JARVIS sees it as working")


# ── messages from sessions, the needs board, the loops ────────────────────

def test_a_loop_s_states():
    assert sd.judge_loop(None, NOW)[0] == sd.UNKNOWN
    assert sd.judge_loop(live_beat("a", ok_ago=None, started_ago=1), NOW)[0] == sd.UNKNOWN
    assert sd.judge_loop(live_beat("b", ok_ago=2), NOW)[0] == sd.OK
    b = live_beat("c", every=5.0, ok_ago=51)           # 4 x 5 s + 30 s
    assert sd.judge_loop(b, NOW) == (sd.DOWN, "Has not finished a look for 51 s")
    b = live_beat("d", ok_ago=None, started_ago=60)    # never finished one
    assert sd.judge_loop(b, NOW)[0] == sd.DOWN
    b = live_beat("e", ok_ago=3)
    b.failed("disk full")
    assert sd.judge_loop(b, NOW) == (sd.DEGRADED, "The last look failed: disk full")


def test_message_files_left_waiting_are_degraded(tmp_path):
    from jarvis import session_messages
    folder = tmp_path / "messages"
    (folder / "proj").mkdir(parents=True)
    old, new = folder / "proj" / "a.md", folder / "proj" / "b.md"
    old.write_text("# hi\nthere")
    new.write_text("# hi\nthere")
    os.utime(old, (NOW - 120, NOW - 120))
    os.utime(new, (NOW - 5, NOW - 5))
    assert session_messages.waiting(folder, NOW, sd.FILE_WAITING_SEC) == 1
    assert session_messages.waiting(tmp_path / "none", NOW, sd.FILE_WAITING_SEC) == 0
    c = sd.incoming_check(live_beat(sd.SESSION_MESSAGES_LOOP), 1, NOW)
    assert c.state == sd.DEGRADED and c.detail == "1 message waiting over 60 s to be picked up"


def test_incoming_is_down_when_its_loop_stops():
    b = live_beat(sd.SESSION_MESSAGES_LOOP, every=5.0, ok_ago=300)
    assert sd.incoming_check(b, 3, NOW).state == sd.DOWN


def test_the_server_s_loops_beat(monkeypatch):
    """`_every` keeps a heartbeat under the loop's name: a failed look is
    noted with its error, and the next good one clears it."""
    from jarvis import server
    looks = iter([ValueError("unreadable"), None, None])
    seen = []

    async def look():
        failure = next(looks)
        seen.append(dict(vars(sd.beats["test loop"])))
        if failure:
            raise failure

    async def run():
        task = asyncio.create_task(server._every(0.01, "test loop", look))
        while len(seen) < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert seen[1]["failures"] == 1 and seen[1]["error"] == "unreadable"
    assert seen[2]["failures"] == 0 and seen[2]["last_ok_at"] is not None


# ── communications, as one ───────────────────────────────────────────────

def test_communications_working_are_ok():
    s = comms(messages=[message("answered")])
    assert s.state == sd.OK and s.summary.startswith("Watching 1 session")
    assert [c.id for c in s.checks] == ["watcher", "outgoing", "queued", "incoming", "needs"]
    assert s.messages[0]["outcome"] == "reached"


@pytest.mark.parametrize("make", [
    lambda: comms(w=watcher(running=False)),
    lambda: comms(w=watcher(last_poll_ok_at=NOW - 120)),
    lambda: comms(w=watcher(poll_failures=5, last_poll_error="boom")),
    lambda: comms(messages=[message("undelivered", "s1"), message("undelivered", "s2")]),
    lambda: comms(beats={sd.NEEDS_LOOP: live_beat(sd.NEEDS_LOOP),
                         sd.SESSION_MESSAGES_LOOP: live_beat(sd.SESSION_MESSAGES_LOOP, ok_ago=600)}),
    lambda: comms(beats={sd.SESSION_MESSAGES_LOOP: live_beat(sd.SESSION_MESSAGES_LOOP),
                         sd.NEEDS_LOOP: live_beat(sd.NEEDS_LOOP, ok_ago=600)}),
], ids=["watcher-ended", "watcher-stopped-polling", "watcher-failing", "messages-lost",
        "incoming-stopped", "needs-board-stopped"])
def test_every_way_communications_are_down(make):
    s = make()
    assert s.state == sd.DOWN
    bad = next(c for c in s.checks if c.state == sd.DOWN)
    assert s.summary == f"{bad.name}: {bad.detail}"


def test_communications_starting_are_unknown_not_down():
    s = comms(w=watcher(last_poll_ok_at=None), beats={}, started_ago=5)
    assert s.state == sd.UNKNOWN and s.summary == "Starting"


# ── the other subsystems ─────────────────────────────────────────────────

def voice_summary(state="healthy", cause="", last_drop=None):
    stages = ("mic", "browser", "connection", "recogniser", "jarvis")
    return {"state": state, "cause": cause, "lastDropAt": last_drop,
            "pipeline": {s: {"state": "ok", "value": 0.1} for s in stages}}


@pytest.mark.parametrize("summary, state, words", [
    (voice_summary("idle"), sd.IDLE, "Not listening"),
    (voice_summary(), sd.OK, "Hearing you"),
    (voice_summary("decoder-slow", "Machine busy"), sd.DEGRADED, "Falling behind: Machine busy"),
    (voice_summary("no-frames", "No audio"), sd.DOWN, "Dropping speech: No audio"),
    (voice_summary(last_drop=NOW - 30), sd.DOWN, "Dropping speech"),
    (voice_summary(last_drop=NOW - 600), sd.OK, "Hearing you"),
])
def test_voice_input_is_judged_from_the_voice_record(summary, state, words):
    s = sd.voice_input(summary, NOW)
    assert (s.state, s.summary) == (state, words)
    assert [c.id for c in s.checks] == ["mic", "browser", "connection", "recogniser", "jarvis"]


def brain(**kw):
    fields = dict(failed=False, failure_reason=None, restarting=False, running=True, ready=True,
                  rate_limited=False, recent_restarts=0, mcp_servers=[],
                  config=SimpleNamespace(restart_window=300.0),
                  provider=SimpleNamespace(name="claude"))
    fields.update(kw)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize("b, autostart, started_ago, state, words", [
    (None, True, 600, sd.UNKNOWN, "Not started"),
    (brain(), True, 600, sd.OK, "Ready"),
    (brain(failed=True, failure_reason="auth"), True, 600, sd.DOWN, "Its login has expired"),
    (brain(failed=True), True, 600, sd.DOWN, "Stopped after crashing repeatedly"),
    (brain(restarting=True, running=False), True, 600, sd.DEGRADED,
     "Restarting after a crash or a stall"),
    (brain(running=False, ready=False), False, 600, sd.UNKNOWN, "Not started (autostart is off)"),
    (brain(running=False, ready=False), True, 5, sd.UNKNOWN, "Starting"),
    (brain(running=False, ready=False), True, 600, sd.DOWN, "No brain process is running"),
    (brain(ready=False), True, 600, sd.UNKNOWN, "Warming up"),
    (brain(rate_limited=True), True, 600, sd.DEGRADED, "At the usage limit"),
    (brain(recent_restarts=2), True, 600, sd.DEGRADED, "Restarted 2 times in the last 5 min"),
])
def test_the_brain_s_states(b, autostart, started_ago, state, words):
    s = sd.brain(b, autostart, NOW, NOW - started_ago)
    assert (s.state, s.summary) == (state, words)


def test_voice_output_s_states():
    calls = sd.Outcomes()
    assert sd.voice_output(False, calls, NOW).state == sd.DOWN
    assert sd.voice_output(True, calls, NOW).state == sd.UNKNOWN
    calls.note(True, now=NOW - 10)
    assert sd.voice_output(True, calls, NOW).summary == "Last sentence voiced 10 s ago"
    calls.note(False, now=NOW - 5)
    assert sd.voice_output(True, calls, NOW).state == sd.DEGRADED
    assert sd.voice_output(True, calls, NOW + 600).state == sd.OK      # an old failure
    calls.note(False, now=NOW - 4)
    calls.note(False, now=NOW - 3)
    s = sd.voice_output(True, calls, NOW)
    assert (s.state, s.summary) == (sd.DOWN, "The last 3 sentences could not be voiced")
    calls.note(True, now=NOW - 1)
    assert sd.voice_output(True, calls, NOW).state == sd.DEGRADED


@pytest.mark.parametrize("b, state", [
    (None, sd.UNKNOWN),
    (brain(running=False), sd.UNKNOWN),
    (brain(), sd.UNKNOWN),                                            # not reported
    (brain(mcp_servers=[{"name": "jarvis", "status": "connected"}]), sd.OK),
    (brain(mcp_servers=[{"name": "jarvis", "status": "pending"}]), sd.DEGRADED),
    (brain(mcp_servers=[{"name": "jarvis", "status": "failed"}]), sd.DOWN),
    (brain(mcp_servers=[{"name": "other", "status": "failed"}]), sd.UNKNOWN),
])
def test_the_tool_channel_s_states(b, state):
    assert sd.tool_channel(b, sd.Outcomes(), NOW).state == state


def test_tool_calls_refused_for_their_token_are_down_until_one_is_taken():
    ok = brain(mcp_servers=[{"name": "jarvis", "status": "connected"}])
    calls = sd.Outcomes()
    calls.note(True, now=NOW - 30)
    calls.note(False, "the token does not match", now=NOW - 20)
    s = sd.tool_channel(ok, calls, NOW)
    assert (s.state, s.summary) == (sd.DOWN, "Tool calls are failing: the token does not match")
    calls.note(True, now=NOW - 10)
    s = sd.tool_channel(ok, calls, NOW)
    assert s.state == sd.OK and s.checks[1].detail == "Last tool call 10 s ago"


def test_tool_calls_failing_inside_the_brain_put_the_brain_down_until_one_is_taken():
    ok = brain(mcp_servers=[{"name": "jarvis", "status": "connected"}])
    calls = sd.Outcomes()
    assert sd.brain(ok, True, NOW, NOW - 600, calls).state == sd.OK
    calls.note(False, "exec: failed to read code-mode host message", now=NOW - 20)
    s = sd.brain(ok, True, NOW, NOW - 600, calls)
    assert (s.state, s.summary) == (sd.DOWN, "Tool calls are failing: exec: failed to read "
                                             "code-mode host message")
    assert s.checks[0].state == sd.OK and s.checks[2].state == sd.DOWN
    assert sd.tool_channel(ok, calls, NOW).state == sd.DOWN
    calls.note(True, now=NOW - 10)
    assert sd.brain(ok, True, NOW, NOW - 600, calls).state == sd.OK


def test_the_workflow_monitor_s_states():
    from jarvis import workflow_store
    running = SimpleNamespace(state="running")
    quiet = SimpleNamespace(state=workflow_store.SILENT)
    watch = live_beat(sd.WORKFLOW_LOOP, every=30.0)
    assert sd.workflows([], watch, NOW).state == sd.IDLE
    assert sd.workflows([running], watch, NOW).summary == "1 workflow running"
    s = sd.workflows([running, quiet], watch, NOW)
    assert (s.state, s.summary) == (sd.DEGRADED, "Workflows: 1 workflow has gone quiet")
    stopped = live_beat(sd.WORKFLOW_LOOP, every=30.0, ok_ago=200)
    assert sd.workflows([running], stopped, NOW).state == sd.DOWN


# ── the route, the hint and the voice ────────────────────────────────────

@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    return server_module


def test_the_route_lists_every_subsystem(server):
    from jarvis import run_store
    run_store.create_message("s1", "anthropic", "hammer", "p", "x")
    r = TestClient(server.app).get("/api/diagnostics/subsystems", headers=ORIGIN)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [s["id"] for s in body["subsystems"]]
    assert ids == ["communications", "voice_input", "brain", "voice_output", "tool_channel",
                   "workflows", "folders", "team_server"]
    for s in body["subsystems"]:
        assert s["state"] in ("ok", "degraded", "down", "idle", "unknown") and s["summary"]
        assert all(set(c) == {"id", "name", "state", "detail"} for c in s["checks"])
    comms_row = body["subsystems"][0]
    assert comms_row["messages"][0]["agentName"] == "hammer"
    assert "messages" not in body["subsystems"][1]


def test_the_route_refuses_another_site(server):
    r = TestClient(server.app).get("/api/diagnostics/subsystems",
                                   headers={"Host": "evil.example"})
    assert r.status_code in (400, 403)


def test_a_refused_tool_call_is_noted(server):
    client = TestClient(server.app, headers=ORIGIN)
    client.post("/internal/tool", json={"tool": "x"}, headers={"Authorization": "Bearer wrong"})
    assert list(sd.tool_calls.items)[-1][1:] == (False, "the token does not match")
    token = server.data_paths.ensure_tool_token()
    client.post("/internal/tool", json={"tool": "x"}, headers={"Authorization": f"Bearer {token}"})
    assert list(sd.tool_calls.items)[-1][1] is True


def test_each_synthesis_is_noted(server, monkeypatch):
    results = iter([None, SimpleNamespace(audio=b"mp3", first_byte_sec=0.1, total_sec=0.2)])

    async def synth(text, **kw):
        return next(results)

    monkeypatch.setattr(server.tts, "synthesize_chunk", synth)
    monkeypatch.setattr(server, "FISH_API_KEY", "key")
    monkeypatch.setattr(server, "_append_usage_entry", lambda *a: None)
    asyncio.run(server._synth_for_speech("Good evening."))
    asyncio.run(server._synth_for_speech("Good evening."))
    assert [ok for _, ok, _ in sd.voice_output_calls.items] == [False, True]


def _subsystem(id, state):
    return sd.Subsystem(id, id, state, "why", [sd.Check("watcher", "Session watcher", state, "")])


def test_communications_down_and_back_are_said_once_each(server, monkeypatch):
    """Down is said once however long it lasts, back once when it is no
    longer down, and starting (unknown) is not taken for back. Every change
    of state hints the pages to read again."""
    seq = [sd.OK, sd.DOWN, sd.DOWN, sd.UNKNOWN, sd.DEGRADED, sd.DEGRADED, sd.DOWN]
    looks = iter(seq)
    hints, said, notes = [], [], []

    def subsystems():
        state = next(looks, None)
        if state is None:
            raise RuntimeError("no more looks")
        return [_subsystem("communications", state), _subsystem("brain", sd.OK)]

    async def say(line, note, what):
        said.append(line)
        notes.append(note)

    monkeypatch.setattr(server, "_subsystems", subsystems)
    monkeypatch.setattr(server, "_say_about_message", say)
    monkeypatch.setattr(server, "_send_to_session_clients", hints.append)
    monkeypatch.setattr(server, "SUBSYSTEMS_POLL_SEC", 0.001)

    async def run():
        task = asyncio.create_task(server._watch_subsystems())
        for _ in range(500):
            if "subsystems" in sd.beats and sd.beats["subsystems"].failures:   # run out
                break
            await asyncio.sleep(0.005)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert said == [server.COMMS_DOWN_LINE, server.COMMS_BACK_LINE, server.COMMS_DOWN_LINE]
    assert "what is down: session watcher" in notes[0]
    # ok>down, down>unknown, unknown>degraded, degraded>down: four changes.
    assert hints == [{"type": "subsystems"}] * 4


# ── the team server ──────────────────────────────────────────────────────

def test_team_server_connected_is_online_with_the_team_and_last_contact():
    now = 1_000_000.0
    last = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 20))
    s = sd.team_server({"state": "connected", "server_url": "https://team.example.com",
                        "team": {"name": "Tokens"}, "me": {"login": "tistocks"},
                        "last_contact_at": last}, now)
    assert s.id == "team_server" and s.state == sd.OK
    assert s.summary == "Online: Tokens as tistocks, last contact 20 s ago"


def test_team_server_unreachable_is_degraded_never_down():
    s = sd.team_server({"state": "stale", "server_url": "https://team.example.com"}, 0.0)
    assert s.state == sd.DEGRADED and s.summary.startswith("Can't reach the team server")
    assert sd.team_server({"state": "signed_out"}, 0.0).state == sd.DEGRADED


def test_team_server_not_connected_is_idle():
    assert sd.team_server({}, 0.0).state == sd.IDLE
    assert sd.team_server({"state": "disconnected"}, 0.0).summary == "Not connected"
