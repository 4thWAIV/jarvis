"""Server-side rotation: asked first, done at a pause, never silently.

Three things are being pinned here. First, rotation is the user's call: when
the brain reports its budget is spent, JARVIS asks, and nothing rotates until
he says yes. Second, a generation is never replaced without a trace: the
outgoing brain is asked for its own handover, and if it will not or cannot
give one, the server writes a minimal entry itself and rotates anyway. Third,
the request for that handover runs with origin="system", so the acting-tool
gate refuses any write the brain might attempt while answering it.

No test here spawns a real `claude`: the brain is a fake throughout.
"""

import asyncio
import types

import pytest


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import importlib
    from jarvis import server as server_module
    importlib.reload(server_module)
    return server_module


@pytest.fixture
def agreed(wired, monkeypatch):
    """The user has already said yes to the rotation being tested."""
    monkeypatch.setattr(wired, "_rotation_agreed", True)
    return wired


class _Brain:
    def __init__(self, pending=True, rotates=True):
        self.rotation_pending = pending
        self.turns_since_rotation = 0
        self.config = types.SimpleNamespace(context_budget=900000)
        self.rotated_with = None
        self.rotations = 0
        self.asked = []
        self.ready = True
        # The real Brain exposes this: None means no turn is in flight, which
        # is what makes the moment a pause.
        self.current_origin = None
        self.stopped = False
        self._rotates = rotates

    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        self.asked.append((text, origin))
        from jarvis import brain
        return brain.TurnResult(origin, "We fixed chitauri and Tony chose Postgres.",
                                "result")

    async def rotate(self, handover=None):
        self.rotations += 1
        if not self._rotates:
            return False
        self.rotated_with = handover
        self.rotation_pending = False
        return True

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_a_pending_rotation_asks_for_a_journal_then_rotates(agreed, monkeypatch):
    server = agreed
    b = _Brain()
    monkeypatch.setattr(server, "brain_instance", b)

    await server._maybe_rotate()

    assert b.asked, "the outgoing brain is asked for a handover"
    assert b.asked[0][1] == "system", "the journal request is not a user turn"
    assert "chitauri" in (b.rotated_with or "")

    from jarvis import jarvis_memory as jm
    assert "chitauri" in (jm.latest_journal() or ""), "the journal is on disk"


class _Speech:
    """Records what was said, without a real SpeechScheduler: this is
    testing that the server WIRES the announcement, not the scheduler's own
    behaviour (that lives in test_speech.py against the real thing)."""

    def __init__(self):
        self.said = []

    async def say(self, text, priority=None, immediate=None, label=None):
        self.said.append((text, priority, immediate))


@pytest.mark.asyncio
async def test_a_rotation_is_shown_on_the_orb_and_never_spoken(agreed, monkeypatch):
    """The user sees the brain process swap underneath him ("that's weird why
    did they just randomly restart"). It was once announced with a spoken
    line afterwards; he found that annoying the moment he knew what it was,
    and he was right. So the orb carries a "compacting" state for the
    duration, is put back to idle when it is over, and nothing is said."""
    server = agreed
    b = _Brain()
    sp = _Speech()
    frames = []

    async def emit(msg):
        frames.append(msg)

    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", sp)
    monkeypatch.setattr(server, "_voice_emit", emit)

    await server._maybe_rotate()

    assert sp.said == [], f"nothing is spoken about a rotation, but heard: {sp.said}"
    states = [f["state"] for f in frames if f.get("type") == "status"]
    assert states[0] == "compacting", states
    assert states[-1] == "idle", "the orb must be put back when it is over"


@pytest.mark.asyncio
async def test_a_failed_rotation_says_nothing(agreed, monkeypatch):
    """rotate() returning False means the old brain is still serving --
    nothing happened from the user's point of view, so nothing is said."""
    server = agreed
    b = _Brain(rotates=False)
    sp = _Speech()
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", sp)

    await server._maybe_rotate()

    assert sp.said == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rotates", [True, False])
async def test_only_a_failed_rotation_is_logged_as_not_happening(agreed, monkeypatch,
                                                                caplog, rotates):
    server = agreed
    monkeypatch.setattr(server, "brain_instance", _Brain(rotates=rotates))

    with caplog.at_level("WARNING", logger=server.log.name):
        await server._maybe_rotate()

    assert ("rotation did not happen" in caplog.text) is not rotates


@pytest.mark.asyncio
async def test_rotation_without_a_speech_scheduler_does_not_raise(agreed, monkeypatch):
    """Boot order: rotation logic must tolerate speech being None (as it is
    before start_brain_and_speech has run)."""
    server = agreed
    b = _Brain()
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", None)

    await server._maybe_rotate()   # must not raise

    assert b.rotated_with is not None


@pytest.mark.asyncio
async def test_nothing_happens_when_no_rotation_is_pending(wired, monkeypatch):
    server = wired
    b = _Brain(pending=False)
    monkeypatch.setattr(server, "brain_instance", b)

    await server._maybe_rotate()

    assert b.rotated_with is None and b.asked == []


@pytest.mark.asyncio
async def test_a_brain_that_will_not_write_a_journal_still_rotates(agreed, monkeypatch):
    """A generation must never vanish without a trace, and a silent brain must
    not block rotation forever."""
    server = agreed
    b = _Brain()

    async def refuse(text, origin="user", on_delta=None):
        from jarvis import brain
        return brain.TurnResult(origin, "", "timeout")

    b.turn = refuse
    monkeypatch.setattr(server, "brain_instance", b)

    await server._maybe_rotate()

    from jarvis import jarvis_memory as jm
    assert jm.latest_journal(include_placeholders=True) is not None, \
        "the server wrote a minimal entry"
    assert jm.latest_journal() is None, \
        "but it is a tombstone, not something to hand the next generation"
    assert b.rotation_pending is False, "rotation happened anyway"


@pytest.mark.asyncio
async def test_an_error_turn_is_not_persisted_as_a_handover(agreed, monkeypatch):
    """A failed turn may still carry the CLI's error text. That is not a
    handover and must not be fed to the next generation as one."""
    server = agreed
    b = _Brain()

    async def errored(text, origin="user", on_delta=None):
        from jarvis import brain
        return brain.TurnResult(origin, "API Error: overloaded_error", "error")

    b.turn = errored
    monkeypatch.setattr(server, "brain_instance", b)

    await server._maybe_rotate()

    assert b.rotated_with is None, "no handover carried forward"
    assert b.rotation_pending is False, "but it still rotated"
    from jarvis import jarvis_memory as jm
    assert "API Error" not in (jm.latest_journal() or "")


@pytest.mark.asyncio
async def test_rotation_waits_while_another_turn_is_in_flight(agreed, monkeypatch):
    """A pause means nothing is being served. Mid-conversation is not a pause."""
    server = agreed
    b = _Brain()
    b.current_origin = "user"
    monkeypatch.setattr(server, "brain_instance", b)

    await server._maybe_rotate()

    assert b.asked == [] and b.rotations == 0
    assert b.rotation_pending is True, "still pending, for the next real pause"


@pytest.mark.asyncio
async def test_a_long_wait_never_forces_a_rotation_mid_conversation(agreed, monkeypatch):
    """There is no forced rotation any more: however long a rotation has
    waited, it still waits for a real pause."""
    server = agreed
    b = _Brain()
    b.current_origin = "user"
    b.turns_since_rotation = 500
    monkeypatch.setattr(server, "brain_instance", b)

    await server._maybe_rotate()

    assert b.rotations == 0 and b.rotation_pending is True


@pytest.mark.asyncio
async def test_two_pauses_at_once_produce_one_rotation(agreed, monkeypatch):
    """_handle_utterance runs as a task per utterance, so two can reach the
    pause together. That must not buy two handovers and two process swaps."""
    server = agreed
    b = _Brain()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(text, origin="user", on_delta=None):
        b.asked.append((text, origin))
        started.set()
        await release.wait()
        from jarvis import brain
        return brain.TurnResult(origin, "handover", "result")

    b.turn = slow
    monkeypatch.setattr(server, "brain_instance", b)

    first = asyncio.create_task(server._maybe_rotate())
    # Bounded: if the handover is never asked for at all, this test must fail
    # on its assertions rather than hang the suite.
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass
    second = asyncio.create_task(server._maybe_rotate())
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=5.0)

    assert len(b.asked) == 1, "the brain is asked once, not twice"
    assert b.rotations == 1, "the process is swapped once, not twice"


@pytest.mark.asyncio
async def test_a_failed_rotation_does_not_re_ask_at_every_pause(agreed, monkeypatch):
    """rotate() returning False leaves rotation_pending True. Retrying is
    right; spending another brain turn and another journal entry on every
    utterance until it succeeds is not."""
    server = agreed
    b = _Brain(rotates=False)
    monkeypatch.setattr(server, "brain_instance", b)

    await server._maybe_rotate()
    await server._maybe_rotate()
    await server._maybe_rotate()

    assert len(b.asked) == 1, "the handover already paid for is reused"
    assert b.rotations == 3, "but rotation is still retried at each pause"


@pytest.mark.asyncio
async def test_a_journal_write_failure_does_not_stop_the_rotation(agreed, monkeypatch):
    """Journalling is bookkeeping. A read-only disk must not be able to pin the
    context window open."""
    server = agreed
    b = _Brain()
    monkeypatch.setattr(server, "brain_instance", b)

    def boom(text, reason="shutdown"):
        raise OSError("read-only file system")

    monkeypatch.setattr(server.jarvis_memory, "write_journal", boom)

    await server._maybe_rotate()

    assert b.rotation_pending is False


@pytest.mark.asyncio
async def test_shutdown_writes_a_journal(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "brain_instance", _Brain(pending=False))
    monkeypatch.setattr(server, "speech", None)

    await server.stop_brain_and_speech()

    from jarvis import jarvis_memory as jm
    assert jm.latest_journal() is not None


@pytest.mark.asyncio
async def test_shutdown_journals_even_when_the_brain_never_started(wired, monkeypatch):
    """Autostart off, or a brain that failed to boot: there is still a shutdown
    to record, and the entry is the only trace of it."""
    server = wired
    monkeypatch.setattr(server, "brain_instance", None)
    monkeypatch.setattr(server, "speech", None)

    await server.stop_brain_and_speech()

    from jarvis import jarvis_memory as jm
    assert jm.latest_journal(include_placeholders=True) is not None
    assert jm.latest_journal() is None, "a tombstone is not a handover"


@pytest.mark.asyncio
async def test_a_journal_failure_cannot_prevent_shutdown(wired, monkeypatch):
    server = wired
    b = _Brain(pending=False)
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", None)

    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(server.jarvis_memory, "write_journal", boom)

    await server.stop_brain_and_speech()

    assert b.stopped, "the brain was still stopped"
    assert server.brain_instance is None


@pytest.mark.asyncio
async def test_a_wedged_brain_cannot_hold_shutdown_open(wired, monkeypatch):
    """The brain's own turn timeout is 90s. Shutdown does not wait that long."""
    server = wired
    b = _Brain(pending=False)

    async def never(text, origin="user", on_delta=None):
        await asyncio.sleep(3600)

    b.turn = never
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", None)
    monkeypatch.setattr(server, "SHUTDOWN_JOURNAL_TIMEOUT", 0.05)

    await asyncio.wait_for(server.stop_brain_and_speech(), timeout=5.0)

    from jarvis import jarvis_memory as jm
    assert jm.latest_journal(include_placeholders=True) is not None
    assert jm.latest_journal() is None, "a tombstone is not a handover"
    assert b.stopped


# ---------------------------------------------------------------------------
# Boot: the other end of the handover. A restart must pick up where the last
# generation left off, and be told who is working right now.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_boot_wires_the_brain_to_the_active_projects(wired, monkeypatch):
    server = wired
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")     # never spawn a real claude

    await server.start_brain_and_speech()
    try:
        assert server.brain_instance is not None
        assert server.brain_instance.active_projects is server._active_project_names
    finally:
        await server.stop_brain_and_speech()


def test_active_project_names_is_empty_before_the_watcher_polls(wired, monkeypatch):
    """Boot order puts the brain before the watcher, so this must degrade to
    an empty list rather than raise."""
    server = wired
    monkeypatch.setattr(server, "session_watcher", None)
    assert server._active_project_names() == []


def test_active_project_names_skips_finished_and_never_started_sessions(wired,
                                                                        monkeypatch):
    """`gone` conversations linger in the snapshot for ten minutes so a
    completion can still be announced; a `fresh` window has never been
    prompted. Neither is work in progress."""
    from jarvis import session_watch
    server = wired

    def state(project, st):
        return session_watch.SessionState(
            session_id=f"{project}-{st}", project=project, cwd="/tmp", state=st)

    class _Watcher:
        snapshot = session_watch.Snapshot(sessions=[
            state("chitauri", session_watch.WORKING),
            state("jarvis", session_watch.NEEDS_YOU),
            state("old-thing", session_watch.GONE),
            state("never-used", session_watch.FRESH),
        ])

    monkeypatch.setattr(server, "session_watcher", _Watcher())

    assert server._active_project_names() == ["chitauri", "jarvis"]


# ── the pause the user sits through ─────────────────────────────────────────
# Collecting a handover and swapping the process takes seconds during which
# nothing answers. The line spoken afterwards explains it too late: by then
# the user has already watched a dead orb and assumed a crash.

@pytest.mark.asyncio
async def test_the_pause_is_announced_before_it_starts(agreed, monkeypatch):
    server = agreed
    frames = []

    async def _emit(msg):
        frames.append(dict(msg))

    monkeypatch.setattr(server, "brain_instance", _Brain())
    monkeypatch.setattr(server, "speech", _Speech())
    monkeypatch.setattr(server, "_voice_emit", _emit)

    await server._maybe_rotate()

    notices = [f for f in frames if f.get("type") == "notice"]
    assert notices, "the user was given no warning at all"
    assert notices[0]["text"] == server.ROTATION_BUSY_LINE
    assert notices[-1]["text"] == "", "the banner must be cleared afterwards"


@pytest.mark.asyncio
async def test_the_busy_banner_is_shown_and_never_spoken(agreed, monkeypatch):
    """The banner is for the screen. Reading it aloud would be the very
    announcement the user asked not to hear."""
    server = agreed
    sp = _Speech()
    monkeypatch.setattr(server, "brain_instance", _Brain())
    monkeypatch.setattr(server, "speech", sp)
    monkeypatch.setattr(server, "_voice_emit", lambda msg: asyncio.sleep(0))

    await server._maybe_rotate()

    spoken = [t for t, _p, _i in sp.said]
    assert server.ROTATION_BUSY_LINE not in spoken
    assert spoken == []


@pytest.mark.asyncio
async def test_a_client_that_has_gone_away_cannot_stop_a_rotation(agreed, monkeypatch):
    """The notice is a courtesy; the rotation is not optional."""
    server = agreed
    b = _Brain()

    async def _boom(msg):
        raise ConnectionError("no voice client connected")

    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", _Speech())
    monkeypatch.setattr(server, "_voice_emit", _boom)

    await server._maybe_rotate()

    assert b.rotations == 1, "the rotation must happen whether or not anyone is listening"


# ── asking first ────────────────────────────────────────────────────────────
# The user was angry to find his context replaced without his say-so. A spent
# budget now only earns a question; the rotation waits for his yes.

def _spoken(sp):
    return [t for t, _p, _i in sp.said]


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_spent_budget_asks_and_does_not_rotate(wired, monkeypatch):
    server = wired
    b = _Brain()
    sp = _Speech()
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", sp)

    await server._maybe_rotate()

    assert _spoken(sp) == [server._rotation_question_line(900000)]
    assert "900 thousand tokens" in _spoken(sp)[0]
    assert b.rotations == 0 and b.asked == [], "no handover, no swap, before a yes"
    assert b.rotation_pending is True


@pytest.mark.asyncio
async def test_the_question_is_not_repeated_at_every_pause(wired, monkeypatch):
    server = wired
    b = _Brain()
    sp = _Speech()
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", sp)

    await server._maybe_rotate()
    assert server._take_rotation_answer("tell me about the weather") is False
    b.turns_since_rotation = server.ROTATION_ASK_AGAIN_AFTER_TURNS - 1
    await server._maybe_rotate()
    assert len(sp.said) == 1, "asked once, then left alone"

    b.turns_since_rotation = server.ROTATION_ASK_AGAIN_AFTER_TURNS
    await server._maybe_rotate()
    assert len(sp.said) == 2, "asked again once enough turns have passed"
    assert b.rotations == 0


@pytest.mark.asyncio
async def test_yes_rotates(wired, monkeypatch):
    server = wired
    b = _Brain()
    sp = _Speech()
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", sp)
    monkeypatch.setattr(server, "_voice_emit", lambda msg: asyncio.sleep(0))

    await server._maybe_rotate()
    assert server._take_rotation_answer("Yes, go ahead.") is True, \
        "the answer is consumed, not sent to the brain"
    await _settle()

    assert b.rotations == 1 and b.rotation_pending is False
    assert server._rotation_agreed is False, "the next rotation needs its own yes"


@pytest.mark.asyncio
async def test_no_does_not_rotate(wired, monkeypatch):
    server = wired
    b = _Brain()
    sp = _Speech()
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", sp)

    await server._maybe_rotate()
    assert server._take_rotation_answer("No, not now.") is True
    await _settle()
    await server._maybe_rotate()

    assert b.rotations == 0 and b.rotation_pending is True
    assert _spoken(sp)[-1] == server.ROTATION_DECLINED_LINE


@pytest.mark.asyncio
async def test_an_unrelated_reply_is_served_and_counts_as_not_yet(wired, monkeypatch):
    server = wired
    b = _Brain()
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", _Speech())

    await server._maybe_rotate()

    assert server._take_rotation_answer("What's running right now?") is False
    assert server._take_rotation_answer("yes") is False, "the question is closed"
    await _settle()
    assert b.rotations == 0


@pytest.mark.asyncio
async def test_nothing_is_taken_as_an_answer_when_nothing_was_asked(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "brain_instance", _Brain())

    assert server._take_rotation_answer("yes") is False


@pytest.mark.asyncio
async def test_a_fresh_start_closes_the_question(wired, monkeypatch):
    server = wired
    b = _Brain()
    monkeypatch.setattr(server, "brain_instance", b)
    monkeypatch.setattr(server, "speech", _Speech())

    await server._maybe_rotate()
    await server._start_fresh()

    assert server._take_rotation_answer("yes") is False
    assert b.rotations == 1, "only the fresh start itself"


@pytest.mark.parametrize("text, answer", [
    ("yes", True), ("Yeah.", True), ("Sure, compact it.", True),
    ("Go ahead", True), ("do it", True), ("okay", True),
    ("no", False), ("Not now", False), ("yes, but not now", False),
    ("Don't", False), ("wait", False), ("hold on", False),
    ("what time is it", None), ("", None),
    ("okay so what is the weather like today", None),
])
def test_the_answer_is_read_plainly(wired, text, answer):
    assert wired._rotation_answer(text) is answer

