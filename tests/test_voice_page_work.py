"""The Work tab and a work item's detail.

The right panel's Work tab (the tab that was Workflows) lists each work
item with its title, its state and one useful update, an open decision's
question first. Clicking a row opens the item in the centre area while the
list stays in view; its pieces say exactly who is on each; a decision is
answered by posting the option clicked; a changed item arrives as one
frame, which the page applies in place with no read (only that row is drawn
again), reading the list in full only on a reconnect or when a frame's
revision shows one was missed; Close goes back to Conversation. The Workflows view is
still there, under the Work list, and still opens a workflow.

Items are listed under their area, or their source folder's name; an item
blocked or waiting on him, or with a piece that is, carries a mark in its
row, with how many decisions wait on him and the first thing holding it up.
Closed or long-done items are under History, still openable. The detail has
tabs (Overview, Decisions, Blocking, Pieces, Reports) under a "Decision
needed" banner that opens Decisions; Decisions answers open ones, opens a
decision's package and keeps the answered and withdrawn ones; Pieces shows
each in dependency order with its latest note, the agent's own worker label
beside its owner, what it waits for and its evidence.

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
from tests.fixtures.waiting import until_async
from tests.test_voice_page_workflows import _Server as _Workflows
from tests.test_voice_page_workflows import _Sessions

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

# Where the list's and the detail's pictures go, for a person to look at.
SHOTS = Path(os.environ.get("JARVIS_WORK_SHOTS") or tempfile.gettempdir())


def _piece(key, title, state, who, **more):
    return {"key": key, "title": title, "state": state, "who": who, "note": "", "note_at": None,
            "worker": "", "after": [], "waits_for": [], "evidence": [], "quiet_at": None, **more}


DECISION = {"key": "engine", "question": "Which engine is the default?", "options": ["Kroko", "Apple"],
            "recommendation": "Kroko", "opened_at": 9_999_999_000.0, "package": "engine-deck"}


def _item(deciding=True, quiet=False):
    blockers = [{"kind": "piece_blocked", "key": "review", "title": "Review it",
                 "text": "Needs the engine first.", "waits_for": []},
                {"kind": "piece_after", "key": "review", "title": "Review it",
                 "text": "Needs the engine first.", "waits_for": ["engine"]}]
    if deciding:
        blockers.insert(0, {"kind": "decision", "key": "engine", "title": DECISION["question"],
                            "text": "", "waits_for": []})
    return {
        "id": "work-1", "title": "Speech migration", "source_kind": "folder", "projects": [],
        "source_path": "/Users/someone/code/speech", "source": "present",
        "state": "waiting_on_tim" if deciding else "working", "reported_state": "working",
        "quiet": quiet, "latest_update": "Engine wired.",
        "headline": DECISION["question"] if deciding else "Engine wired.",
        "revision": 3, "created_at": 1_000_000.0, "updated_at": 9_999_999_999.0,
        "area": "Voice", "blocked_pieces": 1, "waiting_pieces": 0, "closed_at": None, "history": False,
        "pieces": [_piece("engine", "Wire the engine", "verifying",
                          {"kind": "agent", "ref": "s-1", "name": "Builder"},
                          note="Engine wired; checking it.", note_at=9_999_999_000.0, quiet_at=9_999_999_600.0,
                          worker="engine build agent",
                          evidence=[{"kind": "commit", "value": "abc1234"},
                                    {"kind": "url", "value": "https://example.com/pr/7"}]),
                   _piece("review", "Review it", "blocked", {"kind": "tim", "ref": None, "name": None},
                          note="Needs the engine first.", note_at=9_999_999_100.0,
                          after=["engine"], waits_for=["engine"])],
        "decisions": [DECISION] if deciding else [],
        "answered": [{"key": "colour", "question": "Which colour?", "answer": "Asked before the designs.",
                      "where": "withdrawn", "answered_at": 9_999_998_000.0, "package": ""},
                     {"key": "tone", "question": "Which tone?", "answer": "dry", "where": "page",
                      "answered_at": 9_999_997_000.0, "package": ""}],
        "blockers": blockers,
        "quiet_at": None if deciding else (1_000_000.0 if quiet else 9_999_999_999.0 + 600),
    }


def _other():
    """An item with no area and nothing marked, grouped by its folder."""
    item = _item(deciding=False)
    return {**item, "id": "work-2", "title": "Captions", "area": "", "state": "planned",
            "headline": "Planned.", "source_path": "/Users/someone/code/captions",
            "blocked_pieces": 0, "pieces": [], "decisions": [], "answered": [], "blockers": []}


def _past():
    """An item closed a while ago: in History."""
    return {**_other(), "id": "work-3", "title": "Old logo", "state": "done", "headline": "Shipped.",
            "closed_at": 9_999_990_000.0, "history": True}


def _list(deciding, quiet):
    return {"items": [_item(deciding, quiet), _other()],
            "groups": [{"grouped_by": "area", "name": "Voice", "label": "Voice", "ids": ["work-1"]},
                       {"grouped_by": "folder", "name": "/Users/someone/code/captions",
                        "label": "captions", "ids": ["work-2"]}],
            "history": [_past()]}


def _frame(item, revision, kind="work_item"):
    """The server's frame for one changed item (work_view.py)."""
    listed = _list(True, False)
    return {"type": kind, "revision": revision, "item": item, "list": "items",
            "order": {"items": ["work-1", "work-2"], "history": ["work-3"]},
            "groups": listed["groups"]}


class _Work:
    """The Work and package routes, faked: what the page asked for and posted."""

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
            if path.startswith("api/packages/"):
                await route.fulfill(status=409, json={"error": "No JARVIS page is open to show it on."})
            else:
                await route.fulfill(json={"item": _item(self.deciding)})
        elif path == "api/work":
            self.lists += 1
            await route.fulfill(json={**_list(self.deciding, self.quiet),
                                      "revision": getattr(self, "revision", 0)})
        else:
            item = _past() if path.endswith("work-3") else _item(self.deciding)
            await route.fulfill(json={"item": item, "events": [
                {"item_id": item["id"], "kind": "update", "summary": "Engine wired.", "detail": {},
                 "at": 9_999_999_000.0, "seq": 2}]})


async def _posted(work, count):
    await until_async(lambda: len(work.posted) >= count)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_work_tab_lists_opens_answers_and_closes_and_keeps_workflows(page_playwright, page_url):
    work, sessions = _Work(), _Sessions()
    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.route("**/api/work", work)
        await page.route("**/api/work/**", work)
        await page.route("**/api/packages/*/page", work)
        await page.route("**/api/workflows**", _Workflows())
        await page.goto(page_url)

        await page.click(".right-tabs [role=tab][title=Work]")
        row = page.locator(".work-row[data-work-id=work-1]")
        await row.wait_for(timeout=10000)
        assert await row.get_attribute("data-state") == "waiting_on_tim"
        await async_api.expect(row.locator(".work-row-title")).to_have_text("Speech migration")
        await async_api.expect(row.locator(".work-state")).to_have_text("Waiting on you")
        # The open decision takes priority over the latest report, and is counted.
        await async_api.expect(row.locator(".work-update")).to_have_text(
            "Decision: Which engine is the default?")
        await async_api.expect(row.locator(".work-decision-count")).to_have_text("1")
        # The first thing holding it up besides the decision, and how many more.
        await async_api.expect(row.locator(".work-blocker-line")).to_have_text(
            "Blocked: Review it: Needs the engine first. · +1 more")
        # Grouped: its area heads it, and an item with none is under its folder's name.
        heads = await page.locator(".work-panel .work-group-head").all_text_contents()
        assert heads == ["Voice", "captions"], heads
        assert await row.get_attribute("data-mark") == "blocked"
        await async_api.expect(row.locator(".work-mark.mark-blocked")).to_have_count(1)
        other = page.locator(".work-row[data-work-id=work-2]")
        assert await other.get_attribute("data-mark") is None
        await async_api.expect(other.locator(".work-blocker-line")).to_have_count(0)
        # History is folded away until opened.
        await async_api.expect(page.locator(".work-row[data-work-id=work-3]")).to_have_count(0)
        # The Workflows view is still in this tab, under the Work list.
        await page.locator(".work-panel .workflow-row[data-workflow-id=wf-1]").wait_for(timeout=10000)
        assert await page.locator(".work-panel .work-section-head").first.text_content() == "Workflows"
        await page.locator(".work-panel").screenshot(path=str(SHOTS / "work-list.png"))

        await row.click()
        await async_api.expect(page.locator("#work-detail .work-detail-name")).to_have_text(
            "Speech migration", timeout=10000)
        tabs = await page.evaluate("""() => ({
          centre: document.querySelector('.centre-tabs [aria-selected=true]').textContent,
          right: document.querySelector('.right-tabs [aria-selected=true]').title})""")
        assert tabs == {"centre": "Speech migration", "right": "Work"}, tabs
        assert await page.locator(".work-row[data-work-id=work-1]").is_visible(), "the list stays in view"
        names = await page.locator("#work-detail .work-tabs [role=tab]").all_text_contents()
        assert names == ["Overview", "Decisions (1)", "Blocking (3)", "Pieces", "Reports"], names
        banner = page.locator("#work-detail .work-decision-banner")
        await async_api.expect(banner).to_contain_text("Decision needed (1)")
        await async_api.expect(banner).to_contain_text("Which engine is the default?")
        # Overview: the update, the source and who is on it.
        assert "/Users/someone/code/speech" in await page.locator("#work-detail .work-source").text_content()
        who = await page.locator("#work-detail .work-who li").all_text_contents()
        assert who == ["Agent Builder via “engine build agent”", "You"], who
        await page.locator("#work-detail").screenshot(path=str(SHOTS / "work-detail-overview.png"))

        # Blocking: everything holding it up, in order.
        await page.click("#work-detail .work-tabs [role=tab]:text('Blocking')")
        lines = await page.locator("#work-detail .work-blocker").all_text_contents()
        assert lines == ["Decision needed: Which engine is the default?",
                         "Blocked: Review it: Needs the engine first.",
                         "Review it waits for Wire the engine"], lines
        await page.locator("#work-detail").screenshot(path=str(SHOTS / "work-detail-blocking.png"))

        # The banner, on any tab, opens Decisions.
        await banner.click()
        await page.locator("#work-detail .workflow-decision[data-key=engine]").wait_for(timeout=15000)
        assert await page.locator("#work-detail .work-tabs [aria-selected=true]").text_content() == "Decisions (1)"
        answered = await page.locator("#work-detail .work-answered-answer").all_text_contents()
        assert answered[0].startswith("Withdrawn by the agent that asked it: Asked before the designs.")
        assert answered[1].startswith("You answered on the page: dry")
        await page.locator("#work-detail").screenshot(path=str(SHOTS / "work-detail-decisions.png"))
        # The decision's package opens through the server's one way; its refusal is shown.
        await page.click("#work-detail .work-decision-package button")
        await _posted(work, 1)
        assert work.posted == [("api/packages/engine-deck/page", {})]
        await async_api.expect(page.locator("#work-detail .work-decision-package")).to_contain_text(
            "No JARVIS page is open")
        await page.click("#work-detail .workflow-option:text('Apple')")
        await _posted(work, 2)
        assert work.posted[1] == ("api/work/work-1/decisions/engine", {"answer": "Apple"})

        # Pieces: in order, each with its note, worker, what it waits for and evidence.
        await page.click("#work-detail .work-tabs [role=tab]:text('Pieces')")
        await async_api.expect(page.locator("#work-detail .work-piece .work-state")).to_have_text(
            ["Ready for review", "Blocked"])
        worker = page.locator("#work-detail .work-piece-worker")
        assert "own words" in await worker.get_attribute("title")
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
        await page.locator("#work-detail").screenshot(path=str(SHOTS / "work-detail-pieces.png"))

        # The record changed: its frame changes its row in place, with no read;
        # the other row is not drawn again; no decision, no banner.
        work.deciding = False
        before = work.lists
        row_before = await page.query_selector(".work-row[data-work-id=work-1]")
        await page.evaluate("""() => {
          window.__otherRow = 0;
          new MutationObserver((m) => { window.__otherRow += m.length; }).observe(
            document.querySelector('.work-row[data-work-id=work-2]'),
            {subtree: true, childList: true, attributes: true, characterData: true});
        }""")
        sessions.ws.send(json.dumps(_frame(_item(False, False), 1)))
        await page.wait_for_function(
            "() => document.querySelector('.work-row[data-work-id=work-1]').dataset.state === 'activity_unknown'",
            timeout=10000)
        assert work.lists == before, "a frame is applied without reading the list"
        assert await row_before.evaluate("el => el.isConnected"), "the row changed in place"
        assert await page.evaluate("window.__otherRow") == 0, "only the changed row is drawn again"
        await async_api.expect(page.locator(".work-row[data-work-id=work-1] .work-update")).to_have_text(
            "Engine wired.")
        await async_api.expect(page.locator(".work-row[data-work-id=work-1] .work-decision-count")).to_have_count(0)
        await async_api.expect(page.locator("#work-detail .work-decision-banner")).to_have_count(0)
        # Gone quiet: the pill still says its state, and the update says it is quiet.
        work.quiet = True
        sessions.ws.send(json.dumps(_frame(_item(False, True), 2)))
        await async_api.expect(page.locator(".work-row[data-work-id=work-1] .work-update")).to_have_text(
            "No recent update", timeout=10000)
        await async_api.expect(page.locator(".work-row[data-work-id=work-1] .work-state")).to_have_text("Activity unconfirmed")
        assert work.lists == before
        # A frame from before the page's revision changes nothing.
        sessions.ws.send(json.dumps(_frame(_item(True, False), 2)))
        await asyncio.sleep(0.3)
        await async_api.expect(page.locator(".work-row[data-work-id=work-1] .work-update")).to_have_text(
            "No recent update")
        # A frame whose revision shows one was missed: the list is read in full.
        work.revision = 9                 # the server's view, as a read now finds it
        sessions.ws.send(json.dumps(_frame(_item(False, True), 9)))
        await until_async(lambda: work.lists > before)
        # A reconnect (the server's snapshot on every connect): read in full again.
        read = work.lists
        sessions.ws.send(json.dumps({"type": "snapshot"}))
        await until_async(lambda: work.lists > read)

        # History: opened, its item still opens in full.
        await page.click(".work-panel .work-history .workflows-done-toggle")
        past = page.locator(".work-row[data-work-id=work-3]")
        await past.wait_for(timeout=15000)
        await page.locator(".work-panel").screenshot(path=str(SHOTS / "work-list-history.png"))
        await past.click()
        await async_api.expect(page.locator("#work-detail .work-detail-name")).to_have_text("Old logo",
                                                                                          timeout=10000)
        await async_api.expect(page.locator("#work-detail .work-history-pill")).to_have_text("In History")

        await page.click("#work-detail .workflow-close")
        await page.wait_for_selector("#work-detail", state="detached")
        assert await page.locator("#conversation").count() == 1

        # The workflow monitor is still reachable from the same tab.
        await page.click(".work-panel .workflow-row[data-workflow-id=wf-1]")
        await async_api.expect(page.locator("#workflow-detail .workflow-detail-name")).to_have_text(
            "Speech migration", timeout=10000)
        assert errors == [], errors
        await browser.close()


LONG = "Design Work ownership and packaged communications"


def _review_item(item_id, state, approvable, quiet_at):
    """An item as the review flow and the list draw it."""
    return {**_item(deciding=False), "id": item_id, "title": LONG, "state": state,
            "quiet_at": quiet_at, "blockers": [], "blocked_pieces": 0, "approvable": approvable,
            "pieces": [_piece("design", "Design it", state, {"kind": "tim", "ref": None, "name": None},
                              quiet_at=quiet_at)]}


class _Review:
    """Two items: one paused and ready for review (approvable), one still
    working (not)."""

    def __init__(self):
        self.posted = []
        self.items = {"work-r": _review_item("work-r", "verifying", True, 1_000_000.0),
                      "work-w": _review_item("work-w", "working", False, 1_000_000.0)}

    async def __call__(self, route):
        request = route.request
        path = request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if request.method == "POST":
            self.posted.append((path, json.loads(request.post_data or "{}")))
            await route.fulfill(json={"item": self.items["work-r"]})
        elif path == "api/work":
            await route.fulfill(json={"items": list(self.items.values()), "history": [],
                                      "groups": [{"grouped_by": "area", "name": "Voice",
                                                  "label": "Voice", "ids": list(self.items)}]})
        else:
            item = self.items[path.rsplit("/", 1)[1]]
            await route.fulfill(json={"item": item, "events": [], "packages": []})


@pytest.mark.asyncio(loop_scope="session")
async def test_a_paused_row_keeps_its_title_and_approve_is_offered_only_when_it_can_be(
        page_playwright, page_url):
    work = _Review()
    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", _Sessions())
        await page.route("**/api/work", work)
        await page.route("**/api/work/**", work)
        await page.route("**/api/workflows**", _Workflows())
        await page.goto(page_url)

        await page.click(".right-tabs [role=tab][title=Work]")
        for item_id in ("work-r", "work-w"):
            row = page.locator(f".work-row[data-work-id={item_id}]")
            await row.wait_for(timeout=10000)
            # A long state beside it ("Ready for review", "Activity unconfirmed"):
            # the title still has its room, and a claim draws no activity.
            await async_api.expect(row.locator(".activity-mark")).to_have_count(0)
            sizes = await row.evaluate("""(r) => ({
              title: r.querySelector('.work-row-title').getBoundingClientRect().width,
              row: r.getBoundingClientRect().width,
              pausedInTitle: !!r.querySelector('.work-row-title .activity-mark')})""")
            assert not sizes["pausedInTitle"], sizes
            assert sizes["title"] >= 0.8 * sizes["row"], sizes
        await page.locator(".work-panel").screenshot(path=str(SHOTS / "work-list-paused.png"))

        # Still working: no Approve. Ready for review and nothing in the way: Approve.
        await page.click(".work-row[data-work-id=work-w]")
        await async_api.expect(page.locator("#work-detail .work-detail-name")).to_have_text(LONG, timeout=10000)
        await page.locator("#work-detail .work-review").wait_for(timeout=15000)
        await async_api.expect(page.locator("#work-detail .work-approve")).to_have_count(0)
        await page.click(".work-row[data-work-id=work-r]")
        approve = page.locator("#work-detail .work-approve")
        await approve.wait_for(timeout=10000)
        # A long title in the centre's tab gives way inside the tab row.
        tabs = await page.evaluate("""() => {
          const row = document.querySelector('.centre-tabs').getBoundingClientRect();
          return [...document.querySelectorAll('.centre-tabs .word-tab')].map(
            t => t.getBoundingClientRect().right <= row.right + 1)}""")
        assert all(tabs), tabs
        await approve.click()
        await _posted(work, 1)
        assert work.posted == [("api/work/work-r/review", {"action": "approve", "words": ""})]
        assert errors == [], errors
        await browser.close()


class _Needs(_Review):
    """One item stalled, one that needs his decision."""

    def __init__(self):
        super().__init__()
        stalled = _review_item("work-s", "activity_unknown", False, None)
        needing = _review_item("work-n", "waiting_on_tim", False, None)
        stalled.update(stalled=True, needs_you=[], title="Stalled one")
        needing.update(stalled=False, title="Needs him", decisions=[DECISION], headline=DECISION["question"],
                       needs_you=[{"kind": "decision", "key": "engine",
                                   "text": DECISION["question"]}])
        self.items = {"work-s": stalled, "work-n": needing}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_stalled_row_says_so_and_a_needed_one_names_the_act_and_its_place(
        page_playwright, page_url):
    work = _Needs()
    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", _Sessions())
        await page.route("**/api/work", work)
        await page.route("**/api/work/**", work)
        await page.route("**/api/workflows**", _Workflows())
        await page.goto(page_url)
        await page.click(".right-tabs [role=tab][title=Work]")
        stalled = page.locator(".work-row[data-work-id=work-s]")
        await stalled.wait_for(timeout=10000)
        await async_api.expect(stalled.locator(".work-state")).to_have_text("Stalled")
        await async_api.expect(stalled.locator(".work-update")).to_have_text(
            "Stalled: nobody is working on this")
        needed = page.locator(".work-row[data-work-id=work-n]")
        await async_api.expect(needed.locator(".work-update")).to_contain_text(
            "Which engine is the default?")
        await page.locator(".work-panel").screenshot(path=str(SHOTS / "work-list-needs.png"))
        await needed.click()
        need = page.locator("#work-detail .work-needs li[data-need=decision]")
        await async_api.expect(need).to_contain_text("on the Decisions tab", timeout=10000)
        await need.locator("button").click()
        await async_api.expect(page.locator("#work-detail .work-tabs [aria-selected=true]")).to_contain_text(
            "Decisions")
        await page.locator("#work-detail").screenshot(path=str(SHOTS / "work-detail-needs.png"))
        assert errors == [], errors
        await browser.close()


class _Live(_Review):
    """One item whose piece is on a subagent writing now."""

    def __init__(self):
        super().__init__()
        live = _review_item("work-l", "working", False, 9_999_999_999.0)
        live["title"] = "Release"
        live["pieces"] = [_piece("pr77", "Merge PR 77", "working",
                                 {"kind": "agent", "ref": "s-1/sub:aeb4b37bbf9ed2571",
                                  "name": "Release agent"},
                                 quiet_at=9_999_999_999.0,
                                 live={"name": "Release agent", "state": "working",
                                       "written": 9_999_999_900.0, "doing": "Using Bash",
                                       "active_within": 90.0})]
        live["live"] = live["pieces"][0]["live"]
        waiting = _review_item("work-b", "activity_unknown", False, None)
        waiting.update(title="Waits on CI", stalled=False, live={
            "name": "Release agent", "state": "waiting", "written": 9_999_999_000.0,
            "doing": "", "active_within": 90.0})
        self.items = {"work-l": live, "work-b": waiting}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_piece_on_a_subagent_writing_now_glows_and_says_what_it_does(
        page_playwright, page_url):
    work = _Live()
    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", _Sessions())
        await page.route("**/api/work", work)
        await page.route("**/api/work/**", work)
        await page.route("**/api/workflows**", _Workflows())
        await page.goto(page_url)
        await page.click(".right-tabs [role=tab][title=Work]")
        row = page.locator(".work-row[data-work-id=work-l]")
        await row.wait_for(timeout=10000)
        await async_api.expect(row.locator(".work-state")).to_have_text("Working")
        assert await row.get_attribute("data-activity") == "working"
        await async_api.expect(row.locator(".work-update")).to_have_text(
            "Release agent is working now · Using Bash")
        waits = page.locator(".work-row[data-work-id=work-b]")
        await async_api.expect(waits.locator(".work-state")).to_have_text(
            "Waiting on Release agent's background work")
        await async_api.expect(waits.locator(".work-update")).to_have_text(
            "Release agent is waiting on its own background work")
        await row.click()
        await page.click("#work-detail .work-tabs [role=tab]:text('Pieces')")
        piece = page.locator("#work-detail .work-piece[data-key=pr77]")
        await async_api.expect(piece.locator(".work-piece-live")).to_have_text(
            "Release agent is working now · Using Bash", timeout=10000)
        assert await piece.get_attribute("data-activity") == "working"
        await async_api.expect(piece.locator(".work-activity-reason")).to_have_count(0)
        await page.locator("#work-detail").screenshot(path=str(SHOTS / "work-detail-live.png"))
        assert errors == [], errors
        await browser.close()
