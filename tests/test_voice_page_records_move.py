"""The records move, by what the user sees (RecordsMove.tsx): while the
server moves the records it shows "Moving your records into the new
engine", the step, the time left and that stopping is safe, over the whole
workspace; once moved, the page loads again on the records; a move that
failed says so and why.

Headless Chromium on the built page; /api/records/move is answered here in
the shapes records_move.status returns.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_navigation import _Voice
from tests.test_voice_page_workflows import _Sessions

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

STEPS = ["space", "copy", "check", "check_back"]


def _status(state, step=None, done=(), left=None, reason=None):
    return {"state": state, "step": step, "steps": STEPS, "done_steps": list(done),
            "left": left, "elapsed": 10.0, "reason": reason, "plan": None, "started_at": 0,
            "safe_to_stop": ""}


class _Move:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = 0

    async def __call__(self, route):
        self.asked += 1
        body = self.answers[0] if len(self.answers) == 1 else self.answers.pop(0)
        await route.fulfill(content_type="application/json", body=json.dumps(body))


async def _open(p, page_url, move):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", _Voice())
    await page.route_web_socket("**/ws/sessions", _Sessions())
    await page.route("**/api/records/move", move)
    await page.goto(page_url)
    return browser, page


@pytest.mark.asyncio
async def test_the_move_is_shown_step_by_step_then_the_workspace(page_url):
    move = _Move(_status("moving", "check", done=["space", "copy"], left=95),
                 _status("starting", None, done=STEPS),
                 _status("ready"))
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, move)
        try:
            shown = page.locator("#records-move")
            await async_api.expect(shown.locator("h1")).to_have_text("Moving your records into the new engine")
            await async_api.expect(shown.locator(".records-move-step")).to_have_text("Step 2 of 3: Checking")
            await async_api.expect(shown.locator(".records-move-left")).to_have_text("About 1 minute left")
            await async_api.expect(shown.locator(".records-move-safe")).to_contain_text("safe to stop JARVIS")
            await async_api.expect(shown.locator("li.done")).to_have_text("Copying")
            # It covers the whole page: nothing of the workspace is in reach.
            box = await shown.bounding_box()
            assert box["width"] == 1440 and box["height"] == 900
            # Moved and checked: the page loads again on the records, the
            # workspace with nothing left of the move.
            await async_api.expect(page.locator("#records-move")).to_have_count(0, timeout=15000)
            await page.wait_for_selector(".right-tabs [role=tab]", timeout=15000)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_a_failed_move_says_so_and_why(page_url):
    move = _Move(_status("failed", reason="the move needs about 4.2 GB of free disk and 1.0 GB is free"))
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, move)
        try:
            shown = page.locator("#records-move.failed")
            await async_api.expect(shown.locator("h1")).to_have_text("JARVIS could not start on its records")
            await async_api.expect(shown.locator(".records-move-reason")).to_contain_text("4.2 GB of free disk")
            await async_api.expect(shown).to_contain_text("jarvis.db is untouched")
            await async_api.expect(shown).to_contain_text("docs/whats-new.md")
        finally:
            await browser.close()
