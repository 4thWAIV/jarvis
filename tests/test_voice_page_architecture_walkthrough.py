"""An architecture package with its author's story, in the actual PackageView
at the checker's window sizes (decision_check.SIZES): the handoff's purpose
in its head, the story's declared questions answered there by click (with
his words) and by voice, a follow-up asked only once his answer opens it,
the standard vote once, the author's explanation read in place (rendered,
its safe links live, everything else characters, nothing loaded or run),
a replaced package never showing the explanation before it, and an early
Done keeping every unanswered question unanswered.

Every answer here is this test JARVIS's own, in its own data folder. Needs
the pinned kit asset (JARVIS_LANGUAGE_KIT_ASSET; the language-kit workflow)."""
import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from jarvis import (data_paths, decision_check, language_documents, language_projection,
                    make_package, package_inbox, work_packages)
from tests.test_brain_page_parity import wired  # noqa: F401  (a fixture)
from tests.test_language_documents import draft, installed_kit  # noqa: F401  (fixtures)
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_language_documents import canonical_server  # noqa: F401

pytestmark = pytest.mark.page

PURPOSE = "Decide whether the document store's ownership reads clearly before tests are written."
HOSTILE = """# The document store

It owns each **immutable** revision. See [the design](https://example.com/design?a=1&b=2).
A [script link](javascript:alert(1)) and a [data link](data:text/html,hi) stay as characters,
and so does <script>window.__ran = 1</script> or <img src=x onerror="window.__ran = 2">.

| Part | Holds |
|---|---|
| DocumentStore | revisions |

An_extremely_long_declaration_name_that_has_no_spaces_in_it_at_all_and_must_still_wrap_inside_the_panel_without_scrolling_the_page_sideways_ever.
"""
STORY = {"title": "The document store", "questions": [
    {"id": "ownership-clear", "question": "Is what the store owns clear?",
     "options": [{"id": "clear", "name": "Clear", "says": "Nothing to change."},
                 {"id": "unclear", "name": "Unclear", "says": "Explain it again."}],
     "recommended": "clear"},
    {"id": "which-part", "question": "Which part needs explaining again?",
     "options": [{"id": "revisions", "name": "Revisions", "says": "How revisions are kept."},
                 {"id": "ownership", "name": "Ownership", "says": "Who writes them."}]}],
    "slides": [
        {"title": "What it owns", "beats": [{"say": "The store owns its documents."}]},
        {"title": "Clear?", "waits": "ownership-clear", "beats": [{"say": "Is that clear?"}]},
        {"title": "Which part?", "waits": "which-part",
         "asks_when": {"question": "ownership-clear", "answers": ["unclear"]},
         "beats": [{"say": "Which part?"}]}]}


def sent(server_base: str, draft, tmp: Path, name: str, markdown: str) -> str:
    """An authored projection of the checked draft, made by the factory and
    sent to this JARVIS's inbox: its inbox id."""
    service = language_documents.Documents()
    checked = service.validate(**draft)
    envelope = language_projection.checked_revision(service, checked["document_id"],
                                                    checked["revision"])
    folder = tmp / name / "architecture"
    language_projection.project(envelope, folder, markdown.encode(), json.dumps(STORY).encode(),
                                PURPOSE, "en")
    manifest = json.loads((folder / "package.json").read_text())
    manifest["name"] = name
    (folder / "package.json").write_text(json.dumps(manifest))
    made = make_package.make_folder(folder, tmp / name / "built")
    response = httpx.post(server_base + "/api/packages/inbox", content=made.archive.read_bytes(),
                          headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}",
                                   "Content-Type": "application/zip"}, timeout=60)
    assert response.status_code == 200, response.text
    return response.json()["inbox"]["id"]


async def tool(http, server, name: str, **arguments) -> str:
    """A brain tool on a turn he drove (his voice), as the parity tests call it."""
    from tests.test_brain_page_parity import _Brain
    server.brain_instance = _Brain("user")
    r = await http.post("/internal/tool", json={"tool": name, "arguments": arguments},
                        headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}"})
    assert r.status_code == 200, r.text
    return r.json()["text"]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_walkthrough_s_questions_purpose_and_explanation_in_the_actual_view(
        page_playwright, canonical_server, draft, tmp_path):  # noqa: F811
    from tests import dashboard_page
    server, _published, _checked, base, tmp = canonical_server
    await asyncio.to_thread(dashboard_page.build_bundle)
    out = Path(os.getenv("JARVIS_LANGUAGE_REVIEW_DIR", str(tmp / "review"))) / "walkthrough"
    out.mkdir(parents=True, exist_ok=True)
    first = await asyncio.to_thread(sent, base, draft, tmp, "walkthrough-one", HOSTILE)
    second = await asyncio.to_thread(sent, base, draft, tmp, "walkthrough-two",
                                     "# The second explanation\n\nOnly in the second package.\n")
    trace: list[dict] = []
    async with page_playwright as playwright, httpx.AsyncClient(base_url=base, timeout=60) as http:
        browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        for n, (window, _frame) in enumerate(decision_check.SIZES):
            last = n == len(decision_check.SIZES) - 1
            context = await browser.new_context(viewport={"width": window[0], "height": window[1]})
            page = await context.new_page()
            await page.add_init_script(FAKE_TRANSCRIPTION)
            await page.goto(base)
            for _ in range(100):
                if server.ui_clients.pages():
                    break
                await asyncio.sleep(.05)
            opened = await http.post(f"/api/packages/inbox/{first}/open", headers={"Origin": base})
            assert opened.status_code == 200 and not opened.json()["problem"], opened.text
            view = page.locator("#package-view")
            await view.wait_for()
            frame = page.locator("#package-view iframe")
            assert await frame.get_attribute("sandbox") == "allow-scripts", "the sandbox is kept"
            box = await frame.bounding_box()
            head = page.locator(".package-head")
            assert PURPOSE in await head.inner_text(), "the handoff's purpose is in the head"
            assert await page.get_by_role("button", name="Got it").count() == 1, "the vote, once"

            # The first question by click, with his words; the follow-up is
            # asked only once his answer opens it.
            asks = page.locator(".package-asks")
            clear = asks.locator('[data-question="ownership-clear"]')
            if n == 0:
                assert await asks.locator('[data-question="which-part"]').count() == 0
                await clear.get_by_role("textbox").fill("Who writes a revision?")
                await clear.get_by_role("button", name="Unclear").click()
                await clear.locator(".package-vote-recorded").wait_for()
                follow = asks.locator('[data-question="which-part"]')
                await follow.wait_for()
                trace.append({"size": window, "clicked": "ownership-clear=unclear",
                              "follow_up_shown": True})
                # The follow-up by voice, as the brain answers it.
                said = await tool(http, server, "answer_package_decision", package=first,
                                  decision="which-part", option="ownership")
                assert said.startswith("Recorded."), said
                trace.append({"size": window, "voice": "which-part=ownership", "said": said})
                await page.reload()
                await view.wait_for()
            for key, option in (("ownership-clear", "unclear"), ("which-part", "ownership")):
                pressed = asks.locator(f'[data-question="{key}"] [data-option="{option}"]')
                await pressed.wait_for()
                assert await pressed.get_attribute("aria-pressed") == "true", key

            # The explanation, read in place.
            await page.get_by_role("button", name="Read the explanation").click()
            panel = page.locator("#package-explanation")
            await panel.locator("h3", has_text="The document store").wait_for()
            links = panel.locator("a")
            assert await links.count() == 1
            assert await links.get_attribute("href") == "https://example.com/design?a=1&b=2"
            assert await links.get_attribute("rel") == "noopener noreferrer"
            assert await links.get_attribute("target") == "_blank"
            text = await panel.inner_text()
            for kept in ("javascript:alert(1)", "data:text/html,hi", "<script>",
                         'onerror="window.__ran = 2"'):
                assert kept in text, kept
            assert await page.evaluate("window.__ran === undefined")
            assert await panel.locator("img, script, iframe").count() == 0
            assert await panel.locator("table td", has_text="revisions").count() == 1
            wide = await panel.evaluate("e => [e.scrollWidth, e.clientWidth]")
            assert wide[0] <= wide[1] + 1, f"no sideways scroll at {window}: {wide}"
            assert await page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth")
            await page.screenshot(path=str(out / f"walkthrough-{window[0]}x{window[1]}.png"))
            trace.append({"size": window, "frame": box, "explanation": "rendered",
                          "links": 1, "hostile_kept_as_text": True})

            # A replaced package never shows the explanation before it.
            reopened = await http.post(f"/api/packages/inbox/{second}/open", headers={"Origin": base})
            assert reopened.status_code == 200 and not reopened.json()["problem"], reopened.text
            await page.locator(f'#package-view[data-package*="walkthrough-two"]').wait_for()
            await page.get_by_role("button", name="Read the explanation").click()
            await panel.locator("h3", has_text="The second explanation").wait_for()
            assert "The document store" not in await panel.inner_text()

            if last:
                # Done early: what he answered goes back, the vote unanswered.
                back = await http.post(f"/api/packages/inbox/{first}/open", headers={"Origin": base})
                assert back.status_code == 200
                await page.locator(f'#package-view[data-package*="walkthrough-one"]').wait_for()
                await page.locator(".package-done").click()
                await view.wait_for(state="detached")
                entry = package_inbox.get(first)
                assert entry.state == package_inbox.CLOSED
                package = work_packages.find(entry.package_id)
                asked = {q.id: q.answer for q in package_inbox.questions(package)}
                assert asked["ownership-clear"]["answer"] == "unclear"
                assert asked["ownership-clear"]["note"] == "Who writes a revision?"
                assert asked["which-part"]["answer"] == "ownership"
                assert asked["understood"] is None, "signed off early: the vote stays unanswered"
                trace.append({"signed_off": first, "answers": {
                    k: (v or {}).get("answer") for k, v in asked.items()}})
            await context.close()
        await browser.close()
    (out / "trace.json").write_text(json.dumps(trace, indent=2))
