"""The page shows each agent by the one name the server gives it.

The labels Tim saw ("the older denice", "the denice that's working") were
made on the server and shown as they came; tests/test_agent_one_name.py
checks the server now sends only the agent's name. These check the page
adds none of its own: the Agents panel, two agents of one name, and the
capture box's "Send to:" each show exactly the name sent.

A browser page test: run with `pytest -m page` (see pytest.ini).
"""

import pytest

from tests.test_agents_list_fits import _browser, _open_with, async_api  # noqa: F401
from tests.test_voice_page_conversation import _Voice, _open

pytestmark = pytest.mark.page

AGENTS = {"agents": [
    {"session_id": "98880f86", "name": "Denice", "active": True, "state": "idle"},
    {"session_id": "01a1af11", "name": "Designer", "active": True, "state": "working"},
    {"session_id": "h1", "name": "hammer", "active": False, "state": "idle"},
    {"session_id": "h2", "name": "hammer", "active": False, "state": "working"},
]}

NAMES = """() => [...document.querySelectorAll('.agents-list li .agent-name')]
  .map(e => e.textContent.trim())"""


@pytest.mark.asyncio(loop_scope="session")
async def test_the_agents_panel_shows_each_agent_s_exact_name(page_playwright, page_url):
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open_with(browser, page_url, AGENTS)
        shown = await page.evaluate(NAMES)
        assert sorted(shown) == ["Denice", "Designer", "hammer", "hammer"], shown
        text = await page.evaluate("document.querySelector('.agents-list').textContent")
        for label in ("older", "newer", "that's", "hasn't"):
            assert label not in text, text
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("name", ["Denice", "Designer"])
async def test_the_capture_box_says_send_to_the_exact_name(page_playwright, page_url, name):
    voice = _Voice()
    async with page_playwright as p:
        browser, page = await _open(p, page_url, voice)
        voice.send({"type": "capture", "id": 7, "state": "capturing", "text": "Rename the remote.",
                    "to": name, "heard": [], "accepted": False})
        await page.wait_for_selector(".capture-to")
        assert await page.text_content(".capture-to") == f"Send to: {name}"
        await browser.close()
