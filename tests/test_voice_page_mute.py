"""Mute stops sending, and the page shows it: a red "Muted" stands in for the
level meter until mute is pressed again. The microphone stays open, so
unmuting does not ask for it again. Mic off releases the microphone, and
turning it on asks for it again.

Same headless set-up as test_agents_list_fits.py (it builds the page there),
with Chromium's fake microphone, which plays a tone, so there is a level to
see while unmuted.
"""

import json

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.voice_page_input import start_listening
from tests.test_voice_page_speech_input import Voice

FAKE_MIC = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
LIT = "document.querySelectorAll('#mic-meter .mic-segment.on').length"
# Counts the page's requests for the microphone.
COUNT_ASKS = """
  const ask = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  window.__micAsks = 0;
  navigator.mediaDevices.getUserMedia = (c) => { window.__micAsks++; return ask(c); };
"""
SILENCED = "document.querySelector('#mic-silenced')?.textContent ?? null"


async def _peak_lit(page, ms: int) -> int:
    """The most meter segments lit at any moment over `ms` milliseconds."""
    return await page.evaluate(f"""() => new Promise(r => {{
      let most = 0; const end = performance.now() + {ms};
      const look = () => {{ most = Math.max(most, {LIT});
        if (performance.now() < end) setTimeout(look, 50); else r(most); }};
      look(); }})""")


async def _lights(page, why: str) -> None:
    """Until the meter shows a level: the fake microphone's first audio
    reaches it later the busier the machine."""
    try:
        await page.wait_for_function(f"{LIT} > 0", timeout=15000)
    except Exception:
        raise AssertionError(why) from None


async def _says(page, silenced) -> None:
    """Until the microphone's state line says `silenced` (None: nothing)."""
    await page.wait_for_function(f"({SILENCED}) === {json.dumps(silenced)}", timeout=15000)


@pytest.mark.asyncio(loop_scope="session")
async def test_mute_shows_muted_and_unmute_brings_the_meter_back_on_the_same_microphone(
        page_playwright, page_url):
    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=FAKE_MIC)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page()
        await page.add_init_script(COUNT_ASKS)
        # A voice socket that stays up: listening stops while it is down.
        await page.route_web_socket("**/ws/voice", Voice())
        await page.goto(page_url)
        await start_listening(page)

        await _lights(page, "the fake microphone never registered")
        asks = await page.evaluate("window.__micAsks")

        await page.click("#btn-mute")
        await _says(page, "Muted")
        await page.wait_for_function(f"{LIT} === 0", timeout=15000)   # the level falls away
        assert await _peak_lit(page, 2000) == 0, "the meter still shows a level while muted"

        await page.click("#btn-mute")
        await _says(page, None)
        await _lights(page, "the meter did not come back after unmuting")
        assert await page.evaluate("window.__micAsks") == asks, "unmuting asked for the microphone again"
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_mic_off_releases_the_microphone_and_on_asks_for_it_again(page_playwright, page_url):
    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=FAKE_MIC)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page()
        await page.add_init_script(COUNT_ASKS)
        # A voice socket that stays up: listening stops while it is down.
        await page.route_web_socket("**/ws/voice", Voice())
        await page.goto(page_url)
        await start_listening(page)

        await _lights(page, "the fake microphone never registered")
        asks = await page.evaluate("window.__micAsks")

        await page.click("#btn-mic-off")
        await _says(page, "Mic off")
        await page.wait_for_function(f"{LIT} === 0", timeout=15000)   # the level falls away
        assert await _peak_lit(page, 2000) == 0, "the meter still shows a level with the mic off"

        await page.click("#btn-mic-off")
        await _lights(page, "the meter did not come back with the mic on")
        assert await page.evaluate("window.__micAsks") == asks + 1
        await browser.close()
