"""The right panel's six tabs through their real controls and the brain's
requests (AgentsControl.tsx, FoldersPanel.tsx, QueuePanel.tsx,
WorkflowsView.tsx, SubsystemList.tsx, VoiceSummary.tsx, uiRight.ts).

Each navigation control carries the command it runs (`data-ui-command`,
`data-ui-args`); clicking it and sending the brain's `ui_request` for that
command leave the panel showing the same thing. Every row and the panel's
own controls are targets: pointing at one on another tab, or on a later
page of a list, reveals it by its declared command and the panel's own
revealer and arrives; pointing at a Queue row never opens it, so it is
neither activated nor read; a filter he typed is not cleared to reveal a
row it hides.

Headless Chromium on the built page; every route and both sockets are
Playwright's (the folder, workflow, subsystem and Queue fakes of their own
page tests), and /ws/voice is the pipeline test's stand-in backend.
"""

import json
import re
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jarvis import subsystem_diagnostics as sd
from tests import messaging_queue_scenario as sc
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_folders import FOLDERS, _Api as _Folders
from tests.test_voice_page_network import MINI_ID, _Sessions, _Team
from tests.test_voice_page_pipeline import _UiServer
from tests.test_voice_page_subsystems import _Server as _Subsystems
from tests.test_voice_page_workflows import _Server as _Workflows
from tests.fixtures.waiting import LIMIT_SEC, settled, until_async

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


class _Ui(_UiServer):
    """The stand-in backend, also able to send the page a frame (a review's
    capture, for the Queue's routes)."""

    def send(self, *msgs):
        for m in msgs:
            self.ws.send(json.dumps(m))

    async def run(self, operation, timeout=LIMIT_SEC):
        rid, _ = self.request(operation)
        return await self.result(rid, timeout)

    async def navigate(self, command, args):
        return await self.run({"kind": "navigate", "commandId": command, "args": args})

    async def indicate(self, target, **extra):
        return await self.run({"kind": "indicate", "target": target, **extra})


async def _open(p, url, queue_world=None, *, language=None, team=None, subsystems=None, width=1440):
    """The page with every right tab's fakes; `language` is what the server
    says is in force, `team` the Network view's team server fake."""
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": width, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    ui = _Ui()
    await page.route_web_socket("**/ws/voice", ui)
    await page.route_web_socket("**/ws/sessions", _Sessions())
    if team is not None:
        await team.install(page)
    if language is not None:
        await page.route("**/api/settings/language", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"language": language})))
    await page.route("**/api/workflows**", _Workflows())
    await page.route("**/api/folders**", _Folders())
    # No projects and no proposals: the Folders tab opens on Projects and
    # lists every folder as independent.
    await page.route("**/api/project-records**", lambda route: route.fulfill(json=(
        {"candidates": []} if "/candidates" in route.request.url
        else {"projects": [], "linked_folders": []})))
    await page.route("**/api/diagnostics/subsystems", subsystems or _Subsystems())
    routes = None
    if queue_world is not None:
        routes = sc.QueueRoutes(queue_world, ui)
        await routes.install(page)
    await page.goto(url)
    assert await until_async(lambda: ui.client), "the page never said ui_hello"
    return browser, page, ui, routes


async def _tab(page, title):
    await page.click(f".right-tabs [role=tab][title={title}]")


async def _marks(page, selector):
    el = page.locator(selector).first
    return await el.get_attribute("data-ui-command"), json.loads(await el.get_attribute("data-ui-args"))


AGENT_ROWS = "() => [...document.querySelectorAll('.agents-list li')].map(li => li.dataset.sessionId)"
SHOW = "() => document.querySelector('.agents-show [aria-pressed=true]')?.textContent"


def _agent(key, part=None):
    ref = {"region": "right", "view": "right.agents", "kind": "agent", "key": key}
    return {**ref, "part": part} if part else ref


@pytest.mark.asyncio
async def test_the_agents_switch_and_paging_by_hand_and_by_request(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui, _ = await _open(p, page_url)
        try:
            await page.wait_for_function("document.querySelectorAll('.agents-list li').length > 2")
            assert await _marks(page, ".agents-show button >> text=All") == ("agents.filter", {"show": "all"})
            assert await _marks(page, ".agents-chevron[title='More agents']") == \
                ("agents.page", {"direction": "next"})

            await page.click(".agents-show button >> text=All")
            by_hand = (await page.evaluate(SHOW), await page.evaluate(AGENT_ROWS))
            await page.click(".agents-show button >> text=Active")
            result = await ui.navigate("agents.filter", {"show": "all"})
            assert (result["status"], result["effects"]) == ("completed", ["list.filtered"]), result
            assert (await page.evaluate(SHOW), await page.evaluate(AGENT_ROWS)) == by_hand

            top = await page.evaluate(AGENT_ROWS)
            await page.click(".agents-chevron[title='More agents']")
            paged_by_hand = await page.evaluate(AGENT_ROWS)
            assert paged_by_hand != top
            await page.click(".agents-chevron[title='Previous agents']")
            assert await page.evaluate(AGENT_ROWS) == top
            result = await ui.navigate("agents.page", {"direction": "next"})
            assert (result["status"], result["effects"]) == ("completed", ["list.paged"]), result
            assert await page.evaluate(AGENT_ROWS) == paged_by_hand

            # At the top, there is nothing before: refused, nothing moves.
            await ui.navigate("agents.page", {"direction": "previous"})
            result = await ui.navigate("agents.page", {"direction": "previous"})
            assert (result["status"], result["reason"]) == ("unavailable", "no-entity"), result
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_pointing_at_an_agent_on_a_later_page_from_another_tab_reveals_it(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui, _ = await _open(p, page_url)
        try:
            await page.wait_for_function("document.querySelectorAll('.agents-list li').length > 2")
            await _tab(page, "Work")
            # Not on the page and not to be revealed: not found.
            result = await ui.indicate(_agent("27"), reveal="never")
            assert (result["status"], result["reason"]) == ("not_found", "not-mounted"), result
            result = await ui.indicate(_agent("27", "orb"), reveal="navigate")
            assert result["status"] == "completed", json.dumps(result)
            assert "27" in await page.evaluate(AGENT_ROWS)
            assert await page.evaluate("() => document.querySelector('.right-tabs [aria-selected=true]').title") \
                == "Agents"
            # The name sits behind the orb from the rail's side: the route
            # never crosses the orb. It leaves the rail level with the gap
            # over the row, passes the row before it there and drops into
            # the name's top edge.
            result = await ui.indicate(_agent("27", "name"))
            assert result["status"] == "completed", json.dumps(result)
            route = await page.evaluate("""() => {
              const d = [...document.querySelectorAll('#ui-pipeline-over [data-cue] .ui-route')].pop().getAttribute('d');
              const orb = document.querySelector('li[data-session-id="27"] .agent-orb').getBoundingClientRect();
              const name = document.querySelector('li[data-session-id="27"] .agent-name').getBoundingClientRect();
              return {d, orb: [orb.left, orb.top, orb.right, orb.bottom], name: [name.left, name.top, name.right, name.bottom]};
            }""")
            pts = [tuple(map(float, xy)) for xy in re.findall(r"(-?[\d.]+) (-?[\d.]+)", route["d"])]
            (ax, ay), (bx, by) = pts[-2], pts[-1]
            assert abs(ax - bx) < 0.6 and ay < by <= route["name"][1], (pts, route)   # down into its top
            left, top, right, bottom = route["orb"]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                assert not (min(x1, x2) < right and max(x1, x2) > left and min(y1, y2) < bottom
                            and max(y1, y2) > top), ((x1, y1), (x2, y2), route["orb"])
            # The list itself is a target too.
            result = await ui.indicate({"region": "right", "view": "right.agents", "kind": "agentsList"})
            assert result["status"] == "completed", json.dumps(result)
        finally:
            await browser.close()


def _folder(fid, part=None):
    ref = {"region": "right", "view": "right.folders", "kind": "folder", "key": fid}
    return {**ref, "part": part} if part else ref


@pytest.mark.asyncio
async def test_folder_rows_open_by_their_command_and_are_revealed_without_clearing_his_filter(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui, _ = await _open(p, page_url)
        try:
            await _tab(page, "Folders")
            row = f".folders-panel-row[title='{FOLDERS[1]['location']['canonical_path']}']"
            await page.wait_for_selector(row)
            assert await _marks(page, row) == ("folders.open", {"folderId": FOLDERS[1]["id"]})

            # A row far down the list: scrolled to, never by moving focus.
            last = FOLDERS[-1]
            result = await ui.indicate(_folder(last["id"], "availability"), reveal="navigate")
            assert result["status"] == "completed", json.dumps(result)

            # His filter, in the plain folder list, hides a row: said so, and
            # his filter stays.
            await page.click(".folders-panel-switch [role=radio][aria-checked=false]")
            await page.fill(".folders-panel input[type=search]", "aurora")
            result = await ui.indicate(_folder(FOLDERS[1]["id"]), reveal="navigate")
            assert (result["status"], result["reason"]) == ("unavailable", "hidden"), result
            assert await page.input_value(".folders-panel input[type=search]") == "aurora"
        finally:
            await browser.close()


SUBSYSTEM_SHOWN = """() => ({
  centre: document.querySelector('.centre-tabs [aria-selected=true]')?.textContent,
  tabs: [...document.querySelectorAll('.centre-tabs [role=tab]')].map(t => t.textContent),
})"""


@pytest.mark.asyncio
async def test_workflow_subsystem_and_voice_rows_by_hand_and_by_request(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui, _ = await _open(p, page_url)
        try:
            # A workflow row, pointed at from the Agents tab: its tab is shown.
            result = await ui.indicate({"region": "right", "view": "right.workflows", "kind": "workflow",
                                        "key": "wf-1", "part": "tasks"}, reveal="navigate")
            assert result["status"] == "completed", json.dumps(result)
            assert await _marks(page, ".workflow-row[data-workflow-id=wf-1]") == \
                ("workflow.open", {"workflowId": "wf-1"})

            await _tab(page, "Diagnostics")
            row = ".subsystem-row[data-subsystem=brain]"
            await page.wait_for_selector(row)
            assert await _marks(page, row) == ("subsystem.open", {"subsystemId": "brain"})
            await page.click(row)
            by_hand = await page.evaluate(SUBSYSTEM_SHOWN)
            await ui.navigate("subsystem.close", {})
            result = await ui.navigate("subsystem.open", {"subsystemId": "brain"})
            assert result["status"] == "completed", json.dumps(result)
            assert await page.evaluate(SUBSYSTEM_SHOWN) == by_hand

            assert await _marks(page, ".voice-summary-open") == ("voice.diagnostics.open", {})
            for target in ({"region": "right", "view": "right.diagnostics", "kind": "subsystem",
                            "key": "communications", "part": "status"},
                           {"region": "right", "view": "right.diagnostics", "kind": "voice", "part": "open"}):
                result = await ui.indicate(target)
                assert result["status"] == "completed", json.dumps([target, result])
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_pointing_at_a_queue_row_never_opens_activates_or_reads_it(page_url):
    world = await sc.build()
    async with async_api.async_playwright() as p:
        browser, page, ui, routes = await _open(p, page_url, queue_world=world)
        try:
            await _tab(page, "Queue")
            await page.wait_for_selector('.queue-list[data-list="waiting"] > li')
            waiting = await page.evaluate(
                """() => document.querySelector('.queue-list[data-list="waiting"] > li').dataset.threadId""")
            assert await _marks(page, f'li[data-thread-id="{waiting}"] .queue-row') == \
                ("queue.open", {"threadId": waiting})
            before = len(routes.calls)

            await _tab(page, "Agents")
            result = await ui.indicate({"region": "right", "view": "right.queue", "kind": "queueThread",
                                        "key": waiting, "part": "subject"}, reveal="navigate")
            assert result["status"] == "completed", json.dumps(result)
            # A dismissed item: Show dismissed opens and its page is read.
            lunch = world.thread("lunch")
            result = await ui.indicate({"region": "right", "view": "right.queue", "kind": "queueThread",
                                        "key": lunch, "part": "row"}, reveal="navigate")
            assert result["status"] == "completed", json.dumps(result)
            assert await page.get_attribute(".queue-history-toggle", "aria-expanded") == "true"
            assert await page.evaluate(
                """() => document.querySelector('.centre-tabs [aria-selected=true]')?.textContent""") \
                == "Conversation"
            changes = [c for c in routes.calls[before:] if c[0] in ("PUT", "POST")]
            assert changes == [], changes

            # Show dismissed by hand and by request is the same.
            assert await _marks(page, ".queue-history-toggle") == ("queue.history", {"shown": False})
            await page.click(".queue-history-toggle")
            hidden = await page.inner_text(".queue-history")
            result = await ui.navigate("queue.history", {"shown": True})
            assert (result["status"], result["effects"]) == ("completed", ["list.paged"]), result
            result = await ui.navigate("queue.history", {"shown": False})
            assert await page.inner_text(".queue-history") == hidden
        finally:
            await browser.close()


# ── the same panel in Spanish ─────────────────────────────────────────────

def _subsystems_said():
    """/api/diagnostics/subsystems as the real probes say it: every name and
    line with its code, so the page can say it in Spanish."""
    now = 1_000_000.0
    voiced = sd.Outcomes()
    voiced.note(True, now=now - 10)
    contact = datetime.fromtimestamp(now - 20, timezone.utc).isoformat()
    rows = [
        sd.communications(None, [], {}, 2, now, now - 600),
        # Falling behind: the longest state word ("Con problemas") beside
        # one of the longest names ("Entrada de voz").
        sd.voice_input({"state": "decoder-slow", "cause": "Machine busy",
                        "pipeline": {"mic": {"state": "ok", "value": 0.4}}}, now),
        sd.brain(None, True, now, now - 600),
        sd.voice_output(True, voiced, now),
        sd.tool_channel(None, sd.Outcomes(), now),
        sd.workflows([], None, now),
        sd.folder_catalog({"counts": {"available": 3, "missing": 1}, "coverage": {"sources": []}}, None, now),
        sd.team_server({"state": "connected", "team": {"name": "Tokens"}, "me": {"login": "tistocks"},
                        "last_contact_at": contact}, now),
    ]
    body = json.dumps(sd.to_json(rows, now))

    async def answer(route):
        await route.fulfill(content_type="application/json", body=body)
    return answer


# Each right tab, by its registered id, with a target on it the brain can
# point at: its branch must still reach it once the words reflow.
RIGHT_TABS = [
    ("agents", ".agents-list li", {"kind": "agentsList"}),
    ("folders", ".folders-panel-row", {"kind": "folder", "key": FOLDERS[0]["id"], "part": "availability"}),
    ("queue", ".queue-list > li", {"kind": "queuePanel"}),
    ("workflows", ".workflow-row", {"kind": "workflow", "key": "wf-1", "part": "tasks"}),
    ("network", ".node-tile", {"kind": "node", "key": MINI_ID}),
    ("diagnostics", ".subsystem-row", {"kind": "subsystem", "key": "folders", "part": "status"}),
]

# What of the panel sticks out of it, or is cut short inside its box: by
# element (its tag and classes), so the two languages can be compared.
SPILL = """() => {
  const panel = document.querySelector('#right-column').getBoundingClientRect();
  const name = (el) => el.tagName.toLowerCase() + [...el.classList].map(c => '.' + c).join('');
  const out = [], cut = [];
  for (const el of document.querySelectorAll('#right-column *')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') continue;
    if (r.left < panel.left - 1 || r.right > panel.right + 1) out.push(name(el));
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (own && el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible') cut.push(name(el));
  }
  return {out: [...new Set(out)].sort(), cut: [...new Set(cut)].sort()};
}"""

TEXT = "() => document.querySelector('#right-column').innerText"

# Words of the panel's own that must not be left in English once it is
# Spanish (names, paths and people's words are not here).
ENGLISH_LEFT = ["Show agents", "registered", "Open Folders", "Nothing waiting", "Active / Outbox",
                "Show dismissed", "No workflows", "Done (", " online", "From Team Server", "THIS MAC",
                "Working", "Communications", "Hearing you", "heard", "speed", "Last sentence voiced"]


async def _right_tabs(page_url, language, world):
    """Each right tab in `language`: what spills, what is said, and each
    target's branch; a screenshot of each when JARVIS_UI_EVIDENCE names a
    folder."""
    evidence = os.environ.get("JARVIS_UI_EVIDENCE")
    seen = {}
    async with async_api.async_playwright() as p:
        browser, page, ui, _ = await _open(p, page_url, queue_world=world, language=language,
                                           team=_Team(), subsystems=_subsystems_said())
        try:
            await page.wait_for_function("(lang) => document.documentElement.lang.startsWith(lang)",
                                         arg=language)
            for tab, rows, target in RIGHT_TABS:
                await page.click(f".right-tabs [data-ui-tab='{tab}']")
                await page.wait_for_selector(rows)
                spill = await settled(page, SPILL)
                result = await ui.indicate({"region": "right", "view": f"right.{tab}", **target})
                assert result["status"] == "completed", json.dumps([language, tab, result])
                if evidence:
                    Path(evidence).mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(Path(evidence) / f"{language}-{tab}.png"))
                seen[tab] = {**spill, "text": await page.evaluate(TEXT)}
        finally:
            await browser.close()
    return seen


@pytest.mark.asyncio
async def test_the_right_tabs_in_spanish_reflow_without_spilling_or_losing_a_branch(page_url):
    """Spanish words are longer: every tab still fits its panel as the
    English does (nothing sticks out or is cut short that was not already),
    every target's branch still arrives, and none of the panel's own words
    is left in English."""
    world = await sc.build()
    english = await _right_tabs(page_url, "en", world)
    spanish = await _right_tabs(page_url, "es", world)
    for tab, _, _ in RIGHT_TABS:
        assert set(spanish[tab]["out"]) <= set(english[tab]["out"]), (tab, spanish[tab]["out"])
        assert set(spanish[tab]["cut"]) <= set(english[tab]["cut"]), (tab, spanish[tab]["cut"])
        left = [w for w in ENGLISH_LEFT if w in spanish[tab]["text"]]
        assert not left, (tab, left, spanish[tab]["text"])
    assert "Carpetas independientes" in spanish["folders"]["text"] and "HUMANO" in spanish["network"]["text"]
    for language in (english, spanish):
        assert not [c for c in language["diagnostics"]["cut"] if "subsystem-name" in c], language["diagnostics"]["cut"]
    assert "Entrada de voz" in spanish["diagnostics"]["text"]
    assert "Vigilante de sesiones" in spanish["diagnostics"]["text"]
    assert "Última frase dicha hace 10 s" in spanish["diagnostics"]["text"]
    assert "En línea: Tokens como tistocks" in spanish["diagnostics"]["text"]
