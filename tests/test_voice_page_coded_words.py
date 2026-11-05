"""JARVIS's own words about the team server and about voice input, worded
on the page by their codes in the interface's language: a voice cause (the
live health frame, a recorded incident) and the team server's last error,
with what another program said kept as original detail. A record from
before the codes is shown as it was written, marked as original detail.

Headless Chromium on the built page, in Spanish (the sessions socket's
snapshot says so), with the routes and both sockets replaced by Playwright.
"""

import json
import re
import time

import pytest

from tests.voice_page_input import start_listening
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_language import _Sessions
from tests.test_voice_page_navigation import _Voice
from tests.test_voice_page_voice_health import pipeline, record

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


def _record(now: float):
    """The voice record with one incident whose cause has a code, and one
    from before the codes."""
    summary, history = record(now)
    coded = {**history["events"][0], "id": 3, "at": now - 10, "cause": "The page was busy for 807 ms",
             "causeCode": "pageBusy", "causeValues": {"ms": 807}}
    return summary, {**history, "events": [*history["events"], coded]}


def _team(said):
    return {"state": "signed_out", "server_url": "https://team.example.com",
            "last_server_url": "https://team.example.com", "server_id": "s", "team": None, "me": None,
            "session_expires_at": None, "last_contact_at": None,
            "last_error": "Sign in again.", "last_error_said": said, "poll_interval_sec": 30, "pairing": None}


async def _open(p, url, team):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route("**/api/voice/diagnostics", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps(_record(time.time())[0])))
    await page.route("**/api/voice/diagnostics/samples?*", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps(_record(time.time())[1])))
    await page.route("**/api/team", lambda r: r.fulfill(content_type="application/json", body=json.dumps(team)))
    voice = _Voice()
    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", _Sessions(language="es"))
    await page.goto(url)
    await page.wait_for_function("document.documentElement.lang === 'es-419'")
    await start_listening(page)
    await page.wait_for_function("window.__transcribers.some((t) => t.readyState === 1)", timeout=10000)
    return browser, page, voice


@pytest.mark.asyncio
async def test_a_voice_cause_is_worded_by_its_code_and_an_old_one_as_it_was_written(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, _team(None))
        try:
            result = await voice.navigate("voice.diagnostics.open", {})
            assert result["status"] == "completed", result
            await page.wait_for_selector("#voice-diagnostics .voice-card")
            # The live cause, by its code.
            msg = {"type": "health", "state": "browser-behind", "cause": "The page was busy for 807 ms",
                   "causeCode": "pageBusy", "causeValues": {"ms": 807}, "backlogSec": 0.2, "rtf": 0.1,
                   "loopDelayMs": 3, "processCpu": 60, "loadAvg": 4, "pipeline": pipeline(browser="warn")}
            await page.evaluate(f"window.__transcribers.find(t => t.readyState === 1)._emit({json.dumps(msg)})")
            cause = page.locator(".voice-overview .voice-cause-words")
            await async_api.expect(cause).to_have_text("La página estuvo ocupada durante 807 ms")
            assert await cause.get_attribute("data-original-detail") is None
            # The health dot's words too.
            await async_api.expect(page.locator("#health-dot")).to_have_attribute(
                "title", re.compile(re.escape("La página estuvo ocupada durante 807 ms")))

            # Recorded incidents: one with a code in Spanish, one from before
            # the codes as it was written, marked as original detail.
            causes = page.locator("#voice-diagnostics .voice-card .voice-card-cause")
            texts = await causes.all_text_contents()
            assert "La página estuvo ocupada durante 807 ms" in texts, texts
            old = causes.filter(has_text="Machine busy (load 16.9)")
            assert await old.get_attribute("data-original-detail") == ""
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_team_servers_last_error_is_worded_by_its_code_with_its_own_words_as_detail(page_url):
    said = {"code": "sessionEnded", "values": {}, "detail": "Sign in again."}
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, _team(said))
        try:
            result = await voice.navigate("settings.open", {"section": "team"})
            assert result["status"] == "completed", result
            line = page.locator(".team p.settings-hint", has_text="El servidor del equipo terminó esta sesión")
            await async_api.expect(line).to_have_text(
                "El servidor del equipo terminó esta sesión; inicie sesión de nuevo. Detalle: Sign in again.")
            detail = line.locator("[data-original-detail]")
            assert await detail.text_content() == "Sign in again."
        finally:
            await browser.close()
