"""The Settings view, by what the user sees and does: opened from the ⋮
menu; the Fish Audio key saved (with the note to restart), and tested; the
voice id saved; name and honorific loaded and saved; the status dots green
on a good reading and not green on a failed one; and, with no Fish key set,
the first-time setup that opens by itself and walks through the key, then
the name. Then the Brains section, the menu, and both brains in Diagnostics.

The first five were written against the dialog as it was before it moved
into React, and the React view is held to the same behaviour. The one change
is where the Claude Code dot lives: on Claude's card in the Brains section.
Headless Chromium on the built page, with the endpoints answered by the test.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


def _status(fish=True):
    return {"claude_code_installed": True, "server_port": 8340, "uptime_seconds": 3725,
            "env_keys_set": {"fish_audio": fish, "fish_voice_id": True, "user_name": "Tim"}}


def _card(name, title, added, default=False, active=False, signed_in=True, checks=None):
    card = {"name": name, "title": title, "added": added, "default": default, "active": active,
            "model": {"anthropic": "sonnet", "openai": "gpt-5.6-terra"}[name]}
    if added:
        card.update(models=[card["model"], "other-model"], signs_in_here=name == "openai",
                    account={"signed_in": signed_in, "plan": "max" if name == "anthropic" else "pro",
                             "detail": "Signed in." if signed_in else "Not signed in."},
                    checks=checks or [])
    return card


CLAUDE_CHECKS = [{"name": "claude_cli", "status": "ok", "message": "claude 2.1.306 on PATH.", "remedy": None}]


class _Server:
    """The settings and brains endpoints, recording what the page posts."""

    def __init__(self, fish=True, status_ok=True, codex_added=False, codex_signed_in=False):
        self.fish, self.status_ok = fish, status_ok
        self.posts: list[tuple[str, dict]] = []
        self.prefs = {"user_name": "Tim", "honorific": "sir"}
        self.codex_added, self.codex_signed_in = codex_added, codex_signed_in
        self.default = "anthropic"
        self.language, self.spanish_installed = "en", False

    def language_status(self):
        models = [{"language": "en", "label": "English", "archiveBytes": 57_267_600,
                   "installed": True, "install": None},
                  {"language": "es", "label": "Spanish", "archiveBytes": 124_394_665,
                   "installed": self.spanish_installed, "install": None}]
        return {"language": self.language, "speech": {"language": self.language,
                "state": "ready", "failure": None, "models": models}}

    def brains(self):
        return [_card("anthropic", "Claude", True, default=self.default == "anthropic", active=True,
                      checks=CLAUDE_CHECKS),
                _card("openai", "OpenAI (Codex)", self.codex_added, default=self.default == "openai",
                      signed_in=self.codex_signed_in)]

    async def install(self, page):
        async def status(route):
            if not self.status_ok:
                await route.fulfill(status=500, body="no")
                return
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(_status(self.fish)))

        async def prefs(route):
            if route.request.method == "POST":
                body = json.loads(route.request.post_data or "{}")
                self.posts.append(("preferences", body))
                self.prefs.update(body)
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(self.prefs))

        async def keys(route):
            body = json.loads(route.request.post_data or "{}")
            self.posts.append(("keys", body))
            if body.get("key_name") == "FISH_API_KEY":
                self.fish = True
            await route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

        async def test_fish(route):
            body = json.loads(route.request.post_data or "{}")
            self.posts.append(("test-fish", body))
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps({"valid": body.get("key_value") == "good-key"}))

        async def brains(route):
            url = route.request.url
            if route.request.method == "GET":
                cards = self.brains()
                if "brief=1" in url:
                    cards = [{k: c[k] for k in ("name", "title", "added", "default", "active", "model")}
                             for c in cards]
                await route.fulfill(status=200, content_type="application/json",
                                    body=json.dumps({"brains": cards}))
                return
            body = json.loads(route.request.post_data or "{}")
            path = url.split("/api/brains")[1].split("?")[0]
            self.posts.append(("brains" + path, body))
            if path == "/openai" and body.get("action") == "add":
                self.codex_added = True
            if path == "/default":
                self.default = body.get("name")
            if path == "/openai/sign-in":
                await route.fulfill(status=200, content_type="application/json",
                                    body=json.dumps({"url": "https://auth.openai.com/oauth/authorize?x=1",
                                                     "sign_in_id": "login-1"}))
                return
            await route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

        async def language(route):
            if route.request.method == "POST":
                body = json.loads(route.request.post_data or "{}")
                self.posts.append(("language", body))
                self.language = body["language"]
            await route.fulfill(status=200, content_type="application/json",
                                body=json.dumps(self.language_status()))

        async def install(route):
            self.posts.append(("install", {}))
            self.spanish_installed = True
            await route.fulfill(status=200, content_type="application/json", body='{"model": {}}')

        await page.route("**/api/settings/language", language)
        await page.route("**/api/speech/models/es/install", install)
        await page.route("**/api/brains**", brains)
        await page.route("**/api/settings/status", status)
        await page.route("**/api/settings/preferences", prefs)
        await page.route("**/api/settings/keys", keys)
        await page.route("**/api/settings/test-fish", test_fish)
        await page.route_web_socket("**/ws/**", lambda ws: None)


async def _open(p, url, server):
    try:
        browser = await p.chromium.launch(headless=True)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await server.install(page)
    await page.goto(url)
    return browser, page


async def _open_settings(page):
    await page.click("#btn-menu")
    await page.get_by_role("button", name="Settings", exact=True).click()
    # The form is drawn before the preferences arrive, and their arrival
    # fills it; a test that types first has its typing replaced.
    await async_api.expect(page.get_by_placeholder("Your name")).to_have_value("Tim")


# A status dot's colour as seen: green, red, yellow, or off (the dim grey of
# no reading). Found by the words on its row.
DOT = """(name) => {
  const rows = [...document.querySelectorAll('.status-row, .settings-input-row, .check-row')];
  const row = rows.find(r => r.textContent.includes(name));
  if (!row) return null;
  const dot = row.querySelector('.status-dot');
  if (!dot) return null;
  const bg = getComputedStyle(dot).backgroundColor;
  return {"rgb(34, 197, 94)": "green", "rgb(239, 68, 68)": "red",
          "rgb(234, 179, 8)": "yellow"}[bg] || "off";
}"""


@pytest.mark.asyncio
async def test_settings_opens_from_the_menu_with_the_preferences_filled_in(page_url):
    server = _Server()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        assert await page.get_by_placeholder("Your name").input_value() == "Tim"
        assert await page.locator("select:has(option[value='none'])").input_value() == "sir"
        await async_api.expect(page.locator("text=Uptime").locator("..")).to_contain_text("1h")
        await browser.close()


@pytest.mark.asyncio
async def test_saving_the_fish_key_says_to_restart_and_testing_it_checks_it(page_url):
    server = _Server()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        await page.get_by_placeholder("Fish Audio key...").fill("good-key")
        await page.get_by_role("button", name="Test", exact=True).click()
        await page.wait_for_function(f"({DOT})('Test') === 'green'")
        await page.get_by_role("button", name="Save Keys").click()
        await page.get_by_text("Restart the server").wait_for()
        assert ("test-fish", {"key_value": "good-key"}) in server.posts
        assert ("keys", {"key_name": "FISH_API_KEY", "key_value": "good-key"}) in server.posts

        await page.get_by_placeholder("612b878b113047d9a770c069c8b4fdfe").fill("voice-42")
        await page.get_by_role("button", name="Save", exact=True).click()
        await page.wait_for_function("1")
        await page.wait_for_timeout(300)
        assert ("keys", {"key_name": "FISH_VOICE_ID", "key_value": "voice-42"}) in server.posts
        await browser.close()


@pytest.mark.asyncio
async def test_name_and_honorific_are_saved(page_url):
    server = _Server()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        await page.get_by_placeholder("Your name").fill("Timothy")
        await page.locator("select:has(option[value='none'])").select_option("none")
        await page.get_by_role("button", name="Save Preferences").click()
        await page.wait_for_timeout(300)
        assert ("preferences", {"user_name": "Timothy", "honorific": "none"}) in server.posts
        await browser.close()


@pytest.mark.asyncio
async def test_espanol_waits_for_its_model_then_is_chosen(page_url):
    server = _Server()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        espanol = page.locator("#settings-language option[value='es']")
        await async_api.expect(espanol).to_be_disabled()
        await page.get_by_role("button", name="Download (124 MB)").click()
        await async_api.expect(page.get_by_text("Installed", exact=True)).to_be_visible()
        await async_api.expect(espanol).to_be_enabled()
        await page.locator("#settings-language").select_option("es")
        await async_api.expect(page.locator("#settings-language")).to_have_value("es")
        assert ("install", {}) in server.posts
        assert ("language", {"language": "es"}) in server.posts
        assert await page.locator("select:has(option[value='none']) option[value='señor']").count() == 1
        await browser.close()


@pytest.mark.asyncio
async def test_the_status_dots_are_green_on_a_reading_and_not_on_a_failed_one(page_url):
    async with async_api.async_playwright() as p:
        server = _Server()
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        await page.wait_for_function(f"({DOT})('Server') === 'green'")
        await page.wait_for_function(f"({DOT})('Claude Code') === 'green'")
        await browser.close()

        server = _Server(status_ok=False)
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        await page.wait_for_function(f"({DOT})('Server') === 'red'")
        # Drawn from the answer that never came: not green. (The Claude Code
        # dot is Claude's own setup check now, read with the brains.)
        assert await page.evaluate(DOT, "Test") != "green"
        await browser.close()


@pytest.mark.asyncio
async def test_with_no_fish_key_setup_opens_by_itself_and_walks_key_then_name(page_url):
    server = _Server(fish=False)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await page.get_by_text("Let's get you set up").wait_for(timeout=10000)
        assert await page.get_by_placeholder("Fish Audio key...").is_visible()
        assert not await page.get_by_placeholder("Your name").is_visible()
        await page.get_by_role("button", name="Next: Set Your Name").click()
        assert await page.get_by_placeholder("Your name").is_visible()
        assert not await page.get_by_placeholder("Fish Audio key...").is_visible()
        await page.get_by_role("button", name="Finish Setup").click()
        await page.wait_for_timeout(600)
        assert not await page.get_by_placeholder("Your name").is_visible()
        await browser.close()


@pytest.mark.asyncio
async def test_the_menu_offers_settings_and_restart_and_no_fix_yourself(page_url):
    server = _Server()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await page.click("#btn-menu")
        items = await page.evaluate("[...document.querySelectorAll('#menu-dropdown button')].map(b => b.textContent)")
        assert items == ["Settings", "Restart Server"], items
        await page.mouse.click(700, 500)                  # anywhere else closes it
        assert await page.locator("#menu-dropdown").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_codex_is_added_signed_in_from_the_page_and_chosen_to_start_with(page_url):
    server = _Server()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await _open_settings(page)
        await page.get_by_role("button", name="+ Add OpenAI (Codex)").click()
        await page.wait_for_selector(".brain-card[data-brain='openai']")
        assert ("brains/openai", {"action": "add"}) in server.posts
        claude = page.locator(".brain-card[data-brain='anthropic']")
        assert await claude.locator(".brain-card-state").text_content() == "in use"

        async with page.context.expect_page() as opened:
            await page.get_by_role("button", name="Sign in with ChatGPT").click()
        tab = await opened.value
        assert tab.url.startswith("https://auth.openai.com/")
        await tab.close()

        # It turns on once the server has taken the choice.
        await page.locator(".brain-card[data-brain='openai'] input[type=radio]").click()
        await page.wait_for_function(
            "document.querySelector(\".brain-card[data-brain='openai'] input[type=radio]\").checked")
        assert ("brains/default", {"name": "openai"}) in server.posts
        await page.locator(".brain-card[data-brain='openai'] select").select_option("other-model")
        await page.wait_for_timeout(300)
        assert ("brains/openai", {"model": "other-model"}) in server.posts
        await browser.close()


@pytest.mark.asyncio
async def test_diagnostics_shows_both_brains_with_the_one_in_use_marked(page_url):
    server = _Server(codex_added=True, codex_signed_in=True)
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await page.wait_for_function("document.querySelectorAll('.weekly-usage').length === 2",
                                     timeout=10000)
        got = await page.evaluate("""() => [...document.querySelectorAll('.weekly-usage')].map(b => [
          b.dataset.provider, b.querySelector('.weekly-role')?.textContent])""")
        assert got == [["anthropic", "BRAIN"], ["openai", "STANDBY"]], got
        await browser.close()


@pytest.mark.asyncio
async def test_with_one_brain_diagnostics_shows_one_box_and_no_label(page_url):
    server = _Server()
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, server)
        await page.wait_for_timeout(1500)
        assert await page.locator(".weekly-usage").count() == 1
        assert await page.locator(".weekly-role").count() == 0
        await browser.close()
