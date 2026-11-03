"""The navigation that used to be each view's own state, now registered
commands a person's control and the brain's request both run: a folder's
Edit / Reconnect / Remove forms (`folder.mode`), the Add form's Cancel, the
voice record's range, zoom and quiet page (`voice.range`, `voice.zoom`,
`voice.quiet`), a workflow opened on a tab (`workflow.open {tab}`), and the
Queue's New message, Respond and Reply (`queue.new`, `queue.reply`), a link
to another message (`queue.message.show`) and the questions before
discarding and before Done (`queue.discard.ask`, `queue.done.ask`). Also the
targets and reveals added with them: a task card's progress bar, a
subagent's row in the right panel, a subsystem's check and a folder's entry
brought into view by the commands their keys name. And the one rule for
the brain's map: every target's label in the language in force.

Each control carries the command it runs (`data-ui-command`); clicking it
and the brain's `ui_request` leave the view showing the same thing.
Headless Chromium on the built page, with the routes and both sockets
replaced by Playwright.
"""

import json
import time

import pytest

from tests import messaging_queue_scenario as sc
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_centre_nav import _Workflows, _arrived, _open, _shown_tab
from tests.test_voice_page_folders import FOLDERS, _Api as _Folders
from tests.test_voice_page_navigation import _Voice
from tests.test_voice_page_network import _Sessions as _NetworkSessions
from tests.test_voice_page_subsystems import _Server as _Subsystems
from tests.test_voice_page_voice_health import record as voice_record
from tests.test_voice_page_workflows import _workflow

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


def _completed(result):
    assert result["status"] == "completed", {k: result.get(k) for k in ("status", "reason", "steps")}
    return result


async def _command_of(locator):
    return await locator.get_attribute("data-ui-command"), json.loads(await locator.get_attribute("data-ui-args"))


@pytest.mark.asyncio
async def test_a_folders_forms_and_the_add_forms_cancel_are_the_same_by_hand_and_by_request(page_url):
    aurora, other = FOLDERS[0]["id"], FOLDERS[1]["id"]
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, ("**/api/folders**", _Folders()))
        try:
            await voice.navigate("main.tab.select", {"tab": "folders"})
            _completed(await voice.navigate("folders.open", {"folderId": aurora}))
            actions = page.locator(".folder-detail .folder-actions .folder-button")
            await actions.first.wait_for()
            edit = actions.nth(1)
            assert await _command_of(edit) == ("folder.mode", {"folderId": aurora, "mode": "edit"})

            # Edit, by hand then by request: the same form, filled from the folder.
            await edit.click()
            form = page.locator(".folder-detail .folder-edit")
            await form.wait_for()
            assert await form.locator("input").input_value() == FOLDERS[0]["display_name"]
            await form.locator(".folder-button.quiet").click()
            await form.wait_for(state="detached")
            _completed(await voice.navigate("folder.mode", {"folderId": aurora, "mode": "edit"}))
            await form.wait_for()
            _completed(await voice.navigate("folder.mode", {"folderId": aurora, "mode": "view"}))
            await form.wait_for(state="detached")

            # Remove asks, and Keep it puts the question away; nothing is removed.
            _completed(await voice.navigate("folder.mode", {"folderId": aurora, "mode": "remove"}))
            confirm = page.locator(".folder-detail .folder-confirm")
            await confirm.wait_for()
            keep = confirm.locator(".folder-button.quiet")
            assert await _command_of(keep) == ("folder.mode", {"folderId": aurora, "mode": "view"})
            await keep.click()
            await confirm.wait_for(state="detached")

            # Reconnect by hand, closed by request.
            await actions.nth(2).click()
            reconnect = page.locator(".folder-detail form.folder-add")
            await reconnect.wait_for()
            _completed(await voice.navigate("folder.mode", {"folderId": aurora, "mode": "view"}))
            await reconnect.wait_for(state="detached")

            # Another folder's form is refused, never opened on this one.
            result = await voice.navigate("folder.mode", {"folderId": other, "mode": "edit"})
            assert (result["status"], result["reason"]) == ("unavailable", "no-entity")

            # The Add form's Cancel runs folders.add.open {open: false}.
            _completed(await voice.navigate("folders.add.open", {"open": True}))
            cancel = page.locator(".folders-catalog .folder-add .folder-button.quiet")
            assert await _command_of(cancel) == ("folders.add.open", {"open": False})
            await cancel.click()
            await page.locator(".folders-catalog .folder-add").wait_for(state="detached")

            # A folder entry not shown is brought in by the commands its key
            # names: the folder opened, the directory that lists it browsed.
            _completed(await voice.navigate("folders.close", {}))
            await page.locator(".folder-detail").wait_for(state="detached")
            result = await voice.request({"kind": "indicate", "reveal": "navigate", "target": {
                "region": "main", "view": "main.folders", "kind": "folderEntry",
                "key": f"{aurora}/README.md", "part": "row"}})
            assert _arrived(result)
            assert await page.locator(f'.folder-detail[data-folder="{aurora}"]').count() == 1
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_voice_records_range_zoom_and_quiet_page_are_the_same_by_hand_and_by_request(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url)
        try:
            await page.route("**/api/voice/diagnostics", lambda r: r.fulfill(
                content_type="application/json", body=json.dumps(voice_record(time.time())[0])))
            await page.route("**/api/voice/diagnostics/samples?*", lambda r: r.fulfill(
                content_type="application/json", body=json.dumps(voice_record(time.time())[1])))
            _completed(await voice.navigate("voice.diagnostics.open", {}))
            await page.wait_for_selector("#voice-diagnostics .voice-range-button")
            selected = "document.querySelector('.voice-range-button.selected')?.dataset.uiArgs ?? null"

            hour = page.locator('.voice-range-button[data-ui-args=\'{"range":"1h"}\']')
            assert await hour.get_attribute("data-ui-command") == "voice.range"
            await hour.click()
            assert json.loads(await page.evaluate(selected)) == {"range": "1h"}
            _completed(await voice.navigate("voice.range", {"range": "1d"}))
            assert json.loads(await page.evaluate(selected)) == {"range": "1d"}

            # A drop's mark zooms to it by hand; voice.zoom without a moment
            # zooms out; a range chosen by request zooms out too.
            mark = page.locator(".voice-timeline .spark-mark").first
            await mark.wait_for()
            assert await mark.get_attribute("data-ui-command") == "voice.zoom"
            await mark.click()
            await page.wait_for_selector(".voice-zoomed")
            assert await page.evaluate(selected) is None
            _completed(await voice.navigate("voice.zoom", {}))
            await page.wait_for_selector(".voice-zoomed", state="detached")
            _completed(await voice.navigate("voice.zoom", {"at": int(time.time()) - 60}))
            await page.wait_for_selector(".voice-zoomed")
            _completed(await voice.navigate("voice.range", {"range": "15m"}))
            await page.wait_for_selector(".voice-zoomed", state="detached")

            # The quiet page, started by request and ended by hand.
            quiet = page.locator(".voice-quiet-button")
            _completed(await voice.navigate("voice.quiet", {"quiet": True}))
            await async_api.expect(quiet).to_have_attribute("aria-pressed", "true")
            assert await _command_of(quiet) == ("voice.quiet", {"quiet": False})
            await quiet.click()
            await async_api.expect(quiet).to_have_attribute("aria-pressed", "false")
        finally:
            await browser.close()


class _WorkflowsWithWork(_Workflows):
    """The workflow, with a counted task, and a subagent on its own row."""

    async def __call__(self, route):
        path = route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        w = _workflow(self.state)
        w["items"] = w["items"] + [{"slug": "wire-engine", "kind": "task", "phase": "deepgram-engine",
                                    "title": "Wire the engine", "state": "running", "done": 2, "total": 5}]
        if path == "api/workflows":
            await route.fulfill(json={"workflows": [w], "subagents": [{
                "key": "agent:s9/a1", "session_id": "s9", "session_name": "hammer", "project": "jarvis",
                "id": "a1", "description": "Read the logs", "type": "general", "background": False,
                "depth": 1, "state": "working", "started": time.time() - 30, "written": time.time() - 2,
                "dismiss_at": None}]})
        elif path.startswith("api/workflows/wf-1") and not path.endswith(("/events", "/transcript")):
            await route.fulfill(json={"workflow": w})
        else:
            await super().__call__(route)


@pytest.mark.asyncio
async def test_a_workflow_opens_on_a_tab_and_its_progress_bar_and_a_subagents_row_are_targets(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, ("**/api/workflows**", _WorkflowsWithWork()))
        try:
            _completed(await voice.navigate("workflow.open", {"workflowId": "wf-1", "tab": "tasks"}))
            await page.wait_for_selector(".task-card[data-task=wire-engine] .workflow-bar")
            assert await _shown_tab(page) == "Tasks"
            assert _arrived(await voice.request({"kind": "indicate", "reveal": "never", "target": {
                "region": "main", "view": "main.workflow", "kind": "workflowTask",
                "key": "wf-1/wire-engine", "part": "progress"}}))
            # Open already, the tab is moved by the same command.
            _completed(await voice.navigate("workflow.open", {"workflowId": "wf-1", "tab": "events"}))
            assert await _shown_tab(page) == "Events"

            # The subagent's row in the right panel, its tab opened for it.
            result = await voice.request({"kind": "indicate", "reveal": "navigate", "target": {
                "region": "right", "view": "right.workflows", "kind": "subagent", "key": "s9/a1", "part": "row"}})
            assert _arrived(result)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_a_subsystems_check_is_brought_into_view_by_opening_its_subsystem(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, ("**/api/diagnostics/subsystems", _Subsystems()))
        try:
            result = await voice.request({"kind": "indicate", "reveal": "navigate", "target": {
                "region": "main", "view": "main.subsystem", "kind": "subsystemCheck",
                "key": "communications/watcher", "part": "row"}})
            assert _arrived(result)
            assert await page.get_attribute("#subsystem-detail", "data-subsystem") == "communications"
        finally:
            await browser.close()


class _QueueVoice(_Voice):
    """The navigation test's voice socket, which the Queue's routes also
    send a review's capture through."""

    def send(self, *msgs):
        for m in msgs:
            self.ws.send(json.dumps(m))


async def _queue(p, url):
    world = await sc.build()
    voice = _QueueVoice()
    routes = sc.QueueRoutes(world, voice)
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", _NetworkSessions())
    envelope = json.dumps(sc.network_envelope())
    await page.route("**/api/team/network", lambda r: r.fulfill(status=200, content_type="application/json",
                                                                 body=envelope))
    await routes.install(page)
    await page.goto(url)
    await page.wait_for_selector(".right-tabs [data-ui-tab=queue]")
    _completed(await voice.navigate("right.tab.select", {"tab": "queue"}))
    await page.wait_for_selector('.queue-list[data-list="waiting"] > li')
    return browser, page, world, voice


@pytest.mark.asyncio
async def test_the_queues_new_reply_links_and_questions_are_the_same_by_hand_and_by_request(page_url):
    async with async_api.async_playwright() as p:
        browser, page, world, voice = await _queue(p, page_url)
        try:
            item = page.locator("#queue-item-view")
            composer = page.locator("#queue-item-view .message-composer")

            # New message: a blank one, by hand and by request alike.
            new = page.locator(".queue-new")
            assert await new.get_attribute("data-ui-command") == "queue.new"
            _completed(await voice.navigate("queue.new", {}))
            await composer.wait_for()
            await async_api.expect(new).to_have_attribute("aria-current", "true")
            _completed(await voice.navigate("queue.close", {}))
            await item.wait_for(state="detached")

            # Respond answers the latest message from Matt; Reply by request
            # opens the same editor; nothing is saved until he writes.
            newest = world.sent["newest"]
            _completed(await voice.navigate("queue.open", {"threadId": str(newest.thread_id)}))
            respond = item.locator(".item-actions button[data-ui-command='queue.reply']")
            assert json.loads(await respond.get_attribute("data-ui-args")) == {"messageId": str(newest.message_id)}
            _completed(await voice.navigate("queue.reply", {"messageId": str(newest.message_id)}))
            await composer.wait_for()
            await async_api.expect(page.locator(".composer-to")).to_contain_text("Matt")
            assert not [d for d in world.store.drafts.values() if d.thread_id == newest.thread_id]
            # A second reply while one is being written is refused.
            result = await voice.navigate("queue.reply", {"messageId": str(newest.message_id)})
            assert (result["status"], result["reason"]) == ("unavailable", "no-entity")

            # Discard asks first: asked by request, put away by hand, and back.
            ask = composer.locator(".composer-discard")
            assert await _command_of(ask) == ("queue.discard.ask", {"open": True})
            _completed(await voice.navigate("queue.discard.ask", {"open": True}))
            await composer.locator(".composer-confirm").wait_for()
            keep = composer.locator("button[data-ui-command='queue.discard.ask']")
            await keep.click()
            await composer.locator(".composer-confirm").wait_for(state="detached")

            # Done waits for unsent text: why, then whether to discard the
            # reply, each by request; Keep working by hand puts it away.
            await page.fill(".composer-text", "Not sent yet.")
            _completed(await voice.navigate("queue.done.ask", {"step": "blockers"}))
            blockers = item.locator(".item-blockers")
            await blockers.wait_for()
            asks = blockers.locator("button[data-ui-command='queue.done.ask']")
            await async_api.expect(asks).to_have_count(2)          # Discard the reply…, Keep working
            _completed(await voice.navigate("queue.done.ask", {"step": "discard"}))
            await async_api.expect(asks).to_have_count(1)          # Keep working; the discard is his own act
            await asks.click()
            await blockers.wait_for(state="detached")
            assert await page.input_value(".composer-text") == "Not sent yet.", "nothing was discarded"

            # A related message in another item: its own item opens, with Back.
            creds = world.sent["creds"]
            _completed(await voice.navigate("queue.open", {"threadId": str(creds.thread_id)}))
            link = item.locator(".message-related button[data-ui-command='queue.message.show']")
            await link.wait_for()
            target = json.loads(await link.get_attribute("data-ui-args"))["messageId"]
            assert target == str(world.sent["deploy"].message_id)
            result = _completed(await voice.navigate("queue.message.show", {"messageId": target}))
            assert "centre.detail-replaced" in result["effects"], result
            await async_api.expect(item).to_have_attribute("data-thread-id", str(world.sent["deploy"].thread_id))
            await item.locator(".item-back").wait_for()
            # One in this item is only scrolled to.
            _completed(await voice.navigate("queue.message.show", {"messageId": target}))
            await async_api.expect(item).to_have_attribute("data-thread-id", str(world.sent["deploy"].thread_id))

            # A response that did not fail cannot be put back in the editor.
            result = await voice.navigate("queue.draft.edit", {"messageId": target})
            assert (result["status"], result["reason"]) == ("unavailable", "no-entity")
        finally:
            await browser.close()


class _LanguageSessions:
    """The sessions socket: its opening snapshot, then a language frame."""

    def __init__(self):
        self.ws = None

    def __call__(self, ws):
        self.ws = ws
        ws.send(json.dumps({"type": "snapshot", "sessions": []}))

    def language(self, language):
        self.ws.send(json.dumps({"type": "language", "language": language}))


async def _labels(voice):
    result = _completed(await voice.request({"kind": "map"}))
    return {(t["ref"]["kind"], t["ref"].get("key")): t["label"] for t in result["map"]["targets"]}


@pytest.mark.asyncio
async def test_the_maps_labels_follow_the_language_in_force_and_identities_do_not(page_url):
    """One rule for discovery: every target's label is in the language in
    force (Settings' sections included); its kind and key are the same in
    both languages."""
    sessions = _LanguageSessions()
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        voice = _Voice()
        await page.route_web_socket("**/ws/voice", voice)
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.goto(page_url)
        await page.wait_for_selector(".right-tabs [role=tab]")
        try:
            _completed(await voice.navigate("settings.open", {"section": "voice"}))
            await page.wait_for_selector("#settings-language")
            english = await _labels(voice)
            assert english[("orb", None)] == "The orb"
            assert english[("settingsSection", "voice")] == "Settings, Voice and language"
            assert english[("settings", None)] == "Settings"

            sessions.language("es")
            await page.wait_for_function("document.documentElement.lang === 'es-419'")
            spanish = await _labels(voice)
            assert set(spanish) == set(english), "the same targets, by the same identities"
            assert spanish[("orb", None)] == "El orbe"
            assert spanish[("settingsSection", "voice")] == "Configuración, Voz e idioma"
            assert spanish[("settings", None)] == "Configuración"
            assert spanish[("status", None)] == "Estado"
        finally:
            await browser.close()
