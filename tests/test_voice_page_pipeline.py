"""The pipeline and white attention, by what the page draws: the faint map
that is always there (orb rise, top pipeline, a rail per region, a branch
into each object on show), and a cue's whole life along it: white from the
orb, three pulses on registered edges only, arrival rings and the contour,
hold, fade and reset, with the map still there afterwards
(UiPipelineLayer.tsx, uiAttention.ts, uiPipeline.ts, orbSource.ts).

Headless Chromium on the built page. /api/team/network is a fixture (the
Network tab's tiles and the node's centre detail are the targets), and
/ws/voice is a stand-in server that welcomes the page and sends it
`ui_request` frames as JARVIS's backend would, recording its `ui_event`
and `ui_result` frames.
"""

import asyncio
import json
import re
import time

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_folders import _Api as _Folders
from tests.test_voice_page_network import MINI_ID, SIGNED_OUT, _Sessions, _Team
from tests.test_voice_page_workflows import _finished, _workflow

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

TILE_CPU = {"region": "right", "view": "right.network", "kind": "node", "key": MINI_ID, "part": "cpu"}
DETAIL_CPU = {"region": "main", "view": "main.network", "kind": "node", "key": MINI_ID, "part": "cpu"}


class _UiServer:
    """/ws/voice as JARVIS's backend: welcomes the page's ui_hello with
    epoch 1, sends ui_request frames, and keeps every ui_event and the one
    ui_result per request."""

    def __init__(self):
        self.ws = None
        self.client = None
        self.events: dict[str, list[dict]] = {}
        self.results: dict[str, dict] = {}
        self.n = 0

    def __call__(self, ws):
        self.ws = ws
        ws.on_message(self._got)

    def _got(self, raw):
        m = json.loads(raw)
        if m.get("type") == "ui_hello":
            self.client = m["clientId"]
            self.ws.send(json.dumps({"type": "ui_welcome", "clientId": self.client, "epoch": 1}))
        elif m.get("type") == "ui_event":
            self.events.setdefault(m["requestId"], []).append(m)
        elif m.get("type") == "ui_result":
            m["received"] = time.monotonic()
            self.results[m["requestId"]] = m

    def request(self, operation) -> tuple[str, float]:
        self.n += 1
        rid = f"r{self.n}"
        self.ws.send(json.dumps({
            "type": "ui_request", "protocol": 1, "requestId": rid, "clientId": self.client, "epoch": 1,
            "turn": "t1", "expectedNavigationRevision": None,
            "expiresAt": int(time.time() * 1000) + 15000, "operation": operation}))
        return rid, time.monotonic()

    def indicate(self, target, **extra):
        return self.request({"kind": "indicate", "target": target, **extra})

    async def result(self, rid, timeout=10.0):
        deadline = time.monotonic() + timeout
        while rid not in self.results:
            assert time.monotonic() < deadline, f"no ui_result for {rid}; events {self.events.get(rid)}"
            await asyncio.sleep(0.02)
        return self.results[rid]


async def _open(p, url, *, reduced=False, sessions=None, team=None, routes=(), network=True):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900},
                                  reduced_motion="reduce" if reduced else "no-preference")
    await page.add_init_script(FAKE_TRANSCRIPTION)
    ui = _UiServer()
    await page.route_web_socket("**/ws/voice", ui)
    await page.route_web_socket("**/ws/sessions", sessions or _Sessions())
    await _Team(team).install(page)
    for pattern, handler in routes:
        await page.route(pattern, handler)
    await page.goto(url)
    for _ in range(200):
        if ui.client:
            break
        await asyncio.sleep(0.05)
    assert ui.client, "the page never said ui_hello"
    if network:
        await page.click(".right-tabs [data-ui-tab=network]")
        await page.wait_for_selector(f".node-tile[data-node='{MINI_ID}']")
    return browser, page, ui


MAP = """() => [...document.querySelectorAll('#ui-pipeline [data-edge]')].map(p => ({
  id: p.dataset.edge, d: p.getAttribute('d')}))"""

DRAWN = """() => ({
  cues: document.querySelectorAll('[data-cue]').length,
  pulses: document.querySelectorAll('.ui-pulse').length,
  rings: document.querySelectorAll('.ui-ring').length,
  contour: document.querySelectorAll('.ui-contour').length,
  queued: document.querySelectorAll('[data-cue][data-queued="true"]').length,
  route: [...document.querySelectorAll('#ui-pipeline [data-cue] .ui-route')].map(p => p.getAttribute('d')),
  leaf: [...document.querySelectorAll('#ui-pipeline-over [data-cue] .ui-route')].map(p => p.getAttribute('d')),
  source: document.getElementById('ui-pipeline').dataset.source,
  waiting: document.getElementById('ui-pipeline').dataset.waiting,
  status: document.getElementById('ui-pipeline-status').textContent,
})"""


def _points(d):
    nums = [float(x) for x in re.findall(r"-?[\d.]+", d)]
    return list(zip(nums[0::2], nums[1::2]))


def _segments(d):
    pts = _points(d)
    return list(zip(pts, pts[1:]))


BEVEL = 6                                           # uiSignal.ts BEVEL_PX


def _on(seg, lines, tol=1.0):
    """Whether a segment lies within one of the map's lines, is one the map
    draws (a socket branch whose bevel takes its whole length), or is the
    45-degree bevel of a turn between two of them (both ends on the map,
    at most BEVEL along each axis)."""
    (ax, ay), (bx, by) = seg
    near = lambda p, q: abs(p[0] - q[0]) < tol and abs(p[1] - q[1]) < tol  # noqa: E731
    if any(near(seg[0], c) and near(seg[1], d) for c, d in lines):            # a segment the map draws
        return True
    if abs(ax - bx) > tol and abs(ay - by) > tol:
        return (abs(abs(ax - bx) - abs(ay - by)) < tol and abs(ax - bx) <= BEVEL + tol
                and _on(((ax, ay), (ax, ay)), lines, tol) and _on(((bx, by), (bx, by)), lines, tol))
    for (cx, cy), (dx, dy) in lines:
        if abs(ax - bx) < tol and abs(cx - dx) < tol and abs(ax - cx) < tol:          # both vertical
            lo, hi = sorted((cy, dy))
            if lo - tol <= min(ay, by) and max(ay, by) <= hi + tol:
                return True
        if abs(ay - by) < tol and abs(cy - dy) < tol and abs(ay - cy) < tol:          # both across
            lo, hi = sorted((cx, dx))
            if lo - tol <= min(ax, bx) and max(ax, bx) <= hi + tol:
                return True
    return False


@pytest.mark.asyncio
async def test_the_map_stays_and_a_cue_runs_its_whole_life_on_registered_edges_only(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        await page.wait_for_function(
            f"document.querySelector('#ui-pipeline [data-edge=\"socket:right.network|node|{MINI_ID}\"]')")
        idle = {e["id"]: e["d"] for e in await page.evaluate(MAP)}
        for edge in ("edge.orb-rise", "edge.trunk-left", "edge.trunk-main", "edge.trunk-right",
                     "edge.trunk-footer", "rail.left", "rail.main", "rail.right", "rail.footer"):
            assert edge in idle, (edge, sorted(idle))
        assert "rail.overlay" not in idle               # no modal open: no floating rail
        lines = [s for d in idle.values() for s in _segments(d)]

        rid, sent = ui.indicate(TILE_CPU)
        await page.wait_for_function("document.querySelector('#ui-pipeline [data-cue] .ui-route')")
        await page.wait_for_function("document.querySelectorAll('.ui-pulse').length >= 2")
        moving = await page.evaluate(DRAWN)
        assert moving["source"] == "attention", moving          # the orb is emitting white
        assert moving["contour"] == 0 and moving["rings"] == 0, moving   # nothing at the end yet
        # Every segment outside the object runs along the fixed map.
        for seg in _segments(moving["route"][0]):
            assert _on(seg, lines), (seg, moving["route"][0])
        assert moving["leaf"], "the part's leaf inside the tile"

        result = await ui.result(rid)
        took = result["received"] - sent
        assert result["status"] == "completed" and result["arrival"] == "animated", result
        assert 1.5 < took < 3.0, took                            # answered at arrival, not at scheduling
        arrived = await page.evaluate(DRAWN)
        assert arrived["contour"] >= 1, arrived
        assert "pointing from the orb to" in arrived["status"], arrived

        await page.wait_for_timeout(1500)                        # in the hold
        held = await page.evaluate(DRAWN)
        assert held["route"] and held["contour"] >= 1 and held["pulses"] == 0, held

        await page.wait_for_function("!document.querySelector('[data-cue]')", timeout=6000)
        after = await page.evaluate(DRAWN)
        assert after["source"] == "free" and after["waiting"] == "0", after
        assert {e["id"] for e in await page.evaluate(MAP)} == set(idle)   # the map is still there

        events = [e["event"] for e in ui.events[rid]]
        assert events == ["resolving", "emitting", "travelling", "arrived", "holding", "fading", "cleared"], events
        at = {e["event"]: e["at"] for e in ui.events[rid]}
        assert 1400 <= at["arrived"] - at["emitting"] <= 2000, at   # 1,620 ms on the shared schedule
        assert 4500 <= at["cleared"] - at["emitting"] <= 5800, at   # reset by 5,000 ms
        await browser.close()


LEAF_AND_CLOSE = """() => {
  const close = document.querySelector('#network-detail .workflow-close').getBoundingClientRect();
  const leaf = document.querySelector('#ui-pipeline-over [data-cue] .ui-route');
  return {close: [close.left, close.top, close.right, close.bottom], leaf: leaf && leaf.getAttribute('d')};
}"""


def _clear_of(info):
    left, top, right, bottom = info["close"]
    assert info["leaf"], info
    pts = _points(info["leaf"])
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        crosses = min(ax, bx) < right and max(ax, bx) > left and min(ay, by) < bottom and max(ay, by) > top
        assert not crosses, ((ax, ay), (bx, by), info["close"])
    return pts


@pytest.mark.asyncio
async def test_the_detail_branch_keeps_clear_of_its_close_button(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        # With the corridor the page keeps: the × is outside it.
        rid, _ = ui.indicate(DETAIL_CPU, reveal="navigate")
        assert (await ui.result(rid))["status"] == "completed"
        _clear_of(await page.evaluate(LEAF_AND_CLOSE))
        clear, _ = ui.request({"kind": "clear"})
        await ui.result(clear)
        await page.wait_for_function("!document.querySelector('[data-cue]')")
        # Without it, the × reaches the edge: the socket steps down past it.
        await page.add_style_tag(content="#network-detail { padding-right: 0 !important; }")
        rid, _ = ui.indicate(DETAIL_CPU)
        assert (await ui.result(rid))["status"] == "completed"
        info = await page.evaluate(LEAF_AND_CLOSE)
        pts = _clear_of(info)
        assert pts[0][1] > info["close"][3], (pts[0], info["close"])
        await browser.close()


class _LanguageSessions(_Sessions):
    """The sessions socket, its opening snapshot saying which language is in force."""

    def __init__(self, language):
        super().__init__()
        self.language = language

    def __call__(self, ws):
        super().__call__(ws)
        ws.send(json.dumps({"type": "snapshot", "sessions": [], "language": self.language}))


ARRIVAL = """() => {
  const contour = document.querySelector('#ui-pipeline-over .ui-contour, #ui-pipeline .ui-contour');
  const box = contour && contour.getBBox();
  const detail = document.querySelector('#network-detail').getBoundingClientRect();
  return {contour: box && [box.x, box.y, box.x + box.width, box.y + box.height],
          detail: [detail.left, detail.top, detail.right, detail.bottom],
          head: document.querySelector('#network-detail .subsystem-head-state').textContent};
}"""


SAID = {"en": ("Values from Team Server", "JARVIS is pointing from the orb to"),
        "es": ("Valores del servidor del equipo", "JARVIS está señalando desde el orbe")}


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "es"])
async def test_in_either_language_a_cue_reaches_its_part_of_the_reflowed_detail(page_url, language):
    """Spanish words are longer. Once they have reflowed the node's centre
    detail, a cue to one of its parts still leaves the orb along the fixed
    map, its leaf ends at that part and keeps clear of Close, the part lies
    inside the detail, and the status line says it in the language in
    force: the same in English and in Spanish."""
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, sessions=_LanguageSessions(language))
        await page.wait_for_function(f"document.documentElement.lang === '{'es-419' if language == 'es' else 'en-US'}'")
        rid, _ = ui.indicate(DETAIL_CPU, reveal="navigate")
        result = await ui.result(rid)
        assert result["status"] == "completed", result
        arrival = await page.evaluate(ARRIVAL)
        head, status = SAID[language]
        assert arrival["head"] == head, arrival
        lines = [s for e in await page.evaluate(MAP) for s in _segments(e["d"])]
        drawn = await page.evaluate(DRAWN)
        # Up the rise, along the top pipeline and down the main rail: the
        # last turn, into the detail's own socket, is the branch.
        for seg in _segments(drawn["route"][0])[:-1]:
            assert _on(seg, lines), (seg, drawn["route"][0])
        assert status in drawn["status"], drawn
        leaf = _clear_of(await page.evaluate(LEAF_AND_CLOSE))
        x0, y0, x1, y1 = arrival["contour"]
        left, top, right, bottom = arrival["detail"]
        assert left <= x0 and x1 <= right and top <= y0 and y1 <= bottom, arrival   # the part is in its detail
        end_x, end_y = leaf[-1]
        assert x0 - 1 <= end_x <= x1 + 1 and y0 - 1 <= end_y <= y1 + 1, (leaf[-1], arrival)
        await browser.close()


@pytest.mark.asyncio
async def test_reduced_motion_shows_the_whole_static_connection(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, reduced=True)
        rid, sent = ui.indicate(TILE_CPU)
        result = await ui.result(rid)
        assert result["status"] == "completed" and result["arrival"] == "static-reduced-motion", result
        assert result["received"] - sent < 1.0
        await page.wait_for_function("document.querySelector('.ui-contour')")
        still = await page.evaluate(DRAWN)
        assert still["route"] and still["leaf"], still           # source to part, connected
        assert still["pulses"] == 0 and still["rings"] == 0, still
        assert still["source"] == "free", still                  # no spin of the orb
        await browser.close()


@pytest.mark.asyncio
async def test_clearing_and_a_person_s_navigation_end_the_cue_and_leave_nothing(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        rid, _ = ui.indicate(TILE_CPU)
        await page.wait_for_function("document.querySelector('.ui-pulse')")
        clear, _ = ui.request({"kind": "clear"})
        assert (await ui.result(rid))["status"] == "cancelled"
        assert (await ui.result(clear))["status"] == "completed"
        await page.wait_for_function("!document.querySelector('[data-cue]')")
        assert (await page.evaluate(DRAWN))["source"] == "free"

        rid, _ = ui.indicate(TILE_CPU)
        await page.wait_for_function("document.querySelector('.ui-pulse')")
        await page.click(".right-tabs [aria-label='Agents']")   # he goes elsewhere
        assert (await ui.result(rid))["status"] == "cancelled"
        await page.wait_for_function("!document.querySelector('[data-cue]')")
        after = await page.evaluate(DRAWN)
        assert after["source"] == "free" and after["waiting"] == "0", after
        assert ui.events[rid][-1]["event"] == "cancelled"
        await browser.close()


@pytest.mark.asyncio
async def test_a_real_transmission_keeps_the_orb_and_attention_waits_then_says_busy(page_url):
    sessions = _Sessions()
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, sessions=sessions)
        sessions.ws.send(json.dumps({"type": "agent_message", "session_id": "1"}))
        await page.wait_for_function("document.querySelector('#ray-lane .ray')")
        rid, sent = ui.indicate(TILE_CPU)
        await page.wait_for_function("document.querySelector('[data-cue][data-queued=\"true\"]')")
        queued = await page.evaluate(DRAWN)
        assert queued["source"] == "transfer" and queued["waiting"] == "1", queued
        assert queued["pulses"] == 0, queued                     # nothing travels while it waits
        assert "Waiting for the orb" in queued["status"], queued
        result = await ui.result(rid)
        assert result["status"] == "busy" and result["reason"] == "source-busy", result
        assert 2.5 < result["received"] - sent < 4.5             # the contract's 3 s wait
        ray = await page.evaluate("""() => {
          const g = document.querySelector('#ray-lane .ray');
          return g && g.style.getPropertyValue('--ray-colour');
        }""")
        assert ray == "#fbbf24", ray                              # the message's own colour, untouched
        assert (await page.evaluate(DRAWN))["cues"] == 0
        await browser.close()


class _Workflows:
    """The workflow routes: wf-1 needs him; wf-2 has finished and lingers
    with its Dismiss ×."""

    async def __call__(self, route):
        path = route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if path == "api/workflows":
            await route.fulfill(json={"workflows": [_workflow(), _finished("wf-2", "Old build")]})
        elif path.endswith("/events"):
            await route.fulfill(json={"events": []})
        elif path.endswith("/transcript"):
            await route.fulfill(json={"agents": []})
        else:
            await route.fulfill(json={"workflow": _workflow() if "/wf-1" in path else _finished("wf-2", "Old build")})


# Parts that sit beside others in one row, on the side their leaf comes
# from: (the navigation that shows it, the part's target, its whole's
# selector, the part's selector). The finished workflow's × straddles its
# row's inner margin, so its leaf runs straight into it.
WORKFLOW = {"region": "main", "view": "main.workflow", "kind": "workflow", "key": "wf-1"}
BESIDE = [
    (("workflow.open", {"workflowId": "wf-1"}), {**WORKFLOW, "part": "tab.decisions"},
     "#workflow-detail", ".workflow-tabs [role=tab][data-ui-args*='decisions']"),
    (("main.tab.select", {"tab": "folders"}),
     {"region": "main", "view": "main.folders", "kind": "foldersList", "part": "registered"},
     ".folders-catalog", ".folder-tab[data-ui-args*='registered']"),
    (None, {"region": "left", "view": "left.fixed", "kind": "teamServer", "part": "lastWrite"},
     "#team-server-tile", "#team-server-tile .health-row:last-child"),
    (None, {"region": "main", "view": "main.notice", "kind": "teamReminder", "part": "signIn"},
     "#team-reminder", "#team-reminder .settings-btn.primary"),
    (None, {"region": "main", "view": "main.notice", "kind": "teamReminder", "part": "text"},
     "#team-reminder", "#team-reminder p"),
    (("right.tab.select", {"tab": "workflows"}),
     {"region": "right", "view": "right.workflows", "kind": "workflow", "key": "wf-2", "part": "dismiss"},
     ".workflow-row[data-workflow-id=wf-2]", ".workflow-item:has([data-workflow-id=wf-2]) .workflow-dismiss"),
]

# What a leaf inside an object may not cross: its controls and the lines of
# its text, outside the part it points at.
KEPT = """([whole, part]) => {
  const w = document.querySelector(whole), p = document.querySelector(part);
  const box = (r) => [r.left, r.top, r.right, r.bottom];
  const kept = [];
  w.querySelectorAll("button, a[href], input, select, textarea, summary, [role='button'], [role='tab'], [role='link']")
    .forEach((c) => { if (!p.contains(c)) kept.push(box(c.getBoundingClientRect())); });
  const walk = document.createTreeWalker(w, NodeFilter.SHOW_TEXT), range = document.createRange();
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (!n.textContent.trim() || p.contains(n)) continue;
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) if (r.width && r.height) kept.push(box(r));
  }
  return {kept, part: box(p.getBoundingClientRect())};
}"""


def _bevel_or_straight(seg, tol=0.6):
    (ax, ay), (bx, by) = seg
    dx, dy = abs(ax - bx), abs(ay - by)
    return dx < tol or dy < tol or (abs(dx - dy) < tol and dx <= BEVEL + tol)


def _inside(x, y, box, tol=0.5):
    left, top, right, bottom = box
    return left + tol < x < right - tol and top + tol < y < bottom - tol


@pytest.mark.asyncio
async def test_a_part_beside_others_in_its_row_is_reached_over_or_under_the_row(page_url):
    """The workflow's Decisions tab, a phase step's dot, the Folders tabs,
    the microphone's mute and off, the reminder's Sign in and a finished
    workflow's Dismiss each sit beside another part on the side their leaf
    comes from. Each is reached: along the fixed map to its object's
    socket, then a leaf over or under its row (or, for the ×, down the
    margin it straddles), every turn a 45-degree bevel, never across a
    control, another part or a line of text, ending at the part's top or
    bottom edge."""
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, reduced=True, team=SIGNED_OUT, network=False,
                                        routes=[("**/api/workflows**", _Workflows()),
                                                ("**/api/folders**", _Folders())])
        try:
            await page.wait_for_selector("#team-reminder")
            for show, target, whole, part in BESIDE:
                if show:
                    rid, _ = ui.request({"kind": "navigate", "commandId": show[0], "args": show[1]})
                    assert (await ui.result(rid))["status"] == "completed", show
                await page.wait_for_selector(part)
                rid, _ = ui.indicate(target, reveal="never")
                result = await ui.result(rid)
                assert result["status"] == "completed", (target, result)
                await page.wait_for_function("document.querySelector('.ui-contour')")
                drawn = await page.evaluate(DRAWN)
                lines = [s for e in await page.evaluate(MAP) for s in _segments(e["d"])]
                # Nothing leaves the registered map but its bevels.
                for seg in _segments(drawn["route"][0]):
                    assert _on(seg, lines), (target["part"], seg, drawn["route"][0],
                                             [e["id"] for e in await page.evaluate(MAP)])
                leaf = _points(drawn["leaf"][0])
                around = await page.evaluate(KEPT, [whole, part])
                for seg in zip(leaf, leaf[1:]):
                    assert _bevel_or_straight(seg), (target["part"], seg, leaf)
                    middle = ((seg[0][0] + seg[1][0]) / 2, (seg[0][1] + seg[1][1]) / 2)
                    for x, y in (seg[0], seg[1], middle):
                        for box in around["kept"]:
                            assert not _inside(x, y, box), (target["part"], (x, y), box, leaf)
                left, top, right, bottom = around["part"]
                (_, before_y), (end_x, end_y) = leaf[-2], leaf[-1]
                # It ends at the part: down or up into its top or bottom
                # edge, or (the ×) straight in where it straddles the margin.
                assert left - 4 <= end_x <= right + 4 and top - 4 <= end_y <= bottom + 4, \
                    (target["part"], leaf, around["part"])
                if target["part"] != "dismiss":
                    assert left <= end_x <= right and not top <= before_y <= bottom, \
                        (target["part"], leaf, around["part"])
                clear, _ = ui.request({"kind": "clear"})
                await ui.result(clear)
                await page.wait_for_function("!document.querySelector('[data-cue]')")
        finally:
            await browser.close()
