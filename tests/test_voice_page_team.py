"""The Settings view's Team section (TeamSection.tsx), by what the user sees:
connecting shows the pairing code and opens the approval page; connected
shows the team, who you are and the last contact; an administrator's member
list draws names as text, never markup; Disconnect says what happened.
Messages between people have no switch: signed out, one line says signing
in turns them on.

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
        "last_contact_at": None, "last_error": None,
        "poll_interval_sec": 30, "pairing": None}
CONNECTED = dict(BASE, state="connected", server_url="https://team.example", server_id="s",
                 team={"name": "Tokens for Cash", "server_id": "s", "member_count": 2, "member_limit": 100},
                 me={"github_id": 1, "login": "tistocks", "role": "admin", "state": "active"},
                 last_contact_at="2026-10-28T10:00:00+00:00")
MEMBERS = [{"github_id": 1, "login": "tistocks", "role": "admin", "state": "active"},
           {"github_id": 2, "login": "<b>bold</b>", "role": "member", "state": "approved"},
           {"github_id": 3, "login": "gone-quiet", "role": "member", "state": "suspended"}]
NEWCOMER = {"github_id": 7, "login": "newcomer", "name": "New Comer"}


class _TeamServer(_Server):
    def __init__(self):
        super().__init__()
        self.team = dict(BASE, state="disconnected")
        self.team_posts: list[tuple[str, dict]] = []
        self.revision = 4
        self.members = [dict(m) for m in MEMBERS]
        self.accepts = False    # until then every member change is refused

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
                    "revision": self.revision, "member_limit": 100, "members": self.members}))
                return
            elif path == "/members/resolve":
                await route.fulfill(status=200, content_type="application/json", body=json.dumps(
                    dict(NEWCOMER, html_url="https://github.com/newcomer", on_roster=False)))
                return
            elif path.startswith("/members/"):
                if self.accepts:
                    self.revision += 1
                    self.members.append(dict(NEWCOMER, role=body["role"], state="approved"))
                    await route.fulfill(status=200, content_type="application/json", body=json.dumps(
                        {"revision": self.revision, "member": self.members[-1]}))
                    return
                await route.fulfill(status=409, content_type="application/json", body=json.dumps(
                    {"error": {"code": "revision_conflict",
                               "message": "The roster changed; reload it and try again."}}))
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
        # The roster is there at once, with no button to press.
        await async_api.expect(team.locator(".team-member-name").nth(1)).to_have_text("<b>bold</b>")
        assert await team.locator(".team-member b").count() == 0
        # Text with no colour of its own is light, not the browser's black.
        name = await team.locator(".team-member-name").first.evaluate("e => getComputedStyle(e).color")
        assert name == "rgba(255, 255, 255, 0.85)", name
        await team.get_by_role("button", name="Make administrator").first.click()
        # Refused, it says so in that member's own row.
        refusal = team.locator(".team-member", has_text="<b>bold</b>").get_by_role("alert")
        await async_api.expect(refusal).to_have_text(
            "<b>bold</b> was not made an administrator. The roster changed; reload it and try again.")
        await team.get_by_role("button", name="Disconnect").click()
        await async_api.expect(team).to_contain_text("Disconnected and signed out on the server.")
        await async_api.expect(team.get_by_placeholder("https://team.example.com")).to_be_visible()
        await browser.close()


SIGN_IN_LINE = "Signing in to your team server turns on messages between people."


@pytest.mark.asyncio
async def test_signed_out_one_line_says_signing_in_turns_messaging_on(page_url):
    server = _TeamServer()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        team = page.locator("#settings-team")
        await async_api.expect(team).to_contain_text(SIGN_IN_LINE)
        assert await team.get_by_role("checkbox").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_signed_in_there_is_no_messaging_switch(page_url):
    server = _TeamServer()
    server.team = dict(CONNECTED, me=dict(CONNECTED["me"], role="member"))
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        team = page.locator("#settings-team")
        await async_api.expect(team).to_contain_text("Connected")
        await async_api.expect(team).not_to_contain_text(SIGN_IN_LINE)
        assert await team.get_by_role("checkbox").count() == 0
        assert not [post for post in server.team_posts if "/messaging" in post[0]]
        await browser.close()


@pytest.mark.asyncio
async def test_members_by_state_and_a_refused_add_says_nobody_was_added(page_url, tmp_path):
    server = _TeamServer()
    server.team = CONNECTED
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        team = page.locator("#settings-team")
        groups = team.locator(".team-member-group")
        await async_api.expect(groups.locator("h5")).to_have_text(
            ["Signed in (1)", "Pending (not signed in yet) (1)", "Suspended (1)"])
        pending = groups.nth(1).locator(".team-member-name")
        await async_api.expect(pending).to_have_text(["<b>bold</b>"])
        await async_api.expect(groups.nth(2).locator(".team-member-name")).to_have_text("gone-quiet")
        await async_api.expect(groups.nth(2).get_by_role("button", name="Re-enable")).to_be_visible()

        await team.get_by_placeholder("github-username").fill("newcomer")
        await team.get_by_role("button", name="Look up").click()
        confirm = team.locator(".team-confirm")
        await async_api.expect(confirm).to_contain_text("They can sign in once added.")
        await confirm.locator("select").select_option("admin")
        await confirm.get_by_role("button", name="Add", exact=True).click()
        refusal = team.locator(".team-refused")
        await async_api.expect(refusal).to_have_text(
            "newcomer was not added. The roster changed; reload it and try again.")
        await async_api.expect(confirm).not_to_contain_text("They can sign in")
        # The person and the role are kept, so Add can be pressed again.
        await async_api.expect(confirm).to_contain_text("newcomer (New Comer)")
        await async_api.expect(confirm.locator("select")).to_have_value("admin")
        await async_api.expect(pending).to_have_text(["<b>bold</b>"])
        await team.screenshot(path=str(tmp_path / "team-add-refused.png"))

        server.accepts = True
        await confirm.get_by_role("button", name="Add", exact=True).click()
        await async_api.expect(team.locator(".team-done")).to_have_text(
            "newcomer added; pending until they sign in.")
        await async_api.expect(refusal).to_have_count(0)
        await async_api.expect(pending).to_have_text(["<b>bold</b>", "newcomer — New Comer"])
        await team.screenshot(path=str(tmp_path / "team-add-pending.png"))
        assert [post for post in server.team_posts if post[0] == "PUT /members/7"] == [
            ("PUT /members/7", {"expected_revision": 4, "role": "admin"}),
            ("PUT /members/7", {"expected_revision": 4, "role": "admin"})]
        await browser.close()
