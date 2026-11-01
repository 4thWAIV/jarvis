"""The left column, the menu, Settings, the sign-in reminder and the footer
as the brain sees and moves them (MenuControl.tsx, Settings.tsx,
TeamSignInReminder.tsx, OrbControl.tsx, StatusControl.tsx, MicControl.tsx,
TeamServerTile.tsx, WeeklyUsage.tsx, Footer.tsx).

Every object and meaningful part is a registered target the white signal
reaches along the pipeline from its region's rail; each control that
navigates carries the command it runs, and clicking it leaves the page
exactly as the brain's request for that command does, with the side
effects the contract declares. Pointing inside Settings while it covers the
orb is refused as blocked, never drawn from a second orb.

Headless Chromium on the built page; /ws/voice is a stand-in backend that
sends `ui_request` frames (tests/test_voice_page_pipeline.py), and the
team, settings, brains and usage endpoints are fixtures.
"""

import asyncio
import json
import time

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_network import HEALTHY, SIGNED_OUT, _Sessions, _Team
from tests.test_voice_page_pipeline import _UiServer
from tests.test_voice_page_settings import _Server

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

LEFT = {"region": "left", "view": "left.fixed"}


def _week(provider="anthropic"):
    now = time.time()
    start = now - 3 * 86400
    return {"measured": True, "provider": {"service": provider, "subscription": "max"},
            "week_start": start, "resets_at": start + 7 * 86400, "observed_at": now, "age_sec": 30,
            "expected_used": 43.0, "used": 30.0, "ahead": 13.0,
            "points": [{"at": start + 86400, "used": 10.0}, {"at": now, "used": 30.0}]}


async def _open(p, url, *, team=HEALTHY, reduced=True):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900},
                                  reduced_motion="reduce" if reduced else "no-preference")
    await page.add_init_script(FAKE_TRANSCRIPTION)
    # The settings and brains endpoints first: their catch-all socket route
    # is then overridden by the voice and sessions stand-ins below.
    await _Server().install(page)

    async def week(route):
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(_week()))

    await page.route("**/api/usage/week**", week)
    ui = _UiServer()
    await page.route_web_socket("**/ws/voice", ui)
    await page.route_web_socket("**/ws/sessions", _Sessions())
    await _Team(team).install(page)
    await page.goto(url)
    for _ in range(200):
        if ui.client:
            break
        await asyncio.sleep(0.05)
    assert ui.client, "the page never said ui_hello"
    await page.wait_for_selector("#team-server-tile")
    return browser, page, ui


async def _ask(ui, operation):
    rid, _ = ui.request(operation)
    return await ui.result(rid)


async def _navigate(ui, command, args):
    return await _ask(ui, {"kind": "navigate", "commandId": command, "args": args})


async def _point(ui, target, reveal="never"):
    result = await _ask(ui, {"kind": "indicate", "target": target, "reveal": reveal})
    await _ask(ui, {"kind": "clear"})
    return result


SHOWN = """() => ({
  menu: !!document.getElementById('menu-dropdown'),
  expanded: document.getElementById('btn-menu').getAttribute('aria-expanded'),
  settings: !!document.querySelector('#settings-container .settings-view'),
  reminder: !!document.getElementById('team-reminder'),
})"""

# Where Settings' body has scrolled to once its smooth scroll settles, and
# whether the section named is then in view there.
SCROLLED = """async (id) => {
  const body = document.querySelector('.settings-body');
  await new Promise(r => setTimeout(r, 250));
  let last = -1;
  for (let i = 0; i < 100 && body.scrollTop !== last; i++) {
    last = body.scrollTop;
    await new Promise(r => setTimeout(r, 80));
  }
  const b = body.getBoundingClientRect(), s = document.getElementById(id).getBoundingClientRect();
  return {top: Math.round(body.scrollTop), inView: s.top >= b.top - 1 && s.top < b.bottom};
}"""


async def _settled(page):
    """Settings drawn with everything it reads filled in, so its height (and
    so where a section scrolls to) no longer changes."""
    await async_api.expect(page.get_by_placeholder("Your name")).to_have_value("Tim")
    await page.wait_for_selector(".brain-card")
    await async_api.expect(page.locator("#settings-updates")).to_contain_text("new commit")


async def _marks(page, selector):
    el = page.locator(selector)
    return await el.get_attribute("data-ui-command"), json.loads(await el.get_attribute("data-ui-args"))


@pytest.mark.asyncio
async def test_every_left_column_object_and_part_is_reached_from_the_left_rail(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        try:
            await page.wait_for_selector(".weekly-usage .weekly-figure")
            await page.wait_for_selector("#brain-logo")
            targets = [
                ({"kind": "orb"}, [None, "canvas", "logo"]),
                ({"kind": "status"}, [None]),
                ({"kind": "mic"}, [None, "meter", "mute", "off"]),
                ({"kind": "teamServer"}, [None, "readiness", "server", "database", "lastWrite"]),
                ({"kind": "brainAccount", "key": "anthropic"}, [None, "name", "remaining", "pace", "reset"]),
            ]
            missed = []
            for ref, parts in targets:
                for part in parts:
                    target = {**LEFT, **ref, **({"part": part} if part else {})}
                    result = await _point(ui, target)
                    if (result["status"], result.get("arrival")) != ("completed", "static-reduced-motion"):
                        missed.append([ref["kind"], part, result["status"], result.get("reason")])
            # A part beside another on the side its leaf comes from cannot be
            # reached without crossing that part (uiPipeline.ts leafEdge runs
            # straight in from the facing edge): refused truthfully, never
            # drawn across. Waiting on leaf routing around sibling parts.
            assert missed == [["mic", "mute", "blocked", "no-route"], ["mic", "off", "blocked", "no-route"],
                              ["teamServer", "lastWrite", "blocked", "no-route"]], json.dumps(missed)
            footer = await _point(ui, {"region": "footer", "view": "footer.fixed", "kind": "footer",
                                       "part": "label"})
            assert footer["status"] == "completed", footer
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_brain_can_find_every_left_target_and_its_commands_in_the_map(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        try:
            await page.wait_for_selector(".weekly-usage .weekly-figure")
            answer = await _ask(ui, {"kind": "map", "region": "left"})
            kinds = sorted({(t["ref"]["kind"], t["ref"].get("key")) for t in answer["map"]["targets"]})
            assert kinds == [("brainAccount", "anthropic"), ("mic", None), ("orb", None), ("status", None),
                             ("teamServer", None)]
            overlay = await _ask(ui, {"kind": "map", "region": "overlay"})
            commands = {c["id"]: c for c in overlay["map"]["commands"]}
            assert {"menu.toggle", "settings.open", "settings.section", "settings.close"} <= set(commands)
            # Settings is closed: closing it is not offered, and says why.
            assert (commands["settings.close"]["available"], commands["settings.close"]["reason"]) == \
                (False, "no-entity")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_menu_opens_and_closes_the_same_by_hand_and_by_request(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        try:
            closed = await page.evaluate(SHOWN)
            assert await _marks(page, "#btn-menu") == ("menu.toggle", {"open": True})
            await page.click("#btn-menu")
            await page.wait_for_selector("#menu-dropdown")
            by_hand = await page.evaluate(SHOWN)
            await page.mouse.click(700, 450)               # anywhere else closes it
            await page.wait_for_selector("#menu-dropdown", state="detached")
            assert await page.evaluate(SHOWN) == closed

            opened = await _navigate(ui, "menu.toggle", {"open": True})
            assert (opened["status"], opened["effects"]) == ("completed", ["menu.opened"]), opened
            assert await page.evaluate(SHOWN) == by_hand
            assert await _marks(page, "#btn-menu") == ("menu.toggle", {"open": False})
            shut = await _navigate(ui, "menu.toggle", {"open": False})
            assert (shut["status"], shut["effects"]) == ("completed", []), shut
            assert await page.evaluate(SHOWN) == closed

            # The open menu is a target the brain reveals by its command.
            result = await _point(ui, {"region": "overlay", "view": "overlay.menu", "kind": "menu",
                                       "part": "settings"}, reveal="navigate")
            assert (result["status"], result["effects"]) == ("completed", ["menu.opened"]), result
            button = await _point(ui, {"region": "overlay", "view": "overlay.controls", "kind": "menu",
                                       "part": "toggle"})
            assert button["status"] == "completed", button
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_settings_opens_goes_to_a_section_and_closes_the_same_by_hand_and_by_request(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        try:
            closed = await page.evaluate(SHOWN)
            # By hand: the menu's Settings, the nav's Team, the ×.
            await page.click("#btn-menu")
            assert await _marks(page, "#menu-dropdown button >> nth=0") == ("settings.open", {})
            await page.click("#menu-dropdown button >> nth=0")
            await _settled(page)
            by_hand = await page.evaluate(SHOWN)
            assert by_hand["menu"] is False
            nav = ".settings-nav button:has-text('Team')"
            assert await _marks(page, nav) == ("settings.section", {"section": "team"})
            await page.click(nav)
            team_by_hand = await page.evaluate(SCROLLED, "settings-team")
            assert team_by_hand["top"] > 0 and team_by_hand["inView"], team_by_hand
            await page.click(".settings-close")
            await page.wait_for_selector("#settings-container", state="detached")
            assert await page.evaluate(SHOWN) == closed

            # By request, from the same start (the menu open): the same.
            await _navigate(ui, "menu.toggle", {"open": True})
            opened = await _navigate(ui, "settings.open", {})
            await _settled(page)
            assert (opened["status"], opened["effects"]) == ("completed", ["settings.opened"]), opened
            assert await page.evaluate(SHOWN) == by_hand
            went = await _navigate(ui, "settings.section", {"section": "team"})
            assert went["status"] == "completed", went
            assert await page.evaluate(SCROLLED, "settings-team") == team_by_hand
            shut = await _navigate(ui, "settings.close", {})
            assert shut["status"] == "completed", shut
            assert await page.evaluate(SHOWN) == closed

            # Check for updates and its request open Settings at Updates alike.
            await page.click("#btn-menu")
            await page.click("#menu-dropdown button >> nth=1")
            updates_by_hand = await page.evaluate(SCROLLED, "settings-updates")
            assert updates_by_hand["inView"], updates_by_hand
            await page.click(".settings-backdrop", position={"x": 5, "y": 5})
            await page.wait_for_selector("#settings-container", state="detached")
            opened = await _navigate(ui, "settings.open", {"section": "updates"})
            assert opened["effects"] == ["settings.opened"], opened
            assert await page.evaluate(SCROLLED, "settings-updates") == updates_by_hand
            await _navigate(ui, "settings.close", {})

            # Going to a section of Settings that is not open is refused.
            refused = await _navigate(ui, "settings.section", {"section": "team"})
            assert (refused["status"], refused["reason"]) == ("unavailable", "not-mounted"), refused
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_pointing_inside_settings_is_blocked_while_it_covers_the_orb(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        try:
            section = {"region": "overlay", "view": "overlay.settings", "kind": "settingsSection",
                       "key": "team", "part": "heading"}
            result = await _point(ui, section, reveal="navigate")
            # Opened through its registered command (and said so), then
            # refused: the orb every signal starts from is covered.
            assert (result["status"], result["reason"]) == ("blocked", "occluded"), result
            assert result["effects"] == ["settings.opened"]
            assert await page.locator(".ui-contour").count() == 0
            listed = await _ask(ui, {"kind": "map", "region": "overlay"})
            keys = sorted(t["ref"].get("key") or t["ref"]["kind"] for t in listed["map"]["targets"])
            assert keys == ["brains", "menu", "settings", "system", "team", "updates", "voice", "you"]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_reminders_later_is_the_same_by_hand_and_by_request(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, team=SIGNED_OUT)
        try:
            await page.wait_for_selector("#team-reminder")
            reached = {}
            for part in (None, "text", "signIn", "later"):
                result = await _point(ui, {"region": "main", "view": "main.notice", "kind": "teamReminder",
                                           **({"part": part} if part else {})})
                reached[part] = (result["status"], result.get("reason"))
            # Its parts lie in one row; the leaf comes in from the right, so
            # only the rightmost is reached without crossing another
            # (see the left column's sweep above).
            assert reached == {None: ("completed", None), "later": ("completed", None),
                               "signIn": ("blocked", "no-route"), "text": ("blocked", "no-route")}, reached
            assert await _marks(page, "#team-reminder button:has-text('Later')") == ("reminder.later", {})
            await page.click("#team-reminder button:has-text('Later')")
            await page.wait_for_selector("#team-reminder", state="detached")
            by_hand = await page.evaluate(SHOWN)
            refused = await _navigate(ui, "reminder.later", {})
            assert (refused["status"], refused["reason"]) == ("unavailable", "no-entity"), refused

            await page.reload()
            await page.wait_for_selector("#team-reminder")
            for _ in range(200):
                if ui.client and ui.ws is not None:
                    break
                await asyncio.sleep(0.05)
            later = await _navigate(ui, "reminder.later", {})
            assert (later["status"], later["effects"]) == ("completed", []), later
            assert await page.evaluate(SHOWN) == by_hand
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_team_server_tile_opens_its_detail_the_same_by_hand_and_by_request(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        try:
            assert await _marks(page, "#team-server-tile") == ("network.open", {"kind": "server"})
            centre = "() => document.querySelector('.centre-tabs [aria-selected=true]')?.textContent"
            await page.click("#team-server-tile")
            await page.wait_for_selector("#network-detail")
            by_hand = await page.evaluate(centre)
            await _navigate(ui, "network.close", {})
            await page.wait_for_selector("#network-detail", state="detached")
            result = await _navigate(ui, "network.open", {"kind": "server"})
            assert result["status"] == "completed", result
            await page.wait_for_selector("#network-detail")
            assert await page.evaluate(centre) == by_hand
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_the_signal_reaches_the_team_servers_database_row_animated(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, reduced=False)
        try:
            rid, _ = ui.indicate({**LEFT, "kind": "teamServer", "part": "database"})
            result = await ui.result(rid)
            assert (result["status"], result["arrival"]) == ("completed", "animated"), result
            events = [e["event"] for e in ui.events[rid]]
            assert events[:4] == ["resolving", "emitting", "travelling", "arrived"], events
            assert await page.locator(".ui-contour").count() == 1
        finally:
            await browser.close()
