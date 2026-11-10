"""One brain's usage history, by what the user sees (UsageHistoryView.tsx,
usage.ts): each brain's own box in the left column opens that brain's view
alone, named in its title, never both brains together; each window is the
brain box's own burn-down over time (what is left, all left at the top, the
dashed straight line, green ahead and red behind), one continuous line
across readings with stretches of nothing measured between them, each reset
marked; the range buttons read the history again over the range chosen.

Headless Chromium on the built page; /api/brains, /api/usage/week and
/api/usage/history are answered here in the shapes the server returns.
"""

import json
import os
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_centre_nav import _open

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

# Where the screenshot of one brain's view is kept, when asked for.
SHOT = os.environ.get("JARVIS_USAGE_HISTORY_SHOT")
HOUR = 3600
WINDOW_SEC = {"five_hour": 5 * HOUR, "seven_day": 7 * 24 * HOUR}


def _series(provider, window, since, until):
    """Readings of one window: a run, a stretch with none, a reading that did
    not measure it, then a reset part way and the readings after it."""
    step = (until - since) / 40
    reset = since + 30 * step
    length = WINDOW_SEC[window]
    points = []
    for i in range(40):
        if 14 <= i < 20:
            continue                              # nothing measured here
        at = since + i * step
        resets_at = reset if i < 30 else reset + length
        # Used: on the straight line's pace, then well past it.
        start = resets_at - length
        expected = 100 * min(max((at - start) / length, 0), 1)
        used = None if i == 25 else min(100.0, round(expected * (0.6 if i < 30 else 1.8), 1))
        points.append({"at": at, "used": used, "low": used, "high": used, "resets_at": resets_at,
                       "status": "allowed", "account": None, "samples": 1})
    return {"provider": provider, "window": window, "window_sec": length,
            "label": {"five_hour": "5-hour session", "seven_day": "7-day week"}[window],
            "points": points, "resets": [reset],
            "latest": {"window": window, "used": 13.5, "resets_at": until + HOUR,
                       "observed_at": until - 60, "status": "allowed"}}


class _Server:
    def __init__(self):
        self.asked: list[dict] = []

    async def __call__(self, route):
        url = urlparse(route.request.url)
        if url.path.endswith("/api/brains"):
            body = {"brains": [{"name": "anthropic", "added": True, "active": True},
                               {"name": "openai", "added": True, "active": False}]}
        elif url.path.endswith("/api/usage/week"):
            provider = parse_qs(url.query).get("provider", ["anthropic"])[0]
            body = {"measured": False, "provider": {"service": provider, "subscription": ""}}
        elif url.path.endswith("/api/usage/history"):
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            self.asked.append(q)
            since, until = float(q["since"]), float(q["until"])
            if since == 0:
                since = until - 90 * 86400
            body = {"since": float(q["since"]), "until": until,
                    "resolution": "raw" if until - since <= 2 * 86400 else "hour",
                    "raw_keep_sec": 14 * 86400,
                    "series": [_series(q["provider"], "five_hour", since, until),
                               _series(q["provider"], "seven_day", since, until)]}
        else:
            body = {}
        await route.fulfill(content_type="application/json", body=json.dumps(body))


@pytest.mark.asyncio
async def test_each_brain_box_opens_its_own_burn_down_over_time(page_url):
    server = _Server()
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, ("**/api/brains**", server),
                                           ("**/api/usage/**", server))
        try:
            openai_box = page.locator("#brain-usage .weekly-usage[data-provider=openai]")
            await async_api.expect(openai_box).to_have_attribute("data-ui-args", '{"provider":"openai"}')
            await openai_box.click()
            view = page.locator("#usage-view")
            await async_api.expect(view.locator("h2")).to_have_text("OpenAI usage")
            await async_api.expect(view).to_have_attribute("data-provider", "openai")
            assert server.asked[-1]["provider"] == "openai"

            # The other brain's box shows that brain alone, in place of it.
            await page.locator("#brain-usage .weekly-usage[data-provider=anthropic]").click()
            await async_api.expect(view.locator("h2")).to_have_text("Anthropic usage")
            assert server.asked[-1]["provider"] == "anthropic"
            assert float(server.asked[-1]["until"]) - float(server.asked[-1]["since"]) == 86400
            charts = view.locator(".usage-series")
            await async_api.expect(charts).to_have_count(2)
            assert {await c.get_attribute("data-series") for c in await charts.all()} == {
                "anthropic:five_hour", "anthropic:seven_day"}

            first = charts.first
            await async_api.expect(first.locator("h4")).to_have_text("5-hour session")
            # What is left, as on the box: 100% minus the 13.5% used.
            await async_api.expect(first.locator(".usage-left")).to_have_text("87% left")
            # One line across the stretch with nothing measured and the
            # reading that measured nothing: one line per window, the second
            # joined to the first across the reset.
            lines = first.locator("polyline.weekly-actual")
            assert await lines.count() == 2
            assert len((await lines.nth(0).get_attribute("points")).split()) == 23
            assert len((await lines.nth(1).get_attribute("points")).split()) == 11
            # The week: ahead of its straight line before the reset, behind
            # it after (the readings use it faster than the line).
            week = charts.nth(1).locator("polyline.weekly-actual")
            assert "ahead" in await week.nth(0).get_attribute("class")
            assert "behind" in await week.nth(1).get_attribute("class")
            assert await first.locator(".weekly-ideal").count() == 2
            assert await first.locator(".usage-reset").count() == 1
            # The axis reads as what is left: all of it at the top.
            labels = [await t.text_content() for t in await first.locator(".usage-grid text").all()]
            assert labels == ["100%", "50%", "0%"]
            tops = [float(await g.get_attribute("y1")) for g in await first.locator(".usage-grid line").all()]
            assert tops[0] < tops[2]

            # Hovering says the time and what was left.
            svg = first.locator("svg")
            bounds = await svg.bounding_box()
            await page.mouse.move(bounds["x"] + bounds["width"] * 0.3, bounds["y"] + bounds["height"] / 2)
            await async_api.expect(first.locator(".usage-tooltip")).to_contain_text("left")

            if SHOT:
                await page.screenshot(path=SHOT)

            # The month reads the history again, by the hour.
            await view.locator(".usage-ranges [data-range=month]").click()
            await async_api.expect(view.locator(".usage-ranges [data-range=month]")).to_have_attribute(
                "aria-pressed", "true")
            await async_api.expect(view.locator(".subsystem-head-state")).to_have_text(
                "One point an hour: the least left in it.")
            assert float(server.asked[-1]["until"]) - float(server.asked[-1]["since"]) == 30 * 86400
            assert server.asked[-1]["provider"] == "anthropic"

            # The brain's request closes it, back to Conversation.
            assert (await voice.navigate("usage.close", {}))["status"] == "completed"
            await async_api.expect(view).to_have_count(0)
        finally:
            await browser.close()
