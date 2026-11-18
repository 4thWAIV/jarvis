"""A page package's presentation uses the whole display, and the focus
layer (PackageFocus.tsx) presents what needs him beside the part it is
about, by what a real browser draws:

  reading     the package opens taken over; its icon border is the only bar,
              the document takes the whole width below it with nothing over
              it, and the border fades without the pointer and comes back
              when the pointer reaches it or keyboard focus enters it; with
              reduced motion or Steady it stays
  decision    a question opened from the border's Questions icon, and one the
              story waits on: the part it is about stays lit (its box,
              reported by the frame's helper through the bridge, within
              2 px of the element's own) and the rest dims to 55 percent;
              the card beside it ("Question N of M", Back and Next) answers
              through the inbox's one route; a question about the whole
              document dims lightly with the card centred; Escape closes it
              and keyboard focus goes back where it was
  narration   while a beat is told, its targets are lit, the rest dims to 35
              percent and JARVIS's words for it are shown beside them

Headless Chromium on the built page. The package is made here (a page with
a story declaring two questions), served by JARVIS's real app (TestClient);
the story is told by the server's own `_package_story` with the real speech
scheduler and a stand-in synthesizer, as in test_voice_page_story.py. With
FOCUS_SHOTS set to a folder, each mode is saved there as a PNG.
"""

import asyncio
import importlib
import json
import os
from pathlib import Path

import pytest

from tests.package_cards import CARD, QUESTIONS, open_question
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import FAKE_TRANSCRIPTION
from tests.test_voice_page_story import STORY_BROWSER_ARGS, _Voice, _synthesize
from tests.fixtures.waiting import until_async
from tests.settings_fixtures import hold_setting
from tests.voice_page_input import start_listening

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body { margin: 0; font: 16px sans-serif; color: #111; }
section { margin: 40px; padding: 20px; border: 1px solid #ccc; }
#spacer { height: 1100px; }
</style></head><body>
<h1 id="title">A route to choose</h1>
<section id="intro"><h2>What this is</h2><p>Two routes reach the same place.</p></section>
<div id="spacer"></div>
<section id="choice"><h2>The choice</h2><p>Route A is shorter; route B is quieter.</p></section>
<script src="/api/packages/story.js"></script>
</body></html>"""

INTRO = ("Two routes reach the same place, and this introduction is read slowly enough "
         "for anyone watching to see which part of the page it is about.")
STORY = {
    "title": "A route", "language": "en", "answers": "results/decisions.json",
    "questions": [
        {"id": "route", "question": "Which route?", "recommended": "a",
         "reason": "You own the road map.", "context": "Route A was measured twice as fast.",
         "options": [{"id": "a", "name": "Route A", "says": "The shorter one."},
                     {"id": "b", "name": "Route B", "says": "The quieter one."}]},
        {"id": "clear", "question": "Is the document clear?",
         "options": [{"id": "yes", "name": "Clear", "says": "Nothing to change."},
                     {"id": "no", "name": "Unclear", "says": "Explain it again."}]}],
    "slides": [
        {"title": "What this is", "beats": [{"say": INTRO, "targets": ["#intro"]}]},
        {"title": "The choice", "waits": "route", "beats": [{"say": "Which route?", "targets": ["#choice"]}]},
        {"title": "Clear?", "waits": "clear", "beats": [{"say": "Is it clear?"}]}]}

RECT = """(sel) => { const el = document.querySelector(sel); if (!el) return null;
  const r = el.getBoundingClientRect(); return {x: r.left, y: r.top, width: r.width, height: r.height}; }"""


def _near(a, b, px=2) -> bool:
    return bool(a and b) and all(abs(a[k] - b[k]) <= px for k in ("x", "y", "width", "height"))


async def _shot(page, name, tmp_path) -> str:
    folder = Path(os.environ.get("FOCUS_SHOTS") or tmp_path / "shots")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.png"
    await page.screenshot(path=str(path))
    return str(path)


@pytest.fixture
def focus_app(monkeypatch, tmp_path):
    """JARVIS's app over the route package, in the inbox (so his answers go
    through the inbox's answer route), and every answer the one writer is
    given, recorded."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    hold_setting("brain_autostart", "0")
    from jarvis import data_paths, package_inbox, run_store, work_packages
    importlib.reload(data_paths)
    importlib.reload(run_store)
    from jarvis import server
    importlib.reload(server)
    from tests.test_work_packages import MANIFEST, make_zip, write_zip
    manifest = {**{k: v for k, v in MANIFEST.items() if k != "skill"}, "name": "route-choice", "runs": "page",
                "entry": "index.html", "story": "story.json", "returns": ["results/*"]}
    archive = write_zip(tmp_path, make_zip({"index.html": PAGE, "story.json": json.dumps(STORY)}, manifest))
    package, _ = work_packages.unpack(archive, "f0c05f0c", clear_mark=lambda _f: None)
    package_inbox.adopt(package, "0" * 64, package_inbox.ON_THE_PAGE)
    answers = []
    real = package_inbox.record_story_answer
    monkeypatch.setattr(package_inbox, "record_story_answer",
                        lambda p, q, option, note: (answers.append((q.id, option)), real(p, q, option, note))[1])
    from fastapi.testclient import TestClient
    return server, TestClient(server.app), package, answers


def _through(server, client):
    """The page's /api/packages requests: JARVIS's own app (a POST as a
    page JARVIS serves sends it, with its Origin, so an answer is his
    click), and the story through the server's `_package_story` on this
    loop, where the scheduler runs."""
    async def handle(route):
        request = route.request
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1]
        if request.method == "POST" and path.endswith("/story"):
            body = json.loads(request.post_data or "{}")
            done, said = await server._package_story(body.get("action", ""), path.split("/")[3],
                                                     body.get("slide"))
            await route.fulfill(status=200 if done else 409, content_type="application/json",
                                body=json.dumps({"said": said} if done else {"error": said}))
            return
        headers = {"Origin": "http://localhost:5173"} if request.method == "POST" else {}
        if request.headers.get("content-type"):
            headers["Content-Type"] = request.headers["content-type"]
        r = await asyncio.to_thread(client.request, request.method, path,
                                    content=request.post_data_buffer, headers=headers)
        await route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)
    return handle


class _SkippingVoice(_Voice):
    """The voice socket, also hearing the page's Skip (`skip_to_end`), as
    the server's socket does: the scheduler skips that reading."""

    def _heard(self, raw):
        msg = json.loads(raw)
        if msg.get("type") == "skip_to_end" and self.speech is not None:
            asyncio.get_running_loop().create_task(self.speech.skip_reading(int(msg["utt"])))
            return
        super()._heard(raw)


@pytest.fixture
def voice(focus_app, monkeypatch):
    from jarvis.speech import SpeechScheduler
    server = focus_app[0]
    voice = _SkippingVoice(server._opening_frames)
    voice.speech = SpeechScheduler(_synthesize, voice.emit)
    monkeypatch.setattr(server, "speech", voice.speech)
    monkeypatch.setattr(server, "_voice_emit", voice.emit)
    monkeypatch.setattr(server, "voice_clients", {object()})
    return voice


async def _opened(p, page_url, focus_app, voice, reduced=False):
    """The page with the route package open: (browser, page)."""
    from jarvis import packages_api
    server, client, package, _answers = focus_app
    try:
        browser = await p.chromium.launch(headless=True, args=STORY_BROWSER_ARGS)
    except Exception as e:                          # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900},
                                  reduced_motion="reduce" if reduced else "no-preference")
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", lambda ws: None)
    await page.route("**/api/packages**", _through(server, client))
    await page.goto(page_url)
    await start_listening(page)
    await page.wait_for_function("window.__transcribers.some((t) => t.readyState === 1)", timeout=10000)
    await voice.speech.start()
    voice.ws.send(json.dumps({"type": "package_page", "package": packages_api.describe(package)}))
    await page.frame_locator("#package-view iframe").locator("#title").wait_for()
    return browser, page


BORDER = "#package-view .package-border"


async def _opacity(page) -> str:
    return await page.evaluate(f"getComputedStyle(document.querySelector('{BORDER}')).opacity")


async def _lit_matches(page, selector) -> bool:
    """The lit box is the element's own box in the frame, within 2 px."""
    target = await page.frame_locator("#package-view iframe").locator(selector).bounding_box()
    return _near(await page.evaluate(RECT, ".package-focus-lit"), target)


@pytest.mark.asyncio(loop_scope="session")
async def test_reading_the_document_fills_the_display_and_the_bar_fades_and_returns(
        page_playwright, page_url, focus_app, voice, tmp_path):
    async with page_playwright as p:
        browser, page = await _opened(p, page_url, focus_app, voice)
        try:
            # Opened taken over: the takeover bar, the rail, and the icon border over the document.
            await async_api.expect(page.locator("#takeover-bar")).to_be_visible()
            assert await page.evaluate("document.documentElement.dataset.layout") == "takeover"
            bar = page.locator(BORDER)
            await async_api.expect(bar).to_be_visible()
            view = await page.evaluate(RECT, "#package-view")
            frame = await page.evaluate(RECT, "#package-view iframe")
            bar_box = await page.evaluate(RECT, BORDER)
            assert frame["height"] >= 0.85 * view["height"], (frame, view)
            # The whole width under the border, and nothing over it.
            assert abs(frame["width"] - bar_box["width"]) <= 1.5, (frame, bar_box)
            assert abs(frame["width"] - (view["width"] - 32)) <= 1.5, (frame, view)
            assert await page.locator(".package-side, .package-focus").count() == 0
            # A row of its own: it covers nothing of the document.
            assert frame["y"] >= bar_box["y"] + bar_box["height"] - 1, (frame, bar_box)
            assert bar_box["height"] <= 60, f"one thin bar: {bar_box}"
            # The whole display but the rail: the left column folds to the orb
            # in the gutter, and the document starts there (the gutter is the
            # 4vw ray lane, then the caption gap and the view's corridor).
            assert frame["x"] <= 0.04 * 1440 + 12 + 16 + 4, f"the document starts at the gutter: {frame}"
            orb = await page.evaluate(RECT, "#orb-control")
            assert orb["x"] + orb["width"] <= 0.04 * 1440 and orb["width"] > 0, f"the orb, in the gutter: {orb}"
            await async_api.expect(page.locator("#team-server-tile")).to_be_hidden()
            await async_api.expect(page.locator("#brain-usage")).to_be_hidden()
            # What the column offers is in the takeover bar.
            await async_api.expect(page.locator("#takeover-bar .takeover-left button").first).to_have_text("Team server")
            assert await _opacity(page) == "1"
            await _shot(page, "reading-bar-shown", tmp_path)

            # The pointer away (over the document): the border fades.
            await page.mouse.move(frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2)
            await page.wait_for_function(
                f"getComputedStyle(document.querySelector('{BORDER}')).opacity === '0.35'", timeout=8000)
            assert await bar.get_attribute("data-faded") == "true"
            await _shot(page, "reading-bar-faded", tmp_path)
            # Faded, it is still there and Done still takes a first click where
            # it was: nothing hidden to find first.
            done = await page.evaluate(RECT, "#package-view .package-done")
            hit = await page.evaluate(
                "([x, y]) => document.elementFromPoint(x, y).closest('.package-done') !== null",
                [done["x"] + done["width"] / 2, done["y"] + done["height"] / 2])
            assert hit, "Done takes the click where it was"

            # The pointer at the border: back at once.
            await page.mouse.move(bar_box["x"] + bar_box["width"] / 2, bar_box["y"] + bar_box["height"] / 2)
            await async_api.expect(bar).to_have_attribute("data-faded", "false")
            # Away again, it fades; keyboard focus entering it brings it back.
            await page.mouse.move(frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2)
            await async_api.expect(bar).to_have_attribute("data-faded", "true", timeout=8000)
            await page.locator("#package-view .package-done").focus()
            await async_api.expect(bar).to_have_attribute("data-faded", "false")

            # Restore layout brings the left column back, and the view moves in.
            await page.click("#takeover-bar .takeover-restore")
            await async_api.expect(page.locator("#brain-usage")).to_be_visible()
            restored = await page.evaluate(RECT, "#package-view iframe")
            assert restored["x"] > frame["x"] + 150, (restored, frame)
        finally:
            await voice.speech.stop()
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_with_reduced_motion_or_steady_the_bar_stays(page_playwright, page_url, focus_app, voice):
    async with page_playwright as p:
        browser, page = await _opened(p, page_url, focus_app, voice, reduced=True)
        try:
            bar = page.locator(BORDER)
            await async_api.expect(bar).to_be_visible()
            frame = await page.evaluate(RECT, "#package-view iframe")
            await page.mouse.move(frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2)
            await page.wait_for_timeout(4500)
            assert await _opacity(page) == "1" and await bar.get_attribute("data-faded") == "false"
            assert await page.evaluate(
                f"getComputedStyle(document.querySelector('{BORDER}')).transitionDuration") == "0s"
            # A question's card under reduced motion: the light jumps to its
            # part, never slides.
            await page.locator(QUESTIONS).click()
            await page.locator(".package-focus-lit").wait_for()
            assert await page.evaluate(
                "getComputedStyle(document.querySelector('.package-focus-lit')).transitionDuration") == "0s"
        finally:
            await voice.speech.stop()
        await browser.close()
    # Steady (data-motion on the document) keeps it too, whatever the media says.
    async with page_playwright as p:
        browser, page = await _opened(p, page_url, focus_app, voice)
        try:
            await page.evaluate("document.documentElement.dataset.motion = 'steady'")
            await page.wait_for_timeout(4500)
            assert await _opacity(page) == "1"
        finally:
            await voice.speech.stop()
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_question_opened_from_questions_is_presented_beside_its_part(
        page_playwright, page_url, focus_app, voice, tmp_path):
    from jarvis import work_packages
    _server, _client, package, answers = focus_app
    async with page_playwright as p:
        browser, page = await _opened(p, page_url, focus_app, voice)
        try:
            opener = page.locator(QUESTIONS)
            await async_api.expect(opener).to_have_attribute("aria-label", "Questions: 2 to answer")
            await opener.focus()
            await page.keyboard.press("Enter")
            layer = page.locator(".package-focus[data-kind='decision']")
            await async_api.expect(layer).to_be_visible()
            assert await layer.get_attribute("data-dim") == "0.55"
            # The part is brought into view in the frame and lit where it is.
            assert await until_async(lambda: _lit_matches(page, "#choice"), timeout=5), (
                await page.evaluate(RECT, ".package-focus-lit"),
                await page.frame_locator("#package-view iframe").locator("#choice").bounding_box())
            card = page.locator(CARD)
            await async_api.expect(card).to_be_focused()
            await async_api.expect(card.locator(".package-focus-position")).to_have_text("Question 1 of 2")
            await async_api.expect(card.locator("h3")).to_have_text("Which route?")
            await async_api.expect(card).to_contain_text("You own the road map.")
            await async_api.expect(card).to_contain_text("Route A was measured twice as fast.")
            await async_api.expect(card.get_by_role("button", name="Route A (recommended)")).to_be_visible()
            await async_api.expect(card.get_by_role("button", name="Back", exact=True)).to_be_disabled()
            lit, beside = await page.evaluate(RECT, ".package-focus-lit"), await page.evaluate(RECT, CARD)
            assert beside["x"] >= lit["x"] + lit["width"] or beside["x"] + beside["width"] <= lit["x"] \
                or beside["y"] >= lit["y"] + lit["height"] or beside["y"] + beside["height"] <= lit["y"], \
                f"the card is beside the part, not over it: {lit} {beside}"
            assert await page.locator(".package-focus-line line").count() == 1
            await _shot(page, "decision-lit", tmp_path)

            # His words and his click in the card: the inbox's one route and
            # record; the card stays on the question, saying what is recorded.
            await card.get_by_role("textbox").fill("Shorter wins.")
            await card.get_by_role("button", name="Route A (recommended)").click()
            await async_api.expect(card.locator(".package-vote-recorded")).to_have_text("Recorded: Route A")
            assert answers == [("route", "a")]
            record = json.loads(work_packages.read_result(package, "decisions.json"))
            assert record["route"]["answer"] == "a" and record["route"]["note"] == "Shorter wins."
            await async_api.expect(card.get_by_role("button", name="Route A (recommended)")) \
                .to_have_attribute("aria-pressed", "true")
            await async_api.expect(opener).to_have_attribute("aria-label", "Questions: 1 to answer")

            # Next: a question about the whole document, dimmed lightly, the card centred.
            await card.get_by_role("button", name="Next", exact=True).click()
            whole = page.locator(".package-focus[data-kind='decision']")
            await async_api.expect(card.locator(".package-focus-position")).to_have_text("Question 2 of 2")
            await async_api.expect(whole).to_have_attribute("data-lit", "false")
            assert await whole.get_attribute("data-dim") == "0.25"
            assert await page.locator(".package-focus-lit").count() == 0
            box, card_box = await page.evaluate(RECT, ".package-focus"), await page.evaluate(RECT, CARD)
            assert abs((card_box["x"] + card_box["width"] / 2) - (box["x"] + box["width"] / 2)) <= 2
            await _shot(page, "decision-whole", tmp_path)
            # Back returns to the first, still answered.
            await card.get_by_role("button", name="Back", exact=True).click()
            await async_api.expect(card.locator("h3")).to_have_text("Which route?")
            # Escape closes it, and keyboard focus goes back to Questions.
            await page.keyboard.press("Escape")
            await async_api.expect(whole).to_have_count(0)
            await async_api.expect(opener).to_be_focused()
            # Questions now opens the first he has not answered; Next after the last closes it.
            await opener.click()
            await async_api.expect(card.locator("h3")).to_have_text("Is the document clear?")
            await card.get_by_role("button", name="Next", exact=True).click()
            await async_api.expect(page.locator(".package-focus")).to_have_count(0)
            # An answered question before the first open one is still reached:
            # Questions opens at the open one, Back walks to it.
            reached = await open_question(page, "route")
            await async_api.expect(reached.locator(".package-focus-position")).to_have_text("Question 1 of 2")
            await async_api.expect(reached.get_by_role("button", name="Route A (recommended)")) \
                .to_have_attribute("aria-pressed", "true")
            await page.keyboard.press("Escape")
        finally:
            await voice.speech.stop()
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_story_lights_each_beat_and_presents_the_question_it_waits_on(
        page_playwright, page_url, focus_app, voice, tmp_path):
    _server, _client, _package, answers = focus_app
    async with page_playwright as p:
        browser, page = await _opened(p, page_url, focus_app, voice)
        try:
            await page.locator("#package-view [data-story-action=tell]").click()
            # Narration: the beat's targets lit, the rest dimmed lightly, its words beside them.
            told = page.locator(".package-focus[data-kind='narration']")
            await async_api.expect(told).to_be_visible(timeout=20000)
            assert await told.get_attribute("data-dim") == "0.35"
            await async_api.expect(told.locator(".package-narration-words")).to_have_text(INTRO)
            assert await until_async(lambda: _lit_matches(page, "#intro"), timeout=5)
            await async_api.expect(told.get_by_role("button", name="Pause")).to_be_visible()
            await async_api.expect(told.get_by_role("button", name="Stop")).to_be_visible()
            await _shot(page, "narration", tmp_path)

            # Pause: the reading skipped, so the story pauses on that slide;
            # Continue tells it again from there.
            await told.get_by_role("button", name="Pause").click()
            await async_api.expect(told.locator(".package-narration-head")).to_have_text(
                "Paused at slide 1 of 3.", timeout=10000)
            await told.get_by_role("button", name="Continue").click()
            await async_api.expect(told.locator(".package-narration-head")).to_have_text(
                "JARVIS is saying:", timeout=20000)

            # The story waits on the route: the question presented beside its part.
            asked = page.locator(".package-focus[data-kind='decision']")
            await async_api.expect(asked).to_be_visible(timeout=30000)
            await async_api.expect(asked.locator("h3")).to_have_text("Which route?")
            assert await until_async(lambda: _lit_matches(page, "#choice"), timeout=5)
            await _shot(page, "story-decision", tmp_path)
            await asked.get_by_role("button", name="Route B").click()
            assert await until_async(lambda: ("route", "b") in answers, timeout=5)

            # Then the question about the whole document, the card centred.
            await async_api.expect(page.locator(".package-focus-card h3")).to_have_text(
                "Is the document clear?", timeout=30000)
            assert await page.locator(".package-focus").get_attribute("data-lit") == "false"
            # Escape closes it; Questions opens it again.
            await page.keyboard.press("Escape")
            await async_api.expect(page.locator(".package-focus")).to_have_count(0)
            await page.locator(QUESTIONS).click()
            await async_api.expect(page.locator(CARD + ' [data-question="clear"]')).to_be_visible()
            await page.keyboard.press("Escape")
            await page.locator("#package-view [data-story-action=stop]").click()
        finally:
            await voice.speech.stop()
        await browser.close()
