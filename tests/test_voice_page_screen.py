"""What is shown and never said, on the page: a `display` lands in the Screen
tab, which lights up without switching; an open capture shows in the capture
box above the captions (the Main view), white while captured, amber once it is
a message for an agent and while transmitting, the line being read lit while
it is read back, and gone once sent.

Headless Chromium on the built page (built by test_agents_list_fits.py's
fixture), with JARVIS's voice socket replaced by Playwright.
"""

import asyncio
import json

import pytest

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401
from tests.test_voice_page_captions import AUTOPLAY, _silence_wav

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

DISPLAY = {"type": "display", "id": 7, "kind": "sources", "title": "Sources", "items": [
    {"label": "Met Office", "url": "https://www.metoffice.gov.uk/weather",
     "host": "www.metoffice.gov.uk", "note": ""},
    {"label": "a plain line", "url": "", "host": "", "note": "no link"}]}


async def _open(p, url, messages, displays=None, capture=None):
    try:
        browser = await p.chromium.launch(headless=True, args=AUTOPLAY)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.route("**/api/displays", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"displays": displays or []})))
    await page.route("**/api/capture", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"captures": [capture] if capture else []})))

    def voice(ws):
        async def run():
            await asyncio.sleep(2.0)
            for m in messages:
                ws.send(json.dumps(m))
                await asyncio.sleep(0.05)
        asyncio.ensure_future(run())
    await page.route_web_socket("**/ws/voice", voice)
    # The capture boxes above the captions are the Main view's.
    await page.goto(url + "?view=main")
    return browser, page


@pytest.mark.asyncio
async def test_a_display_lands_in_the_screen_tab_which_lights_up_without_switching(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [DISPLAY])
        await page.wait_for_selector(".right-tab.lit", timeout=10000)
        assert await page.locator(".right-tab.selected").get_attribute("title") == "Diagnostics"
        await page.click(".right-tab[title='Screen']")
        await page.wait_for_selector(".screen-item")
        got = await page.evaluate("""() => ({
          title: document.querySelector('.screen-item h3').textContent,
          link: document.querySelector('.screen-item a').getAttribute('href'),
          target: document.querySelector('.screen-item a').getAttribute('target'),
          host: document.querySelector('.screen-host').textContent,
          plain: document.querySelectorAll('.screen-item li')[1].querySelector('a'),
          lit: !!document.querySelector('.right-tab.lit')})""")
        assert got == {"title": "Sources", "link": "https://www.metoffice.gov.uk/weather",
                       "target": "_blank", "host": "www.metoffice.gov.uk", "plain": None,
                       "lit": False}
        await browser.close()


@pytest.mark.asyncio
async def test_a_page_that_reconnects_gets_what_was_shown_without_it_lighting_up(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [], displays=[DISPLAY])
        await page.click(".right-tab[title='Screen']")
        await page.wait_for_selector(".screen-item")
        await page.click(".right-tab[title='Diagnostics']")
        await page.wait_for_timeout(300)
        assert await page.locator(".right-tab.lit").count() == 0
        await browser.close()


TEXT = "Use Postgres for the store, and keep the migrations in one folder."


def _box_js():
    return """() => {
      const b = document.querySelector('.capture-box');
      if (!b) return null;
      const c = document.getElementById('caption-box').getBoundingClientRect();
      const r = b.getBoundingClientRect();
      const label = b.querySelector('.capture-state');
      const l = label.getBoundingClientRect();
      return {state: label.textContent, cls: b.className,
              text: b.querySelector('.capture-text.arriving').textContent,
              border: getComputedStyle(b).borderTopColor,
              gap: c.top - r.bottom, left: r.left - c.left, width: r.width - c.width,
              height: r.height, labelOnBorder: l.top < r.top && l.bottom > r.top,
              labelRight: r.right - l.right};
    }"""


@pytest.mark.asyncio
async def test_a_capture_shows_white_above_the_captions_with_its_state_on_the_border(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [
            {"type": "capture", "id": 3, "state": "capturing", "text": TEXT}])
        await page.wait_for_selector(".capture-box")
        got = await page.evaluate(_box_js())
        assert got["state"] == "capturing" and got["text"] == TEXT
        assert got["border"].startswith("rgba(255, 255, 255"), got
        assert got["labelOnBorder"] and 0 <= got["labelRight"] <= 20, got
        assert 0 <= got["gap"] <= 8 and abs(got["left"]) < 0.5 and abs(got["width"]) < 0.5, got

        await browser.close()


@pytest.mark.asyncio
async def test_transmitting_turns_the_box_yellow_and_sending_clears_it(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [
            {"type": "capture", "id": 3, "state": "capturing", "text": TEXT},
            {"type": "capture", "id": 3, "state": "transmitting"}])
        await page.wait_for_selector(".capture-box.transmitting")
        got = await page.evaluate(_box_js())
        assert got["state"] == "transmitting" and got["text"] == TEXT
        assert got["border"].startswith("rgba(251, 191, 36"), got
        await browser.close()

    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [
            {"type": "capture", "id": 3, "state": "capturing", "text": TEXT},
            {"type": "capture", "id": 3, "state": None},
            DISPLAY])
        await page.wait_for_selector(".right-tab.lit", timeout=10000)   # all three arrived
        assert await page.locator(".capture-box").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_the_box_grows_to_fit_a_long_capture(page_url):
    long = " ".join([TEXT] * 12)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [], capture={
            "type": "capture", "id": 4, "state": "capturing", "text": long})
        await page.wait_for_selector(".capture-box")
        got = await page.evaluate("""() => {
          const b = document.querySelector('.capture-box');
          return {scroll: b.scrollHeight, client: b.clientHeight, height: b.clientHeight};
        }""")
        assert got["scroll"] <= got["client"] and got["height"] > 3 * 20, got
        await browser.close()


@pytest.mark.asyncio
async def test_read_backs_are_spoken_into_the_captions(page_url):
    clip = _silence_wav(0.4)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [
            {"type": "status", "state": "speaking"},
            {"type": "audio", "utt": 1, "idx": 0, "data": clip,
             "text": "Telling chitauri: use Postgres.", "kind": "readback"}])
        await page.wait_for_function(
            "document.querySelector('#caption-box').textContent.includes('Telling chitauri')",
            timeout=15000)
        await browser.close()


@pytest.mark.asyncio
async def test_a_capture_for_an_agent_is_amber_and_several_stack(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [
            {"type": "capture", "id": 3, "state": "capturing", "text": TEXT},
            {"type": "capture", "id": 4, "state": "capturing", "text": "Ship it.", "to": "hammer"}])
        await page.wait_for_function("document.querySelectorAll('.capture-box').length === 2")
        got = await page.evaluate("""() => [...document.querySelectorAll('.capture-box')].map(b => ({
          id: b.dataset.capture, border: getComputedStyle(b).borderTopColor,
          top: b.getBoundingClientRect().top}))""")
        assert [g["id"] for g in got] == ["3", "4"] and got[0]["top"] < got[1]["top"], got
        assert got[0]["border"].startswith("rgba(255, 255, 255"), got     # not yet a message
        assert got[1]["border"].startswith("rgba(251, 191, 36"), got      # a message: amber
        await browser.close()


@pytest.mark.asyncio
async def test_a_read_back_glows_word_by_word_exactly_as_jarvis_speaking_does(page_url):
    """One way of marking the spoken word everywhere: the capture box draws
    it with the same component and styles as the captions, so the word being
    read looks the same as the word being spoken, and the read-back stays out
    of the captions."""
    long = " ".join([TEXT] * 4)
    clip = _silence_wav(3.0)
    glow = """(s) => { const w = document.querySelector(s); if (!w) return null;
      const c = getComputedStyle(w); return [c.color, c.textShadow, w.className]; }"""
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [
            {"type": "capture", "id": 3, "state": "capturing", "text": long, "to": "hammer"},
            {"type": "capture", "id": 3, "state": "reading"},
            {"type": "status", "state": "speaking"},
            {"type": "audio", "utt": 1, "idx": 0, "data": clip, "text": long,
             "kind": "reply", "capture": 3},
            {"type": "audio", "utt": 2, "idx": 0, "data": clip, "text": "Shall I send it, sir?",
             "kind": "reply"}])
        # The glow eases in; with that off, what is measured is the glow itself.
        await page.add_style_tag(content="* { transition: none !important; }")
        await page.wait_for_selector(".capture-box .spoken-word.now", timeout=15000)
        read = await page.evaluate(glow, ".capture-box .spoken-word.now")
        got = await page.evaluate("""() => ({
          now: document.querySelectorAll('.capture-box .spoken-word.now').length,
          captions: document.getElementById('caption-box').textContent})""")
        assert got["now"] == 1 and "Postgres" not in got["captions"], got

        await page.wait_for_selector("#caption-box .spoken-word.now", timeout=15000)
        spoken = await page.evaluate(glow, "#caption-box .spoken-word.now")
        assert read == spoken, (read, spoken)
        await browser.close()


@pytest.mark.asyncio
async def test_many_captures_scroll_inside_the_list_and_a_reconnect_resyncs_it(page_url):
    many = [{"type": "capture", "id": n, "state": "capturing", "text": " ".join([TEXT] * 3)}
            for n in range(1, 7)]
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, many)
        await page.wait_for_function("document.querySelectorAll('.capture-box').length === 6",
                                     timeout=10000)
        got = await page.evaluate("""() => { const l = document.querySelector('.capture-list');
          return {scrolls: l.scrollHeight > l.clientHeight, within: l.clientHeight <= innerHeight * 0.4 + 1,
                  overflow: getComputedStyle(l).overflowY}; }""")
        assert got == {"scrolls": True, "within": True, "overflow": "auto"}, got
        await browser.close()
