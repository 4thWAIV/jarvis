"""The page never stays wrong about the server's state (liveRead.ts): the
brain list, the running brain on the orb and Diagnostics are
read again on every reconnect and whenever a brain becomes ready; a read that
fails is retried and said, not hidden; Settings changes reach Diagnostics at
once; and Save Preferences waits until the preferences have been read.

Headless Chromium on the built page, with the server's answers played by the
test (including answers that fail, as during a restart)."""

import json

import pytest

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401
from tests.test_voice_page_settings import _Server, _open_settings

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


def _cards(active, both=True):
    cards = [{"name": "anthropic", "title": "Claude", "added": True,
              "default": active == "anthropic", "active": active == "anthropic", "model": "sonnet"}]
    cards.append({"name": "openai", "title": "OpenAI (Codex)", "added": both,
                  "default": active == "openai", "active": active == "openai", "model": "gpt-5.6-terra"})
    return cards


class _Live:
    """/api/brains and the voice socket, as a server that restarts."""

    def __init__(self, active="anthropic", fail_first=0):
        self.active, self.fail_left = active, fail_first
        self.sockets = []

    async def install(self, page):
        async def brains(route):
            if self.fail_left > 0:
                self.fail_left -= 1
                await route.fulfill(status=502, body="the server is coming back")
                return
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps({"brains": _cards(self.active)}))
        await page.route("**/api/brains**", brains)
        await page.route("**/api/usage/week**", lambda r: r.fulfill(
            status=200, content_type="application/json", body='{"measured": false}'))
        def voice(ws):
            # Playwright's stand-in socket reports itself open to the page only
            # once it sends something; a real server's opens on connect.
            self.sockets.append(ws)
            ws.send(json.dumps({"type": "status", "state": "idle"}))
        await page.route_web_socket("**/ws/voice", voice)
        await page.route_web_socket("**/ws/sessions", lambda ws: None)


async def _page(p, url, live):
    try:
        browser = await p.chromium.launch(headless=True)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await live.install(page)
    await page.goto(url)
    return browser, page


LOGO = "(document.getElementById('brain-logo')?.dataset.provider || null)"
BOXES = "document.querySelectorAll('.weekly-usage').length"


@pytest.mark.asyncio
async def test_a_reload_before_the_server_is_back_says_so_and_catches_up(page_url):
    live = _Live(active="openai", fail_first=2)
    async with async_api.async_playwright() as p:
        browser, page = await _page(p, page_url, live)
        brains_failure = page.locator(".read-failure", has_text="Couldn't read the brains")
        await brains_failure.first.wait_for(timeout=10000)
        await page.wait_for_function(f"{BOXES} === 2", timeout=15000)
        await page.wait_for_function(f"{LOGO} === 'openai'")
        assert await page.locator("#brain-usage .read-failure").count() == 0, \
            "said only while true"
        await browser.close()


@pytest.mark.asyncio
async def test_a_restart_with_the_page_open_shows_the_brain_that_now_runs(page_url):
    live = _Live(active="anthropic")
    async with async_api.async_playwright() as p:
        browser, page = await _page(p, page_url, live)
        await page.wait_for_function(f"{LOGO} === 'anthropic'", timeout=10000)
        live.active = "openai"                     # the server comes back with OpenAI
        await live.sockets[-1].close()             # ...and the page's socket reconnects
        await page.wait_for_function(f"{LOGO} === 'openai'", timeout=15000)
        roles_js = """[...document.querySelectorAll('.weekly-usage')].map(b =>
          [b.dataset.provider, b.querySelector('.weekly-role')?.textContent])"""
        await page.wait_for_function(f"JSON.stringify({roles_js}) === "
                                     f"'[[\"anthropic\",\"STANDBY\"],[\"openai\",\"BRAIN\"]]'",
                                     timeout=10000)
        roles = await page.evaluate(roles_js)
        assert roles == [["anthropic", "STANDBY"], ["openai", "BRAIN"]], roles
        await browser.close()


@pytest.mark.asyncio
async def test_a_brain_becoming_ready_is_shown_without_a_reconnect(page_url):
    live = _Live(active="anthropic")
    async with async_api.async_playwright() as p:
        browser, page = await _page(p, page_url, live)
        await page.wait_for_function(f"{LOGO} === 'anthropic'", timeout=10000)
        live.active = "openai"
        live.sockets[-1].send(json.dumps({"type": "brain_state", "state": "ready"}))
        await page.wait_for_function(f"{LOGO} === 'openai'", timeout=10000)
        await browser.close()


@pytest.mark.asyncio
async def test_adding_a_brain_in_settings_reaches_diagnostics_at_once(page_url):
    server = _Server()
    async with async_api.async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await server.install(page)
        await page.route("**/api/usage/week**", lambda r: r.fulfill(
            status=200, content_type="application/json", body='{"measured": false}'))
        await page.goto(page_url)
        await page.wait_for_function(f"{BOXES} === 1", timeout=10000)
        await _open_settings(page)
        await page.get_by_role("button", name="+ Add OpenAI (Codex)").click()
        await page.wait_for_function(f"{BOXES} === 2", timeout=10000)
        await browser.close()


@pytest.mark.asyncio
async def test_preferences_cannot_be_saved_until_they_have_been_read(page_url):
    server = _Server()
    prefs = {"fail": True}
    async with async_api.async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await server.install(page)

        async def preferences(route):
            if prefs["fail"]:
                await route.fulfill(status=503, body="not yet")
                return
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps({"user_name": "Tim", "honorific": "sir"}))
        await page.route("**/api/settings/preferences", preferences)
        await page.goto(page_url)
        await page.click("#btn-menu")
        await page.get_by_role("button", name="Settings", exact=True).click()
        save = page.get_by_role("button", name="Save Preferences")
        await page.get_by_text("Your preferences could not be read").wait_for(timeout=10000)
        assert await save.is_disabled(), "a blank name must not be saved over the real one"

        prefs["fail"] = False                      # the next try reads them
        await page.wait_for_function(
            "document.querySelector('input[placeholder=\"Your name\"]').value === 'Tim'", timeout=15000)
        assert await save.is_enabled()
        await browser.close()
