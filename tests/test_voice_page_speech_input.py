"""The voice page's speech input: one microphone stream, streamed at once and
continuously to the transcription socket; what it hears goes to /ws/voice as
before; mute finishes the words already spoken and sends nothing more, with
the microphone kept open; mic off finishes them and releases the microphone;
held audio is sent once the recogniser is ready; and every way input can fail
stays on screen.

Headless Chromium on the built page, with Chromium's fake microphone and the
fake transcription socket from test_voice_page_captions.py; /ws/voice is a
Playwright route that records what the page sends.
"""

import json

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.voice_page_input import start_listening
from tests.fixtures.waiting import until_async

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


async def _open(p, url, script=None, listen=True, before=None):
    """`before`: a script run before the fakes are installed."""
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    # Routed before the fake transcription socket is installed, so the page
    # holds the fake itself, not Playwright's relay around it (see
    # test_voice_page_quiet_input._page).
    voice = Voice()
    await page.route_web_socket("**/ws/voice", voice)
    if before:
        await page.add_init_script(before)
    if script:
        await page.add_init_script(f"window.__transcribe = {json.dumps(script)};")
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.add_init_script(COUNT_MICROPHONES)
    # The Conversation view, where the microphone control is.
    await page.goto(url)
    if listen:
        await start_listening(page)
    return browser, page, voice


async def _ready(page):
    await page.wait_for_function(
        "window.__transcribers.some((t) => t.readyState === 1) && window.__framesSent > 0",
        timeout=10000)


@pytest.mark.asyncio(loop_scope="session")
async def test_one_microphone_stream_is_streamed_from_the_start_without_gaps(page_playwright, page_url):
    async with page_playwright as p:
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


@pytest.mark.asyncio(loop_scope="session")
async def test_what_is_heard_goes_to_jarvis_as_before(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, voice = await _open(p, page_url)
        await _ready(page)
        await page.evaluate("window.__hear('open the', false)")
        await page.evaluate("window.__hear('open the pod bay doors', true)")
        assert await until_async(voice.finals)
        assert voice.finals() == ["open the pod bay doors"]
        final = [m for m in voice.sent if m.get("type") == "transcript"][0]
        assert final["isFinal"] is True and "typed" not in final
        interim = [m for m in voice.sent if m.get("type") == "interim"][0]
        assert interim["text"] == "open the"
        # Every word carries this page and the capture epoch it was heard in.
        assert interim["clientId"] == final["clientId"] and interim["clientId"].startswith("page-")
        assert final["captureEpoch"] == interim["captureEpoch"] >= 1
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_mute_commits_nothing_and_takes_nothing_with_the_microphone_kept(page_playwright, page_url):
    """Mute (decided as proposed): the words already spoken are finished but
    not sent (they become an unsent draft), the server drops what it was
    gathering, nothing more is sent, and the microphone stays open."""
    async with page_playwright as p:
        browser, page, voice = await _open(p, page_url,
                                           {"finalOnFinish": "turn off the lights"})
        await _ready(page)
        await page.evaluate("window.__hear('turn off the', false)")
        await page.click("#btn-mute")
        assert await until_async(lambda: [m for m in voice.sent if m.get("type") == "turn_discard"])
        await page.wait_for_function("window.__transcribers[0].readyState === 3", timeout=15000)
        assert voice.finals() == []
        assert await page.evaluate("window.__liveTracks()") == 1, "mute released the microphone"
        after = await page.evaluate("window.__framesSent")
        await page.wait_for_timeout(1500)
        assert await page.evaluate("window.__framesSent") == after, "audio sent while muted"
        lit = await page.evaluate("document.querySelectorAll('#mic-meter .mic-segment.on').length")
        assert lit == 0

        # Unmuted: the same microphone and a fresh session in a new epoch.
        await page.click("#btn-mute")
        assert await until_async(lambda: page.evaluate(f"window.__framesSent > {after + 5}"))
        assert await page.evaluate("window.__micOpens") == 1
        starts = await page.evaluate("window.__starts")
        assert len(starts) == 2 and starts[1]["captureEpoch"] > starts[0]["captureEpoch"]
        assert voice.finals() == [], "unmuting sent the old words"
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_mic_off_sends_the_words_already_spoken_then_releases_the_microphone(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, voice = await _open(p, page_url,
                                           {"finalOnFinish": "turn off the lights"})
        await _ready(page)
        await page.evaluate("window.__hear('turn off the', false)")
        await page.click("#btn-mic-off")
        assert await until_async(voice.finals)
        assert voice.finals() == ["turn off the lights"]
        await page.wait_for_function("window.__liveTracks() === 0")
        after = await page.evaluate("window.__framesSent")

        # On again: a fresh microphone and a fresh session, streaming again.
        await page.click("#btn-mic-off")
        await page.wait_for_function("window.__micOpens === 2 && window.__liveTracks() === 1")
        assert await until_async(lambda: page.evaluate(f"window.__framesSent > {after + 5}"))
        assert await page.evaluate("window.__transcribers.length") == 2
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_closed_session_cannot_submit_stale_words(page_playwright, page_url):
    async with page_playwright as p:
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


@pytest.mark.asyncio(loop_scope="session")
async def test_audio_said_while_the_recogniser_loads_is_held_then_sent(page_playwright, page_url):
    async with page_playwright as p:
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


@pytest.mark.asyncio(loop_scope="session")
async def test_a_recogniser_that_never_gets_ready_drops_the_attempt_and_says_so(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, voice = await _open(p, page_url, {"readyDelay": 60000})
        await page.wait_for_function(
            "document.querySelector('#health-dot')?.title.includes('not sent')",
            timeout=20000)
        assert await page.evaluate("window.__framesSent") == 0
        assert voice.finals() == []
        # The drop is reported on the abort, with the ten seconds it held.
        drop = (await page.evaluate("window.__aborts"))[0]["dropped"]
        assert drop["reason"] == "tooSlow" and 9.5 <= drop["lostSec"] <= 10.5, drop
        await browser.close()


# Chrome runs a hidden tab's timers about once a second; the worklet's
# frames still arrive every 100 ms. Every timer here runs at most once a
# second, as in a hidden tab.
ONCE_A_SECOND_TIMERS = """
(() => {
  const every = window.setInterval.bind(window), after = window.setTimeout.bind(window);
  window.setInterval = (fn, ms, ...a) => every(fn, Math.max(ms || 0, 1000), ...a);
  window.setTimeout = (fn, ms, ...a) => after(fn, Math.max(ms || 0, 1000), ...a);
})();
"""


@pytest.mark.asyncio(loop_scope="session")
async def test_with_timers_running_once_a_second_every_frame_is_sent_and_nothing_dropped(
        page_playwright, page_url):
    async with page_playwright as p:
        browser, page, voice = await _open(p, page_url, before=ONCE_A_SECOND_TIMERS)
        await page.wait_for_function(
            "window.__transcribers.some((t) => t.readyState === 1) && window.__framesSent > 0",
            timeout=20000)
        # Fifteen seconds: past the ten seconds the page holds, where the old
        # pacing (three frames a timer tick, 0.7 s more held every second)
        # dropped everything it held and started again.
        behind = []
        for _ in range(15):
            await page.wait_for_timeout(1000)
            sent, captured = await page.evaluate("[window.__framesSent, window.__framesCaptured]")
            behind.append(captured - sent)
        # What was held while the slowed connection opened drains at one and
        # a half times real time; after that, under a second (ten frames)
        # waits at any time.
        assert max(behind[-5:]) < 10, behind
        held = [t["heldSec"] for t in await page.evaluate("window.__telemetry")]
        assert held and max(held[-5:]) < 1.0, held
        assert await page.evaluate("window.__aborts") == []
        assert await page.evaluate("window.__transcribers.length") == 1
        assert await page.evaluate(STATUS) == ""
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_backed_up_connection_drop_is_reported_with_what_it_held(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, voice = await _open(p, page_url)
        await _ready(page)
        await page.evaluate("window.__transcribers.find((t) => t.readyState === 1).bufferedAmount = 1e9")
        await page.wait_for_function("window.__aborts.length > 0", timeout=15000)
        drop = (await page.evaluate("window.__aborts"))[0]["dropped"]
        assert drop["reason"] == "backedUp" and 0 < drop["lostSec"] < 1, drop
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("code, message, shown", [
    ("busy", "Another JARVIS page is listening. Typing still works here.",
     "Another JARVIS page is listening. Typing still works here."),
    # Worded by its code in the interface's language, the server's own text after it.
    ("model-unavailable", "The speech model is not installed. Run setup.",
     "Speech recognition is not available. Detail: The speech model is not installed. Run setup."),
])
async def test_a_busy_or_missing_recogniser_stays_on_screen(page_playwright, page_url, code, message, shown):
    async with page_playwright as p:
        browser, page, voice = await _open(p, page_url, {"error": {"code": code,
                                                                   "message": message}})
        await page.wait_for_function(
            f"document.querySelector('#health-dot')?.title === {json.dumps(shown)}",
            timeout=10000)
        await page.wait_for_timeout(6000)       # longer than the old error toast
        assert await page.evaluate(STATUS) == shown
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_listening_stops_while_jarvis_is_disconnected_and_says_so(page_playwright, page_url):
    """Nothing is heard under the old epoch once the voice socket drops: the
    recogniser and the microphone are let go, and the page says why."""
    async with page_playwright as p:
        browser, page, voice = await _open(p, page_url)
        await _ready(page)
        await voice.socket.close()
        await page.wait_for_function("window.__liveTracks() === 0", timeout=15000)
        await page.wait_for_function(
            "document.querySelector('#health-dot')?.title.includes('not sent')")
        assert await page.evaluate("window.__transcribers.every((t) => t.readyState === 3)")
        assert voice.finals() == []
        await browser.close()
