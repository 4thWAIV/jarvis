"""What became of each chunk JARVIS sent to be spoken (speech.Outcome).

Every outcome is a fact the page or the user gave: queued when it went out,
started and played when the page said so, failed when there was no audio or
the page could not decode it, skipped by Skip the rest, interrupted by Stop
speaking, a barge-in or a lost connection. The transport moving past a chunk
is never the same as it being heard, so a read-back gate (`wait_for`,
`wait_played`) is never satisfied by a chunk that failed or was skipped.
"""
import asyncio

import pytest
import pytest_asyncio

from tests.test_speech import Harness


@pytest_asyncio.fixture
async def h():
    harness = Harness()
    await harness.sched.start()
    yield harness
    await harness.sched.stop()


def _mp3(seconds: float) -> bytes:
    """A constant-bitrate MPEG-1 Layer III blob 128 kbit/s long enough to
    play for `seconds`: only its first frame header is ever read."""
    return b"\xff\xfb\x90\x00" + bytes(int(16000 * seconds) - 4)


@pytest.mark.asyncio
async def test_a_chunk_goes_queued_started_played(h):
    s = h.sched
    u = await s.say("One sentence. Another one.")
    await asyncio.sleep(0.1)
    assert u.outcomes() == ["queued", "queued"]
    s.playback_started(u.id, 0)
    assert u.outcomes() == ["started", "queued"]
    await h.ack(u.id, 0)
    s.playback_started(u.id, 1)
    await h.ack(u.id, 1)
    assert await s.wait_for(u, timeout=1)
    assert u.outcomes() == ["played", "played"] and u.heard_in_full


@pytest.mark.asyncio
async def test_a_decode_failure_is_failed_not_played_and_the_next_chunk_still_goes(h):
    s = h.sched
    u = await s.say("First part. Second part. Third part.")
    await asyncio.sleep(0.1)
    assert [i for _, i, _ in h.audio()] == [0, 1]
    s.playback_failed(u.id, 0)                    # the page could not decode it
    await asyncio.sleep(0.1)
    assert [i for _, i, _ in h.audio()] == [0, 1, 2], "the transport moved on"
    await h.ack(u.id, 1)
    await h.ack(u.id, 2)
    assert not await s.wait_for(u, timeout=1)
    assert u.done and u.outcomes() == ["failed", "played", "played"]
    assert not u.heard_in_full


@pytest.mark.asyncio
async def test_a_failed_read_back_is_not_heard_so_it_cannot_approve(h):
    s = h.sched
    reading = s.read_aloud("Telling hammer: ship it.", capture=7)
    await asyncio.sleep(0.1)
    s.playback_failed(reading.id, 0)
    assert not await s.wait_for(reading, timeout=1)
    assert reading.outcomes() == ["failed"]


@pytest.mark.asyncio
async def test_a_reading_inside_a_turn_is_judged_by_its_own_chunks(h):
    """A reply sentence that failed does not stop a capture read in the same
    turn from being heard in full, and a capture sentence that failed does."""
    s = h.sched
    u = s.begin_turn()
    s.feed(u, "Here it is. ")
    last = s.read_into(u, "Telling hammer: ship it.", 3)
    await s.end_turn(u)
    await asyncio.sleep(0.1)
    s.playback_failed(u.id, 0)
    await asyncio.sleep(0.05)
    await h.ack(u.id, last)
    assert await s.wait_played(u, last, timeout=1)

    v = s.begin_turn()
    s.feed(v, "Here it is. ")
    last = s.read_into(v, "Telling hammer: hold it.", 4)
    await s.end_turn(v)
    await asyncio.sleep(0.1)
    await h.ack(v.id, 0)
    s.playback_failed(v.id, last)
    assert not await s.wait_played(v, last, timeout=1)


@pytest.mark.asyncio
async def test_a_synthesis_failure_is_failed_and_shown_as_text(h):
    s = h.sched
    h.fail_texts.add("Unvoiced.")
    reading = s.read_aloud("Unvoiced.", capture=9)
    assert not await s.wait_for(reading, timeout=1)
    assert reading.outcomes() == ["failed"]
    assert any(m["type"] == "text" and m["text"] == "Unvoiced." for m in h.msgs)


@pytest.mark.asyncio
async def test_skipping_a_read_back_in_a_turn_leaves_it_unheard(h):
    s = h.sched
    u = s.begin_turn()
    last = s.read_into(u, "Telling hammer: ship it now.", 5)
    await asyncio.sleep(0.1)
    waiting = asyncio.ensure_future(s.wait_played(u, last, timeout=2))
    assert await s.skip_reading()                 # the one sounding now
    assert not await waiting
    assert set(u.outcomes()) == {"skipped"}


@pytest.mark.asyncio
async def test_late_acks_never_turn_skipped_audio_into_played(h):
    s = h.sched
    u = await s.say("Skip me. And me.")
    await asyncio.sleep(0.1)
    await s.skip_reading(u.id)
    await s.played(u.id, 1)                       # a stale ack in the air
    assert u.outcomes() == ["skipped", "skipped"]
    assert not await s.wait_for(u, timeout=0.2)


@pytest.mark.asyncio
async def test_stop_speaking_interrupts_and_a_kept_ack_is_still_the_truth(h):
    s = h.sched
    u = await s.say("Alpha one. Beta two. Gamma three.")
    await asyncio.sleep(0.1)
    s.playback_started(u.id, 0)
    await s.barge_in(reason="hush (key)")
    assert u.outcomes() == ["interrupted"] * 3
    await s.played(u.id, 0)                       # it had ended just before the stop
    assert u.outcomes() == ["played", "interrupted", "interrupted"]
    assert not await s.wait_for(u, timeout=0.2)


@pytest.mark.asyncio
async def test_withdrawing_a_reading_interrupts_its_chunks(h):
    s = h.sched
    u = s.narrate([("Slide one. Still slide one.", {"slide": 1})])
    await asyncio.sleep(0.1)
    await s.withdraw(u, reason="story stopped")
    assert set(u.outcomes()) == {"interrupted"}


@pytest.mark.asyncio
async def test_reports_for_chunks_never_sent_are_ignored(h):
    s = h.sched
    u = await s.say("One. Two. Three. Four.")
    await asyncio.sleep(0.1)
    s.playback_failed(u.id, 3)                    # not sent yet: prefetch is two
    s.playback_started(u.id, 3)
    s.playback_failed(999, 0)
    assert u.outcomes()[3] == "pending" and u.played == -1


@pytest.mark.asyncio
async def test_a_failure_report_after_playing_changes_nothing(h):
    s = h.sched
    u = await s.say("Only one.")
    await asyncio.sleep(0.1)
    await h.ack(u.id, 0)
    s.playback_failed(u.id, 0)
    assert u.outcomes() == ["played"]
    assert await s.wait_for(u, timeout=0.5)


@pytest.mark.asyncio
async def test_words_of_a_chunk_that_never_sounded_are_not_echo(h):
    s = h.sched
    u = await s.say("Deploying the staging cluster.")
    await asyncio.sleep(0.1)
    assert s.classify("deploying the staging cluster") == "echo"
    s.playback_failed(u.id, 0)
    assert s.classify("deploying the staging cluster") == "speech"


@pytest.mark.asyncio
async def test_a_failed_chunk_gives_back_the_floor_it_held_over_the_next():
    """Each ack is held until its chunk could have ended (ACK_FLOOR_FACTOR).
    A chunk that never played no longer counts toward the next one's floor."""
    from jarvis.speech import SpeechScheduler
    now = [100.0]
    sent: list[dict] = []

    async def synth(text):
        return _mp3(2.0)

    async def emit(msg):
        sent.append(msg)

    s = SpeechScheduler(synth, emit, clock=lambda: now[0])
    u = await s.say("First. Second.")
    for c in u.chunks:
        await c.synthesis
    await s._pump()
    assert [m["idx"] for m in sent if m["type"] == "audio"] == [0, 1]
    floor = 2.0 * 0.9
    assert u.chunks[1].earliest_ack == pytest.approx(100.0 + 2 * floor)
    s.playback_failed(u.id, 0)
    assert u.chunks[0].earliest_ack == pytest.approx(100.0)
    assert u.chunks[1].earliest_ack == pytest.approx(100.0 + floor)
    now[0] = 100.0 + floor + 0.01
    await s.played(u.id, 1)
    assert u.outcomes() == ["failed", "played"] and u.held_ack == -1
    await s.stop()
