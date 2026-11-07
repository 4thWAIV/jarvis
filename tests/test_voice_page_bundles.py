"""Files a teammate sent, read as files on the page (BundleInventory.tsx,
BundleMemberView.tsx, bundles.ts), by what the owner sees: a checked file's
Open, an archive's contents a folder at a time with each member's state only
when it is not readable and a work-package badge, and a member opened in the
centre: its text a page at a time with More, a PDF's rendered pages with
Previous and Next, and a static HTML preview in which no script runs.

Headless Chromium on the built page. The Queue is Tim's world
(tests/messaging_queue_scenario.py); the bundle routes are answered here in
bundle_contract's shapes, the HTML preview with the headers web_auth gives
it. Names and text are synthetic.
"""

import json
import struct
import zlib
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from jarvis import web_auth
from jarvis.bundle_contract import BUNDLE_VIEW_FRAME, member_id
from tests import messaging_queue_scenario as sc
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice
from tests.test_voice_page_network import _Sessions

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

SHA = "cd" * 32
FOLDER = "moment-summary"


def _png(width=4, height=6) -> bytes:
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _attachment(message, attachment):
    return {"message_id": message, "attachment_id": attachment, "filename": "review.zip",
            "declared_media_type": "application/zip", "size_bytes": 179692, "sha256": SHA}


def _member(path, fmt, support="readable", reason=None, is_dir=False, media="application/pdf"):
    return {"member_id": member_id(SHA, path), "path": path, "name": path.rsplit("/", 1)[-1],
            "is_dir": is_dir, "size_bytes": 0 if is_dir else 24576, "compressed_size": 900,
            "format": fmt, "media_type": media, "support": support, "reason": reason,
            "operations": [] if is_dir or support == "blocked" else ["read_text"]}


PDF = _member(f"{FOLDER}/summary.pdf", "pdf")
BRIEF = _member(f"{FOLDER}/brief.md", "markdown", media="text/markdown")
PAGE = _member(f"{FOLDER}/summary.html", "html", media="text/html")
LINK = _member(f"{FOLDER}/latest", "unknown", support="blocked", reason="link")
NESTED = _member(f"{FOLDER}/sources", "folder", is_dir=True, media="inode/directory")
SOURCE = _member(f"{FOLDER}/sources/notes.txt", "text", media="text/plain")
MEMBERS = {m["member_id"]: m for m in (PDF, BRIEF, PAGE, LINK, SOURCE)}
SCRIPTED_HTML = ("<html><body><h1 id='h'>Summary preview</h1>"
                 "<script>document.getElementById('h').textContent = 'script ran';"
                 "parent.postMessage('script ran', '*');</script></body></html>")


class Bundles:
    """The bundle routes, answered in the contract's shapes."""

    def __init__(self):
        self.calls = []

    async def __call__(self, route):
        req = route.request
        url = urlparse(req.url)
        parts = [unquote(p) for p in url.path.split("/")]
        message, attachment = parts[4], parts[6]
        rest = parts[7:]
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.calls.append((req.method, "/".join(rest), q))
        info = _attachment(message, attachment)
        if rest == ["bundle"]:
            body = {"attachment": info, "format": "zip", "media_type": "application/zip",
                    "container": "zip", "support": "partial", "reason": None,
                    "root_member_id": member_id(SHA, ""),
                    "inventory": {"files": 5, "folders": 2, "expanded_bytes": 242260,
                                  "readable": 4, "partial": 0, "unsupported": 0, "blocked": 1,
                                  "wrapper": FOLDER, "ignored": 0},
                    "package": {"recognised": True, "runs": "conversation", "reason": None}}
        elif rest == ["bundle", "members"]:
            folder = q.get("folder", "")
            if folder == f"{FOLDER}/sources":
                members = [SOURCE]
            elif folder == FOLDER:
                members = [NESTED, PDF, BRIEF, PAGE, LINK]
            else:
                members = [_member(FOLDER, "folder", is_dir=True, media="inode/directory")]
            body = {"attachment": info, "folder": folder, "members": members,
                    "total": len(members), "continuation": None}
        elif rest[-1] == "show":
            await route.fulfill(status=204, body="")
            return
        elif rest[-1] == "text":
            m = MEMBERS[rest[2]]
            second = q.get("continuation") == "more"
            text = ("The second part of the brief." if second else
                    "Background: <b>not bold</b>\nClaims and evidence.")
            body = {"attachment": info, "member": m, "reader": {"name": "text", "version": 1},
                    "chunks": [{"location": {"kind": "lines", "start": 3 if second else 1,
                                             "end": 3 if second else 2}, "text": text}],
                    "chars": len(text), "extent": {"pages": 2 if m["format"] == "pdf" else None,
                                                   "lines": 3, "sheets": [], "slides": None},
                    "complete": second, "continuation": None if second else "more", "notes": []}
            if m["format"] == "pdf":
                body["chunks"][0]["location"] = {"kind": "page", "page": 2 if second else 1}
        elif rest[-1] == "preview":
            m = MEMBERS[rest[2]]
            base = f"/api/team/messages/{message}/attachments/{attachment}/bundle/members/{rest[2]}"
            refs = ([{"kind": "html", "member_id": rest[2], "page": None,
                      "url": f"{base}/html/summary.html", "width": None, "height": None}]
                    if m["format"] == "html" else
                    [{"kind": "page_image", "member_id": rest[2], "page": 1,
                      "url": f"{base}/pages/1", "width": 4, "height": 6}]
                    if m["format"] == "pdf" else [])
            body = {"attachment": info, "member": m, "refs": refs,
                    "extent": {"pages": 2 if m["format"] == "pdf" else None, "lines": None,
                               "sheets": [], "slides": None},
                    "blocked_resources": ["https://tracker.example/pixel.gif"]
                    if m["format"] == "html" else [], "continuation": None}
        elif "pages" in rest:
            await route.fulfill(status=200, content_type="image/png", body=_png())
            return
        elif "html" in rest:
            headers = {k.decode(): v.decode() for k, v in web_auth.BUNDLE_PREVIEW_HEADERS}
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                headers=headers, body=SCRIPTED_HTML)
            return
        else:
            await route.fulfill(status=404, content_type="application/json",
                                body=json.dumps({"error": {"code": "member_not_found",
                                                           "message": "Not in it.", "reason": None}}))
            return
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


async def _open(p, url):
    world = await sc.build()
    voice = _Voice()
    routes = sc.QueueRoutes(world, voice)
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", voice)
    sessions = _Sessions()
    await page.route_web_socket("**/ws/sessions", sessions)
    routes.on_change = lambda: sessions.ws.send(json.dumps({"type": "messages"}))
    envelope = json.dumps(sc.network_envelope())
    await page.route("**/api/team/network", lambda r: r.fulfill(
        status=200, content_type="application/json", body=envelope))
    await routes.install(page)
    bundles = Bundles()
    await page.route("**/api/team/messages/*/attachments/**", bundles)
    await page.goto(url)
    await page.wait_for_selector(".right-tabs [aria-label='Queue']")
    return browser, page, world, voice, bundles


def _frame(world, member):
    sent = world.sent["ana-numbers"]
    return {"type": BUNDLE_VIEW_FRAME, "message_id": str(sent.message_id),
            "attachment_id": str(sent.attachments[0].attachment_id),
            "member_id": member["member_id"]}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_checked_file_opens_its_contents_a_folder_at_a_time(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, _voice, bundles = await _open(p, page_url)
        await page.click(".right-tabs [aria-label='Queue']")
        await page.click(f'.queue-view li[data-thread-id="{world.thread("ana-numbers")}"] .queue-row')
        card = page.locator("#queue-item-view .queue-file").first
        await card.locator("text=Download").click()
        # Checked is the ordinary state: the Open button appears, with no words.
        await async_api.expect(card.get_by_role("button", name="Open q3-numbers.pdf")).to_be_visible()
        await async_api.expect(card.locator(".file-state")).to_have_count(0)
        await card.get_by_role("button", name="Open q3-numbers.pdf").click()
        contents = card.get_by_role("button", name="Contents q3-numbers.pdf")
        await async_api.expect(contents).to_have_attribute("aria-expanded", "true")
        await async_api.expect(card.locator(".bundle-badge")).to_have_text("Work package")
        await async_api.expect(card.locator(".file-meta").nth(1)).to_have_text("5 files")

        # A zip whose top level is one folder opens inside it: each member's
        # name, type and size; a state only where it is not readable. There
        # is no ".." row, and no path until a folder below it is opened.
        rows = card.locator(".bundle-members > li")
        await async_api.expect(rows).to_have_count(5)
        assert await card.locator(".bundle-path").count() == 0
        assert await card.locator(".bundle-name").all_inner_texts() == [
            "sources/", "summary.pdf", "brief.md", "summary.html", "latest"]
        assert await card.locator(".bundle-support").all_inner_texts() == [
            "It is a link, which JARVIS never follows."]
        assert await card.locator(f'[data-member-id="{PDF["member_id"]}"] .file-meta').inner_text() \
            == "pdf · 24.0 KB"
        assert await card.locator(f'[data-member-id="{LINK["member_id"]}"] button').count() == 0
        await card.locator("text=sources/").click()
        await async_api.expect(card.locator(".bundle-name")).to_have_text(["notes.txt"])
        # The path starts at the zip's own name and ends at where you are.
        assert await card.locator(".bundle-path").inner_text() == "q3-numbers.pdf / sources"
        await card.locator(".bundle-path >> text=q3-numbers.pdf").click()
        await async_api.expect(rows).to_have_count(5)
        assert [c[2].get("folder", "") for c in bundles.calls if c[1] == "bundle/members"] == [
            "", FOLDER, f"{FOLDER}/sources", FOLDER]

        # Open a member: the show route, and the centre's File view at once.
        await card.get_by_role("button", name="Open brief.md").click()
        await async_api.expect(page.locator(".centre-tabs [aria-label='File']")) \
            .to_have_attribute("aria-selected", "true")
        await async_api.expect(page.locator("#bundle-member-view h2")).to_have_text("brief.md")
        assert ("POST", f"bundle/members/{BRIEF['member_id']}/show", {}) in bundles.calls
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_member_s_text_reads_on_with_more_and_a_pdf_turns_its_pages(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, voice, _bundles = await _open(p, page_url)
        voice.send(_frame(world, BRIEF))
        view = page.locator("#bundle-member-view")
        await async_api.expect(view.locator(".bundle-where")).to_have_text(["Lines 1–2"])
        assert await view.locator(".message-text").inner_text() == \
            "Background: <b>not bold</b>\nClaims and evidence."
        await view.locator("text=More").click()
        await async_api.expect(view.locator(".bundle-where")).to_have_text(["Lines 1–2", "Line 3"])
        await async_api.expect(view.locator("text=More")).to_have_count(0)

        voice.send(_frame(world, PDF))
        await async_api.expect(view.locator("h2")).to_have_text("summary.pdf")
        image = view.locator(".bundle-page img")
        assert (await image.get_attribute("src")).endswith("/pages/1?max_edge=1600")
        await page.wait_for_function(
            "() => document.querySelector('#bundle-member-view .bundle-page img').naturalWidth === 4")
        assert await view.locator(".bundle-page-number").inner_text() == "1 / 2"
        assert await view.get_by_role("button", name="Previous").is_disabled()
        await view.get_by_role("button", name="Next").click()
        assert (await image.get_attribute("src")).endswith("/pages/2?max_edge=1600")
        assert await view.get_by_role("button", name="Next").is_disabled()
        await async_api.expect(view.locator(".bundle-where")).to_have_text(["Page 1"])
        await view.locator(".workflow-close").click()
        await async_api.expect(view).to_have_count(0)
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_an_html_preview_shows_with_no_script_running(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, voice, _bundles = await _open(p, page_url)
        await page.evaluate("""() => { window.__posted = [];
            addEventListener('message', (e) => window.__posted.push(String(e.data))); }""")
        voice.send(_frame(world, PAGE))
        frame_el = page.locator("#bundle-member-view iframe")
        await async_api.expect(frame_el).to_have_count(1)
        assert await frame_el.get_attribute("sandbox") == ""
        heading = page.frame_locator("#bundle-member-view iframe").locator("#h")
        await async_api.expect(heading).to_have_text("Summary preview")
        await page.wait_for_timeout(300)
        assert await page.evaluate("window.__posted") == []
        assert await page.locator("#bundle-member-view .bundle-html .file-state").inner_text() \
            == "1 link not loaded"
        # Its text instead, as text.
        await page.get_by_role("button", name="Text", exact=True).click()
        await async_api.expect(page.locator("#bundle-member-view iframe")).to_have_count(0)
        await async_api.expect(page.locator("#bundle-member-view .message-text")) \
            .to_have_text("Background: <b>not bold</b>\nClaims and evidence.")
        await browser.close()
