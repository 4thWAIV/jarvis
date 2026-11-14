"""Story targets on the kit's own rendered page, in a real browser.

The kit renders tests/fixtures/architecture/quoted-members.yaml (shared with
the kit's own tests): QuotedStore has a member whose name holds a double
quote, a single quote and a backslash, and a member GetValue; PlainStore has
a member of the same name. That page is opened as a page package in this
test's own JARVIS, its story's beats point with package_story.target (each
value written as a CSS string), and the real speech scheduler tells it. The
frame's helper must ring exactly the one intended member row (the data row,
never the note row under it), and report a member that is not there as
missing and a selector matching both GetValue rows as ambiguous, never
widening either.

The fixture is a draft the kit renders, not a checked revision: the language
types a member's name as `name` ([A-Za-z_][A-Za-z0-9_]*), so the kit's check
refuses the quoted one.

Only a kit whose page loads the story helper and marks member rows with
data-member can be pointed at like this. Until such a kit is adopted, the
test skips saying so, with the active kit's digest and the digest it needs
(KIT_WITH_MEMBERS, filled in once the kit's release gives one); it never
falls back to plain names. Needs the kit asset (JARVIS_LANGUAGE_KIT_ASSET)."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from jarvis import decision_check, language_kit as lk, package_story
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import FAKE_TRANSCRIPTION
from tests.test_voice_page_story import (RECORD, STORY_BROWSER_ARGS, _inner,  # noqa: F401
                                         jarvis_app, voice)
from tests.test_voice_page_walkthrough_story import _state, _through
from tests.voice_page_input import start_listening

pytestmark = pytest.mark.page

FIXTURE = Path(__file__).parent / "fixtures" / "architecture" / "quoted-members.yaml"
QUOTED = 'Get"It\'s\\Value'
# The released kit whose page marks member rows (data-member) and loads the
# story helper: its SHA-256, once its release gives one. None until then.
KIT_WITH_MEMBERS = None

THE_QUOTED_ROW = package_story.target("QuotedStore", member=QUOTED)
ITS_GET_VALUE = package_story.target("QuotedStore", member="GetValue")
ABSENT = package_story.target("QuotedStore", member="NotAMember")
BOTH_GET_VALUES = '[data-member="GetValue"]'
BEATS = [(THE_QUOTED_ROW, "The member whose name carries a quote, an apostrophe and a "
                          "backslash, and nothing else, is pointed at now."),
         (ITS_GET_VALUE, "Now GetValue of QuotedStore only, never the one of the same name "
                         "in PlainStore."),
         (ABSENT, "A member that is not on this page at all is reported, and nothing is "
                  "pointed at for it."),
         (BOTH_GET_VALUES, "GetValue alone names two rows on this page, so it is reported "
                           "as ambiguous and neither is pointed at.")]


def rendered_view(tmp_path: Path) -> tuple[bytes, str]:
    """The kit's own render of the fixture, and the active kit's digest."""
    asset = os.getenv("JARVIS_LANGUAGE_KIT_ASSET")
    if not asset:
        pytest.skip("Supply the kit asset (JARVIS_LANGUAGE_KIT_ASSET) to render the fixture.")
    with decision_check._data_dir(tmp_path / "kit-data"):
        installed = lk.stage(Path(asset), lk.Release(lk.PINNED_VERSION, lk.PINNED_COMMIT,
                                                     lk.PINNED_DIGEST, ("gate", "release")))
        rendered = installed.run("render", FIXTURE.read_bytes())
    assert rendered.ok and rendered.view, "the kit's render drew nothing"
    return rendered.view, installed.release.digest


@pytest.fixture
def kit_page(jarvis_app, tmp_path):
    """The kit's rendered page as a page package with a story pointing at
    its members, sent to this JARVIS's inbox."""
    server, client, _deck = jarvis_app
    from jarvis import data_paths, make_package, work_packages
    view, digest = rendered_view(tmp_path)
    folder = tmp_path / "kit-selectors"
    folder.mkdir()
    (folder / "index.html").write_bytes(view)
    story = {"title": "Members", "slides": [
        {"title": "Members", "beats": [{"say": say, "targets": [target]} for target, say in BEATS]}]}
    (folder / "story.json").write_bytes(
        package_story.compose(package_story.authored(json.dumps(story).encode()), "en"))
    (folder / "README.md").write_text("# Kit selectors\n\nThe kit's render of the fixture.\n")
    (folder / "package.json").write_text(json.dumps({
        "name": "kit-selectors", "purpose": "Point at exact members of a kit-rendered page.",
        "runs": "page", "entry": "index.html", "story": "story.json", "brief": "README.md",
        "understanding": {"index.html": "README.md", "story.json": "README.md"}}))
    # Skipped only while the adopted kit is not the release that marks members,
    # decided by its digest; with that kit adopted a missing helper or
    # data-member fails loudly here and below, never skips.
    if KIT_WITH_MEMBERS is None or digest != KIT_WITH_MEMBERS:
        pytest.skip(f"No adopted kit marks member rows: the active kit is {digest}; this needs "
                    f"the kit release that does ({KIT_WITH_MEMBERS or 'its digest not yet given'}).")
    assert package_story.HELPER_PATH.encode() in view, f"kit {digest} does not load the story helper"
    made = make_package.make_folder(folder, tmp_path / "built")
    r = client.post("/api/packages/inbox", content=made.archive.read_bytes(),
                    headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}",
                             "Content-Type": "application/zip"})
    assert r.status_code == 200, r.text
    return server, client, work_packages.find(r.json()["package"]["id"]), digest


@pytest.mark.asyncio(loop_scope="session")
async def test_the_helper_rings_exactly_the_one_intended_member_row(
        page_playwright, page_url, kit_page, voice, tmp_path):
    from jarvis import packages_api
    server, client, package, digest = kit_page
    out = Path(os.getenv("JARVIS_LANGUAGE_REVIEW_DIR", str(tmp_path / "review"))) / "kit-selectors"
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
            frame = page.frame_locator("#package-view iframe")
            await frame.locator("[data-declaration]").first.wait_for(timeout=15000)
            assert await frame.locator("[data-member]").count() > 0, \
                f"kit {digest} is the release that marks members, yet draws no data-member"
            await page.locator("#package-view .package-story button").first.click()
            await _state(page, "Waiting for your answer on slide 2 of 2.")
            await asyncio.sleep(1.0)              # the helper's settle before it reports
            seen = await _inner(page).evaluate("""(targets) => targets.map((t) => {
                const found = [...document.querySelectorAll(t)];
                return {target: t, count: found.length,
                        rows: found.map((e) => ({tag: e.tagName, note: e.classList.contains('r-note'),
                                                 member: e.getAttribute('data-member'),
                                                 declaration: e.closest('[data-declaration]')
                                                   ?.getAttribute('data-declaration')}))};
              })""", [t for t, _ in BEATS])
            rings = await _inner(page).evaluate("window.__rings")
            await page.screenshot(path=str(out / "kit-selectors.png"), full_page=True)
        finally:
            await voice.speech.stop()
        await browser.close()
    (out / "trace.json").write_text(json.dumps({"kit": digest, "seen": seen, "rings": rings,
                                                "reports": reports}, indent=2))
    by = {s["target"]: s for s in seen}
    for target, member in ((THE_QUOTED_ROW, QUOTED), (ITS_GET_VALUE, "GetValue")):
        assert by[target]["count"] == 1, by[target]
        [row] = by[target]["rows"]
        assert (row["tag"], row["note"], row["member"], row["declaration"]) == \
            ("TR", False, member, "QuotedStore"), row
        assert any(r["target"] == target and r["shown"] for r in rings), target
    assert [t for m in reports if m["type"] == "story.missing" for t in m["targets"]] == [ABSENT]
    assert [t for m in reports if m["type"] == "story.ambiguous" for t in m["targets"]] == \
        [BOTH_GET_VALUES]
    assert by[BOTH_GET_VALUES]["count"] == 2, "the ambiguous selector names both rows"
