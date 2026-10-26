"""The voice page's layout: the footer is always at the bottom across the
full width, nothing else on the page overlaps it, and the right-hand panel
and the left column are both exactly 230 pixels wide and end at the same
place, with the caption box centred between them.

Headless Chromium on the built page (built by test_agents_list_fits.py's
fixture), at several window sizes.
"""

import pytest

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

LAYOUT = """() => {
  const footer = document.getElementById('footer').getBoundingClientRect();
  // Everything visible on the page that is not the footer, the full-page
  // ray layer, or a closed menu.
  const over = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (el.closest('#footer') || el.closest('#ray-lane')) continue;
    const css = getComputedStyle(el);
    if (css.display === 'none' || css.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.bottom > footer.top + 0.5) over.push((el.id || el.className || el.tagName) + ':' + r.bottom);
  }
  const orb = document.getElementById('orb-control').getBoundingClientRect();
  const panel = document.querySelector('.right-panel').getBoundingClientRect();
  const left = document.getElementById('left-column').getBoundingClientRect();
  const right = document.getElementById('right-column').getBoundingClientRect();
  // The centre view's box and the stack it ends: the captions in Main, the
  // white box in Conversation.
  const centre = document.getElementById('centre-bottom') || document.getElementById('conversation');
  const caption = (document.getElementById('caption-box')
    || document.querySelector('.conversation-hearing')).getBoundingClientRect();
  return {footerBottom: footer.bottom, footerWidth: footer.width, page: [innerWidth, innerHeight],
          over, orbWidth: orb.width, leftWidth: left.width, panelWidth: panel.width,
          gapLeft: caption.left - left.right, gapRight: right.left - caption.right,
          leftBottom: left.bottom, panelBottom: panel.bottom,
          captionBottom: centre.getBoundingClientRect().bottom,
          lastThing: Math.max(...[...document.querySelectorAll('.agents-view *')]
            .map(e => e.getBoundingClientRect().bottom))};
}"""


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["", "?view=main"])
@pytest.mark.parametrize("size", [(1440, 900), (1280, 650), (1920, 1080)])
async def test_the_footer_is_at_the_bottom_and_nothing_overlaps_it(page_url, size, view):
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": size[0], "height": size[1]})
        await page.goto(page_url + view)
        await page.get_by_role("button", name="All", exact=True).click()
        await page.wait_for_timeout(1500)
        geo = await page.evaluate(LAYOUT)
        assert abs(geo["footerBottom"] - size[1]) < 0.5, geo
        assert abs(geo["footerWidth"] - size[0]) < 0.5, geo
        assert geo["over"] == [], geo["over"]
        # The two side panels: the left column and the right panel.
        assert geo["leftWidth"] == 230 and geo["panelWidth"] == 230, geo
        assert geo["gapLeft"] > 0 and abs(geo["gapLeft"] - geo["gapRight"]) < 0.5, geo
        # The left column ends flush with the right panel; the agents list,
        # in the right panel's Agents tab, fills the panel to its bottom.
        # (The left column has room under the mic now: the agents moved.)
        assert abs(geo["leftBottom"] - geo["panelBottom"]) < 0.5, geo
        # The Main view's stack ends level with them; the Conversation view's
        # sits at the top, under the header.
        if view:
            assert abs(geo["captionBottom"] - geo["panelBottom"]) < 0.5, geo
        # As many whole rows as fit: what is left under the list is less
        # than another row (60px) and its gap.
        assert 0 <= geo["panelBottom"] - geo["lastThing"] < 68, geo
        await browser.close()


ROWS_1440, ROWS_1920 = 10, 12


@pytest.mark.asyncio
@pytest.mark.parametrize("size, rows", [((1440, 900), ROWS_1440), ((1920, 1080), ROWS_1920)])
async def test_the_agents_list_fills_the_right_panel_and_both_columns_end_together(
        page_url, size, rows):
    """In the right panel's Agents tab the list has the panel's whole height
    below the tabs: more rows than it had under the orb in the left column
    (6 at 1440x900, 8 at 1920x1080)."""
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": size[0], "height": size[1]})
        await page.goto(page_url)
        await page.get_by_role("button", name="All", exact=True).click()
        await page.wait_for_timeout(1500)
        geo = await page.evaluate("""() => ({
          rows: document.querySelectorAll('.agents-list li').length,
          left: document.getElementById('left-column').getBoundingClientRect(),
          right: document.getElementById('right-column').getBoundingClientRect(),
          footerTop: document.getElementById('footer').getBoundingClientRect().top})""")
        assert geo["rows"] == rows, geo["rows"]
        assert geo["left"]["bottom"] <= geo["footerTop"], geo
        assert geo["right"]["bottom"] <= geo["footerTop"], geo
        await browser.close()



@pytest.mark.asyncio
async def test_neither_side_panel_draws_a_box(page_url):
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(page_url)
        looks = await page.evaluate("""() => ['#left-column', '.right-panel'].map(s => {
          const css = getComputedStyle(document.querySelector(s));
          return [css.borderTopColor, css.backgroundColor]; })""")
        clear = "rgba(0, 0, 0, 0)"
        assert looks == [[clear, clear], [clear, clear]], looks
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider, shown", [("anthropic", True), (None, False)])
async def test_the_active_brain_s_logo_sits_in_the_orb_s_lower_left_corner(
        page_url, provider, shown):
    import json
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        # The running brain comes from the brain list (brains.ts).
        cards = [{"name": "anthropic", "title": "Claude", "added": True, "default": True,
                  "active": provider == "anthropic", "model": "sonnet"}]
        await page.route("**/api/brains**", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"brains": cards})))
        await page.goto(page_url)
        if shown:
            await page.wait_for_function(
                "document.getElementById('brain-logo')?.naturalWidth > 0", timeout=10000)
        else:
            await page.wait_for_timeout(1500)
        geo = await page.evaluate("""() => {
          const logo = document.getElementById('brain-logo');
          if (!logo) return null;
          const l = logo.getBoundingClientRect(), o = document.getElementById('orb-control').getBoundingClientRect();
          return {src: logo.getAttribute('src'), loaded: logo.naturalWidth > 0,
                  left: l.left - o.left, bottom: o.bottom - l.bottom};
        }""")
        if not shown:
            assert geo is None
        else:
            assert geo["src"] == "/providers/anthropic.svg" and geo["loaded"], geo
            assert abs(geo["left"]) < 0.5 and abs(geo["bottom"]) < 0.5, geo    # tight in the lower left
        await browser.close()
