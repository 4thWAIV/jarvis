"""The agents list never runs past the bottom of the page.

It once did, twice. The first time the row count was fixed at five. The
second time it was measured, but only when the control first loaded and
when the window was resized: changing the row size from 44px to 66px reached
the open page through Vite's hot reload without either, so the old count was
drawn at the new size and ran off the bottom.

So this builds the real page, opens it headless at several window heights
with more agents than fit, and checks every row is inside the page and
inside the list's space. Then it changes the row size on the open page,
with no reload and no resize, and checks again.

Needs node (to build the page) and Playwright's headless Chromium; skips
itself when either is missing. Headless, and served from a temporary folder
on loopback, so it touches neither the network nor the screen.
"""

import json

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

async def _browser(p):
    try:
        return await p.chromium.launch(headless=True)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")


# Rows inside the page, inside the list's space, whole, and the down chevron
# inside the page too.
LAYOUT = """() => {
  const space = document.querySelector('.agents-space').getBoundingClientRect();
  const rows = [...document.querySelectorAll('.agents-list li')].map(li => li.getBoundingClientRect());
  const down = document.querySelectorAll('.agents-chevron')[1].getBoundingClientRect();
  return {page: innerHeight, spaceBottom: space.bottom, downBottom: down.bottom,
          rows: rows.length, rowHeight: rows.length ? rows[0].height : 0,
          lastRowBottom: rows.length ? rows[rows.length - 1].bottom : 0};
}"""


# The same checks as `_assert_fits`, in the page, so a test can wait for
# the list to settle after a change instead of guessing how long that takes
# (a page busy starting WebGL orbs re-measures a little later).
FITS = """() => {
  const space = document.querySelector('.agents-space').getBoundingClientRect();
  const rows = [...document.querySelectorAll('.agents-list li')];
  const down = document.querySelectorAll('.agents-chevron')[1].getBoundingClientRect();
  if (!rows.length) return false;
  const last = rows[rows.length - 1].getBoundingClientRect();
  return last.bottom <= space.bottom + 0.5 && down.bottom <= innerHeight + 0.5
      && space.bottom <= innerHeight + 0.5;
}"""


async def _settled(page):
    """The layout once the list fits, or as it stands after five seconds."""
    try:
        await page.wait_for_function(FITS, timeout=5000)
    except Exception:
        pass                    # the assertion that follows says what is wrong
    await page.wait_for_timeout(100)
    return await page.evaluate(LAYOUT)


def _assert_fits(layout):
    assert layout["rows"] >= 1, layout
    assert layout["lastRowBottom"] <= layout["spaceBottom"] + 0.5, layout
    assert layout["downBottom"] <= layout["page"] + 0.5, layout
    assert layout["spaceBottom"] <= layout["page"] + 0.5, layout


async def _open(browser, url, height):
    page = await browser.new_page(viewport={"width": 1280, "height": height})
    await page.goto(url)
    await page.get_by_role("button", name="All", exact=True).click()
    await page.wait_for_function("document.querySelectorAll('.agents-list li').length > 0")
    await page.wait_for_timeout(300)
    return page


# The async API: the suite's own async fixtures already run an event loop,
# and Playwright's sync API refuses to start another inside it.
async_api = pytest.importorskip("playwright.async_api")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("height", [500, 650, 900, 1200])
async def test_the_list_fits_the_page_at_every_height(page_playwright, page_url, height):
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open(browser, page_url, height)
        _assert_fits(await _settled(page))
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_resized_window_is_measured_again(page_playwright, page_url):
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open(browser, page_url, 1100)
        tall = await page.evaluate(LAYOUT)
        await page.set_viewport_size({"width": 1280, "height": 600})
        short = await _settled(page)
        _assert_fits(short)
        assert short["rows"] < tall["rows"]
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("row", ["99px", "30px"])
async def test_a_new_row_size_on_the_open_page_is_measured_again(page_playwright, page_url, row):
    """What Vite's hot reload does to the CSS, without a reload or a resize."""
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open(browser, page_url, 900)
        before = await page.evaluate(LAYOUT)
        await page.add_style_tag(content=f".agents-list {{ --agent-row: {row}; }}")
        after = await _settled(page)
        _assert_fits(after)
        assert after["rowHeight"] == float(row[:-2])
        assert after["rows"] != before["rows"]
        await browser.close()


# --- What each agent's orb shows -------------------------------------------

LOOKS = {"agents": [
    {"session_id": "a", "name": "off idle", "active": False, "state": "idle"},
    {"session_id": "b", "name": "off working", "active": False, "state": "working"},
    {"session_id": "c", "name": "on idle", "active": True, "state": "idle"},
    {"session_id": "d", "name": "on working", "active": True, "state": "working"},
    {"session_id": "e", "name": "on needs you", "active": True, "state": "needs_you"},
]}

ORB_LOOKS = """() => Object.fromEntries([...document.querySelectorAll('.agents-list li')].map(li =>
  [li.querySelector('.agent-name').textContent,
   [li.querySelector('.agent-orb').dataset.orbGray, li.querySelector('.agent-orb').dataset.orbState]]))"""


async def _open_with(browser, url, agents):
    page = await browser.new_page(viewport={"width": 1280, "height": 1000})
    await page.route("**/api/agents", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(agents)))
    return await _open_routed(page, url)


async def _open_routed(page, url):
    await page.goto(url)
    await page.get_by_role("button", name="All", exact=True).click()
    await page.wait_for_function("document.querySelectorAll('.agents-list li').length > 0")
    await page.wait_for_timeout(1500)       # the orbs settle into their colours
    return page


@pytest.mark.asyncio(loop_scope="session")
async def test_inactive_is_gray_idle_is_idle_and_every_other_state_is_thinking(page_playwright, page_url):
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open_with(browser, page_url, LOOKS)
        assert await page.evaluate(ORB_LOOKS) == {
            "off idle": ["true", "idle"],
            "off working": ["true", "thinking"],
            "on idle": ["false", "idle"],
            "on working": ["false", "thinking"],
            "on needs you": ["false", "thinking"],
        }
        await browser.close()


def _colourfulness(png: bytes) -> float:
    """The most saturated lit pixel's spread between its strongest and
    weakest channel. Gray (and white) is 0."""
    import io
    from PIL import Image
    raw = Image.open(io.BytesIO(png)).convert("RGB").tobytes()
    best = 0
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        if max(r, g, b) > 60:
            best = max(best, max(r, g, b) - min(r, g, b))
    return best


@pytest.mark.asyncio(loop_scope="session")
async def test_a_gray_orb_is_drawn_without_colour_and_an_active_one_with_it(page_playwright, page_url):
    pytest.importorskip("PIL")
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open_with(browser, page_url, LOOKS)
        orbs = page.locator(".agents-list li .agent-orb")
        gray = await orbs.nth(0).screenshot()
        active = await orbs.nth(2).screenshot()
        assert _colourfulness(gray) <= 12, "an inactive agent's orb has colour"
        assert _colourfulness(active) > 40, "an active agent's orb is not coloured"
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_switch_shows_only_in_all_and_active_entries_fill_the_row(page_playwright, page_url):
    look = """() => [...document.querySelectorAll('.agents-list li')].map(li => ({
        switch: !!li.querySelector('.agent-switch'),
        fills: Math.abs(li.querySelector('.agent-entry').getBoundingClientRect().width
                        - li.getBoundingClientRect().width) < 1}))"""
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open_with(browser, page_url, LOOKS)          # opens on All
        assert all(row["switch"] for row in await page.evaluate(look))
        await page.get_by_role("button", name="Active", exact=True).click()
        await page.wait_for_timeout(300)
        active = await page.evaluate(look)
        assert len(active) == 3
        assert all(not row["switch"] and row["fills"] for row in active), active
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_switch_stands_upright_with_on_at_the_top(page_playwright, page_url):
    look = """() => [...document.querySelectorAll('.agents-list li')].map(li => {
        const sw = li.querySelector('.agent-switch');
        const r = sw.getBoundingClientRect();
        const knob = parseFloat(getComputedStyle(sw, '::after').top);
        return [sw.getAttribute('aria-checked'), r.height > r.width, knob < r.height / 2];
    })"""
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open_with(browser, page_url, LOOKS)          # opens on All
        await page.wait_for_timeout(300)                            # the knob's slide
        rows = await page.evaluate(look)
        assert rows and all(upright for _on, upright, _top in rows), rows
        assert all((on == "true") == at_top for on, _upright, at_top in rows), rows
        await browser.close()


# --- The needs-board count ----------------------------------------------------

NEEDS = {"agents": [
    {"session_id": "a", "name": "with three", "active": True, "state": "idle", "needs": 3,
     "provider": "anthropic"},
    {"session_id": "b", "name": "with none", "active": True, "state": "idle", "needs": 0,
     "provider": "openai"},
    {"session_id": "c", "name": "unknown", "active": True, "state": "idle", "needs": 0,
     "provider": None},
]}


@pytest.mark.asyncio(loop_scope="session")
async def test_the_needs_count_is_a_bare_number_in_the_card_s_upper_left(page_playwright, page_url):
    """The rows mirror the old left-side ones: the orb at the right end, so
    the count sits in the upper left, clear of the right-aligned name."""
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open_with(browser, page_url, NEEDS)
        got = await page.evaluate("""() => [...document.querySelectorAll('.agents-list li')].map(li => {
          const box = li.querySelector('.agent-entry').getBoundingClientRect();
          const n = li.querySelector('.agent-needs');
          const name = li.querySelector('.agent-name').getBoundingClientRect();
          const r = n.getBoundingClientRect();
          return [li.querySelector('.agent-name').textContent, n.textContent,
                  r.left >= box.left && r.left - box.left < 10,
                  r.top >= box.top && r.top - box.top < 6, name.left >= r.right];
        })""")
        assert got == [["with three", "3", True, True, True], ["with none", "0", True, True, True],
                       ["unknown", "0", True, True, True]]
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_provider_logo_sits_in_the_card_s_lower_left(page_playwright, page_url):
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open_with(browser, page_url, NEEDS)
        await page.wait_for_function(
            "[...document.querySelectorAll('.agent-provider')].every(i => i.complete)")
        got = await page.evaluate("""() => [...document.querySelectorAll('.agents-list li')].map(li => {
          const box = li.querySelector('.agent-entry').getBoundingClientRect();
          const img = li.querySelector('.agent-provider');
          if (!img) return [li.querySelector('.agent-name').textContent, null];
          const r = img.getBoundingClientRect();
          return [li.querySelector('.agent-name').textContent, img.getAttribute('src'),
                  r.left >= box.left && r.left - box.left < 8,
                  r.bottom <= box.bottom && box.bottom - r.bottom < 8,
                  r.width];
        })""")
        # 14px: the brain's logo on the orb (18px) a little smaller.
        assert got == [["with three", "/providers/anthropic.svg", True, True, 14],
                       ["with none", "/providers/openai.svg", True, True, 14],
                       ["unknown", None]], got
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_list_stops_at_its_ends_and_each_arrow_shows_only_when_there_is_more(page_playwright, page_url):
    many = {"agents": [{"session_id": str(i), "name": f"agent {i}", "active": True,
                        "state": "idle", "needs": 0, "provider": None} for i in range(12)]}
    state = """() => ({
      names: [...document.querySelectorAll('.agents-list .agent-name')].map(e => e.textContent),
      arrows: [...document.querySelectorAll('.agents-chevron')].map(b => b.style.visibility)})"""
    async with page_playwright as p:
        browser = await _browser(p)
        page = await _open_with(browser, page_url, many)
        top = await page.evaluate(state)
        rows = len(top["names"])
        assert 0 < rows < 12, top
        assert top["names"][0] == "agent 0" and top["arrows"] == ["hidden", "visible"], top

        await page.click(".agents-chevron >> nth=0", force=True)    # up at the top: nothing
        assert (await page.evaluate(state))["names"] == top["names"]

        for _ in range(20):                                          # past the end
            if (await page.evaluate(state))["arrows"][1] == "hidden":
                break
            await page.click(".agents-chevron >> nth=1")
        bottom = await page.evaluate(state)
        assert bottom["names"][-1] == "agent 11" and len(bottom["names"]) == rows, bottom
        assert bottom["arrows"] == ["visible", "hidden"], bottom
        assert "agent 0" not in bottom["names"], "it does not wrap around"

        await page.mouse.move(150, 600)
        await page.mouse.wheel(0, 400)                               # a gesture past the end
        await page.wait_for_timeout(200)
        assert (await page.evaluate(state))["names"] == bottom["names"]
        await browser.close()



async def show_diagnostics(page):
    """The right panel opens on Agents; the Diagnostics tab is one click."""
    await page.click(".right-tabs [aria-label='Diagnostics']")
