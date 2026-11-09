"""The voice page loads quiet and listens only on his word (the voice control
contract, sections 5 and 6): a fresh load or a reload asks for no
microphone and opens no recogniser; Muted and Mic off survive a reload
without a microphone being opened to reproduce them; storage that cannot be
read, or settings not yet read, never mean listening. His two settings
decide what a stop and leaving the page do, all four ways, and late words
from a stopped capture are never sent. Mute and Mic off reach every tab,
and nothing from another tab or a retry starts listening.

Headless Chromium on the built page with Chromium's fake microphone, the
fake transcription socket, a fake /ws/voice, and the settings route
answered by the test.
"""

import json

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_speech_input import COUNT_MICROPHONES, Voice
from tests.fixtures.waiting import until_async
from tests.test_voice_page_packages import jarvis_app  # noqa: F401  (the fixture)
from tests.voice_page_input import LISTEN, route_settings, start_listening

SILENCED = "document.querySelector('#mic-silenced')?.textContent ?? null"
STATE = "document.querySelector('#mic-state')?.textContent ?? null"
QUIET_KEY = "jarvis.voice.quiet"
# The page can be told it is hidden or shown again.
VISIBILITY = """
(() => {
  window.__visibility = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => window.__visibility });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => window.__visibility === "hidden" });
  window.__setVisibility = (v) => { window.__visibility = v; document.dispatchEvent(new Event("visibilitychange")); };
})();
"""
# Drop the finish message, so a stopped session can never finish.
NEVER_FINISH = """() => { for (const t of window.__transcribers) {
  const send = t.send.bind(t);
  t.send = (d) => { if (typeof d === "string" && JSON.parse(d).type === "finish") return; send(d); };
} }"""


async def _browser(p):
    try:
        return await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")


async def _page(context, url, *, stop="keep", switch="stop", settings_status=200, script=None,
                storage=None, view="", sounds=True, init=(), **settings):
    page = await context.new_page()
    # Routed before the fake transcription socket is installed: Playwright's
    # socket routing replaces window.WebSocket with its own relay, and routed
    # after the fake it would wrap it, so a message a test gives a fake
    # transcriber would reach Playwright's relay, never the page's handler.
    voice = Voice()
    await page.route_web_socket("**/ws/voice", voice)
    if script:
        await page.add_init_script(f"window.__transcribe = {json.dumps(script)};")
    if storage is not None:
        await page.add_init_script(storage)
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.add_init_script(COUNT_MICROPHONES)
    await page.add_init_script(VISIBILITY)
    for extra in init:
        await page.add_init_script(extra)
    posted = await route_settings(page, stop=stop, switch=switch, status=settings_status,
                                  sounds=sounds, **settings)
    await page.route("**/api/conversation", lambda route: route.fulfill(json={"items": [], "earlier": None}))
    await page.goto(url + view)
    page.posted, page.voice = posted, voice
    return page


async def _listening(page):
    await start_listening(page)
    await page.wait_for_function(
        "window.__transcribers.some((t) => t.readyState === 1) && window.__framesSent > 0", timeout=10000)


async def _quiet_since_load(page, ms=1500):
    """Nothing asked for and nothing opened since the page loaded."""
    await page.wait_for_timeout(ms)
    assert await page.evaluate("window.__micOpens") == 0
    assert await page.evaluate("window.__transcribers.length") == 0


async def _shows(page, what, want):
    """Wait for the page to say `want` in `what` (STATE or SILENCED): read
    straight after a click or a reload it can still be drawing."""
    try:
        await page.wait_for_function(f"({what}) === {json.dumps(want)}", timeout=15000)
    except async_api.Error:
        pass
    assert await page.evaluate(what) == want


def _sent(voice, kind):
    return [m for m in voice.sent if m.get("type") == kind]


# ── quiet reload (rows 1 to 4) ───────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_a_fresh_load_and_a_reload_of_a_listening_page_ask_for_no_microphone(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url)
        await _quiet_since_load(page)
        await _shows(page, STATE, "Not listening")
        await async_api.expect(page.locator(LISTEN)).to_have_attribute("aria-label", "Start listening")

        await _listening(page)
        await _shows(page, STATE, "Listening")
        starts = await page.evaluate("window.__starts")
        assert starts[0]["claim"] is True and starts[0]["clientId"].startswith("page-")
        await page.reload()
        await _quiet_since_load(page)
        await _shows(page, STATE, "Not listening")
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_muted_and_off_survive_a_reload_without_a_microphone(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url)
        await _listening(page)
        await page.click("#btn-mute")
        await async_api.expect(page.locator("#btn-mute")).to_have_attribute("aria-pressed", "true")
        await page.reload()
        await _quiet_since_load(page)
        await _shows(page, SILENCED, "Muted · microphone inactive")
        await async_api.expect(page.locator("#btn-mute")).to_have_attribute("aria-pressed", "true")

        # Unmuting is the deliberate enable: one microphone, then listening.
        await page.click("#btn-mute")
        await page.wait_for_function("window.__micOpens === 1 && window.__framesSent > 0", timeout=10000)

        await page.click("#btn-mic-off")
        await page.reload()
        await _quiet_since_load(page)
        await _shows(page, SILENCED, "Mic off")
        await async_api.expect(page.locator("#btn-mic-off")).to_have_attribute("aria-pressed", "true")
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("storage", [
    f"localStorage.setItem({json.dumps(QUIET_KEY)}, 'listening');",
    "Storage.prototype.getItem = () => { throw new Error('denied'); };",
])
async def test_unreadable_or_unknown_storage_is_not_listening(page_playwright, page_url, storage):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, storage=storage)
        await _quiet_since_load(page)
        await _shows(page, STATE, "Not listening")
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_that_cannot_be_read_keep_start_listening_unavailable(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, settings_status=503)
        await page.wait_for_selector(LISTEN)
        assert await page.is_disabled(LISTEN)
        await page.click(LISTEN, force=True)
        # Unmuting from Mic off is refused too: no grant without the settings.
        await page.click("#btn-mic-off")
        await page.click("#btn-mic-off")
        await _quiet_since_load(page)
        await context.close()


# ── the four combinations ────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("switch", ["continue", "stop"])
async def test_mic_off_under_keep_finishes_the_words_from_before_and_releases_at_once(
        page_playwright, page_url, switch):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, stop="keep", switch=switch,
                           script={"finalOnFinish": "turn off the lights"})
        await _listening(page)
        await page.evaluate("window.__hear('turn off the', false)")
        await page.click("#btn-mic-off")
        await page.wait_for_function("window.__liveTracks() === 0", timeout=15000)
        assert await until_async(page.voice.finals)
        assert page.voice.finals() == ["turn off the lights"]
        assert _sent(page.voice, "turn_discard") == []
        await _shows(page, SILENCED, "Mic off")
        after = await page.evaluate("window.__framesSent")
        await page.wait_for_timeout(1000)
        assert await page.evaluate("window.__framesSent") == after
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("switch", ["continue", "stop"])
async def test_mic_off_under_discard_sends_nothing_and_late_words_stay_dropped(
        page_playwright, page_url, switch):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, stop="discard", switch=switch,
                           script={"finalOnFinish": "drop the database"})
        await _listening(page)
        await page.evaluate("window.__hear('drop the', false)")
        await page.click("#btn-mic-off")
        await page.wait_for_function("window.__liveTracks() === 0", timeout=15000)
        assert await until_async(lambda: _sent(page.voice, "turn_discard"))
        # A final from the stopped capture arrives late: never sent.
        await page.evaluate("""() => { const old = window.__transcribers[0];
          old.onmessage && old.onmessage({ data: JSON.stringify(
            { session: old.session, type: 'final', segment: 9, text: 'drop the database' }) }); }""")
        await page.wait_for_timeout(800)
        assert page.voice.finals() == []
        await _shows(page, SILENCED, "Mic off")
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("stop", ["keep", "discard"])
async def test_leaving_the_page_under_continue_keeps_listening(page_playwright, page_url, stop):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, stop=stop, switch="continue")
        await _listening(page)
        await page.evaluate("window.__setVisibility('hidden')")
        before = await page.evaluate("window.__framesSent")
        await page.wait_for_timeout(1000)
        assert await page.evaluate("window.__framesSent") > before
        assert await page.evaluate("window.__liveTracks()") == 1
        assert _sent(page.voice, "turn_discard") == []
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("stop", ["keep", "discard"])
async def test_leaving_the_page_under_stop_ends_listening_and_coming_back_does_not_restart_it(
        page_playwright, page_url, stop):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, stop=stop, switch="stop",
                           script={"finalOnFinish": "and the kitchen"})
        await _listening(page)
        await page.evaluate("window.__hear('lights in the hall', true)")
        await page.evaluate("window.__hear('and the', false)")
        await page.evaluate("window.__setVisibility('hidden')")
        await page.wait_for_function("window.__liveTracks() === 0", timeout=15000)
        if stop == "keep":
            assert await until_async(lambda: "and the kitchen" in page.voice.finals())
            assert _sent(page.voice, "turn_discard") == []
        else:
            assert await until_async(lambda: _sent(page.voice, "turn_discard"))
            await page.wait_for_timeout(500)
            assert "and the kitchen" not in page.voice.finals()
        # The preference is unchanged: not listening, not muted, not off.
        await page.wait_for_function(f"{STATE} === 'Not listening'", timeout=15000)
        opens = await page.evaluate("window.__micOpens")
        sockets = await page.evaluate("window.__transcribers.length")
        await page.evaluate("window.__setVisibility('visible')")
        await page.wait_for_timeout(1500)
        assert await page.evaluate("window.__micOpens") == opens
        assert await page.evaluate("window.__transcribers.length") == sockets
        await _shows(page, STATE, "Not listening")
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_stopped_capture_that_cannot_finish_leaves_its_words_as_an_unsent_draft(
        page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url, stop="keep")
        await _listening(page)
        await page.evaluate(NEVER_FINISH)
        await page.click("#btn-mic-off")
        # Heard from before the stop, after it: held, not sent.
        await page.evaluate("window.__hear('book the table', true)")
        await page.wait_for_timeout(1000)
        assert page.voice.finals() == []
        await page.wait_for_function(
            "document.querySelector('#conversation-draft')?.value === 'book the table'", timeout=20000)
        assert page.voice.finals() == []
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_hidden_or_shown_never_arms_a_quiet_page(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, switch="continue")
        for _ in range(2):
            await page.evaluate("window.__setVisibility('hidden')")
            await page.evaluate("window.__setVisibility('visible')")
        await _quiet_since_load(page, 800)
        await context.close()


# ── settings, tabs, ownership and retries ────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_changing_a_setting_while_listening_starts_stops_and_sends_nothing(page_playwright, page_url):
    """Settings, Voice and language: the change is saved and applies at the
    next stop; the listening session goes on untouched."""
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url, stop="keep", switch="stop")
        await _listening(page)
        await page.evaluate("window.__hear('half a', false)")
        await page.click("#btn-menu")
        await page.get_by_role("button", name="Settings", exact=True).click()
        await async_api.expect(page.locator("#settings-voice-stop")).to_have_value("keep")
        await async_api.expect(page.locator("#settings-voice-switch")).to_have_value("stop")
        await page.locator("#settings-voice-stop").select_option("discard")
        assert await until_async(lambda: page.posted == [{"stop": "discard"}])
        await page.wait_for_timeout(500)
        assert await page.evaluate("window.__transcribers.length") == 1
        assert await page.evaluate("window.__liveTracks()") == 1
        assert page.voice.finals() == [] and _sent(page.voice, "turn_discard") == []

        # The next stop follows the new setting. Mic off is on the
        # Conversation view, under Settings: closing Settings stops nothing.
        await page.click("#settings-view .settings-head .workflow-close")
        await page.click("#btn-mic-off")
        assert await until_async(lambda: _sent(page.voice, "turn_discard"))
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_mute_and_off_reach_every_tab_but_unmuting_elsewhere_never_starts_this_one(
        page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        a = await _page(context, page_url)
        b = await _page(context, page_url)
        await _listening(a)
        await b.click("#btn-mute")
        await a.wait_for_function(f"{SILENCED} === 'Muted'", timeout=15000)
        after = await a.evaluate("window.__framesSent")
        await a.wait_for_timeout(800)
        assert await a.evaluate("window.__framesSent") == after

        # B unmutes: A lets go and waits for its own Start.
        await b.click("#btn-mute")
        await a.wait_for_function(f"{STATE} === 'Not listening'", timeout=15000)
        await a.wait_for_function("window.__liveTracks() === 0", timeout=15000)

        await b.click("#btn-mic-off")
        await a.wait_for_function(f"{SILENCED} === 'Mic off'", timeout=15000)
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_page_whose_listening_is_taken_lets_go_and_does_not_take_it_back(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, stop="discard")
        await _listening(page)
        await page.evaluate("""() => { const t = window.__transcribers[0];
          t._emit({ type: 'error', code: 'taken', message: 'Listening moved.' }); }""")
        await page.wait_for_function("window.__liveTracks() === 0", timeout=15000)
        assert await until_async(lambda: _sent(page.voice, "turn_discard"))
        await page.wait_for_timeout(2500)                 # longer than the first retry
        assert await page.evaluate("window.__transcribers.length") == 1
        await async_api.expect(page.locator(LISTEN)).to_have_attribute("aria-label", "Start listening")

        # His Start here takes it back, claiming it.
        await _listening(page)
        starts = await page.evaluate("window.__starts")
        assert starts[-1]["claim"] is True and starts[-1]["captureEpoch"] > starts[0]["captureEpoch"]
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_busy_recogniser_is_not_waited_for_with_the_microphone_open(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, script={"error": {"code": "busy", "message": "busy"}})
        await start_listening(page)
        await page.wait_for_function("window.__transcribers.length === 1", timeout=10000)
        await page.wait_for_function("window.__liveTracks() === 0", timeout=15000)
        await page.wait_for_timeout(2500)
        assert await page.evaluate("window.__transcribers.length") == 1
        await _shows(page, STATE, "Busy in another tab")
        await async_api.expect(page.locator(LISTEN)).to_have_attribute("aria-label", "Listen here")
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_retry_after_a_lost_session_never_arms_a_page_that_was_stopped(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url, stop="discard")
        await _listening(page)
        # The recogniser goes away; a retry is scheduled for a second on.
        await page.evaluate("window.__transcribers[0].close()")
        await page.click("#btn-mic-off")
        await page.wait_for_timeout(2500)
        assert await page.evaluate("window.__transcribers.length") == 1
        assert await page.evaluate("window.__liveTracks()") == 0
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_retry_within_the_grant_takes_nothing_from_another_page(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url)
        await _listening(page)
        await page.evaluate("window.__transcribers[0].close()")
        await page.wait_for_function("window.__starts.length === 2", timeout=10000)
        starts = await page.evaluate("window.__starts")
        assert starts[0]["claim"] is True and starts[1]["claim"] is False
        assert starts[1]["captureEpoch"] > starts[0]["captureEpoch"]
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_typing_works_while_voice_is_quiet(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url)
        await page.click("#btn-mic-off")
        await page.fill("#conversation-draft", "status report")
        await page.press("#conversation-draft", "Enter")
        assert await until_async(lambda: [m for m in page.voice.sent
                                     if m.get("type") == "transcript" and m.get("typed")])
        await _quiet_since_load(page, 300)
        await context.close()


# ── Mute and reconnecting, as decided ────────────────────────────────────

@pytest.mark.asyncio(loop_scope="session")
async def test_mute_turns_what_was_said_into_an_unsent_draft_and_unmute_never_sends_it(
        page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url, script={"finalOnFinish": "at seven"})
        await _listening(page)
        await page.evaluate("window.__hear('book a table', true)")      # gathered, not committed
        assert await until_async(page.voice.finals)
        # The server says what it gathered as his: the page keeps only that.
        page.voice.socket.send(json.dumps({"type": "turn", "state": "gathering", "turnId": 1,
                                           "text": "book a table"}))
        await page.wait_for_function("document.querySelector('#conversation-control').dataset.state === 'turn-gathering'", timeout=15000)
        await page.evaluate("window.__hear('at', false)")
        await page.click("#btn-mute")
        await page.wait_for_function(
            "document.querySelector('#conversation-draft')?.value === 'book a table at seven'",
            timeout=15000)
        assert await until_async(lambda: _sent(page.voice, "turn_discard"))
        discard = _sent(page.voice, "turn_discard")
        assert len(discard) == 1
        assert page.voice.finals() == ["book a table"], "the finished words were sent"
        await page.click("#btn-mute")
        await page.wait_for_function("window.__starts.length === 2", timeout=10000)
        assert (await page.evaluate("window.__starts"))[1]["captureEpoch"] == discard[0]["captureEpoch"] + 1
        await page.wait_for_timeout(500)
        assert page.voice.finals() == ["book a table"]
        assert await page.evaluate("document.querySelector('#conversation-draft').value") == \
            "book a table at seven"
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_listening_page_resumes_under_a_new_epoch_once_the_voice_socket_is_back(
        page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url)
        await _listening(page)
        await page.voice.socket.close()
        await page.wait_for_function("window.__liveTracks() === 0", timeout=15000)
        await _shows(page, STATE, "Not listening")
        # The page's socket reconnects by itself (ws.ts); the route answers again.
        await page.wait_for_function("window.__starts.length === 2", timeout=20000)
        starts = await page.evaluate("window.__starts")
        assert starts[1]["claim"] is False and starts[1]["captureEpoch"] > starts[0]["captureEpoch"]
        await page.wait_for_function("window.__liveTracks() === 1", timeout=15000)
        assert await page.evaluate("window.__micOpens") == 2
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_page_that_was_not_listening_or_was_muted_does_not_resume(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context()
        page = await _page(context, page_url)
        assert await until_async(lambda: page.voice.socket is not None)
        await page.voice.socket.close()
        await page.wait_for_timeout(4000)
        await _quiet_since_load(page, 100)

        await _listening(page)
        await page.click("#btn-mute")
        await page.voice.socket.close()
        await page.wait_for_timeout(4000)
        assert await page.evaluate("window.__starts.length") == 1
        await _shows(page, SILENCED, "Muted · microphone inactive")
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_muted_or_not_listening_no_frame_reaches_a_package_listener(page_playwright, page_url, jarvis_app):
    """Mute suspends PCM to every consumer: the page package's listener as
    much as the recogniser."""
    from jarvis import packages_api
    from tests.test_voice_page_packages import _through
    client, token, package = jarvis_app
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.add_init_script(COUNT_MICROPHONES)
        await route_settings(page)
        voice = Voice()
        await page.route_web_socket("**/ws/voice", voice)
        await page.route_web_socket("**/ws/sessions", lambda ws: None)
        await page.route("**/api/packages/**", _through(client, token))
        await page.goto(page_url)
        await page.wait_for_selector(LISTEN)
        assert await until_async(lambda: voice.socket is not None)
        voice.socket.send(json.dumps({"type": "package_page", "package": packages_api.describe(package)}))
        await page.wait_for_selector("#package-view iframe")
        inner = None
        for _ in range(100):
            inner = next((f for f in page.frames if f.url.endswith("/files/index.html")), None)
            if inner:
                break
            await page.wait_for_timeout(100)
        await inner.evaluate("""() => { window.__audio = 0;
            addEventListener("message", (e) => { if (e.data?.type === "audio") window.__audio++; });
            parent.postMessage({jarvis: "package", type: "listen", on: true}, "*"); }""")
        await page.wait_for_timeout(800)
        assert await inner.evaluate("window.__audio") == 0, "heard without the grant"

        await start_listening(page)
        await inner.wait_for_function("window.__audio > 5", timeout=10000)
        await page.click("#btn-mute")
        await page.wait_for_timeout(300)
        muted = await inner.evaluate("window.__audio")
        await page.wait_for_timeout(1000)
        assert await inner.evaluate("window.__audio") == muted, "a package heard while muted"
        await page.click("#btn-mute")
        await inner.wait_for_function(f"window.__audio > {muted + 5}", timeout=10000)
        await context.close()


# ── the brain's quiet_microphone: only ever quieter (contract section 10) ─

@pytest.mark.asyncio(loop_scope="session")
async def test_the_brain_s_quiet_frame_mutes_or_turns_off_and_never_undoes_off(page_playwright, page_url):
    async with page_playwright as p:
        context = await (await _browser(p)).new_context(viewport={"width": 1440, "height": 900})
        page = await _page(context, page_url)
        await _listening(page)
        page.voice.socket.send(json.dumps({"type": "quiet", "state": "muted"}))
        await page.wait_for_function("document.querySelector('#btn-mute')?.getAttribute('aria-pressed') === 'true'",
                                     timeout=15000)
        assert await page.evaluate(f"localStorage.getItem({json.dumps(QUIET_KEY)})") == "muted"
        page.voice.socket.send(json.dumps({"type": "quiet", "state": "off"}))
        await page.wait_for_function("document.querySelector('#btn-mic-off')?.getAttribute('aria-pressed') === 'true'",
                                     timeout=15000)
        page.voice.socket.send(json.dumps({"type": "quiet", "state": "muted"}))
        page.voice.socket.send(json.dumps({"type": "quiet", "state": "none"}))
        await page.wait_for_timeout(500)
        assert await page.evaluate(f"localStorage.getItem({json.dumps(QUIET_KEY)})") == "off", \
            "a frame never makes input louder"
        await context.close()
