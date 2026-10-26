"""Voice health on the page: the page asks for diagnostics and reports once a
second; the health the server sends drives the Diagnostics tab and the
health dot; text that used to be written under the orb is in Messages
instead; the Diagnostics view opens in the centre area and draws the record;
and the quiet page is reported.

Headless Chromium on the built page, with the fake transcription socket from
test_voice_page_captions.py and the voice record's routes answered by
Playwright.
"""

import json
import time

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION

STAGES = ["mic", "browser", "connection", "recogniser", "jarvis"]


def pipeline(**states):
    return {s: {"state": states.get(s, "ok"), "value": 0.1} for s in STAGES}


def record(now: float):
    """The record as the routes answer it, dated from `now` (each request's
    own time, so a long run does not move it out of the page's window)."""
    sample = {"at": now - 30, "capture": "c", "receivedSec": 1, "decodedSec": 1, "backlogSec": 0.3,
              "oldestSec": 0.3, "rtf": 0.2, "loopDelayMs": 4, "processCpu": 80, "loadAvg": 6,
              "framesCaptured": 10, "framesSent": 10, "heldSec": 0, "socketSec": 0, "longTaskMs": 0,
              "longTasks": 0, "tabHidden": False, "notices": 2, "noticesBySource": {"Input": 2},
              "voiceMsgs": 3, "sessionMsgs": 1, "renders": 4, "finals": 1, "typed": 0,
              "quiet": False, "state": "healthy", "cause": ""}
    summary = {"state": "healthy", "cause": "", "pipeline": pipeline(), "last2min": [sample],
               "incidentsLastHour": {"drops": 2, "reconnects": 1, "errors": 0},
               "today": {"listenedSec": 600, "finals": 40, "typed": 2, "drops": 2, "reconnects": 1},
               "now": now}
    events = [
        {"id": 1, "at": now - 20, "kind": "overload", "capture": "c", "source": None,
         "text": "Speech recognition fell behind, so this was not heard.",
         "cause": "Machine busy (load 16.9)", "topProcesses": [{"name": "rider", "cpu": 99.1}]},
        {"id": 2, "at": now - 19, "kind": "message", "capture": "c", "source": "Input",
         "text": "Speech recognition fell behind, so that was not sent.", "cause": None,
         "topProcesses": []},
    ]
    return summary, {"samples": [sample], "events": events}


async def _open(p, url):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route("**/api/voice/diagnostics", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps(record(time.time())[0])))
    await page.route("**/api/voice/diagnostics/samples?*", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps(record(time.time())[1])))
    voice = []
    await page.route_web_socket("**/ws/voice", lambda ws: (setattr(_open, "voice", ws),
                                                          ws.on_message(lambda m: voice.append(m))))
    await page.goto(url + "?view=main")
    await page.wait_for_function(
        "window.__transcribers.some((t) => t.readyState === 1) && window.__framesSent > 0",
        timeout=10000)
    return browser, page


async def _health(page, **fields):
    msg = {"type": "health", "state": "healthy", "cause": "", "backlogSec": 0.2, "rtf": 0.1,
           "loopDelayMs": 3, "processCpu": 60, "loadAvg": 4, "pipeline": pipeline(), **fields}
    await page.evaluate(f"window.__transcribers.find(t => t.readyState === 1)._emit({json.dumps(msg)})")


@pytest.mark.asyncio
async def test_the_page_asks_for_diagnostics_and_reports_each_second(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        assert (await page.evaluate("window.__starts[0]"))["diagnostics"] is True
        await page.wait_for_function("window.__telemetry.length >= 3", timeout=8000)
        t = await page.evaluate("window.__telemetry[window.__telemetry.length - 1]")
        assert set(t) >= {"framesCaptured", "framesSent", "heldSec", "socketSec", "longTaskMs",
                          "longTasks", "tabHidden", "notices", "voiceMsgs", "sessionMsgs",
                          "renders", "quiet", "messages"}
        assert 5 <= t["framesCaptured"] <= 15 and t["quiet"] is False
        await browser.close()


@pytest.mark.asyncio
async def test_health_from_the_server_drives_the_tab_and_the_dot(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        await _health(page)
        await page.wait_for_function(
            "document.querySelector('.voice-health')?.textContent === 'Hearing you'")
        assert await page.query_selector("#health-dot") is None
        await _health(page, state="decoder-slow", cause="Machine busy (load 16.9)",
                      backlogSec=1.4, pipeline=pipeline(recogniser="warn"))
        await page.wait_for_function(
            "document.querySelector('.voice-health')?.textContent === 'Recogniser behind'")
        assert await page.text_content(".voice-cause") == "Machine busy (load 16.9)"
        stage = await page.get_attribute("[data-stage='recogniser']", "class")
        assert "st-warn" in stage
        assert await page.get_attribute("#health-dot", "title") == \
            "Recogniser behind: Machine busy (load 16.9)"
        assert await page.get_attribute(".voice-summary .voice-orb", "data-state") == "decoder-slow"
        await browser.close()


@pytest.mark.asyncio
async def test_text_that_was_written_under_the_orb_goes_to_messages(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        ws = _open.voice
        ws.send(json.dumps({"type": "notice", "text": "Rotating my context; one moment."}))
        ws.send(json.dumps({"type": "text", "text": "A sentence I could not voice."}))
        await page.wait_for_timeout(300)
        status = await page.evaluate("document.querySelector('#status-text')?.textContent ?? ''")
        assert "Rotating" not in status and "voice" not in status
        assert await page.query_selector("#error-text") is None
        await page.click(".voice-summary-open")
        await page.wait_for_selector("#voice-diagnostics")
        messages = await page.text_content(".voice-messages")
        assert "Rotating my context; one moment." in messages
        assert "A sentence I could not voice." in messages
        await page.wait_for_function(
            "window.__telemetry.some(t => t.messages.some(m => m.text === 'A sentence I could not voice.'))",
            timeout=5000)
        await browser.close()


@pytest.mark.asyncio
async def test_the_diagnostics_view_draws_the_record_and_closes(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        await page.click(".voice-summary-open")
        await page.wait_for_selector("#voice-diagnostics .voice-card")
        card = await page.text_content("#voice-diagnostics .voice-card")
        assert "Speech dropped" in card and "Machine busy (load 16.9)" in card and "rider 99%" in card
        assert await page.locator("#voice-diagnostics .voice-timeline-row").count() == 7
        assert await page.locator("#voice-diagnostics .spark-mark").count() >= 7   # the drop, on each row
        assert "40 sentences heard" in await page.text_content(".voice-today")
        assert await page.locator(".centre-tabs [aria-label='Diagnostics']").count() == 1
        await page.click("#voice-diagnostics .workflow-close")
        await page.wait_for_selector("#voice-diagnostics", state="detached")
        assert await page.locator(".centre-tabs [aria-label='Diagnostics']").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_the_quiet_page_is_reported_in_telemetry(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        await page.click(".voice-summary-open")
        await page.click(".voice-quiet-button")
        assert await page.get_attribute(".voice-quiet-button", "aria-pressed") == "true"
        await page.wait_for_function("window.__telemetry.some(t => t.quiet === true)", timeout=5000)
        await page.click(".voice-quiet-button")
        n = await page.evaluate("window.__telemetry.length")
        await page.wait_for_function(f"window.__telemetry.length > {n} && "
                                     "window.__telemetry[window.__telemetry.length - 1].quiet === false",
                                     timeout=5000)
        await browser.close()


@pytest.mark.asyncio
async def test_the_health_dot_opens_the_diagnostics_view(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        await _health(page, state="no-frames", cause="No audio from the microphone",
                      pipeline=pipeline(mic="bad"))
        await page.wait_for_selector("#health-dot.bad")
        await page.click("#health-dot")
        await page.wait_for_selector("#voice-diagnostics")
        await browser.close()
