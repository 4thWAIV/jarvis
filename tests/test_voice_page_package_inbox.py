"""A package on the page, in a real browser: a decision deck sent the way
make_package sends it (as a message carrying it; here through JARVIS's own inbox path, for a work item, from a
session) shows nowhere on arrival; it is a message on its Work item, so
Updates waiting counts it in its one line, whose Open goes to the Inbox,
where the item is one line that opens its Messages tab; there the message
carries the package as a card, and his Open shows it in the centre; what
he chose in it is recorded under the message; and Done in the view's head
signs it off: the view closes, the message is done, and a capture of his
answers waits to be read back. A package for no item is a message in the
Inbox, opened from there.

Headless Chromium on the built page. Every /api/packages, /api/attention
and /api/work request is answered by JARVIS's real app (TestClient) over a
temporary JARVIS_DATA_DIR; a POST carries the page's own Origin, as the
browser's would. Screenshots go to $PAGE_SHOTS when it is set.
"""

import asyncio
import importlib
import json
import os
import shutil
from pathlib import Path

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice
from tests.fixtures.waiting import until_async
from tests.settings_fixtures import hold_setting

pytestmark = pytest.mark.page

EXAMPLES = Path(__file__).parent.parent / "examples" / "packages"
ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture
def jarvis_app(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    hold_setting("brain_autostart", "0")
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
    from tests.author_fixtures import sign_authored
    sign_authored(raw, stamped, agent, "sess-1", (package_signature.DECISION_CHECKED,))
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


class _Sessions:
    """/ws/sessions, held so a test can send the server's hints."""

    def __init__(self):
        self.ws = None

    def __call__(self, ws):
        self.ws = ws

    def hint(self, kind: str) -> None:
        self.ws.send(json.dumps({"type": kind}))


def _proven(server, monkeypatch):
    """The session a package names counts as proven, by its listed name:
    these tests are about the page, and the proof has its own tests
    (test_package_inbox.py, "who sent it")."""
    from jarvis import session_message_sender
    monkeypatch.setattr(server, "_package_sender", lambda claimed, reporter: (
        session_message_sender.Sender(claimed, "chitauri", "agent_signature") if claimed else None))


async def _page(p, client, voice, sessions):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                          # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", sessions)
    await page.route("**/api/conversation", lambda route: route.fulfill(
        json={"items": [], "earlier": None}))
    for pattern in ("**/api/packages**", "**/api/attention**", "**/api/work**", "**/api/messages**"):
        await page.route(pattern, _through(client))
    return browser, page


@pytest.mark.asyncio(loop_scope="session")
async def test_a_package_arrives_unshown_as_a_message_on_its_item_and_is_opened_on_his_click(
        page_playwright, page_url, jarvis_app, monkeypatch):
    server, client, data, item = jarvis_app
    from jarvis import package_inbox, work_packages, work_store
    _proven(server, monkeypatch)

    async with page_playwright as p:
        voice, sessions = _Voice(), _Sessions()
        browser, page = await _page(p, client, voice, sessions)

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
        # Updates waiting says one line, never the package's own: Open goes to the Inbox.
        line = page.locator("#updates-notice .updates-messages")
        await async_api.expect(line).to_contain_text("1 new message")
        await async_api.expect(page.locator("#updates-notice .updates-package")).to_have_count(0)
        assert await page.locator("#package-view").count() == 0, "nothing is shown on arrival"
        await line.get_by_role("button", name="Open").click()
        assert await page.evaluate(
            "() => document.querySelector('.right-tabs [aria-selected=true]').title") == "Inbox"

        # The Inbox: the Work item with a new message, one line; no Packages section.
        work_line = page.locator(f".inbox-work-row")
        await async_api.expect(work_line).to_contain_text("Office tea service")
        await async_api.expect(work_line).to_contain_text("1 new")
        await async_api.expect(page.locator(".queue-packages")).to_have_count(0)
        await _shot(page, "1-arrived-inbox")

        # It opens the item's Messages tab: the message, from its proven
        # sender, NEW, carrying the package with where it stands.
        await work_line.click()
        selected = page.locator("#work-detail .work-tabs [aria-selected=true]")
        await async_api.expect(selected).to_contain_text("Messages (")
        card = page.locator(f"#work-detail .message-card:has(.message-package[data-inbox-id='{inbox_id}'])")
        await async_api.expect(card.locator(".message-who")).to_have_text("chitauri")
        await async_api.expect(card.locator(".message-new")).to_have_text("NEW")
        await async_api.expect(card.locator(".message-says")).to_contain_text("Package: Office tea: decisions")
        await async_api.expect(card.locator(".package-progress")).to_have_text("0 of 2 answered")
        names = await page.locator("#work-detail .work-tabs [role=tab]").all_text_contents()
        assert names[0].startswith("Messages (") and "Decisions" not in " ".join(names), names
        await _shot(page, "2-messages-tab")

        # His Open shows the deck in the centre.
        await card.locator(".message-package").get_by_role("button", name="Open").click()
        await async_api.expect(page.locator("#package-view iframe")).to_be_visible()
        assert await until_async(lambda: package_inbox.get(inbox_id).state == package_inbox.OPENED), \
            package_inbox.get(inbox_id).state
        await page.wait_for_timeout(1000)
        await _shot(page, "3-opened-deck")

        # He answers in the package (its own record, as its vote does), and
        # what he chose is recorded under the message.
        r = client.post(f"/api/packages/inbox/{inbox_id}/answer", headers=ORIGIN,
                        json={"decision": "kettle-kind", "option": "electric", "words": ""})
        assert r.status_code == 200, r.text
        entry = package_inbox.get(inbox_id)
        assert '"kettle-kind"' in (work_packages.find(entry.package_id).folder / "results"
                                   / "decisions.json").read_text()
        await page.click(".right-tabs [aria-label='Work']")
        await page.click("text=Office tea service")
        sessions.hint("work")
        await async_api.expect(card.locator(".message-chose")).to_have_text(
            "You chose: An electric kettle", timeout=10000)
        await async_api.expect(card.locator(".package-progress")).to_have_text("1 of 2 answered")
        await _shot(page, "4-you-chose")

        # Done in the head of its view signs it off: the view closes, the
        # message is done, and a capture of his answers waits to be read back.
        await card.locator(".message-package").get_by_role("button", name="Open").click()
        done = page.locator("#package-view .package-head .package-done")
        await async_api.expect(done).to_be_visible()
        assert await page.locator("#package-view iframe .package-done").count() == 0
        await done.click()
        await async_api.expect(page.locator("#package-view")).to_have_count(0)
        assert await until_async(lambda: (package_inbox.get(inbox_id).closed_why,
                                          bool(package_inbox.get(inbox_id).signed_off_at))
                                 == (package_inbox.SIGNED_OFF, True)), package_inbox.get(inbox_id)
        assert await until_async(
            lambda: server.attention_record.get("package", inbox_id).state == "resolved"), \
            server.attention_record.get("package", inbox_id).state
        [message] = [m for m in server.conversation_log.work_thread(item.id) if m.carries == inbox_id]
        assert message.state == "done"
        capture = next(c for c in server._captures.values() if c.get("to_session") == "sess-1")
        assert "kettle-kind: electric" in capture["text"] and "tea-kinds: not answered" in capture["text"]
        assert not [d for d in work_store.get(item.id).decisions], "its work item is told"
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_page_s_standard_vote_is_answered_in_the_view_s_head(
        page_playwright, page_url, jarvis_app, monkeypatch):
    """A hand-made page as make_package sends it, its story ending with the
    standard vote: Got it and I have questions sit in the view's head,
    outside the frame, and each lands in the story's answers file through
    the inbox's answer route, as his voice's answer does."""
    server, client, _data, _item = jarvis_app
    from jarvis import package_inbox, work_packages
    from tests.test_package_inbox import VOTES, story_zip
    _proven(server, monkeypatch)

    async with page_playwright as p:
        voice = _Voice()
        browser, page = await _page(p, client, voice, _Sessions())

        r = client.post("/api/packages/inbox", content=story_zip(VOTES), params={"session": "sess-1"},
                        headers={"Authorization": f"Bearer {server.data_paths.ensure_tool_token()}",
                                 "Content-Type": "application/zip"})
        assert r.status_code == 200, r.text
        inbox_id = r.json()["inbox"]["id"]
        package = work_packages.find(package_inbox.get(inbox_id).package_id)

        async def emit(frame):
            voice.send(frame)
        monkeypatch.setattr(server, "_voice_emit", emit)
        monkeypatch.setattr(server, "voice_clients", {object()})

        await page.goto(page_url)
        await page.click(".right-tabs [aria-label='Inbox']")
        # A package for no Work item is a message in the Inbox; Open is his click.
        row = page.locator(f".inbox-general .message-package[data-inbox-id='{inbox_id}']")
        await row.get_by_role("button", name="Open").click()
        vote = page.locator("#package-view .package-head .package-vote")
        await async_api.expect(vote).to_be_visible()
        assert await page.locator("#package-view iframe .package-vote").count() == 0
        await _shot(page, "vote-1-in-the-head")

        def recorded():
            return package_inbox.questions(package)[0].answer

        await vote.get_by_role("button", name="Got it").click()
        await async_api.expect(vote.locator(".package-vote-recorded")).to_have_text("Recorded: Got it.")
        assert recorded()["answer"] == "got-it"

        await vote.get_by_role("button", name="I have questions").click()
        await vote.locator(".package-vote-words").fill("Why two keys?")
        await _shot(page, "vote-2-his-words")
        await vote.get_by_role("button", name="Send").click()
        await async_api.expect(vote.locator(".package-vote-recorded")).to_have_text(
            "Recorded: your questions go back to the sender.")
        assert (recorded()["answer"], recorded()["note"]) == ("questions", "Why two keys?")
        assert package_inbox.get(inbox_id).decisions_answered == 1
        await browser.close()
