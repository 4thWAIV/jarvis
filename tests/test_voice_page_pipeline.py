"""The pipeline and white attention, by what the page draws: nothing at
rest (the map of orb rise, top pipeline, a rail per region and a branch
into each object is data only), and a cue's whole life drawn along its own
route alone: white from the orb, three pulses on registered edges only,
down the primary channel to the latest branch point, a mark on each box it
passes into in turn, arrival rings and the contour, hold, fade and reset,
with nothing left afterwards (UiPipelineLayer.tsx, uiAttention.ts,
uiPipeline.ts, orbSource.ts).

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
from tests.test_voice_page_centre_nav import _History
from tests.test_voice_page_folders import FOLDERS, _Api as _Folders
from tests.test_voice_page_network import MINI_ID, SIGNED_OUT, _Sessions, _Team
from tests.test_voice_page_workflows import _finished, _workflow

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

TILE_CPU = {"region": "right", "view": "right.network", "kind": "node", "key": MINI_ID, "part": "cpu"}
DETAIL_CPU = {"region": "main", "view": "main.network", "kind": "node", "key": MINI_ID, "part": "cpu"}
FOLDER_ID = FOLDERS[0]["id"]


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


async def _open(p, url, *, reduced=False, sessions=None, team=None, routes=(), network=True, size=(1440, 900)):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": size[0], "height": size[1]},
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


# Everything the pipeline's layers draw: at rest, nothing (its one filter
# definition draws nothing itself).
DRAWN_AT_ALL = """() => [...document.querySelectorAll('svg.ui-pipeline > :not(defs)')].map(e => e.outerHTML)"""

DRAWN = """() => ({
  cues: document.querySelectorAll('[data-cue]').length,
  pulses: document.querySelectorAll('.ui-pulse').length,
  rings: document.querySelectorAll('.ui-ring').length,
  contour: document.querySelectorAll('.ui-contour').length,
  queued: document.querySelectorAll('[data-cue][data-queued="true"]').length,
  edges: [...document.querySelectorAll('[data-cue]')].flatMap(g => g.dataset.edges.split(' ').filter(Boolean)),
  marks: [...document.querySelectorAll('#ui-pipeline [data-cue]')].flatMap(g => g.dataset.marks.split(' ').filter(Boolean)),
  marked: [...document.querySelectorAll('[data-mark]')].map(m => m.dataset.mark),
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


# The ids the fixed map's edges take (uiPipeline.ts): the declared trunk
# and rails, a rail's tap and branch into one object, and a part's leaf.
REGISTERED = re.compile(r"^(edge\.[a-z-]+|rail\.[a-z]+|rail\.[a-z]+:tap:.+|socket:.+|leaf:.+)$")


def _bevel_or_straight(seg, tol=0.6):
    (ax, ay), (bx, by) = seg
    dx, dy = abs(ax - bx), abs(ay - by)
    return dx < tol or dy < tol or (abs(dx - dy) < tol and dx <= BEVEL + tol)


def _on_the_map(drawn, what):
    """A cue's route runs on registered edges only, out of the orb: every
    edge it draws is one of the map's, the first its rise, and every
    segment is straight along one or a 45-degree bevel between two."""
    assert drawn["edges"] and drawn["edges"][0] == "edge.orb-rise", (what, drawn["edges"])
    for edge in drawn["edges"]:
        assert REGISTERED.match(edge), (what, edge, drawn["edges"])
    for seg in _segments(drawn["route"][0]):
        assert _bevel_or_straight(seg), (what, seg, drawn["route"][0])


async def _nothing_drawn(page):
    """Nothing of the pipeline is on the page: no map, no route, no ring."""
    await page.wait_for_function("!document.querySelector('svg.ui-pipeline > :not(defs)')", timeout=8000)
    assert await page.evaluate(DRAWN_AT_ALL) == []


@pytest.mark.asyncio
async def test_nothing_at_rest_and_a_cue_runs_its_whole_life_on_registered_edges_only(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        await page.wait_for_timeout(500)                         # the page settled, its tiles on show
        await _nothing_drawn(page)                               # no map at rest

        rid, sent = ui.indicate(TILE_CPU)
        await page.wait_for_function("document.querySelector('#ui-pipeline [data-cue] .ui-route')")
        await page.wait_for_function("document.querySelectorAll('.ui-pulse').length >= 2")
        moving = await page.evaluate(DRAWN)
        assert moving["source"] == "attention", moving          # the orb is emitting white
        assert moving["contour"] == 0 and moving["rings"] == 0, moving   # nothing at the end yet
        # Its own route alone, out of the orb, down the right rail, and in
        # from the rail straight to the part: no socket near the tile's top.
        _on_the_map(moving, "tile")
        tile = f"right.network|node|{MINI_ID}"
        for edge in ("edge.trunk-right", f"rail.right:tap:{tile}", f"leaf:{tile}:cpu"):
            assert edge in moving["edges"], (edge, moving["edges"])
        assert f"socket:{tile}" not in moving["edges"], moving["edges"]
        assert not any(e.startswith(("rail.left", "rail.main", "rail.footer")) for e in moving["edges"]), moving
        # The boxes on the way, outermost first: the panel, then the tile.
        assert moving["marks"] == ["right.panel", tile], moving["marks"]
        seen = []
        while len(seen) < 2:
            for mark in (await page.evaluate(DRAWN))["marked"]:
                if mark not in seen:
                    seen.append(mark)
            assert time.monotonic() - sent < 5, seen
            await asyncio.sleep(0.03)
        assert seen == ["right.panel", tile], seen               # marked in turn, the panel first

        result = await ui.result(rid)
        took = result["received"] - sent
        assert result["status"] == "completed" and result["arrival"] == "animated", result
        assert 2.6 < took < 4.6, took                            # answered at arrival, after both stops
        arrived = await page.evaluate(DRAWN)
        assert arrived["contour"] >= 1, arrived
        assert "pointing from the orb to" in arrived["status"], arrived

        await page.wait_for_timeout(1500)                        # in the hold
        held = await page.evaluate(DRAWN)
        assert held["route"] and held["contour"] >= 1 and held["pulses"] == 0, held

        await page.wait_for_function("!document.querySelector('[data-cue]')", timeout=8000)
        after = await page.evaluate(DRAWN)
        assert after["source"] == "free" and after["waiting"] == "0", after
        await _nothing_drawn(page)                               # nothing of it is left

        events = [e["event"] for e in ui.events[rid]]
        assert events == ["resolving", "emitting", "travelling", "arrived", "holding", "fading", "cleared"], events
        at = {e["event"]: e["at"] for e in ui.events[rid]}
        # 2,220 ms on the shared schedule, and 450 ms at each of two boxes.
        assert 2900 <= at["arrived"] - at["emitting"] <= 3700, at
        assert 6000 <= at["cleared"] - at["emitting"] <= 7400, at   # reset by 5,600 + 900 ms
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
        drawn = await page.evaluate(DRAWN)
        # Up the rise, along the top pipeline and down the main rail, then
        # the branch into the detail's own socket.
        _on_the_map(drawn, language)
        assert "edge.trunk-main" in drawn["edges"], drawn["edges"]
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
        await _nothing_drawn(page)

        rid, _ = ui.indicate(TILE_CPU)
        await page.wait_for_function("document.querySelector('.ui-pulse')")
        await page.click(".right-tabs [aria-label='Agents']")   # he goes elsewhere
        assert (await ui.result(rid))["status"] == "cancelled"
        await page.wait_for_function("!document.querySelector('[data-cue]')")
        after = await page.evaluate(DRAWN)
        assert after["source"] == "free" and after["waiting"] == "0", after
        assert ui.events[rid][-1]["event"] == "cancelled"
        await _nothing_drawn(page)
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
    (("main.tab.select", {"tab": "conversation"}), {"region": "main", "view": "main.conversation", "kind": "mic", "part": "mute"}, "#mic-control", "#btn-mute"),
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


def _inside(x, y, box, tol=0.5):
    left, top, right, bottom = box
    return left + tol < x < right - tol and top + tol < y < bottom - tol


@pytest.mark.asyncio
async def test_a_part_beside_others_in_its_row_is_reached_over_or_under_the_row(page_url):
    """The workflow's Decisions tab, the Folders tabs, the microphone's mute
    (under the row, in the lane kept below it; off, the row's last, is
    reached straight from its side), the reminder's
    Sign in and a finished
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
                # Nothing leaves the registered map but its bevels.
                _on_the_map(drawn, target["part"])
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


# Inside the workflow's detail, outside one step: its controls and lines of
# text, and the other steps.
AROUND_STEP = """(slug) => {
  const w = document.querySelector('#workflow-detail'), s = document.querySelector(`.phase-step[data-phase="${slug}"]`);
  const box = (r) => [r.left, r.top, r.right, r.bottom];
  const kept = [...document.querySelectorAll('.phase-step')].filter((o) => o !== s)
    .map((o) => box(o.getBoundingClientRect()));
  w.querySelectorAll("button, a[href], input, select, textarea, summary, [role='button'], [role='tab'], [role='link']")
    .forEach((c) => { if (!s.contains(c)) kept.push(box(c.getBoundingClientRect())); });
  const walk = document.createTreeWalker(w, NodeFilter.SHOW_TEXT), range = document.createRange();
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (!n.textContent.trim() || s.contains(n)) continue;
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) if (r.width && r.height) kept.push(box(r));
  }
  return {kept, step: box(s.getBoundingClientRect())};
}"""


@pytest.mark.asyncio
async def test_a_phase_step_left_of_the_last_is_reached_over_the_stepper(page_url):
    """A phase step's straight branch from the rail would cross the steps
    after it, and the next step's connector line lies over its dot. The
    connector ignores the pointer, and the branch leaves the rail level
    with the lane over the stepper, runs in it and drops into the step's
    top edge (or its dot's), every turn a 45-degree bevel, on the map,
    never across another step, a control or a line of text; the step, its
    dot and its count are all reached. The last step is still entered
    straight from the rail, level with its middle."""
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, reduced=True, team=SIGNED_OUT, network=False,
                                        routes=[("**/api/workflows**", _Workflows())])
        try:
            rid, _ = ui.request({"kind": "navigate", "commandId": "workflow.open",
                                 "args": {"workflowId": "wf-1", "tab": "phases"}})
            assert (await ui.result(rid))["status"] == "completed"
            await page.wait_for_selector(".phase-step[data-phase=deepgram-engine]")
            for slug, over in (("mic-recorder", True), ("deepgram-engine", False)):
                for part in (None, "state"):
                    target = {"region": "main", "view": "main.workflow", "kind": "workflowPhase",
                              "key": f"wf-1/{slug}", **({"part": part} if part else {})}
                    rid, _ = ui.indicate(target, reveal="never")
                    result = await ui.result(rid)
                    assert result["status"] == "completed", (target, result)
                    await page.wait_for_function("document.querySelector('.ui-contour')")
                    drawn = await page.evaluate(DRAWN)
                    _on_the_map(drawn, (slug, part))
                    route = _points(drawn["route"][0])
                    around = await page.evaluate(AROUND_STEP, slug)
                    left, top, right, bottom = around["step"]
                    # The branch's own run: from where it leaves the rail
                    # (the route's last point right of the step) to its end.
                    branch = route[max(i for i, (x, _) in enumerate(route) if x > right + 1):]
                    for seg in zip(branch, branch[1:]):
                        middle = ((seg[0][0] + seg[1][0]) / 2, (seg[0][1] + seg[1][1]) / 2)
                        for x, y in (seg[0], seg[1], middle):
                            for box in around["kept"]:
                                assert not _inside(x, y, box), (slug, (x, y), box, branch)
                    end_x, end_y = branch[-1] if part is None else route[-1]
                    if over and part is None:
                        # Down into its top edge, from the lane over the stepper.
                        assert abs(end_y - top) < 1 and left < end_x < right, (branch, around["step"])
                        assert min(y for _, y in branch) < top - 1, branch
                    elif not over and part is None:
                        assert abs(end_x - right) < 1 and top < end_y < bottom, (branch, around["step"])
                    clear, _ = ui.request({"kind": "clear"})
                    await ui.result(clear)
                    await page.wait_for_function("!document.querySelector('[data-cue]')")
        finally:
            await browser.close()


# Parts the sweep of every registered target found refused, each reached
# now by the room its object keeps (--ui-leaf-lane, --ui-corridor) or by
# visibility no longer counting a holder as a cover: (the navigation that
# shows it, the target).
KEPT_ROOM = [
    (("right.tab.select", {"tab": "agents"}),
     {"region": "right", "view": "right.agents", "kind": "agentsList", "part": "next"}),
    (("main.tab.select", {"tab": "folders"}),
     {"region": "main", "view": "main.folders", "kind": "folderRow", "key": FOLDER_ID, "part": "name"}),
    (("folders.open", {"folderId": FOLDER_ID}),
     {"region": "main", "view": "main.folders", "kind": "folder", "key": FOLDER_ID, "part": "contents"}),
    (("network.open", {"kind": "node", "nodeId": MINI_ID}), {**DETAIL_CPU, "part": "name"}),
    (("thread.open", {"sessionId": "h"}),
     {"region": "main", "view": "main.thread", "kind": "threadRecord", "key": "h/r3", "part": "agent"}),
]


@pytest.mark.asyncio
async def test_parts_beside_a_close_a_status_or_their_objects_edge_are_reached(page_url):
    record = _History()
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, reduced=True, routes=[
            ("**/api/folders**", _Folders()),
            ("**/api/conversation/agents/**", record.thread),
            (re.compile(r".*/api/conversation(\?.*)?$"), record.page)])
        try:
            for show, target in KEPT_ROOM:
                if show:
                    rid, _ = ui.request({"kind": "navigate", "commandId": show[0], "args": show[1]})
                    assert (await ui.result(rid))["status"] == "completed", show
                    if show[1].get("tab") == "agents":
                        await page.wait_for_selector(".agents-list li")
                    await asyncio.sleep(0.5)
                rid, _ = ui.indicate(target, reveal="never")
                result = await ui.result(rid)
                assert result["status"] == "completed", (target, result)
                clear, _ = ui.request({"kind": "clear"})
                await ui.result(clear)
        finally:
            await browser.close()


# ── the tour's routes: trunk first, boxes in order, never across text ──

def _tour_workflow():
    """The workflow, with a task in its second phase for the tour's last stop."""
    wf = _workflow()
    wf["items"] = wf["items"] + [{"slug": "import", "kind": "task", "phase": "deepgram-engine",
                                  "title": "Import entries", "state": "running", "done": 1, "total": 4}]
    return wf


class _TourWorkflows(_Workflows):
    async def __call__(self, route):
        path = route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        if path == "api/workflows":
            await route.fulfill(json={"workflows": [_tour_workflow()]})
        elif path.endswith(("/events", "/transcript")):
            await super().__call__(route)
        else:
            await route.fulfill(json={"workflow": _tour_workflow()})


TILE = f"right.network|node|{MINI_ID}"
WORKFLOW_BOX = "main.workflow|workflow|wf-1"
# Each stop of the demo's tour: (the navigation that shows it, the target,
# the boxes its route marks, outermost first).
TOUR = [
    (("right.tab.select", {"tab": "network"}), TILE_CPU, ["right.panel", TILE]),
    (("network.open", {"kind": "node", "nodeId": MINI_ID}), DETAIL_CPU, [f"main.network|node|{MINI_ID}"]),
    (None, {"region": "left", "view": "left.fixed", "kind": "teamServer", "part": "database"},
     ["left.fixed|teamServer|"]),
    (("workflow.open", {"workflowId": "wf-1", "tab": "phases"}),
     {"region": "main", "view": "main.workflow", "kind": "workflowPhase", "key": "wf-1/mic-recorder"},
     [WORKFLOW_BOX, "main.workflow.view", "main.workflow.phases"]),
    (None, {**WORKFLOW, "part": "tab.decisions"}, [WORKFLOW_BOX, "main.workflow.tabs"]),
    (("main.tab.select", {"tab": "folders"}),
     {"region": "main", "view": "main.folders", "kind": "foldersList", "part": "registered"},
     ["main.folders|foldersList|", "main.folders.tabs"]),
    (("right.tab.select", {"tab": "workflows"}),
     {"region": "right", "view": "right.workflows", "kind": "workflow", "key": "wf-1", "part": "row"},
     ["right.panel"]),
    (("workflow.open", {"workflowId": "wf-1", "tab": "tasks"}),
     {"region": "main", "view": "main.workflow", "kind": "workflowTask", "key": "wf-1/import", "part": "row"},
     [WORKFLOW_BOX, "main.workflow.view"]),    # Last, once every workflow has been read: the Conversation view, which
    # a workflow being opened would otherwise take the centre from.
    (("main.tab.select", {"tab": "conversation"}), {"region": "main", "view": "main.conversation", "kind": "mic", "part": "mute"},
     ["main.conversation|conversation|", "main.conversation|mic|"]),
]

# Every control and line of text on show on the page but the target's own
# (inside its box) and the controls holding it: what a branch never crosses.
ON_THE_PAGE = """(box) => {
  const [l, t, r, b] = box;
  const inside = (x) => x.left >= l - 1 && x.right <= r + 1 && x.top >= t - 1 && x.bottom <= b + 1;
  const holds = (x) => x.left <= l + 1 && x.right >= r - 1 && x.top <= t + 1 && x.bottom >= b - 1;
  const rect = (x) => [x.left, x.top, x.right, x.bottom];
  const kept = [];
  document.querySelectorAll("button, a[href], input, select, textarea, summary, [role='button'], [role='tab'], [role='link']")
    .forEach((c) => {
      const x = c.getBoundingClientRect();
      if (x.width && x.height && !inside(x) && !holds(x) && getComputedStyle(c).visibility === 'visible') kept.push(rect(x));
    });
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), range = document.createRange();
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const el = n.parentElement;
    if (!n.textContent.trim() || !el || el.closest('#ui-pipeline-status') || getComputedStyle(el).visibility !== 'visible') continue;
    range.selectNodeContents(n);
    for (const x of range.getClientRects()) if (x.width && x.height && !inside(x)) kept.push(rect(x));
  }
  return kept;
}"""

CONTOUR_PAD = 4                                     # UiPipelineLayer.tsx


def _branch(points):
    """Where the route leaves its rail: the end of its last long run down
    (the rail's tap), and the run from there to its end."""
    for i in range(len(points) - 1, 0, -1):
        (ax, ay), (bx, by) = points[i - 1], points[i]
        if abs(ax - bx) < 0.6 and by - ay > 40:
            return points[i], points[i:]
    raise AssertionError(f"no run down a rail in {points}")


def _length(points):
    return sum(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 for (ax, ay), (bx, by) in zip(points, points[1:]))


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(1440, 900), (1280, 800)], ids=["1440", "1280"])
@pytest.mark.parametrize("language", ["en", "es"])
async def test_each_tour_stop_stays_on_the_trunk_marks_its_boxes_in_order_and_crosses_no_text(
        page_url, language, size):
    """For every stop of the demo's tour, in English and in Spanish, at
    1440 and 1280 wide: the route runs down its rail to the latest branch
    point, level with the target or the lane beside its row (never an
    early cut-in near the top of an object), marks the boxes holding the
    target outermost first, and from the rail to the target crosses no
    control and no line of text on the page."""
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, reduced=True, sessions=_LanguageSessions(language),
                                        network=False, size=size,
                                        routes=[("**/api/workflows**", _TourWorkflows()),
                                                ("**/api/folders**", _Folders())])
        try:
            await page.wait_for_function(f"document.documentElement.lang === '{'es-419' if language == 'es' else 'en-US'}'")
            for show, target, boxes in TOUR:
                what = (language, size, target.get("part") or target["kind"])
                if show:
                    rid, _ = ui.request({"kind": "navigate", "commandId": show[0], "args": show[1]})
                    assert (await ui.result(rid))["status"] == "completed", (what, show)
                    await asyncio.sleep(0.4)
                rid, _ = ui.indicate(target, reveal="never")
                result = await ui.result(rid)
                assert result["status"] == "completed", (what, result)
                await page.wait_for_function("document.querySelector('.ui-contour')")
                drawn = await page.evaluate(DRAWN)
                _on_the_map(drawn, what)
                assert drawn["marks"] == boxes, (what, drawn["marks"])
                route = _points(drawn["route"][0]) + (_points(drawn["leaf"][0])[1:] if drawn["leaf"] else [])
                box = await page.evaluate("""() => { const b = document.querySelector('.ui-contour').getBBox();
                                                   return [b.x, b.y, b.x + b.width, b.y + b.height]; }""")
                left, top, right, bottom = (box[0] + CONTOUR_PAD, box[1] + CONTOUR_PAD,
                                            box[2] - CONTOUR_PAD, box[3] - CONTOUR_PAD)
                (bx, by), branch = _branch(route)
                # Trunk first: it leaves the rail level with the target, or
                # with the lane just over or under its row.
                assert top - 40 <= by <= bottom + 40, (what, (bx, by), (left, top, right, bottom), route)
                assert _length(route[:route.index(branch[0]) + 1]) > by, (what, route)
                kept = await page.evaluate(ON_THE_PAGE, [left, top, right, bottom])
                for (ax, ay), (cx, cy) in zip(branch, branch[1:]):
                    steps = max(1, int(max(abs(cx - ax), abs(cy - ay)) / 2))
                    for k in range(steps + 1):
                        x, y = ax + (cx - ax) * k / steps, ay + (cy - ay) * k / steps
                        for keep in kept:
                            assert not _inside(x, y, keep), (what, (x, y), keep, branch)
                clear, _ = ui.request({"kind": "clear"})
                await ui.result(clear)
                await page.wait_for_function("!document.querySelector('[data-cue]')")
        finally:
            await browser.close()
