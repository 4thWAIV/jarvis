"""An architecture view set in the actual PackageView: the frame shows one
view at a time, the border's chapters name each slide's view and move the
frame between the views, the source and check links follow the view shown,
and an element he clicks in one view stays lit in every other view that
shows it (the same id), the chapters saying which views show it.

Every answer here is this test JARVIS's own, in its own data folder. Needs
the pinned kit asset (JARVIS_LANGUAGE_KIT_ASSET; the language-kit workflow)."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from jarvis import data_paths, language_projection, make_package
from tests.test_architecture_view_set import MARKDOWN, PURPOSE, SCENARIO, envelopes, walk
from tests.test_brain_page_parity import wired  # noqa: F401  (a fixture)
from tests.test_language_documents import draft, installed_kit  # noqa: F401  (fixtures)
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_language_documents import canonical_server  # noqa: F401

pytestmark = pytest.mark.page


def sent(server_base: str, draft, tmp: Path) -> tuple[str, list[dict]]:  # noqa: F811
    """A view set of two checked documents, made by the factory and sent to
    this JARVIS's inbox: (its inbox id, the two envelopes)."""
    first, second = envelopes(draft)
    folder = tmp / "store-views" / "architecture"
    language_projection.project(first, folder, MARKDOWN, json.dumps(walk(
        [first["document_id"], second["document_id"]])).encode(), PURPOSE, "en", False, (second,))
    manifest = json.loads((folder / "package.json").read_text())
    (folder / "package.json").write_text(json.dumps({**manifest, "name": "store-views"}))
    made = make_package.make_folder(folder, tmp / "store-views" / "built")
    response = httpx.post(server_base + "/api/packages/inbox", content=made.archive.read_bytes(),
                          headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}",
                                   "Content-Type": "application/zip"}, timeout=60)
    assert response.status_code == 200, response.text
    return response.json()["inbox"]["id"], [first, second]


@pytest.mark.asyncio(loop_scope="session")
async def test_the_chapters_move_between_views_and_one_element_stays_lit_in_each(
        page_playwright, canonical_server, draft, tmp_path):  # noqa: F811
    from tests import dashboard_page
    server, _published, _checked, base, tmp = canonical_server
    await asyncio.to_thread(dashboard_page.build_bundle)
    inbox_id, (first, second) = await asyncio.to_thread(sent, base, draft, tmp)
    async with page_playwright as playwright, httpx.AsyncClient(base_url=base, timeout=60) as http:
        browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.goto(base)
        for _ in range(200):
            if server.ui_clients.pages() and server.voice_clients:
                break
            await asyncio.sleep(.05)
        opened = await http.post(f"/api/packages/inbox/{inbox_id}/open", headers={"Origin": base})
        assert opened.status_code == 200 and not opened.json()["problem"], opened.text
        frame = page.locator("#package-view iframe")
        await async_api.expect(frame).to_have_attribute("data-view", first["document_id"])
        assert await frame.get_attribute("sandbox") == "allow-scripts", "the sandbox is kept"
        now = page.locator(".package-view-now")
        await async_api.expect(now).to_contain_text("View 1 of 2")

        # More names the scenario the story walks through every view.
        head = page.locator(".package-head")
        await head.get_by_role("button", name="More", exact=True).click()
        await async_api.expect(page.locator(".package-scenario")).to_contain_text(SCENARIO)
        await page.keyboard.press("Escape")

        # He clicks the store in the first view: it is lit there.
        inside = page.frame_locator("#package-view iframe")
        store = inside.locator('[data-declaration="DocumentStore"]').first
        await store.wait_for()
        await store.click(position={"x": 4, "y": 4})
        await inside.locator('.jarvis-story-lit[data-declaration="DocumentStore"]').wait_for(
            state="attached")

        # The chapters name each slide's view and which views show it; the
        # second chapter opens the second view, where the store is lit too.
        await head.get_by_role("button", name="Chapters", exact=True).click()
        chapters = page.locator(".package-chapters button")
        assert await chapters.nth(1).locator(".package-chapter-view").get_attribute(
            "data-view") == second["document_id"]
        await async_api.expect(chapters.nth(1).locator(".package-chapter-lit")).to_contain_text(
            "DocumentStore")
        await chapters.nth(1).click()
        await async_api.expect(frame).to_have_attribute("data-view", second["document_id"])
        prefix = language_projection.view_prefix(second["document_id"])
        assert prefix + "index.html" in (await frame.get_attribute("src"))
        await async_api.expect(now).to_contain_text("View 2 of 2")
        source = page.locator(".package-canonical a").first
        assert prefix + "source.yaml" in (await source.get_attribute("href")), \
            "the source link follows the view shown"
        await inside.locator('.jarvis-story-lit[data-declaration="DocumentStore"]').wait_for(
            state="attached")
        await browser.close()
