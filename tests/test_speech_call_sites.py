"""Nothing speaks of a routine event but the one digest.

Two checks, so a helper cannot quietly bring direct speech back:

  static    every call that hands words to the voice (`speech.say`,
            `read_aloud`, `read_into`, `narrate`, `begin_turn`, and the
            server's `_say_notice`) anywhere in jarvis/ is in one of the
            approved entry points below, each with the reason it may speak.
            A new call site fails here until it is classified, and a routine
            producer is never one: it records an attention item.
  runtime   every routine producer is driven with a voice that records any
            call at all; none may reach it, and each leaves its item.

Brain health and turn faults are shown (the brain's attention item, a
failed reply's reason) and a shell command's or keypress's outcome after
its exact read-back is an attention item: none of them is spoken.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.attention_fixtures import attention, recorded  # noqa: F401  (a fixture)
from tests.fixtures.waiting import until_async

ROOT = Path(__file__).resolve().parent.parent / "jarvis"
SINKS = {"say", "read_aloud", "read_into", "narrate", "begin_turn"}

DIGEST = "the one digest the admission policy allows"
REQUESTED = "the answer to something he asked for himself"
CONFIRMATION = "a yes-or-no question he must answer (clearing, compaction)"
REPLY = "the brain's own reply to his turn"
READBACK = "an exact read-back he asked for, or must hear before it acts"
NARRATION = "a story he asked to be told"
REPLAY = "what was already said, said again at his request"

# (file, enclosing function, method) -> why it may speak.
APPROVED = {
    ("server.py", "_digest_speak", "say"): DIGEST,
    ("server.py", "_say_notice", "say"): REQUESTED,
    ("server.py", "<module>", "_say_notice"): REQUESTED,           # the language route
    ("server.py", "_update_by_voice", "_say_notice"): REQUESTED,
    ("server.py", "_switch_language_by_voice", "say"): REQUESTED,
    ("server.py", "voice_handler", "say"): REPLAY,                  # nothing to replay
    ("speech.py", "SpeechScheduler.replay_last", "say"): REPLAY,
    ("server.py", "_clear_question_expired", "say"): CONFIRMATION,
    ("server.py", "_ask_to_clear", "say"): CONFIRMATION,
    ("server.py", "_take_clear_answer", "say"): CONFIRMATION,
    ("server.py", "_start_fresh", "say"): CONFIRMATION,
    ("server.py", "_ask_to_rotate", "say"): CONFIRMATION,
    ("server.py", "_take_rotation_answer", "say"): CONFIRMATION,
    ("server.py", "_handle_utterance", "begin_turn"): REPLY,
    ("server.py", "tool_read_capture", "read_into"): READBACK,
    ("server.py", "tool_read_capture", "read_aloud"): READBACK,
    ("server.py", "api_capture_read", "read_aloud"): READBACK,
    ("story_teller.py", "StoryTeller._tell", "narrate"): NARRATION,
    ("server.py", "_perform_command", "say"): READBACK,
    ("server.py", "_perform_dialog", "say"): READBACK,
    # His Read on a session message's or an agent's answer's Updates entry,
    # or his asking the brain to read it aloud: that one message, never a
    # digest of others.
    ("server.py", "_speak_message", "say"): REQUESTED,
}


def _speaks_to_the_voice(call: ast.Call, path: Path, inside: str) -> str | None:
    """The sink a call hands words to, or None."""
    f = call.func
    if isinstance(f, ast.Name) and f.id == "_say_notice":
        return "_say_notice"
    if not isinstance(f, ast.Attribute) or f.attr not in SINKS:
        return None
    target = ast.unparse(f.value)
    if target == "speech" or target.endswith(".speech") or target.endswith("_speech"):
        return f.attr
    if path.name == "speech.py" and target == "self" and inside.startswith("SpeechScheduler"):
        return f.attr
    return None


def call_sites() -> list[tuple[str, str, str, int]]:
    """(file, enclosing function, sink, line) for every call to the voice."""
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        stack: list[str] = []

        def visit(node):
            named = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if named:
                stack.append(node.name)
            if isinstance(node, ast.Call):
                inside = ".".join(stack) or "<module>"
                sink = _speaks_to_the_voice(node, path, inside)
                if sink:
                    found.append((path.name, _where(path, stack), sink, node.lineno))
            for child in ast.iter_child_nodes(node):
                visit(child)
            if named:
                stack.pop()

        visit(tree)
    return found


def _where(path: Path, stack: list[str]) -> str:
    """The enclosing function as the table names it: a class's method as
    Class.method, a nested function by its outermost function."""
    if not stack:
        return "<module>"
    if len(stack) >= 2 and stack[0][:1].isupper():
        return f"{stack[0]}.{stack[1]}"
    return stack[0]


def test_every_call_to_the_voice_is_an_approved_entry_point():
    unapproved = [site for site in call_sites() if site[:3] not in APPROVED]
    assert unapproved == [], (
        "speech outside the approved entry points: a routine event is an attention "
        f"item (server._attend), never a line of its own: {unapproved}")


def test_no_approved_entry_point_is_stale():
    """An entry point no longer used is taken out of the table, so it cannot
    be reused unnoticed."""
    used = {site[:3] for site in call_sites()}
    assert set(APPROVED) - used == set()


def test_nothing_is_said_at_the_announcement_priority_or_batched_but_the_digest():
    """No routine line is queued as a batched (LOW) or announcement-labelled
    utterance anywhere but the digest and the answer to his own request
    (`_say_notice`, which waits for a pause the same way)."""
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.name == "speech.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for call in ast.walk(node):
                    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr == "say"):
                        continue
                    text = ast.unparse(call)
                    if ("Priority.LOW" in text or "'announcement'" in text
                            or '"announcement"' in text) and node.name not in (
                                "_digest_speak", "_say_notice"):
                        offenders.append((path.name, node.name, call.lineno))
    assert offenders == []


# ── runtime: every routine producer, with a voice that records anything ──────

class Sink:
    """A voice that takes nothing: every call to it is recorded."""

    def __init__(self):
        self.calls: list[str] = []
        self.is_speaking = False

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def record(*a, **k):
            self.calls.append(name)

            async def nothing():
                return None
            return nothing()
        return record


@pytest.fixture
def wired(monkeypatch, tmp_path, attention):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from jarvis import server as server_module
    importlib.reload(server_module)
    server_module.run_store.init_db()
    sink = Sink()
    monkeypatch.setattr(server_module, "speech", sink)
    monkeypatch.setattr(server_module, "attention_record", attention)
    monkeypatch.setattr(server_module, "_snapshot_or_empty",
                        lambda: SimpleNamespace(sessions=[]))
    return server_module, sink, attention


async def _drain(server):
    for _ in range(10):
        pending = [t for t in server._bg_tasks if not t.done()]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)


def _session_event(kind, **message):
    return {"kind": kind, "at": 1.0,
            "session": {"session_id": "s1", "voice_name": "hammer", "project": "jarvis",
                        "needs": "permission prompt", "needs_a_human_hand": False},
            "message": {"id": 9, "body": "go on", **message}}


@pytest.mark.asyncio
async def test_no_routine_producer_reaches_the_voice(wired, monkeypatch):
    server, sink, attention = wired
    from jarvis import session_needs, team_reminder, transmission
    monkeypatch.setattr(team_reminder, "_said", False)
    monkeypatch.setattr(server, "_message_went", lambda *a: asyncio.sleep(0))

    for kind in ("sent", "send_failed", "delivered", "undelivered", "answered",
                 "needs_you", "finished", "fresh"):
        server._on_session_event(_session_event(kind))
    for n, status in enumerate(("succeeded", "failed", "timed_out", "cancelled")):
        server._on_run_event({"type": "run_finished", "run": {
            "id": f"r{n}", "project_name": "jarvis", "status": status, "origin": "voice"}})
    server._on_run_event({"type": "run_finished", "run": {
        "id": "p1", "project_name": "pkg", "status": "succeeded", "package_id": "gone"}})
    server._attend_board(session_needs.Board(project="jarvis", text="- one\n- two", mtime=1))
    server._attend_session_messages([SimpleNamespace(id=4, agent_name="hammer")])
    w = SimpleNamespace(id="wf-1", name="build", state="succeeded")
    for kind in ("phase_started", "decision_needed", "blocker_raised", "workflow_finished",
                 "task_started"):
        server._workflow_recorded(w, SimpleNamespace(kind=kind, item="phase-2", summary="Which?",
                                                    detail={}, reporter=""))
    staged = server._StagedSteer(session_id="s1", voice_name="hammer", project="jarvis",
                                 prompt="go", socket_path=None, message_id=11)
    destination = server._AgentDestination(staged)
    await destination.report_queued(sink, "is mid-turn")
    await destination.report_outcome(sink, "not_live")
    await team_reminder.remind_once(attention, lambda: True, settle_sec=0)
    reminder = asyncio.create_task(team_reminder.remind_once(
        SimpleNamespace(record=attention.record, resolve=attention.resolve),
        lambda: False, settle_sec=0, watch_sec=0.01))
    assert await until_async(lambda: attention.get(team_reminder.KIND, team_reminder.SOURCE)), \
        "the reminder was never recorded"
    reminder.cancel()
    await _drain(server)
    assert isinstance(destination, transmission.Destination)

    assert sink.calls == [], f"a routine producer reached the voice: {sink.calls}"
    kinds = {kind for kind, _, _ in recorded(attention)}
    assert kinds == {"agent_message", "needs_you", "session_finished", "run", "package_run",
                     "needs_board", "session_message", "workflow", "sign_in"}
    assert ("run", "r3", "cancelled") not in recorded(attention)
    queued = attention.get("agent_message", "11")
    assert [h["event"] for h in attention.history(queued.id)] == ["not_live"], \
        "queued is never an update"
    message = attention.get("agent_message", "9")
    assert {h["event"] for h in attention.history(message.id)}.isdisjoint(
        {"sent", "delivered"}), "sent and delivered are never an update"


@pytest.mark.asyncio
async def test_a_page_connecting_is_not_greeted(wired):
    """No greeting per connection: the voice handler says nothing on its
    own when a page connects (only the opening frames go out)."""
    server, sink, attention = wired
    source = Path(server.__file__).read_text()
    assert "_greeting" not in source and "Good morning, sir" not in source


@pytest.mark.asyncio
async def test_a_digest_turn_may_use_no_tool(wired, monkeypatch):
    server, sink, attention = wired
    from fastapi.testclient import TestClient
    from jarvis import attention as attention_module, data_paths

    monkeypatch.setattr(server, "brain_instance", SimpleNamespace(
        current_origin=attention_module.DIGEST_ORIGIN, turn_untrusted_source=None,
        turn_is_tainted=False))
    token = data_paths.ensure_tool_token()
    client = TestClient(server.app, base_url="http://127.0.0.1:8340")   # no lifespan
    for tool in ("attention_updates", "list_messages", "steer_session"):
        r = client.post("/internal/tool", json={"tool": tool, "arguments": {}},
                        headers={"Authorization": f"Bearer {token}"})
        body = r.json()
        assert body.get("ok") is False and "not_allowed_in_digest" in body["text"], (tool, body)
