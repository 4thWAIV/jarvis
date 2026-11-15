"""The document first: an architecture package with everything around it
open (its story's controls, its source, checker evidence and explanation,
long questions with their context, his approval and the vote) still gives
its own page most of the view, at the checker's window sizes and at a
laptop's. The questions and the explanation sit beside the page at every
width, scrolling within their own space; nothing
around the page may squeeze it. A package once drew its page an inch high
under its chrome; this holds it."""
import asyncio
import json

import httpx
import pytest

from jarvis import decision_check
from tests.test_brain_page_parity import wired  # noqa: F401  (a fixture)
from tests.test_language_documents import draft, installed_kit  # noqa: F401  (fixtures)
from tests.test_voice_page_architecture_walkthrough import STORY, sent
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_language_documents import canonical_server  # noqa: F401
from tests.voice_page_input import show_package_bar

pytestmark = pytest.mark.page

# As long as a story allows (package_story: a context at most 200 characters).
LONG = ("Choosing either route starts language-design work. Each new word still needs "
        "individual approval; this answer approves no vocabulary and no implementation of it.")[:200]
EXPLANATION = "# The document store\n\n" + "\n\n".join(
    f"## Part {i}\n\nThe store keeps part {i} of every revision, and says so here at length "
    "so the explanation is longer than any side column." for i in range(30))
# Tim's own windows beside the checker's.
WINDOWS = [w for w, _frame in decision_check.SIZES] + [(1440, 900), (1280, 720), (1024, 700)]
# The page's share of the view: most of its height, and beside a side column
# most of its width.
HEIGHT_SHARE, WIDTH_SHARE = 0.6, 0.55


def long_story() -> dict:
    story = json.loads(json.dumps(STORY))
    for q in story["questions"]:
        q["context"] = LONG
    return story


@pytest.mark.asyncio(loop_scope="session")
async def test_the_document_keeps_most_of_the_view_with_everything_open(
        page_playwright, canonical_server, draft, tmp_path, monkeypatch):  # noqa: F811
    from tests import dashboard_page
    from tests import test_voice_page_architecture_walkthrough as walkthrough
    server, _published, _checked, base, tmp = canonical_server
    monkeypatch.setattr(walkthrough, "STORY", long_story())
    await asyncio.to_thread(dashboard_page.build_bundle)
    inbox = await asyncio.to_thread(sent, base, draft, tmp, "layout-one", EXPLANATION, True)
    async with page_playwright as playwright, httpx.AsyncClient(base_url=base, timeout=60) as http:
        browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        for window in WINDOWS:
            context = await browser.new_context(viewport={"width": window[0], "height": window[1]})
            page = await context.new_page()
            await page.add_init_script(FAKE_TRANSCRIPTION)
            await page.goto(base)
            for _ in range(100):
                if server.ui_clients.pages():
                    break
                await asyncio.sleep(.05)
            opened = await http.post(f"/api/packages/inbox/{inbox}/open", headers={"Origin": base})
            assert opened.status_code == 200 and not opened.json()["problem"], opened.text
            view = page.locator("#package-view")
            await view.wait_for()
            await page.locator('.package-asks [data-kind="approval"]').wait_for()
            await show_package_bar(page)  # a faded bar lets the pointer through
            await page.get_by_role("button", name="Read the explanation").click()
            await page.locator("#package-explanation h3").first.wait_for()
            frame = await page.locator("#package-view iframe").bounding_box()
            whole = await view.bounding_box()
            side = await page.locator(".package-side").bounding_box()
            assert frame["height"] >= HEIGHT_SHARE * whole["height"], (window, frame, whole)
            assert side["x"] >= frame["x"] + frame["width"] - 1, "the side column is beside the page"
            assert frame["width"] >= WIDTH_SHARE * whole["width"], (window, frame, whole)
            # The questions stay reachable: the side column scrolls, the view does not.
            assert await page.locator('.package-asks [data-question]').first.is_visible()
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth")
            await context.close()
        await browser.close()
