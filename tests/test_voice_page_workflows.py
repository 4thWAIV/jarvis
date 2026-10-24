"""The Workflows view and a workflow's detail.

The right panel's Workflows tab lists every workflow, one compact row each:
its name, the phase it is on, the tasks done and the decisions open.
Clicking a row opens the workflow in the centre area, and its Decisions tab
answers a decision by posting the option clicked. A "workflows" hint on
/ws/sessions makes the list read the record again.

Headless Chromium on the built page, with /api/workflows* and both sockets
replaced by Playwright.
"""

import asyncio
import json

import pytest

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401
from tests.test_voice_page_captions import AUTOPLAY, FAKE_RECOGNITION
from tests.test_voice_page_conversation import _Voice

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


def _workflow(state="needs_you"):
    return {
        "id": "wf-1", "name": "Speech migration", "project": "jarvis", "state": state,
        "phase": "deepgram-engine", "phases_done": 1, "phases_total": 5,
        "tasks_done": 6, "tasks_total": 10, "open_waits": [],
        "open_decisions": ["engine"] if state == "needs_you" else [], "open_blockers": [],
        "agents": ["s1"], "started_at": 1_000_000.0, "last_event_at": 9_999_999_999.0,
        "last_summary": "Engine wired up.",
        "items": [{"slug": "mic-recorder", "kind": "phase", "phase": None,
                   "title": "Mic recorder", "state": "done", "done": None, "total": None},
                  {"slug": "deepgram-engine", "kind": "phase", "phase": None,
                   "title": "Deepgram engine", "state": "running", "done": None, "total": None}],
        "open": ([{"kind": "decision", "key": "engine", "summary": "Which engine is the default?",
                   "detail": {"options": ["Deepgram", "Apple"], "recommendation": "Deepgram"},
                   "opened_at": 9_999_999_000.0}] if state == "needs_you" else []),
    }


class _Server:
    """The workflow routes, faked: what the page asked for and posted."""

    def __init__(self):
        self.state = "needs_you"
        self.lists = 0
        self.posted = []

    async def __call__(self, route):
        request = route.request
        path = request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if request.method == "POST":
            self.posted.append((path, json.loads(request.post_data or "{}")))
            await route.fulfill(json={"workflow": _workflow(self.state)})
        elif path == "api/workflows":
            self.lists += 1
            await route.fulfill(json={"workflows": [_workflow(self.state)]})
        elif path.endswith("/events"):
            await route.fulfill(json={"events": []})
        elif path.endswith("/transcript"):
            await route.fulfill(json={"agents": []})
        else:
            await route.fulfill(json={"workflow": _workflow(self.state)})


class _Sessions:
    def __init__(self):
        self.ws = None

    def __call__(self, ws):
        self.ws = ws


@pytest.mark.asyncio
async def test_a_workflow_row_opens_its_detail_and_a_decision_is_answered(page_url):
    server, sessions = _Server(), _Sessions()
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=AUTOPLAY)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_RECOGNITION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.route("**/api/workflows**", server)
        await page.goto(page_url)

        await page.click(".right-tabs [role=tab][title=Workflows]")
        row = page.locator(".workflow-row[data-workflow-id=wf-1]")
        await row.wait_for(timeout=10000)
        text = await row.text_content()
        for part in ("Speech migration", "phase 2/5 deepgram-engine", "6/10 tasks", "1 decision"):
            assert part in text, text
        assert await row.get_attribute("data-state") == "needs_you"

        await row.click()
        await page.wait_for_selector("#workflow-detail .workflow-detail-name")
        assert await page.text_content(".workflow-detail-name") == "Speech migration"
        selected = await page.evaluate(
            "() => document.querySelector('.centre-tabs [aria-selected=true]').textContent")
        assert selected == "Workflow"

        await page.click(".workflow-tabs [role=tab][title=Decisions]")
        await page.click(".workflow-option:text('Apple')")
        for _ in range(50):
            if server.posted:
                break
            await asyncio.sleep(0.1)
        assert server.posted == [("api/workflows/wf-1/decisions/engine", {"answer": "Apple"})]

        # The record changed: the hint makes the list read it again.
        server.state = "running"
        before = server.lists
        sessions.ws.send(json.dumps({"type": "workflows"}))
        await page.wait_for_function(
            "() => document.querySelector('.workflow-row').dataset.state === 'running'",
            timeout=10000)
        assert server.lists > before

        await page.click(".workflow-close")
        await page.wait_for_selector("#workflow-detail", state="detached")
        assert await page.locator("#conversation").count() == 1
        await browser.close()
