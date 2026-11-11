"""The Inbox's messages from agents, in a real browser over JARVIS's real
app (Work item work-b93db25f6aac, piece one-channel-fixes):

  - only the new messages that belong to no item are in view; the read and
    answered ones are under Read, closed to begin with ("Read (2)"), and a
    message he is done with is nowhere;
  - Read opens and closes like Work's History;
  - a message marked read leaves the new list for Read at once;
  - the page's minimise mark (named Hide messages) folds the messages away
    under their heading ("From agents"), so the rest of the Inbox is in
    view, and Updates waiting's new messages line has the same mark; this
    browser keeps both minimised after a reload, and the mark (Show
    messages) brings them back.

Headless Chromium on the built page; every /api/messages request is
answered by JARVIS's own app (TestClient) over a temporary
JARVIS_DATA_DIR. Pictures go to $PAGE_SHOTS when it is set.
"""

import pytest

from jarvis import conversation_log as cl
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_conversation import _Voice
from tests.test_voice_page_messages import _from_agent, _shots
from tests.test_voice_page_package_inbox import _page, _Sessions, jarvis_app  # noqa: F401

pytestmark = pytest.mark.page


@pytest.mark.asyncio(loop_scope="session")
async def test_read_messages_wait_under_read_and_the_messages_can_be_hidden(
        page_playwright, page_url, jarvis_app):
    server, client, _data, item = jarvis_app
    new = _from_agent("CI is green on main.", "n1")
    read = _from_agent("The deck is checked.", "r1")
    answered = _from_agent("Which branch?", "a1")
    done = _from_agent("Package signed off.", "d1")
    later = _from_agent("Two pieces are left.", "n2")
    cl.mark(read.id, cl.MessageState.READ)
    cl.mark(answered.id, cl.MessageState.ANSWERED)
    cl.mark(done.id, cl.MessageState.DONE)

    async with page_playwright as p:
        sessions = _Sessions()
        browser, page = await _page(p, client, _Voice(), sessions)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(page_url)
        await page.click(".right-tabs [aria-label='Inbox']")

        general = page.locator(".inbox-general .message-card")
        await async_api.expect(general).to_have_count(2, timeout=10000)
        assert await general.evaluate_all("cs => cs.map(c => +c.dataset.messageId)") == [later.id, new.id]
        heading = page.locator("#inbox-agents-heading")
        await async_api.expect(heading).to_contain_text("From agents")
        toggle = page.locator(".inbox-read .queue-history-toggle")
        await async_api.expect(toggle).to_have_text("▸ Read (2)")
        await async_api.expect(toggle).to_have_attribute("aria-expanded", "false")
        await async_api.expect(page.locator(".inbox-read .message-card")).to_have_count(0)
        await async_api.expect(page.locator(f".message-card[data-message-id='{done.id}']")).to_have_count(0)
        await page.locator("#right-column").screenshot(path=str(_shots("inbox-read-closed.png")))

        # Read opens like Work's History: the read and answered ones, newest first.
        await toggle.click()
        shelf = page.locator(".inbox-read .message-card")
        await async_api.expect(shelf).to_have_count(2, timeout=10000)
        assert await shelf.evaluate_all("cs => cs.map(c => +c.dataset.messageId)") == [answered.id, read.id]
        await async_api.expect(page.locator(".inbox-read .message-new")).to_have_count(0)
        await page.locator("#right-column").screenshot(path=str(_shots("inbox-read-open.png")))
        await toggle.click()
        await async_api.expect(shelf).to_have_count(0)

        # Marked read, a message leaves the new list for Read.
        card = page.locator(f".inbox-general .message-card[data-message-id='{new.id}']")
        await card.get_by_role("button", name="Mark read").click()
        await async_api.expect(general).to_have_count(1, timeout=10000)
        await async_api.expect(toggle).to_have_text("▸ Read (3)")
        assert cl.get(new.id).state == "read"

        # The minimise mark: only the heading stays, and the rest of the Inbox is in view.
        hide = page.locator(".inbox-agents .minimise-toggle")
        await async_api.expect(hide).to_have_text("▾")
        await async_api.expect(hide).to_have_attribute("aria-label", "Hide messages")
        await async_api.expect(hide).to_have_attribute("title", "Hide messages")
        await page.locator("#right-column").screenshot(path=str(_shots("inbox-minimise-shown.png")))
        await hide.click()
        await async_api.expect(hide).to_have_text("▸")
        await async_api.expect(hide).to_have_attribute("aria-label", "Show messages")
        await async_api.expect(hide).to_have_attribute("aria-expanded", "false")
        await async_api.expect(page.locator(".inbox-agents .message-card")).to_have_count(0)
        await async_api.expect(page.locator(".inbox-read")).to_have_count(0)
        await async_api.expect(heading).to_be_visible()
        await async_api.expect(page.locator(".queue-view .queue-notice")).to_be_visible()
        await page.locator("#right-column").screenshot(path=str(_shots("inbox-minimise-minimised.png")))

        # Updates waiting's new messages line has the same mark.
        line = page.locator("#updates-notice .updates-messages")
        mark = line.locator(".minimise-toggle")
        await async_api.expect(line).to_contain_text("1 new message")
        await async_api.expect(mark).to_have_text("▾")
        await async_api.expect(mark).to_have_attribute("aria-label", "Hide messages")
        await page.locator("#updates-notice").screenshot(path=str(_shots("updates-minimise-shown.png")))
        await mark.click()
        await async_api.expect(mark).to_have_text("▸")
        await async_api.expect(mark).to_have_attribute("aria-label", "Show messages")
        await async_api.expect(line).to_have_text("▸1 new")
        await async_api.expect(line.get_by_role("button", name="Open")).to_have_count(0)
        await page.locator("#updates-notice").screenshot(path=str(_shots("updates-minimise-minimised.png")))

        # This browser keeps both minimised after a reload; the marks bring them back.
        await page.reload()
        await async_api.expect(mark).to_have_text("▸", timeout=10000)
        await mark.click()
        await async_api.expect(line.get_by_role("button", name="Open")).to_be_visible()
        await page.click(".right-tabs [aria-label='Inbox']")
        await async_api.expect(page.locator(".inbox-agents .minimise-toggle")).to_have_attribute(
            "aria-label", "Show messages", timeout=10000)
        await async_api.expect(page.locator(".inbox-agents .message-card")).to_have_count(0)
        await page.locator(".inbox-agents .minimise-toggle").click()
        await async_api.expect(general).to_have_count(1)
        await async_api.expect(page.locator(".inbox-read .queue-history-toggle")).to_have_text("▸ Read (3)")
        assert errors == [], errors
        await browser.close()
