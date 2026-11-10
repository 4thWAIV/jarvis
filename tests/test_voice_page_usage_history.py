"""One brain's weekly usage history, by what the user sees
(UsageHistoryView.tsx, usage.ts): each brain's own box in the left column
opens that brain's view alone, named in its title, never both brains
together. Only the week is shown: this week's burn-down first, drawn as the
brain's box draws it (what is left, all left at the top, the dashed
straight line, green ahead and red behind), one continuous line across a
stretch with nothing measured, the reset marked at the week's end; Earlier
week, Later week and This week move through the weeks in order. No
five-hour chart anywhere.

Headless Chromium on the built page; /api/brains, /api/usage/week and
/api/usage/history are answered here in the shapes the server returns.
"""

import json
import os
import time
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_centre_nav import _open

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

# Where the screenshot of one brain's view is kept, when asked for.
SHOT = os.environ.get("JARVIS_USAGE_HISTORY_SHOT")
HOUR = 3600
WEEK = 7 * 24 * HOUR


def _week(resets_at, pace, upto=None):
    """A week of hourly readings at `pace` times the straight line's use,
    with a stretch of nothing measured and one reading that measured nothing,
    up to `upto` (the week's end when left out)."""
    start = resets_at - WEEK
    points = []
    for h in range(0, 168, 3):
        at = start + h * HOUR
        if upto is not None and at > upto:
            break
        if 60 <= h < 84:
            continue                              # nothing measured here
        used = None if h == 99 else min(100.0, round(100 * h / 168 * pace, 1))
        points.append({"at": at, "used": used, "low": used, "high": used, "resets_at": resets_at,
                       "status": "", "account": None, "samples": 3})
    return {"resets_at": resets_at, "start": start, "points": points,
            "measured": sum(1 for p in points if p["used"] is not None)}


class _Server:
    def __init__(self):
        self.asked: list[dict] = []
        now = time.time()
        self.now = now
        self.this_reset = now + 2 * 24 * HOUR       # this week resets in two days

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
            r = self.this_reset
            weeks = [_week(r, 1.3, upto=self.now - 600),     # this week: behind the line
                     _week(r - WEEK, 0.7),                    # last week: ahead of it
                     _week(r - 2 * WEEK, 1.1)]
            body = {"provider": q["provider"], "window": "seven_day", "week_sec": WEEK,
                    "since": 0, "until": self.now, "resolution": "hour", "raw_keep_sec": 14 * 86400,
                    "latest": {"used": 80.0, "resets_at": r, "observed_at": self.now - 60, "status": "allowed"},
                    "weeks": weeks}
        else:
            body = {}
        await route.fulfill(content_type="application/json", body=json.dumps(body))


@pytest.mark.asyncio
async def test_each_brain_box_opens_its_own_weekly_burn_down_week_by_week(page_url):
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
            assert server.asked[-1] == {"provider": "openai"}

            # The other brain's box shows that brain alone, in place of it.
            await page.locator("#brain-usage .weekly-usage[data-provider=anthropic]").click()
            await async_api.expect(view.locator("h2")).to_have_text("Anthropic usage")
            assert server.asked[-1] == {"provider": "anthropic"}

            # This week first, and only the week: one chart, no five-hour one.
            name = view.locator(".usage-week-name")
            await async_api.expect(name).to_have_attribute("data-week-index", "0")
            await async_api.expect(name).to_contain_text("This week, resets")
            await async_api.expect(name).to_contain_text("week 1 of 3")
            assert await view.locator(".usage-chart svg").count() == 1
            assert "5-hour" not in await view.inner_text()
            # What is left, as on the box: 100% minus the 80% used, behind the line.
            await async_api.expect(view.locator(".usage-left")).to_have_text("20% left")
            assert "behind" in await view.locator(".usage-left").get_attribute("class")
            await async_api.expect(view.locator(".usage-latest")).to_contain_text("until it resets")

            chart = view.locator(".usage-chart svg")
            # One continuous line across the stretch with nothing measured
            # and the reading that measured nothing.
            line = chart.locator("polyline.weekly-actual")
            assert await line.count() == 1
            assert "behind" in await line.get_attribute("class")
            assert await chart.locator(".weekly-ideal").count() == 1
            assert await chart.locator(".usage-reset").count() == 1
            assert await chart.locator(".weekly-now").count() == 1
            # All left at the top, as the box draws it.
            labels = [await t.text_content() for t in await chart.locator(".usage-grid text").all()]
            assert labels == ["100%", "50%", "0%"]

            # Hovering says the time and what was left.
            bounds = await chart.bounding_box()
            await page.mouse.move(bounds["x"] + bounds["width"] * 0.3, bounds["y"] + bounds["height"] / 2)
            await async_api.expect(view.locator(".usage-tooltip")).to_contain_text("left")

            if SHOT:
                await page.screenshot(path=SHOT)

            # No later week than this one; the earlier ones in order.
            later = view.locator("[data-week=later]")
            await async_api.expect(later).to_be_disabled()
            await view.locator("[data-week=earlier]").click()
            await async_api.expect(name).to_have_attribute("data-week-index", "1")
            await async_api.expect(name).to_contain_text("Week that reset")
            await async_api.expect(view.locator(".usage-left")).to_contain_text("Ended with")
            assert "ahead" in await chart.locator("polyline.weekly-actual").get_attribute("class")
            assert await chart.locator(".weekly-now").count() == 0
            await view.locator("[data-week=earlier]").click()
            await async_api.expect(name).to_have_attribute("data-week-index", "2")
            await async_api.expect(view.locator("[data-week=earlier]")).to_be_disabled()
            # The brain's request goes back to this week.
            assert (await voice.navigate("usage.week", {"to": "current"}))["status"] == "completed"
            await async_api.expect(name).to_have_attribute("data-week-index", "0")

            # The brain's request closes it, back to Conversation.
            assert (await voice.navigate("usage.close", {}))["status"] == "completed"
            await async_api.expect(view).to_have_count(0)
        finally:
            await browser.close()
