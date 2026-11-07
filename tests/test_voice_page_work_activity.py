"""Live activity on the nodes of tracked work (nodeActivity.tsx).

A Work row, a Work piece and a workflow's phase and task each show where
work is happening, all from the record: working (in a working state and its
`quiet_at` still ahead) has a glow travelling along its edge; paused (its
`quiet_at` passed) is dimmed and still and marked "Paused"; blocked is still
red, waiting on him still amber, done still with a check. Each report that
arrives while he looks, read after a `work` or `workflows` hint, flashes its
node once; nothing flashes for what was already there. A working node goes
paused at its `quiet_at` with no hint. The "waits for" line of a piece flows
while the piece it waits for is working. Reduced motion and Steady keep the
looks and drop the motion and the flash.

Headless Chromium on the built page, with the routes and both sockets
replaced by Playwright. With JARVIS_WORK_MOTION_FRAMES set to a folder, it
also saves frames of each look in motion there, for the presentation.
"""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice
from tests.test_voice_page_workflows import _Sessions

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

FRAMES = os.environ.get("JARVIS_WORK_MOTION_FRAMES")

# Records every flash (Element.animate) with the node it was on.
FLASH_SPY = """
window.__flashes = [];
const animate = Element.prototype.animate;
Element.prototype.animate = function (...args) {
  window.__flashes.push(this.dataset.key || this.dataset.workId || this.dataset.task
                        || this.className);
  return animate.apply(this, args);
};
"""


def _piece(key, title, state, quiet_at=None, note_at=None, **more):
    return {"key": key, "title": title, "state": state, "who": {"kind": "tim", "ref": None, "name": None},
            "note": f"{title}: latest.", "note_at": note_at, "worker": "", "after": [], "waits_for": [],
            "evidence": [], "quiet_at": quiet_at, **more}


def _item(id_, title, state, quiet_at=None, updated_at=None, pieces=(), **more):
    return {"id": id_, "title": title, "source_kind": "folder", "source_path": f"/Users/someone/code/{id_}",
            "source": "present", "state": state, "reported_state": state,
            "quiet": quiet_at is not None and quiet_at < time.time(), "latest_update": f"{title}: going.",
            "headline": f"{title}: going.", "revision": 2, "created_at": 1_000_000.0,
            "updated_at": updated_at or 1_000_100.0, "area": "Voice", "blocked_pieces": 0,
            "waiting_pieces": 0, "pieces": list(pieces), "decisions": [], "quiet_at": quiet_at, **more}


class _Work:
    """The Work record as the page reads it; `heard` moves a report on."""

    def __init__(self):
        now = time.time()
        self.start = now
        self.engine_heard = now - 30
        self.engine_quiet = now + 600

    def items(self):
        now = self.start
        pieces = [
            _piece("engine", "Wire the engine", "working", self.engine_quiet, self.engine_heard),
            _piece("docs", "Write the docs", "working", now - 1, now - 700),
            _piece("review", "Review it", "waiting_on_tim", note_at=now - 50,
                   after=["engine"], waits_for=["engine"]),
            _piece("ship", "Ship it", "blocked", note_at=now - 60),
            _piece("spec", "Spec it", "done", note_at=now - 4000),
        ]
        return [
            _item("work-1", "Speech migration", "working", self.engine_quiet, self.engine_heard, pieces,
                  blocked_pieces=1, waiting_pieces=1),
            _item("work-2", "Captions", "working", now - 1, now - 700),
            _item("work-3", "Rebrand", "done"),
            _item("work-4", "Release", "blocked"),
            _item("work-5", "Pricing", "waiting_on_tim"),
        ]

    async def __call__(self, route):
        path = route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        items = self.items()
        if path == "api/work":
            await route.fulfill(json={"items": items, "groups": [
                {"grouped_by": "area", "name": "Voice", "label": "Voice", "ids": [i["id"] for i in items]}]})
        else:
            item = next(i for i in items if path.endswith(i["id"]))
            await route.fulfill(json={"item": item, "events": []})


class _Flow:
    """One workflow with a running task heard from now, a quiet one and a
    finished one."""

    def __init__(self):
        now = time.time()
        self.start = now
        self.build_heard = now - 20

    def workflow(self):
        now = self.start
        item = lambda slug, kind, state, heard=None, quiet=None, phase=None: {  # noqa: E731
            "slug": slug, "kind": kind, "phase": phase, "title": slug.replace("-", " ").title(),
            "state": state, "done": None, "total": None, "heard_at": heard, "quiet_at": quiet}
        return {
            "id": "wf-1", "name": "Speech migration", "project": "jarvis", "state": "running",
            "phase": "engine", "phases_done": 1, "phases_total": 2, "tasks_done": 1, "tasks_total": 3,
            "open_waits": [], "open_decisions": [], "open_blockers": [], "agents": [],
            "started_at": now - 5000, "last_event_at": self.build_heard, "last_summary": "Going.",
            "finished_at": None, "dismissed": False, "dismiss_at": None,
            "items": [item("setup", "phase", "done", now - 4000),
                      item("engine", "phase", "running", self.build_heard, self.build_heard + 600),
                      item("plan-it", "task", "done", now - 4000, phase="setup"),
                      item("build-it", "task", "running", self.build_heard, self.build_heard + 600, "engine"),
                      item("test-it", "task", "running", now - 900, now - 300, "engine")],
            "open": [],
        }

    async def __call__(self, route):
        path = route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if path == "api/workflows":
            await route.fulfill(json={"workflows": [self.workflow()]})
        elif path.endswith("/events"):
            await route.fulfill(json={"events": []})
        elif path.endswith("/transcript"):
            await route.fulfill(json={"agents": []})
        else:
            await route.fulfill(json={"workflow": self.workflow(), "subagents": []})


async def _animation(page, selector, pseudo):
    return await page.evaluate(
        "([s, p]) => { const el = document.querySelector(s); return el ? getComputedStyle(el, p).animationName : null; }",
        [selector, pseudo])


async def _frames(page, selector, name, count=12, every=0.12):
    """Frames of one part of the page in motion, for the presentation."""
    if not FRAMES:
        return
    folder = Path(FRAMES) / name
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(count):
        await page.locator(selector).screenshot(path=str(folder / f"{n:03}.png"), animations="allow")
        await asyncio.sleep(every)


@pytest.mark.asyncio(loop_scope="session")
async def test_work_nodes_show_where_work_moves_and_where_it_has_paused(page_playwright, page_url):
    work, flow, sessions = _Work(), _Flow(), _Sessions()
    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.add_init_script(FLASH_SPY)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.route("**/api/work", work)
        await page.route("**/api/work/**", work)
        await page.route("**/api/workflows**", flow)
        await page.goto(page_url)

        # ── the Work tab's rows ────────────────────────────────────────
        await page.click(".right-tabs [role=tab][title=Work]")
        row = ".work-row[data-work-id=work-1]"
        await page.locator(row).wait_for(timeout=10000)
        looks = await page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.work-row')]
            .map(r => [r.dataset.workId, r.dataset.activity ?? null]))""")
        assert looks == {"work-1": "working", "work-2": "paused", "work-3": "done",
                         "work-4": "blocked", "work-5": "waiting"}, looks
        assert await _animation(page, row, "::after") == "activity-travel"
        assert await _animation(page, ".work-row[data-work-id=work-2]", "::after") in ("none", None, "")
        await async_api.expect(page.locator(".work-row[data-work-id=work-2] .activity-paused")).to_have_text("Paused")
        await async_api.expect(page.locator(".work-row[data-work-id=work-3] .activity-done")).to_have_text("✓")
        await async_api.expect(page.locator(".work-row[data-work-id=work-1] .activity-mark")).to_have_count(0)
        await _frames(page, ".work-panel .work-groups", "list")

        # ── the detail's pieces ────────────────────────────────────────
        await page.click(row)
        piece = "#work-detail .work-piece[data-key={}]"
        await page.locator(piece.format("engine")).wait_for(timeout=10000)
        looks = await page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#work-detail .work-piece')]
            .map(p => [p.dataset.key, p.dataset.activity ?? null]))""")
        assert looks == {"engine": "working", "docs": "paused", "review": "waiting", "ship": "blocked",
                         "spec": "done"}, looks
        assert await _animation(page, piece.format("engine"), "::before") == "activity-edge"
        await async_api.expect(page.locator(piece.format("docs") + " .activity-paused")).to_have_text("Paused")
        # The review waits for the working engine: its line flows.
        after = piece.format("review") + " .work-piece-after"
        assert await page.locator(after).get_attribute("data-flow") == "on"
        assert await _animation(page, after, None) == "activity-flow"
        # Nothing flashed for what was already there.
        assert await page.evaluate("window.__flashes") == []
        await _frames(page, "#work-detail .work-pieces", "pieces")

        # ── a report arrives: one flash, on its piece, row and update ──
        work.engine_heard = time.time()
        work.engine_quiet = time.time() + 2.5
        sessions.ws.send(json.dumps({"type": "work"}))
        try:
            await page.wait_for_function("() => window.__flashes.length >= 3", timeout=10000)
        except Exception:
            raise AssertionError(await page.evaluate("""() => ({f: window.__flashes,
              rows: [...document.querySelectorAll('.work-row')].map(r => r.dataset.activity),
              mo: matchMedia('(prefers-reduced-motion: reduce)').matches})"""))
        await asyncio.sleep(0.3)
        flashed = await page.evaluate("window.__flashes")
        assert sorted(flashed) == sorted(["engine", "work-1", "work-latest"]), flashed
        if FRAMES:
            await page.evaluate("window.__flashes = []")
            work.engine_heard = time.time()
            sessions.ws.send(json.dumps({"type": "work"}))
            await page.wait_for_function("() => window.__flashes.length >= 1", timeout=10000)
            await _frames(page, piece.format("engine"), "flash", count=10, every=0.06)

        # ── nothing more reported: it goes paused by itself, no hint ───
        await page.wait_for_function(
            f"() => document.querySelector('{piece.format('engine')}').dataset.activity === 'paused'",
            timeout=10000)
        assert await page.locator(after).get_attribute("data-flow") is None
        assert await page.locator(row).get_attribute("data-activity") == "paused"

        # ── Steady: the same looks, standing still, no flash ───────────
        work.engine_quiet = time.time() + 600
        work.engine_heard = time.time()
        await page.evaluate("document.documentElement.dataset.motion = 'steady'; window.__flashes = []")
        sessions.ws.send(json.dumps({"type": "work"}))
        await page.wait_for_function(
            f"() => document.querySelector('{piece.format('engine')}').dataset.activity === 'working'",
            timeout=10000)
        await asyncio.sleep(0.3)
        assert await page.evaluate("window.__flashes") == []
        assert await _animation(page, piece.format("engine"), "::before") == "none"
        assert await _animation(page, row, "::after") == "none"
        assert await _animation(page, after, None) == "none"
        await page.evaluate("delete document.documentElement.dataset.motion")

        # ── reduced motion: the same ───────────────────────────────────
        await page.emulate_media(reduced_motion="reduce")
        assert await _animation(page, row, "::after") == "none"
        assert await _animation(page, piece.format("engine"), "::before") == "none"
        await page.emulate_media(reduced_motion="no-preference")
        assert await _animation(page, row, "::after") == "activity-travel"

        # ── a workflow's phases and tasks ──────────────────────────────
        await page.click(".work-panel .workflow-row[data-workflow-id=wf-1]")
        await page.click(".workflow-tabs [role=tab][title=Tasks]")
        card = "#workflow-detail .task-card[data-task={}]"
        await page.locator(card.format("build-it")).wait_for(timeout=10000)
        looks = await page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#workflow-detail .task-card')]
            .map(c => [c.dataset.task, c.dataset.activity ?? null]))""")
        assert looks == {"plan-it": "done", "build-it": "working", "test-it": "paused"}, looks
        assert await _animation(page, card.format("build-it"), "::after") == "activity-travel"
        await async_api.expect(page.locator(card.format("test-it") + " .activity-paused")).to_have_text("Paused")
        await _frames(page, "#workflow-detail .task-board", "tasks")
        await page.evaluate("window.__flashes = []")
        flow.build_heard = time.time()
        sessions.ws.send(json.dumps({"type": "workflows"}))
        await page.wait_for_function("() => window.__flashes.includes('build-it')", timeout=10000)
        assert "test-it" not in await page.evaluate("window.__flashes")

        await page.click(".workflow-tabs [role=tab][title=Phases]")
        phase = "#workflow-detail .phase-step[data-phase=engine]"
        await page.locator(phase).wait_for(timeout=10000)
        assert await page.locator(phase).get_attribute("data-activity") == "working"
        assert await _animation(page, phase + " .phase-step-dot", "::after") == "activity-travel"
        await _frames(page, "#workflow-detail .phase-stepper", "phases")
        await browser.close()
