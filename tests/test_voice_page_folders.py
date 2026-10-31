"""The Folders views, by what the user sees: the right panel's six labelled
tabs in two rows, its compact Folders list, and the centre's Folders view
(FoldersPanel.tsx, FoldersView.tsx, FolderDetail.tsx), both read from the
one store (folders.ts).

Headless Chromium on the built page. The /api/folders routes are answered
here with fixtures in the shapes jarvis/folder_service.py returns; the
paths are synthetic.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

NODE = "6f0c1d8e-3b7a-4f0e-9b1c-2a4d5e6f7a80"


def _folder(i: int, name: str, availability="available", role="data") -> dict:
    fid = f"00000000-0000-4000-8000-{i:012d}"
    path = f"/demo/home/{name}"
    return {
        "id": fid, "display_name": name, "role": role, "lifecycle": "active", "revision": 1,
        "owner_id": None, "created_at": 1.0, "updated_at": 1.0, "removed_at": None,
        "location": {"id": f"l-{fid}", "node_id": NODE, "local": True, "supplied_path": path,
                     "canonical_path": path, "revision": 1, "availability": availability,
                     "last_checked_at": 1.0, "last_seen_at": 1.0, "error_code": None},
        "access": {"reader": "files", "description": "Files and folders, except hidden, credential and key files",
                   "permitted_subtree": "", "policy_version": 1, "enabled": True},
    }


FOLDERS = [_folder(1, "aurora", role="source"), _folder(2, "field-notes"),
           _folder(3, "ledger", availability="missing"), _folder(4, "locked", availability="access_denied")]
FOLDERS += [_folder(10 + i, f"zz-archive-{i:02}") for i in range(30)]


class _Api:
    """The folder routes, answered from FOLDERS."""

    def __init__(self):
        self.reads = []

    async def __call__(self, route):
        url = route.request.url
        self.reads.append(url)
        path = url.split("/api/folders", 1)[1].split("?", 1)[0]
        if path == "" and "lifecycle=removed" in url:
            body = {"folders": [], "next_cursor": None, "counts": {}, "node_id": NODE}
        elif path == "":
            body = {"folders": FOLDERS, "next_cursor": None, "counts": {"available": 32},
                    "node_id": NODE}
        elif path.startswith("/candidates"):
            body = {"candidates": [], "next_cursor": None,
                    "coverage": {"complete": True, "sources": []}}
        elif path.endswith("/entries"):
            fid = path.split("/")[1]
            if fid == FOLDERS[3]["id"]:
                await route.fulfill(status=403, content_type="application/json", body=json.dumps(
                    {"error": {"code": "access_denied", "message": "macOS does not let JARVIS read that location."}}))
                return
            body = {"folder_id": fid, "location_id": "l", "location_revision": 1,
                    "canonical_path": "/demo", "path": "", "reader": "files",
                    "breadcrumbs": [{"name": "aurora", "path": ""}],
                    "entries": [{"name": "README.md", "path": "README.md", "kind": "file", "size": 20,
                                 "modified": 1.0, "transcript": False}],
                    "withheld": 1, "scanned": 2, "partial": False, "next_cursor": None}
        elif path.endswith("/preview"):
            body = {"folder_id": "x", "location_id": "l", "location_revision": 1, "canonical_path": "/demo",
                    "path": "README.md", "reader": "files", "kind": "text", "size": 20, "modified": 1.0,
                    "lines": ["<b>not bold</b>"], "first_line": 1, "offset": 0, "next_cursor": None,
                    "partial": False, "clipped_lines": 0, "skipped_records": 0, "sessions": []}
        else:
            fid = path.split("/")[1]
            folder = next(f for f in FOLDERS if f["id"] == fid)
            body = {"folder": folder, "sessions": [], "events": [],
                    "coverage": {"complete": True, "sources": []}}
        await route.fulfill(content_type="application/json", body=json.dumps(body))


async def _open(p, url):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", _Voice())
    await page.route_web_socket("**/ws/sessions", lambda ws: None)
    await page.route("**/api/folders**", _Api())
    await page.goto(url)
    await page.wait_for_selector(".right-tabs")
    return browser, page


@pytest.mark.asyncio
async def test_six_labelled_tabs_in_two_rows(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        tabs = await page.evaluate("""() => [...document.querySelectorAll('.right-tabs [role=tab]')]
            .map(t => ({label: t.getAttribute('aria-label'), text: t.textContent.trim(),
                        top: Math.round(t.getBoundingClientRect().top)}))""")
        assert [t["label"] for t in tabs] == ["Folders", "Agents", "Queue", "Workflows", "Network",
                                              "Diagnostics"]
        assert all(t["text"] == t["label"] for t in tabs), tabs
        tops = sorted({t["top"] for t in tabs})
        assert len(tops) == 2 and [t["top"] for t in tabs] == [tops[0]] * 3 + [tops[1]] * 3
        assert await page.get_attribute(".right-tabs [aria-label='Agents']", "aria-selected") == "true"
        await browser.close()


@pytest.mark.asyncio
async def test_the_right_list_and_the_centre_share_one_selection(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        await page.click(".right-tabs [aria-label='Folders']")
        await page.click(".folders-panel-row:has-text('field-notes')")
        await async_api.expect(page.locator(".centre-tabs [aria-label='Folders']")).to_have_attribute(
            "aria-selected", "true")
        await async_api.expect(page.locator(".folder-detail h2")).to_have_text("field-notes")
        assert await page.text_content(".folder-row[aria-current=true] .folder-row-name") == "field-notes"
        await page.click(".folder-row:has-text('aurora')")
        await async_api.expect(page.locator(".folders-panel-row[aria-current=true] .folders-panel-name")) \
            .to_have_text("aurora")
        # Content is text, never markup.
        await page.click(".folder-entry:has-text('README.md')")
        await async_api.expect(page.locator(".folder-text")).to_have_text("<b>not bold</b>")
        assert await page.locator(".folder-text b").count() == 0
        await browser.close()


@pytest.mark.asyncio
async def test_filters_no_results_and_denied_say_so_in_words(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        await page.click(".centre-tabs [aria-label='Folders']")
        await page.fill(".folders-catalog input[type=search]", "no-such-folder")
        await async_api.expect(page.locator(".folders-list")).to_contain_text("No folders match these filters.")
        await page.fill(".folders-catalog input[type=search]", "")
        await page.select_option(".folders-filters select >> nth=1", "missing")
        assert await page.locator(".folder-row").all_text_contents() == [
            await page.locator(".folder-row").first.text_content()]
        await async_api.expect(page.locator(".folder-row .folder-availability")).to_have_text("Missing")
        await page.select_option(".folders-filters select >> nth=1", "access_denied")
        await page.click(".folder-row:has-text('locked')")
        await async_api.expect(page.locator(".folder-detail")).to_contain_text(
            "macOS does not let JARVIS read this location.")
        await browser.close()


@pytest.mark.asyncio
async def test_the_keyboard_reaches_both_views_and_the_catalog_keeps_its_place(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url)
        await page.focus(".right-tabs [aria-label='Folders']")
        await page.keyboard.press("Enter")
        await page.focus(".folders-panel-row:has-text('aurora')")
        await page.keyboard.press("Enter")
        await async_api.expect(page.locator(".folder-detail h2")).to_have_text("aurora")
        await page.eval_on_selector(".folders-catalog", "e => { e.scrollTop = 400; }")
        await page.click(".centre-tabs [aria-label='Conversation']")
        await page.click(".centre-tabs [aria-label='Folders']")
        assert await page.eval_on_selector(".folders-catalog", "e => e.scrollTop") == 400
        await async_api.expect(page.locator(".folder-detail h2")).to_have_text("aurora")
        await browser.close()
