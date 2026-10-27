"""The Diagnostics tab's subsystems: one row per subsystem, agent
communications first and this page's own connection last; communications
down is a red row and a red Diagnostics tab, whichever tab is open; a
"subsystems" hint on /ws/sessions makes the list read again; and clicking
the row opens communications in full in the centre area.

Headless Chromium on the built page, with /api/diagnostics/subsystems and
both sockets replaced by Playwright.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

IDS = ["communications", "voice_input", "brain", "voice_output", "tool_channel", "workflows"]


def _record(comms="ok"):
    down = comms == "down"
    checks = [
        {"id": "watcher", "name": "Session watcher", "state": "ok", "detail": "Watching 2 sessions"},
        {"id": "outgoing", "name": "Messages to agents", "state": comms,
         "detail": ("The last 2 messages, to 2 different agents, did not reach them" if down
                    else "3 reached their agents in the last hour")},
        {"id": "incoming", "name": "Messages from agents", "state": "ok", "detail": "Last look 2 s ago"},
        {"id": "needs", "name": "Needs board", "state": "ok", "detail": "Last look 1 s ago"},
    ]
    summary = (f"Messages to agents: {checks[1]['detail']}" if down else "Watching 2 sessions")
    messages = [{"id": 7, "agentName": "hammer", "status": "undelivered" if down else "answered",
                 "outcome": "lost" if down else "reached", "at": 1_000_000.0, "failure": None}]
    rows = [{"id": "communications", "name": "Communications", "state": comms,
             "summary": summary, "checks": checks, "messages": messages}]
    for id in IDS[1:]:
        rows.append({"id": id, "name": id.replace("_", " ").capitalize(), "state": "ok",
                     "summary": "Fine", "checks": []})
    return {"now": 1_000_000.0, "subsystems": rows}


class _Server:
    def __init__(self):
        self.comms = "ok"
        self.reads = 0

    async def __call__(self, route):
        self.reads += 1
        await route.fulfill(content_type="application/json", body=json.dumps(_record(self.comms)))


class _Sessions:
    def __init__(self):
        self.ws = None

    def __call__(self, ws):
        self.ws = ws               # not connected anywhere: Playwright holds it open


STATE = """() => {
  const rows = [...document.querySelectorAll('.subsystem-row')];
  const comms = document.querySelector(".subsystem-row[data-subsystem='communications']");
  const tab = document.querySelector(".right-tabs [aria-label='Diagnostics']");
  return {ids: rows.map(r => r.dataset.subsystem),
          classes: rows.map(r => r.className),
          commsBg: comms && getComputedStyle(comms).backgroundColor,
          commsState: comms && comms.querySelector('.subsystem-state').textContent,
          commsSummary: comms && comms.querySelector('.subsystem-summary').textContent,
          tabAlert: tab.classList.contains('alert')};
}"""


@pytest.mark.asyncio
async def test_communications_down_is_red_in_the_list_and_on_the_tab(page_url):
    server, sessions = _Server(), _Sessions()
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.route("**/api/diagnostics/subsystems", server)
        await page.goto(page_url)

        await page.click(".right-tabs [aria-label='Diagnostics']")
        await page.wait_for_selector(".subsystem-row[data-subsystem='page'].state-ok", timeout=10000)
        shown = await page.evaluate(STATE)
        assert shown["ids"] == IDS + ["page"], "one row each, communications first, the page last"
        assert all("state-ok" in c for c in shown["classes"]), shown
        assert not shown["tabAlert"]

        # Communications go down; the hint makes the page read again.
        server.comms = "down"
        sessions.ws.send(json.dumps({"type": "subsystems"}))
        await page.wait_for_selector(".subsystem-row[data-subsystem='communications'].state-down")
        shown = await page.evaluate(STATE)
        assert shown["commsState"] == "Down"
        assert "did not reach them" in shown["commsSummary"]
        assert shown["commsBg"] == "rgba(239, 68, 68, 0.22)", "filled red"
        assert shown["tabAlert"]
        dot = await page.evaluate("""getComputedStyle(document.querySelector(
            ".subsystem-row[data-subsystem='communications'] .subsystem-dot")).backgroundColor""")
        assert dot == "rgb(239, 68, 68)"

        # Seen from another tab, the Diagnostics tab is still red.
        await page.click(".right-tabs [aria-label='Agents']")
        assert "alert" in await page.get_attribute(".right-tabs [aria-label='Diagnostics']", "class")
        await page.click(".right-tabs [aria-label='Diagnostics']")

        # Clicking the row opens communications in full in the centre area.
        await page.click(".subsystem-row[data-subsystem='communications']")
        await page.wait_for_selector("#subsystem-detail[data-subsystem='communications']")
        assert await page.locator(".centre-tabs [aria-label='Communications']").count() == 1
        checks = await page.evaluate("""[...document.querySelectorAll('.subsystem-checks li')]
            .map(li => [li.dataset.check, li.className])""")
        assert checks[1] == ["outgoing", "state-down"], checks
        lost = await page.evaluate("document.querySelector('.subsystem-messages li').className")
        assert lost == "outcome-lost"
        head = await page.text_content(".subsystem-head-state")
        assert head.startswith("Down: Messages to agents")

        # They come back: green again, the tab no longer red; then closed.
        server.comms = "ok"
        sessions.ws.send(json.dumps({"type": "subsystems"}))
        await page.wait_for_selector(".subsystem-row[data-subsystem='communications'].state-ok")
        assert not (await page.evaluate(STATE))["tabAlert"]
        await page.click("#subsystem-detail .workflow-close")
        assert await page.locator("#subsystem-detail").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_a_page_that_cannot_reach_jarvis_says_so(page_url):
    """No subsystems route and no sessions socket (the static server): the
    page's own row is down, with why, and the tab is red."""
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.goto(page_url)
        await page.click(".right-tabs [aria-label='Diagnostics']")
        row = ".subsystem-row[data-subsystem='page']"
        await page.wait_for_selector(f"{row}.state-down", timeout=10000)
        assert "Can't read JARVIS" in await page.text_content(f"{row} .subsystem-summary")
        assert "alert" in await page.get_attribute(".right-tabs [aria-label='Diagnostics']", "class")
        await browser.close()
