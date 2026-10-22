"""The user can throw away a tainted generation.

A memory writer is gated on the whole generation's taint, not the turn's,
because what it writes becomes trusted text in every later generation. The
refusal told the user to say it again "in a fresh conversation" — and there
was no way for him to start one. Live, on 2026-09-06, that cost him: he showed
JARVIS a website on his second screen, then spent four turns trying to have a
fact saved and was refused every time.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import server


def test_only_the_exact_command_is_recognised():
    for said in ("start fresh", "Start Fresh.", "Jarvis start fresh",
                 "jarvis, start fresh!", "  START   fresh  "):
        assert server._is_fresh_start(said), said


def test_nothing_else_clears_his_context():
    """This throws away everything he knows. A misheard sentence containing
    the words once wiped a live conversation."""
    for said in ("start over", "new conversation", "okay start fresh please",
                 "okay — start fresh, please", "clear your head",
                 "clear your context", "fresh conversation",
                 "let's start a fresh conversation", "forget this conversation",
                 "i want to start fresh on the build", "start fresh now",
                 "jarvis jarvis start fresh", "please jarvis start fresh",
                 "what's running right now", "start the build", "",
                 "a fresh set of eyes on this", "did you clear the error"):
        assert not server._is_fresh_start(said), said


def test_the_line_he_hears_says_it_is_gone():
    assert "Cleared" in server.FRESH_START_LINE


# ── two turns, never one ────────────────────────────────────────────────

import asyncio

import pytest


class _Speech:
    def __init__(self):
        self.said = []

    async def say(self, text, priority=None, label=None):
        self.said.append(text)


class _Brain:
    def __init__(self):
        self.rotations = []

    async def rotate(self, handover=None):
        self.rotations.append(handover)
        return True


@pytest.fixture
def wired(monkeypatch):
    brain, speech = _Brain(), _Speech()
    monkeypatch.setattr(server, "brain_instance", brain)
    monkeypatch.setattr(server, "speech", speech)
    server._close_clear_question()
    yield brain, speech
    server._close_clear_question()


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_the_command_alone_only_asks(wired):
    brain, speech = wired
    await server._ask_to_clear("start fresh")
    await _settle()
    assert brain.rotations == [], "one turn must never clear"
    assert speech.said == [server.FRESH_START_CONFIRM_LINE]


@pytest.mark.asyncio
async def test_command_then_yes_clears(wired):
    brain, speech = wired
    await server._ask_to_clear("start fresh")
    assert server._take_clear_answer("yes") is True
    await _settle()
    assert brain.rotations == [None]
    assert speech.said[-1] == server.FRESH_START_LINE


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["no", "not now", "yes but not now",
                                    "what time is it", "start fresh", ""])
async def test_command_then_anything_else_does_not_clear(wired, answer):
    brain, speech = wired
    await server._ask_to_clear("start fresh")
    assert server._take_clear_answer(answer) is False
    await _settle()
    assert brain.rotations == []
    assert speech.said[-1] == server.FRESH_START_CANCELLED_LINE
    # and the question is closed: a later yes clears nothing
    assert server._take_clear_answer("yes") is None
    await _settle()
    assert brain.rotations == []


@pytest.mark.asyncio
async def test_a_yes_with_no_question_open_is_not_ours(wired):
    brain, _ = wired
    assert server._take_clear_answer("yes") is None
    await _settle()
    assert brain.rotations == []


@pytest.mark.asyncio
async def test_a_timeout_cancels_and_says_so(wired, monkeypatch):
    brain, speech = wired
    monkeypatch.setattr(server, "FRESH_START_CONFIRM_TIMEOUT", 0.01)
    await server._ask_to_clear("start fresh")
    await asyncio.sleep(0.1)
    assert speech.said[-1] == server.FRESH_START_CANCELLED_LINE
    assert server._take_clear_answer("yes") is None
    assert brain.rotations == []


def test_the_old_phrase_list_is_gone():
    assert not hasattr(server, "FRESH_START_PHRASES")
