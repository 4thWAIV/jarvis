"""The voice page's speech input: one microphone stream, streamed at once and
continuously to the transcription socket; what it hears goes to /ws/voice as
before; mute finishes the words already spoken and releases the microphone;
held audio is sent once the recogniser is ready; and every way input can fail
stays on screen.

Headless Chromium on the built page, with Chromium's fake microphone and the
fake transcription socket from test_voice_page_captions.py; /ws/voice is a
Playwright route that records what the page sends.
"""

import asyncio
import json

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION

# Counts the page's getUserMedia calls and the tracks still live.
COUNT_MICROPHONES = """
(() => {
  window.__micOpens = 0;
  // Frames the worklet really produced: headless audio runs slower than the
  // wall clock on a busy Mac, so held audio is checked against these.
  window.__framesCaptured = 0;
  const RealWorklet = window.AudioWorkletNode;
  window.AudioWorkletNode = class extends RealWorklet {
    constructor(...args) {
      super(...args);
      this.port.addEventListener("message", (e) => {
        if (e.data?.type === "frame") window.__framesCaptured++;
      });
    }
  };
  window.__liveTracks = () => (window.__streams || []).flatMap(s => s.getTracks())
    .filter(t => t.readyState === "live").length;
  const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => {
    window.__micOpens++;
    const s = await real(c);
    (window.__streams = window.__streams || []).push(s);
    return s;
  };
})();
"""

# What voice input says when it is not simply listening: the health dot's
# title beside the meter (MicControl.tsx); the words are no longer written
# under the orb.
STATUS = "() => document.querySelector('#health-dot')?.title ?? ''"


class Voice:
    """A fake /ws/voice that records the page's messages and can drop it."""

    def __init__(self):
        self.sent = []
        self.socket = None

    def __call__(self, ws):
        self.socket = ws
        ws.on_message(lambda m: self.sent.append(json.loads(m)))

    def finals(self):
        return [m["text"] for m in self.sent if m.get("type") == "transcript"]


async def _open(p, url, script=None):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    if script:
        await page.add_init_script(f"window.__transcribe = {json.dumps(script)};")
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.add_init_script(COUNT_MICROPHONES)
    voice = Voice()
    await page.route_web_socket("**/ws/voice", voice)
    await page.goto(url + "?view=main")
    return browser, page, voice


async def _until(predicate, timeout=10.0):
    """Wait for `predicate` (sync or async) to be truthy."""
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        value = predicate()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return True
        await asyncio.sleep(0.05)
    return False


async def _ready(page):
    await page.wait_for_function(
        "window.__transcribers.some((t) => t.readyState === 1) && window.__framesSent > 0",
        timeout=10000)


@pytest.mark.asyncio
async def test_one_microphone_stream_is_streamed_from_the_start_without_gaps(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url)
        await _ready(page)
        start = await page.evaluate("window.__framesSent")
        await page.wait_for_timeout(5000)
        sent = await page.evaluate("window.__framesSent") - start
        # Ten 100 ms frames a second, continuously: five seconds is about 50.
        assert 42 <= sent <= 58, sent
        assert await page.evaluate("window.__micOpens") == 1
        assert await page.evaluate("window.__transcribers.length") == 1
        await browser.close()


@pytest.mark.asyncio
async def test_what_is_heard_goes_to_jarvis_as_before(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url)
        await _ready(page)
        await page.evaluate("window.__hear('open the', false)")
        await page.evaluate("window.__hear('open the pod bay doors', true)")
        assert await _until(voice.finals)
        assert voice.finals() == ["open the pod bay doors"]
        final = [m for m in voice.sent if m.get("type") == "transcript"][0]
        assert final["isFinal"] is True and "typed" not in final
        assert {"type": "interim", "text": "open the"} in voice.sent
        await browser.close()


@pytest.mark.asyncio
async def test_mute_sends_the_words_already_spoken_then_takes_nothing(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url,
                                           {"finalOnFinish": "turn off the lights"})
        await _ready(page)
        await page.evaluate("window.__hear('turn off the', false)")
        await page.click("#btn-mute")
        assert await _until(voice.finals)
        assert voice.finals() == ["turn off the lights"]
        assert await page.evaluate("window.__liveTracks()") == 0
        after = await page.evaluate("window.__framesSent")
        await page.wait_for_timeout(1500)
        assert await page.evaluate("window.__framesSent") == after, "audio sent while muted"
        lit = await page.evaluate("document.querySelectorAll('#mic-meter .mic-segment.on').length")
        assert lit == 0

        # Unmuted: a fresh microphone and a fresh session, streaming again.
        await page.click("#btn-mute")
        await page.wait_for_function("window.__micOpens === 2 && window.__liveTracks() === 1")
        assert await _until(lambda: page.evaluate(f"window.__framesSent > {after + 5}"))
        assert await page.evaluate("window.__transcribers.length") == 2
        await browser.close()


@pytest.mark.asyncio
async def test_a_closed_session_cannot_submit_stale_words(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url)
        await _ready(page)
        await page.click("#btn-mute")
        await page.click("#btn-mute")
        await page.wait_for_function("window.__transcribers.length === 2 && "
                                     "window.__transcribers[1].readyState === 1")
        # The first socket, long closed, delivers a final after all.
        await page.evaluate("""() => { const old = window.__transcribers[0];
          old.onmessage && old.onmessage({ data: JSON.stringify(
            { session: old.session, type: 'final', segment: 9, text: 'stale words' }) }); }""")
        await page.wait_for_timeout(500)
        assert voice.finals() == []
        await browser.close()


@pytest.mark.asyncio
async def test_audio_said_while_the_recogniser_loads_is_held_then_sent(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, {"readyDelay": 3000})
        await page.wait_for_function("window.__transcribers.length === 1")
        await page.wait_for_timeout(1500)
        assert await page.evaluate("window.__framesSent") == 0
        assert "loading" in (await page.evaluate(STATUS)).lower() or \
            "connecting" in (await page.evaluate(STATUS)).lower()
        # Ready at 3 s: the held frames (about 30) go first, at one and a half
        # times real time, so by 10 s after that the backlog has drained and
        # every frame the worklet produced has been sent, bar the one or two
        # waiting for the next send tick.
        await page.wait_for_timeout(11500)
        sent, captured = await page.evaluate("[window.__framesSent, window.__framesCaptured]")
        assert captured - 3 <= sent <= captured, (sent, captured)
        assert await page.evaluate("window.__transcribers.length") == 1
        assert await page.evaluate(STATUS) == ""
        await browser.close()


@pytest.mark.asyncio
async def test_a_recogniser_that_never_gets_ready_drops_the_attempt_and_says_so(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, {"readyDelay": 60000})
        await page.wait_for_function(
            "document.querySelector('#health-dot')?.title.includes('not sent')",
            timeout=20000)
        assert await page.evaluate("window.__framesSent") == 0
        assert voice.finals() == []
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("code, message", [
    ("busy", "Another JARVIS page is listening. Typing still works here."),
    ("model-unavailable", "The speech model is not installed. Run setup."),
])
async def test_a_busy_or_missing_recogniser_stays_on_screen(page_url, code, message):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, {"error": {"code": code,
                                                                   "message": message}})
        await page.wait_for_function(
            f"document.querySelector('#health-dot')?.title === {json.dumps(message)}",
            timeout=10000)
        await page.wait_for_timeout(6000)       # longer than the old error toast
        assert await page.evaluate(STATUS) == message
        await browser.close()


@pytest.mark.asyncio
async def test_a_final_heard_while_jarvis_is_disconnected_is_reported_not_lost(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url)
        await _ready(page)
        await voice.socket.close()
        await page.wait_for_timeout(200)
        await page.evaluate("window.__hear('deploy it', true)")
        await page.wait_for_function(
            "document.querySelector('#health-dot')?.title.includes('not sent')")
        assert voice.finals() == []
        await browser.close()
