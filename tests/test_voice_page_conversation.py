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

from tests.voice_page_input import start_listening
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
    await page.route("**/api/conversation", lambda route: route.fulfill(json={"items": [], "earlier": None}))
    await page.goto(url)
    await start_listening(page)
    await page.wait_for_function("window.__transcribers.some((t) => t.readyState === 1)", timeout=10000)
    return browser, page


BOXES = """() => {
  const q = s => document.querySelector(s);
  const text = s => [...document.querySelectorAll(s)].map(e => e.textContent.trim());
  return {history: text('.conversation-history .history-turn'),
          historyWho: [...document.querySelectorAll('.conversation-history .history-turn')]
            .map(e => e.classList.contains('jarvis') ? 'jarvis' : 'user'),
          speaking: q('.chat-playback')?.textContent.trim() ?? '',
          hearing: q('.conversation-interim p')?.textContent.trim() ?? ''};
}"""


@pytest.mark.asyncio(loop_scope="session")
async def test_conversation_and_folders_are_the_tabs_offered_and_main_opens_by_address(page_playwright, page_url):
    async with page_playwright as p:
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
async def test_composer_and_timeline_have_separate_non_overlapping_scroll_areas(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        editor = page.get_by_role("textbox", name="Message JARVIS")
        await editor.fill("\n".join(f"Draft line {n}" for n in range(40)))
        got = await page.evaluate("""() => {
          const r = s => document.querySelector(s).getBoundingClientRect();
          const e = document.querySelector('.hearing-input');
          return {history:r('.conversation-history'), composer:r('.chat-composer'),
            field:r('.hearing-input'), footer:r('.composer-footer'),
            overflow:getComputedStyle(e).overflowY, scroll:e.scrollHeight, client:e.clientHeight};
        }""")
        assert got["history"]["bottom"] <= got["composer"]["top"]
        assert got["field"]["bottom"] <= got["footer"]["top"]
        assert got["scroll"] > got["client"] and got["overflow"] == "auto"
        assert await page.locator('.conversation-speaking').count() == 0
        assert await page.get_by_role("button", name="Send message").is_visible()
        await browser.close()

@pytest.mark.asyncio
async def test_final_spoken_turns_and_full_reply_share_one_timeline(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        await page.evaluate("window.__hear('how are the', false)")
        assert (await page.evaluate(BOXES))["hearing"] == "how are the"
        await page.evaluate("window.__hear('how are the agents', true)")
        await page.wait_for_function("document.querySelector('.history-turn.user')?.textContent === 'how are the agents'")
        voice.send({"type":"reply", "utt":1, "text":"All six agents are idle, sir.", "done":True},
                   {"type":"status", "state":"speaking"},
                   {"type":"audio", "utt":1, "idx":0, "data":_silence_wav(1.2),
                    "text":"All six agents are idle, sir.", "kind":"reply"})
        await page.wait_for_selector('.chat-playback')
        got = await page.evaluate(BOXES)
        assert got["history"] == ["how are the agents", "All six agents are idle, sir."]
        assert got["historyWho"] == ["user", "jarvis"]
        assert got["hearing"] == ""
        voice.send({"type":"status", "state":"idle"})
        await page.wait_for_selector('.chat-playback', state='detached')
        assert (await page.evaluate(BOXES))["history"] == got["history"]
        await browser.close()

@pytest.mark.asyncio(loop_scope="session")
async def test_multiline_composer_sends_with_keyboard_and_button(page_playwright, page_url):
    voice = _Voice()
    async with page_playwright as p:
        browser, page = await _open(p, page_url, voice)
        editor = page.get_by_role("textbox", name="Message JARVIS")
        await editor.fill("check the build")
        await editor.press("Shift+Enter")
        await editor.type("and the tests")
        assert not [m for m in voice.got if m.get("type") == "transcript"]
        await editor.evaluate("e => e.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', isComposing:true, bubbles:true, cancelable:true}))")
        assert not [m for m in voice.got if m.get("type") == "transcript"]
        await editor.press("Enter")
        await page.wait_for_function("document.querySelector('.history-turn.user')?.textContent.includes('and the tests')")
        sent = [m for m in voice.got if m.get("type") == "transcript"][-1]
        assert sent["text"] == "check the build\nand the tests" and sent["typed"] is True
        assert await editor.input_value() == "" and await editor.evaluate("e => e === document.activeElement")
        await editor.fill("A second message")
        await page.get_by_role("button", name="Send message").click()
        await page.wait_for_function("document.querySelectorAll('.history-turn.user').length === 2")
        assert await editor.input_value() == ""
        await browser.close()

@pytest.mark.asyncio(loop_scope="session")
async def test_words_made_into_a_capture_become_a_collapsed_item_and_a_box(page_playwright, page_url):
    voice = _Voice()
    async with page_playwright as p:
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
          '.chat-composer'].map(s => document.querySelector(s).getBoundingClientRect().top)""")
        assert order == sorted(order), order

        await page.click(".capture-chip-head")
        opened = await page.evaluate("""() => ['.capture-chip-said', '.capture-chip-captured'].map(s => {
          const e = document.querySelector(s);
          return [e.textContent, getComputedStyle(e).color, e.getBoundingClientRect().top]; })""")
        (said, sent) = opened
        assert said[0] == "tell hammer to ship it" and sent[0] == "Ship it.", opened
        assert said[2] < sent[2], "what he said comes first, then the capture"
        assert said[1] == "rgb(167, 181, 198)", said                # readable secondary text
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


@pytest.mark.asyncio(loop_scope="session")
async def test_a_message_box_says_send_to_and_has_its_buttons_in_the_corner(page_playwright, page_url):
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

    async with page_playwright as p:
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
async def test_long_replies_are_full_length_without_hidden_expansion(page_url):
    voice = _Voice()
    text = "\n\n".join(f"Paragraph {n}. The complete answer stays readable." for n in range(30))
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        voice.send({"type":"reply", "utt":1, "text":text, "done":True})
        await page.wait_for_function("document.querySelector('.history-turn.jarvis')?.textContent.includes('Paragraph 29')")
        assert await page.locator('.history-turn.clamped').count() == 0
        last = page.locator('.reply-prose p').last
        await last.scroll_into_view_if_needed()
        assert await last.is_visible()
        size = await page.locator('.history-turn.jarvis').evaluate("e => [e.scrollHeight,e.clientHeight,getComputedStyle(e).overflowY]")
        assert size[0] == size[1] and size[2] == 'visible'
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


@pytest.mark.asyncio(loop_scope="session")
async def test_the_history_reads_the_record_with_live_message_status_and_threads(page_playwright, page_url):
    """What was said before the page opened comes from the record and is
    there again after a reload; a message's status moves on a hint and opens
    to the answer; an agent's name opens its thread."""
    voice, record = _Voice(), _Record()
    async with page_playwright as p:
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
        await start_listening(page)
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


@pytest.mark.asyncio(loop_scope="session")
async def test_a_cleared_capture_has_a_restore_button_until_it_is_restored(page_playwright, page_url):
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

    async with page_playwright as p:
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


@pytest.mark.asyncio
async def test_text_only_reply_and_sent_draft_stay_visible_without_audio(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        editor = page.get_by_role("textbox", name="Message JARVIS")
        await editor.fill("Please reply in text.")
        await editor.press("Enter")
        await page.wait_for_function("document.querySelector('.history-turn.user')?.textContent === 'Please reply in text.'")
        assert await editor.input_value() == ""
        await editor.click()
        await editor.press("Tab")
        assert await editor.input_value() == ""
        assert await page.locator('.history-turn.user').inner_text() == "Please reply in text."
        voice.send({"type": "status", "state": "thinking"},
                   {"type": "reply", "utt": 17, "text": "Text works.", "done": False})
        await page.get_by_text("JARVIS is thinking", exact=True).wait_for()
        # With no audio, completion of the reply itself must end thinking.
        voice.send({"type": "reply", "utt": 17, "text": "Text works. No audio needed.", "done": True})
        await page.wait_for_function("document.querySelector('.history-turn.jarvis')?.textContent.includes('No audio needed.')")
        await page.wait_for_selector(".chat-playback", state="detached")
        got = await page.evaluate(BOXES)
        assert got["history"] == ["Please reply in text.", "Text works. No audio needed."]
        assert got["hearing"] == "" and got["speaking"] == ""
        await browser.close()


@pytest.mark.asyncio
async def test_disconnected_submission_preserves_draft_with_error(page_url):
    voice = _Voice()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, voice)
        editor = page.get_by_role("textbox", name="Message JARVIS")
        await editor.fill("Keep this draft")
        await voice.ws.close()
        await asyncio.sleep(0.1)
        await editor.press("Enter")
        assert await editor.input_value() == "Keep this draft"
        assert "has not been sent" in await page.get_by_role("alert").inner_text()
        assert not [m for m in voice.got if m.get("type") == "transcript"]
        await browser.close()
