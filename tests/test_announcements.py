"""The sessions' routine events (needs you, finished) are attention items,
never spoken by the server: whether anything is said is the one admission
policy's (attention.py), and what is said the brain's digest."""
import pytest

from tests.attention_fixtures import attention, recorded  # noqa: F401  (a fixture)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from jarvis import server as server_module
    importlib.reload(server_module)
    return server_module


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


@pytest.mark.asyncio
async def test_a_session_that_needs_you_is_an_action_item_not_an_interruption(
        wired, monkeypatch, attention):
    server = wired
    speech = FakeSpeech()
    monkeypatch.setattr(server, "speech", speech)
    monkeypatch.setattr(server, "attention_record", attention)

    await server._attend_needs_you(_event())

    assert speech.calls == [], "nothing is spoken"
    (item,) = attention.items()
    assert (item.kind, item.source_id, item.event) == ("needs_you", "s", "needs")
    assert item.facts == {"needs": "permission prompt", "needs_a_human_hand": True}
    assert item.untrusted == {"agent": "chitauri"}, "the name is the session's own words"


@pytest.mark.asyncio
async def test_the_same_needs_twice_is_one_item_and_a_new_need_its_next_revision(
        wired, monkeypatch, attention):
    server = wired
    monkeypatch.setattr(server, "attention_record", attention)
    await server._attend_needs_you(_event())
    await server._attend_needs_you(_event())
    await server._attend_needs_you(_event(session={"needs": "input"}))
    (item,) = attention.items()
    assert item.revision == 2 and item.facts["needs"] == "input"


@pytest.mark.asyncio
async def test_finished_sessions_are_one_item_each_and_nothing_is_said(
        wired, monkeypatch, attention):
    server = wired
    speech = FakeSpeech()
    monkeypatch.setattr(server, "speech", speech)
    monkeypatch.setattr(server, "attention_record", attention)
    monkeypatch.setattr(server, "_spawn", lambda coro: coro.close())

    for sid, name in (("a", "chitauri"), ("b", "hammer"), ("a", "chitauri")):
        server._on_session_event(_event("finished", session={"session_id": sid,
                                                             "voice_name": name}))

    assert speech.calls == []
    assert recorded(attention) == [("session_finished", "a", "finished"),
                                   ("session_finished", "b", "finished")]


@pytest.mark.asyncio
async def test_with_no_record_running_nothing_raises(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "attention_record", None)
    monkeypatch.setattr(server, "speech", None)
    await server._attend_needs_you(_event())


def test_the_event_handler_routes_by_kind(wired, monkeypatch, attention):
    server = wired
    routed = []
    monkeypatch.setattr(server, "attention_record", attention)

    def _capture(coro):
        # Every event also fans out to _broadcast_session_event
        # unconditionally, so which coroutine was spawned is checked directly.
        routed.append(coro.cr_code.co_name)
        coro.close()

    monkeypatch.setattr(server, "_spawn", _capture)

    server._on_session_event(_event("needs_you"))
    server._on_session_event(_event("finished", session={"voice_name": "hammer"}))
    server._on_session_event(_event("delivered", message={"id": 3}))

    assert routed.count("_attend_needs_you") == 1, routed
    assert routed.count("_attend_message") == 1, routed
    assert recorded(attention) == [("session_finished", "s", "finished")]
