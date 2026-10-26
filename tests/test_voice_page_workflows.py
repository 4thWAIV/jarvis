"""The Workflows view and a workflow's detail.

The right panel's Workflows tab lists every workflow as a compact summary row
at the panel's fixed width: its light red orb, its name, a segment per phase
with the current one marked, tasks done and an icon per kind of open item.
Both sidebars share one width that only shrinks, and never cover the centre. Its detail's Overview shows the orb large and the phases as a track.
Clicking a row opens the workflow in the centre area, and its Decisions tab
answers a decision by posting the option clicked. A "workflows" hint on
/ws/sessions makes the list read the record again.

Headless Chromium on the built page, with /api/workflows* and both sockets
replaced by Playwright.
"""

import asyncio
import json

import pytest

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


def _workflow(state="needs_you"):
    return {
        "id": "wf-1", "name": "Speech migration", "project": "jarvis", "state": state,
        "phase": "deepgram-engine", "phases_done": 1, "phases_total": 5,
        "tasks_done": 6, "tasks_total": 10, "open_waits": [],
        "open_decisions": ["engine"] if state == "needs_you" else [], "open_blockers": [],
        "agents": ["s1"], "started_at": 1_000_000.0, "last_event_at": 9_999_999_999.0,
        "last_summary": "Engine wired up.",
        "items": [{"slug": "mic-recorder", "kind": "phase", "phase": None,
                   "title": "Mic recorder", "state": "done", "done": None, "total": None},
                  {"slug": "deepgram-engine", "kind": "phase", "phase": None,
                   "title": "Deepgram engine", "state": "running", "done": None, "total": None}],
        "open": ([{"kind": "decision", "key": "engine", "summary": "Which engine is the default?",
                   "detail": {"options": ["Deepgram", "Apple"], "recommendation": "Deepgram"},
                   "opened_at": 9_999_999_000.0}] if state == "needs_you" else []),
    }


class _Server:
    """The workflow routes, faked: what the page asked for and posted."""

    def __init__(self):
        self.state = "needs_you"
        self.lists = 0
        self.posted = []

    async def __call__(self, route):
        request = route.request
        path = request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if request.method == "POST":
            self.posted.append((path, json.loads(request.post_data or "{}")))
            await route.fulfill(json={"workflow": _workflow(self.state)})
        elif path == "api/workflows":
            self.lists += 1
            await route.fulfill(json={"workflows": [_workflow(self.state)]})
        elif path.endswith("/events"):
            await route.fulfill(json={"events": []})
        elif path.endswith("/transcript"):
            await route.fulfill(json={"agents": []})
        else:
            await route.fulfill(json={"workflow": _workflow(self.state)})


class _Sessions:
    def __init__(self):
        self.ws = None

    def __call__(self, ws):
        self.ws = ws


@pytest.mark.asyncio
async def test_a_workflow_row_opens_its_detail_and_a_decision_is_answered(page_url):
    server, sessions = _Server(), _Sessions()
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.route("**/api/workflows**", server)
        await page.goto(page_url)

        await page.click(".right-tabs [role=tab][title=Workflows]")
        row = page.locator(".workflow-row[data-workflow-id=wf-1]")
        await row.wait_for(timeout=10000)
        text = await row.text_content()
        for part in ("Speech migration", "6/10"):
            assert part in text, text
        assert await row.get_attribute("data-state") == "needs_you"
        got = await row.evaluate("""r => ({
          orb: r.querySelector('.workflow-orb canvas').getBoundingClientRect().width,
          segments: [...r.querySelectorAll('.phase-segment')].map(s => [s.dataset.phase, s.className]),
          decisions: r.querySelector('.open-icon.open-decision').textContent})""")
        assert got["orb"] >= 28, got
        assert [p for p, _ in got["segments"]] == ["mic-recorder", "deepgram-engine"], got
        assert "at-done" in got["segments"][0][1] and "at-current" in got["segments"][1][1], got
        assert got["decisions"] == "1", got

        await row.click()
        await page.wait_for_selector("#workflow-detail .workflow-detail-name")
        assert await page.text_content(".workflow-detail-name") == "Speech migration"
        overview = await page.evaluate("""() => ({
          orb: document.querySelector('.workflow-detail-head .workflow-orb canvas').getBoundingClientRect().width,
          steps: document.querySelectorAll('.workflow-overview .phase-track .phase-segment').length,
          rings: document.querySelectorAll('.workflow-overview .workflow-ring').length})""")
        assert overview["orb"] >= 64 and overview["steps"] == 2 and overview["rings"] == 2, overview
        selected = await page.evaluate(
            "() => document.querySelector('.centre-tabs [aria-selected=true]').textContent")
        assert selected == "Workflow"

        await page.click(".workflow-tabs [role=tab][title=Decisions]")
        await page.click(".workflow-option:text('Apple')")
        for _ in range(50):
            if server.posted:
                break
            await asyncio.sleep(0.1)
        assert server.posted == [("api/workflows/wf-1/decisions/engine", {"answer": "Apple"})]

        # The record changed: the hint makes the list read it again.
        server.state = "running"
        before = server.lists
        sessions.ws.send(json.dumps({"type": "workflows"}))
        await page.wait_for_function(
            "() => document.querySelector('.workflow-row').dataset.state === 'running'",
            timeout=10000)
        assert server.lists > before

        await page.click(".workflow-close")
        await page.wait_for_selector("#workflow-detail", state="detached")
        assert await page.locator("#conversation").count() == 1
        await browser.close()


def _entry(offset, role, text, tool=None):
    return {"offset": offset, "at": 1_792_886_400.0, "role": role, "text": text, "tool": tool}


@pytest.mark.asyncio
async def test_the_transcript_tab_shows_each_kind_of_entry_and_loads_earlier(page_url):
    server, sessions = _Server(), _Sessions()
    asked = []

    async def workflow_transcript(route):
        await route.fulfill(json={"agents": [{"session_id": "s1", "name": "guard", "state": "idle"}]})

    async def session_transcript(route):
        before = route.request.url.split("before=")[1] if "before=" in route.request.url else None
        asked.append(before)
        if before is None:
            await route.fulfill(json={"session_id": "s1", "name": "guard", "state": "idle",
                                      "earlier": 100, "entries": [
                _entry(100, "jarvis", "Use Postgres."), _entry(200, "agent", "On it."),
                _entry(200, "tool", "psql -c 'select 1'", "Bash"), _entry(300, "result", "1 row")]})
        else:
            await route.fulfill(json={"session_id": "s1", "name": "guard", "state": "idle",
                                      "earlier": None, "entries": [_entry(0, "you", "Start the build.")]})

    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.route("**/api/workflows**", server)
        await page.route("**/api/workflows/*/transcript", workflow_transcript)
        await page.route("**/api/sessions/*/transcript**", session_transcript)
        await page.goto(page_url)

        await page.click(".right-tabs [role=tab][title=Workflows]")
        await page.click(".workflow-row[data-workflow-id=wf-1]")
        await page.click(".workflow-tabs [role=tab][title=Transcript]")
        await page.wait_for_selector(".transcript-entry.role-result")
        roles = await page.evaluate("""() => [...document.querySelectorAll('.transcript-entry')]
          .map(e => [e.className.replace('transcript-entry role-', ''), e.querySelector('pre').textContent])""")
        assert roles == [["jarvis", "Use Postgres."], ["agent", "On it."],
                         ["tool", "psql -c 'select 1'"], ["result", "1 row"]], roles
        tool_label = await page.evaluate(
            "() => document.querySelector('.transcript-entry.role-tool .transcript-role').firstChild.textContent")
        assert tool_label == "Bash"
        colours = await page.evaluate("""() => ['jarvis','tool','result'].map(r =>
          getComputedStyle(document.querySelector('.transcript-entry.role-' + r)).borderLeftColor)""")
        assert len(set(colours)) == 3, colours

        await page.click(".transcript-earlier")
        await page.wait_for_selector(".transcript-entry.role-you")
        first = await page.evaluate(
            "() => document.querySelector('.transcript-entry pre').textContent")
        assert first == "Start the build." and "100" in asked
        assert await page.locator(".transcript-earlier").count() == 0
        await browser.close()


def _finished(wid, name, state="succeeded", dismissed=False, dismiss_at=None):
    import time as _t
    w = _workflow("running")
    w.update(id=wid, name=name, state=state, phases_done=2, phases_total=2,
             tasks_done=10, tasks_total=10, finished_at=_t.time() - 5, dismissed=dismissed,
             dismiss_at=None if dismissed else (dismiss_at or _t.time() + 3600))
    for item in w["items"]:
        item["state"] = "done"
    return w


@pytest.mark.asyncio
async def test_a_finished_workflow_lingers_is_dismissed_and_is_listed_as_done(page_url):
    """Finished, it stays in view with its outcome and an ×; dismissed, it
    leaves the list at once and is listed under Done; one nobody dismisses
    leaves on its own at its dismiss_at, without a hint."""
    import time as _t
    sessions = _Sessions()
    state = {"list": [_finished("wf-a", "Transcript reader"),
                      _finished("wf-b", "Panel width"),
                      _finished("wf-c", "Old build", state="failed", dismissed=True)],
             "posted": []}

    async def api(route):
        request = route.request
        path = request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if request.method == "POST":
            state["posted"].append(path)
            wid = path.split("/")[2]
            for w in state["list"]:
                if w["id"] == wid:
                    w.update(dismissed=True, dismiss_at=None)
            await route.fulfill(json={"workflow": next(w for w in state["list"] if w["id"] == wid)})
        elif path == "api/workflows":
            # wf-b's time runs from the page's first read: 8 s, then gone.
            if not state.get("read"):
                state["read"] = True
                state["list"][1]["dismiss_at"] = _t.time() + 8
            # Past its time it reads as dismissed, as the server reads it.
            for w in state["list"]:
                if w["dismiss_at"] is not None and _t.time() >= w["dismiss_at"]:
                    w.update(dismissed=True, dismiss_at=None)
            await route.fulfill(json={"workflows": state["list"]})
        else:
            wid = path.split("/")[2]
            await route.fulfill(json={"workflow": next(w for w in state["list"] if w["id"] == wid)})

    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.route("**/api/workflows**", api)
        await page.goto(page_url)
        await page.click(".right-tabs [role=tab][title=Workflows]")

        main = ".workflows-list:not(.done) .workflow-row"
        await page.wait_for_selector(f"{main}[data-workflow-id=wf-a]")
        row = page.locator(f"{main}[data-workflow-id=wf-a]")
        assert "finished" in await row.get_attribute("class")
        assert (await page.text_content(f"{main}[data-workflow-id=wf-a] .workflow-outcome")) == "Finished"
        assert await page.locator(f"{main}[data-workflow-id=wf-c]").count() == 0, \
            "a dismissed one is not in the main list"
        assert (await page.text_content(".workflows-done-toggle")).strip().endswith("Done (1)")

        # Nobody dismisses wf-b: it leaves by itself at its dismiss_at, no hint sent.
        await page.wait_for_selector(f"{main}[data-workflow-id=wf-b]", state="detached", timeout=20000)

        # He dismisses wf-a: gone at once, and the dismissal is posted.
        await page.click(".workflow-item:has([data-workflow-id=wf-a]) .workflow-dismiss")
        await page.wait_for_selector(f"{main}[data-workflow-id=wf-a]", state="detached", timeout=5000)
        assert "api/workflows/wf-a/dismiss" in state["posted"]

        await page.click(".workflows-done-toggle")
        done = await page.evaluate("""() => [...document.querySelectorAll('.workflow-done-row')]
          .map(r => [r.dataset.workflowId, r.querySelector('.workflow-dismissed-mark').textContent])""")
        assert sorted(done) == [["wf-a", "Dismissed"], ["wf-b", "Dismissed"], ["wf-c", "Dismissed"]], done
        assert (await page.text_content(".workflows-done-toggle")).strip().endswith("Done (3)")

        # A dismissed one still opens, marked Dismissed and without a Dismiss button.
        await page.click(".workflow-done-row[data-workflow-id=wf-c]")
        await page.wait_for_selector("#workflow-detail .workflow-detail-dismissed")
        assert await page.locator(".workflow-detail-dismiss").count() == 0
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [1440, 1200, 900])
async def test_both_sidebars_match_never_grow_and_never_cover_the_centre(page_url, width):
    """One width for both sidebars, at most 230px and shrinking with the
    window; the centre area always sits between them; a workflow row never
    runs past its panel."""
    server, sessions = _Server(), _Sessions()
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": width, "height": 860})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.route("**/api/workflows**", server)
        await page.goto(page_url)
        await page.click(".right-tabs [role=tab][title=Workflows]")
        await page.wait_for_selector(".workflow-row")
        got = await page.evaluate("""() => {
          const r = s => document.querySelector(s).getBoundingClientRect();
          const left = r('#left-column'), right = r('#right-column'), centre = r('#conversation');
          const panel = r('.right-panel');
          const rows = [...document.querySelectorAll('.workflow-item')].map(e => e.getBoundingClientRect());
          const tabs = r('.right-tabs [role=tab]'), menu = r('#btn-menu');
          return {left: left.width, right: right.width, overlapLeft: centre.left < left.right,
                  overlapRight: centre.right > right.left,
                  rowsInside: rows.every(b => b.left >= panel.left - 0.5 && b.right <= panel.right + 0.5),
                  tabsClearOfMenu: tabs.right <= menu.left + 0.5};
        }""")
        assert abs(got["left"] - got["right"]) < 0.5, got
        assert got["left"] <= 230.5 and got["left"] <= width * 0.18 + 0.5, got
        assert not got["overlapLeft"] and not got["overlapRight"], got
        assert got["rowsInside"] and got["tabsClearOfMenu"], got
        await browser.close()
