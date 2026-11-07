"""The left column, the menu, Settings, the sign-in reminder and the footer
as the brain sees and moves them (MenuControl.tsx, Settings.tsx,
TeamSignInReminder.tsx, OrbControl.tsx, MicControl.tsx,
TeamServerTile.tsx, WeeklyUsage.tsx, Footer.tsx).

Every object and meaningful part is a registered target the white signal
reaches along the pipeline from its region's rail; each control that
navigates carries the command it runs, and clicking it leaves the page
exactly as the brain's request for that command does, with the side
effects the contract declares. Settings is a centre view: every section and
every control and reading in it is reached along the main rail.

In Spanish (the language setting's, on the sessions socket's snapshot) the
same words are Spanish, nothing in the column, the tile, the reminder or
Settings' list of sections runs out of its box, every target and part is
reached exactly as in English, and a change back to English rewords it all
in place (frontend/src/messages/left.*.ts).

Headless Chromium on the built page; /ws/voice is a stand-in backend that
sends `ui_request` frames (tests/test_voice_page_pipeline.py), and the
team, settings, brains and usage endpoints are fixtures.
"""

import asyncio
from datetime import timedelta
import json
import time

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_language import _Sessions as _LanguageSessions
from tests.test_voice_page_network import FITS, HEALTHY, SIGNED_OUT, _envelope, _server, _Sessions, _Team
from tests.test_voice_page_pipeline import _UiServer
from tests.test_voice_page_settings import _Server
from jarvis.ui_contract import load as load_contract

UI_CONTRACT = load_contract()

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

LEFT = {"region": "left", "view": "left.fixed"}
MIC = {"region": "main", "view": "main.conversation"}


def _week(provider="anthropic"):
    now = time.time()
    start = now - 3 * 86400
    return {"measured": True, "provider": {"service": provider, "subscription": "max"},
            "week_start": start, "resets_at": start + 7 * 86400, "observed_at": now, "age_sec": 30,
            "expected_used": 43.0, "used": 30.0, "ahead": 13.0,
            "points": [{"at": start + 86400, "used": 10.0}, {"at": now, "used": 30.0}]}


async def _open(p, url, *, team=HEALTHY, reduced=True, language=None):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900},
                                  reduced_motion="reduce" if reduced else "no-preference")
    await page.add_init_script(FAKE_TRANSCRIPTION)
    # The settings and brains endpoints first: their catch-all socket route
    # is then overridden by the voice and sessions stand-ins below.
    server = _Server()
    if language:
        server.language, server.spanish_installed = language, True
    await server.install(page)

    async def week(route):
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(_week()))

    await page.route("**/api/usage/week**", week)
    ui = _UiServer()
    await page.route_web_socket("**/ws/voice", ui)
    sessions = _LanguageSessions(language) if language else _Sessions()
    await page.route_web_socket("**/ws/sessions", sessions)
    await _Team(team).install(page)
    await page.goto(url)
    for _ in range(200):
        if ui.client:
            break
        await asyncio.sleep(0.05)
    assert ui.client, "the page never said ui_hello"
    await page.wait_for_selector("#team-server-tile")
    if language:
        page.sessions, page.settings_server = sessions, server
        await page.wait_for_function("(lang) => document.documentElement.lang.startsWith(lang)", arg=language)
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
  settings: !!document.getElementById('settings-view'),
  reminder: !!document.getElementById('team-reminder'),
})"""

# Where Settings' body has scrolled to once its smooth scroll settles, and
# whether the section named is then in view there.
SCROLLED = """async (id) => {
  const body = document.querySelector('.settings-body');
  // A smooth scroll can take a few frames to start: settled is the same
  // reading six times running, 100 ms apart.
  let last = -1, same = 0;
  for (let i = 0; i < 100 && same < 6; i++) {
    same = body.scrollTop === last ? same + 1 : 0;
    last = body.scrollTop;
    await new Promise(r => setTimeout(r, 100));
  }
  const b = body.getBoundingClientRect(), s = document.getElementById(id).getBoundingClientRect();
  return {top: Math.round(body.scrollTop), inView: s.top >= b.top - 1 && s.top < b.bottom};
}"""


async def _settled(page):
    """Settings drawn with everything it reads filled in, so its height (and
    so where a section scrolls to) no longer changes."""
    await async_api.expect(page.get_by_placeholder("Your name")).to_have_value("Tim")
    await page.wait_for_selector(".brain-card")
    await async_api.expect(page.locator("#settings-updates")).to_contain_text("newer version")


SETTINGS = {"region": "main", "view": "main.settings"}
CLOSE = "#settings-view .settings-head .workflow-close"
# What opening Settings from closed does, as the contract declares it.
OPENED = ["settings.opened", "centre.view-shown"]
# Why a declared part is not pointed at: it is not drawn for this record
# (a brain with nothing failing has no checks), which is not a miss.
ABSENT = {"not-mounted", "hidden", "unmounted"}


async def _settings_missed(ui):
    """Every Settings target on show and each of its parts pointed at
    (scrolled into view where needed): those not reached, and why."""
    listed = await _ask(ui, {"kind": "map", "view": "main.settings"})
    parts = {kind: decl["parts"] for kind, decl in UI_CONTRACT.kinds.items()}
    missed = []
    for t in listed["map"]["targets"]:
        for part in [None, *parts[t["ref"]["kind"]]]:
            target = {**t["ref"], **({"part": part} if part else {})}
            result = await _point(ui, target, reveal="navigate")
            if result["status"] != "completed" and not (part and result.get("reason") in ABSENT):
                missed.append([t["ref"]["kind"], t["ref"].get("key"), part, result["status"], result.get("reason")])
    return missed


async def _marks(page, selector):
    el = page.locator(selector)
    return await el.get_attribute("data-ui-command"), json.loads(await el.get_attribute("data-ui-args"))


LEFT_TARGETS = [
    ({"kind": "orb"}, [None, "canvas", "logo"]),
    # A page loads not listening: Start listening stands where the meter goes.
    # The microphone moved to the Conversation's message input, right of
    # Send; it is checked here with the left column's controls it left.
    ({**MIC, "kind": "mic"}, [None, "listen", "mute", "off", "speaker"]),
    ({"kind": "teamServer"}, [None, "readiness", "server", "database", "lastWrite"]),
    ({"kind": "brainAccount", "key": "anthropic"}, [None, "name", "remaining", "pace", "reset"]),
]
# A part beside another on the side its leaf comes from is reached over or
# under its row (uiPipeline.ts leafEdge; the Team Server's last write and the
# microphone's mute and off go under their rows, the mic in the lane it
# keeps below it, --ui-leaf-lane). Every target and part is reached.
LEFT_MISSED = []


async def _left_missed(ui):
    """Every left target and part pointed at: those not reached, and why."""
    missed = []
    for ref, parts in LEFT_TARGETS:
        for part in parts:
            target = {**LEFT, **ref, **({"part": part} if part else {})}
            result = await _point(ui, target)
            if (result["status"], result.get("arrival")) != ("completed", "static-reduced-motion"):
                missed.append([ref["kind"], part, result["status"], result.get("reason")])
    return missed


@pytest.mark.asyncio
async def test_every_left_column_object_and_part_is_reached_from_the_left_rail(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        try:
            await page.wait_for_selector(".weekly-usage .weekly-figure")
            await page.wait_for_selector("#brain-logo")
            missed = await _left_missed(ui)
            assert missed == LEFT_MISSED, json.dumps(missed)
            footer = await _point(ui, {"region": "footer", "view": "footer.fixed", "kind": "footer",
                                       "part": "label"})
            assert footer["status"] == "completed", footer
        finally:
            await browser.close()


# What the left column says, by its fixed parts.
LEFT_WORDS = """() => ({
  tile: document.querySelector('#team-server-tile .tile-title').textContent,
  rows: [...document.querySelectorAll('#team-server-tile .health-row')].map(r =>
          [r.dataset.row, r.querySelector(':scope > span').textContent]),
  role: document.querySelector('.weekly-usage')?.title ?? null,
  listen: document.getElementById('btn-listen')?.getAttribute('aria-label') ?? null,
  mute: document.getElementById('btn-mute').title,
  off: document.getElementById('btn-mic-off').title,
  reminder: document.querySelector('#team-reminder p')?.textContent ?? null,
  reminderButtons: [...document.querySelectorAll('#team-reminder button')].map(b => b.textContent.trim()),
})"""

# Nothing in Settings' list of sections or its header runs past its own box.
SETTINGS_FITS = """() => {
  const bad = [];
  for (const el of document.querySelectorAll('.settings-nav button, .settings-head h2, .settings-section h3')) {
    if (el.scrollWidth > el.clientWidth + 0.5) bad.push(['clipped', el.textContent]);
    const p = el.parentElement.getBoundingClientRect(), r = el.getBoundingClientRect();
    if (r.left < p.left - 0.5 || r.right > p.right + 0.5) bad.push(['outside', el.textContent]);
  }
  return bad;
}"""


@pytest.mark.asyncio
async def test_in_spanish_the_left_column_reads_spanish_fits_and_every_target_is_reached(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, team=SIGNED_OUT, language="es")
        try:
            await page.wait_for_selector(".weekly-usage .weekly-figure")
            await page.wait_for_selector("#team-reminder")
            words = await page.evaluate(LEFT_WORDS)
            assert words["tile"] == "SERVIDOR DEL EQUIPO", words
            # The rows keep their identities; only their words change.
            assert words["rows"] == [["Server", "Servidor"], ["Database", "Base de datos"],
                                     ["Last write", "Escritura"]], words
            assert "de la semana transcurrido" in words["role"], words
            assert (words["listen"], words["mute"], words["off"]) == (
                "Empezar a escuchar", "Silenciar/Activar sonido",
                "Apagar todo el audio (micrófono y voz de JARVIS)"), words
            assert words["reminder"].startswith("Inicie sesión en el servidor de su equipo"), words
            assert words["reminderButtons"] == ["Iniciar sesión", "Más tarde"], words
            assert await page.locator(".weekly-days").text_content() in ("4,0 días", "4.0 días")
            assert await page.evaluate(FITS, "#team-server-tile") == []

            # The same targets and parts as in English, reached the same way.
            assert await _left_missed(ui) == LEFT_MISSED
            later = await _point(ui, {"region": "main", "view": "main.notice", "kind": "teamReminder",
                                      "part": "later"})
            assert later["status"] == "completed", later

            await page.click("#btn-menu")
            await page.click("#menu-dropdown button >> nth=0")
            await page.wait_for_selector("#settings-view")
            nav = await page.locator(".settings-nav button").all_text_contents()
            assert nav == ["Usted", "Voz e idioma", "Cerebros", "Equipo", "Actualizaciones", "Servidor"], nav
            assert await page.locator(".centre-tabs [aria-selected=true]").text_content() == "Configuración"
            assert await page.evaluate(SETTINGS_FITS) == []
            await async_api.expect(page.get_by_placeholder("Su nombre")).to_have_value("Tim")
            section = await _navigate(ui, "settings.section", {"section": "updates"})
            assert section["status"] == "completed", section
            await async_api.expect(page.locator("#settings-updates")).to_contain_text("versiones más nuevas en main")

            # Back to English from elsewhere: reworded in place, Settings still open.
            page.settings_server.language = "en"
            page.sessions.send({"type": "language", "language": "en"})
            await async_api.expect(page.locator(".settings-nav button").first).to_have_text("You")
            assert await page.locator("#settings-view").count() == 1
            missed = await _settings_missed(ui)
            assert missed == [], json.dumps(missed)
            await page.click(CLOSE)
            words = await page.evaluate(LEFT_WORDS)
            assert words["tile"] == "TEAM SERVER" and words["reminderButtons"] == ["Sign in", "Later"], words
            assert await _left_missed(ui) == LEFT_MISSED
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
            assert kinds == [("brainAccount", "anthropic"), ("orb", None), ("teamServer", None)]
            main = await _ask(ui, {"kind": "map", "view": "main.conversation"})
            assert "mic" in {t["ref"]["kind"] for t in main["map"]["targets"]}
            # Off Conversation the one microphone is in the centre's corner,
            # mapped and reached there.
            assert (await _navigate(ui, "main.tab.select", {"tab": "folders"}))["status"] == "completed"
            corner = await _ask(ui, {"kind": "map", "view": "main.corner"})
            assert [t["ref"]["kind"] for t in corner["map"]["targets"]] == ["mic"]
            for part in ("listen", "mute", "off", "speaker"):
                got = await _point(ui, {"region": "main", "view": "main.corner", "kind": "mic", "part": part})
                assert got["status"] == "completed", (part, got)
            assert await page.locator("#mic-control").count() == 1
            overlay = await _ask(ui, {"kind": "map", "region": "overlay"})
            assert "menu.toggle" in {c["id"] for c in overlay["map"]["commands"]}
            settings = await _ask(ui, {"kind": "map", "view": "main.settings"})
            commands = {c["id"]: c for c in settings["map"]["commands"]}
            assert {"settings.open", "settings.section", "settings.close"} <= set(commands)
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
            await page.click(CLOSE)
            await page.wait_for_selector("#settings-view", state="detached")
            assert await page.evaluate(SHOWN) == closed

            # By request, from the same start (the menu open): the same.
            await _navigate(ui, "menu.toggle", {"open": True})
            opened = await _navigate(ui, "settings.open", {})
            await _settled(page)
            assert (opened["status"], opened["effects"]) == ("completed", OPENED), opened
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
            await page.click(CLOSE)
            await page.wait_for_selector("#settings-view", state="detached")
            opened = await _navigate(ui, "settings.open", {"section": "updates"})
            assert opened["effects"] == OPENED, opened
            assert await page.evaluate(SCROLLED, "settings-updates") == updates_by_hand
            await _navigate(ui, "settings.close", {})

            # Going to a section of Settings that is not open is refused.
            refused = await _navigate(ui, "settings.section", {"section": "team"})
            assert (refused["status"], refused["reason"]) == ("unavailable", "not-mounted"), refused
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_every_settings_section_and_control_is_reached_along_the_main_rail(page_url):
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url)
        try:
            section = {**SETTINGS, "kind": "settingsSection", "key": "team", "part": "heading"}
            result = await _point(ui, section, reveal="navigate")
            # Opened through its registered command (and said so), then
            # reached from the orb, which nothing covers now.
            assert (result["status"], result["effects"]) == ("completed", OPENED), result
            await _settled(page)
            listed = await _ask(ui, {"kind": "map", "view": "main.settings"})
            keys = {(t["ref"]["kind"], t["ref"].get("key")) for t in listed["map"]["targets"]}
            assert {("settings", None), ("brainCard", "anthropic"), ("brainAdd", "openai"),
                    ("updateVersion", "e" * 40)} <= keys, keys
            assert {k for kind, k in keys if kind == "settingsSection"} == {
                "you", "voice", "brains", "team", "updates", "server"}
            assert {k for kind, k in keys if kind == "settingsItem"} >= {
                "name", "honorific", "saveYou", "language", "spanishModel", "voiceId", "saveVoiceId",
                "fishKey", "testFishKey", "saveFishKey", "currentVersion", "checkUpdates", "installUpdate",
                "serverPort", "serverUptime"}
            missed = await _settings_missed(ui)
            assert missed == [], json.dumps(missed)
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
            # Its parts lie in one row; the leaf comes in from the right and
            # reaches those left of Later under the row.
            assert reached == {None: ("completed", None), "later": ("completed", None),
                               "signIn": ("completed", None), "text": ("completed", None)}, reached
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


@pytest.mark.asyncio
@pytest.mark.parametrize("write_ago", [timedelta(seconds=3), timedelta(minutes=1), timedelta(hours=20)])
async def test_in_spanish_a_healthy_team_server_tile_fits_its_figures(page_url, write_ago):
    """The longest Spanish ages ("hace 1 min", "hace 20 h") beside the
    row's name ("Escritura") fit, as the seconds do."""
    team = lambda: _envelope(server=_server(write_ago=write_ago))  # noqa: E731
    async with async_api.async_playwright() as p:
        browser, page, ui = await _open(p, page_url, team=team, language="es")
        try:
            await page.wait_for_function(
                "() => document.querySelector('#team-server-tile [data-row=\"Last write\"] .ago')")
            assert await page.evaluate(FITS, "#team-server-tile") == []
            label = await page.get_attribute("#team-server-tile", "aria-label")
            assert label.startswith("Servidor del equipo:") and "Base de datos conectada" in label, label
        finally:
            await browser.close()
