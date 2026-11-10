"""Messages to him on the page (one channel round 2,
docs/planning/one-channel/messages-by-work-item.md, "What he will see" 2-4),
in a real browser over JARVIS's real app:

  - Updates waiting says one line, "3 new messages", whose Open goes to the
    Inbox; no message is an update line of its own;
  - the Inbox (the tab that was the Queue) lists each Work item with new
    messages as one line, and the messages that belong to no item, each
    with Read and Reply;
  - a Work row counts its new messages beside its state, and its update is
    the first new message's first line;
  - the item opens on its Messages tab, counted by its new messages and
    open decisions: the conversation oldest first, his reply in it, a
    decision asked the old way answered where its message is ("You
    chose: ..."), Reply addressed to the proven sender, Mark read;
  - a message recorded while it is shown arrives with the `inbox` hint.

Headless Chromium on the built page; every /api/messages, /api/work,
/api/packages and /api/attention request is answered by JARVIS's own app
(TestClient) over a temporary JARVIS_DATA_DIR. Pictures go to
$PAGE_SHOTS when it is set.
"""

import pytest

from jarvis import conversation_log as cl
from jarvis.conversation_log import ConversationItem, Counterpart, ItemKind
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_conversation import _Voice
from tests.test_voice_page_package_inbox import (_page, _Sessions, _shot,  # noqa: F401
                                                 jarvis_app)

pytestmark = pytest.mark.page


def _from_agent(text: str, source: str, about: str = "") -> ConversationItem:
    """A message an agent sent him, as the signed route records it."""
    return cl.inbound(ItemKind.MESSAGE_IN, Counterpart.AGENT, "sess-1", "chitauri", text,
                      "agent_signature", source_kind=cl.Source.SIGNED, source_id=source,
                      sender="agent:sess-1", sender_name="chitauri", about=about)


@pytest.mark.asyncio(loop_scope="session")
async def test_messages_are_counted_in_one_line_listed_in_the_inbox_and_read_on_their_item(
        page_playwright, page_url, jarvis_app, monkeypatch):
    server, client, _data, item = jarvis_app
    from jarvis import messages_api, work_store
    first = _from_agent("What is left: two pieces are open.\nThe rest is done.", "m1", item.id)
    # A decision asked the old way (work_report.py decision), until round 3.
    asked = {"key": "first-tea", "options": ["Assam", "Darjeeling"], "asked_by": "agent:sess-1"}
    item = work_store.record(work_store.WorkEvent(item.id, "decision_needed", "Which tea first?",
                                                  asked, reporter="agent:sess-1"))
    server._work_recorded(item, work_store.DECISION_NEEDED, asked)     # as the route does
    general = _from_agent("CI is green on main.", "m2")
    sender = messages_api.message_json(first)["from"]["name"]

    async with page_playwright as p:
        sessions = _Sessions()
        browser, page = await _page(p, client, _Voice(), sessions)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(page_url)

        # One line in Updates waiting: every new message, with Open to the Inbox.
        line = page.locator("#updates-notice .updates-messages")
        await async_api.expect(line).to_contain_text("3 new messages", timeout=10000)
        await async_api.expect(page.locator("#updates-notice .updates-session-message")).to_have_count(0)
        await line.get_by_role("button", name="Open").click()
        work_line = page.locator(f".inbox-work-row")
        await async_api.expect(work_line).to_contain_text("2 new")
        card = page.locator(f".inbox-general .message-card[data-message-id='{general.id}']")
        await async_api.expect(card.locator(".message-says")).to_have_text("CI is green on main.")
        await async_api.expect(card.locator(".message-new")).to_be_visible()
        await page.locator("#right-column").screenshot(path=str(_shots("inbox.png")))
        await _shot(page, "messages-1-inbox")
        # Reply on a message with no item: a capture to its proven sender.
        await card.get_by_role("button", name="Reply").click()
        await async_api.expect(card.locator(".file-state")).to_contain_text("Your reply to")
        assert any(c.get("reply_to") == general.id for c in server._captures.values())

        # The Work row: the count beside its state, the first new message's first line.
        await page.click(".right-tabs [aria-label='Work']")
        row = page.locator(f".work-row[data-work-id='{item.id}']")
        await async_api.expect(row.locator(".work-message-count")).to_have_text("2", timeout=10000)
        await async_api.expect(row.locator(".work-update")).to_have_text("What is left: two pieces are open.")
        await row.screenshot(path=str(_shots("work-row.png")))

        # The item opens on its Messages tab, counted: 2 new and 1 decision open.
        await row.click()
        tabs = page.locator("#work-detail .work-tabs [role=tab]")
        await async_api.expect(tabs.first).to_have_text("Messages (3)", timeout=10000)
        assert (await tabs.all_text_contents())[1:] == ["Overview", "Blocking (1)", "Pieces", "Reports"]
        cards = page.locator("#work-detail .message-card")
        await async_api.expect(cards).to_have_count(2)
        mine = cards.nth(0)
        await async_api.expect(mine.locator(".message-who")).to_have_text(sender)
        await async_api.expect(mine.locator(".message-says")).to_have_text(
            "What is left: two pieces are open.\nThe rest is done.")
        decision = cards.nth(1).locator(".workflow-decision[data-key='first-tea']")
        await async_api.expect(decision).to_be_visible()
        await page.locator("#work-detail").screenshot(path=str(_shots("messages-tab.png")))
        await _shot(page, "messages-2-tab")

        # Reply on an agent's message: addressed to its sender, about the item.
        await mine.get_by_role("button", name="Reply").click()
        await async_api.expect(mine.locator(".file-state")).to_contain_text("Your reply to")
        capture = next(c for c in server._captures.values() if c.get("reply_to") == first.id)
        assert (capture["to_session"], capture["about"]) == ("sess-1", item.id)

        # The decision asked the old way is answered where its message is.
        await decision.locator(".workflow-option:text('Assam')").click()
        await async_api.expect(cards.nth(1).locator(".message-chose")).to_have_text("You chose: Assam",
                                                                                timeout=10000)
        assert work_store.get(item.id).decisions == []

        # Mark read: no longer new, and the tab counts what is left.
        await mine.get_by_role("button", name="Mark read").click()
        await async_api.expect(mine.locator(".message-new")).to_have_count(0)
        assert cl.get(first.id).state == "read"
        await async_api.expect(tabs.first).to_have_text("Messages")

        # His reply is in the thread, and a message arriving is drawn on its hint.
        cl.append(ConversationItem.new(ItemKind.TO_AGENT, "Good, go on.", agent_session_id="sess-1",
                                       message_id=9, about=item.id, detail={"reply_to": first.id}))
        _from_agent("Done: both pieces are in.", "m3", item.id)
        sessions.hint("inbox")
        await async_api.expect(cards).to_have_count(4, timeout=10000)
        await async_api.expect(cards.nth(2)).to_have_class("message-card mine")
        await async_api.expect(cards.nth(2).locator(".message-who")).to_have_text("You")
        await async_api.expect(cards.nth(3).locator(".message-new")).to_be_visible()
        await async_api.expect(tabs.first).to_have_text("Messages (1)")
        assert errors == [], errors
        await browser.close()


def _shots(name: str):
    """Where a picture of this test's views goes: $PAGE_SHOTS, else a
    temporary folder."""
    import os
    import tempfile
    from pathlib import Path
    folder = Path(os.environ.get("PAGE_SHOTS") or tempfile.gettempdir())
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name
