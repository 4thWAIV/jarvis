"""An author's walk-through as JARVIS tells it, in a real browser: a page
whose story is the one composition (package_story.compose) of an authored
story that declares a question and a follow-up, sent through the package
inbox. The real speech scheduler speaks each beat (a stand-in synthesizer
whose audio lasts as long as the words), the frame's helper rings each
beat's targets, the story waits at its question until he answers it in the
view's head, passes over the follow-up his answer does not open, and waits
again at the standard vote, asked once, last. Stop from the page and from
JARVIS each end it within a second, and Continue resumes at the first
applicable question with no answer.

The harness is test_voice_page_story's; every /api/packages request is
answered by JARVIS's real app, its body and the page's origin passed on."""
import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from jarvis import package_inbox, package_story
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_package_modes import EXAMPLES
from tests.test_voice_page_captions import FAKE_TRANSCRIPTION
from tests.test_voice_page_story import (RECORD, STORY_BROWSER_ARGS, _inner, _ringed,  # noqa: F401
                                         jarvis_app, voice)
from tests.voice_page_input import start_listening

pytestmark = pytest.mark.page

STOP_WITHIN_SEC = 1.0
ORIGIN = "http://localhost:5173"
AUTHORED = {
    "title": "A pot of tea",
    "questions": [
        {"id": "kettle", "question": "Which kettle should the office buy?",
         "options": [{"id": "electric", "name": "Electric", "says": "Fast, and it switches off."},
                     {"id": "stovetop", "name": "Stovetop", "says": "Slower, and it whistles."}],
         "recommended": "electric"},
        {"id": "stove-kind", "question": "Gas or induction?",
         "options": [{"id": "gas", "name": "Gas", "says": "Any pot works."},
                     {"id": "induction", "name": "Induction", "says": "Needs a steel base."}]}],
    "slides": [
        {"title": "What a pot needs", "target": "#s1",
         "beats": [{"say": "A good pot of tea needs three things.", "targets": ["#s1 h2"]},
                   {"say": "Fresh water, first of all.", "targets": ["#water"]}]},
        {"title": "The kettle", "target": "#s2", "waits": "kettle",
         "beats": [{"say": "Which kettle should the office buy?",
                    "targets": ["#s2 [data-step='1']"]}]},
        {"title": "The stove", "waits": "stove-kind",
         "asks_when": {"question": "kettle", "answers": ["stovetop"]},
         "beats": [{"say": "Gas or induction?"}]},
        {"title": "Steeping", "target": "#s3",
         "beats": [{"say": "Each tea has its own time.", "targets": [".tea"]}]}]}


@pytest.fixture
def walkthrough(jarvis_app, tmp_path):
    """The story deck with its story replaced by the composed walk-through,
    sent to the inbox: (server, client, the package, its inbox id)."""
    server, client, _deck = jarvis_app
    from jarvis import data_paths, make_package, work_packages
    folder = tmp_path / "walkthrough"
    shutil.copytree(EXAMPLES / "story-deck", folder)
    told = package_story.compose(package_story.authored(json.dumps(AUTHORED).encode()), "en")
    (folder / "story.json").write_bytes(told)
    manifest = json.loads((folder / "package.json").read_text())
    (folder / "package.json").write_text(json.dumps({**manifest, "name": "tea-walkthrough"}))
    made = make_package.make_folder(folder, tmp_path / "built")
    r = client.post("/api/packages/inbox", content=made.archive.read_bytes(),
                    headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}",
                             "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    inbox_id = r.json()["inbox"]["id"]
    return server, client, work_packages.find(r.json()["package"]["id"]), inbox_id


def _through(server, client):
    async def handle(route):
        request = route.request
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1]
        if request.method == "POST" and path.endswith("/story"):
            body = json.loads(request.post_data or "{}")
            done, said = await server._package_story(body.get("action", ""),
                                                     path.split("/")[3], body.get("slide"))
            await route.fulfill(status=200 if done else 409, content_type="application/json",
                                body=json.dumps({"said": said} if done else {"error": said}))
            return
        r = await asyncio.to_thread(
            client.request, request.method, path, content=request.post_data_buffer,
            headers={"Origin": ORIGIN, "Content-Type": request.headers.get(
                "content-type", "application/json")})
        await route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)
    return handle


async def _state(page, words: str, timeout: float = 60000):
    await async_api.expect(page.locator(".package-story-now")).to_have_text(words, timeout=timeout)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_walkthrough_waits_for_his_answers_and_stops_and_resumes_on_time(
        page_playwright, page_url, walkthrough, voice, tmp_path):
    from jarvis import packages_api
    server, client, package, inbox_id = walkthrough
    out = Path(os.getenv("JARVIS_LANGUAGE_REVIEW_DIR", str(tmp_path / "review"))) / "story"
    out.mkdir(parents=True, exist_ok=True)
    trace: list[dict] = []
    async with page_playwright as p:
        browser = await p.chromium.launch(headless=True, args=STORY_BROWSER_ARGS)
        page = await browser.new_page(viewport={"width": 1440, "height": 813})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.add_init_script(RECORD)
        await page.route_web_socket("**/ws/voice", voice)
        await page.route_web_socket("**/ws/sessions", lambda ws: None)
        await page.route("**/api/packages/**", _through(server, client))
        await page.goto(page_url)
        await start_listening(page)
        await voice.speech.start()
        try:
            voice.ws.send(json.dumps({"type": "package_page",
                                      "package": packages_api.describe(package)}))
            await page.locator("#package-view iframe").wait_for()
            assert await page.get_by_role("button", name="Got it").count() == 1, "the vote, once"
            slides = await page.locator("#package-view .package-story select option").all_inner_texts()
            assert slides[-1].endswith("Got it?") and sum("Got it?" in s for s in slides) == 1

            # Told from the start, it waits at his question.
            await page.locator("#package-view .package-story button").first.click()
            await _ringed(page, "(r) => r.target === '#water' && r.shown")
            await _state(page, "Waiting for your answer on slide 2 of 5.")
            trace.append({"waiting": 2, "audio_frames": len(voice.audio)})

            # Answered in the head: it goes on, passes over the follow-up his
            # answer did not open, and waits at the vote.
            ask = page.locator('.package-asks [data-question="kettle"]')
            await ask.get_by_role("button", name="Electric (recommended)").click()
            await ask.locator(".package-vote-recorded").wait_for()
            await page.locator('.package-asks [data-question="stove-kind"]').wait_for(
                state="detached")
            await _state(page, "Waiting for your answer on slide 5 of 5.")
            told = sorted({m["cue"]["slide"] for m in voice.audio})
            assert told == [1, 2, 4, 5], f"the follow-up is passed over: {told}"
            trace.append({"answered": {"kettle": "electric"}, "slides_told": told})

            # Stop from the page, within a second; then nothing more.
            await page.select_option("#package-view .package-story select", "1")
            await page.locator("#package-view .package-story button").first.click()
            await _ringed(page, "(r) => r.beat === '1.1' && r.added > Date.now() - 5000")
            pressed = time.monotonic()
            await page.click("#package-view .package-story button:has-text('Stop')")
            await _state(page, "Stopped.", timeout=STOP_WITHIN_SEC * 1000)
            stopped_in = time.monotonic() - pressed
            assert stopped_in <= STOP_WITHIN_SEC, stopped_in
            heard = len(voice.audio)
            await asyncio.sleep(1.5)
            assert len(voice.audio) == heard, "nothing more is said once stopped"
            trace.append({"stop": "page", "seconds": round(stopped_in, 3)})

            # Continue resumes at the first applicable question with no answer:
            # the vote (kettle answered, the follow-up not opened).
            before = len(voice.audio)
            await page.click("#package-view .package-story button:has-text('Continue')")
            await _state(page, "Waiting for your answer on slide 5 of 5.")
            resumed = [m["cue"]["slide"] for m in voice.audio[before:]]
            assert resumed and set(resumed) == {5}, resumed
            trace.append({"resumed_at": 5})

            # Stop from JARVIS (the brain's tell_package_story stop), within a second.
            await page.select_option("#package-view .package-story select", "4")
            await page.locator("#package-view .package-story button").first.click()
            await _state(page, "Telling slide 4 of 5.")
            asked = time.monotonic()
            done, said = await server._package_story("stop", package.id, None)
            assert done, said
            await _state(page, "Stopped.", timeout=STOP_WITHIN_SEC * 1000)
            stopped_in = time.monotonic() - asked
            assert stopped_in <= STOP_WITHIN_SEC, stopped_in
            trace.append({"stop": "jarvis", "seconds": round(stopped_in, 3), "said": said})
            await page.screenshot(path=str(out / "walkthrough-story.png"))

            record = json.loads((package.folder / "results" / "decisions.json").read_text())
            assert record["kettle"]["answer"] == "electric" and "understood" not in record
            assert package_inbox.get(inbox_id).decisions_total == 2, \
                "the question asked and the vote; the follow-up not opened"
            trace.append({"answers": {k: v["answer"] for k, v in record.items()}})
        finally:
            await voice.speech.stop()
        await browser.close()
    (out / "trace.json").write_text(json.dumps(trace, indent=2))


# A page shaped as the kit's renderer contract settles it: a section.d-decl
# per declaration with data-declaration, data-part sections inside, a member
# table with its column cells (data-col); its rows also carry data-member, which
# no beat here depends on until the kit settles it. The shell loads the helper
# with defer, as the kit renderer does.
# One declaration id is drawn twice, to be reported as ambiguous.
KIT_SHAPED = """<!doctype html><html><head><meta charset="utf-8"><title>Surface</title>
<style>section{margin:12px;padding:8px;border:1px solid #888}</style></head><body>
<section class="d-decl" data-declaration="IGrantStore"><h2>IGrantStore</h2>
 <div data-part="members"><table><tbody>
  <tr id="declare[0].constant.value.members[0]" class="r-marked teal" data-member="GetActiveGrantsAsync"><td data-col="name">GetActiveGrantsAsync</td></tr>
  <tr class="r-note"><td>Answers with Loaded.</td></tr>
 </tbody></table></div></section>
<section class="d-decl" data-declaration="IWindowsHelloNativeOps"><h2>IWindowsHelloNativeOps</h2>
 <div data-part="members"><table><thead><tr><th data-col="name">Name</th></tr></thead><tbody>
  <tr data-member="BeginAvailabilityCheck"><td data-col="name">BeginAvailabilityCheck</td></tr>
  <tr data-member="BeginVerification"><td data-col="name">BeginVerification</td></tr>
  <tr class="r-note"><td>The native cause path.</td></tr>
 </tbody></table></div></section>
<section class="d-decl" data-declaration="Loaded"><h2>Loaded</h2></section>
<section class="d-decl" data-declaration="Loaded"><h2>Loaded again</h2></section>
<script defer src="/api/packages/story.js"></script></body></html>"""
PRECISE = {"title": "Surface", "slides": [{"title": "Targets", "beats": [
    {"say": "The grant store's members.",
     "targets": ['[data-declaration="IGrantStore"] [data-part="members"]']},
    {"say": "The native helper's name column.",
     "targets": ['[data-declaration="IWindowsHelloNativeOps"] th[data-col="name"]']},
    {"say": "Loaded is drawn twice on this page, so pointing at it would be a guess, and JARVIS does not guess.", "targets": ['[data-declaration="Loaded"]']},
    {"say": "This declaration is not on the page at all, so there is nothing here to point at for it.",
     "targets": ['[data-declaration="IWarningSink"]']}]}]}


@pytest.fixture
def kit_shaped(jarvis_app, tmp_path):
    server, client, _deck = jarvis_app
    from jarvis import data_paths, make_package, work_packages
    folder = tmp_path / "surface"
    folder.mkdir()
    (folder / "index.html").write_text(KIT_SHAPED)
    (folder / "story.json").write_bytes(
        package_story.compose(package_story.authored(json.dumps(PRECISE).encode()), "en"))
    (folder / "UNDERSTANDING.md").write_text("# Surface\n\nA page shaped as the kit's.\n")
    (folder / "package.json").write_text(json.dumps({
        "name": "kit-shaped-surface", "purpose": "Point at exact declarations and members.",
        "runs": "page", "entry": "index.html", "story": "story.json", "brief": "UNDERSTANDING.md",
        "understanding": {"index.html": "UNDERSTANDING.md", "story.json": "UNDERSTANDING.md"}}))
    made = make_package.make_folder(folder, tmp_path / "built")
    r = client.post("/api/packages/inbox", content=made.archive.read_bytes(),
                    headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}",
                             "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    return server, client, work_packages.find(r.json()["package"]["id"])


@pytest.mark.asyncio(loop_scope="session")
async def test_a_precise_target_rings_exactly_one_element_or_is_reported(
        page_playwright, page_url, kit_shaped, voice, tmp_path):
    from jarvis import packages_api
    server, client, package = kit_shaped
    out = Path(os.getenv("JARVIS_LANGUAGE_REVIEW_DIR", str(tmp_path / "review"))) / "precise"
    out.mkdir(parents=True, exist_ok=True)
    async with page_playwright as p:
        browser = await p.chromium.launch(headless=True, args=STORY_BROWSER_ARGS)
        page = await browser.new_page(viewport={"width": 1440, "height": 813})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.add_init_script(RECORD)
        reports: list[dict] = []
        await page.expose_function("__report", lambda m: reports.append(m))
        await page.add_init_script("""window.addEventListener('message', (e) => {
            const m = e.data; if (window === window.top && m && m.jarvis === 'package'
              && (m.type === 'story.missing' || m.type === 'story.ambiguous')) window.__report(m); });""")
        await page.route_web_socket("**/ws/voice", voice)
        await page.route_web_socket("**/ws/sessions", lambda ws: None)
        await page.route("**/api/packages/**", _through(server, client))
        await page.goto(page_url)
        await start_listening(page)
        await voice.speech.start()
        try:
            voice.ws.send(json.dumps({"type": "package_page",
                                      "package": packages_api.describe(package)}))
            await page.locator("#package-view iframe").wait_for()
            await page.locator("#package-view .package-story button").first.click()
            # While Loaded's beat plays, its ring is drawn nowhere.
            hidden = await _inner(page).wait_for_function(
                """(t) => { const r = [...document.querySelectorAll('.jarvis-story-ring')]
                             .find((e) => e.dataset.target === t);
                           return r ? r.style.display : null; }""",
                arg='[data-declaration="Loaded"]', timeout=60000)
            assert await hidden.json_value() == "none", "an ambiguous target is never widened"
            await _state(page, "Waiting for your answer on slide 2 of 2.")
            await asyncio.sleep(1.0)            # the helper's settle before it reports
            rings = await _inner(page).evaluate("""window.__rings.map((r) => ({...r,
                count: document.querySelectorAll(r.target).length,
                tag: (document.querySelector(r.target) || {}).tagName,
                note: (document.querySelector(r.target) || {classList: {contains: () => false}})
                      .classList.contains('r-note')}))""")
            await page.screenshot(path=str(out / "precise-targets.png"))
        finally:
            await voice.speech.stop()
        await browser.close()
    shown = {r["target"]: r for r in rings}
    members = '[data-declaration="IGrantStore"] [data-part="members"]'
    column = '[data-declaration="IWindowsHelloNativeOps"] th[data-col="name"]'
    assert shown[members]["shown"] and shown[members]["count"] == 1
    assert shown[column]["shown"] and shown[column]["tag"] == "TH"
    # Matching several or none, nothing is drawn for it: each is reported.
    ambiguous = [m for m in reports if m["type"] == "story.ambiguous"]
    missing = [m for m in reports if m["type"] == "story.missing"]
    assert [t for m in ambiguous for t in m["targets"]] == ['[data-declaration="Loaded"]']
    assert [t for m in missing for t in m["targets"]] == ['[data-declaration="IWarningSink"]']
    (out / "trace.json").write_text(json.dumps({"rings": rings, "reports": reports}, indent=2))
