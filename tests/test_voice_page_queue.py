"""The Queue, by what the owner sees and does: the right panel's Queue tab
(QueuePanel.tsx), one item in the centre (QueueItemView.tsx), the inline
response (MessageComposer.tsx) and its review (MessageReview.tsx).

Headless Chromium on the built page. The page's messaging routes are
answered by `QueueRoutes` over Tim's world (tests/messaging_queue_scenario.py):
every message really sent through the contract's fake team server and
synced into the local store, so work states, receipts and drafts follow the
contract's own rules. Both sockets are Playwright's; a review's capture
arrives on the voice socket as JARVIS sends it, and closing the voice socket
is a reconnect, after which the page reads the queue again.
"""

import asyncio
import json

import pytest

from tests import messaging_queue_scenario as sc
from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice
from tests.test_voice_page_network import _Sessions

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

ROWS = """(list) => [...document.querySelectorAll(`.queue-list[data-list="${list}"] > li`)]
  .map(li => li.dataset.threadId)"""


async def _open(p, url, *, viewport=(1440, 900), reduced=False):
    world = await sc.build()
    voice = _Voice()
    routes = sc.QueueRoutes(world, voice)
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": viewport[0], "height": viewport[1]},
                                  reduced_motion="reduce" if reduced else "no-preference")
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", voice)
    await page.route_web_socket("**/ws/sessions", _Sessions())
    envelope = json.dumps(sc.network_envelope())
    await page.route("**/api/team/network", lambda r: r.fulfill(status=200, content_type="application/json",
                                                                 body=envelope))
    await routes.install(page)
    await page.goto(url)
    await page.wait_for_selector(".right-tabs [aria-label='Queue']")
    return browser, page, world, routes, voice


async def _queue_tab(page):
    await page.click(".right-tabs [aria-label='Queue']")
    await page.wait_for_selector('.queue-list[data-list="waiting"] > li')


def _names(world):
    return {str(m.thread_id): n for n, m in world.sent.items() if m.thread_id == m.message_id}


async def _rows(page, world, which):
    names = _names(world)
    return [names.get(t, t) for t in await page.evaluate(ROWS, which)]


def _row(world, name):
    return f'.queue-view li[data-thread-id="{world.thread(name)}"] .queue-row'


async def _arrive(world, voice, page, name, subject):
    """Matt sends Tim something new; Tim's JARVIS syncs it; the page hears
    of it (a reconnect makes it read again)."""
    await world.send(name, sc.MATT_MAC, sc.TIM, subject, "Arrived while you were busy.")
    await world.sync()
    await voice.ws.close()
    await page.wait_for_selector(f'.queue-view li[data-thread-id="{world.thread(name)}"]', timeout=15000)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_queue_tab_lists_waiting_then_active_newest_first(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, routes, _ = await _open(p, page_url)
        tabs = await page.evaluate("() => [...document.querySelectorAll('.right-tabs [role=tab]')].map(t => t.title)")
        assert tabs == ["Folders", "Agents", "Queue", "Workflows", "Network", "Diagnostics"], tabs
        # Something unread is waiting: the tab is lit while another is open.
        await async_api.expect(page.locator(".right-tabs [aria-label='Queue']")).to_have_class(
            "control-button right-tab labelled-tab lit")
        await _queue_tab(page)
        assert await _rows(page, world, "waiting") == ["newest", "creds", "long", "former", "self-file", "note-self"]
        assert await _rows(page, world, "active") == ["release", "budget", "deploy", "ana-numbers"]

        text = lambda name: page.locator(_row(world, name)).inner_text()  # noqa: E731
        newest = await text("newest")
        assert "Matt · Matt’s MacBook" in newest and "Your turn" in newest and "Unread" in newest
        deploy = await text("deploy")
        assert "Matt’s turn" in deploy and "Received by their JARVIS" in deploy and "Unread" not in deploy
        assert "Read by Ana" in await text("budget")
        assert "You replied" in await text("release")
        assert "Related" in await text("creds") and "You replied" not in await text("creds")
        assert "A member no longer listed · a JARVIS no longer listed" in await text("former")
        self_file = await text("self-file")
        assert "Me · Tim’s i9" in self_file and "Your turn" in self_file and "1 file" in self_file
        assert "Your turn" in await text("note-self")
        ana = await text("ana-numbers")
        assert "Your turn" in ana and "Reply drafted, not sent" in ana

        # Every icon is decorative; the words are the label.
        hidden = await page.evaluate("() => [...document.querySelectorAll('.queue-view svg')].every(s => s.getAttribute('aria-hidden') === 'true')")
        assert hidden
        # Long names and subjects stay inside their row and the panel.
        fits = await page.evaluate("""() => {
          const panel = document.querySelector('.right-panel').getBoundingClientRect();
          return [...document.querySelectorAll('.queue-row')].filter(r => {
            const b = r.getBoundingClientRect();
            return b.right > panel.right + 0.5 || b.left < panel.left - 0.5 || r.scrollWidth > r.clientWidth + 1;
          }).map(r => r.textContent);
        }""")
        assert fits == [], fits
        # Nothing about the queue was read or changed by looking at it.
        assert routes.reads == [] and not [c for c in routes.calls if c[0] != "GET"]
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_open_next_and_put_back(page_playwright, page_url):
    """Open makes it Active and reads it once the body has been shown; Next
    keeps the item left in Active; Put back returns it to its place."""
    async with page_playwright as p:
        browser, page, world, routes, _ = await _open(p, page_url)
        await _queue_tab(page)
        before = await _rows(page, world, "waiting")
        await page.click(_row(world, "newest"))
        await page.wait_for_selector("#queue-item-view .item-subject")
        assert await page.inner_text("#queue-item-view .item-subject") == "Can you look at this?"
        assert await page.inner_text(".centre-tabs [aria-selected=true]") == "CAN YOU LOOK AT THIS?"
        await page.wait_for_function("() => document.querySelector('.queue-message .queue-cue.cue-read-by-you')")
        assert routes.reads == [world.message("newest")]
        assert "newest" in await _rows(page, world, "active")
        await async_api.expect(page.locator(_row(world, "newest"))).to_have_attribute("aria-current", "true")

        await page.click("text=Next item")
        await page.wait_for_function(
            f"() => document.querySelector('#queue-item-view')?.dataset.threadId === '{world.thread('creds')}'")
        assert "newest" in await _rows(page, world, "active")
        await page.wait_for_function("() => document.querySelectorAll('.queue-message .cue-read-by-you').length === 1")
        assert routes.reads == [world.message("newest"), world.message("creds")]

        await page.click("#queue-item-view >> text=Put back")
        await page.wait_for_selector("#queue-item-view", state="detached")
        waiting = await _rows(page, world, "waiting")
        assert waiting == [n for n in before if n != "newest"], waiting
        # Read and Addressed are independent; neither is a reply.
        text = await page.locator(_row(world, "creds")).inner_text()
        assert "Read by you" in text and "Addressed" not in text and "You replied" not in text
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_respond_review_and_send_inline(page_playwright, page_url):
    """A response is written on the item, reviewed exactly as it will be
    sent, approved with the existing capture's Send, and stays Active as
    Matt's turn. Editing after a review invalidates it."""
    async with page_playwright as p:
        browser, page, world, routes, voice = await _open(p, page_url)
        await _queue_tab(page)
        await page.click(_row(world, "newest"))
        await page.click("#queue-item-view >> text=Respond")
        await async_api.expect(page.locator(".composer-to")).to_contain_text("To Matt · Matt’s MacBook")
        await page.fill(".composer-text", "Looking now.\nBack in ten.")
        await async_api.expect(page.locator(".composer-status")).to_have_text("Draft saved, not sent", timeout=5000)
        draft = next(d for d in world.store.drafts.values() if d.thread_id == world.sent["newest"].thread_id)
        assert draft.in_reply_to_message_id == world.sent["newest"].message_id
        assert draft.send.state == "draft"

        await page.click(".composer-review")
        review = page.locator(".message-review")
        await async_api.expect(review.locator(".review-to")).to_have_text("Matt · Matt’s MacBook")
        assert await review.locator(".review-text").inner_text() == "Looking now.\nBack in ten."
        await async_api.expect(review).to_have_attribute("data-content-revision", "1")
        await review.locator(".capture-send").wait_for()

        # An edit takes the review away; reviewing again is a new revision.
        await page.fill(".composer-text", "Looking now.\nBack in five.")
        await async_api.expect(review).to_have_count(0)
        await async_api.expect(page.locator(".composer-status")).to_have_text("Draft saved, not sent", timeout=5000)
        await page.click(".composer-review")
        await async_api.expect(review).to_have_attribute("data-content-revision", "2")
        assert not [m for m in world.serve.messages.values() if m.text.startswith("Looking now")], \
            "nothing is sent before the owner's Send"

        await review.locator(".capture-send").click()
        reply = page.locator(".queue-message.outgoing", has_text="Back in five.")
        await async_api.expect(reply).to_contain_text("Awaiting receipt", timeout=10000)
        sent = [m for m in world.serve.messages.values() if m.text.startswith("Looking now")]
        assert [(m.text, m.content_revision, m.in_reply_to_message_id) for m in sent] == \
            [("Looking now.\nBack in five.", 2, world.sent["newest"].message_id)]
        # Still here, still Active, now Matt's turn; the read is not a reply.
        await async_api.expect(page.locator("#queue-item-view .item-cues")).to_contain_text("Matt’s turn")
        assert "newest" in await _rows(page, world, "active")
        await async_api.expect(page.locator(_row(world, "newest"))).to_contain_text("You replied")
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_message_to_oneself_goes_the_real_way_and_is_your_turn(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, routes, voice = await _open(p, page_url)
        await _queue_tab(page)
        await page.click(".queue-new")
        await async_api.expect(page.locator(".composer-to option")).to_have_count(5)
        options = await page.evaluate("() => [...document.querySelectorAll('.composer-to option')].map(o => o.textContent)")
        assert options == ["Choose who it is for", "Me · Tim’s i9", "Matt · Matt’s MacBook",
                           "Ana · Ana’s Studio", f"{sc.LONG_NAME} · Max’s JARVIS"], options
        await page.select_option(".composer-to select", str(sc.TIM))
        await page.fill(".composer-field input", "Reminder")
        await page.fill(".composer-text", "Call the bank.")
        assert await page.evaluate("document.activeElement.className") == "composer-text"
        await async_api.expect(page.locator(".composer-status")).to_have_text("Draft saved, not sent", timeout=5000)
        # The first save gave it its own id without losing the editor.
        assert await page.evaluate("document.activeElement.className") == "composer-text"
        unsent = await _rows(page, world, "active")
        assert len(unsent) == 5 and unsent[0] not in _names(world).values()

        await page.click(".composer-review")
        await async_api.expect(page.locator(".message-review .review-to")).to_have_text("Me · Tim’s i9")
        await page.locator(".message-review .capture-send").click()
        mine = page.locator(".queue-message.outgoing", has_text="Call the bank.")
        await mine.wait_for(timeout=10000)
        await async_api.expect(page.locator("#queue-item-view .item-cues")).to_contain_text("Your turn")
        sent = [m for m in world.serve.messages.values() if m.subject == "Reminder"]
        assert len(sent) == 1 and sent[0].sender_github_id == sent[0].recipient_github_id == sc.TIM
        # One item, in Waiting (a message to oneself arrives), not a second row.
        thread = str(sent[0].thread_id)
        assert (await page.evaluate(ROWS, "waiting")).count(thread) == 1
        assert thread not in await page.evaluate(ROWS, "active")
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_an_arrival_never_takes_focus_text_or_the_review(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, routes, voice = await _open(p, page_url)
        await _queue_tab(page)
        await page.click(_row(world, "ana-numbers"))
        editor = page.locator(".composer-text")
        await async_api.expect(editor).to_have_value("Checking the second tab now.")
        await editor.click()
        await page.keyboard.press("End")
        await page.keyboard.type(" And the third.")
        scroll = await page.evaluate("document.querySelector('.item-scroll').scrollTop")
        await _arrive(world, voice, page, "interrupt", "Urgent?")
        assert await page.evaluate("document.activeElement.className") == "composer-text"
        await async_api.expect(editor).to_have_value("Checking the second tab now. And the third.")
        assert await page.evaluate("document.querySelector('#queue-item-view').dataset.threadId") == \
            world.thread("ana-numbers")
        assert await page.evaluate("document.querySelector('.item-scroll').scrollTop") == scroll
        assert world.message("interrupt") not in routes.reads
        assert (await _rows(page, world, "waiting"))[0] == "interrupt"
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_done_waits_for_unsent_work_and_says_why(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, routes, _ = await _open(p, page_url)
        await _queue_tab(page)
        # Finished work dismisses at once.
        await page.click(_row(world, "budget"))
        await page.click("#queue-item-view >> text=Done")
        await page.wait_for_selector("#queue-item-view", state="detached")
        assert "budget" not in await _rows(page, world, "active")

        # Unsent text does not.
        await page.click(_row(world, "ana-numbers"))
        await page.click("#queue-item-view >> text=Done")
        blockers = page.locator(".item-blockers")
        await async_api.expect(blockers).to_contain_text("There is a reply here that has not been sent.")
        assert "ana-numbers" in await _rows(page, world, "active")
        await blockers.locator("text=Discard the reply…").click()
        await blockers.locator("text=Discard the reply and dismiss").click()
        await page.wait_for_selector("#queue-item-view", state="detached")
        assert "ana-numbers" not in await _rows(page, world, "active")
        assert not [d for d in world.store.drafts.values() if d.thread_id == world.sent["ana-numbers"].thread_id]

        # Both are in the dismissed history, with their facts.
        await page.click(".queue-history-toggle")
        await page.wait_for_selector('.queue-list[data-list="dismissed"] > li')
        assert set(await _rows(page, world, "dismissed")) == {"budget", "ana-numbers", "lunch"}
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_dismissed_item_opens_for_reference_and_restores_with_its_history(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, routes, _ = await _open(p, page_url)
        await _queue_tab(page)
        await page.click(".queue-history-toggle")
        assert await page.inner_text(".queue-history-toggle") == "▾ Hide dismissed (1)"
        await page.click(".queue-done-row")
        await async_api.expect(page.locator("#queue-item-view .item-cues")).to_contain_text("Dismissed")
        assert not [c for c in routes.calls if c[0] == "PUT"], "looking is not reactivating"
        await page.click("#queue-item-view >> text=Restore to queue")
        await page.wait_for_function(
            f"() => [...document.querySelectorAll('.queue-list[data-list=\"waiting\"] > li')].some(li => li.dataset.threadId === '{world.thread('lunch')}')")
        text = await page.locator(_row(world, "lunch")).inner_text()
        assert "Read by you" in text and "Dismissed" not in text
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_save_keeps_the_text_through_navigation(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, routes, _ = await _open(p, page_url)
        await _queue_tab(page)
        routes.fail_saves = True
        await page.click(_row(world, "newest"))
        await page.click("#queue-item-view >> text=Respond")
        await page.fill(".composer-text", "Do not lose this.")
        await async_api.expect(page.locator(".composer-error")).to_contain_text("Not saved", timeout=5000)
        await page.click(_row(world, "creds"))
        await page.click(_row(world, "newest"))
        await async_api.expect(page.locator(".composer-text")).to_have_value("Do not lose this.")
        # Put back refuses while it cannot be saved, and says why.
        await page.click("#queue-item-view >> text=Put back")
        await async_api.expect(page.locator(".composer-error")).to_contain_text("Your text is kept here.")
        assert await page.locator("#queue-item-view").count() == 1
        routes.fail_saves = False
        await page.click("#queue-item-view >> text=Put back")
        await page.wait_for_selector("#queue-item-view", state="detached")
        saved = [d for d in world.store.drafts.values() if d.thread_id == world.sent["newest"].thread_id]
        assert [d.text for d in saved] == ["Do not lose this."]
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_command_kept_offline_says_so_and_the_queue_stays(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, routes, voice = await _open(p, page_url)
        await _queue_tab(page)
        routes.offline = True
        await page.click(_row(world, "deploy"))
        await page.click("#queue-item-view >> text=Mark addressed")
        await async_api.expect(page.locator("#queue-item-view .item-note.warn")).to_have_text(
            "Kept on this JARVIS until the team server answers.")
        await voice.ws.close()
        await async_api.expect(page.locator(".queue-notice.stale")).to_be_visible(timeout=15000)
        assert len(await _rows(page, world, "waiting")) == 6
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_read_or_a_lost_pairing_keeps_the_queue_and_says_why(page_playwright, page_url):
    async with page_playwright as p:
        browser, page, world, routes, voice = await _open(p, page_url)
        await _queue_tab(page)
        before = await _rows(page, world, "waiting")
        routes.down = True
        await voice.ws.close()
        await async_api.expect(page.locator(".queue-view .read-failure")).to_contain_text(
            "Couldn't read the queue", timeout=15000)
        assert await _rows(page, world, "waiting") == before, "a failed read never empties the queue"
        routes.down = False
        routes.availability = "not_paired"
        await async_api.expect(page.locator(".queue-notice")).to_have_text(
            "Pair this JARVIS with the team server to send and receive messages.", timeout=20000)
        await async_api.expect(page.locator(".queue-view .read-failure")).to_have_count(0)
        assert await _rows(page, world, "waiting") == before
        assert await page.locator(".queue-new").is_disabled()
        await browser.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_keyboard_only_and_narrow_and_still(page_playwright, page_url):
    """A narrow window and reduced motion: rows fit, nothing in the Queue
    animates, and an item opens and closes from the keyboard with focus
    returned to the row."""
    async with page_playwright as p:
        browser, page, world, routes, _ = await _open(p, page_url, viewport=(1024, 700), reduced=True)
        await _queue_tab(page)
        row = page.locator(_row(world, "former"))
        await row.focus()
        await page.keyboard.press("Enter")
        await page.wait_for_selector("#queue-item-view")
        assert await row.get_attribute("aria-current") == "true"
        await page.locator("#queue-item-view >> text=Close").focus()
        await page.keyboard.press("Enter")
        await page.wait_for_selector("#queue-item-view", state="detached")
        await page.wait_for_function("() => document.activeElement?.classList.contains('queue-row')")
        assert await page.evaluate("document.activeElement.closest('li').dataset.threadId") == world.thread("former")
        moving = await page.evaluate("() => document.querySelector('.queue-view').getAnimations({subtree: true}).length")
        assert moving == 0
        fits = await page.evaluate("""() => [...document.querySelectorAll('.queue-row')]
          .filter(r => r.scrollWidth > r.clientWidth + 1).length""")
        assert fits == 0
        await browser.close()
