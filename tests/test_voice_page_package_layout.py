"""The presentation first: an architecture package with everything it can
carry (its story's controls, its source, checker evidence and explanation,
long questions with their context, his approval and the vote) gives its own
page the whole width of the view below its icon border, at the checker's
window sizes and at a laptop's. There is no side column: every question is
reached through its card (Questions, then Next through them all), the
explanation opens as a card centred over the view and scrolls within it,
and with nothing open nothing covers the page. A package once drew its page
an inch high under its chrome, and later beside a column that took a third
of its width; this holds it."""
import asyncio
import json

import httpx
import pytest

from jarvis import decision_check
from tests.package_cards import CARD, QUESTIONS
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_brain_page_parity import wired  # noqa: F401  (a fixture)
from tests.test_language_documents import draft, installed_kit  # noqa: F401  (fixtures)
from tests.test_voice_page_architecture_walkthrough import STORY, sent
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_language_documents import canonical_server  # noqa: F401

pytestmark = pytest.mark.page

# As long as a story allows (package_story: a context at most 200 characters).
LONG = ("Choosing either route starts language-design work. Each new word still needs "
        "individual approval; this answer approves no vocabulary and no implementation of it.")[:200]
EXPLANATION = "# The document store\n\n" + "\n\n".join(
    f"## Part {i}\n\nThe store keeps part {i} of every revision, and says so here at length "
    "so the explanation is longer than its card." for i in range(30))
# Tim's own windows beside the checker's.
WINDOWS = [w for w, _frame in decision_check.SIZES] + [(1440, 900), (1280, 720), (1024, 700)]
# The page's share of the view's height (the border and the architecture
# links above it), and the most its sides may differ from the
# view's own inner edges (the view's 16 px padding each side, and a pixel).
HEIGHT_SHARE, PADDING, PX = 0.6, 16, 1.5


def long_story() -> dict:
    story = json.loads(json.dumps(STORY))
    for q in story["questions"]:
        q["context"] = LONG
    return story


def inside(box, outer) -> bool:
    return (box["x"] >= outer["x"] - PX and box["y"] >= outer["y"] - PX
            and box["x"] + box["width"] <= outer["x"] + outer["width"] + PX
            and box["y"] + box["height"] <= outer["y"] + outer["height"] + PX)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_presentation_fills_the_width_and_every_question_is_reached_by_its_card(
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
            await page.locator(QUESTIONS).wait_for()

            # Nothing open: no column, no dim, no card; the page has the
            # view's whole width below the border.
            assert await page.locator(".package-side, .package-focus").count() == 0, window
            frame = await page.locator("#package-view iframe").bounding_box()
            whole = await view.bounding_box()
            border = await page.locator("#package-view .package-border").bounding_box()
            assert abs(frame["x"] - (whole["x"] + PADDING)) <= PX, (window, frame, whole)
            assert abs(frame["width"] - (whole["width"] - 2 * PADDING)) <= PX, (window, frame, whole)
            assert abs(frame["width"] - border["width"]) <= PX, (window, frame, border)
            assert frame["y"] >= border["y"] + border["height"] - PX, (window, frame, border)
            assert frame["height"] >= HEIGHT_SHARE * whole["height"], (window, frame, whole)

            # Every question through its card: Questions opens the first he
            # has not answered, Next steps through them all, Back returns,
            # and each card is whole inside the view.
            await page.locator(QUESTIONS).click()
            card = page.locator(CARD)
            await card.wait_for()
            await async_api.expect(card).to_be_focused()
            position = card.locator(".package-focus-position")
            total = int((await position.inner_text()).rsplit(" ", 1)[1])
            seen = []
            for n in range(1, total + 1):
                await async_api.expect(position).to_have_text(f"Question {n} of {total}")
                asked = card.locator("[data-question], .package-vote").first
                seen.append(await asked.get_attribute("data-question") or "vote")
                assert await card.locator("h3").first.is_visible(), (window, n)
                assert inside(await card.bounding_box(), whole), (window, n, await card.bounding_box(), whole)
                if n < total:
                    await card.get_by_role("button", name="Next", exact=True).click()
            assert seen[0] == "approve-architecture", f"his approval first: {seen}"
            assert set(seen) >= {"approve-architecture", "ownership-clear"}, seen
            await card.get_by_role("button", name="Back", exact=True).click()
            await async_api.expect(position).to_have_text(f"Question {total - 1} of {total}")
            # Next after the last closes it; the page is uncovered again.
            await card.get_by_role("button", name="Next", exact=True).click()
            await card.get_by_role("button", name="Next", exact=True).click()
            await async_api.expect(page.locator(".package-focus")).to_have_count(0)
            assert await page.locator("#package-view iframe").bounding_box() == frame, window

            # The explanation: a card centred over the view, scrolling within
            # itself; Escape closes it and the page is as it was.
            await page.get_by_role("button", name="Read the explanation").click()
            layer = page.locator(".package-focus[data-kind='explanation']")
            await layer.wait_for()
            await page.locator("#package-explanation h3").first.wait_for()
            box = await card.bounding_box()
            assert inside(box, whole), (window, box, whole)
            assert abs((box["x"] + box["width"] / 2) - (whole["x"] + whole["width"] / 2)) <= 2 + PX, (window, box)
            scroll = await card.evaluate("e => [e.scrollHeight, e.clientHeight, e.scrollWidth, e.clientWidth]")
            assert scroll[0] > scroll[1], f"the explanation scrolls within its card: {scroll}"
            assert scroll[2] <= scroll[3] + 1, f"never sideways: {scroll}"
            await page.keyboard.press("Escape")
            await async_api.expect(layer).to_have_count(0)
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth")
            await context.close()
        await browser.close()
