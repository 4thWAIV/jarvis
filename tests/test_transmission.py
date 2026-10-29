"""The shared send orchestration (`jarvis/transmission.py`) against a
destination that is not an agent: the shape a human's JARVIS takes, which
has no one-message-per-turn queue of its own and whose "sent" is the
approved envelope being durably in the outbox. The agent's adapters are
pinned end to end in `test_agent_transmission_pins.py`."""
from __future__ import annotations

import pytest

from jarvis import transmission


class _Speech:
    def __init__(self, cancelled=False):
        self.said, self.cancelled, self.windows = [], cancelled, []

    async def say(self, text, *a, **k):
        self.said.append(text)

    async def open_cancel_window(self, seconds):
        self.windows.append(seconds)
        return self.cancelled


class _Box:
    def __init__(self):
        self.events = []

    def transmitting(self, capture_id):
        self.events.append(("transmitting", capture_id))

    def sent(self, capture_id):
        self.events.append(("sent", capture_id))

    def reopen(self, capture_id):
        self.events.append(("reopen", capture_id))


class _Outbox:
    """A destination whose hand-over is a durable outbox write."""

    def __init__(self, outcome="stored", why=None, priority=False, raises=None):
        self.capture_id, self.priority = 4, priority
        self.outcome, self.why, self.raises = outcome, why, raises
        self.records, self.settled, self.went_count, self.outcomes = 0, [], 0, []

    def record(self):
        self.records += 1
        return "m-1"

    def settle(self, status, **fields):
        self.settled.append((status, fields))

    def why_wait(self):
        return self.why

    async def deliver(self):
        if self.raises:
            raise self.raises
        return self.outcome

    def delivered(self, outcome):
        return outcome == "stored"

    async def went(self):
        self.went_count += 1

    async def say_queued(self, speech, why):
        await speech.say(f"waits: {why}")

    async def say_outcome(self, speech, outcome):
        self.outcomes.append(outcome)


async def _perform(dest, speech=None, box=None):
    speech = _Speech() if speech is None else speech
    box = box or _Box()
    await transmission.perform(dest, speech=speech, cancel_window=2.0, captures=box)
    return speech, box


def test_an_outbox_destination_satisfies_the_interface():
    assert isinstance(_Outbox(), transmission.Destination)


@pytest.mark.asyncio
async def test_handed_over_it_is_sent_once_drawn_and_its_capture_closed():
    dest = _Outbox()
    speech, box = await _perform(dest)
    assert dest.records == 1 and speech.windows == [2.0]
    assert [s for s, _ in dest.settled] == ["sent"] and "sent_at" in dest.settled[0][1]
    assert dest.went_count == 1 and dest.outcomes == ["stored"]
    assert box.events == [("transmitting", 4), ("sent", 4), ("reopen", 4)]


@pytest.mark.asyncio
async def test_cancelled_in_the_window_nothing_is_handed_over():
    dest = _Outbox()
    speech, box = await _perform(dest, _Speech(cancelled=True))
    assert dest.settled == [("cancelled", {})] and speech.said == ["Cancelled, sir."]
    assert dest.went_count == 0 and box.events == [("reopen", 4)]


@pytest.mark.asyncio
async def test_no_voice_fails_it_unsent():
    dest = _Outbox()
    await transmission.perform(dest, speech=None, cancel_window=2.0, captures=_Box())
    assert dest.settled == [("failed", {"failure": "no_voice"})]


@pytest.mark.asyncio
async def test_a_destination_without_a_queue_never_waits_and_one_with_a_reason_queues():
    dest = _Outbox(why="is mid-turn")
    speech, box = await _perform(dest)
    assert dest.settled == [("queued", {})] and speech.said == ["waits: is mid-turn"]
    assert box.events == [("sent", 4), ("reopen", 4)]
    urgent = _Outbox(why="is mid-turn", priority=True)
    await _perform(urgent)
    assert [s for s, _ in urgent.settled] == ["sent"]


@pytest.mark.asyncio
async def test_a_refused_hand_over_is_failed_with_its_outcome():
    dest = _Outbox(outcome="too_large")
    await _perform(dest)
    assert dest.settled == [("failed", {"failure": "too_large"})] and dest.went_count == 0
    assert dest.outcomes == ["too_large"]


@pytest.mark.asyncio
async def test_a_hand_over_that_raises_is_failed_once_and_raised():
    dest = _Outbox(raises=RuntimeError("disk"))
    with pytest.raises(RuntimeError):
        await _perform(dest)
    assert dest.settled == [("failed", {"failure": "failed"})]


class _Person:
    def __init__(self, refusal=None):
        self._refusal, self.refused, self.opened = refusal, [], []

    def refusal(self):
        return self._refusal

    def unheard(self):
        return "no voice"

    def refuse(self, failure):
        self.refused.append(failure)

    def open(self, capture):
        self.opened.append(capture["id"])
        return "m-1", _Outbox()


def test_staging_asks_the_recipient_first_then_the_voice_then_records():
    capture = {"id": 4, "text": "hi", "read": "hi"}
    staged = []
    assert transmission.stage(_Person(("not_paired", "Not paired.")), capture,
                              voice=object(), put=staged.append) == (None, "Not paired.")
    person = _Person()
    assert transmission.stage(person, capture, voice=None, put=staged.append) == \
        (None, "no voice")
    assert person.refused == ["no_voice"] and staged == [] and "staged" not in capture
    assert transmission.stage(person, capture, voice=object(), put=staged.append) == ("m-1", None)
    assert person.opened == [4] and len(staged) == 1 and capture["staged"] is True


def test_acceptance_is_heard_or_accepted_since_the_last_change():
    capture = {"text": "hi", "read": None}
    assert not transmission.accepted(capture)
    transmission.accept(capture)
    assert transmission.accepted(capture)
    capture["text"] = "hello"
    assert not transmission.accepted(capture)
