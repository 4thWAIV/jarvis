"""The sign-in reminder at the top of the centre area (TeamSignInReminder.tsx,
teamSignIn.ts), by what the user sees: shown while this JARVIS is not signed
in to a team server, below the tabs and covering nothing; Later hides it;
gone by itself once signed in; Sign in starts Settings, Team's own flow with
the saved address, or opens Settings at Team without one. The Team Server
tile says Sign in whenever the reminder is up.

Headless Chromium on the built page, with /api/team* answered by the test.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_network import _envelope, _open, _Sessions, _Team

pytestmark = pytest.mark.page

TEXT = "Sign in to your team server: messages between people and the Network view need it"
ADDRESS = "https://team.example"
APPROVAL = ADDRESS + "/device?code=BCDF-GHJK"
NOT_PAIRED = lambda: _envelope(connection="disconnected", snapshot=False,  # noqa: E731
                               stale=False, round_trip=None)
SIGNED_OUT = lambda: _envelope(connection="signed_out", stale=True, round_trip=None)  # noqa: E731


class _Status:
    """GET /api/team and POST /api/team/connect: team_client's snapshot."""

    def __init__(self, state="signed_out", last_server_url=ADDRESS):
        self.team = {"state": state, "server_url": last_server_url, "last_server_url": last_server_url,
                     "server_id": None, "team": None, "me": None, "session_expires_at": None,
                     "last_contact_at": None, "last_error": None, "poll_interval_sec": 30,
                     "pairing": None}
        self.posts: list[tuple[str, dict]] = []

    def routes(self):
        async def status(route):
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(self.team))

        async def connect(route):
            body = json.loads(route.request.post_data or "{}")
            self.posts.append(("connect", body))
            self.team = dict(self.team, state="pairing", server_url=body["server_url"], pairing={
                "user_code": "BCDF-GHJK", "verification_uri_complete": APPROVAL,
                "expires_at": "2099-01-01T00:00:00+00:00"})
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(self.team))

        return [("**/api/team", status), ("**/api/team/connect", connect)]


async def _top(page, selector: str) -> float:
    return await page.locator(selector).evaluate("e => e.getBoundingClientRect().top")


@pytest.mark.asyncio
async def test_shown_while_signed_out_covers_nothing_and_later_hides_it(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, _Team(SIGNED_OUT), routes=_Status().routes())
        reminder = page.locator("#team-reminder")
        await async_api.expect(reminder).to_contain_text(TEXT)
        await async_api.expect(reminder.get_by_role("button", name="Sign in")).to_be_visible()
        await async_api.expect(page.locator("#team-server-tile .readiness-word")).to_have_text("Sign in")
        # The conversation, its input included, starts below the reminder.
        box = await reminder.bounding_box()
        conversation = await _top(page, "#conversation")
        assert conversation >= box["y"] + box["height"], (box, conversation)
        typed = await page.locator("#conversation .hearing-input").bounding_box()
        assert typed["y"] >= box["y"] + box["height"]

        await reminder.get_by_role("button", name="Later").click()
        await async_api.expect(reminder).to_have_count(0)
        # The room it took is given back.
        assert await _top(page, "#conversation") < conversation
        # Still signed out: the tile keeps saying so.
        await async_api.expect(page.locator("#team-server-tile .readiness-word")).to_have_text("Sign in")
        await browser.close()


@pytest.mark.asyncio
async def test_gone_by_itself_once_signed_in(page_url):
    team, sessions = _Team(NOT_PAIRED), _Sessions()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, team, sessions=sessions, routes=_Status().routes())
        reminder = page.locator("#team-reminder")
        await async_api.expect(reminder).to_contain_text(TEXT)
        team.envelope = _envelope
        sessions.hint()
        await async_api.expect(page.locator("#team-server-tile .readiness-word")).to_have_text("Ready")
        await async_api.expect(reminder).to_have_count(0)
        await browser.close()


@pytest.mark.asyncio
async def test_sign_in_with_a_saved_address_runs_the_team_sections_flow(page_url):
    status = _Status(state="signed_out", last_server_url=ADDRESS)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, _Team(SIGNED_OUT), routes=status.routes())
        await page.context.route(ADDRESS + "/**", lambda route: route.fulfill(
            status=200, content_type="text/html", body="<p>approve</p>"))
        async with page.context.expect_page() as popup:
            await page.locator("#team-reminder").get_by_role("button", name="Sign in").click()
        assert (await popup.value).url == APPROVAL
        assert status.posts == [("connect", {"server_url": ADDRESS})]
        # Settings opens at Team, showing the code to check the approval page against.
        team = page.locator("#settings-team")
        await async_api.expect(team.locator(".team-code")).to_have_text("BCDF-GHJK")
        await async_api.expect(team).to_be_in_viewport()
        await browser.close()


@pytest.mark.asyncio
async def test_sign_in_with_no_saved_address_opens_settings_at_team(page_url):
    status = _Status(state="disconnected", last_server_url=None)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, _Team(NOT_PAIRED), routes=status.routes())
        await page.locator("#team-reminder").get_by_role("button", name="Sign in").click()
        team = page.locator("#settings-team")
        await async_api.expect(team.get_by_placeholder("https://team.example.com")).to_be_visible()
        await async_api.expect(team).to_be_in_viewport()
        assert status.posts == []
        await browser.close()
