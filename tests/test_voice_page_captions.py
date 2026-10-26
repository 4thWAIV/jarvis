"""The caption box: a back-and-forth conversation, always four lines tall,
left-aligned, centred above the footer between the two side columns. The
user's words appear as the speech recognition hears them; JARVIS's appear
sentence by sentence as they are spoken, with the one being spoken now
highlighted; and it scrolls itself so what is being said now is in view.

Headless Chromium on the built page (built by test_agents_list_fits.py's
fixture). JARVIS's voice socket is replaced by Playwright with one that sends
spoken sentences, and its speech recognition socket by a stand-in the test
can make "hear" words; Chromium's fake microphone stands in for the real one,
so no server and no microphone are needed.
"""

import asyncio
import base64
import io
import json
import struct
import wave

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401

# Autoplay without a click, and Chromium's fake microphone (a tone) granted
# without asking, so the page captures from the start as it does for him.
BROWSER_ARGS = ["--autoplay-policy=no-user-gesture-required",
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]

# A stand-in for JARVIS's speech recognition socket (/ws/transcribe): every
# other socket is the real WebSocket. It answers `start` with `ready`, counts
# the audio frames the page streams to it (`window.__framesSent`), and
# `window.__hear(text, final)` makes the newest one send an interim or a
# final, as the recogniser does when it hears words. `window.__transcribe`
# can set `readyDelay` (ms before ready), `error` ({code, message} instead
# of ready) and `finalOnFinish` (the text of a final sent on finish).
FAKE_TRANSCRIPTION = """
(() => {
  const Real = window.WebSocket;
  const live = [];
  window.__transcribers = live;
  window.__framesSent = 0;
  window.__telemetry = [];          // every telemetry message the page sent
  window.__starts = [];             // every start message
  window.__transcribe = window.__transcribe || {};
  class FakeTranscriber {
    constructor(url) {
      this.url = url; this.readyState = 0; this.bufferedAmount = 0;
      this.binaryType = "blob"; this.segment = 0; this.session = "fake" + live.length;
      this.createdAt = performance.now();
      live.push(this);
      setTimeout(() => { this.readyState = 1; this.onopen && this.onopen({}); }, 0);
    }
    _emit(msg) {
      if (this.readyState !== 1) return;
      this.onmessage && this.onmessage({ data: JSON.stringify({ session: this.session, ...msg }) });
    }
    send(data) {
      if (typeof data !== "string") { window.__framesSent++; return; }
      const msg = JSON.parse(data);
      const script = window.__transcribe;
      if (msg.type === "telemetry") window.__telemetry.push(msg);
      if (msg.type === "start") {
        window.__starts.push(msg);
        if (script.error) setTimeout(() => { this._emit({ type: "error", ...script.error }); this.close(); }, 0);
        else setTimeout(() => this._emit({ type: "ready" }), script.readyDelay || 0);
      }
      if (msg.type === "finish") setTimeout(() => {
        if (script.finalOnFinish) this._emit({ type: "final", segment: this.segment++, text: script.finalOnFinish });
        this._emit({ type: "finished" }); this.close();
      }, 0);
      if (msg.type === "abort") this.close();
    }
    close() {
      if (this.readyState === 3) return;
      this.readyState = 3;
      setTimeout(() => this.onclose && this.onclose({}), 0);
    }
  }
  window.WebSocket = function (url, protocols) {
    return String(url).endsWith("/ws/transcribe") ? new FakeTranscriber(url)
                                                   : new Real(url, protocols);
  };
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  window.WebSocket.prototype = Real.prototype;
  window.__hear = (text, final) => {
    const t = [...live].reverse().find((x) => x.readyState === 1);
    if (!t) throw new Error("no open transcription socket");
    t._emit(final ? { type: "final", segment: t.segment++, text } : { type: "interim", segment: t.segment, text });
  };
})();
"""


def _silence_wav(seconds: float) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * int(16000 * seconds))
    return base64.b64encode(buf.getvalue()).decode()


async def _open(p, url, on_socket=None):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    if on_socket is not None:
        await page.route_web_socket("**/ws/voice", on_socket)
    # The captions are the Main view's, which the header does not offer yet.
    await page.goto(url + "?view=main")
    return browser, page


def _speaker(utterances, clip_seconds=0.3, delay=1.5, after_user=False):
    """A fake /ws/voice that speaks each utterance (a list of sentences):
    after `delay`, or, with `after_user`, once the page sends the user's
    final words, as a real conversation goes."""
    clip = _silence_wav(clip_seconds)

    def on_socket(ws):
        def speak_now():
            ws.send(json.dumps({"type": "status", "state": "speaking"}))
            for utt, sentences in enumerate(utterances, start=1):
                for idx, text in enumerate(sentences):
                    ws.send(json.dumps({"type": "audio", "utt": utt, "idx": idx,
                                        "data": clip, "text": text}))

        if after_user:
            def heard(message):
                msg = json.loads(message)
                if msg.get("type") == "transcript" and msg.get("isFinal"):
                    speak_now()
            ws.on_message(heard)
        else:
            async def later():
                await asyncio.sleep(delay)
                speak_now()
            asyncio.ensure_future(later())
    return on_socket


TURNS = """() => [...document.querySelectorAll('#caption-box .caption-turn')].map(p => ({
  speaker: p.classList.contains('jarvis') ? 'jarvis' : 'user', text: p.textContent,
  now: [...p.querySelectorAll('.now')].map(s => s.textContent.trim())}))"""

NOW = """() => { const w = document.querySelector('#caption-box .spoken-word.now');
  if (!w) return null; const css = getComputedStyle(w);
  return {word: w.textContent.trim(), glow: css.textShadow, background: css.backgroundColor}; }"""


@pytest.mark.asyncio
async def test_always_four_lines_left_aligned_between_the_side_columns(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        box = await page.evaluate("""() => {
          const el = document.getElementById('caption-box');
          const b = el.getBoundingClientRect(), css = getComputedStyle(el);
          const scroll = el.querySelector('.caption-scroll'), sc = getComputedStyle(scroll);
          const left = document.getElementById('left-column').getBoundingClientRect();
                    return {left: b.left, rightGap: innerWidth - b.right, column: left.right,
                  bottom: b.bottom, columnBottom: left.bottom, align: sc.textAlign,
                  lines: scroll.clientHeight / parseFloat(sc.lineHeight)};
        }""")
        # Centred between the two side panels, the same gap to each.
        assert box["left"] > box["column"], box
        assert abs((box["left"] - box["column"]) - (box["rightGap"] - box["column"])) < 0.5, box
        # The bottom of the centre stack, level with the side panels.
        assert abs(box["bottom"] - box["columnBottom"]) < 0.5, box
        assert box["lines"] == 4, box                     # exactly four, even empty
        assert box["align"] in ("left", "start"), box
        await browser.close()


@pytest.mark.asyncio
async def test_the_user_s_words_show_as_heard_and_then_jarvis_answers(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, _speaker(
            [["Good evening, sir.", "All six agents are idle."]], after_user=True))
        await page.wait_for_function("window.__transcribers.some((t) => t.readyState === 1)", timeout=10000)
        await page.evaluate("window.__hear('how are the', false)")
        await page.evaluate("window.__hear('how are the agents', false)")
        # The page passes on what is heard at most every so often, so wait.
        await page.wait_for_function(
            "document.querySelector('#caption-box').textContent === 'how are the agents'",
            timeout=5000)
        assert (await page.evaluate(TURNS))[-1] == {
            "speaker": "user", "text": "how are the agents", "now": []}
        await page.evaluate("window.__hear('how are the agents doing', true)")

        await page.wait_for_function(
            "document.querySelector('#caption-box').textContent.includes('agents are idle')",
            timeout=10000)
        turns = await page.evaluate(TURNS)
        assert [(t["speaker"], t["text"]) for t in turns] == [
            ("user", "how are the agents doing"),
            ("jarvis", "Good evening, sir. All six agents are idle."),
        ]
        await browser.close()


@pytest.mark.asyncio
async def test_only_the_word_being_spoken_glows_and_it_moves_along(page_url):
    """One word at a time glows (bright text and a halo, no box); it moves
    through the sentence while the sentence plays; nothing glows after."""
    sentence = "one two three four five six seven eight"
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, _speaker([[sentence]], clip_seconds=3.0))
        await page.wait_for_function(
            "document.querySelector('#caption-box .spoken-word.now')", timeout=10000)
        seen = []
        for _ in range(12):
            now = await page.evaluate(NOW)
            if now:
                assert len(now["word"].split()) == 1, now
                assert now["glow"] != "none", "the word does not glow"
                assert now["background"] in ("rgba(0, 0, 0, 0)", "transparent"), now
                if not seen or seen[-1] != now["word"]:
                    seen.append(now["word"])
            await page.wait_for_timeout(250)
        assert len(seen) >= 4, seen                       # it moved along the sentence
        assert seen == sorted(seen, key=sentence.split().index), seen   # in order
        await page.wait_for_function(
            "!document.querySelector('#caption-box .spoken-word.now')", timeout=10000)
        await browser.close()


@pytest.mark.asyncio
async def test_it_scrolls_to_what_is_being_said_now(page_url):
    long = [[f"Sentence {u}-{i} with enough words in it to take up a line or so."
             for i in range(3)] for u in range(4)]
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, _speaker(long, clip_seconds=0.2))
        await page.wait_for_function(
            "document.querySelector('#caption-box').textContent.includes('Sentence 3-2')",
            timeout=20000)
        await page.wait_for_timeout(200)
        view = await page.evaluate("""() => {
          const box = document.querySelector('#caption-box .caption-scroll');
          const last = [...box.querySelectorAll('.caption-turn span')].pop().getBoundingClientRect();
          const b = box.getBoundingClientRect();
          const line = parseFloat(getComputedStyle(box).lineHeight);
          return {overflowing: box.scrollHeight > box.clientHeight,
                  atEnd: box.scrollTop + box.clientHeight >= box.scrollHeight - 1,
                  lastVisible: last.bottom <= b.bottom + 1 && last.top >= b.top - 1,
                  // No sliver of an earlier line at the top: the top line
                  // starts exactly at the edge.
                  wholeLines: box.scrollTop % line === 0};
        }""")
        assert view == {"overflowing": True, "atEnd": True, "lastVisible": True,
                        "wholeLines": True}, view
        await browser.close()
