"""The Network view, by what the user sees: the Team Server tile in the left
column, the Network tab's instance tiles on the right, and their details in
the centre (TeamServerTile.tsx, NetworkPanel.tsx, NetworkTile.tsx,
TeamServerView.tsx, NodeView.tsx), all read from GET /api/team/network alone;
and the page's one human-activity collector (humanActivity.ts).

Headless Chromium on the built page. /api/team/network is answered here with
fixture envelopes in the route's approved shape (a NetworkSnapshot as the
team server returns it, plus the backend's own few fields), and both sockets
are Playwright's, so a "network" hint can be sent when the fixture changes.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _record_item, _Voice

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

SELF_ID = "6f0c1d8e-3b7a-4f0e-9b1c-2a4d5e6f7a80"
MINI_ID = "0b8e2c4a-1d3f-4a5b-8c7d-9e0f1a2b3c4d"
MINUS = "−"


def _iso(ago: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) - ago).isoformat().replace("+00:00", "Z")


def _resources(cpu=32.0, used=24 * 1024 ** 3, total=64 * 1024 ** 3, state="measured", scope="host"):
    return {"state": state, "scope": scope, "cpu_percent": cpu if state == "measured" else None,
            "memory_used_bytes": used if state == "measured" else None,
            "memory_total_bytes": total if state == "measured" else None, "age_ms": 1200}


def _account(provider, role, remaining, pace, left=None, runs_out=None):
    return {"account_ref": f"ref{provider}0123456789abcdefghij", "provider": provider, "role": role,
            "remaining_percent": remaining, "pace_points": pace, "resets_at": _iso(-timedelta(days=3)),
            "left_at_reset_percent": left, "runs_out_hours_before_reset": runs_out,
            "age_ms": None if pace is None else 90_000}


def _node(node_id, label, *, cpu=32.0, human=timedelta(minutes=2), pace=3.0, presence="online",
          activity="available", resources=None, accounts=None, configured=2, measured=2):
    accounts = accounts if accounts is not None else [
        _account("anthropic", "brain", 60.0, 10.0, left=20.0),
        _account("openai", "standby", 46.0, -4.0, runs_out=12.0)]
    return {
        "node_id": node_id, "owner_github_id": 1, "owner_login": "tistocks", "label": label,
        "jarvis_version": "0.9.0", "os": "macOS 15.6", "presence": presence,
        "last_seen_at": _iso(timedelta(seconds=3)),
        "resources": resources or _resources(cpu=cpu),
        "brains": {"configured": configured, "measured": measured,
                   "average_remaining_percent": None if pace is None else 53.0,
                   "pace_points": pace, "accounts": accounts},
        "activity_state": activity,
        "last_human_activity_at": _iso(human) if activity == "available" and human is not None else None,
        "last_human_activity_source": "gui" if activity == "available" and human is not None else None,
    }


def _server(database="connected", ping=9.0, write_ago=timedelta(seconds=3), failures=0, error=None):
    return {
        "database": database, "database_ping_ms": ping if database == "connected" else None,
        "database_checked_at": _iso(timedelta(seconds=2)),
        "last_write_ok_at": None if write_ago is None else _iso(write_ago),
        "last_database_error": error, "database_failures_in_a_row": failures,
        "uptime_sec": 8040, "version": "1.4.0", "commit": "e609c93",
        "resources": _resources(cpu=18.0, used=410 * 1024 ** 2, total=1024 ** 3, scope="container"),
        "history": [{"at": _iso(timedelta(seconds=30 * (29 - i))), "database_ping_ms": 9.0 + i % 3,
                     "write_ok": True} for i in range(30)],
    }


def _envelope(*, nodes=None, server=None, connection="connected", stale=False, round_trip=42.0,
              snapshot=True, last_error=None):
    snap = None
    if snapshot:
        snap = {"revision": 41, "server_time": _iso(), "you_node_id": SELF_ID,
                # The team server's order puts the other machine first: the
                # page must still show this one first.
                "nodes": nodes if nodes is not None else [
                    _node(MINI_ID, "Tim’s Mac mini", cpu=12.0, human=timedelta(hours=1), pace=-6.5,
                          accounts=[_account("anthropic", "brain", 22.0, -6.5, runs_out=12.0)],
                          configured=1, measured=1),
                    _node(SELF_ID, "Tim’s i9")],
                "server": server or _server()}
    return {"snapshot": snap, "received_at": _iso(timedelta(seconds=3)), "stale": stale,
            "last_error": last_error, "round_trip_ms": round_trip,
            "last_contact_at": _iso(timedelta(seconds=3)), "connection": connection}


HEALTHY = _envelope
DATABASE_DOWN = lambda: _envelope(  # noqa: E731
    server=_server(database="unreachable", write_ago=timedelta(seconds=40), failures=8,
                   error="ServerSelectionTimeoutError"),
    stale=True, round_trip=46.0, last_error="503 from the team server")
NO_CONTACT = lambda: _envelope(connection="stale", stale=True, round_trip=None,  # noqa: E731
                               last_error="timed out")
SIGNED_OUT = lambda: _envelope(connection="signed_out", stale=True, round_trip=None)  # noqa: E731
# The last exchange timed out but the status poll is recent: the team
# server's last health says connected, which is stale and so not shown,
# and not called Ready.
STALE = lambda: _envelope(stale=True, round_trip=None, last_error="timed out")  # noqa: E731


class _Team:
    """/api/team/network and /api/team/activity, answered by the test."""

    def __init__(self, envelope=None):
        self.envelope = envelope or HEALTHY
        self.reads = 0
        self.activity: list[dict] = []
        self.other: list[str] = []

    async def install(self, page):
        async def network(route):
            self.reads += 1
            await route.fulfill(status=200, content_type="application/json", body=await self.body())

        async def activity(route):
            self.activity.append({"body": json.loads(route.request.post_data or "{}"),
                                  "origin": route.request.headers.get("origin")})
            await route.fulfill(status=204, body="")

        async def other(route):
            self.other.append(route.request.url)
            await route.fulfill(status=404, body="")

        await page.route("**/api/team/**", other)
        await page.route("**/api/team/network", network)
        await page.route("**/api/team/activity", activity)

    async def body(self) -> bytes:
        # A factory makes its times when it is read, as the backend would.
        envelope = self.envelope() if callable(self.envelope) else self.envelope
        return json.dumps(envelope).encode()


class _RealRoute(_Team):
    """/api/team/network answered by JARVIS's own route (team_api.py), over
    a TeamConnection exchanging with a stand-in team server."""

    async def body(self) -> bytes:
        from jarvis import server as jarvis_server
        transport = httpx.ASGITransport(app=jarvis_server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8340") as client:
            r = await client.get("/api/team/network")
        assert r.status_code == 200, r.text
        return r.content


class _Sessions:
    def __init__(self):
        self.ws = None

    def __call__(self, ws):
        self.ws = ws               # not connected anywhere: Playwright holds it open

    def hint(self):
        self.ws.send(json.dumps({"type": "network"}))


async def _open(p, url, team, *, panel=None, sessions=None, routes=()):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", _Voice())
    await page.route_web_socket("**/ws/sessions", sessions or _Sessions())
    await team.install(page)
    for pattern, handler in routes:
        await page.route(pattern, handler)
    await page.goto(url)
    if panel:
        await page.add_style_tag(content=f":root {{ --panel-width: {panel}px; }}")
    await page.wait_for_selector("#team-server-tile")
    return browser, page


async def _network_tab(page):
    await page.click(".right-tabs [aria-label='Network']")
    await page.wait_for_selector(".node-tile")


async def _changed(page, sessions, team, envelope, tile_word):
    team.envelope = envelope
    sessions.hint()
    await async_api.expect(page.locator("#team-server-tile .readiness-word")).to_have_text(tile_word)


TILE = """(sel) => {
  const t = document.querySelector(sel);
  const r = t.getBoundingClientRect();
  return {height: r.height, width: r.width, radius: getComputedStyle(t).borderTopLeftRadius,
          label: t.getAttribute('aria-label')};
}"""

SERVER_ROWS = """() => [...document.querySelectorAll('#team-server-tile .health-row')].map(r => ({
  name: r.dataset.row, state: r.className.replace('health-row ', ''),
  figure: r.querySelector('strong').textContent,
  marker: getComputedStyle(r.querySelector('i')).transform !== 'none' ? 'diamond'
        : getComputedStyle(r.querySelector('i')).backgroundColor === 'rgba(0, 0, 0, 0)' ? 'ring' : 'dot'}))"""

NODE = """(id) => {
  const t = document.querySelector(`.node-tile[data-node="${id}"]`);
  const q = s => t.querySelector(s);
  const metrics = [...t.querySelectorAll('.visual-metric')];
  return {title: q('.tile-title').textContent, classes: t.className,
          human: q('.human-beacon strong').textContent, badge: q('.human-beacon').className,
          figures: metrics.map(m => m.querySelector('strong').textContent),
          unknown: metrics.map(m => m.classList.contains('unknown')),
          pace: q('.pace-metric strong').className,
          paceRail: q('.pace-track i') && [q('.pace-track i').className, q('.pace-track i').style.width],
          marker: q('.online-marker').className,
          label: t.getAttribute('aria-label')};
}"""

# Every element in a tile is inside the tile and inside its parent, and no
# element's text runs past its own box; a tile's title may only truncate.
FITS = """(sel) => {
  const bad = [];
  for (const tile of document.querySelectorAll(sel)) {
    const box = tile.getBoundingClientRect();
    for (const el of tile.querySelectorAll('.tile-body *')) {
      if (el.closest('svg') && el.tagName !== 'svg') continue;
      const s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden') continue;
      const r = el.getBoundingClientRect();
      if (!r.width) continue;
      const p = el.parentElement.getBoundingClientRect();
      const name = `${el.tagName}.${el.className && el.className.baseVal === undefined ? el.className : ''} "${el.textContent}"`;
      if (r.left < box.left + 0.5 || r.right > box.right - 0.5) bad.push(['outside tile', name, r.left, r.right, box.left, box.right]);
      if (r.left < p.left - 0.5 || r.right > p.right + 0.5) bad.push(['outside parent', name, r.left, r.right, p.left, p.right]);
      if (el.clientWidth && el.scrollWidth > el.clientWidth) bad.push(['clipped', name, el.scrollWidth, el.clientWidth]);
    }
    const title = tile.querySelector('.tile-title').getBoundingClientRect();
    if (title.left < box.left || title.right > box.right) bad.push(['title outside', tile.className]);
    for (const el of tile.querySelectorAll('.tile-self, .online-marker')) {
      if (el.scrollWidth > el.clientWidth + 0.5) bad.push(['title marker clipped', el.textContent]);
    }
  }
  return bad;
}"""


@pytest.mark.asyncio
async def test_the_tiles_have_the_brain_boxes_footprint_and_this_mac_comes_first(page_url):
    team = _Team()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team)
        server = await page.evaluate(TILE, "#team-server-tile")
        brain = await page.evaluate(TILE, ".weekly-usage")
        assert server["height"] == brain["height"] == 76, (server, brain)
        # Brain and Standby are widened to the Team Server tile's width.
        assert abs(server["width"] - brain["width"]) < 0.5, (server, brain)
        assert server["radius"] == "16px"
        # Under the microphone, above the brains, which are unchanged.
        order = await page.evaluate("""() => [...document.querySelectorAll('#left-column > *')].map(e => e.id)""")
        assert order.index("team-server-tile") == order.index("brain-usage") - 1, order
        assert await page.locator("#team-indicator").count() == 0

        tabs = await page.evaluate("""() => [...document.querySelectorAll('.right-tabs [role=tab]')]
                                         .map(t => t.getAttribute('aria-label'))""")
        assert tabs == ["Agents", "Diagnostics", "Workflows", "Network"], tabs
        await _network_tab(page)
        tiles = await page.evaluate("""() => [...document.querySelectorAll('.node-tile')].map(t => ({
            id: t.dataset.node, h: t.getBoundingClientRect().height, self: t.classList.contains('self-tile')}))""")
        assert [t["id"] for t in tiles] == [SELF_ID, MINI_ID], "this machine first, as the team server said"
        assert all(t["h"] == 76 for t in tiles), tiles
        assert [t["self"] for t in tiles] == [True, False]
        shown = await page.evaluate(NODE, SELF_ID)
        assert shown["title"] == "TIM’S I9 · THIS MAC", shown["title"]
        assert (await page.evaluate(NODE, MINI_ID))["title"] == "TIM’S MAC MINI"
        assert await page.text_content(".roster-count") == "2 online"
        assert re.fullmatch(r"From Team Server · updated [3-6]s ago", await page.text_content(".roster-freshness"))
        # Nothing but the network route is read for any of it.
        assert team.other == [], team.other
        await browser.close()


STATES = [
    ("healthy", HEALTHY, "Ready",
     [("Server", "ok", "42 ms", "dot"), ("Database", "ok", "9 ms", "dot"), ("Last write", "ok", r"[3-6]s ago", "dot")],
     "Team server: Ready. Round trip 42 milliseconds. Database connected, ping 9 milliseconds. "
     "Last write succeeded just now. Open details in the centre."),
    ("database down", DATABASE_DOWN, "Not ready",
     [("Server", "ok", "46 ms", "dot"), ("Database", "bad", "down", "diamond"),
      ("Last write", "bad", r"4[0-3]s ago", "diamond")],
     r"Team server: Not ready\. Round trip 46 milliseconds\. Database down\. Writes failing for 4[0-3] seconds\. "
     r"Open details in the centre\."),
    ("stale", STALE, "No contact",
     [("Server", "unknown", "—", "ring"), ("Database", "unknown", "—", "ring"), ("Last write", "unknown", "—", "ring")],
     "Team server: No contact. Round trip unknown. Database state unknown. Open details in the centre."),
    ("no contact", NO_CONTACT, "No contact",
     [("Server", "unknown", "—", "ring"), ("Database", "unknown", "—", "ring"), ("Last write", "unknown", "—", "ring")],
     "Team server: No contact. Round trip unknown. Database state unknown. Open details in the centre."),
    ("sign in", SIGNED_OUT, "Sign in",
     [("Server", "unknown", "—", "ring"), ("Database", "unknown", "—", "ring"), ("Last write", "unknown", "—", "ring")],
     "Team server: Sign in. Round trip unknown. Database state unknown. Open details in the centre."),
]


@pytest.mark.asyncio
async def test_every_team_server_state_is_drawn_and_spoken(page_url):
    team, sessions = _Team(), _Sessions()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, sessions=sessions)
        for name, envelope, word, rows, spoken in STATES:
            await _changed(page, sessions, team, envelope, word)
            got = await page.evaluate(SERVER_ROWS)
            assert [(r["name"], r["state"], r["marker"]) for r in got] == [(n, st, m) for n, st, _, m in rows], (name, got)
            for row, (_, _, figure, _) in zip(got, rows):
                assert re.fullmatch(figure, row["figure"]), (name, row, figure)
            label = (await page.evaluate(TILE, "#team-server-tile"))["label"]
            assert re.fullmatch(spoken if "\\" in spoken else re.escape(spoken), label), (name, label)
            colour = await page.evaluate(
                "getComputedStyle(document.querySelector('#team-server-tile .server-beacon')).color")
            assert colour == ("rgb(74, 222, 128)" if word == "Ready" else "rgb(251, 191, 36)"), (name, colour)
        await browser.close()


# The word follows the network exchange, not the 30-second status poll: as
# staging found it, the link is cut while the poll still says connected,
# then the exchange is fresh again while the poll still says stale.
FOLLOWS_THE_EXCHANGE = [
    ("link cut, poll behind", lambda: _envelope(stale=True, round_trip=None, last_error="timed out"), "No contact"),
    ("fresh again, poll behind", lambda: _envelope(connection="stale"), "Ready"),
    ("fresh, poll not yet in", lambda: _envelope(connection="connecting"), "Ready"),
    ("fresh, database down", lambda: _envelope(connection="stale", server=_server(database="unreachable")),
     "Not ready"),
    ("stale, database down", lambda: _envelope(connection="connected", stale=True,
                                               server=_server(database="unreachable")), "Not ready"),
    ("signed out, fresh", lambda: _envelope(connection="signed_out"), "Sign in"),
]


@pytest.mark.asyncio
async def test_the_word_follows_the_network_exchange_not_the_status_poll(page_url):
    team, sessions = _Team(), _Sessions()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, sessions=sessions)
        for name, envelope, word in [("healthy", HEALTHY, "Ready"), *FOLLOWS_THE_EXCHANGE]:
            await _changed(page, sessions, team, envelope, word)
            tile = await page.evaluate(TILE, "#team-server-tile")
            assert tile["label"].startswith(f"Team server: {word}. "), (name, tile["label"])
            beacon = await page.get_attribute("#team-server-tile", "class")
            assert f"readiness-{word.lower().replace(' ', '-')}" in beacon.split(), (name, beacon)
            if word == "Ready":             # the rows follow the exchange too, not the poll
                rows = await page.evaluate(SERVER_ROWS)
                assert all(r["state"] == "ok" and r["figure"] != "—" for r in rows), (name, rows)
        await browser.close()


@pytest.mark.asyncio
async def test_the_detail_shows_stale_health_as_unknown_and_failures_as_failures(page_url):
    team, sessions = _Team(STALE), _Sessions()
    facts = """() => Object.fromEntries([...document.querySelectorAll('#network-detail dt')]
                 .map(dt => [dt.textContent, dt.nextElementSibling.textContent]))"""
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, sessions=sessions)
        await page.click("#team-server-tile")
        await page.wait_for_selector("#network-detail[data-node='server']")
        shown = await page.evaluate(facts)
        stale = "Unknown; values are stale"
        for name in ("Database connection", "Last successful write", "Last database error", "Failures in a row"):
            assert shown[name] == stale, (name, shown)
        assert shown["Database ping"] == "—" and shown["Round trip"] == "—", shown

        await _changed(page, sessions, team, DATABASE_DOWN, "Not ready")
        await async_api.expect(page.locator("#network-detail")).to_contain_text("ServerSelectionTimeoutError")
        shown = await page.evaluate(facts)
        assert shown["Database connection"] == "Unreachable · ServerSelectionTimeoutError", shown
        assert shown["Failures in a row"] == "8" and shown["Round trip"] == "46 ms", shown
        assert re.fullmatch(r"4[0-3] seconds ago \(.+\)", shown["Last successful write"]), shown
        await browser.close()


@pytest.mark.asyncio
async def test_pace_signs_unknown_readings_and_stale_values(page_url):
    odd = "1d2e3f4a-5b6c-4d7e-8f90-a1b2c3d4e5f6"
    nodes = [
        _node(SELF_ID, "Tim’s i9"),
        _node(MINI_ID, "Tim’s Mac mini", cpu=12.0, human=timedelta(hours=1), pace=-6.5),
        _node(odd, "Headless box", resources=_resources(state="warming_up"), activity="never_observed",
              human=None, pace=None, accounts=[], configured=0, measured=0, presence="delayed"),
    ]
    team, sessions = _Team(_envelope(nodes=nodes)), _Sessions()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, sessions=sessions)
        await _network_tab(page)
        mine = await page.evaluate(NODE, SELF_ID)
        assert mine["human"] == "2m" and "recent" in mine["badge"], mine
        assert mine["figures"] == ["32%", "38%", "+3.0%"], mine
        assert mine["pace"] == "pace-ahead" and mine["paceRail"] == ["ahead", "15%"], mine
        assert mine["marker"] == "online-marker online"
        assert mine["label"] == (
            "Tim’s i9, this Mac, owned by tistocks, as reported by Team Server: online; last human activity "
            "2 minutes ago. CPU 32 percent, memory 38 percent, brains 3.0 points ahead of an even pace. "
            "Open details in the centre.")

        mini = await page.evaluate(NODE, MINI_ID)
        assert mini["human"] == "1h" and "away" in mini["badge"], mini
        assert mini["figures"] == ["12%", "38%", f"{MINUS}6.5%"], mini
        assert mini["pace"] == "pace-behind" and mini["paceRail"] == ["behind", "32.5%"], mini
        assert "brains 6.5 points behind an even pace, heading for the limit." in mini["label"]
        badge = await page.evaluate(f"""getComputedStyle(document.querySelector(
            '.node-tile[data-node="{MINI_ID}"] .human-beacon')).borderTopStyle""")
        assert badge == "dashed"

        unknown = await page.evaluate(NODE, odd)
        assert unknown["human"] == "?" and "unknown" in unknown["badge"], unknown
        assert unknown["figures"] == ["—", "—", "—"] and unknown["unknown"] == [True, True, True], unknown
        assert unknown["marker"] == "online-marker delayed"
        assert "no human activity observed. CPU unknown, memory unknown, no brain accounts configured." \
            in unknown["label"], unknown["label"]
        rail = await page.evaluate(f"""getComputedStyle(document.querySelector(
            '.node-tile[data-node="{odd}"] .metric-track')).backgroundImage""")
        assert rail.startswith("repeating-linear-gradient"), "patterned, not an empty healthy rail"

        # The team server stops answering: the last values stay, marked stale.
        team.envelope = _envelope(nodes=nodes, connection="stale", stale=True, round_trip=None)
        sessions.hint()
        await page.wait_for_selector(f'.node-tile.stale[data-node="{SELF_ID}"]')
        mine = await page.evaluate(NODE, SELF_ID)
        assert mine["figures"] == ["32%", "38%", "+3.0%"] and mine["human"] == "2m", mine
        assert mine["marker"] == "online-marker unknown"
        assert mine["label"].endswith("Values are stale. Open details in the centre."), mine["label"]
        assert "online" not in mine["label"], "no presence claimed from a stale roster"
        assert await page.text_content(".roster-count") == "Last-known: 3"
        assert "stale" in await page.text_content(".roster-freshness")
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("panel", [230, 210, 190, 180])
async def test_nothing_is_clipped_at_narrow_panel_widths(page_url, panel):
    worst = lambda: [  # noqa: E731
        _node(SELF_ID, "Tim’s extraordinarily long-named workstation", cpu=100.0, human=timedelta(minutes=59),
              pace=-12.5, resources=_resources(cpu=100.0, used=64 * 1024 ** 3)),
        _node(MINI_ID, "Tim’s Mac mini", human=timedelta(seconds=45), pace=99.9),
        _node("1d2e3f4a-5b6c-4d7e-8f90-a1b2c3d4e5f6", "Box", human=timedelta(hours=23), pace=-0.04),
    ]
    team, sessions = _Team(), _Sessions()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, panel=panel, sessions=sessions)
        await _network_tab(page)
        for name, envelope, word, *_ in STATES:
            def env(envelope=envelope):
                e = envelope()
                e["snapshot"]["nodes"] = worst()
                return e
            await _changed(page, sessions, team, env, word)
            await page.wait_for_selector(f'.node-tile[data-node="{SELF_ID}"]')
            bad = await page.evaluate(FITS, "#team-server-tile, .node-tile")
            assert bad == [], (panel, name, bad)
            ago = await page.locator("#team-server-tile .ago").count()
            if ago and word in ("Ready", "Not ready"):
                shown = await page.evaluate(
                    "getComputedStyle(document.querySelector('#team-server-tile .ago')).display")
                assert (shown == "none") == (panel < 230), (panel, shown)
        sizes = await page.evaluate("""() => { const r = document.querySelector('#team-server-tile .health-row');
            return [getComputedStyle(r.querySelector('span')).fontSize,
                    getComputedStyle(r.querySelector('strong b')).fontSize]; }""")
        assert sizes == (["10px", "12.5px"] if panel <= 190 else ["11px", "14px"]), (panel, sizes)
        title = await page.evaluate(f"""getComputedStyle(document.querySelector(
            '.node-tile[data-node="{SELF_ID}"] .tile-name')).textOverflow""")
        assert title == "ellipsis"
        await browser.close()


def _conversation(n=60):
    return {"items": [_record_item(i, "heard" if i % 2 else "jarvis", f"Line {i} of an earlier conversation.",
                                   at=1_000_000.0 + i) for i in range(1, n + 1)],
            "earlier": None}


@pytest.mark.asyncio
async def test_the_keyboard_opens_each_detail_and_conversation_comes_back_as_it_was(page_url):
    team = _Team()

    async def conversation(route):
        await route.fulfill(json=_conversation())

    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, routes=[("**/api/conversation", conversation)])
        await page.wait_for_selector(".conversation-history .history-turn")
        await page.evaluate("document.querySelector('.conversation-history').scrollTop = 120")
        scrolled = await page.evaluate("document.querySelector('.conversation-history').scrollTop")
        assert scrolled == 120

        # The Team Server tile, by keyboard.
        await page.focus("#team-server-tile")
        ring = await page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
        assert ring == "solid", "focus is visible"
        await page.keyboard.press("Enter")
        await page.wait_for_selector("#network-detail[data-node='server']")
        assert await page.text_content(".centre-tabs [aria-selected=true]") == "Team server"
        detail = await page.text_content("#network-detail")
        order = [detail.index(w) for w in ("Database connection", "Database ping", "Last successful write",
                                           "Last database error", "Failures in a row", "Round trip",
                                           "Last contact", "Version / commit", "Uptime", "CPU / memory")]
        assert order == sorted(order), "the database first, CPU and memory last"
        assert "1.4.0 · e609c93" in detail and "2h 14m" in detail
        assert "CPU 18% · 410 MB of 1.0 GB · container" in detail
        assert await page.locator("#network-detail .health-history polyline").count() == 1
        assert await page.locator("#conversation").count() == 1, "kept, hidden, not thrown away"
        await page.focus("#network-detail .workflow-close")
        await page.keyboard.press("Enter")
        await page.wait_for_selector("#network-detail", state="detached")
        assert await page.text_content(".centre-tabs [aria-selected=true]") == "Conversation"
        assert await page.evaluate("document.querySelector('.conversation-history').scrollTop") == 120

        # An instance's tile, by keyboard, and back by the Conversation tab.
        await _network_tab(page)
        await page.focus(f'.node-tile[data-node="{SELF_ID}"]')
        await page.keyboard.press(" ")
        await page.wait_for_selector(f"#network-detail[data-node='{SELF_ID}']")
        assert await page.get_attribute(f'.node-tile[data-node="{SELF_ID}"]', "aria-pressed") == "true"
        detail = await page.text_content("#network-detail")
        for words in ("Tim’s i9 · This Mac", "Values from Team Server", "tistocks", "JARVIS 0.9.0 · macOS 15.6",
                      "Online", "2 minutes ago", "on screen", "32% · host", "24.0 GB of 64.0 GB (38%) · host",
                      "+3.0% · 3.0 points ahead of an even pace (average of 2)", "2/2 measured",
                      "Anthropic · Brain", "at this rate 20% left at reset",
                      "OpenAI · Standby", "at this rate runs out about 12 hours before reset"):
            assert words in detail, (words, detail)
        await page.focus(".centre-tabs [aria-label='Conversation']")
        await page.keyboard.press("Enter")
        await page.wait_for_function(
            "!document.querySelector('.centre-kept').classList.contains('hidden')")
        assert await page.evaluate("document.querySelector('.conversation-history').scrollTop") == 120
        await browser.close()


@pytest.mark.asyncio
async def test_this_machine_shows_the_team_servers_values_and_keeps_them_when_it_goes_quiet(page_url):
    """The stand-in team server's figures, not this Mac's own: CPU 77%,
    human 9m, pace −2.0. Then it stops answering: the same figures, stale."""
    nodes = [_node(SELF_ID, "Tim’s i9", cpu=77.0, human=timedelta(minutes=9), pace=-2.0)]
    team, sessions = _Team(_envelope(nodes=nodes)), _Sessions()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, sessions=sessions)
        await _network_tab(page)
        mine = await page.evaluate(NODE, SELF_ID)
        assert mine["figures"][0] == "77%" and mine["human"] == "9m" and mine["figures"][2] == f"{MINUS}2.0%", mine
        team.envelope = _envelope(nodes=nodes, connection="stale", stale=True, round_trip=None,
                                  last_error="timed out")
        sessions.hint()
        await page.wait_for_selector(f'.node-tile.stale[data-node="{SELF_ID}"]')
        mine = await page.evaluate(NODE, SELF_ID)
        assert mine["figures"][0] == "77%" and mine["human"] == "9m" and mine["figures"][2] == f"{MINUS}2.0%", mine
        assert "Values are stale" in mine["label"]
        assert team.other == [], "nothing read around the team server"
        await browser.close()


@pytest.mark.asyncio
async def test_the_real_route_shows_the_stand_ins_values_and_keeps_them_stale(page_url, monkeypatch):
    """The same, end to end through the backend: a stand-in team server
    answers the node update with this machine at CPU 77%, human 9m, pace
    −2.0, unlike what this Mac's sampler sent; the page shows the stand-in's
    figures from JARVIS's own GET /api/team/network. Then the stand-in stops
    answering: the same figures, stale, and no round trip."""
    from jarvis import team_api, team_client
    from tests.test_team_client import connect, public
    from tests.test_team_network import NetworkServer

    stand_in = NetworkServer()
    snapshot = _envelope(nodes=[_node(SELF_ID, "Tim’s i9", cpu=77.0, human=timedelta(minutes=9),
                                      pace=-2.0)])["snapshot"]
    stand_in.answer = lambda: httpx.Response(200, json=snapshot)
    conn = team_client.TeamConnection(store=team_client.connection.store,
                                      transport=httpx.MockTransport(stand_in.handler), resolver=public)
    conn._node_update = team_api.node_update
    monkeypatch.setattr(team_client, "connection", conn)
    await connect(conn, stand_in)
    await conn.exchange_once()
    assert stand_in.updates[-1]["resources"].get("cpu_percent") != 77.0

    team, sessions = _RealRoute(), _Sessions()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, sessions=sessions)
        await async_api.expect(page.locator("#team-server-tile .readiness-word")).to_have_text("Ready")
        await _network_tab(page)
        mine = await page.evaluate(NODE, SELF_ID)
        assert mine["figures"][0] == "77%" and mine["human"] == "9m" and mine["figures"][2] == f"{MINUS}2.0%", mine
        assert "THIS MAC" in mine["title"].upper(), mine

        stand_in.down = True
        await conn.exchange_once()
        sessions.hint()
        await page.wait_for_selector(f'.node-tile.stale[data-node="{SELF_ID}"]')
        mine = await page.evaluate(NODE, SELF_ID)
        assert mine["figures"][0] == "77%" and mine["human"] == "9m" and mine["figures"][2] == f"{MINUS}2.0%", mine
        assert "Values are stale" in mine["label"]
        rows = {r["name"]: r for r in await page.evaluate(SERVER_ROWS)}
        assert rows["Server"]["state"] == "unknown" and rows["Server"]["figure"] == "—", rows
        assert team.other == [], "nothing read around the team server"
        await browser.close()


# The team server's subsystem row for each connection state, as
# subsystem_diagnostics.team_server words it, and the tile's word for it.
AGREE = [("connected", "ok", "Ready"), ("stale", "degraded", "No contact"),
         ("signed_out", "degraded", "Sign in"), ("connecting", "unknown", "No contact"),
         ("pairing", "idle", "Sign in"), ("disconnected", "idle", "Sign in")]


@pytest.mark.asyncio
async def test_the_subsystem_row_and_the_tile_agree(page_url):
    team, sessions = _Team(), _Sessions()
    row = {"state": "ok"}

    async def subsystems(route):
        await route.fulfill(json={"now": 1_000_000.0, "subsystems": [
            {"id": "team_server", "name": "Team server", "state": row["state"], "summary": "", "checks": []}]})

    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, sessions=sessions,
                                    routes=[("**/api/diagnostics/subsystems", subsystems)])
        await page.click(".right-tabs [aria-label='Diagnostics']")
        for connection, state, word in AGREE:
            row["state"] = state
            team.envelope = _envelope(connection=connection, stale=connection != "connected",
                                      snapshot=connection in ("connected", "stale", "signed_out"))
            sessions.ws.send(json.dumps({"type": "subsystems"}))
            sessions.hint()
            await async_api.expect(page.locator("#team-server-tile .readiness-word")).to_have_text(word)
            await page.wait_for_selector(f".subsystem-row[data-subsystem='team_server'].state-{state}")
        await browser.close()


@pytest.mark.asyncio
async def test_activity_is_reported_from_trusted_input_only_without_content(page_url):
    team = _Team()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team)
        await page.clock.install()
        await page.wait_for_timeout(300)
        assert team.activity == [], "loading the page is not a person"
        # A script's events are not a person either.
        await page.evaluate("""() => { for (const t of ['click', 'keydown', 'pointermove', 'scroll'])
                                         window.dispatchEvent(new Event(t)); }""")
        await page.wait_for_timeout(200)
        assert team.activity == []

        # Movement: the first at once, the rest of the window folded into one.
        await page.mouse.move(700, 400)
        await page.wait_for_timeout(300)
        assert [a["body"] for a in team.activity] == [{"source": "gui"}], "one report, at once"
        assert team.activity[0]["origin"], "sent from the page itself"
        await page.mouse.move(720, 420)
        await page.mouse.wheel(0, 40)
        await page.wait_for_timeout(300)
        assert len(team.activity) == 1, "movement and the wheel wait for the window"

        # A click is deliberate: sent at once, inside the window, and it
        # takes the movement waiting with it.
        await page.mouse.click(720, 420)
        await page.wait_for_timeout(300)
        assert len(team.activity) == 2, team.activity
        await page.clock.fast_forward(5000)
        await page.wait_for_timeout(300)
        assert len(team.activity) == 2, "nothing was left waiting after the click"

        # Typing is throttled like movement: one report at the end of the window.
        await page.evaluate("""() => { const i = document.createElement('input');
                                       i.id = 'probe'; document.body.append(i); }""")
        await page.focus("#probe")
        await page.keyboard.type("secret words")
        await page.wait_for_timeout(200)
        assert len(team.activity) == 3, "the first key after a quiet spell, at once"
        await page.keyboard.type(" more")
        await page.wait_for_timeout(200)
        assert len(team.activity) == 3
        await page.clock.fast_forward(5000)
        await page.wait_for_timeout(300)
        assert [a["body"] for a in team.activity] == [
            {"source": "gui"}, {"source": "gui"}, {"source": "typed"}, {"source": "typed"}], team.activity

        # Keyboard activation is deliberate: Enter in the field (with the
        # change it makes) and Space on a control are each one report, at once.
        await page.clock.fast_forward(5000)
        await page.keyboard.type("x")
        await page.wait_for_timeout(200)
        assert len(team.activity) == 5
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(200)
        assert len(team.activity) == 6, "Enter at once, as one report"
        await page.evaluate("""() => { const b = document.createElement('button');
                                       b.id = 'probe-button'; b.textContent = 'Probe'; document.body.append(b); }""")
        await page.focus("#probe-button")
        await page.keyboard.press(" ")
        await page.wait_for_timeout(200)
        assert len(team.activity) == 7, "Space on a control at once"

        # A chosen file and a changed setting are deliberate too: one report each.
        await page.evaluate("""() => { const f = document.createElement('input'); f.type = 'file';
                                       f.id = 'probe-file'; document.body.append(f);
                                       const c = document.createElement('input'); c.type = 'checkbox';
                                       c.id = 'probe-box'; document.body.append(c); }""")
        # Opening the chooser is the trusted click; Playwright's chosen-file
        # change is synthetic (isTrusted false), so it adds nothing here.
        async with page.expect_file_chooser() as chooser:
            await page.click("#probe-file")
        await (await chooser.value).set_files(files=[
            {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"private"}])
        await page.wait_for_timeout(200)
        assert len(team.activity) == 8, "file selection at once"
        await page.check("#probe-box")
        await page.wait_for_timeout(200)
        assert len(team.activity) == 9, "a changed setting at once"
        assert all(set(a["body"]) == {"source"} for a in team.activity), "where it came from, nothing else"
        assert all(a["body"]["source"] in ("gui", "typed") for a in team.activity)
        await browser.close()
