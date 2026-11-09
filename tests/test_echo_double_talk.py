"""Double talk: the user speaking while JARVIS's own voice is coming out of
the speaker, as the recogniser would hand it to the server.

These are text fixtures over the real scheduler, not audio. Each one pins a
case the echo rules must get right with the microphone open during
playback: short interruptions (stop, no, wait, and the Spanish ones), the
recogniser garbling JARVIS's own sentence, the user repeating JARVIS's words,
quiet speech arriving as fragments, and words that are only in a chunk
still queued behind the one playing. What they cannot show is how a real
speaker, room and microphone garble the audio, or what browser echo
cancellation removes before recognition; that needs listening tests on the
actual output devices.
"""
import asyncio

import pytest
import pytest_asyncio

from tests.fixtures.waiting import until_async
from tests.test_speech import Harness


@pytest_asyncio.fixture
async def h():
    harness = Harness()
    await harness.sched.start()
    yield harness
    await harness.sched.stop()


async def _sounding(h, text, *, started=True):
    """JARVIS saying `text`, its first chunk sounding now (sent, unacked)."""
    u = await h.sched.say(text)
    assert await until_async(lambda: u.outcomes()[0] == "queued"), u.outcomes()
    assert h.sched.is_speaking
    if started:
        h.sched.playback_started(u.id, 0)
    return u


# ── short interruptions while he is audible ────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["stop", "wait", "no", "Stop.", "cancel"])
async def test_a_one_word_interruption_over_an_unrelated_sentence_cuts_him_off(h, word):
    await _sounding(h, "The staging deploy finished in four minutes.")
    await h.sched.user_interim(word)
    assert not h.sched.is_speaking, word


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["espera", "alto", "detente"])
async def test_a_spanish_one_word_interruption_cuts_him_off(h, spanish, word):
    await _sounding(h, "El despliegue terminó en cuatro minutos.")
    await h.sched.user_interim(word)
    assert not h.sched.is_speaking, word


@pytest.mark.asyncio
@pytest.mark.parametrize("sentence,heard", [
    ("Stopped the work in chitauri, sir.", "stop"),
    ("Cancelled the run, sir.", "cancel"),
    ("Waiting on the build, sir.", "wait"),
])
async def test_his_own_word_clipped_by_the_recogniser_does_not_cut_him_off(h, sentence, heard):
    await _sounding(h, sentence)
    await h.sched.user_interim(heard)
    assert h.sched.is_speaking, f"{heard!r} is his own {sentence!r} coming back"


@pytest.mark.asyncio
async def test_a_garbled_echo_of_his_sentence_does_not_cut_him_off(h):
    await _sounding(h, "Found it — chitauri is idle on the build.")
    await h.sched.user_interim("found it guitar an")
    assert h.sched.is_speaking


@pytest.mark.asyncio
@pytest.mark.parametrize("words", ["actually hold on", "no that is wrong", "wait wait wait"])
async def test_a_real_interruption_of_new_words_cuts_him_off(h, words):
    await _sounding(h, "The staging deploy finished in four minutes.")
    await h.sched.user_interim(words)
    assert not h.sched.is_speaking, words


@pytest.mark.asyncio
async def test_his_whole_sentence_coming_back_is_echo(h):
    await _sounding(h, "The staging deploy finished in four minutes.")
    assert await h.sched.user_final("the staging deploy finished in four minutes") == "echo"
    assert h.sched.is_speaking


# ── quiet speech: fragments as the recogniser builds them ──────────────────

@pytest.mark.asyncio
async def test_quiet_speech_arriving_as_growing_fragments_cuts_him_off_once_it_is_words(h):
    await _sounding(h, "The staging deploy finished in four minutes.")
    await h.sched.user_interim("the")            # one of his words, nothing more
    assert h.sched.is_speaking
    await h.sched.user_interim("the wro")
    assert h.sched.is_speaking                  # one new fragment is not two new words
    await h.sched.user_interim("the wrong branch")
    assert not h.sched.is_speaking


# ── repeating his words ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_his_word_said_back_is_echo_inside_the_grace_and_the_user_after_it(h):
    """A short reply made only of his own words is the user's once his audio
    has ended by SHORT_ECHO_GRACE_SEC. Inside the grace it cannot be told
    from the tail of his own voice: a known limit of text matching."""
    h.sched.echo_window = 6.0
    u = await _sounding(h, "Shall I run it now?")
    await h.ack(u.id, 0)
    assert h.sched.classify("run it") == "echo"
    await asyncio.sleep(0.8)
    assert h.sched.classify("run it") == "speech"


@pytest.mark.asyncio
async def test_a_long_repeat_of_his_sentence_inside_the_echo_window_is_taken_for_echo(h):
    """A known limit: repeating three or more of his words back to him while
    they may still be in the room is indistinguishable from echo by text."""
    h.sched.echo_window = 6.0
    u = await _sounding(h, "Deploy the staging cluster now?")
    await h.ack(u.id, 0)
    await asyncio.sleep(0.8)
    assert h.sched.classify("deploy the staging cluster") == "echo"


# ── only what has sounded can echo ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_words_only_in_the_chunk_queued_behind_the_one_playing_are_the_user_s(h):
    u = await h.sched.say("The build is green. Deploy staging tonight.")
    assert await until_async(lambda: [i for utt, i, _ in h.audio() if utt == u.id] == [0, 1]), \
        ("both sent, prefetch", h.audio())
    h.sched.playback_started(u.id, 0)
    assert h.sched.classify("deploy staging tonight") == "speech"
    assert h.sched.classify("the build is green") == "echo"
    await h.ack(u.id, 0)
    h.sched.playback_started(u.id, 1)
    assert h.sched.classify("deploy staging tonight") == "echo"


@pytest.mark.asyncio
async def test_an_older_page_that_never_reports_starts_keeps_every_sent_chunk_as_echo(h):
    u = await h.sched.say("The build is green. Deploy staging tonight.")
    assert await until_async(lambda: [i for utt, i, _ in h.audio() if utt == u.id] == [0, 1]), \
        h.audio()
    assert h.sched.classify("deploy staging tonight") == "echo"


@pytest.mark.asyncio
async def test_a_new_page_has_to_report_starts_for_itself(h):
    await _sounding(h, "The build is green. Deploy staging tonight.")
    await h.sched.barge_in()      # the tab closes mid-sentence
    h.sched.transport_gone()
    v = await h.sched.say("Release notes are ready. Publish them later.")
    assert await until_async(lambda: v.outcomes() == ["queued", "queued"]), v.outcomes()
    assert h.sched.classify("publish them later") == "echo"


@pytest.mark.asyncio
async def test_words_of_a_skipped_reading_that_never_sounded_are_the_user_s(h):
    u = await h.sched.say("The build is green. Deploy staging tonight.")
    assert await until_async(lambda: u.outcomes() == ["queued", "queued"]), u.outcomes()
    h.sched.playback_started(u.id, 0)
    await h.sched.skip_reading(u.id)
    assert h.sched.classify("deploy staging tonight") == "speech"


@pytest.mark.asyncio
async def test_after_stop_speaking_the_tail_in_the_room_is_still_echo_for_a_moment(h):
    await _sounding(h, "The staging deploy finished in four minutes.")
    await h.sched.barge_in(reason="hush (the page)")
    assert h.sched.classify("staging deploy finished") == "echo"
    await asyncio.sleep(0.6)                     # past the harness's echo_window
    assert h.sched.classify("staging deploy finished") == "speech"
