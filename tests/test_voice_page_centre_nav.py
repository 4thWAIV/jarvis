"""The centre views' navigation and targets, through their real controls and
the brain's requests (WorkflowDetail.tsx, FoldersView.tsx, FolderDetail.tsx,
ConversationView.tsx, AgentThread.tsx).

Each control that navigates carries the command it runs
(`data-ui-command`, `data-ui-args`); clicking it and sending the brain's
`ui_request` for that command must leave the view showing the same thing.
What the views register can be pointed at: an indication of a workflow's
tab, phase and decision, a folder entry's preview and a conversation
record arrives.

Headless Chromium on the built page, with the routes and both sockets
replaced by Playwright.
"""

import json
import re

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Record, _record_item
from tests.test_voice_page_folders import FOLDERS, _Api as _Folders
from tests.test_voice_page_navigation import _Voice
from tests.test_voice_page_workflows import _Server, _Sessions

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

TABS = ["overview", "phases", "tasks", "transcript", "progress", "waits", "decisions", "blockers", "events"]


class _Workflows(_Server):
    """The workflow routes, with two kinds of event in the log."""

    async def __call__(self, route):
        if route.request.url.split("?")[0].endswith("/events"):
            await route.fulfill(json={"events": [
                {"seq": 1, "at": 1_000_000.0, "kind": "phase_started", "item": "mic-recorder", "summary": "Mic"},
                {"seq": 2, "at": 1_000_010.0, "kind": "note", "item": None, "summary": "A note"},
            ]})
            return
        await super().__call__(route)


class _History(_Record):
    """The record, answered, with one earlier page behind the latest."""

    def __init__(self):
        super().__init__()
        self.status = "answered"

    async def page(self, route):
        if "before=" in route.request.url:
            await route.fulfill(json={"items": [_record_item(0, "heard", "The very first thing.", at=999_000.0)],
                                      "earlier": None})
        else:
            await route.fulfill(json={"items": self.items(), "earlier": 1})


async def _open(p, page_url, *routes):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    voice = _Voice()
    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", _Sessions())
    for pattern, handler in routes:
        await page.route(pattern, handler)
    await page.goto(page_url)
    await page.wait_for_selector(".right-tabs [role=tab]")
    return browser, page, voice


async def _indicate(voice, target, **extra):
    return await voice.request({"kind": "indicate", "target": target, "reveal": "never", **extra})


def _arrived(result):
    assert result["status"] == "completed", {k: result.get(k) for k in ("status", "reason", "arrival", "steps")}
    assert result.get("arrival") in ("animated", "static-reduced-motion"), result
    return True


async def _shown_tab(page):
    return await page.get_attribute(".workflow-tab-view", "aria-label")


@pytest.mark.asyncio
async def test_every_workflow_tab_is_the_same_by_hand_and_by_request_and_its_parts_can_be_pointed_at(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, ("**/api/workflows**", _Workflows()))
        try:
            assert (await voice.navigate("workflow.open", {"workflowId": "wf-1"}))["status"] == "completed"
            await page.wait_for_selector(".workflow-tabs [role=tab]")
            for tab in TABS:
                button = page.locator(f'.workflow-tabs [data-ui-args=\'{json.dumps({"workflowId": "wf-1", "tab": tab}, separators=(",", ":"))}\']')
                assert await button.get_attribute("data-ui-command") == "workflow.tab.select"
                await button.click()
                by_hand = await _shown_tab(page)
                assert by_hand.lower() == tab
                other = "overview" if tab != "overview" else "events"
                assert (await voice.navigate("workflow.tab.select", {"workflowId": "wf-1", "tab": other}))["status"] == "completed"
                result = await voice.navigate("workflow.tab.select", {"workflowId": "wf-1", "tab": tab})
                assert result["status"] == "completed", {k: result.get(k) for k in ("status", "reason", "arrival", "steps")}
                assert await _shown_tab(page) == by_hand
            # Another workflow's tab is refused, never shown in this one.
            result = await voice.navigate("workflow.tab.select", {"workflowId": "wf-9", "tab": "phases"})
            assert (result["status"], result["reason"]) == ("unavailable", "no-entity")

            # A tab (the last in its row: a leaf never crosses another), a phase
            # and a decision are pointed at where they are shown.
            ref = {"region": "main", "view": "main.workflow"}
            assert _arrived(await _indicate(voice, {**ref, "kind": "workflow", "key": "wf-1", "part": "tab.events"}))
            await voice.navigate("workflow.tab.select", {"workflowId": "wf-1", "tab": "phases"})
            assert _arrived(await _indicate(voice, {**ref, "kind": "workflowPhase", "key": "wf-1/deepgram-engine",
                                                    "part": "state"}))
            # Not on the shown tab: not found, and no other tab is opened.
            result = await _indicate(voice, {**ref, "kind": "workflowDecision", "key": "wf-1/engine"})
            assert result["status"] == "not_found", result
            assert await _shown_tab(page) == "Phases"
            await voice.navigate("workflow.tab.select", {"workflowId": "wf-1", "tab": "decisions"})
            assert _arrived(await _indicate(voice, {**ref, "kind": "workflowDecision", "key": "wf-1/engine",
                                                    "part": "options"}))

            # The events' kind: the brain's filter and the select are one state.
            await voice.navigate("workflow.tab.select", {"workflowId": "wf-1", "tab": "events"})
            await page.wait_for_selector(".workflow-events tr[data-kind=note]")
            result = await voice.navigate("workflow.events.filter", {"workflowId": "wf-1", "kind": "note"})
            assert result["status"] == "completed" and result["effects"] == ["list.filtered"], result
            assert await page.input_value(".workflow-events select") == "note"
            assert await page.locator(".workflow-events tr").count() == 1
            await page.select_option(".workflow-events select", "")
            await page.wait_for_function("document.querySelectorAll('.workflow-events tr').length === 2")

            # Closing by request goes back to Conversation.
            assert (await voice.navigate("workflow.close", {}))["status"] == "completed"
            await page.wait_for_selector("#workflow-detail", state="detached")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_a_workflows_hidden_item_is_revealed_by_opening_it_on_its_tab(page_url):
    """An item of a workflow that is not open (or open on another tab) is
    brought into view by the registered commands its key names: the
    workflow opened, then the tab that draws it selected, their effects
    reported; then the signal arrives. A person's tab is never put back."""
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, ("**/api/workflows**", _Workflows()))
        try:
            ref = {"region": "main", "view": "main.workflow"}
            await page.wait_for_selector("#workflow-detail", state="detached")
            result = await voice.request({"kind": "indicate", "reveal": "navigate", "target": {
                **ref, "kind": "workflowDecision", "key": "wf-1/engine", "part": "options"}})
            assert _arrived(result)
            assert "centre.view-shown" in result["effects"], result
            assert await _shown_tab(page) == "Decisions"
            # Open on Decisions, a phase is brought in on Phases.
            result = await voice.request({"kind": "indicate", "reveal": "navigate", "target": {
                **ref, "kind": "workflowPhase", "key": "wf-1/deepgram-engine", "part": "state"}})
            assert _arrived(result)
            assert await _shown_tab(page) == "Phases"
            # Without reveal it is still only looked for where it is shown.
            result = await _indicate(voice, {**ref, "kind": "workflowDecision", "key": "wf-1/engine"})
            assert result["status"] == "not_found", result
            assert await _shown_tab(page) == "Phases"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_folder_catalog_and_a_folders_contents_are_the_same_by_hand_and_by_request(page_url):
    aurora = FOLDERS[0]["id"]
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, ("**/api/folders**", _Folders()))
        try:
            await voice.navigate("main.tab.select", {"tab": "folders"})
            await page.wait_for_selector(".folder-rows .folder-row")
            rows = "document.querySelectorAll('.folders-catalog .folder-row').length"

            result = await voice.navigate("folders.list", {"view": "discovered"})
            assert result["status"] == "completed", {k: result.get(k) for k in ("status", "reason", "arrival", "steps")}
            assert (await page.text_content(".folder-tab[aria-selected=true]")).startswith("Discovered")
            await page.click(".folder-tab >> text=Registered")
            assert (await page.text_content(".folder-tab[aria-selected=true]")).startswith("Registered")

            assert (await voice.navigate("folders.search", {"text": "aur"}))["status"] == "completed"
            assert await page.input_value(".folder-search input") == "aur"
            assert await page.evaluate(rows) == 1
            await page.fill(".folder-search input", "")
            assert await page.evaluate(rows) == len(FOLDERS)

            assert (await voice.navigate("folders.filter", {"availability": "missing"}))["status"] == "completed"
            assert await page.evaluate(rows) == 1
            await page.select_option(".folders-filters select >> nth=1", "")
            assert await page.evaluate(rows) == len(FOLDERS)

            assert (await voice.navigate("folders.add.open", {"open": True}))["status"] == "completed"
            assert await page.locator(".folder-add").count() == 1
            await page.click(".folder-add .folder-button.quiet")
            assert await page.locator(".folder-add").count() == 0

            # A folder's file preview, opened by request and pointed at.
            assert (await voice.navigate("folders.open", {"folderId": aurora}))["status"] == "completed"
            await page.wait_for_selector(".folder-entry.kind-file")
            entry = page.locator(".folder-entry.kind-file")
            assert await entry.get_attribute("data-ui-command") == "folder.preview"
            result = await voice.navigate("folder.preview", {"folderId": aurora, "path": "README.md"})
            assert result["status"] == "completed", {k: result.get(k) for k in ("status", "reason", "arrival", "steps")}
            await page.wait_for_selector(".folder-preview pre")
            folder = {"region": "main", "view": "main.folders", "kind": "folder", "key": aurora}
            assert _arrived(await _indicate(voice, {**folder, "part": "location"}))
            assert _arrived(await _indicate(voice, {"region": "main", "view": "main.folders", "kind": "folderEntry",
                                                    "key": f"{aurora}/README.md", "part": "row"}))
            # Another folder's contents are refused; a stale revision too.
            other = FOLDERS[1]["id"]
            result = await voice.navigate("folder.browse", {"folderId": other})
            assert (result["status"], result["reason"]) == ("unavailable", "no-entity")
            result = await voice.navigate("folder.browse", {"folderId": aurora, "revision": 9})
            assert result["status"] == "stale", result
            # Browsing its top again closes the preview, as the crumb does by hand.
            assert (await voice.navigate("folder.browse", {"folderId": aurora}))["status"] == "completed"
            await page.wait_for_selector(".folder-preview", state="detached")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_history_and_a_thread_page_and_open_records_the_same_by_hand_and_by_request(page_url):
    record = _History()
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url,
                                           ("**/api/conversation/agents/**", record.thread),
                                           (re.compile(r".*/api/conversation(\?.*)?$"), record.page))
        try:
            await page.wait_for_selector(".record-status")
            # A message opens to its answer by request and closes by hand.
            assert await page.get_attribute(".record-status", "data-ui-command") == "conversation.record.expand"
            result = await voice.navigate("conversation.record.expand", {"recordId": "r3", "open": True})
            assert result["status"] == "completed", {k: result.get(k) for k in ("status", "reason", "arrival", "steps")}
            assert await page.text_content(".record-answer") == "Postgres it is."
            await page.click(".record-status")
            await page.wait_for_selector(".record-answer", state="detached")
            # A record that is not on the page cannot be opened.
            result = await voice.navigate("conversation.record.expand", {"recordId": "r99", "open": True})
            assert (result["status"], result["reason"]) == ("unavailable", "no-entity")

            assert _arrived(await _indicate(voice, {"region": "main", "view": "main.conversation",
                                                    "kind": "conversationRecord", "key": "r3", "part": "expand"}))

            # The earlier page, by request; then there is nothing earlier.
            result = await voice.navigate("conversation.earlier", {})
            assert result["status"] == "completed" and result["effects"] == ["list.paged"], result
            await page.wait_for_selector(".conversation-history >> text=The very first thing.")
            await page.wait_for_selector(".conversation-history .history-earlier", state="detached")
            result = await voice.navigate("conversation.earlier", {})
            assert (result["status"], result["reason"]) == ("unavailable", "no-entity")

            # The agent's thread, its record pointed at, closed by request.
            assert (await voice.navigate("thread.open", {"sessionId": "h"}))["status"] == "completed"
            await page.wait_for_selector("#agent-thread .record-message")
            assert _arrived(await _indicate(voice, {"region": "main", "view": "main.thread",
                                                    "kind": "threadRecord", "key": "h/r3", "part": "text"}))
            assert await page.get_attribute(".thread-close", "data-ui-command") == "thread.close"
            assert (await voice.navigate("thread.close", {}))["status"] == "completed"
            await page.wait_for_selector("#agent-thread", state="detached")
        finally:
            await browser.close()
