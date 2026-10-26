"""Turn-taking: JARVIS waits for him to finish. The server gathers what he
says until he has been quiet for END_OF_TURN_SEC, so a pause mid-sentence
does not send the brain half a sentence; nothing JARVIS says starts while he
is talking, his replies included; and an announcement never starts at once."""

import asyncio

import pytest
import pytest_asyncio

from jarvis import server
from jarvis.speech import END_OF_TURN_SEC, Priority, SpeechScheduler
from tests.test_speech import Harness


# ── the server gathers his words ─────────────────────────────────────────

class Sent:
    def __init__(self):
        self.calls = []

    async def __call__(self, text, heard):
        self.calls.append((text, heard))


@pytest.mark.asyncio
async def test_finals_heard_with_a_short_pause_between_are_one_utterance():
    sent = Sent()
    u = server._Utterance(sent, hold=0.2)
    u.heard("send a message to agent guard", 1)
    await asyncio.sleep(0.1)
    u.heard("asking for its status", 2)
    await asyncio.sleep(0.1)
    assert sent.calls == [], "nothing goes while he may still be talking"
    await asyncio.sleep(0.2)
    assert sent.calls == [("send a message to agent guard asking for its status", 2)]


@pytest.mark.asyncio
async def test_more_words_on_the_way_hold_it_longer():
    sent = Sent()
    u = server._Utterance(sent, hold=0.2)
    u.heard("don't restart", 1)
    for _ in range(4):
        await asyncio.sleep(0.1)
        u.still_talking()                  # an interim: he is still going
    assert sent.calls == []
    await asyncio.sleep(0.3)
    assert sent.calls == [("don't restart", 1)]


@pytest.mark.asyncio
async def test_an_interim_with_nothing_gathered_starts_nothing():
    sent = Sent()
    u = server._Utterance(sent, hold=0.05)
    u.still_talking()
    await asyncio.sleep(0.15)
    assert sent.calls == []


@pytest.mark.asyncio
async def test_now_sends_what_was_heard_at_once_and_only_once():
    sent = Sent()
    u = server._Utterance(sent, hold=0.2)
    u.heard("first part", 7)
    await u.now()
    await asyncio.sleep(0.3)
    assert sent.calls == [("first part", 7)]


def test_the_server_waits_as_long_as_the_scheduler():
    assert server._Utterance(Sent())._hold == END_OF_TURN_SEC == 1.5


# ── JARVIS does not talk over him ────────────────────────────────────────

@pytest_asyncio.fixture
async def h():
    harness = Harness()          # pause_after 0.2, so end_of_turn is 0.2 too
    await harness.sched.start()
    yield harness
    await harness.sched.stop()


@pytest.mark.asyncio
async def test_an_announcement_waits_for_him_to_stop_talking(h):
    await h.sched.user_interim("so what I want is")
    await h.sched.say("Delivered to Agent Guard, sir.", Priority.NORMAL, label="announcement")
    await asyncio.sleep(0.1)
    assert h.audio() == []
    await asyncio.sleep(0.25)
    assert [t for _, _, t in h.audio()] == ["Delivered to Agent Guard, sir."]


@pytest.mark.asyncio
async def test_a_reply_waits_until_he_has_finished(h):
    await h.sched.user_final("tell me the weather")
    utt = h.sched.begin_turn()
    h.sched.feed(utt, "It is sunny, sir.")
    await h.sched.end_turn(utt)
    await asyncio.sleep(0.05)
    assert h.audio() == [], "he spoke a moment ago"
    await asyncio.sleep(0.3)
    assert [t for _, _, t in h.audio()] == ["It is sunny, sir."]


@pytest.mark.asyncio
async def test_what_he_typed_is_answered_at_once(h):
    await h.sched.user_final("what time is it", typed=True)
    utt = h.sched.begin_turn()
    h.sched.feed(utt, "Five o'clock, sir.")
    await h.sched.end_turn(utt)
    await asyncio.sleep(0.08)
    assert [t for _, _, t in h.audio()] == ["Five o'clock, sir."]


def test_the_end_of_turn_wait_is_never_longer_than_the_announcement_pause():
    async def nop(*a):
        return None
    assert SpeechScheduler(nop, nop).end_of_turn == END_OF_TURN_SEC
    assert SpeechScheduler(nop, nop, pause_after=0.2).end_of_turn == 0.2
