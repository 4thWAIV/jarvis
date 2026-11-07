"""Takeover mode (uiNavigation.ts `layout`, TakeoverBar.tsx, RightPanel.tsx's
rail), by what a real browser draws at the two window sizes JARVIS is used
at: a page package or a workflow shown in the centre takes over the page by
`layout.takeover` (the brain's request here; the click and the key run the
same handler), the centre widens over the right panel, which folds to a
rail of its six tab icons, the takeover bar holds the item's name, the
conversation's state, the microphone and Restore layout, and the normal
layout comes back with Restore, another view or the item closing.

Also the microphone fix that holds in the normal layout too: the
microphone corner never covers a package's frame.

Headless Chromium on the built page. The package is the example
mic-check-page, served by JARVIS's real app (TestClient) as in
test_voice_page_packages.py; the workflow is test_voice_page_workflows's.
With TAKEOVER_SHOTS set to a folder, each state is saved there as a PNG.
"""

import json
import os
from pathlib import Path

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_navigation import _Voice
from tests.test_voice_page_packages import _through, jarvis_app  # noqa: F401
from tests.test_voice_page_workflows import _Server, _Sessions
from tests.voice_page_input import start_listening

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

SIZES = {"1440": (1440, 813), "1280": (1280, 713)}   # the page a Chrome window of that size leaves

RECT = """(sel) => { const el = document.querySelector(sel); if (!el) return null;
  const r = el.getBoundingClientRect(); return {left: r.left, top: r.top, right: r.right, bottom: r.bottom,
                                                width: r.width, height: r.height}; }"""


def _overlap(a, b) -> bool:
    return a["left"] < b["right"] and b["left"] < a["right"] and a["top"] < b["bottom"] and b["top"] < a["bottom"]


async def _shot(page, name):
    folder = os.environ.get("TAKEOVER_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(Path(folder) / f"{name}.png"))


async def _open(p, page_url, size, routes=()):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    width, height = SIZES[size]
    page = await browser.new_page(viewport={"width": width, "height": height})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    voice = _Voice()
    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", _Sessions())
    for pattern, handler in routes:
        await page.route(pattern, handler)
    await page.goto(page_url)
    await page.wait_for_selector(".right-tabs [role=tab]")
    for _ in range(100):
        if voice.client:
            break
        await page.wait_for_timeout(50)
    return browser, page, voice


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("size", list(SIZES))
async def test_a_package_takes_over_the_page_and_the_layout_comes_back(page_playwright, page_url, jarvis_app, size):
    client, token, package = jarvis_app
    from jarvis import packages_api
    async with page_playwright as p:
        browser, page, voice = await _open(p, page_url, size, [("**/api/packages/**", _through(client, token))])
        try:
            # Nothing to take over on Conversation: refused, nothing moves.
            refused = await voice.navigate("layout.takeover", {})
            assert (refused["status"], refused["reason"]) == ("unavailable", "no-entity"), refused

            voice.ws.send(json.dumps({"type": "package_page", "package": packages_api.describe(package)}))
            await async_api.expect(page.locator(".centre-tabs [aria-label='mic-check-page']")) \
                .to_have_attribute("aria-selected", "true")
            await page.frame_locator("#package-view iframe").locator("h1").wait_for()
            # Listening, so the corner holds the live meter.
            await start_listening(page)
            await page.wait_for_selector("#centre-mic #mic-meter, #centre-mic .mic-meter, #centre-mic canvas",
                                         timeout=10000)
            normal = await page.evaluate(RECT, "#package-view iframe")
            corner = await page.evaluate(RECT, "#centre-mic")
            # The microphone corner covers no part of the frame.
            assert corner and not _overlap(normal, corner), (normal, corner)
            await _shot(page, f"{size}-normal")

            result = await voice.navigate("layout.takeover", {})
            assert result["status"] == "completed" and result["effects"] == ["layout.taken-over"], result
            bar = page.locator("#takeover-bar")
            await async_api.expect(bar).to_be_visible()
            await async_api.expect(bar.locator(".takeover-name")).to_have_text("mic-check-page")
            assert await page.locator(".centre-tabs").count() == 0
            assert await page.evaluate("document.documentElement.dataset.layout") == "takeover"
            # The right panel is a rail of its six icons, none of its views.
            rail = await page.evaluate(RECT, "#right-column")
            assert rail["width"] <= 60, rail
            assert await page.locator("#right-column.rail .rail-tabs [role=tab]").count() == 6
            assert await page.locator("#right-column .right-panel").count() == 0
            # The microphone, its health slot and Stop's slot moved into the bar; none left in the corner.
            assert await page.locator("#takeover-bar #mic-control").count() == 1
            assert await page.locator("#takeover-bar #stop-slot").count() == 1
            assert await page.locator("#centre-mic").count() == 0
            # The frame is wider and taller, and nothing of the bar covers it.
            taken = await page.evaluate(RECT, "#package-view iframe")
            bar_box = await page.evaluate(RECT, "#takeover-bar")
            assert taken["width"] >= normal["width"] + 150, (normal, taken)
            assert taken["height"] > normal["height"], (normal, taken)
            assert not _overlap(taken, bar_box) and not _overlap(taken, rail), (taken, bar_box, rail)
            assert taken["right"] <= rail["left"]
            await _shot(page, f"{size}-taken-over")

            # A rail icon opens its tab over the takeover; Escape closes it.
            await page.click("#right-column.rail [data-ui-tab=queue]")
            flyout = page.locator("#right-column .rail-flyout")
            await async_api.expect(flyout).to_be_visible()
            assert (await page.evaluate("document.documentElement.dataset.layout")) == "takeover"
            await _shot(page, f"{size}-flyout")
            await page.keyboard.press("Escape")
            await async_api.expect(flyout).to_have_count(0)

            # Restore layout: the tabs, the panel and the frame as they were.
            await page.click("#takeover-bar .takeover-restore")
            await async_api.expect(bar).to_have_count(0)
            await async_api.expect(page.locator(".centre-tabs [aria-label='mic-check-page']")) \
                .to_have_attribute("aria-selected", "true")
            assert await page.locator("#right-column.rail").count() == 0
            assert await page.locator("#right-column .right-panel").count() == 1
            assert await page.evaluate(RECT, "#package-view iframe") == normal
            assert await page.locator("#centre-mic #mic-control").count() == 1
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_a_workflow_takes_over_and_another_view_or_closing_it_restores(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, "1440", [("**/api/workflows**", _Server())])
        try:
            opened = await voice.navigate("workflow.open", {"workflowId": "wf-1"})
            assert opened["status"] == "completed", opened
            await async_api.expect(page.locator("#workflow-detail .workflow-detail-name")).to_have_text("Speech migration")
            result = await voice.navigate("layout.takeover", {})
            assert result["effects"] == ["layout.taken-over"], result
            await async_api.expect(page.locator("#takeover-bar .takeover-name")).to_have_text("Speech migration")
            # Taking over again changes nothing.
            again = await voice.navigate("layout.takeover", {})
            assert again["status"] == "completed" and again["effects"] == [], again
            # The brain's request for a right tab opens it over the takeover.
            shown = await voice.navigate("right.tab.select", {"tab": "network"})
            assert shown["status"] == "completed", shown
            await async_api.expect(page.locator("#right-column .rail-flyout")).to_be_visible()
            closed = await voice.navigate("layout.flyout.close", {})
            assert closed["status"] == "completed", closed
            await async_api.expect(page.locator("#right-column .rail-flyout")).to_have_count(0)

            # Another centre view restores the layout, in its own command.
            back = await voice.navigate("main.tab.select", {"tab": "conversation"})
            assert back["effects"] == ["centre.view-shown", "layout.restored"], back
            await async_api.expect(page.locator("#takeover-bar")).to_have_count(0)
            await async_api.expect(page.locator(".centre-tabs")).to_be_visible()

            # Closing what took over restores it too.
            await voice.navigate("main.tab.select", {"tab": "workflow"})
            await voice.navigate("layout.takeover", {})
            await async_api.expect(page.locator("#takeover-bar")).to_be_visible()
            gone = await voice.navigate("workflow.close", {})
            assert gone["effects"] == ["centre.detail-closed", "layout.restored"], gone
            await async_api.expect(page.locator("#takeover-bar")).to_have_count(0)
            restore = await voice.navigate("layout.restore", {})
            assert (restore["status"], restore["reason"]) == ("unavailable", "no-entity"), restore
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_take_over_by_its_button_and_by_the_key_never_while_typing(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, "1440", [("**/api/workflows**", _Server())])
        try:
            bar = page.locator("#takeover-bar")
            # On Conversation the key does nothing.
            await page.keyboard.press("Alt+Shift+KeyT")
            assert await page.evaluate("document.documentElement.dataset.layout") == "normal"

            await page.click(".right-tabs [role=tab][title=Work]")
            await page.locator(".workflow-row[data-workflow-id=wf-1]").click()
            await async_api.expect(page.locator("#workflow-detail .workflow-detail-name")).to_have_text("Speech migration")
            button = page.locator("#workflow-detail .takeover-enter")
            assert await button.get_attribute("data-ui-command") == "layout.takeover"
            await button.click()
            await async_api.expect(bar).to_be_visible()
            # Taken over, Take over gives way to Restore layout in the bar.
            await async_api.expect(button).to_have_count(0)
            await _shot(page, "workflow-taken-over")
            await page.click("#takeover-bar .takeover-restore")
            await async_api.expect(bar).to_have_count(0)

            # The key toggles it from anywhere but a text field.
            await page.locator("#workflow-detail .workflow-detail-name").click()
            await page.keyboard.press("Alt+Shift+KeyT")
            await async_api.expect(bar).to_be_visible()
            await page.keyboard.press("Alt+Shift+KeyT")
            await async_api.expect(bar).to_have_count(0)
            await page.evaluate("""() => { const f = document.createElement('input'); f.id = 'typing-here';
                                           document.body.appendChild(f); f.focus(); }""")
            await page.keyboard.press("Alt+Shift+KeyT")
            await page.wait_for_timeout(300)
            assert await bar.count() == 0

            # The brain's request, then × on the item: the layout comes back with it.
            await voice.navigate("layout.takeover", {})
            await async_api.expect(bar).to_be_visible()
            await page.evaluate("document.getElementById('typing-here').remove()")
            closed = await voice.navigate("workflow.close", {})
            assert "layout.restored" in closed["effects"], closed
            await async_api.expect(bar).to_have_count(0)
        finally:
            await browser.close()
