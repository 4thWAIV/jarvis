"""The Settings view's Team section (TeamSection.tsx), by what the user sees:
connecting shows the pairing code and opens the approval page; connected
shows the team, who you are and the last contact; an administrator's member
list draws names as text, never markup; Disconnect says what happened.

Headless Chromium on the built page, with /api/team* answered by the test.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_settings import _Server, _open, _open_settings

pytestmark = pytest.mark.page

PAIRING = {"user_code": "BCDF-GHJK", "verification_uri_complete": "https://team.example/device?code=BCDF-GHJK",
           "expires_at": "2099-01-01T00:00:00+00:00"}
BASE = {"server_url": None, "last_server_url": None, "server_id": None, "team": None, "me": None, "session_expires_at": None,
        "recent_auth_until": None, "last_contact_at": None, "last_error": None,
        "poll_interval_sec": 30, "pairing": None}
CONNECTED = dict(BASE, state="connected", server_url="https://team.example", server_id="s",
                 team={"name": "Tokens for Cash", "server_id": "s", "member_count": 2, "member_limit": 100},
                 me={"github_id": 1, "login": "tistocks", "role": "admin", "state": "active"},
                 last_contact_at="2026-10-28T10:00:00+00:00")


class _TeamServer(_Server):
    def __init__(self):
        super().__init__()
        self.team = dict(BASE, state="disconnected")
        self.team_posts: list[tuple[str, dict]] = []

    async def install(self, page):
        await super().install(page)

        async def team(route):
            req = route.request
            path = req.url.split("/api/team")[1]
            body = json.loads(req.post_data or "{}") if req.method != "GET" else {}
            if req.method != "GET":
                self.team_posts.append((req.method + " " + path, body))
            if path == "/connect":
                self.team = dict(BASE, state="pairing", server_url=body["server_url"], pairing=PAIRING)
            elif path == "/disconnect":
                self.team = dict(BASE, state="disconnected")
                await route.fulfill(status=200, content_type="application/json", body=json.dumps(
                    {"remote": "revoked", "message": "Disconnected and signed out on the server.",
                     "team": self.team}))
                return
            elif path == "/members":
                await route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "revision": 4, "member_limit": 100, "members": [
                        {"github_id": 1, "login": "tistocks", "role": "admin", "state": "active"},
                        {"github_id": 2, "login": "<b>bold</b>", "role": "member", "state": "approved"}]}))
                return
            elif path.startswith("/members/"):
                await route.fulfill(status=403, content_type="application/json", body=json.dumps(
                    {"error": {"code": "reauth_required", "message": "Confirm with GitHub again."}}))
                return
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(self.team))

        await page.route("**/api/team**", team)


@pytest.mark.asyncio
async def test_connecting_shows_the_code_and_opens_the_approval_page(page_url):
    server = _TeamServer()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        # The approval page is the team server's; answered here, offline.
        await page.context.route("https://team.example/**", lambda route: route.fulfill(
            status=200, content_type="text/html", body="<p>approve</p>"))
        await page.get_by_placeholder("https://team.example.com").fill("https://team.example")
        async with page.context.expect_page() as popup:
            await page.get_by_role("button", name="Sign in with GitHub").click()
        assert (await popup.value).url.startswith("https://team.example/device?code=BCDF-GHJK")
        await async_api.expect(page.locator(".team-code")).to_have_text("BCDF-GHJK")
        # Read by eye against the team server's page: light text, not the
        # panel's dark default.
        colour = await page.evaluate("getComputedStyle(document.querySelector('.team-code')).color")
        assert colour == "rgb(248, 250, 252)", colour
        assert ("POST /connect", {"server_url": "https://team.example"}) in server.team_posts
        await browser.close()


@pytest.mark.asyncio
async def test_after_a_code_expires_the_address_is_offered_again(page_url):
    server = _TeamServer()
    server.team = dict(BASE, state="disconnected", last_server_url="https://team.example",
                       last_error="The pairing code expired. Start again.")
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        await async_api.expect(page.get_by_placeholder("https://team.example.com")).to_have_value(
            "https://team.example")
        await async_api.expect(page.get_by_role("button", name="Sign in with GitHub")).to_be_enabled()
        await browser.close()


@pytest.mark.asyncio
async def test_connected_admin_sees_team_members_as_text_and_can_disconnect(page_url):
    server = _TeamServer()
    server.team = CONNECTED
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        team = page.locator("#settings-team")
        await async_api.expect(team).to_contain_text("Tokens for Cash (2 of 100)")
        await async_api.expect(team).to_contain_text("tistocks (administrator)")
        await async_api.expect(team).to_contain_text("Connected")
        await page.get_by_role("button", name="Manage members").click()
        await async_api.expect(team.locator(".team-member-name").nth(1)).to_have_text("<b>bold</b>")
        assert await team.locator(".team-member b").count() == 0
        # Text with no colour of its own is light, not the browser's black.
        name = await team.locator(".team-member-name").first.evaluate("e => getComputedStyle(e).color")
        assert name == "rgba(255, 255, 255, 0.85)", name
        await team.get_by_role("button", name="Make administrator").click()
        await async_api.expect(team.get_by_role("button", name="Confirm with GitHub")).to_be_visible()
        await team.get_by_role("button", name="Disconnect").click()
        await async_api.expect(team).to_contain_text("Disconnected and signed out on the server.")
        await async_api.expect(team.get_by_placeholder("https://team.example.com")).to_be_visible()
        await browser.close()
