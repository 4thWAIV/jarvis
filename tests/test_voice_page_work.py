"""The Work tab and a work item's detail.

The right panel's Work tab (the tab that was Workflows) lists each work
item with its title, its state and one useful update, an open decision's
question first. Clicking a row opens the item in the centre area while the
list stays in view; its pieces say exactly who is on each; a decision is
answered by posting the option clicked; a "work" hint makes the list read
the record again; Close goes back to Conversation. The Workflows view is
still there, under the Work list, and still opens a workflow.

Items are listed under their area, or their source folder's name; an item
blocked or waiting on him, or with a piece that is, carries a mark in its
row. The detail shows each piece in dependency order with its latest note,
the agent's own worker label beside its owner, what it waits for and its
evidence (a URL as a link, a commit or path with Copy).

Headless Chromium on the built page, with /api/work*, /api/workflows* and
both sockets replaced by Playwright.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice
from tests.test_voice_page_workflows import _Server as _Workflows
from tests.test_voice_page_workflows import _Sessions

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

# Where the list's and the detail's pictures go, for a person to look at.
SHOTS = Path(os.environ.get("JARVIS_WORK_SHOTS") or tempfile.gettempdir())


def _piece(key, title, state, who, **more):
    return {"key": key, "title": title, "state": state, "who": who, "note": "", "note_at": None,
            "worker": "", "after": [], "waits_for": [], "evidence": [], **more}


def _item(deciding=True, quiet=False):
    return {
        "id": "work-1", "title": "Speech migration", "source_kind": "folder",
        "source_path": "/Users/someone/code/speech", "source": "present",
        "state": "waiting_on_tim" if deciding else "working", "reported_state": "working",
        "quiet": quiet, "latest_update": "Engine wired.",
        "headline": "Which engine is the default?" if deciding else "Engine wired.",
        "revision": 3, "created_at": 1_000_000.0, "updated_at": 9_999_999_999.0,
        "area": "Voice", "blocked_pieces": 1, "waiting_pieces": 0,
        "pieces": [_piece("engine", "Wire the engine", "verifying",
                          {"kind": "agent", "ref": "s-1", "name": "Builder"},
                          note="Engine wired; checking it.", note_at=9_999_999_000.0,
                          worker="engine build agent",
                          evidence=[{"kind": "commit", "value": "abc1234"},
                                    {"kind": "url", "value": "https://example.com/pr/7"}]),
                   _piece("review", "Review it", "blocked", {"kind": "tim", "ref": None, "name": None},
                          note="Needs the engine first.", note_at=9_999_999_100.0,
                          after=["engine"], waits_for=["engine"])],
        "decisions": ([{"key": "engine", "question": "Which engine is the default?",
                        "options": ["Kroko", "Apple"], "recommendation": "Kroko",
                        "opened_at": 9_999_999_000.0}] if deciding else []),
    }


def _other():
    """An item with no area and nothing marked, grouped by its folder."""
    item = _item(deciding=False)
    return {**item, "id": "work-2", "title": "Captions", "area": "", "state": "planned",
            "headline": "Planned.", "source_path": "/Users/someone/code/captions",
            "blocked_pieces": 0, "pieces": [], "decisions": []}


def _list(deciding, quiet):
    return {"items": [_item(deciding, quiet), _other()],
            "groups": [{"grouped_by": "area", "name": "Voice", "label": "Voice", "ids": ["work-1"]},
                       {"grouped_by": "folder", "name": "/Users/someone/code/captions",
                        "label": "captions", "ids": ["work-2"]}]}


class _Work:
    """The Work routes, faked: what the page asked for and posted."""

    def __init__(self):
        self.deciding = True
        self.quiet = False
        self.lists = 0
        self.posted = []

    async def __call__(self, route):
        request = route.request
        path = request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if request.method == "POST":
            self.posted.append((path, json.loads(request.post_data or "{}")))
            await route.fulfill(json={"item": _item(self.deciding)})
        elif path == "api/work":
            self.lists += 1
            await route.fulfill(json=_list(self.deciding, self.quiet))
        else:
            await route.fulfill(json={"item": _item(self.deciding), "events": [
                {"item_id": "work-1", "kind": "update", "summary": "Engine wired.", "detail": {},
                 "at": 9_999_999_000.0, "seq": 2}]})


@pytest.mark.asyncio(loop_scope="session")
async def test_the_work_tab_lists_opens_answers_and_closes_and_keeps_workflows(page_playwright, page_url):
    work, sessions = _Work(), _Sessions()
    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.route("**/api/work", work)
        await page.route("**/api/work/**", work)
        await page.route("**/api/workflows**", _Workflows())
        await page.goto(page_url)

        await page.click(".right-tabs [role=tab][title=Work]")
        row = page.locator(".work-row[data-work-id=work-1]")
        await row.wait_for(timeout=10000)
        assert await row.get_attribute("data-state") == "waiting_on_tim"
        await async_api.expect(row.locator(".work-row-title")).to_have_text("Speech migration")
        await async_api.expect(row.locator(".work-state")).to_have_text("Waiting on you")
        # The open decision takes priority over the latest report.
        await async_api.expect(row.locator(".work-update")).to_have_text(
            "Decision: Which engine is the default?")
        # The Workflows view is still in this tab, under the Work list.
        await page.locator(".work-panel .workflow-row[data-workflow-id=wf-1]").wait_for(timeout=10000)
        assert await page.locator(".work-panel .work-section-head").first.text_content() == "Workflows"

        # Grouped: its area heads it, and an item with none is under its folder's name.
        heads = await page.locator(".work-panel .work-group-head").all_text_contents()
        assert heads == ["Voice", "captions"], heads
        # A piece is blocked: the row carries the mark and counts it.
        assert await row.get_attribute("data-mark") == "blocked"
        await async_api.expect(row.locator(".work-mark.mark-blocked")).to_have_count(1)
        await async_api.expect(row.locator(".work-marks")).to_have_text("Pieces blocked: 1")
        other = page.locator(".work-row[data-work-id=work-2]")
        assert await other.get_attribute("data-mark") is None
        await async_api.expect(other.locator(".work-marks")).to_have_count(0)
        await page.locator(".work-panel").screenshot(path=str(SHOTS / "work-list.png"))

        await row.click()
        await async_api.expect(page.locator("#work-detail .work-detail-name")).to_have_text(
            "Speech migration", timeout=10000)
        tabs = await page.evaluate("""() => ({
          centre: document.querySelector('.centre-tabs [aria-selected=true]').textContent,
          right: document.querySelector('.right-tabs [aria-selected=true]').title})""")
        assert tabs == {"centre": "Speech migration", "right": "Work"}, tabs
        assert await page.locator(".work-row[data-work-id=work-1]").is_visible(), "the list stays in view"
        who = await page.locator("#work-detail .work-piece-who").all_text_contents()
        assert who == ["Agent Builder via “engine build agent”", "You"], who
        worker = page.locator("#work-detail .work-piece-worker")
        assert "own words" in await worker.get_attribute("title")
        await async_api.expect(page.locator("#work-detail .work-piece .work-state")).to_have_text(
            ["Verifying", "Blocked"])
        notes = await page.locator("#work-detail .work-piece-note").all_text_contents()
        assert [n.endswith(said) for n, said in zip(notes, ("Engine wired; checking it.",
                                                          "Needs the engine first."))] == [True, True], notes
        await async_api.expect(page.locator("#work-detail .work-piece-after")).to_have_text(
            "Waits for: Wire the engine")
        assert await page.locator("#work-detail .work-piece[data-key=review]").get_attribute(
            "data-mark") == "blocked"
        link = page.locator("#work-detail .work-evidence a")
        assert (await link.get_attribute("href"), await link.get_attribute("rel")) == (
            "https://example.com/pr/7", "noopener noreferrer")
        await async_api.expect(page.locator("#work-detail .work-evidence code")).to_have_text("abc1234")
        await async_api.expect(page.locator("#work-detail .work-evidence-copy")).to_have_count(1)
        await page.locator("#work-detail").screenshot(path=str(SHOTS / "work-detail.png"))
        assert "/Users/someone/code/speech" in await page.locator("#work-detail .work-source").text_content()

        await page.click("#work-detail .workflow-option:text('Apple')")
        for _ in range(50):
            if work.posted:
                break
            await asyncio.sleep(0.1)
        assert work.posted == [("api/work/work-1/decisions/engine", {"answer": "Apple"})]

        # The record changed: the hint makes the list read it again.
        work.deciding = False
        before = work.lists
        sessions.ws.send(json.dumps({"type": "work"}))
        await page.wait_for_function(
            "() => document.querySelector('.work-row[data-work-id=work-1]').dataset.state === 'working'", timeout=10000)
        assert work.lists > before
        await async_api.expect(page.locator(".work-row[data-work-id=work-1] .work-update")).to_have_text("Engine wired.")
        # Gone quiet: the pill still says its state, and the update says it is quiet.
        work.quiet = True
        sessions.ws.send(json.dumps({"type": "work"}))
        await async_api.expect(page.locator(".work-row[data-work-id=work-1] .work-update")).to_have_text(
            "No recent update", timeout=10000)
        await async_api.expect(page.locator(".work-row[data-work-id=work-1] .work-state")).to_have_text("Working")

        await page.click("#work-detail .workflow-close")
        await page.wait_for_selector("#work-detail", state="detached")
        assert await page.locator("#conversation").count() == 1

        # The workflow monitor is still reachable from the same tab.
        await page.click(".work-panel .workflow-row[data-workflow-id=wf-1]")
        await async_api.expect(page.locator("#workflow-detail .workflow-detail-name")).to_have_text(
            "Speech migration", timeout=10000)
        await browser.close()
