"""The conversation floor (server._ConversationFloor): what the attention
policy and the speech scheduler read to know whether the floor is his, the
brain's, the mouth's or free (contract section 7). Read from every page's
turn controller, the recogniser's backlog, the brain's turn and the
scheduler, never from the stand-in it replaced."""
from __future__ import annotations

import types

import pytest

from jarvis import attention, server, transcription_api


class _Turns:
    def __init__(self, state="idle", revision=0):
        self.state, self.revision = state, revision


class _Mouth:
    def __init__(self, speaking=False, in_flight=False):
        self.is_speaking, self._in_flight = speaking, in_flight

    def has_speech_in_flight(self):
        return self._in_flight


class _Capture:
    def __init__(self, waiting):
        self._waiting = waiting

    def backlog(self):
        return self._waiting, 0.0


@pytest.fixture
def floor(monkeypatch):
    monkeypatch.setattr(server, "_voice_turns", {})
    monkeypatch.setattr(server, "speech", _Mouth())
    monkeypatch.setattr(server, "brain_instance", types.SimpleNamespace(current_origin=None))
    monkeypatch.setattr(transcription_api, "service", None)
    return server._ConversationFloor()


def test_free_when_nothing_holds_it(floor):
    assert floor.state() == attention.FLOOR_IDLE and not floor.held()


@pytest.mark.parametrize("state", ["gathering", "commit-pending"])
def test_a_page_s_turn_holds_it(floor, state):
    server._voice_turns["a"] = _Turns("idle")
    server._voice_turns["b"] = _Turns(state)
    assert floor.state() == state and floor.held()


def test_a_recogniser_behind_him_holds_it(floor, monkeypatch):
    monkeypatch.setattr(transcription_api, "service",
                        types.SimpleNamespace(active=_Capture(server.RECOGNITION_BEHIND_SEC + 0.1)))
    assert floor.state() == "gathering" and floor.held()
    monkeypatch.setattr(transcription_api, "service",
                        types.SimpleNamespace(active=_Capture(0.1)))
    assert floor.state() == attention.FLOOR_IDLE


def test_a_committed_turn_or_a_brain_turn_holds_it(floor, monkeypatch):
    server._voice_turns["a"] = _Turns("awaiting-reply")
    assert floor.state() == "awaiting-reply" and floor.held()
    server._voice_turns["a"] = _Turns("idle")
    monkeypatch.setattr(server, "brain_instance", types.SimpleNamespace(current_origin="user"))
    assert floor.state() == "awaiting-reply" and floor.held()


def test_the_mouth_in_use_is_reply_playing_but_not_held_against_itself(floor, monkeypatch):
    """A reply or read-back sounding, queued, or a cancel window open: the
    policy sees reply-playing; the scheduler orders its own speech, so it is
    not told the floor is held."""
    monkeypatch.setattr(server, "speech", _Mouth(in_flight=True))
    assert floor.state() == "reply-playing" and not floor.held()


def test_the_revision_moves_with_any_page_s_activity_and_a_page_leaving(floor):
    first = floor.activity_revision()
    assert floor.activity_revision() == first, "nothing moved"
    turns = _Turns("gathering", revision=1)
    server._voice_turns["a"] = turns
    second = floor.activity_revision()
    assert second > first
    turns.revision = 2
    third = floor.activity_revision()
    assert third > second
    del server._voice_turns["a"]
    assert floor.activity_revision() > third


def test_the_policy_and_the_scheduler_read_this_floor():
    import inspect
    source = inspect.getsource(server)
    assert "_StandInTurnState" not in source
    assert "attention_record, conversation_floor, human_activity.recorder.current" in source
    assert "speech.floor_held = conversation_floor.held" in source
