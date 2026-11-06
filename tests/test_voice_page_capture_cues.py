"""The capture cues (backlog: completion-cues, first part): a quiet tone when
listening really starts and a different one when it really stops, whatever
stopped it, each exactly once per change; none on a load or a reload, none
when nothing changed, none at all with his capture sounds Off; never played
into the capture graph, and never late once audio is unlocked.

Headless Chromium on the built page with Chromium's fake microphone, the
fake transcription socket and a fake /ws/voice (test_voice_page_quiet_input's
harness). Every oscillator the page starts is recorded with the audio
context it belongs to, grouped by the task that started it (one cue's
notes are all started at once); each cue is told by its notes
(voice.ts TONE_NOTES), and by default start rises and stop falls.

The pause cues (the rest of completion-cues) are given by the server in a
`turn` frame (state `pause`) to the page gathering the turn; here the fake
/ws/voice sends them, as the server's own tests show it does only then.
"""

import json

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_voice_page_quiet_input import STATE, _browser, _listening, _page
from tests.test_voice_page_speech_input import _until
from jarvis import voice_control

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
  window.__peaks = [];
  const start = OscillatorNode.prototype.start;
  OscillatorNode.prototype.start = function (...args) {
    // One cue's notes are all started in one go: a new group begins once
    // the code that started the last note has run to its end.
    if (!window.__inCue) {
      window.__inCue = true;
      window.__cueGroup = (window.__cueGroup ?? 0) + 1;
      queueMicrotask(() => { window.__inCue = false; });
    }
    window.__oscillators.push({ hz: this.frequency.value, ctx: window.__contexts.indexOf(this.context),
                                group: window.__cueGroup });
    return start.apply(this, args);
  };
  const target = AudioParam.prototype.setTargetAtTime;
  AudioParam.prototype.setTargetAtTime = function (value, ...rest) {
    if (value > 0) window.__peaks.push(value);
    return target.call(this, value, ...rest);
  };
})();
"""
# Each tone's notes (voice.ts TONE_NOTES).
TONES = {(660, 880): "rise", (880, 660): "fall", (587,): "pulse", (440,): "low",
         (740, 740): "double", (784, 523): "resolve"}
# The cue each tone is by default (voice_control.CUE_DEFAULTS).
BY_DEFAULT = {"rise": "start", "fall": "stop", "pulse": "tentative", "low": "deciding",
              "resolve": "likely"}
# The notes played so far, one list per cue, in order.
NOTES = """() => { const out = []; let key = null;
  for (const o of window.__oscillators) {
    const k = `${o.ctx}:${o.group}`;
    if (k !== key) { out.push([]); key = k; }
    out[out.length - 1].push(Math.round(o.hz));
  }
  return out; }"""


async def _cue_page(context, url, **kw):
    return await _page(context, url, init=(CUE_SPY,), **kw)


async def _tones(page):
    return [TONES[tuple(notes)] for notes in await page.evaluate(NOTES)]


async def _cues(page):
    """The cues played, by the cue each tone is by default."""
    return [BY_DEFAULT[tone] for tone in await _tones(page)]


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


# ── the pause cues ───────────────────────────────────────────────────────

def _pause(page, stage, sound=True, turn=1, text="book a table"):
    """The server's pause frame for the turn this page is gathering."""
    page.voice.socket.send(json.dumps({"type": "turn", "state": "pause", "stage": stage,
                                       "turnId": turn, "text": text, "sound": sound}))


def _gathering(page, turn=1, text="book a table"):
    page.voice.socket.send(json.dumps({"type": "turn", "state": "gathering", "turnId": turn,
                                       "text": text}))


PAUSE_SHOWN = "document.querySelector('#conversation-control')?.dataset.pause ?? null"


@pytest.mark.asyncio(loop_scope="session")
async def test_each_pause_cue_plays_once_on_the_output_shown_and_never_heard_as_words(
        page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _cue_page(context, page_url, view="")
        await _listening(page)
        await _cues_become(page, ["start"], settle_ms=0)
        _gathering(page)
        _pause(page, "tentative")
        await _cues_become(page, ["start", "tentative"], settle_ms=0)
        await page.wait_for_function(f"{PAUSE_SHOWN} === 'tentative'", timeout=3000)
        _pause(page, "deciding")
        await _cues_become(page, ["start", "tentative", "deciding"], settle_ms=0)
        await page.wait_for_function(f"{PAUSE_SHOWN} === 'deciding'", timeout=3000)
        assert await page.locator("#conversation-control .control-title").text_content() == "Deciding"
        _pause(page, "likely")
        await _cues_become(page, ["start", "tentative", "deciding", "likely"])
        await page.wait_for_function(f"{PAUSE_SHOWN} === 'likely'", timeout=3000)
        # He speaks again: a new gathering frame, the pause no longer shown.
        _gathering(page, text="book a table for two")
        await page.wait_for_function(f"{PAUSE_SHOWN} === null", timeout=3000)
        capture = await page.evaluate("window.__captureContexts")
        played = await page.evaluate("window.__oscillators.map((o) => o.ctx)")
        assert not set(capture) & set(played) and len(set(played)) == 1
        assert page.voice.finals() == [], "a cue became words"
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_pause_cue_without_its_sound_or_while_muted_or_off_is_only_shown(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _cue_page(context, page_url, view="")
        await _listening(page)
        await _cues_become(page, ["start"], settle_ms=0)
        _gathering(page)
        _pause(page, "tentative", sound=False)          # off, or he is away: the server says so
        await page.wait_for_function(f"{PAUSE_SHOWN} === 'tentative'", timeout=3000)
        await page.click("#btn-mute")
        await _cues_become(page, ["start", "stop"], settle_ms=0)
        _gathering(page, turn=2)
        _pause(page, "likely", turn=2)                  # muted: never heard
        _pause(page, "deciding", turn=2)
        await page.click("#btn-mic-off")
        _pause(page, "deciding", turn=2)
        _pause(page, "tentative", turn=2)               # off: never heard
        await _cues_become(page, ["start", "stop"])
        await context.close()


@pytest.mark.parametrize("off", ["tentative", "deciding"])
@pytest.mark.asyncio(loop_scope="session")
async def test_turning_one_cue_off_silences_that_cue_alone(page_playwright, page_url, off):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        cues = {**voice_control.CUE_DEFAULTS, off: {**voice_control.CUE_DEFAULTS[off], "on": False}}
        page = await _cue_page(context, page_url, view="", cues=cues)
        await _listening(page)
        _gathering(page)
        for stage in voice_control.PAUSE_CUES:
            _pause(page, stage)
        await _cues_become(page, ["start"] + [c for c in voice_control.PAUSE_CUES if c != off])
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_chosen_tone_and_volume_are_what_plays(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        cues = {**voice_control.CUE_DEFAULTS,
                "likely": {**voice_control.CUE_DEFAULTS["likely"], "tone": "double"}}
        page = await _cue_page(context, page_url, view="", cues=cues, volume=20)
        await _listening(page)
        _gathering(page)
        _pause(page, "likely")
        assert await _until(lambda: _tones(page), 5)
        await page.wait_for_timeout(500)
        assert await _tones(page) == ["rise", "double"]
        peaks = await page.evaluate("window.__peaks")
        assert peaks and all(abs(v - 0.1 * 20 / 100) < 1e-6 for v in peaks), peaks
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_pause_cue_given_while_audio_is_locked_never_plays_once_it_is_unlocked(
        page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _cue_page(context, page_url)
        await _listening(page)
        await _cues_become(page, ["start"], settle_ms=0)
        output = await page.evaluate("window.__oscillators[0].ctx")
        await page.evaluate(f"window.__contexts[{output}].suspend()")
        await page.wait_for_function(f"window.__contexts[{output}].state === 'suspended'", timeout=3000)
        _gathering(page)
        for stage in voice_control.PAUSE_CUES:
            _pause(page, stage)
        await page.wait_for_timeout(500)
        await page.evaluate(f"window.__contexts[{output}].resume()")
        await page.wait_for_function(f"window.__contexts[{output}].state === 'running'", timeout=3000)
        await page.wait_for_timeout(1000)
        assert await _cues(page) == ["start"]
        await context.close()


# ── Settings: each cue, its Test, its timing, the volume ─────────────────

async def _settings(page):
    await page.click("#btn-menu")
    await page.get_by_role("button", name="Settings", exact=True).click()
    await page.locator("#settings-cue-tentative").wait_for()


@pytest.mark.asyncio(loop_scope="session")
async def test_each_test_button_plays_its_cue_once_even_when_it_is_off(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        cues = {**voice_control.CUE_DEFAULTS,
                "stop": {**voice_control.CUE_DEFAULTS["stop"], "on": False}}
        page = await _cue_page(context, page_url, cues=cues, sounds=False)
        await _settings(page)
        played = []
        for name in voice_control.CUES:
            await page.click(f"#settings-cue-{name}-test")
            played.append(name)
            await _cues_become(page, played, settle_ms=300)
        assert page.posted == [], "a Test changed a setting"
        # The brain's play_cue reaches the same call.
        page.voice.socket.send(json.dumps({"type": "cue_test", "cue": "likely"}))
        await _cues_become(page, played + ["likely"], settle_ms=300)
        page.voice.socket.send(json.dumps({"type": "cue_test", "cue": "deciding"}))
        await _cues_become(page, played + ["likely", "deciding"])
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_cue_rows_save_through_the_settings_route_and_say_when_each_plays(
        page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _cue_page(context, page_url, patience=4)
        await _settings(page)
        # Each timing says, at his patience, when it plays and its gaps to the
        # beeps either side (40, 65 and 85 percent of 4 s).
        said = {name: await page.locator(f"#settings-cue-{name}-at").text_content()
                for name in voice_control.PAUSE_CUES}
        assert "after 1.6 s of quiet. The deciding sound comes 1 s later." in said["tentative"]
        assert ("after 2.6 s of quiet. That is 1 s after the pause sound. The about-to-send "
                "sound comes 0.8 s later.") in said["deciding"]
        assert ("after 3.4 s of quiet. That is 0.8 s after the deciding sound. What you said is "
                "sent 0.6 s later.") in said["likely"]
        assert "Deciding sound" in await page.locator("label[for='settings-cue-deciding']").text_content()
        await page.select_option("#settings-cue-likely", "low")
        await page.select_option("#settings-cue-start", "off")
        tentative = page.locator("#settings-cue-tentative-percent")
        await tentative.fill("20")
        await tentative.press("Enter")
        volume = page.locator("#settings-cue-volume")
        await volume.fill("30")
        await volume.press("Enter")
        deciding = page.locator("#settings-cue-deciding-percent")
        await deciding.fill("50")
        await deciding.press("Enter")
        await page.select_option("#settings-cue-deciding", "double")
        assert await _until(lambda: len(page.posted) == 6)
        assert page.posted == [{"cues": {"likely": {"on": True, "tone": "low"}}},
                               {"cues": {"start": {"on": False}}},
                               {"cues": {"tentative": {"percent": 20}}},
                               {"volume": 30},
                               {"cues": {"deciding": {"percent": 50}}},
                               {"cues": {"deciding": {"on": True, "tone": "double"}}}]
        await page.wait_for_function(
            "document.querySelector('#settings-cue-tentative-at').textContent.includes('after 0.8 s')",
            timeout=3000)
        # The two gaps are set apart: 1.2 s after the pause sound, 1.4 s before the next.
        await page.wait_for_function(
            "document.querySelector('#settings-cue-deciding-at').textContent.includes("
            "'after 2 s of quiet. That is 1.2 s after the pause sound. The about-to-send sound "
            "comes 1.4 s later.')", timeout=3000)
        assert await _cues(page) == [], "saving played or started nothing"
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_timing_fields_cannot_cross_and_show_seconds_at_his_patience(page_playwright, page_url):
    """At his patience of 1.5 s every pause cue plays, in order, and says when.
    Each field is bounded by its neighbours that are on (5 points apart, 5 to
    95): a value typed past a neighbour is saved at the bound, never across
    it; a cue that is off holds nothing in place."""
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _cue_page(context, page_url, patience=1.5)
        await _settings(page)
        said = {name: await page.locator(f"#settings-cue-{name}-at").text_content()
                for name in voice_control.PAUSE_CUES}
        assert "after 0.6 s of quiet." in said["tentative"]
        assert "after 0.98 s of quiet." in said["deciding"]
        assert "after 1.28 s of quiet." in said["likely"] and "sent 0.22 s later" in said["likely"]

        async def bounds(name):
            field = page.locator(f"#settings-cue-{name}-percent")
            return (await field.get_attribute("min"), await field.get_attribute("max"))
        assert [await bounds(n) for n in voice_control.PAUSE_CUES] == [
            ("5", "60"), ("45", "80"), ("70", "95")]

        async def type_into(name, value):
            field = page.locator(f"#settings-cue-{name}-percent")
            await field.fill(value)
            await field.press("Enter")
        await type_into("tentative", "90")               # past deciding and likely
        await type_into("likely", "10")                  # past both, the other way
        assert await _until(lambda: len(page.posted) == 2)
        assert page.posted == [{"cues": {"tentative": {"percent": 60}}},
                               {"cues": {"likely": {"percent": 70}}}]
        await page.wait_for_function(
            "document.querySelector('#settings-cue-deciding-percent').max === '65'", timeout=3000)
        assert await bounds("deciding") == ("65", "65")   # held in place between them
        await type_into("deciding", "99")                # kept at 65: nothing to save
        await page.wait_for_timeout(500)
        assert len(page.posted) == 2

        # Off, deciding is skipped: its neighbours' bounds open up to each other.
        await page.select_option("#settings-cue-deciding", "off")
        await page.wait_for_function(
            "document.querySelector('#settings-cue-tentative-percent').max === '65'", timeout=3000)
        assert await bounds("likely") == ("65", "95")
        assert "skipped" in await page.locator("#settings-cue-deciding-at").text_content()
        await context.close()
