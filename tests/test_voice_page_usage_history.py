"""The brains' usage history, by what the user sees (UsageHistoryView.tsx,
usage.ts): a brain's box in the left column opens it in the centre, with a
chart per brain and window, the latest reading, each reset marked, and a gap
where nothing was measured, never a line through zero; the range buttons
read the history again over the range chosen.

Headless Chromium on the built page; /api/usage is answered here in the
shapes usage_store.week and usage_history.read return.
"""

import json
import os
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_centre_nav import _open

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

# Where the screenshot of the view is kept, when asked for.
SHOT = os.environ.get("JARVIS_USAGE_HISTORY_SHOT")


def _series(window, since, until, resolution):
    """A run of readings, a stretch with none (a gap), a reading that did
    not measure the window, then a reset and the readings after it."""
    span = until - since
    step = span / 40
    points = []
    for i in range(40):
        at = since + i * step
        if 14 <= i < 20:
            continue                              # nothing measured here
        used = None if i == 25 else (i * 2.0 if i < 30 else (i - 30) * 1.5)
        points.append({"at": at, "used": used, "low": used, "high": used,
                       "resets_at": since + 30 * step if i < 30 else until + 3600,
                       "status": "allowed", "account": None, "samples": 1})
    return {"provider": "anthropic", "window": window,
            "label": {"five_hour": "5-hour session", "seven_day": "7-day week"}[window],
            "points": points, "resets": [since + 30 * step],
            "latest": {"window": window, "used": 13.5, "resets_at": until + 3600,
                       "observed_at": until - 60, "status": "allowed"}}


class _Usage:
    def __init__(self):
        self.asked: list[dict] = []

    async def __call__(self, route):
        url = urlparse(route.request.url)
        if url.path.endswith("/api/usage/week"):
            body = {"measured": False, "provider": {"service": "anthropic", "subscription": "max"}}
        elif url.path.endswith("/api/usage/history"):
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            self.asked.append(q)
            since, until = float(q["since"]), float(q["until"])
            if since == 0:
                since = until - 90 * 86400
            resolution = "raw" if until - since <= 2 * 86400 else "hour"
            # Two steps apart is still a line; the missing stretch is a gap.
            body = {"since": float(q["since"]), "until": until, "resolution": resolution,
                    "gap_sec": (until - since) / 40 * 2,
                    "raw_keep_sec": 14 * 86400,
                    "series": [_series("five_hour", since, until, resolution),
                               _series("seven_day", since, until, resolution)]}
        else:
            body = {}
        await route.fulfill(content_type="application/json", body=json.dumps(body))


@pytest.mark.asyncio
async def test_the_brain_box_opens_the_history_and_the_chart_shows_gaps_and_resets(page_url):
    usage = _Usage()
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, ("**/api/usage/**", usage))
        try:
            box = page.locator("#brain-usage .weekly-usage").first
            await async_api.expect(box).to_have_attribute("data-ui-command", "usage.open")
            await box.click()
            view = page.locator("#usage-view")
            await async_api.expect(view.locator("h2")).to_have_text("Usage history")
            charts = view.locator(".usage-series")
            await async_api.expect(charts).to_have_count(2)
            first = charts.first
            await async_api.expect(first.locator("h4")).to_have_text("5-hour session")
            await async_api.expect(first.locator(".usage-latest")).to_contain_text("Latest: 14% used")
            # Broken where nothing was measured and at the reset: four runs,
            # never one line through zero.
            assert await first.locator(".usage-line").count() == 4
            assert await first.locator(".usage-reset").count() == 1
            assert await first.locator("[aria-pressed=true]").count() == 0
            assert usage.asked[-1]["until"] and float(usage.asked[-1]["until"]) - float(usage.asked[-1]["since"]) == 86400

            # Hovering says the time and the reading.
            svg = first.locator("svg")
            bounds = await svg.bounding_box()
            await page.mouse.move(bounds["x"] + bounds["width"] * 0.3, bounds["y"] + bounds["height"] / 2)
            await async_api.expect(first.locator(".usage-tooltip")).to_be_visible()

            if SHOT:
                await page.screenshot(path=SHOT)

            # The month reads the history again, by the hour.
            await view.locator(".usage-ranges [data-range=month]").click()
            await async_api.expect(view.locator(".usage-ranges [data-range=month]")).to_have_attribute(
                "aria-pressed", "true")
            await async_api.expect(view.locator(".subsystem-head-state")).to_have_text(
                "One point an hour: the highest reading in it.")
            assert float(usage.asked[-1]["until"]) - float(usage.asked[-1]["since"]) == 30 * 86400

            # The brain's request closes it, back to Conversation.
            assert (await voice.navigate("usage.close", {}))["status"] == "completed"
            await async_api.expect(view).to_have_count(0)
        finally:
            await browser.close()
