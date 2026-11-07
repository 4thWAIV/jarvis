"""The interface's language on the page (frontend/src/locale.ts), by what
the user sees: choosing Español in Settings turns the shell's words (the
right and centre tabs, the menu, the language control) into Spanish without
a reload, keeping Settings open where it was, and English turns them back; a
refused change leaves them as they were; a change made elsewhere arrives as
the sessions socket's `language` frame, and a page that connects reads the
language on its snapshot; a read answered after a pushed change does not undo
it. The document's `lang` follows.

Headless Chromium on the built page, with the endpoints answered by the test
(the Settings test's server) and the sessions socket played by the test.
"""

import asyncio
import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_settings import _Server, _open_settings

pytestmark = pytest.mark.page

TABS = """() => [...document.querySelectorAll('.right-tabs [role=tab]')].map(t => t.getAttribute('aria-label'))"""
ENGLISH_TABS = ["Folders", "Agents", "Queue", "Work", "Network", "Diagnostics"]
SPANISH_TABS = ["Carpetas", "Agentes", "Cola", "Trabajo", "Red", "Diagnóstico"]


class _Sessions:
    """The sessions socket: its opening snapshot, then what the test sends."""

    def __init__(self, language=None):
        self.ws = None
        self.language = language

    def __call__(self, ws):
        self.ws = ws
        frame = {"type": "snapshot", "sessions": []}
        if self.language:
            frame["language"] = self.language
        ws.send(json.dumps(frame))

    def send(self, frame):
        self.ws.send(json.dumps(frame))


async def _open(p, url, server, sessions):
    try:
        browser = await p.chromium.launch(headless=True)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await server.install(page)
    await page.route_web_socket("**/ws/sessions", sessions)
    await page.goto(url)
    await page.wait_for_selector(".right-tabs [role=tab]")
    # Set once: still there afterwards means the page was never reloaded.
    await page.evaluate("() => { window.__notReloaded = true; }")
    return browser, page


async def _tabs_are(page, tabs):
    await page.wait_for_function(f"(want) => JSON.stringify(({TABS})()) === JSON.stringify(want)", arg=tabs)


@pytest.mark.asyncio
async def test_espanol_in_settings_turns_the_interface_spanish_and_english_turns_it_back(page_url):
    server = _Server()
    server.spanish_installed = True
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server, _Sessions())
        assert await page.evaluate(TABS) == ENGLISH_TABS
        await page.click("[data-ui-tab=queue]")
        await _open_settings(page)
        await page.locator("#settings-language").select_option("es")

        await _tabs_are(page, SPANISH_TABS)
        assert await page.get_attribute("#btn-menu", "aria-label") == "Menú"
        assert await page.evaluate("document.documentElement.lang") == "es-419"
        await async_api.expect(page.get_by_text("Idioma", exact=True)).to_be_visible()   # Settings still open
        await async_api.expect(page.locator("#settings-language")).to_have_value("es")
        assert await page.get_attribute("[data-ui-tab=queue]", "aria-selected") == "true"  # same tab
        assert await page.evaluate("window.__notReloaded") is True
        # Each language keeps its own name.
        assert await page.locator("#settings-language option[value=en]").text_content() == "English"

        await page.locator("#settings-language").select_option("en")
        await _tabs_are(page, ENGLISH_TABS)
        assert await page.evaluate("document.documentElement.lang") == "en-US"
        assert await page.evaluate("window.__notReloaded") is True
        await browser.close()


@pytest.mark.asyncio
async def test_a_refused_change_leaves_the_interface_as_it_was(page_url):
    server = _Server()
    server.spanish_installed = True

    async def refuse(route):
        if route.request.method == "POST":
            await route.fulfill(status=500, content_type="application/json",
                                body=json.dumps({"error": "The Spanish speech model failed to load: bad weights"}))
            return
        await route.fulfill(status=200, content_type="application/json",
                            body=json.dumps(server.language_status()))

    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server, _Sessions())
        await page.route("**/api/settings/language", refuse)
        await _open_settings(page)
        await page.locator("#settings-language").select_option("es")
        await async_api.expect(page.get_by_text("bad weights")).to_be_visible()
        assert await page.evaluate(TABS) == ENGLISH_TABS
        await async_api.expect(page.locator("#settings-language")).to_have_value("en")
        assert await page.evaluate("document.documentElement.lang") == "en-US"
        await browser.close()


@pytest.mark.asyncio
async def test_a_change_made_elsewhere_arrives_on_the_socket(page_url):
    server, sessions = _Server(), _Sessions()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server, sessions)
        sessions.send({"type": "language", "language": "es"})       # the brain, or another page
        await _tabs_are(page, SPANISH_TABS)
        sessions.send({"type": "language", "language": "en"})
        await _tabs_are(page, ENGLISH_TABS)
        await browser.close()


@pytest.mark.asyncio
async def test_a_page_that_connects_reads_the_language_and_a_late_read_does_not_undo_it(page_url):
    server = _Server()
    server.language = "en"                    # what a slow read will say...

    async def slow(route):
        await asyncio.sleep(1.5)
        try:
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(server.language_status()))
        except Exception:                     # pragma: no cover - the page went first
            pass

    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:                # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await server.install(page)
        await page.route("**/api/settings/language", slow)
        # ...after the socket's snapshot said Spanish is in force.
        await page.route_web_socket("**/ws/sessions", _Sessions(language="es"))
        await page.goto(page_url)
        await _tabs_are(page, SPANISH_TABS)
        await page.wait_for_timeout(2500)     # the slow read has answered "en"
        assert await page.evaluate(TABS) == SPANISH_TABS
        await browser.close()
