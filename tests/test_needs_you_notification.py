"""The macOS notice for a needs-you while he is not engaged here.

A needs-you is an attention item, never spoken by the server. Whether he is
here is his recent interaction with this JARVIS (attention.availability),
never a connected page: engaged, the item is on the page and nothing more is
raised; away or unknown, Notification Centre gets it. Neither a finished
session nor a `fresh` one earns an interruption.

No test here posts a real notification — `notifier.notify` is replaced in
every one, and tests/conftest.py blocks the real implementation besides.
"""

import asyncio

import pytest

from tests.attention_fixtures import attention  # noqa: F401  (a fixture)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from jarvis import server as server_module
    importlib.reload(server_module)
    server_module.voice_clients.clear()
    return server_module


def _engaged(monkeypatch, server):
    """He used this JARVIS just now."""
    from jarvis import human_activity
    monkeypatch.setattr(human_activity, "recorder", human_activity.HumanActivity())
    human_activity.recorder.observe("gui")


class FakeSpeech:
    def __init__(self):
        self.calls = []

    async def say(self, text, priority=None, **k):
        self.calls.append((text, priority))


def _event(kind="needs_you", **over):
    session = {"session_id": "s", "voice_name": "chitauri", "project": "chitauri",
               "state": "needs_you", "needs": "permission prompt",
               "needs_a_human_hand": True, "title": "Fix the redirect",
               "summary": "Fix the redirect", "last_text": "Shall I proceed?",
               "steerable": True}
    session.update(over.pop("session", {}))
    return {"kind": kind, "at": 1.0, "session": session, **over}


class _Notifier:
    def __init__(self, ok=True):
        self.calls = []
        self._ok = ok

    async def notify(self, title, message, *, subtitle=""):
        self.calls.append((title, message, subtitle))
        return self._ok


def _fake_notifier(monkeypatch, ok=True):
    from jarvis import notifier
    fake = _Notifier(ok)
    monkeypatch.setattr(notifier, "available", lambda: True)
    monkeypatch.setattr(notifier, "notify", fake.notify)
    return fake


@pytest.mark.asyncio
async def test_a_needs_you_while_he_is_not_engaged_posts_a_notification(wired, monkeypatch):
    server = wired
    speech = FakeSpeech()
    monkeypatch.setattr(server, "speech", speech)
    fake = _fake_notifier(monkeypatch)
    server.voice_clients.add(object())         # a page is open: not a person
    try:
        await server._attend_needs_you(_event())
    finally:
        server.voice_clients.clear()

    assert len(fake.calls) == 1, "nobody is known to be here, so notify"
    assert speech.calls == [], "and nothing is spoken"
    _title, message, subtitle = fake.calls[0]
    assert "chitauri" in message or "chitauri" in subtitle, "it names the session"
    assert "permission" in message, "and says why it is waiting"


@pytest.mark.asyncio
async def test_a_needs_you_while_he_is_engaged_does_not_notify(wired, monkeypatch):
    """He is here: the item is on the page. Do not raise it twice."""
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    fake = _fake_notifier(monkeypatch)
    _engaged(monkeypatch, server)
    await server._attend_needs_you(_event())

    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_failing_notifier_does_not_lose_the_item(wired, monkeypatch, attention):
    server = wired
    monkeypatch.setattr(server, "attention_record", attention)

    from jarvis import notifier

    async def explode(*a, **k):
        raise RuntimeError("osascript is on fire")

    monkeypatch.setattr(notifier, "available", lambda: True)
    monkeypatch.setattr(notifier, "notify", explode)

    await server._attend_needs_you(_event())      # must not raise

    assert [i.kind for i in attention.items()] == ["needs_you"], "the item is still recorded"


@pytest.mark.asyncio
async def test_a_notification_failure_never_reaches_the_watcher(wired, monkeypatch):
    """_on_session_event runs on the loop for the watcher thread. Nothing that
    happens downstream of it may surface as an unhandled task exception."""
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())

    from jarvis import notifier

    async def explode(*a, **k):
        raise RuntimeError("osascript is on fire")

    monkeypatch.setattr(notifier, "available", lambda: True)
    monkeypatch.setattr(notifier, "notify", explode)

    server._on_session_event(_event())
    await asyncio.sleep(0.05)

    for task in list(server._bg_tasks):
        if task.done():
            assert task.exception() is None


@pytest.mark.asyncio
async def test_an_unavailable_notifier_is_not_called(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    fake = _fake_notifier(monkeypatch)
    from jarvis import notifier
    monkeypatch.setattr(notifier, "available", lambda: False)

    await server._attend_needs_you(_event())

    assert fake.calls == []


@pytest.mark.asyncio
async def test_finished_sessions_never_notify(wired, monkeypatch):
    """A notification is an interruption. A finished session has not earned one."""
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    fake = _fake_notifier(monkeypatch)

    server._on_session_event(_event(kind="finished"))
    await asyncio.sleep(0.05)

    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_fresh_session_never_notifies(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    fake = _fake_notifier(monkeypatch)

    server._on_session_event(_event(kind="fresh"))
    await asyncio.sleep(0.05)

    assert fake.calls == []


@pytest.mark.asyncio
async def test_the_session_text_reaches_the_notifier_verbatim(wired, monkeypatch):
    """A session name comes out of somebody else's transcript. It is handed to
    notifier.notify() as an argument, so it arrives unescaped and unspliced —
    notifier.py passes it as argv, never as AppleScript source, and this path
    must not undo that by pre-formatting it into a command."""
    server = wired
    monkeypatch.setattr(server, "speech", FakeSpeech())
    fake = _fake_notifier(monkeypatch)
    payload = '" & (do shell ' + 'script "id") & "'

    await server._attend_needs_you(_event(session={"voice_name": payload}))

    assert len(fake.calls) == 1
    _title, message, subtitle = fake.calls[0]
    assert payload in message or payload in subtitle, \
        "the hostile string arrives verbatim, neither escaped nor spliced"
