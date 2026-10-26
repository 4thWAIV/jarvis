"""The brains' account boxes, in the left column under the microphone, two rows of the same height: the
avatar and what is left of the week's limit; then the provider's logo and the
number (green when ahead of the straight line, red when behind). To their
right, centred across both rows, a burn-down chart with the straight line and
the readings.

Headless Chromium on the built page (built by test_agents_list_fits.py's
fixture), with /api/usage/week replaced by Playwright.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api, page_url  # noqa: F401

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

WEEK = 7 * 24 * 3600


def _week(ahead, used):
    start = 1_000_000.0
    return {"measured": True, "provider": {"service": "anthropic", "subscription": "max"}, "week_start": start, "resets_at": start + WEEK,
            "observed_at": start + WEEK / 2, "age_sec": 120, "stale": False,
            "expected_used": 50.0, "used": used, "ahead": ahead,
            "points": [{"at": start + 3600, "used": 5.0},
                       {"at": start + WEEK / 2, "used": used}]}


async def _shown(page_url, body, status=200, logo=True):
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.route("**/api/usage/week", lambda route: route.fulfill(
            status=status, content_type="application/json", body=json.dumps(body)))
        # A stand-in logo file, or none (a 404) to see the slot stay empty.
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
        await page.route("**/providers/*.svg", lambda route: route.fulfill(
            status=200, content_type="image/svg+xml", body=svg) if logo else route.fulfill(
            status=404, content_type="text/plain", body=""))
        await page.goto(page_url)
        await page.wait_for_selector(".weekly-usage")
        await page.wait_for_timeout(300)
        out = await page.evaluate("""() => {
          const view = document.querySelector('.weekly-usage');
          const n = document.querySelector('.weekly-ahead');
          const chart = document.querySelector('.weekly-chart');
          const orb = document.querySelector('.agent-orb');
          const nr = n.getBoundingClientRect(), cr = chart && chart.getBoundingClientRect();
          const rows = [...view.querySelectorAll('.account-row')].map(r => {
            const b = r.getBoundingClientRect();
            return {cls: r.className, height: b.height, right: b.right,
                    kids: [...r.children].map(e => e.getAttribute('class').split(' ')[0])}; });
          const left = document.querySelector('.weekly-left');
          const leftColour = getComputedStyle(left).color;
          const rowsBox = view.querySelector('.account-rows').getBoundingClientRect();
          return {text: n.textContent.trim(), cls: n.className, colour: getComputedStyle(n).color,
                  numberHeight: nr.height, numberRight: nr.right,
                  numberFits: n.scrollWidth <= n.clientWidth,
                  chartHeight: cr ? cr.height : null, chartLeft: cr ? cr.left : null,
                  orb: orb ? orb.getBoundingClientRect().height : null,
                  ideal: !!document.querySelector('.weekly-chart .weekly-ideal'),
                  actual: chart?.querySelector('.weekly-actual')?.getAttribute('points'),
                  rows, left: left.textContent, leftColour, numberLeft: nr.left,
                  rowsTop: rowsBox.top, rowsBottom: rowsBox.bottom, rowsRight: rowsBox.right,
                  chartTop: cr ? cr.top : null, chartBottom: cr ? cr.bottom : null,
                  burn: (() => { const b = document.querySelector('.weekly-burn');
                    if (!b) return null;
                    const r = s => b.querySelector(s).getBoundingClientRect();
                    const d = b.querySelector('.weekly-days'), w = b.querySelector('.weekly-reset');
                    return {days: d.textContent, daysColour: getComputedStyle(d).color,
                            daysBottom: r('.weekly-days').bottom, daysTop: r('.weekly-days').top,
                            date: w.textContent, dateTop: r('.weekly-reset').top,
                            dateColour: getComputedStyle(w).color,
                            fonts: [d, w].map(e => getComputedStyle(e).fontFamily),
                            aligns: [d, w].map(e => getComputedStyle(e).textAlign),
                            centres: [d, w, b.querySelector('.weekly-chart')].map(e => {
                              const q = e.getBoundingClientRect(); return (q.left + q.right) / 2; }),
                            widths: [d, w].map(e => [e.scrollWidth, e.clientWidth]),
                            dateBottom: r('.weekly-reset').bottom,
                            dateSize: parseFloat(getComputedStyle(w).fontSize)}; })(),
                  textNodes: [...view.querySelectorAll('.account-rows *')].filter(e =>
                    [...e.childNodes].some(c => c.nodeType === 3 && c.textContent.trim()))
                    .map(e => e.textContent.trim()),
                  hover: view.title || n.title,
                  logo: (() => { const i = view.querySelector('.weekly-provider img');
                                 return i ? [i.getAttribute('src'), i.naturalWidth > 0] : null; })(),
                  logoTitle: view.querySelector('.weekly-provider')?.title,
                  front: [view.querySelector('.weekly-avatar'),
                          view.querySelector('.weekly-provider')].map(e => {
                    const r = e.getBoundingClientRect();
                    return [e.className, e.dataset.provider ?? null, r.width, r.height,
                            getComputedStyle(e).borderTopLeftRadius]; }),
                  fills: ['.weekly-usage', '.account-row', '.weekly-left', '.weekly-ahead']
                    .flatMap(s => [...view.querySelectorAll(s)])
                    .map(e => getComputedStyle(e).backgroundColor),
                  gapFill: chart?.querySelector('.weekly-gap')
                    ? getComputedStyle(chart.querySelector('.weekly-gap')).fill : null};
        }""")
        await browser.close()
        return out


@pytest.mark.asyncio
async def test_ahead_of_the_line_is_a_green_positive_number(page_url):
    shown = await _shown(page_url, _week(10.0, 40.0))
    assert shown["text"] == "+10.0%"
    assert shown["colour"] == shown["leftColour"] == "rgb(74, 222, 128)"   # both green
    assert shown["gapFill"] == "rgb(34, 197, 94)"            # the gap, solid green
    assert shown["ideal"] and len(shown["actual"].split()) == 3


@pytest.mark.asyncio
async def test_behind_the_line_is_a_red_negative_number(page_url):
    shown = await _shown(page_url, _week(-15.0, 65.0))
    assert shown["text"] == "-15.0%"
    assert shown["colour"] == shown["leftColour"] == "rgb(248, 113, 113)"  # both red
    assert shown["gapFill"] == "rgb(239, 68, 68)"            # the gap, solid red


@pytest.mark.asyncio
async def test_two_rows_of_the_same_height(page_url):
    shown = await _shown(page_url, _week(-88.8, 98.8))
    (available, pace) = shown["rows"]
    assert available["height"] == pace["height"] == 26, shown["rows"]
    assert available["kids"] == ["weekly-avatar", "weekly-left"]
    assert pace["kids"] == ["weekly-provider", "weekly-ahead"]


@pytest.mark.asyncio
async def test_the_first_row_shows_what_is_left_and_no_words(page_url):
    shown = await _shown(page_url, _week(10.0, 40.0))
    assert shown["left"] == "60%"
    assert shown["textNodes"] == ["60", "%", "+", "10.0", "%"], "no words, only the two figures"


@pytest.mark.asyncio
async def test_the_figures_sit_on_a_transparent_background(page_url):
    shown = await _shown(page_url, _week(10.0, 40.0))
    assert set(shown["fills"]) == {"rgba(0, 0, 0, 0)"}, shown["fills"]


@pytest.mark.asyncio
async def test_the_days_left_sit_above_the_chart_and_the_reset_day_below(page_url):
    """Within the height of the two rows: the days left until the reset on
    top in the figures' colour, the chart, the day it resets underneath."""
    import re
    import time
    body = _week(-15.0, 65.0)
    body["resets_at"] = time.time() + 3.5 * 86400
    body["week_start"] = body["resets_at"] - WEEK
    shown = await _shown(page_url, body)
    assert shown["numberFits"], "-88.8 does not fit"
    assert shown["chartLeft"] >= shown["rowsRight"], shown
    burn = shown["burn"]
    assert burn["days"] in ("3.5 days", "3.4 days"), burn
    assert burn["daysColour"] == burn["dateColour"] == shown["colour"] == "rgb(248, 113, 113)", burn
    assert all("monospace" in f for f in burn["fonts"]), burn
    assert burn["aligns"] == ["center", "center"], burn
    days, date, chart = burn["centres"]
    assert abs(days - chart) < 1 and abs(date - chart) < 1, "centred over the chart"
    assert re.fullmatch(r"(Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d{1,2} "
                        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", burn["date"]), burn
    assert burn["dateSize"] >= 12, "large enough to read"
    assert burn["daysBottom"] <= shown["chartTop"] < shown["chartBottom"] <= burn["dateTop"], shown
    assert burn["daysTop"] >= shown["rowsTop"] - 0.5 and burn["dateBottom"] <= shown["rowsBottom"] + 0.5, \
        "it fits in the box as it is"
    assert shown["chartBottom"] - shown["chartTop"] >= 20, "the chart is still readable"


@pytest.mark.asyncio
async def test_a_reset_already_past_is_no_days_left(page_url):
    shown = await _shown(page_url, _week(10.0, 40.0))
    assert shown["burn"]["days"] == "0.0 days"
    assert shown["burn"]["daysColour"] == shown["burn"]["dateColour"] == "rgb(74, 222, 128)"


@pytest.mark.asyncio
async def test_no_reading_is_a_dash(page_url):
    shown = await _shown(page_url, {"measured": False})
    assert shown["text"] == "—" and "none" in shown["cls"]
    assert shown["left"] == "—"


@pytest.mark.asyncio
async def test_a_server_without_the_route_is_a_red_dash_with_the_reason_on_hover(page_url):
    """A JARVIS not yet restarted answered 404, and the panel once claimed
    'not measured yet' for fifteen turns."""
    shown = await _shown(page_url, {"detail": "Not Found"}, status=404)
    assert shown["text"] == "—" and "behind" in shown["cls"]
    assert "restart JARVIS" in shown["hover"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ahead, shown_as", [(5.2, "+05.2%"), (0.0, "+00.0%"), (-0.4, "-00.4%"),
                                             (-15.0, "-15.0%"), (-0.04, "+00.0%"),
                                             (123.4, "+99.9%")])
async def test_every_place_is_filled(page_url, ahead, shown_as):
    shown = await _shown(page_url, _week(ahead, 50.0 - ahead))
    assert shown["text"] == shown_as


@pytest.mark.asyncio
async def test_each_row_starts_with_its_icon_at_the_row_s_height(page_url):
    shown = await _shown(page_url, _week(5.2, 44.8))
    (avatar, provider) = shown["front"]
    assert avatar[0] == "weekly-avatar" and avatar[4] == "50%"          # round
    assert provider[0] == "weekly-provider" and provider[1] == "anthropic"
    for _, _, w, h, _ in shown["front"]:
        assert abs(w - shown["numberHeight"]) < 0.5 and abs(h - shown["numberHeight"]) < 0.5


@pytest.mark.asyncio
async def test_the_logo_is_chosen_from_the_report_s_own_provider(page_url):
    shown = await _shown(page_url, _week(5.2, 44.8))
    assert shown["logo"] == ["/providers/anthropic.svg", True]
    assert shown["logoTitle"] == "Anthropic max"


@pytest.mark.asyncio
async def test_another_provider_gets_its_own_slot_and_no_anthropic_logo(page_url):
    body = {**_week(5.2, 44.8), "provider": {"service": "someone-else", "subscription": "pro"}}
    shown = await _shown(page_url, body)
    assert shown["logo"] is None
    assert shown["front"][1][1] == "someone-else"


@pytest.mark.asyncio
async def test_a_missing_logo_file_leaves_the_slot_empty_not_broken(page_url):
    shown = await _shown(page_url, _week(5.2, 44.8), logo=False)
    assert shown["logo"] is None


@pytest.mark.asyncio
async def test_each_figure_has_a_half_size_percent_and_the_sign_on_its_left(page_url):
    """Both figures are percents of the week's limit. A half-size % sits
    flush against each figure's right, its top level with the digits' top.
    The over/under figure's sign sits just left of its first digit, drawn two
    thirds as tall as the digits with its top level with theirs, and the two figures'
    first digits line up. The first figure has no sign."""
    async with async_api.async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        body = json.dumps(_week(-15.0, 65.0))
        await page.route("**/api/usage/week", lambda r: r.fulfill(
            status=200, content_type="application/json", body=body))
        await page.goto(page_url)
        await page.wait_for_selector(".weekly-ahead .weekly-sign")
        got = await page.evaluate("""() => ['.weekly-left', '.weekly-ahead'].map(s => {
          const fig = document.querySelector(s + ' .weekly-figure');
          const pct = document.querySelector(s + ' .weekly-pct');
          const sign = document.querySelector(s + ' .weekly-sign');
          const f = fig.getBoundingClientRect(), a = pct.getBoundingClientRect();
          const b = sign ? sign.getBoundingClientRect() : null;
          const size = e => parseFloat(getComputedStyle(e).fontSize);
          return {pct: size(pct) / size(fig), flush: Math.abs(a.left - f.right),
                  top: Math.abs(a.top - f.top), figLeft: f.left,
                  sign: sign ? [sign.textContent, Math.abs(b.height - f.height * 2 / 3),
                                f.left - b.right, Math.abs(b.top - f.top)] : null};
        })""")
        await browser.close()
    (left, ahead) = got
    assert left["pct"] == ahead["pct"] == 0.5, got
    assert all(g["flush"] < 0.5 and g["top"] < 0.5 for g in got), got
    assert abs(left["figLeft"] - ahead["figLeft"]) < 0.5, got
    assert left["sign"] is None, got
    text, off_size, gap, top = ahead["sign"]
    assert text == "-" and off_size < 0.5 and 0 <= gap < 3 and top < 0.5, got
