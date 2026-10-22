"""Mute turns the microphone off, and the page shows it: the level meter goes
dark and stays dark until mute is pressed again.

Same headless set-up as test_agents_list_fits.py (it builds the page there),
with Chromium's fake microphone, which plays a tone, so there is a level to
see while unmuted.
"""

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401

FAKE_MIC = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
LIT = "document.querySelectorAll('#mic-meter .mic-segment.on').length"


async def _peak_lit(page, ms: int) -> int:
    """The most meter segments lit at any moment over `ms` milliseconds."""
    return await page.evaluate(f"""() => new Promise(r => {{
      let most = 0; const end = performance.now() + {ms};
      const look = () => {{ most = Math.max(most, {LIT});
        if (performance.now() < end) setTimeout(look, 50); else r(most); }};
      look(); }})""")


@pytest.mark.asyncio
async def test_mute_turns_the_meter_off_and_unmute_turns_it_back_on(page_url):
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=FAKE_MIC)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page()
        await page.goto(page_url)

        assert await _peak_lit(page, 4000) > 0, "the fake microphone never registered"

        await page.click("#btn-mute")
        await page.wait_for_timeout(300)
        assert await _peak_lit(page, 2000) == 0, "the meter still shows a level while muted"

        await page.click("#btn-mute")
        assert await _peak_lit(page, 4000) > 0, "the meter did not come back after unmuting"
        await browser.close()
