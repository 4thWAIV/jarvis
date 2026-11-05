"""The capture cues (backlog: completion-cues, first part): a quiet tone when
listening really starts and a different one when it really stops, whatever
stopped it, each exactly once per change; none on a load or a reload, none
when nothing changed, none at all with his capture sounds Off; never played
into the capture graph, and never late once audio is unlocked.

Headless Chromium on the built page with Chromium's fake microphone, the
fake transcription socket and a fake /ws/voice (test_voice_page_quiet_input's
harness). Every oscillator the page starts is recorded with the audio
context it belongs to; each cue is two notes, rising for start and falling
for stop (voice.ts CUE_NOTES).
"""

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_voice_page_quiet_input import STATE, _browser, _listening, _page
from tests.test_voice_page_speech_input import _until

# Records every audio context, the ones the microphone feeds, and every
# oscillator started (its frequency, its context).
CUE_SPY = """
(() => {
  window.__contexts = [];
  window.__captureContexts = [];
  window.__oscillators = [];
  const Real = window.AudioContext;
  window.AudioContext = class extends Real {
    constructor(...args) { super(...args); window.__contexts.push(this); }
  };
  const source = Real.prototype.createMediaStreamSource;
  Real.prototype.createMediaStreamSource = function (...args) {
    window.__captureContexts.push(window.__contexts.indexOf(this));
    return source.apply(this, args);
  };
  const start = OscillatorNode.prototype.start;
  OscillatorNode.prototype.start = function (...args) {
    window.__oscillators.push({ hz: this.frequency.value, ctx: window.__contexts.indexOf(this.context) });
    return start.apply(this, args);
  };
})();
"""
# The cues played so far, in order: each is two notes in a row.
CUES = """() => { const o = window.__oscillators, out = [];
  for (let i = 0; i + 1 < o.length; i += 2) out.push(o[i].hz < o[i + 1].hz ? "start" : "stop");
  return out; }"""


async def _cue_page(context, url, **kw):
    return await _page(context, url, init=(CUE_SPY,), **kw)


async def _cues(page):
    return await page.evaluate(CUES)


async def _cues_become(page, expected, timeout=10.0, settle_ms=800):
    """The cues reach `expected` and stay there: no second cue for one change."""
    async def now():
        return await _cues(page) == expected
    assert await _until(now, timeout), await _cues(page)
    await page.wait_for_timeout(settle_ms)
    assert await _cues(page) == expected


# ── once per real change, never on load ──────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_start_and_stop_each_play_once_per_real_change_and_never_on_load(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _cue_page(context, page_url)
        await page.wait_for_timeout(1500)
        assert await _cues(page) == [], "a cue on load"
        # Leaving and coming back while not listening changes nothing.
        await page.evaluate("window.__setVisibility('hidden')")
        await page.evaluate("window.__setVisibility('visible')")

        await _listening(page)
        await _cues_become(page, ["start"])
        await page.click("#btn-mic-off")                      # Mic off: stopped
        await _cues_become(page, ["start", "stop"])
        await page.click("#btn-mic-off")                      # Mic on: listening again
        await _cues_become(page, ["start", "stop", "start"])
        await page.click("#btn-mute")                         # Mute
        await _cues_become(page, ["start", "stop", "start", "stop"])
        await page.click("#btn-mute")                         # Unmute
        await _cues_become(page, ["start", "stop", "start", "stop", "start"])
        await page.click("#btn-mute")
        await _cues_become(page, ["start", "stop", "start", "stop", "start", "stop"])
        await page.click("#btn-mic-off")                      # muted to off: nothing changed
        await _cues_become(page, ["start", "stop", "start", "stop", "start", "stop"])
        await page.click("#btn-mic-off")                      # Mic on
        await _cues_become(page, ["start", "stop", "start", "stop", "start", "stop", "start"])

        await page.reload()
        await page.wait_for_timeout(1500)
        assert await _cues(page) == [], "a cue on reload"
        assert await page.evaluate("window.__micOpens") == 0
        await context.close()


# ── stops that were not his ──────────────────────────────────────────────

INVOLUNTARY = {
    # Another page took the recogniser: this one lets go and stays stopped.
    "taken": ("""() => window.__transcribers[0]._emit(
                 { type: 'error', code: 'taken', message: 'Listening moved.' })""", False),
    # The recogniser failed: stopped, then a retry gets through.
    "recogniser failed": ("""() => window.__transcribers[0]._emit(
                 { type: 'error', code: 'decoder-failed', message: 'broken' })""", True),
    # The recogniser's socket went away.
    "recogniser lost": ("() => window.__transcribers[0].close()", True),
    # The capture was dropped: the socket backed up past its limit.
    "capture dropped": ("() => { window.__transcribers[0].bufferedAmount = 1e9; }", True),
    # Left the page under Stop listening.
    "switched away": ("() => window.__setVisibility('hidden')", False),
}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("cause", list(INVOLUNTARY))
async def test_an_involuntary_stop_plays_the_stop_sound(page_playwright, page_url, cause):
    script, resumes = INVOLUNTARY[cause]
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _cue_page(context, page_url, stop="discard", switch="stop")
        await _listening(page)
        await _cues_become(page, ["start"], settle_ms=0)
        await page.evaluate(script)
        if resumes:
            # Stopped, then listening again once the retry is ready: one each.
            await _cues_become(page, ["start", "stop", "start"], timeout=15)
        else:
            await _cues_become(page, ["start", "stop"], settle_ms=2500)
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_losing_the_voice_socket_stops_with_a_sound_and_resuming_starts_with_one(
        page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _cue_page(context, page_url)
        await _listening(page)
        await _cues_become(page, ["start"], settle_ms=0)
        await page.voice.socket.close()
        await _cues_become(page, ["start", "stop"], settle_ms=0)
        await page.wait_for_function("window.__starts.length === 2", timeout=20000)
        await _cues_become(page, ["start", "stop", "start"])
        await context.close()


# ── Off ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_with_capture_sounds_off_nothing_plays_and_the_page_shows_the_same(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _cue_page(context, page_url, sounds=False)
        await _listening(page)
        assert await page.evaluate(STATE) == "Listening"
        await page.click("#btn-mute")
        await page.click("#btn-mute")
        await page.wait_for_function("window.__starts.length === 2 && window.__transcribers[1].readyState === 1",
                                     timeout=10000)
        await page.evaluate("() => window.__transcribers[1]._emit({ type: 'error', code: 'taken', message: '' })")
        await page.wait_for_function(f"{STATE} === 'Not listening'", timeout=5000)
        await page.wait_for_timeout(1000)
        assert await page.evaluate("window.__oscillators.length") == 0
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_turning_capture_sounds_off_in_settings_saves_it_and_silences_the_next_stop(
        page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _cue_page(context, page_url)
        await _listening(page)
        await _cues_become(page, ["start"], settle_ms=0)
        await page.click("#btn-menu")
        await page.get_by_role("button", name="Settings", exact=True).click()
        sounds = page.locator("#settings-voice-sounds")
        await sounds.wait_for()
        assert await sounds.input_value() == "on"
        await sounds.select_option("off")
        assert await _until(lambda: page.posted == [{"sounds": False}])
        # Saving it starts, stops and plays nothing.
        await page.wait_for_timeout(500)
        assert await _cues(page) == ["start"]
        assert await page.evaluate("window.__liveTracks()") == 1
        await page.click("#btn-mic-off")
        await page.wait_for_function("window.__liveTracks() === 0", timeout=3000)
        await page.wait_for_timeout(800)
        assert await _cues(page) == ["start"]
        await context.close()


# ── echo and audio unlock ────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_a_cue_is_played_on_the_output_and_never_into_the_capture_graph(page_playwright, page_url):
    """The microphone's own audio context, the only one that feeds the
    recogniser, never makes a sound: every cue is on the output's context,
    so no cue reaches what is sent for recognition except as room echo,
    which the browser's echo cancellation is there for."""
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _cue_page(context, page_url)
        await _listening(page)
        await page.click("#btn-mute")
        await page.click("#btn-mute")
        await _cues_become(page, ["start", "stop", "start"], settle_ms=0)
        capture = await page.evaluate("window.__captureContexts")
        played = await page.evaluate("window.__oscillators.map((o) => o.ctx)")
        assert capture and played
        assert not set(capture) & set(played), (capture, played)
        assert len(set(played)) == 1, "every cue goes through the one output"
        assert page.voice.finals() == [], "a cue became words"
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_cue_missed_while_audio_is_locked_never_plays_once_it_is_unlocked(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _cue_page(context, page_url)
        await _listening(page)
        await _cues_become(page, ["start"], settle_ms=0)
        output = await page.evaluate("window.__oscillators[0].ctx")
        await page.evaluate(f"window.__contexts[{output}].suspend()")
        await page.wait_for_function(f"window.__contexts[{output}].state === 'suspended'", timeout=3000)
        # A stop and a start while the output is locked: neither is heard...
        await page.voice.socket.close()
        await page.wait_for_function("window.__starts.length === 2", timeout=20000)
        await page.wait_for_function("window.__transcribers[1].readyState === 1", timeout=5000)
        await page.wait_for_timeout(500)
        # ...and neither sounds once it is unlocked.
        await page.evaluate(f"window.__contexts[{output}].resume()")
        await page.wait_for_function(f"window.__contexts[{output}].state === 'running'", timeout=3000)
        await page.wait_for_timeout(1000)
        assert await _cues(page) == ["start"]
        assert await page.evaluate("window.__oscillators.length") == 2
        await context.close()
