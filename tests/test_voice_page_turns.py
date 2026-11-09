"""What the voice page does with his turns (voice control contract, rows 20
and section 7): words the server has not committed when the voice socket
drops are kept as an unsent draft, never sent again by themselves, and only
the words the server gathered as his (never ones it took for echo); a turn
the recogniser could not finish offers Retry, Send now and Keep as draft,
each a frame to the server, and kept as a draft it comes back in the box;
Send now is offered while a turn is gathered; a committed turn leaves
nothing behind; and a discard says which epoch it moved to.

Headless Chromium on the built page, with the fake transcription socket and
a fake /ws/voice that can send `turn` frames and drop the page."""

import json

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_quiet_input import _browser, _listening, _page, _sent
from tests.fixtures.waiting import until_async

DRAFT = "document.querySelector('#conversation-draft')?.value ?? null"


def _turn(page, state, turn_id, text):
    """The server's `turn` frame."""
    page.voice.socket.send(json.dumps({"type": "turn", "state": state, "turnId": turn_id,
                                       "text": text}))


@pytest.mark.asyncio(loop_scope="session")
async def test_words_not_committed_when_the_voice_socket_drops_become_an_unsent_draft(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url, view="")
        await _listening(page)
        await page.evaluate("window.__hear('remind me to call', true)")
        assert await until_async(page.voice.finals)
        _turn(page, "gathering", 1, "remind me to call")
        await page.wait_for_selector("#voice-turn-send", timeout=15000)
        await page.voice.socket.close()
        await page.wait_for_function(f"{DRAFT} === 'remind me to call'", timeout=15000)
        assert page.voice.finals() == ["remind me to call"]
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_words_the_server_took_for_echo_never_reach_the_draft(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url, view="")
        await _listening(page)
        await page.evaluate("window.__hear('as I was saying sir', true)")
        assert await until_async(page.voice.finals)
        await page.wait_for_timeout(300)        # no gathering frame: the server judged it echo
        await page.voice.socket.close()
        await page.wait_for_timeout(500)
        assert await page.evaluate(DRAFT) == ""
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_committed_turn_leaves_no_draft_and_a_stalled_one_comes_back(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url, view="")
        await _listening(page)
        await page.evaluate("window.__hear('what time is it', true)")
        assert await until_async(page.voice.finals)
        page.voice.socket.send(json.dumps({"type": "turn", "state": "committed", "turnId": 1}))
        await page.wait_for_timeout(300)
        await page.wait_for_function("document.querySelector('#conversation-control').dataset.state === 'empty'", timeout=15000)
        _turn(page, "stalled", 2, "transfer the")
        await page.wait_for_selector("#voice-turn-retry", timeout=15000)
        assert await page.evaluate(DRAFT) == "", "it waits for his choice"
        for button, frame in (("#voice-turn-retry", "turn_retry"), ("#voice-turn-send", "turn_send_now"),
                              ("#voice-turn-draft", "turn_keep_draft")):
            await page.click(button)
            assert await until_async(lambda: _sent(page.voice, frame)), frame
        _turn(page, "draft", 2, "transfer the")
        await page.wait_for_function(f"{DRAFT} === 'transfer the'", timeout=15000)
        await page.wait_for_function("document.querySelector('#conversation-control').dataset.state === 'empty'", timeout=15000)
        await page.voice.socket.close()
        await page.wait_for_timeout(500)
        assert await page.evaluate(DRAFT) == "transfer the"
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_discard_names_the_page_and_the_epoch_it_moved_to(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, stop="discard")
        await _listening(page)
        await page.evaluate("window.__hear('drop it', true)")
        assert await until_async(page.voice.finals)
        heard = [m for m in page.voice.sent if m.get("type") == "transcript"][0]
        await page.click("#btn-mic-off")
        assert await until_async(lambda: _sent(page.voice, "turn_discard"))
        discard = _sent(page.voice, "turn_discard")[0]
        assert discard["clientId"] == heard["clientId"]
        assert discard["captureEpoch"] > heard["captureEpoch"]
        await context.close()
