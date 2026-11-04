"""What the voice page does with his turns (voice control contract, rows 20
and section 7): words the server has not committed when the voice socket
drops are kept as an unsent draft, never sent again by themselves; a turn
the recogniser could not finish comes back as a draft; a committed turn
leaves nothing behind; and a discard says which epoch it moved to.

Headless Chromium on the built page, with the fake transcription socket and
a fake /ws/voice that can send `turn` frames and drop the page."""

import json

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_quiet_input import _browser, _listening, _page, _sent
from tests.test_voice_page_speech_input import _until

DRAFT = "document.querySelector('#conversation-draft')?.value ?? null"


@pytest.mark.asyncio(loop_scope="session")
async def test_words_not_committed_when_the_voice_socket_drops_become_an_unsent_draft(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url, view="")
        await _listening(page)
        await page.evaluate("window.__hear('remind me to call', true)")
        assert await _until(page.voice.finals)
        await page.voice.socket.close()
        await page.wait_for_function(f"{DRAFT} === 'remind me to call'", timeout=5000)
        assert page.voice.finals() == ["remind me to call"]
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_committed_turn_leaves_no_draft_and_a_stalled_one_comes_back(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url, view="")
        await _listening(page)
        await page.evaluate("window.__hear('what time is it', true)")
        assert await _until(page.voice.finals)
        page.voice.socket.send(json.dumps({"type": "turn", "state": "committed", "turnId": 1}))
        await page.wait_for_timeout(300)
        page.voice.socket.send(json.dumps({"type": "turn", "state": "stalled", "turnId": 2,
                                           "text": "transfer the"}))
        await page.wait_for_function(f"{DRAFT} === 'transfer the'", timeout=5000)
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
        assert await _until(page.voice.finals)
        heard = [m for m in page.voice.sent if m.get("type") == "transcript"][0]
        await page.click("#btn-mic-off")
        assert await _until(lambda: _sent(page.voice, "turn_discard"))
        discard = _sent(page.voice, "turn_discard")[0]
        assert discard["clientId"] == heard["clientId"]
        assert discard["captureEpoch"] > heard["captureEpoch"]
        await context.close()
