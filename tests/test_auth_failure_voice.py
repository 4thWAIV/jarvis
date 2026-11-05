"""What JARVIS shows (and logs) when the brain has given up.

"My language systems are down, sir." was true but useless: it named neither
cause nor remedy, and an expired Claude Code login cost the user twenty
minutes to find what was plainly in the server log. Now an auth-classified
failure (brain.py's `failure_reason == "auth"`) is the brain's attention
item `sign_in` and a failed reply's reason `sign_in`, which the page words
with the remedy, and the remedy is logged at ERROR level. Nothing of it is
spoken: a brain that is down cannot word it, and a canned line in its place
is the chatter the quiet-conversation plan removed.

No test here spawns a real `claude` or a real brain: brain_instance and
speech are both fakes, following the pattern in test_run_announcements.py.
"""

import importlib

import pytest


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    from jarvis import server as server_module
    importlib.reload(server_module)
    return server_module


class FakeSpeech:
    def __init__(self):
        self.calls = []

    async def say(self, text, priority=None, **k):
        self.calls.append((text, priority))


class FakeBrain:
    """Only the attributes _handle_utterance / _on_brain_state actually touch."""
    def __init__(self, ready=False, failed=False, failure_reason=None):
        self.ready = ready
        self.failed = failed
        self.failure_reason = failure_reason


# --- _on_brain_state: the "failed" event ------------------------------------

class FakeRecord:
    def __init__(self):
        self.items = []

    def record(self, kind, source_id, event, facts=None, untrusted=None, audible=True):
        self.items.append((kind, source_id, event, audible))

    def resolve(self, kind, source_id):
        self.items.append((kind, source_id, "resolved", None))


@pytest.mark.parametrize("info, event", [({"failure_reason": "auth"}, "sign_in"), ({}, "down")])
@pytest.mark.asyncio
async def test_a_failed_brain_is_an_attention_item_and_says_nothing(wired, monkeypatch, info, event):
    server = wired
    speech = FakeSpeech()
    record = FakeRecord()
    monkeypatch.setattr(server, "speech", speech)
    monkeypatch.setattr(server, "attention_record", record)

    await server._on_brain_state("failed", info)
    await server._on_brain_state("ready", {})

    assert speech.calls == []
    assert record.items == [("brain", "brain", event, False), ("brain", "brain", "resolved", None)]


@pytest.mark.asyncio
async def test_on_brain_state_never_raises_with_no_speech(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "speech", None)
    await server._on_brain_state("failed", {"failure_reason": "auth"})
    await server._on_brain_state("failed", {})
    await server._on_brain_state("restarting", {})
    await server._on_brain_state("rate_limited", {"resets_at": 0})


@pytest.mark.asyncio
async def test_auth_failure_logs_the_remedy_at_error_level(wired, monkeypatch, caplog):
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    with caplog.at_level("ERROR", logger="jarvis"):
        await server._on_brain_state("failed", {"failure_reason": "auth"})
    messages = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert any("claude" in m.lower() and "log in" in m.lower() for m in messages)


@pytest.mark.asyncio
async def test_auth_failure_logs_the_remedy_even_with_no_speech(wired, monkeypatch, caplog):
    """The whole point: visible in the terminal even if TTS itself is down."""
    server = wired
    monkeypatch.setattr(server, "speech", None)
    with caplog.at_level("ERROR", logger="jarvis"):
        await server._on_brain_state("failed", {"failure_reason": "auth"})
    messages = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert any("claude" in m.lower() and "log in" in m.lower() for m in messages)


@pytest.mark.asyncio
async def test_non_auth_failure_does_not_log_the_auth_remedy(wired, monkeypatch, caplog):
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    with caplog.at_level("ERROR", logger="jarvis"):
        await server._on_brain_state("failed", {})
    messages = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert not any("run `claude`" in m for m in messages)


# --- _handle_utterance: a turn attempted while the brain is not ready -------

class FakeMouth(FakeSpeech):
    """Enough of the scheduler for a reply that is shown and not spoken."""

    def __init__(self):
        super().__init__()
        self._next = 1

    def begin_turn(self):
        from types import SimpleNamespace
        u = SimpleNamespace(id=self._next)
        self._next += 1
        return u

    async def end_turn(self, utt):
        pass


@pytest.mark.parametrize("failure_reason, reason", [("auth", "sign_in"), (None, "down")])
@pytest.mark.asyncio
async def test_a_turn_while_the_brain_is_down_shows_why_on_its_reply(wired, monkeypatch,
                                                                      failure_reason, reason):
    server = wired
    mouth = FakeMouth()
    frames = []
    monkeypatch.setattr(server, "speech", mouth)
    monkeypatch.setattr(server, "_queue_voice", frames.append)
    monkeypatch.setattr(server, "brain_instance",
                        FakeBrain(ready=False, failed=True, failure_reason=failure_reason))

    await server._handle_utterance("are you there")

    assert mouth.calls == [], "nothing is spoken in its place"
    done = [f for f in frames if f.get("type") == "reply" and f.get("done")]
    assert done and (done[-1]["outcome"], done[-1]["reason"]) == ("failed", reason)


@pytest.mark.asyncio
async def test_handle_utterance_never_raises_with_no_speech(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "speech", None)
    monkeypatch.setattr(server, "brain_instance",
                        FakeBrain(ready=False, failed=True, failure_reason="auth"))
    await server._handle_utterance("are you there")   # must not raise
