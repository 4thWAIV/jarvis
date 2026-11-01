"""The Diagnostics tab's subsystems: one row per subsystem, agent
communications first and this page's own connection last; communications
down is a red row and a red Diagnostics tab, whichever tab is open; a
"subsystems" hint on /ws/sessions makes the list read again; and clicking
the row opens communications in full in the centre area.

Headless Chromium on the built page, with /api/diagnostics/subsystems and
both sockets replaced by Playwright.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

IDS = ["communications", "voice_input", "brain", "voice_output", "tool_channel", "workflows",
       "folders", "team_server"]


def _record(comms="ok"):
    down = comms == "down"
    checks = [
        {"id": "watcher", "name": "Session watcher", "state": "ok", "detail": "Watching 2 sessions"},
        {"id": "outgoing", "name": "Messages to agents", "state": comms,
         "detail": ("The last 2 messages, to 2 different agents, did not reach them" if down
                    else "3 reached their agents in the last hour")},
        {"id": "incoming", "name": "Messages from agents", "state": "ok", "detail": "Last look 2 s ago"},
        {"id": "needs", "name": "Needs board", "state": "ok", "detail": "Last look 1 s ago"},
    ]
    summary = (f"Messages to agents: {checks[1]['detail']}" if down else "Watching 2 sessions")
    messages = [{"id": 7, "agentName": "hammer", "status": "undelivered" if down else "answered",
                 "outcome": "lost" if down else "reached", "at": 1_000_000.0, "failure": None}]
    rows = [{"id": "communications", "name": "Communications", "state": comms,
             "summary": summary, "checks": checks, "messages": messages}]
    for id in IDS[1:]:
        rows.append({"id": id, "name": id.replace("_", " ").capitalize(), "state": "ok",
                     "summary": "Fine", "checks": []})
    return {"now": 1_000_000.0, "subsystems": rows}


class _Server:
    def __init__(self):
        self.comms = "ok"
        self.reads = 0

    async def __call__(self, route):
        self.reads += 1
        await route.fulfill(content_type="application/json", body=json.dumps(_record(self.comms)))


class _Sessions:
    def __init__(self):
        self.ws = None

    def __call__(self, ws):
        self.ws = ws               # not connected anywhere: Playwright holds it open


STATE = """() => {
  const rows = [...document.querySelectorAll('.subsystem-row')];
  const comms = document.querySelector(".subsystem-row[data-subsystem='communications']");
  const tab = document.querySelector(".right-tabs [aria-label='Diagnostics']");
  return {ids: rows.map(r => r.dataset.subsystem),
          classes: rows.map(r => r.className),
          commsBg: comms && getComputedStyle(comms).backgroundColor,
          commsState: comms && comms.querySelector('.subsystem-state').textContent,
          commsSummary: comms && comms.querySelector('.subsystem-summary').textContent,
          tabAlert: tab.classList.contains('alert')};
}"""


@pytest.mark.asyncio
async def test_communications_down_is_red_in_the_list_and_on_the_tab(page_url):
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
        await page.route("**/api/diagnostics/subsystems", server)
        await page.goto(page_url)

        await page.click(".right-tabs [aria-label='Diagnostics']")
        await page.wait_for_selector(".subsystem-row[data-subsystem='page'].state-ok", timeout=10000)
        shown = await page.evaluate(STATE)
        assert shown["ids"] == IDS + ["page"], "one row each, communications first, the page last"
        assert all("state-ok" in c for c in shown["classes"]), shown
        assert not shown["tabAlert"]

        # Communications go down; the hint makes the page read again.
        server.comms = "down"
        sessions.ws.send(json.dumps({"type": "subsystems"}))
        await page.wait_for_selector(".subsystem-row[data-subsystem='communications'].state-down")
        shown = await page.evaluate(STATE)
        assert shown["commsState"] == "Down"
        assert "did not reach them" in shown["commsSummary"]
        assert shown["commsBg"] == "rgba(239, 68, 68, 0.22)", "filled red"
        assert shown["tabAlert"]
        dot = await page.evaluate("""getComputedStyle(document.querySelector(
            ".subsystem-row[data-subsystem='communications'] .subsystem-dot")).backgroundColor""")
        assert dot == "rgb(239, 68, 68)"

        # Seen from another tab, the Diagnostics tab is still red.
        await page.click(".right-tabs [aria-label='Agents']")
        assert "alert" in await page.get_attribute(".right-tabs [aria-label='Diagnostics']", "class")
        await page.click(".right-tabs [aria-label='Diagnostics']")

        # Clicking the row opens communications in full in the centre area.
        await page.click(".subsystem-row[data-subsystem='communications']")
        await page.wait_for_selector("#subsystem-detail[data-subsystem='communications']")
        assert await page.locator(".centre-tabs [aria-label='Communications']").count() == 1
        checks = await page.evaluate("""[...document.querySelectorAll('.subsystem-checks li')]
            .map(li => [li.dataset.check, li.className])""")
        assert checks[1] == ["outgoing", "state-down"], checks
        lost = await page.evaluate("document.querySelector('.subsystem-messages li').className")
        assert lost == "outcome-lost"
        head = await page.text_content(".subsystem-head-state")
        assert head.startswith("Down: Messages to agents")

        # They come back: green again, the tab no longer red; then closed.
        server.comms = "ok"
        sessions.ws.send(json.dumps({"type": "subsystems"}))
        await page.wait_for_selector(".subsystem-row[data-subsystem='communications'].state-ok")
        assert not (await page.evaluate(STATE))["tabAlert"]
        await page.click("#subsystem-detail .workflow-close")
        assert await page.locator("#subsystem-detail").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_a_page_that_cannot_reach_jarvis_says_so(page_url):
    """No subsystems route and no sessions socket (the static server): the
    page's own row is down, with why, and the tab is red."""
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                              # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.route_web_socket("**/ws/voice", _Voice())
        await page.goto(page_url)
        await page.click(".right-tabs [aria-label='Diagnostics']")
        row = ".subsystem-row[data-subsystem='page']"
        await page.wait_for_selector(f"{row}.state-down", timeout=10000)
        assert "Can't read JARVIS" in await page.text_content(f"{row} .subsystem-summary")
        assert "alert" in await page.get_attribute(".right-tabs [aria-label='Diagnostics']", "class")
        await browser.close()


async def _open_with(p, page_url, args):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS + args)
    except Exception as e:                                  # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    voice = _Voice()
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(COUNT_CONTEXTS)
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", _Sessions())
    await page.route("**/api/diagnostics/subsystems", _Server())
    await page.route("**/api/agents", lambda route: route.fulfill(json=MANY_AGENTS))
    await page.route("**/api/workflows**", _workflows)
    await page.goto(page_url)
    return browser, page, voice


@pytest.mark.asyncio
async def test_with_webgl_switched_off_the_page_still_runs_and_says_why(page_url):
    """Measured 29 October 2026: after three graphics-process crashes Chrome
    ran with --use-gl=disabled, the orb's WebGL renderer threw, and the whole
    page stopped before it connected to JARVIS; reloading did the same."""
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open_with(p, page_url, ["--disable-webgl", "--disable-3d-apis"])
        await page.click(".right-tabs [aria-label='Diagnostics']")
        row = ".subsystem-row[data-subsystem='page']"
        await page.wait_for_selector(f"{row}.state-degraded", timeout=10000)
        assert "switched WebGL off" in await page.text_content(f"{row} .subsystem-summary")
        assert voice.ws is not None, "the voice page started without the orb"
        assert await page.locator(".subsystem-row[data-subsystem='communications']").count() == 1
        await browser.close()


@pytest.mark.asyncio
async def test_a_lost_graphics_context_is_shown_until_it_comes_back(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open_with(p, page_url, [])
        await page.click(".right-tabs [aria-label='Diagnostics']")
        row = ".subsystem-row[data-subsystem='page']"
        await page.wait_for_selector(f"{row}.state-ok", timeout=10000)
        await page.evaluate("""() => {
            const gl = document.getElementById('orb-canvas').getContext('webgl2');
            window.__lose = gl.getExtension('WEBGL_lose_context'); window.__lose.loseContext(); }""")
        await page.wait_for_selector(f"{row}.state-degraded")
        assert "took the orbs' graphics away" in await page.text_content(f"{row} .subsystem-summary")
        await page.evaluate("() => window.__lose.restoreContext()")
        await page.wait_for_selector(f"{row}.state-ok")
        await browser.close()


@pytest.mark.asyncio
async def test_with_webgl_switched_off_the_agent_and_workflow_orbs_are_still_listed(page_url):
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open_with(p, page_url, ["--disable-webgl", "--disable-3d-apis"])
        await page.wait_for_selector(".agents-list li .agent-orb", timeout=10000)
        await page.click(".right-tabs [role=tab][title=Workflows]")
        await page.wait_for_selector(".workflow-row[data-workflow-id=wf-19] .workflow-orb canvas")
        assert (await page.evaluate(CONTEXTS))["held"] == 0
        assert voice.ws is not None, "the voice page started without the orbs"
        await browser.close()


# --- How many WebGL contexts the orbs hold ------------------------------------
#
# Chrome lets a page hold sixteen WebGL contexts and takes the oldest away past
# that. The main orb has a renderer of its own and every small orb (each
# agent's, each workflow's and subagent's) is drawn by one shared renderer, so
# however many orbs the page shows it holds two.

# Every WebGL context the page makes, kept so a test can count those still
# held and lose the shared one.
COUNT_CONTEXTS = """(() => {
  const made = window.__glContexts = [];
  const getContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
    const context = getContext.call(this, kind, ...rest);
    if (context && String(kind).includes('webgl') && !made.some(m => m.context === context)) {
      made.push({canvas: this, context});
    }
    return context;
  };
  window.__glMostHeld = 0;
  setInterval(() => {
    const held = made.filter(m => !m.context.isContextLost()).length;
    window.__glMostHeld = Math.max(window.__glMostHeld, held);
  }, 50);
})();"""

CONTEXTS = """() => ({
  made: window.__glContexts.length,
  held: window.__glContexts.filter(m => !m.context.isContextLost()).length,
  mostHeld: window.__glMostHeld})"""

MANY_AGENTS = {"agents": [{"session_id": f"s{i}", "name": f"agent {i}", "active": True,
                           "state": "working" if i % 2 else "idle"} for i in range(30)]}


def _many_workflows():
    from tests.test_voice_page_workflows import _workflow
    states = ["running", "waiting", "needs_you", "blocked", "succeeded"]
    return [dict(_workflow(states[i % len(states)]), id=f"wf-{i}", name=f"Workflow {i}")
            for i in range(20)]


async def _workflows(route):
    path = route.request.url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
    many = _many_workflows()
    if path == "api/workflows":
        await route.fulfill(json={"workflows": many})
    elif path.endswith("/events"):
        await route.fulfill(json={"events": []})
    elif path.endswith("/transcript"):
        await route.fulfill(json={"agents": []})
    else:
        await route.fulfill(json={"workflow": many[0]})


async def _open_with_orbs(p, page_url):
    browser, page, _voice = await _open_with(p, page_url, [])
    await page.wait_for_selector(".agents-list li .agent-orb", timeout=10000)
    await page.wait_for_timeout(1000)          # the orbs settle into their colours
    return browser, page


def _lit(png: bytes) -> int:
    """Pixels an orb has drawn: anything brighter than the page behind it."""
    import io
    from PIL import Image
    raw = Image.open(io.BytesIO(png)).convert("RGB").tobytes()
    return sum(1 for i in range(0, len(raw), 3) if max(raw[i], raw[i + 1], raw[i + 2]) > 60)


@pytest.mark.asyncio
async def test_many_agent_and_workflow_orbs_hold_two_webgl_contexts(page_url):
    pytest.importorskip("PIL")
    async with async_api.async_playwright() as p:
        browser, page = await _open_with_orbs(p, page_url)
        agent_orbs = page.locator(".agents-list li .agent-orb")
        assert await agent_orbs.count() >= 5, await agent_orbs.count()
        assert _lit(await agent_orbs.nth(1).screenshot()) > 30, "an agent's orb is not drawn"

        await page.click(".right-tabs [role=tab][title=Workflows]")
        await page.wait_for_selector(".workflow-row[data-workflow-id=wf-19]", timeout=10000)
        await page.click(".workflow-row[data-workflow-id=wf-0]")
        head = ".workflow-detail-head .workflow-orb canvas"
        await page.wait_for_selector(f"#workflow-detail {head}")
        await page.wait_for_timeout(1000)
        shown = await page.locator(".workflow-orb canvas").count()
        assert shown >= 10, shown
        assert _lit(await page.locator(head).screenshot()) > 30, "a workflow's orb is not drawn"

        # Back and forth: small orbs made and dropped again and again.
        for _ in range(3):
            await page.click(".right-tabs [aria-label='Agents']")
            await page.wait_for_selector(".agents-list li .agent-orb")
            await page.click(".right-tabs [role=tab][title=Workflows]")
            await page.wait_for_selector(".workflow-row[data-workflow-id=wf-19]")
        await page.wait_for_timeout(300)

        contexts = await page.evaluate(CONTEXTS)
        assert contexts["held"] == 2 and contexts["mostHeld"] <= 2, contexts
        assert contexts["made"] == 2, "the shared renderer was made again as views changed"
        await page.click(".right-tabs [aria-label='Diagnostics']")
        await page.wait_for_selector(".subsystem-row[data-subsystem='page'].state-ok")
        await browser.close()


@pytest.mark.asyncio
async def test_losing_the_small_orbs_context_is_shown_until_it_comes_back(page_url):
    pytest.importorskip("PIL")
    async with async_api.async_playwright() as p:
        browser, page = await _open_with_orbs(p, page_url)
        # A workflow's detail in the centre keeps a small orb on the page
        # whichever tab the right panel shows.
        await page.click(".right-tabs [role=tab][title=Workflows]")
        await page.click(".workflow-row[data-workflow-id=wf-0]")
        orb = page.locator("#workflow-detail .workflow-detail-head .workflow-orb canvas")
        await orb.wait_for()
        await page.wait_for_timeout(1000)
        assert _lit(await orb.screenshot()) > 30
        await page.evaluate("""() => {
            const shared = window.__glContexts.find(m => m.canvas.id !== 'orb-canvas' && !m.context.isContextLost());
            window.__lose = shared.context.getExtension('WEBGL_lose_context');
            window.__lose.loseContext(); }""")
        await page.wait_for_timeout(300)
        drawn = await orb.evaluate("""c => c.getContext('2d').getImageData(0, 0, c.width, c.height)
            .data.filter((v, i) => i % 4 === 3 && v > 0).length""")
        assert drawn == 0, "a small orb still shows with its context gone"
        await page.click(".right-tabs [aria-label='Diagnostics']")
        row = ".subsystem-row[data-subsystem='page']"
        await page.wait_for_selector(f"{row}.state-degraded")
        assert "took the orbs' graphics away" in await page.text_content(f"{row} .subsystem-summary")

        await page.evaluate("() => window.__lose.restoreContext()")
        await page.wait_for_selector(f"{row}.state-ok")
        await page.wait_for_timeout(1000)
        assert _lit(await orb.screenshot()) > 30, "the small orbs did not come back"
        await browser.close()
