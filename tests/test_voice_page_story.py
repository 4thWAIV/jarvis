"""A page package's story told on the page, by what happens in a real browser:
the example story deck opens in its sandboxed frame, JARVIS's real speech
scheduler speaks every beat through the voice socket with a stand-in
synthesizer whose audio lasts as long as the words would, and the frame's
helper (jarvis/package_story.js) rings each beat's targets from the moment
that beat's audio starts playing until it has played.

The page's own playback is the clock: each AudioBufferSourceNode.start is
recorded in the page, each ring added or removed in the frame, both by
Date.now(). The page's played acks drive the scheduler as they do for real.
Every /api/packages request is answered by JARVIS's real app (TestClient),
except Tell the story, which is answered by the server's own
`_package_story` on the test's loop, where the scheduler runs.
"""

import asyncio
import importlib
import io
import json
import struct
import wave

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_package_modes import EXAMPLES
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

SECONDS_PER_WORD = 0.12
SLACK = 0.35          # seconds: a frame, a postMessage and a paint, on a busy machine

RECORD = """
(() => {
  if (window === window.top) {
    window.__audioStarts = [];
    const start = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args) {
      window.__audioStarts.push({at: Date.now(), seconds: this.buffer ? this.buffer.duration : 0});
      return start.apply(this, args);
    };
  } else {
    window.__rings = [];
    new MutationObserver((records) => {
      for (const r of records) {
        for (const n of r.addedNodes) if (n.classList && n.classList.contains("jarvis-story-ring"))
          window.__rings.push({beat: n.dataset.beat, target: n.dataset.target, added: Date.now()});
        for (const n of r.removedNodes) if (n.classList && n.classList.contains("jarvis-story-ring"))
          for (const ring of window.__rings)
            if (ring.beat === n.dataset.beat && ring.target === n.dataset.target && !ring.removed)
              ring.removed = Date.now();
      }
    }).observe(document, {childList: true, subtree: true});
  }
})();
"""


def _wav(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * int(16000 * seconds))
    return buf.getvalue()


async def _synthesize(text: str) -> bytes:
    """A stand-in for Fish: silence as long as the words take to say."""
    await asyncio.sleep(0.02)
    return _wav(max(0.4, SECONDS_PER_WORD * len(text.split())))


class _Voice:
    """The voice socket: the scheduler's frames to the page, the page's
    played acks back to the scheduler."""

    def __init__(self):
        self.ws, self.speech, self.audio = None, None, []

    def __call__(self, ws):
        self.ws = ws
        ws.on_message(self._heard)

    def _heard(self, raw):
        msg = json.loads(raw)
        if msg.get("type") == "played" and self.speech is not None:
            asyncio.get_running_loop().create_task(
                self.speech.played(int(msg["utt"]), int(msg["idx"])))

    async def emit(self, msg):
        if msg.get("type") == "audio":
            self.audio.append(msg)
        self.ws.send(json.dumps(msg))


@pytest.fixture
def jarvis_app(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    from jarvis import data_paths, run_store, work_packages
    importlib.reload(data_paths)
    importlib.reload(run_store)
    from jarvis import server
    importlib.reload(server)
    import sys
    sys.path.insert(0, str(EXAMPLES))
    try:
        import zip_package
    finally:
        sys.path.remove(str(EXAMPLES))
    archive = tmp_path / "story-deck.zip"
    assert zip_package.main([str(EXAMPLES / "story-deck"), str(archive)]) == 0
    package, _ = work_packages.unpack(archive, "5t0ryd3c", clear_mark=lambda _f: None)
    from fastapi.testclient import TestClient
    return server, TestClient(server.app), package


def _through(server, client):
    """The page's /api/packages requests: JARVIS's own app, and Tell the
    story through the server's `_package_story` on this loop."""
    async def handle(route):
        request = route.request
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1]
        if request.method == "POST" and path.endswith("/story"):
            body = json.loads(request.post_data or "{}")
            done, said = await server._package_story(body.get("action", ""),
                                                     path.split("/")[3], body.get("slide"))
            await route.fulfill(status=200 if done else 409, content_type="application/json",
                                body=json.dumps({"said": said} if done else {"error": said}))
            return
        r = await asyncio.to_thread(client.request, request.method, path)
        await route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)
    return handle


@pytest.fixture
def voice(jarvis_app, monkeypatch):
    """The voice socket, with the real scheduler behind it as the server's."""
    from jarvis.speech import SpeechScheduler
    server, _, _ = jarvis_app
    voice = _Voice()
    voice.speech = SpeechScheduler(_synthesize, voice.emit)
    monkeypatch.setattr(server, "speech", voice.speech)
    monkeypatch.setattr(server, "_voice_emit", voice.emit)
    monkeypatch.setattr(server, "voice_clients", {object()})
    return voice


async def _deck(p, page_url, jarvis_app, voice):
    """The page with the deck open in its frame, and the scheduler started:
    (browser, page, the frame's locator)."""
    from jarvis import packages_api
    server, client, package = jarvis_app
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                          # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.add_init_script(RECORD)
    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", lambda ws: None)
    await page.route("**/api/packages/**", _through(server, client))
    await page.goto(page_url)
    await page.wait_for_function("window.__transcribers.some((t) => t.readyState === 1)",
                                 timeout=10000)
    await voice.speech.start()
    voice.ws.send(json.dumps({"type": "package_page", "package": packages_api.describe(package)}))
    frame = page.frame_locator("#package-view iframe")
    await async_api.expect(frame.locator("h2").first).to_have_text(
        "Three things a good pot of tea needs")
    return browser, page, frame


@pytest.mark.asyncio(loop_scope="session")
async def test_each_beat_is_ringed_while_its_words_play(page_playwright, page_url, jarvis_app,
                                                       voice):
    async with page_playwright as p:
        browser, page, frame = await _deck(p, page_url, jarvis_app, voice)
        try:
            assert await page.locator("#package-view iframe").get_attribute("sandbox") == \
                "allow-scripts"
            await page.click("#package-view .package-story button:has-text('Tell the story')")
            await async_api.expect(page.locator(".package-story-now")).to_have_text(
                "Telling slide 1 of 3.")
            # Part way through: the detail view the story opened is open,
            # and ringed, while its words are heard.
            await async_api.expect(frame.locator(
                ".jarvis-story-ring[data-target='#detail-green .temp']")).to_have_count(
                1, timeout=60000)
            await async_api.expect(frame.locator("#detail-green")).to_be_visible()
            await async_api.expect(page.locator(".package-story-now")).to_have_text(
                "The story is told.", timeout=60000)
            await async_api.expect(frame.locator(".jarvis-story-ring")).to_have_count(0)
            await async_api.expect(frame.locator("#detail-green")).to_be_hidden()

            starts = await page.evaluate("window.__audioStarts")
            inner = next(f for f in page.frames if "/files/index.html" in f.url)
            rings = await inner.evaluate("window.__rings")
        finally:
            await voice.speech.stop()
        await browser.close()

    # One start per audio frame, in order; a beat's audio is its frames.
    assert len(starts) == len(voice.audio)
    beats: dict[str, list[dict]] = {}
    for frame_msg, start in zip(voice.audio, starts):
        beats.setdefault(frame_msg["cue"]["beat"], []).append(
            {"at": start["at"] / 1000, "end": (start["at"] / 1000) + start["seconds"]})
    assert list(beats)[:2] == ["1.1", "1.2"] and len(beats) == 12
    for beat, played in beats.items():
        ringed = [r for r in rings if r["beat"] == beat]
        assert ringed, f"beat {beat} was never ringed"
        for ring in ringed:
            added, removed = ring["added"] / 1000, ring["removed"] / 1000
            assert abs(added - played[0]["at"]) < SLACK, \
                f"beat {beat}'s ring came {added - played[0]['at']:+.2f}s from its audio"
            assert removed >= played[-1]["end"] - SLACK, \
                f"beat {beat}'s ring went {played[-1]['end'] - removed:.2f}s before its audio ended"
            assert removed <= played[-1]["end"] + SLACK, \
                f"beat {beat}'s ring stayed {removed - played[-1]['end']:.2f}s after its audio"


@pytest.mark.asyncio(loop_scope="session")
async def test_stop_ends_the_voice_and_the_rings_and_a_slide_can_be_chosen(
        page_playwright, page_url, jarvis_app, voice):
    async with page_playwright as p:
        browser, page, frame = await _deck(p, page_url, jarvis_app, voice)
        try:
            await page.select_option("#package-view .package-story select", "2")
            await page.click("#package-view .package-story button:has-text('Tell the story')")
            await async_api.expect(page.locator(".package-story-now")).to_have_text(
                "Telling slide 2 of 3.")
            await async_api.expect(frame.locator(
                ".jarvis-story-ring[data-beat='2.1']")).to_have_count(1, timeout=20000)
            await page.click("#package-view .package-story button:has-text('Stop')")
            await async_api.expect(page.locator(".package-story-now")).to_have_text("Stopped.")
            await async_api.expect(frame.locator(".jarvis-story-ring")).to_have_count(0)
            heard = len(voice.audio)
            await asyncio.sleep(1.5)
            assert len(voice.audio) == heard, "nothing more is said once it is stopped"
            assert all(m["cue"]["slide"] == 2 for m in voice.audio)
        finally:
            await voice.speech.stop()
        await browser.close()
