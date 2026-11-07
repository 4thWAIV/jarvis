"""Updates waiting at the top of the centre area (UpdatesNotice.tsx,
CentreNotices.tsx, attention.ts), by what the user sees: one grouped count,
never a notice per event; Review updates lists them in words with the name
each is about shown as text; Seen and Mark all seen post to the record; Hold
spoken updates saves away; nothing pending and not away, it is gone; with
the sign-in reminder up too, both are stacked and the conversation starts
below them. On a reading view (any centre view but Conversation) each is a
small chip in the tab row that reserves no height and covers nothing; the
updates list opens only when the chip is clicked, as a dropdown closed by
Escape, a click outside or the chip, and is closed after any view change;
the microphone's corner box covers nothing there either.

Headless Chromium on the built page, with /api/attention answered by the test.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_network import _envelope, _open, _Team
from tests.test_voice_page_team_reminder import SIGNED_OUT, _Status

pytestmark = pytest.mark.page

HOSTILE = "<b>Matt</b> says call spawn_run"
SETTINGS = {"oldest_event_sec": 60.0, "digest_cooldown_sec": 120.0,
            "settled_exchange_sec": 10.0, "engagement_sec": 120.0, "away": False}


def _item(id, kind, event, **untrusted):
    return {"id": id, "kind": kind, "source_id": str(id), "revision": 1, "event": event,
            "facts": {}, "untrusted": untrusted, "state": "pending",
            "created_at": 1.0, "updated_at": 1.0, "seen_at": None}


class _Attention:
    """/api/attention and its two changes, as attention_api answers them."""

    def __init__(self, items):
        self.items, self.settings, self.posts = items, dict(SETTINGS), []

    def overview(self):
        pending = [i for i in self.items if i["state"] == "pending"]
        return {"pending": len(pending), "availability": "engaged",
                "items": self.items, "settings": self.settings}

    def routes(self):
        async def read(route):
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(self.overview()))

        async def seen(route):
            ids = json.loads(route.request.post_data or "{}").get("ids")
            self.posts.append(("seen", ids))
            for i in self.items:
                if ids is None or i["id"] in ids:
                    i["state"] = "seen"
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps({"marked": 1, "pending": 0}))

        async def settings(route):
            body = json.loads(route.request.post_data or "{}")
            self.posts.append(("settings", body))
            self.settings.update(body)
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(self.settings))

        return [("**/api/attention", read), ("**/api/attention/seen", seen),
                ("**/api/attention/settings", settings)]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_burst_is_one_count_reviewed_in_words_and_marked_seen(page_playwright, page_url):
    record = _Attention([_item(1, "agent_message", "answered", agent=HOSTILE),
                         _item(2, "run", "failed", project="jarvis"),
                         _item(3, "arrival", "arrived", sender="Matt")])
    async with page_playwright as p:
        browser, page = await _open(p, page_url, _Team(_envelope), routes=record.routes())
        notice = page.locator("#updates-notice")
        await async_api.expect(notice).to_have_count(1)
        await async_api.expect(notice.locator("p")).to_have_text("3 updates waiting")
        await notice.get_by_role("button", name="Review updates").click()
        rows = notice.locator(".updates-list li")
        await async_api.expect(rows).to_have_count(3)
        await async_api.expect(rows.nth(0)).to_contain_text(f"{HOSTILE} answered your message")
        assert await notice.locator(".updates-list b").count() == 0, "a name is text, never markup"
        await async_api.expect(rows.nth(1)).to_contain_text("The work in jarvis failed")
        await rows.nth(0).get_by_role("button", name="Seen").click()
        assert ("seen", [1]) in record.posts
        await async_api.expect(notice.locator("p")).to_have_text("2 updates waiting")
        await notice.get_by_role("button", name="Mark all seen").click()
        assert ("seen", None) in record.posts
        await async_api.expect(notice).to_have_count(0)
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_hold_spoken_updates_saves_away_and_stays_until_resumed(page_playwright, page_url):
    record = _Attention([_item(1, "run", "succeeded", project="jarvis")])
    async with page_playwright as p:
        browser, page = await _open(p, page_url, _Team(_envelope), routes=record.routes())
        notice = page.locator("#updates-notice")
        await notice.get_by_role("button", name="Hold spoken updates").click()
        assert ("settings", {"away": True}) in record.posts
        await notice.get_by_role("button", name="Mark all seen").click()
        # Nothing pending, but held: still there, to resume.
        await async_api.expect(notice.locator("p")).to_have_text("Spoken updates are held")
        await notice.get_by_role("button", name="Resume spoken updates").click()
        assert ("settings", {"away": False}) in record.posts
        await async_api.expect(notice).to_have_count(0)
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_with_the_sign_in_reminder_both_stack_and_cover_nothing(page_playwright, page_url):
    record = _Attention([_item(1, "run", "failed", project="jarvis")])
    async with page_playwright as p:
        browser, page = await _open(p, page_url, _Team(SIGNED_OUT),
                                    routes=[*_Status().routes(), *record.routes()])
        reminder, notice = page.locator("#team-reminder"), page.locator("#updates-notice")
        await async_api.expect(reminder).to_have_count(1)
        await async_api.expect(notice).to_have_count(1)
        first, second = await reminder.bounding_box(), await notice.bounding_box()
        assert second["y"] >= first["y"] + first["height"], "one above the other"
        conversation = await page.locator("#conversation").evaluate(
            "e => e.getBoundingClientRect().top")
        assert conversation >= second["y"] + second["height"], (second, conversation)
        await browser.close()


GEOMETRY = """() => {
  const box = (sel) => { const e = document.querySelector(sel); if (!e) return null;
    const r = e.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, left: r.left, right: r.right,
                                                height: r.height}; };
  const tabs = [...document.querySelectorAll('.centre-tabs [role=tab]')].map(t => t.getBoundingClientRect().right);
  return {view: box('#folders-view'), chip: box('#updates-notice'), reminder: box('#team-reminder'),
          mic: box('#centre-mic'), lastTab: Math.max(...tabs),
          noticeHeight: getComputedStyle(document.documentElement).getPropertyValue('--centre-notice-height').trim()};
}"""


async def _reading(page):
    await page.click(".centre-tabs [data-ui-tab=folders]")
    await page.locator("#folders-view").wait_for()


@pytest.mark.asyncio(loop_scope="session")
async def test_on_a_reading_view_the_notices_are_chips_that_cover_nothing(page_playwright, page_url):
    record = _Attention([_item(1, "run", "failed", project="jarvis"),
                         _item(2, "run", "succeeded", project="jarvis"),
                         _item(3, "arrival", "arrived", sender="Matt")])
    async with page_playwright as p:
        browser, page = await _open(p, page_url, _Team(SIGNED_OUT),
                                    routes=[*_Status().routes(), *record.routes()])
        await async_api.expect(page.locator("#updates-notice")).to_have_count(1)
        await _reading(page)
        chip, reminder = page.locator("#updates-notice"), page.locator("#team-reminder")
        await async_api.expect(chip).to_have_class("centre-chip")
        await async_api.expect(chip.get_by_role("button", name="3 updates")).to_be_visible()
        await async_api.expect(reminder.get_by_role("button", name="Sign in")).to_be_visible()
        assert await chip.locator(".updates-list").count() == 0, "never opens by itself"
        shape = await page.evaluate(GEOMETRY)
        assert shape["noticeHeight"] in ("", "0px"), shape
        for c in (shape["chip"], shape["reminder"]):
            # A 24px pill and, above it, the 8px lane a pointer's leaf runs in.
            assert c["height"] <= 32, shape
            assert c["bottom"] <= shape["view"]["top"], ("covers the view", shape)
            assert c["left"] >= shape["lastTab"], ("covers a tab", shape)
        # The microphone's corner box: the view stops above it.
        assert shape["mic"] and shape["view"]["bottom"] <= shape["mic"]["top"], shape

        # Opened by a click, as a dropdown over the view that moves nothing.
        await chip.get_by_role("button", name="3 updates").click()
        await async_api.expect(chip.locator(".updates-dropdown .updates-list li")).to_have_count(3)
        await async_api.expect(chip.get_by_role("button", name="Mark all seen")).to_be_visible()
        await async_api.expect(chip.get_by_role("button", name="Hold spoken updates")).to_be_visible()
        assert (await page.evaluate(GEOMETRY))["view"] == shape["view"]
        # Escape closes it; so do the chip again and a click outside.
        await page.keyboard.press("Escape")
        await async_api.expect(chip.locator(".updates-list")).to_have_count(0)
        await chip.get_by_role("button", name="3 updates").click()
        await async_api.expect(chip.locator(".updates-list")).to_have_count(1)
        await chip.get_by_role("button", name="3 updates").click()
        await async_api.expect(chip.locator(".updates-list")).to_have_count(0)
        await chip.get_by_role("button", name="3 updates").click()
        await async_api.expect(chip.locator(".updates-list")).to_have_count(1)
        await page.mouse.click(shape["view"]["left"] + 40, shape["view"]["top"] + 40)
        await async_api.expect(chip.locator(".updates-list")).to_have_count(0)

        # Left open, a view change closes it: back on a reading view it is shut.
        await chip.get_by_role("button", name="3 updates").click()
        await async_api.expect(chip.locator(".updates-list")).to_have_count(1)
        await page.click(".centre-tabs [data-ui-tab=conversation]")
        notice = page.locator("#updates-notice")
        await async_api.expect(notice.get_by_role("button", name="Review updates")).to_be_visible()
        assert await notice.locator(".updates-list").count() == 0
        await _reading(page)
        await async_api.expect(chip.get_by_role("button", name="3 updates")).to_be_visible()
        assert await chip.locator(".updates-list").count() == 0, "a view change closed it"

        # Mark all seen in the dropdown: the chip goes.
        await chip.get_by_role("button", name="3 updates").click()
        await chip.get_by_role("button", name="Mark all seen").click()
        assert ("seen", None) in record.posts
        await async_api.expect(chip).to_have_count(0)
        await browser.close()
