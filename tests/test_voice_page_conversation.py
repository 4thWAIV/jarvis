"""The centre area's views and the Conversation view.

The header offers Conversation, shown to begin with; Main (the capture boxes
above the captions) is kept but not offered, and `?view=main` opens it. The
Conversation view, top to bottom: a six-line history (JARVIS in blue, Tim in
white), the open captures, a three-line blue box with what JARVIS is saying,
and a white box with what Tim is saying. Tim's finished words wait in the
white box until JARVIS answers, then move into the history; JARVIS's words
move there once he has finished. Words the brain made a capture of become a
collapsed item in the history.

Headless Chromium on the built page, with JARVIS's voice socket replaced by
Playwright and the speech recognition by a stand-in (test_voice_page_captions).
"""

import asyncio
import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION, _silence_wav

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


class _Voice:
    """A fake /ws/voice: records what the page sends and can send it messages."""

    def __init__(self):
        self.ws = None
        self.got = []

    def __call__(self, ws):
        self.ws = ws
        ws.on_message(lambda m: self.got.append(json.loads(m)))

    def send(self, *msgs):
        for m in msgs:
            self.ws.send(json.dumps(m))


async def _open(p, url, voice=None):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", voice or _Voice())
    await page.goto(url)
    await page.wait_for_function("window.__transcribers.some((t) => t.readyState === 1)", timeout=10000)
    return browser, page


BOXES = """() => {
  const q = s => document.querySelector(s);
  const text = s => [...document.querySelectorAll(s)].map(e => e.textContent.trim());
  return {history: text('.conversation-history .history-turn'),
          historyWho: [...document.querySelectorAll('.conversation-history .history-turn')]
            .map(e => e.classList.contains('jarvis') ? 'jarvis' : 'user'),
          speaking: q('.conversation-speaking').textContent.trim(),
          hearing: q('.conversation-hearing').textContent.trim(),
          now: text('.conversation-speaking .spoken-word.now')};
}"""


@pytest.mark.asyncio
async def test_conversation_and_folders_are_the_tabs_offered_and_main_opens_by_address(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        tabs = await page.evaluate(
            "() => [...document.querySelectorAll('.centre-tabs [role=tab]')].map(t => [t.textContent, t.ariaSelected])")
        # The words, not icons; Conversation selected to begin with.
        assert tabs == [["Conversation", "true"], ["Folders", "false"]], tabs
        fits = await page.evaluate("""() => { const t = document.querySelector('.centre-tabs [role=tab]');
          return {fits: t.scrollWidth <= t.clientWidth, wider: t.clientWidth > 36,
                  caps: getComputedStyle(t).textTransform}; }""")
        assert fits == {"fits": True, "wider": True, "caps": "uppercase"}, fits
        assert await page.locator("#conversation").count() == 1
        assert await page.locator("#centre-bottom").count() == 0
        await page.goto(page_url + "?view=main")
        await page.wait_for_selector("#caption-box")
        assert await page.locator("#conversation").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_the_white_box_is_at_the_bottom_and_the_history_fills_the_height(page_url):
    """Anchored to the bottom: the white box sits on the footer's gap, the
    four-line blue box above it, and the history takes every bit of height
    left, up to the header, scrolling."""
    voice = _Voice()
    clip = _silence_wav(0.2)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        geo = """() => {
          const r = s => document.querySelector(s).getBoundingClientRect();
          const e = s => document.querySelector(s);
          const left = document.getElementById('left-column').getBoundingClientRect();
          const right = document.getElementById('right-column').getBoundingClientRect();
          const c = r('#conversation');
          return {header: r('.centre-tabs'), c, history: r('.conversation-history'),
                  speaking: r('.conversation-speaking'), hearing: r('.conversation-hearing'),
                  speakingLines: (e('.conversation-speaking').clientHeight - 16) / 20,
                  hearingLines: (e('.conversation-hearing').clientHeight - 16) / 20,
                  historyScroll: [e('.conversation-history').scrollHeight, e('.conversation-history').clientHeight,
                                  getComputedStyle(e('.conversation-history')).overflowY],
                  footerTop: document.getElementById('footer').getBoundingClientRect().top,
                  gaps: [c.left - left.right, right.left - c.right], columnBottom: left.bottom};
        }"""
        got = await page.evaluate(geo)
        # Bottom up: the white box, then the blue box, then the history.
        assert abs(got["hearing"]["bottom"] - got["columnBottom"]) < 0.5, got
        assert got["history"]["bottom"] <= got["speaking"]["top"] < got["hearing"]["top"], got
        assert got["speakingLines"] == 4 and got["hearingLines"] == 1, got
        # The history fills up to just under the header.
        assert 0 < got["history"]["top"] - got["header"]["bottom"] <= 12, got
        assert got["history"]["top"] == got["c"]["top"], got
        assert got["gaps"][0] > 0 and abs(got["gaps"][0] - got["gaps"][1]) < 0.5, got

        # The white box grows to four lines and no further.
        await page.evaluate("window.__hear(" + json.dumps(" ".join(["what is going on"] * 80)) + ", false)")
        got = await page.evaluate(geo)
        assert got["hearingLines"] == 4, got
        assert abs(got["hearing"]["bottom"] - got["columnBottom"]) < 0.5, "it grows upward"

        # Many turns: the history keeps every one and scrolls.
        await page.evaluate("window.__hear('one more thing', true)")
        for n in range(1, 46):
            voice.send({"type": "status", "state": "speaking"},
                       {"type": "audio", "utt": n, "idx": 0, "data": clip,
                        "text": f"Answer number {n}, sir.", "kind": "reply"},
                       {"type": "status", "state": "idle"})
        await page.wait_for_function(
            "document.querySelectorAll('.history-turn').length >= 40", timeout=30000)
        scroll_height, client_height, overflow = (await page.evaluate(geo))["historyScroll"]
        assert scroll_height > client_height and overflow == "auto", (scroll_height, client_height)
        await browser.close()


@pytest.mark.asyncio
async def test_a_turn_moves_from_the_live_boxes_into_the_history(page_url):
    voice = _Voice()
    clip = _silence_wav(1.2)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.evaluate("window.__hear('how are the', false)")
        got = await page.evaluate(BOXES)
        assert got["hearing"] == "how are the" and got["history"] == [], got

        await page.evaluate("window.__hear('how are the agents', true)")
        await asyncio.sleep(0.2)
        got = await page.evaluate(BOXES)
        assert got["hearing"] == "how are the agents" and got["history"] == [], \
            "his finished words wait in the white box until JARVIS answers"
        sent = [m for m in voice.got if m.get("type") == "transcript"]
        assert sent and isinstance(sent[-1].get("id"), int), sent

        voice.send({"type": "status", "state": "speaking"},
                   {"type": "audio", "utt": 1, "idx": 0, "data": clip,
                    "text": "All six agents are idle, sir.", "kind": "reply"})
        await page.wait_for_function(
            "document.querySelector('.conversation-speaking').textContent.includes('idle')",
            timeout=10000)
        got = await page.evaluate(BOXES)
        assert got["history"] == ["how are the agents"] and got["historyWho"] == ["user"], got
        assert got["hearing"] == "" and len(got["now"]) == 1, got

        voice.send({"type": "status", "state": "idle"})
        await page.wait_for_function(
            "document.querySelector('.conversation-speaking').textContent === ''", timeout=10000)
        got = await page.evaluate(BOXES)
        assert got["history"] == ["how are the agents", "All six agents are idle, sir."], got
        assert got["historyWho"] == ["user", "jarvis"], got
        colours = await page.evaluate("""() => [...document.querySelectorAll('.history-turn')]
          .map(e => getComputedStyle(e).color)""")
        assert colours[0].startswith("rgba(255, 255, 255") and colours[1].startswith("rgba(14, 165, 233"), colours
        await browser.close()



@pytest.mark.asyncio
async def test_typing_in_the_white_box_sends_it_as_if_he_had_said_it(page_url):
    """Clicked, the white box takes typing. Shift+Enter starts a new line;
    Enter sends it, marked typed, and the words wait in the box as spoken
    ones do until JARVIS answers."""
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.evaluate("window.__hear('something half heard', false)")
        await page.click(".conversation-hearing")
        assert await page.evaluate("document.activeElement.className") == "hearing-input"
        await page.keyboard.type("check the build")
        await page.keyboard.press("Shift+Enter")
        await page.keyboard.type("and the tests")
        assert not [m for m in voice.got if m.get("type") == "transcript"]
        lines = await page.evaluate("document.querySelector('.hearing-input').clientHeight / 20")
        assert lines == 2, lines

        await page.keyboard.press("Enter")
        await asyncio.sleep(0.2)
        (sent,) = [m for m in voice.got if m.get("type") == "transcript"]
        assert sent["text"] == "check the build\nand the tests" and sent["typed"] is True, sent
        assert sent["isFinal"] is True and isinstance(sent["id"], int), sent
        assert await page.evaluate("document.activeElement.tagName") == "BODY"
        got = await page.evaluate(BOXES)
        assert got["hearing"] == "check the build\nand the tests", got
        assert got["history"] == ["something half heard"], got

        # Heard words start a new turn after the typed one.
        await page.evaluate("window.__hear('and deploy', false)")
        got = await page.evaluate(BOXES)
        assert got["hearing"] == "and deploy", got
        assert got["history"] == ["something half heard", "check the build\nand the tests"], got
        # In the history it is his turn like any other: same place, same look.
        assert got["historyWho"] == ["user", "user"], got
        classes = await page.evaluate(
            "() => [...document.querySelectorAll('.history-turn')].map(e => e.className)")
        assert classes[0] == classes[1] == "history-turn user", classes
        await browser.close()

@pytest.mark.asyncio
async def test_words_made_into_a_capture_become_a_collapsed_item_and_a_box(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.evaluate("window.__hear('tell hammer to ship it', true)")
        await asyncio.sleep(0.2)
        heard = [m for m in voice.got if m.get("type") == "transcript"][-1]["id"]
        voice.send({"type": "capture", "id": 3, "state": "capturing",
                    "text": "Ship it.", "to": "hammer", "heard": [heard]})
        await page.wait_for_selector("#conversation .capture-box.message")
        got = await page.evaluate(BOXES)
        assert got["hearing"] == "" and got["history"] == ["Transmission to hammer"], got
        look = await page.evaluate("""() => { const p = document.querySelector('.history-turn');
          return {align: getComputedStyle(p).textAlign,
                  colour: getComputedStyle(p.querySelector('.capture-chip-head')).color}; }""")
        assert look["align"] == "right" and look["colour"].startswith("rgba(251, 191, 36"), look
        order = await page.evaluate("""() => ['.conversation-history', '.capture-box',
          '.conversation-speaking'].map(s => document.querySelector(s).getBoundingClientRect().top)""")
        assert order == sorted(order), order

        await page.click(".capture-chip-head")
        opened = await page.evaluate("""() => ['.capture-chip-said', '.capture-chip-captured'].map(s => {
          const e = document.querySelector(s);
          return [e.textContent, getComputedStyle(e).color, e.getBoundingClientRect().top]; })""")
        (said, sent) = opened
        assert said[0] == "tell hammer to ship it" and sent[0] == "Ship it.", opened
        assert said[2] < sent[2], "what he said comes first, then the capture"
        assert said[1].startswith("rgba(210, 214, 222"), said        # quiet grey
        assert sent[1].startswith("rgba(251, 191, 36"), sent          # the transmission's amber
        await page.wait_for_timeout(400)           # the crossfade from his words
        text = await page.evaluate("document.querySelector('.capture-box .capture-body').textContent")
        assert text == "Ship it.", text
        # Before it is for an agent, it is not yet a transmission: plain and on the left.
        voice.send({"type": "capture", "id": 4, "state": "capturing", "text": "Note this.",
                    "heard": [heard]})
        await page.wait_for_function(
            "document.querySelector('.history-turn .capture-chip-head').textContent === 'Capture 4'")
        align = await page.evaluate("getComputedStyle(document.querySelector('.history-turn')).textAlign")
        assert align in ("left", "start"), align
        await browser.close()


@pytest.mark.asyncio
async def test_a_message_box_says_send_to_and_has_its_buttons_in_the_corner(page_url):
    """"Send to:" over the top left once it is a message, its state over the
    top right; Read, Accept, Delete and Send in the bottom right, each a POST to its
    route; a refusal shown under the text."""
    voice = _Voice()
    posted = []

    async def answer(route):
        posted.append(route.request.url.split("/api/capture/")[1])
        if posted[-1].endswith("/send"):
            await route.fulfill(status=409, content_type="application/json",
                                body=json.dumps({"detail": "There are two hammers: which one?"}))
        else:
            await route.fulfill(status=200, content_type="application/json", body="{}")

    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.route("**/api/capture/*/*", answer)
        voice.send({"type": "capture", "id": 5, "state": "capturing", "text": "Note this.",
                    "heard": [], "accepted": False})
        await page.wait_for_selector(".capture-box[data-capture='5']")
        assert await page.evaluate("document.querySelector('.capture-to')") is None
        assert await page.evaluate(
            "document.querySelector('.capture-send').disabled"), "no one to send it to yet"

        voice.send({"type": "capture", "id": 5, "state": "capturing", "text": "Note this.",
                    "to": "hammer", "heard": [], "accepted": False})
        await page.wait_for_selector(".capture-box.message .capture-to")
        where = await page.evaluate("""() => {
          const r = s => document.querySelector(s).getBoundingClientRect();
          const box = r('.capture-box'), to = r('.capture-to'), state = r('.capture-state');
          const buttons = [...document.querySelectorAll('.capture-buttons button')];
          return {to: document.querySelector('.capture-to').textContent,
                  toLeft: to.left - box.left, toTop: to.top - box.top,
                  stateRight: box.right - state.right,
                  labels: buttons.map(b => b.textContent),
                  buttonsRight: box.right - buttons[3].getBoundingClientRect().right,
                  buttonsBottom: box.bottom - buttons[3].getBoundingClientRect().bottom,
                  textBottom: r('.capture-body').bottom,
                  buttonsTop: buttons[0].getBoundingClientRect().top};
        }""")
        assert where["to"] == "Send to: hammer", where
        assert where["toLeft"] < 30 and where["toTop"] < 5, where
        assert where["stateRight"] < 30, where
        assert where["labels"] == ["Read", "✓ Accept", "Delete", "Send"], where
        assert where["buttonsRight"] < 20 and where["buttonsBottom"] < 20, where
        assert where["buttonsTop"] >= where["textBottom"], where

        await page.click(".capture-read")
        await page.click(".capture-accept")
        await page.click(".capture-send")
        await page.wait_for_selector(".capture-refused")
        assert await page.text_content(".capture-refused") == "There are two hammers: which one?"
        await page.click(".capture-delete")
        await page.wait_for_function("document.querySelector('.capture-refused') === null")
        assert posted == ["5/read", "5/accept", "5/send", "5/delete"], posted

        voice.send({"type": "capture", "id": 5, "state": "capturing", "text": "Note this.",
                    "to": "hammer", "heard": [], "accepted": True})
        await page.wait_for_function(
            "document.querySelector('.capture-accept').textContent === '✓ Accepted'")
        assert await page.evaluate("document.querySelector('.capture-accept').disabled")

        voice.send({"type": "capture", "id": 5, "state": "reading", "text": "Note this.",
                    "to": "hammer", "heard": [], "accepted": True})
        await page.wait_for_function(
            "document.querySelector('.capture-read').textContent === 'Reading'")
        assert await page.evaluate("document.querySelector('.capture-read').disabled")
        await browser.close()


@pytest.mark.asyncio
async def test_a_long_turn_shows_two_lines_and_opens_when_clicked(page_url):
    voice = _Voice()
    clip = _silence_wav(0.3)
    long = " ".join(["The build finished and every one of the tests passed."] * 20)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        voice.send({"type": "status", "state": "speaking"},
                   {"type": "audio", "utt": 1, "idx": 0, "data": clip, "text": long, "kind": "reply"})
        await page.wait_for_function(
            "document.querySelector('.conversation-speaking').textContent.includes('build')",
            timeout=10000)
        voice.send({"type": "status", "state": "idle"})
        await page.wait_for_selector(".history-turn.clamped", timeout=10000)
        lines = await page.evaluate("""() => { const e = document.querySelector('.history-turn');
          return e.getBoundingClientRect().height / parseFloat(getComputedStyle(e).lineHeight); }""")
        assert lines == 2, lines
        await page.click(".history-turn.clamped")
        lines = await page.evaluate("""() => { const e = document.querySelector('.history-turn');
          return e.scrollHeight / parseFloat(getComputedStyle(e).lineHeight); }""")
        assert lines > 4, lines
        assert await page.locator(".history-turn.clamped").count() == 0
        await browser.close()


def _record_item(id_, kind, text, at=1_000_000.0, **extra):
    item = {"id": id_, "at": at, "kind": kind, "text": text, "agent_session_id": None,
            "agent_name": None, "message_id": None, "session_message_id": None,
            "capture_id": None, "message": None, "detail": {}}
    item.update(extra)
    return item


class _Record:
    """The conversation's record, faked: older items, a message to an agent
    whose status the test moves, and that agent's thread."""

    def __init__(self):
        self.status = "delivered"
        self.threads = []

    def items(self):
        agent = {"agent_session_id": "h", "agent_name": "hammer", "message_id": 7}
        return [
            _record_item(1, "heard", "Earlier, I asked about the build."),
            _record_item(2, "jarvis", "It was green, sir."),
            _record_item(3, "to_agent", "Use Postgres.",
                         message={"id": 7, "status": self.status,
                                  "answered": self.status == "answered", "failure": None},
                         **agent),
            *([_record_item(4, "answer", "Postgres it is.", **agent)]
              if self.status == "answered" else []),
        ]

    async def page(self, route):
        await route.fulfill(json={"items": self.items(), "earlier": None})

    async def thread(self, route):
        self.threads.append(route.request.url)
        await route.fulfill(json={"items": [i for i in self.items() if i["agent_session_id"] == "h"],
                                  "earlier": None,
                                  "agent": {"session_id": "h", "name": "hammer", "state": "idle"}})


@pytest.mark.asyncio
async def test_the_history_reads_the_record_with_live_message_status_and_threads(page_url):
    """What was said before the page opened comes from the record and is
    there again after a reload; a message's status moves on a hint and opens
    to the answer; an agent's name opens its thread."""
    voice, record = _Voice(), _Record()
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", voice)
        await page.route("**/api/conversation/agents/**", record.thread)
        await page.route("**/api/conversation", record.page)
        await page.goto(page_url)
        await page.wait_for_selector(".record-status")

        async def history():
            return await page.evaluate("""() => [...document.querySelectorAll(
              '.conversation-history .history-turn')].map(e => e.textContent.trim())""")

        got = await history()
        assert got[:2] == ["Earlier, I asked about the build.", "It was green, sir."], got
        assert await page.text_content(".record-status") == "Delivered"

        record.status = "answered"
        voice.send({"type": "conversation"})
        await page.wait_for_function(
            "document.querySelector('.record-status').textContent.startsWith('Answered')",
            timeout=10000)
        assert len(await history()) == 3, "the answer is drawn inside its message"
        await page.click(".record-status")
        assert await page.text_content(".record-answer") == "Postgres it is."

        await page.reload()
        await page.wait_for_function("window.__transcribers.some((t) => t.readyState === 1)", timeout=10000)
        await page.wait_for_selector(".record-status")
        assert (await history())[:2] == ["Earlier, I asked about the build.", "It was green, sir."]

        await page.click(".record-agent")
        await page.wait_for_selector("#agent-thread .record-message")
        tab = await page.evaluate(
            "() => document.querySelector('.centre-tabs [aria-selected=true]').textContent")
        assert tab.lower() == "hammer" and record.threads, tab
        assert await page.text_content("#agent-thread .thread-header h3") == "hammer"
        await page.click(".thread-close")
        await page.wait_for_selector("#agent-thread", state="detached")
        assert await page.locator("#conversation").count() == 1
        await browser.close()


@pytest.mark.asyncio
async def test_a_cleared_capture_has_a_restore_button_until_it_is_restored(page_url):
    voice, posted = _Voice(), []
    restored = {"done": False}

    def items():
        out = [_record_item(1, "capture", "Use Postgres.", capture_id=3, agent_name="hammer"),
               _record_item(2, "notice", "Capture 3 was cleared by JARVIS.", capture_id=3,
                            detail={"capture_event": "cleared", "by": "JARVIS"})]
        if restored["done"]:
            out.append(_record_item(3, "notice", "Capture 3 was restored by you.", capture_id=3,
                                    detail={"capture_event": "restored", "by": "you"}))
        return [{**{"detail": {}}, **i} for i in out]

    async def record(route):
        await route.fulfill(json={"items": items(), "earlier": None})

    async def restore(route):
        posted.append(route.request.url.split("/api/")[1])
        restored["done"] = True
        await route.fulfill(json={"capture": 3, "restored": True})

    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", voice)
        await page.route("**/api/conversation", record)
        await page.route("**/api/capture/*/restore**", restore)
        await page.goto(page_url)
        await page.wait_for_selector(".record-restore")
        await page.click(".record-restore")
        await page.wait_for_selector(".record-restore", state="detached", timeout=10000)
        assert posted == ["capture/3/restore?notice=2"], posted
        await browser.close()
