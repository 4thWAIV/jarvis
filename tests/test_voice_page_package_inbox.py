"""The package inbox on the page, in a real browser: a decision deck sent
the way make_package sends it (POST /api/packages/inbox, for a work item,
from a session) shows nowhere on arrival; it is listed at the top of the
Queue tab and as one reduced line in Updates waiting; his Open shows it in
the centre; and the work item's Decisions tab asks its questions, an
answer there landing in the package's own record; and Done in the view's
head signs it off: the view closes, the row goes to Closed, and a capture
of his answers waits to be read back.

Headless Chromium on the built page. Every /api/packages, /api/attention
and /api/work request is answered by JARVIS's real app (TestClient) over a
temporary JARVIS_DATA_DIR; a POST carries the page's own Origin, as the
browser's would. Screenshots go to $PAGE_SHOTS when it is set.
"""

import asyncio
import importlib
import os
import shutil
from pathlib import Path

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice
from tests.fixtures.waiting import until_async

pytestmark = pytest.mark.page

EXAMPLES = Path(__file__).parent.parent / "examples" / "packages"
ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture
def jarvis_app(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    from jarvis import data_paths, run_store
    importlib.reload(data_paths)
    importlib.reload(run_store)
    from jarvis import server
    importlib.reload(server)
    run_store.init_db()
    from jarvis import attention_store, make_package, package_signature, work_packages, work_store
    from tests.agent_key_fixtures import onboarded_auth
    monkeypatch.setattr(work_packages, "unquarantine", lambda _folder: None)
    monkeypatch.setattr(server.agent_directory, "names_by_session", lambda: {"sess-1": "chitauri"})
    server.attention_record = attention_store.AttentionStore()
    folder = tmp_path / "deck"
    shutil.copytree(EXAMPLES / "decision-deck", folder)
    raw, stamped = tmp_path / "raw.zip", tmp_path / "deck.zip"
    assert make_package._zipper().main([str(folder), str(raw)]) == 0
    agent = onboarded_auth(session="sess-1").agent
    package_signature.sign(raw, stamped, agent.private, agent.public, "sess-1", (package_signature.DECISION_CHECKED,))
    (tmp_path / "work").mkdir()
    item = work_store.create("Office tea service", "folder", str(tmp_path / "work"))
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    yield server, client, stamped.read_bytes(), item
    server.attention_record = None


def _through(client):
    """Answer the page's requests with JARVIS's own app, as from the page."""
    async def handle(route):
        request = route.request
        path = request.url.split("://", 1)[1].split("/", 1)[1]
        headers = ORIGIN if request.method != "GET" else {}
        if request.headers.get("content-type"):
            headers = {**headers, "Content-Type": request.headers["content-type"]}
        r = await asyncio.to_thread(client.request, request.method, "/" + path,
                                    content=request.post_data_buffer, headers=headers)
        await route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)
    return handle


async def _shot(page, name: str) -> None:
    folder = os.environ.get("PAGE_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(Path(folder) / f"{name}.png"))


@pytest.mark.asyncio(loop_scope="session")
async def test_a_package_arrives_unshown_and_is_opened_and_answered_on_his_click(
        page_playwright, page_url, jarvis_app, monkeypatch):
    server, client, data, item = jarvis_app
    from jarvis import package_inbox, work_packages, work_store

    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        voice = _Voice()
        await page.route_web_socket("**/ws/voice", voice)
        await page.route_web_socket("**/ws/sessions", lambda ws: None)
        await page.route("**/api/conversation", lambda route: route.fulfill(
            json={"items": [], "earlier": None}))
        for pattern in ("**/api/packages**", "**/api/attention**", "**/api/work**"):
            await page.route(pattern, _through(client))

        # The session it names counts as proven, by its listed name: this
        # test is about the page, and the proof has its own tests
        # (test_package_inbox.py, "who sent it").
        from jarvis import session_message_sender
        monkeypatch.setattr(server, "_package_sender", lambda claimed, reporter: (
            session_message_sender.Sender(claimed, "chitauri", "test") if claimed else None))

        # It arrives before the page loads, as make_package sends it.
        r = client.post("/api/packages/inbox", content=data,
                        params={"project": "tea", "session": "sess-1", "work": item.id},
                        headers={"Authorization": f"Bearer {server.data_paths.ensure_tool_token()}",
                                 "Content-Type": "application/zip"})
        assert r.status_code == 200, r.text
        inbox_id = r.json()["inbox"]["id"]

        # A page JARVIS shows packages on, its frames going to this page.
        async def emit(frame):
            voice.send(frame)
        monkeypatch.setattr(server, "_voice_emit", emit)
        monkeypatch.setattr(server, "voice_clients", {object()})

        await page.goto(page_url)
        await page.wait_for_selector(".right-tabs [aria-label='Queue']")
        await page.click(".right-tabs [aria-label='Queue']")

        # Listed at the top of the Queue, signed out of any team server.
        row = page.locator(f".queue-packages [data-inbox-id='{inbox_id}']")
        await async_api.expect(row).to_be_visible()
        await async_api.expect(row.locator(".row-person")).to_have_text("chitauri")
        await async_api.expect(row.locator(".package-title")).to_have_text("Office tea: decisions")
        await async_api.expect(row.locator(".package-progress")).to_have_text("0 of 2 answered")
        await async_api.expect(page.locator("#package-view")).to_have_count(0)

        # One reduced line in Updates waiting: who, the title, how many wait.
        await page.click("#updates-notice button[aria-expanded]")
        line = page.locator(f"#updates-notice .updates-package[data-inbox-id='{inbox_id}']")
        await async_api.expect(line.locator(".updates-words")).to_have_text(
            "Package from chitauri: Office tea: decisions, 2 decisions waiting")
        assert await line.locator("img, iframe, video, audio").count() == 0
        await _shot(page, "1-arrived-queue-and-update-line")
        assert await page.locator("#package-view").count() == 0, "nothing is shown on arrival"

        # His Open shows the deck in the centre.
        await row.get_by_role("button", name="Open").click()
        await async_api.expect(page.locator("#package-view iframe")).to_be_visible()
        await async_api.expect(row.locator(".package-progress")).to_have_text("0 of 2 answered")
        assert await until_async(lambda: package_inbox.get(inbox_id).state == package_inbox.OPENED), \
            package_inbox.get(inbox_id).state
        await page.wait_for_timeout(1500)
        await _shot(page, "2-opened-deck")

        # The work item's Decisions tab asks the package's questions.
        await page.click(".right-tabs [aria-label='Work']")
        await page.click(f"text=Office tea service")
        await async_api.expect(page.locator("#work-detail .work-decision-banner")).to_be_visible()
        await page.locator("#work-detail .work-decision-banner").click()
        package = page.locator(f"#work-detail .work-package[data-inbox-id='{inbox_id}']")
        await async_api.expect(package).to_be_visible()
        kettle = package.locator(".workflow-decision[data-key='kettle-kind']")
        await async_api.expect(kettle).to_be_visible()
        await _shot(page, "3-work-item-package-questions")
        await kettle.locator(".workflow-option.recommended").click()
        await async_api.expect(package.locator(".work-answered-item[data-key='kettle-kind']")) \
            .to_be_visible()
        assert await until_async(lambda: (package_inbox.get(inbox_id).decisions_answered,
                                          package_inbox.get(inbox_id).state) == (1, package_inbox.ANSWERING)), \
            package_inbox.get(inbox_id)
        entry = package_inbox.get(inbox_id)
        found = work_packages.find(entry.package_id)
        assert '"kettle-kind"' in (found.folder / "results" / "decisions.json").read_text()
        await _shot(page, "4-answered-on-the-work-item")

        # Done: on its Queue row, and in the head of its view, outside the
        # frame. Signed off from the view: the view closes, the row goes to
        # Closed, and a capture of his answers waits to be read back.
        await page.click(".right-tabs [aria-label='Queue']")
        await async_api.expect(row.get_by_role("button", name="Done")).to_be_visible()
        await row.get_by_role("button", name="Open").click()
        done = page.locator("#package-view .package-head .package-done")
        await async_api.expect(done).to_be_visible()
        assert await page.locator("#package-view iframe .package-done").count() == 0
        await _shot(page, "5-done-in-the-view-head")
        await done.click()
        await async_api.expect(page.locator("#package-view")).to_have_count(0)
        await async_api.expect(row).to_have_count(0)
        await async_api.expect(page.locator(".queue-packages .queue-history-toggle")) \
            .to_contain_text("Closed (1)")
        assert await until_async(lambda: (package_inbox.get(inbox_id).closed_why,
                                          bool(package_inbox.get(inbox_id).signed_off_at))
                                 == (package_inbox.SIGNED_OFF, True)), package_inbox.get(inbox_id)
        capture = next(c for c in server._captures.values() if c.get("to_session") == "sess-1")
        assert "kettle-kind: electric" in capture["text"] and "tea-kinds: not answered" in capture["text"]
        assert not [d for d in work_store.get(item.id).decisions], "its work item is told"
        await page.wait_for_timeout(500)
        await _shot(page, "6-signed-off")
        await browser.close()
