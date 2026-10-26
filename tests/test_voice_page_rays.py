"""The rays: when JARVIS sends a message to an agent, a ray runs up out of the
main orb, along the top of the page, down the lane at the right edge and into
that agent's orb in the right panel's Agents tab, bringing the agent into
view first; with another tab open it ends at the Agents tab's icon, which
lights.

Headless Chromium on the built page (built by test_agents_list_fits.py's
fixture), with /api/agents and /ws/sessions replaced by Playwright.
"""

import asyncio
import json
import re

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401

AGENTS = {"agents": [{"session_id": f"s{i}", "name": f"agent {i}", "active": True,
                      "state": "idle"} for i in range(30)]}


async def _open(p, url, messages):
    try:
        browser = await p.chromium.launch(headless=True)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.route("**/api/agents", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(AGENTS)))

    def sessions(ws):
        async def later():
            await asyncio.sleep(3.0)
            for m in messages:
                ws.send(json.dumps(m))
        asyncio.ensure_future(later())
    await page.route_web_socket("**/ws/sessions", sessions)
    await page.goto(url)
    return browser, page


@pytest.mark.asyncio
async def test_the_column_leaves_the_ray_lane_clear(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [])
        geo = await page.evaluate("""() => {
          const c = document.getElementById('left-column').getBoundingClientRect();
          return {left: c.left, width: c.width, lane: innerWidth * 0.04};
        }""")
        assert abs(geo["left"] - geo["lane"]) < 1, geo        # the lane is clear
        assert geo["width"] == 230, geo
        await browser.close()


RAYS = """() => [...document.querySelectorAll('#ray-lane .ray')].map(g => ({
  d: g.querySelector('.ray-core').getAttribute('d'),
  colour: g.style.getPropertyValue('--ray-colour')}))"""


def _segments(d):
    """'M sx sy V top H cx V ey H ex' -> (sx, sy, top, cx, ey, ex); fails on
    any other shape."""
    m = re.fullmatch(r"M (\S+) (\S+) V (\S+) H (\S+) V (\S+) H (\S+)", d)
    assert m, f"not a right-angle path: {d}"
    return tuple(float(x) for x in m.groups())


@pytest.mark.asyncio
async def test_a_message_draws_a_right_angle_ray_from_the_orb_into_that_agent_s_orb(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [
            {"type": "agent_message", "session_id": "s25"}])
        await page.get_by_role("button", name="All", exact=True).click()
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=10000)
        info = await page.evaluate("""() => {
          const orb = document.getElementById('orb-control').getBoundingClientRect();
          const target = document.querySelector('.agents-list li[data-session-id="s25"] .agent-orb');
          const t = target && target.getBoundingClientRect();
          const col = document.getElementById('right-column').getBoundingClientRect();
          const tabs = document.querySelector('.centre-tabs').getBoundingClientRect();
          return {orbCentre: orb.left + orb.width / 2,
                  orbMid: orb.top + orb.height / 2, shown: !!target,
                  targetX: t && t.left + t.width / 2, targetY: t && t.top + t.height / 2,
                  laneLeft: col.right, tabsTop: tabs.top};
        }""")
        assert info["shown"], "the messaged agent was not brought into view"
        (ray,) = await page.evaluate(RAYS)
        sx, sy, top, cx, ey, ex = _segments(ray["d"])
        assert abs(sx - info["orbCentre"]) < 1 and abs(sy - info["orbMid"]) < 1, info
        assert 0 < top < info["tabsTop"], info                         # above the centre tabs
        assert info["laneLeft"] < cx < 1440, info                      # in the right-hand lane
        assert abs(ex - info["targetX"]) < 1 and abs(ey - info["targetY"]) < 1, info
        await page.wait_for_timeout(4000)
        assert await page.evaluate(RAYS), "the ray should still be showing at four seconds"
        await page.wait_for_function("!document.querySelector('#ray-lane .ray')", timeout=8000)
        await browser.close()


PRIMARY, SECOND = "#fbbf24", "#f472b6"      # amber, pink: the start of rayColours.ts's palette


async def _open_scripted(p, url, script):
    """Like _open, but the fake /ws/sessions sends each message after its own
    delay (seconds from the page loading), or once its asyncio.Event is set."""
    try:
        browser = await p.chromium.launch(headless=True)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.route("**/api/agents", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(AGENTS)))

    def sessions(ws):
        async def run():
            for delay, m in script:
                if isinstance(delay, asyncio.Event):
                    await delay.wait()
                else:
                    await asyncio.sleep(delay)
                ws.send(json.dumps(m))
        asyncio.ensure_future(run())
    await page.route_web_socket("**/ws/sessions", sessions)
    await page.goto(url)
    return browser, page


async def _until(condition, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.05)


def _to(sid):
    return {"type": "agent_message", "session_id": sid}


@pytest.mark.asyncio
async def test_rays_share_one_channel_and_take_colours_in_order(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [_to(s) for s in ("s0", "s1", "s2")])
        await page.get_by_role("button", name="All", exact=True).click()
        await page.wait_for_function(
            "document.querySelectorAll('#ray-lane .ray').length === 3", timeout=10000)
        rays = await page.evaluate(RAYS)
        assert len({_segments(r["d"])[3] for r in rays}) == 1       # one channel
        assert [r["colour"] for r in rays][:2] == [PRIMARY, SECOND]
        assert len({r["colour"] for r in rays}) == 3                # none shared
        await browser.close()


@pytest.mark.asyncio
async def test_the_colour_follows_the_transmission_not_the_agent(page_url):
    """The same agent messaged twice, alone each time, is the primary colour
    both times; and once the first ray has gone, a new one is primary again."""
    async with async_api.async_playwright() as p:
        browser, page = await _open_scripted(p, page_url, [
            (3.0, _to("s1")), (0.5, _to("s2")), (7.0, _to("s2"))])
        await page.get_by_role("button", name="All", exact=True).click()
        await page.wait_for_function(
            "document.querySelectorAll('#ray-lane .ray').length === 2", timeout=10000)
        assert [r["colour"] for r in await page.evaluate(RAYS)] == [PRIMARY, SECOND]
        await page.wait_for_function(
            "document.querySelectorAll('#ray-lane .ray').length === 0", timeout=10000)
        await page.wait_for_function(
            "document.querySelectorAll('#ray-lane .ray').length === 1", timeout=10000)
        assert [r["colour"] for r in await page.evaluate(RAYS)] == [PRIMARY]
        await browser.close()


@pytest.mark.asyncio
async def test_the_ray_is_behind_the_orbs(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [])
        z = await page.evaluate("""() => [getComputedStyle(document.getElementById('ray-lane')).zIndex,
                                          getComputedStyle(document.getElementById('left-column')).zIndex]""")
        assert int(z[0]) < int(z[1]), z
        await browser.close()


def _amber_pixels(png: bytes) -> int:
    """Amber pixels in the RIGHT half of the orb's box. The ray leaves the
    centre going left and shows through the orb there, so only the right
    half tells whether the orb itself took the colour."""
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    raw = img.crop((img.width // 2 + 4, 0, img.width, img.height)).tobytes()
    return sum(1 for i in range(0, len(raw), 3)
               if raw[i] > 120 and raw[i + 1] > 80 and raw[i + 2] < raw[i + 1] * 0.6)


@pytest.mark.asyncio
async def test_the_main_orb_fills_with_the_ray_s_colour_as_it_transmits(page_url):
    pytest.importorskip("PIL")
    async with async_api.async_playwright() as p:
        # Sent well after the "before" picture: one screenshot can take
        # seconds on a busy machine.
        browser, page = await _open_scripted(p, page_url, [(10.0, _to("s1"))])
        orb = page.locator("#orb-control")
        before = _amber_pixels(await orb.screenshot())
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=20000)
        await page.wait_for_timeout(1500)                       # the wave has spread
        during = _amber_pixels(await orb.screenshot())
        assert before < 10, before
        assert during > 20, (before, during)     # 0 with the orb's colour off
        await browser.close()


def _centre(png: bytes):
    """The colour at the orb box's centre: the core, which is drawn solid
    over everything. (The glow around it is white, so look at the middle.)"""
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    return img.getpixel((img.width // 2, img.height // 2))


@pytest.mark.asyncio
async def test_the_core_is_always_lit_and_takes_the_ray_s_colour(page_url):
    pytest.importorskip("PIL")
    async with async_api.async_playwright() as p:
        browser, page = await _open_scripted(p, page_url, [(10.0, _to("s1"))])
        orb = page.locator("#orb-control")
        r, g, b = _centre(await orb.screenshot(scale="device"))
        assert min(r, g, b) > 150, ("the core is not lit", (r, g, b))
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=20000)
        await page.wait_for_timeout(800)
        r, g, b = _centre(await orb.screenshot(scale="device"))
        assert r > 180 and g > 120 and b < g * 0.7, ("the core is not amber", (r, g, b))
        await browser.close()


@pytest.mark.asyncio
async def test_a_ray_follows_its_agent_when_the_list_reorders(page_url):
    """A message moves its agent up the list; the ray ends at the agent's
    orb where it is now, not where it was when the ray was drawn."""
    reordered = {"agents": [AGENTS["agents"][3]] + [a for i, a in enumerate(AGENTS["agents"])
                                                    if i != 3]}
    calls = {"n": 0}

    def agents(route):
        calls["n"] += 1
        body = AGENTS if calls["n"] == 1 else reordered
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(body))

    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.route("**/api/agents", agents)

        def sessions(ws):
            async def run():
                await asyncio.sleep(6.0)
                ws.send(json.dumps(_to("s3")))
                await asyncio.sleep(0.5)
                ws.send(json.dumps({"type": "agents"}))   # s3 now first
            asyncio.ensure_future(run())
        await page.route_web_socket("**/ws/sessions", sessions)
        await page.goto(page_url)
        await page.get_by_role("button", name="All", exact=True).click()
        await page.wait_for_function(
            "document.querySelector('.agents-list li')?.dataset.sessionId === 's3'"
            " && document.querySelector('#ray-lane .ray')", timeout=15000)
        await page.wait_for_timeout(300)
        end, target = await page.evaluate("""() => {
          const d = document.querySelector('#ray-lane .ray-core').getAttribute('d');
          const t = document.querySelector('.agents-list li[data-session-id="s3"] .agent-orb')
            .getBoundingClientRect();
          return [d, [t.left + t.width / 2, t.top + t.height / 2]];
        }""")
        _, _, _, _, ey, ex = _segments(end)
        assert abs(ex - target[0]) < 1 and abs(ey - target[1]) < 1, (end, target)
        await browser.close()



def _amber_halves(png: bytes) -> tuple[int, int, int]:
    """Amber pixels in the top half and the bottom half of the orb's box,
    leaving out a band down the middle where the ray itself runs up behind
    the orb, and the total."""
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    top = bottom = 0
    for y in range(h):
        for x in range(w):
            if abs(x - w // 2) <= 8:
                continue
            r, g, b = img.getpixel((x, y))
            if r > 120 and g > 80 and b < g * 0.6:
                if y < h // 2:
                    top += 1
                else:
                    bottom += 1
    return top, bottom, top + bottom


@pytest.mark.asyncio
async def test_transmitting_fills_the_orb_and_leans_it_toward_the_ray(page_url):
    """The ray leaves upward: while it shows, the orb takes the ray's colour
    and its particles gather at the top; afterwards it is back."""
    pytest.importorskip("PIL")
    async with async_api.async_playwright() as p:
        browser, page = await _open_scripted(p, page_url, [(10.0, _to("s1"))])
        orb = page.locator("#orb-control")
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=20000)
        await page.wait_for_timeout(2500)                     # well into the state
        top, bottom, during = _amber_halves(await orb.screenshot())
        assert during > 100, (top, bottom)
        assert top > 1.5 * bottom, (top, bottom)
        await page.wait_for_function("!document.querySelector('#ray-lane .ray')", timeout=15000)
        await page.wait_for_timeout(1500)
        assert _amber_halves(await orb.screenshot())[2] < 20, "it did not ease back"
        await browser.close()


# --- The ray only ever ends at its own agent's card ---------------------------

def _row(sid, name, state="idle"):
    return {"session_id": sid, "name": name, "active": True, "state": state, "needs": 0,
            "provider": "anthropic"}


# Two cards in one project, and more agents than the list shows: the message
# moves the target from the bottom to near the top, past the rows showing.
_FILL = [_row(f"x{i}", f"other {i}") for i in range(20)]
_BEFORE = {"agents": [_row("diag", "Diagnostics", "working"), _row("claude", "Agent Guard")]
           + _FILL + [_row("codex", "agent-guard, the approval authority one")]}
_AFTER = {"agents": [_BEFORE["agents"][0], _BEFORE["agents"][-1], _BEFORE["agents"][1]] + _FILL}

# Every frame's answer to: which card does the ray end at? None when no ray.
_RAY_ENDS_AT = """() => {
  const core = document.querySelector('#ray-lane .ray-core');
  if (!core) return null;
  const n = core.getAttribute('d').match(/-?[\\d.]+/g).map(Number);
  const [ex, ey] = [n[n.length - 1], n[n.length - 2]];
  const li = [...document.querySelectorAll('.agents-list li')].find(li => {
    const b = li.querySelector('.agent-orb').getBoundingClientRect();
    return ex >= b.left && ex <= b.right && ey >= b.top && ey <= b.bottom; });
  return li ? li.dataset.sessionId : 'no card';
}"""


async def _open_moving(p, url, list_delay_ms):
    """The All view of _BEFORE; six seconds in, the server says the list
    changed (answered with _AFTER, `list_delay_ms` late) and that a message
    went to the codex card, in that order, as server.py sends them."""
    calls = {"n": 0}

    async def agents(route):
        calls["n"] += 1
        if calls["n"] > 1 and list_delay_ms:
            await asyncio.sleep(list_delay_ms / 1000)
        await route.fulfill(status=200, content_type="application/json",
                            body=json.dumps(_BEFORE if calls["n"] == 1 else _AFTER))

    try:
        browser = await p.chromium.launch(headless=True)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(
        "try { localStorage.setItem('jarvis.agents.show', 'all') } catch (e) {}")
    await page.route("**/api/agents", agents)

    def sessions(ws):
        async def run():
            await asyncio.sleep(6.0)
            ws.send(json.dumps({"type": "agents"}))
            ws.send(json.dumps(_to("codex")))
        asyncio.ensure_future(run())
    await page.route_web_socket("**/ws/sessions", sessions)
    await page.goto(url)
    return browser, page


@pytest.mark.parametrize("list_delay_ms", [0, 300, 1500])
@pytest.mark.asyncio
async def test_the_ray_ends_only_at_its_own_card_while_the_list_moves(page_url, list_delay_ms):
    async with async_api.async_playwright() as p:
        browser, page = await _open_moving(p, page_url, list_delay_ms)
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=15000)
        # Every 20ms for the ray's six seconds; a frame with no ray is fine,
        # one ending at any other card is not.
        ends = []
        for _ in range(300):
            ends.append(await page.evaluate(_RAY_ENDS_AT))
            await page.wait_for_timeout(20)
        drawn = [end for end in ends if end is not None]
        assert drawn and set(drawn) == {"codex"}, ends
        await browser.close()


@pytest.mark.asyncio
async def test_a_ray_whose_card_is_moved_out_of_view_is_not_drawn_at_another(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open_moving(p, page_url, 0)
        await page.wait_for_function(
            "document.querySelector('#ray-lane .ray')"
            " && document.querySelector('.agents-list li')?.dataset.sessionId", timeout=15000)
        await page.wait_for_timeout(300)
        # He moves the list on, past the card the ray goes to.
        for _ in range(6):
            await page.get_by_title("More agents").click()
        assert not await page.evaluate(
            "!!document.querySelector('.agents-list li[data-session-id=\"codex\"]')")
        assert await page.evaluate(_RAY_ENDS_AT) is None
        await browser.close()


# --- The message arriving at the agent ----------------------------------------

_AGENT_ORB = '.agents-list li[data-session-id="s1"] .agent-orb'


def _warm_pixels(png: bytes) -> int:
    """Pixels in the ray's amber (or any warm colour) in an agent's orb,
    leaving out a band across the middle, where the ray runs in behind the
    orb to its centre. The orb's own colours are all blue."""
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    return sum(1 for y in range(h) if abs(y - h // 2) > max(3, h // 8)
               for x in range(w)
               if (lambda r, g, b: r > 90 and r > b + 40)(*img.getpixel((x, y))))


@pytest.mark.asyncio
async def test_the_agent_s_orb_takes_the_ray_s_colour_when_the_first_pulse_arrives(page_url):
    """Not when the ray appears: when its first pulse has run the length of
    it (RAY_TRAVEL_MS)."""
    pytest.importorskip("PIL")
    async with async_api.async_playwright() as p:
        browser, page = await _open_scripted(p, page_url, [(10.0, _to("s1"))])
        orb = page.locator(_AGENT_ORB)
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=20000)
        on_arrival = _warm_pixels(await orb.screenshot(scale="device"))
        await page.wait_for_timeout(1500)                # arrived, and the wave is in
        received = _warm_pixels(await orb.screenshot(scale="device"))
        assert on_arrival < 5, on_arrival
        assert received > 30, (on_arrival, received)     # 0 with the receipt off
        await page.wait_for_function("!document.querySelector('#ray-lane .ray')", timeout=15000)
        await page.wait_for_timeout(1500)
        assert _warm_pixels(await orb.screenshot(scale="device")) < 5, "it did not ease back"
        await browser.close()


@pytest.mark.asyncio
async def test_two_rings_ripple_out_from_the_agent_s_orb_as_the_message_arrives(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open_scripted(p, page_url, [(4.0, _to("s1"))])
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=20000)
        rings, centre = await page.evaluate("""(sel) => {
          const t = document.querySelector(sel).getBoundingClientRect();
          return [[...document.querySelectorAll('#ray-lane .ray-receipt')].map(c => ({
                     x: +c.getAttribute('cx'), y: +c.getAttribute('cy'),
                     delay: getComputedStyle(c).animationDelay,
                     stroke: getComputedStyle(c).stroke })),
                  [t.left + t.width / 2, t.top + t.height / 2]];
        }""", _AGENT_ORB)
        assert [r["delay"] for r in rings] == ["1.4s", "1.75s"], rings
        for r in rings:
            assert abs(r["x"] - centre[0]) < 1 and abs(r["y"] - centre[1]) < 1, (r, centre)
            assert r["stroke"] == "rgb(251, 191, 36)", r          # the ray's amber
        await browser.close()


# --- With another tab open in the right panel ---------------------------------

@pytest.mark.asyncio
async def test_with_another_tab_open_the_ray_ends_at_the_lit_agents_tab(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [_to("s2")])
        await page.click(".right-tabs [aria-label='Diagnostics']")
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=10000)
        (ray,) = await page.evaluate(RAYS)
        sx, sy, top, cx, ey, ex = _segments(ray["d"])
        icon = await page.evaluate("""() => { const b = document.querySelector(
          ".right-tabs [aria-label='Agents']").getBoundingClientRect();
          return [b.left + b.width / 2, b.top + b.height / 2]; }""")
        assert abs(ex - icon[0]) < 1 and abs(ey - icon[1]) < 1 and cx == ex, (ray, icon)
        tab = page.locator(".right-tabs [aria-label='Agents']")
        await page.wait_for_function(
            "document.querySelector(\".right-tabs [aria-label='Agents']\").classList.contains('lit')",
            timeout=5000)
        await tab.click()
        assert "lit" not in (await tab.get_attribute("class")), "seen once opened"
        await browser.close()


@pytest.mark.asyncio
async def test_hints_still_arrive_with_the_agents_tab_closed(page_url):
    """The sessions socket is the page's, not the agents list's: with the
    Workflows tab open, an "agents" hint still reaches the list the next
    time it opens, and a "workflows" hint reads the workflows again."""
    calls = {"workflows": 0}
    hint = asyncio.Event()

    async def workflows(route):
        calls["workflows"] += 1
        await route.fulfill(status=200, content_type="application/json",
                            body=json.dumps({"workflows": []}))

    async with async_api.async_playwright() as p:
        browser, page = await _open_scripted(p, page_url, [(hint, {"type": "workflows"})])
        await page.route("**/api/workflows", workflows)
        await page.click(".right-tabs [aria-label='Workflows']")
        # The hint is sent only once the view has made its first read, so the
        # read it causes cannot be mistaken for that one on a slow machine.
        await _until(lambda: calls["workflows"] > 0)
        await page.wait_for_timeout(500)
        before = calls["workflows"]
        hint.set()
        await _until(lambda: calls["workflows"] > before)
        assert await page.locator(".agents-list").count() == 0
        await page.click(".right-tabs [aria-label='Agents']")
        await page.wait_for_selector(".agents-list li")
        await browser.close()
