"""The page's shared navigation, through its real controls and the brain's
requests (uiNavigation.ts, Tabs.tsx, uiClient.ts).

Each control that navigates carries the command it runs
(`data-ui-command`, `data-ui-args`). Clicking it, and sending the brain's
`ui_request` for that same command over the voice socket, must leave the
page showing the same thing; and the brain's navigation never moves the
person's keyboard focus or counts as his activity.

Headless Chromium on the built page, with /api/workflows* and both sockets
replaced by Playwright.
"""

import asyncio
import json
import time

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_workflows import _Server, _Sessions
from tests.fixtures.waiting import until_async

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

PROTOCOL = 1


class _Voice:
    """A fake /ws/voice that welcomes the page's UI client and carries the
    brain's requests to it."""

    def __init__(self):
        self.ws = None
        self.client = None
        self.results: dict[str, dict] = {}
        self.sent = 0

    def __call__(self, ws):
        self.ws = ws
        ws.on_message(self._got)

    def _got(self, raw):
        msg = json.loads(raw)
        if msg.get("type") == "ui_hello":
            self.client = msg["clientId"]
            self.ws.send(json.dumps({"type": "ui_welcome", "clientId": self.client, "epoch": 1}))
        elif msg.get("type") == "ui_result":
            self.results[msg["requestId"]] = msg

    async def request(self, operation: dict, expected: int | None = None) -> dict:
        assert await until_async(lambda: self.client), "the page never said hello"
        self.sent += 1
        rid = f"req-{self.sent}"
        self.ws.send(json.dumps({
            "type": "ui_request", "protocol": PROTOCOL, "requestId": rid, "clientId": self.client,
            "epoch": 1, "turn": "t1", "expectedNavigationRevision": expected,
            "expiresAt": int(time.time() * 1000) + 15000, "operation": operation}))
        if await until_async(lambda: rid in self.results):
            return self.results[rid]
        raise AssertionError(f"no ui_result for {operation}")

    async def navigate(self, command: str, args: dict) -> dict:
        return await self.request({"kind": "navigate", "commandId": command, "args": args})


SHOWN = """() => ({
  right: document.querySelector('.right-tabs [aria-selected=true]')?.title,
  centreTabs: [...document.querySelectorAll('.centre-tabs [role=tab]')].map(t => t.textContent),
  centre: document.querySelector('.centre-tabs [aria-selected=true]')?.textContent,
  workflow: document.querySelector('#workflow-detail .workflow-detail-name')?.textContent ?? null,
})"""


async def _open(p, page_url):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    voice, activity = _Voice(), []

    async def report(route):
        activity.append(route.request.post_data)
        await route.fulfill(status=204, body="")

    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", _Sessions())
    await page.route("**/api/workflows**", _Server())
    await page.route("**/api/team/activity", report)
    await page.goto(page_url)
    await page.wait_for_selector(".right-tabs [role=tab]")
    return browser, page, voice, activity


async def _marks(page, selector):
    el = page.locator(selector)
    return await el.get_attribute("data-ui-command"), json.loads(await el.get_attribute("data-ui-args"))


@pytest.mark.asyncio
async def test_a_tab_and_the_brains_request_for_its_command_show_the_same(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice, _ = await _open(p, page_url)
        try:
            for title, tab_id in (("Work", "workflows"), ("Network", "network"), ("Folders", "folders"),
                                  ("Agents", "agents")):
                tab = f".right-tabs [role=tab][title={title}]"
                command, args = await _marks(page, tab)
                assert command == "right.tab.select" and args == {"tab": tab_id}
                await page.click(".right-tabs [role=tab][title=Queue]")
                await page.click(tab)
                by_hand = await page.evaluate(SHOWN)
                await page.click(".right-tabs [role=tab][title=Queue]")
                result = await voice.navigate(command, args)
                assert result["status"] == "completed", result
                assert await page.evaluate(SHOWN) == by_hand
                assert by_hand["right"] == title

            # The centre's tabs run main.tab.select the same way.
            command, args = await _marks(page, ".centre-tabs [role=tab][title=Folders]")
            assert (command, args) == ("main.tab.select", {"tab": "folders"})
            result = await voice.navigate(command, args)
            assert result["status"] == "completed" and result["effects"] == ["centre.view-shown"], result
            assert (await page.evaluate(SHOWN))["centre"] == "Folders"
            # A detail tab with nothing open is refused, not shown empty.
            result = await voice.navigate("main.tab.select", {"tab": "workflow"})
            assert (result["status"], result["reason"]) == ("unavailable", "no-entity")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_a_workflow_opens_and_closes_the_same_by_hand_and_by_request(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice, _ = await _open(p, page_url)
        try:
            await page.click(".right-tabs [role=tab][title=Work]")
            row = page.locator(".workflow-row[data-workflow-id=wf-1]")
            await row.wait_for(timeout=10000)

            await row.click()
            # Its name once read, not the placeholder drawn before the read answers.
            await async_api.expect(page.locator("#workflow-detail .workflow-detail-name")).to_have_text("Speech migration")
            by_hand = await page.evaluate(SHOWN)
            await page.click(".workflow-close")
            await page.wait_for_selector("#workflow-detail", state="detached")
            closed_by_hand = await page.evaluate(SHOWN)

            result = await voice.navigate("workflow.open", {"workflowId": "wf-1"})
            assert result["status"] == "completed" and result["effects"] == ["centre.view-shown"], result
            # Its name once read, not the placeholder drawn before the read answers.
            await async_api.expect(page.locator("#workflow-detail .workflow-detail-name")).to_have_text("Speech migration")
            assert await page.evaluate(SHOWN) == by_hand
            assert by_hand["centre"] == "Workflow" and by_hand["workflow"] == "Speech migration"

            result = await voice.navigate("workflow.close", {})
            assert result["status"] == "completed" and result["effects"] == ["centre.detail-closed"], result
            assert await page.evaluate(SHOWN) == closed_by_hand
            assert closed_by_hand["centre"] == "Conversation" and "Workflow" not in closed_by_hand["centreTabs"]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_brains_navigation_leaves_focus_and_activity_alone(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice, activity = await _open(p, page_url)
        try:
            # He has put the keyboard on the right panel's Network tab.
            await page.focus(".right-tabs [role=tab][title=Network]")
            await asyncio.sleep(1.5)
            reports = len(activity)
            for command, args in (("main.tab.select", {"tab": "folders"}),
                                  ("right.tab.select", {"tab": "workflows"}),
                                  ("workflow.open", {"workflowId": "wf-1"}),
                                  ("workflow.close", {})):
                result = await voice.navigate(command, args)
                assert result["status"] == "completed", (command, result)
                assert await page.evaluate("() => document.activeElement?.title") == "Network"
            await asyncio.sleep(1.5)
            assert len(activity) == reports, activity[reports:]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_a_request_planned_before_he_navigated_is_stale_and_leaves_his_view(page_url):
    """The brain reads the map, he clicks another tab, then the brain's
    request planned at the map's revision arrives: the page answers stale
    and keeps his tab, rather than taking his view back."""
    async with async_api.async_playwright() as p:
        browser, page, voice, _ = await _open(p, page_url)
        try:
            seen = await voice.request({"kind": "map", "region": "right"})
            assert seen["status"] == "completed", seen
            planned = seen["navigationRevision"]
            await page.click(".right-tabs [role=tab][title=Agents]")
            stale = await voice.request({"kind": "navigate", "commandId": "right.tab.select",
                                         "args": {"tab": "network"}}, expected=planned)
            assert stale["status"] == "stale" and stale["effects"] == [], stale
            assert (await page.evaluate(SHOWN))["right"] == "Agents"
            # Planned at the page's revision now, the same request is carried out.
            done = await voice.request({"kind": "navigate", "commandId": "right.tab.select",
                                        "args": {"tab": "network"}}, expected=stale["navigationRevision"])
            assert done["status"] == "completed", done
            assert (await page.evaluate(SHOWN))["right"] == "Network"
        finally:
            await browser.close()
