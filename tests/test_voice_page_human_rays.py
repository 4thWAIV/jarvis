"""A person's messages on the agents' rays: a `human_transfer` frame on
/ws/sessions draws the same ray, with the same palette, pulses and rings, to
the queue item's row (the element marked `data-transfer-endpoint` with its
thread id), or to the Queue tab's icon when the row is not on the page; an
incoming one travels the other way and the main orb receives it. A replayed
or repeated event draws nothing; a burst shows at most five rays and counts
the rest; reduced motion draws a still route.

Headless Chromium on the built page (built by conftest's `page_url`), with
/api/agents, /api/team/network and /ws/sessions replaced by Playwright. The
Queue's own markup belongs to the queue stream; until it is on the page the
test puts a row and a Queue tab of that shape there itself.
"""

import asyncio
import json
import re
import uuid

import pytest

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_rays import AGENTS, PRIMARY, SECOND, RAYS, _segments, _to

TIM, MATT = 863470, 1001
THREAD = "6f0b1c3e-0000-4000-8000-000000000001"
NETWORK = {"snapshot": {"revision": 1, "server_time": "2026-10-29T12:00:00Z",
                        "you_node_id": "00000000-0000-0000-0000-000000000001",
                        "nodes": [{"node_id": "00000000-0000-0000-0000-000000000003",
                                   "owner_github_id": MATT, "owner_login": "matt"}],
                        "server": {}},
           "received_at": None, "stale": False, "last_error": None, "round_trip_ms": None,
           "last_contact_at": None, "connection": "connected"}


def _event(direction="outgoing", *, sender=TIM, recipient=MATT, thread=THREAD, replay=False,
           event_id=None):
    return {"type": "human_transfer", "event": {
        "event_id": event_id or f"human:{uuid.uuid4()}", "message_id": str(uuid.uuid4()),
        "thread_id": thread, "direction": direction, "owner_github_id": TIM,
        "source_node_id": "00000000-0000-0000-0000-000000000001",
        "sender_github_id": sender, "recipient_github_id": recipient,
        "occurred_at": "2026-10-29T12:00:00Z", "replay": replay}}


# The queue stream's shape, until it is on the page: a Queue tab in the
# right panel's tab row, and a row carrying its item's thread id.
QUEUE_MARKUP = """(thread) => {
  const tabs = document.querySelector('.right-tabs');
  const tab = document.createElement('button');
  tab.className = 'control-button right-tab';
  tab.setAttribute('aria-label', 'Queue');
  tab.textContent = 'Q';
  tabs.appendChild(tab);
  if (thread) {
    const row = document.createElement('div');
    row.setAttribute('data-transfer-endpoint', thread);
    row.style.cssText = 'position:fixed; right:12vw; top:300px; width:24px; height:24px';
    document.body.appendChild(row);
  }
}"""


async def _open(p, url, script, *, reduced=False, row=True):
    """The page with the Queue's markup on it; the fake /ws/sessions sends
    each message once its asyncio.Event is set, or after its delay."""
    try:
        browser = await p.chromium.launch(headless=True)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900},
                                  reduced_motion="reduce" if reduced else "no-preference")
    await page.route("**/api/agents", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(AGENTS)))
    await page.route("**/api/team/network", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(NETWORK)))
    ready = asyncio.Event()

    def sessions(ws):
        async def run():
            await ready.wait()
            for delay, m in script:
                if isinstance(delay, asyncio.Event):
                    await delay.wait()
                else:
                    await asyncio.sleep(delay)
                ws.send(json.dumps(m))
        asyncio.ensure_future(run())
    await page.route_web_socket("**/ws/sessions", sessions)
    await page.goto(url)
    await page.wait_for_selector(".right-tabs")
    await page.evaluate(QUEUE_MARKUP, THREAD if row else None)
    ready.set()
    return browser, page


def _centre(page, selector):
    return page.evaluate("""(sel) => { const b = document.querySelector(sel).getBoundingClientRect();
      return [b.left + b.width / 2, b.top + b.height / 2]; }""", selector)


ROW = f"[data-transfer-endpoint='{THREAD}']"
QUEUE_TAB = ".right-tabs [aria-label='Queue']"
GROUPS = """() => [...document.querySelectorAll('#ray-lane .ray')].map(g => ({
  id: g.dataset.rayTo, direction: g.dataset.direction, motion: g.dataset.motion,
  pulses: g.querySelectorAll('.ray-pulse').length,
  pulseDirection: g.querySelector('.ray-pulse') && g.querySelector('.ray-pulse').style.animationDirection,
  rings: [...g.querySelectorAll('.ray-receipt')].map(c => [+c.getAttribute('cx'), +c.getAttribute('cy')])}))"""
STATUS = "() => document.getElementById('ray-status').textContent"


@pytest.mark.asyncio
async def test_an_outgoing_message_draws_the_agents_ray_into_the_queue_row(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [(0.5, _event("outgoing"))])
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=10000)
        (ray,) = await page.evaluate(RAYS)
        sx, sy, top, cx, ey, ex = _segments(ray["d"])
        target = await _centre(page, ROW)
        orb = await _centre(page, "#orb-control")
        assert abs(sx - orb[0]) < 1 and abs(sy - orb[1]) < 1
        assert abs(ex - target[0]) < 1 and abs(ey - target[1]) < 1 and cx > ex
        assert ray["colour"] == PRIMARY, "the same palette as the agents' rays"
        (group,) = await page.evaluate(GROUPS)
        assert group["id"].startswith("human:") and group["direction"] == "outgoing"
        assert group["pulses"] == 1 and group["pulseDirection"] in ("", "normal")
        assert all(abs(x - ex) < 1 and abs(y - ey) < 1 for x, y in group["rings"])
        assert await page.evaluate(STATUS) == "Sending a message to matt"
        await page.wait_for_function("!document.querySelector('#ray-lane .ray')", timeout=10000)
        assert await page.evaluate(STATUS) == ""
        await browser.close()


@pytest.mark.asyncio
async def test_an_incoming_message_travels_the_same_ray_back_to_the_main_orb(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [(0.5, _event("incoming", sender=MATT,
                                                                recipient=TIM))])
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=10000)
        (ray,) = await page.evaluate(RAYS)
        sx, sy, top, cx, ey, ex = _segments(ray["d"])
        target = await _centre(page, ROW)
        assert abs(ex - target[0]) < 1 and abs(ey - target[1]) < 1, "the same route"
        (group,) = await page.evaluate(GROUPS)
        assert group["direction"] == "incoming" and group["pulseDirection"] == "reverse"
        assert all(abs(x - sx) < 1 and abs(y - sy) < 1 for x, y in group["rings"]), \
            "the rings ripple out from the main orb"
        assert await page.evaluate(STATUS) == "A message from matt is arriving"
        await browser.close()


@pytest.mark.asyncio
async def test_with_the_row_not_on_the_page_the_ray_ends_at_the_queue_icon(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [(0.5, _event("outgoing"))], row=False)
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=10000)
        (ray,) = await page.evaluate(RAYS)
        sx, sy, top, cx, ey, ex = _segments(ray["d"])
        icon = await _centre(page, QUEUE_TAB)
        assert abs(ex - icon[0]) < 1 and abs(ey - icon[1]) < 1 and cx == ex
        assert await page.evaluate(STATUS) == "Sending a message to matt", \
            "who it is for stays in words"
        await browser.close()


@pytest.mark.asyncio
async def test_a_row_that_goes_while_the_ray_shows_hands_it_to_the_queue_icon(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [(0.5, _event("outgoing"))])
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=10000)
        await page.evaluate(f"document.querySelector(\"{ROW}\").remove()")
        await page.wait_for_timeout(200)
        (ray,) = await page.evaluate(RAYS)
        sx, sy, top, cx, ey, ex = _segments(ray["d"])
        icon = await _centre(page, QUEUE_TAB)
        assert abs(ex - icon[0]) < 1 and abs(ey - icon[1]) < 1, "never a stale row"
        await browser.close()


@pytest.mark.asyncio
async def test_a_replayed_or_repeated_event_draws_nothing(page_url):
    replayed = _event("incoming", sender=MATT, recipient=TIM, replay=True)
    live_again = json.loads(json.dumps(replayed))
    live_again["event"]["replay"] = False
    fresh = _event("outgoing")
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [
            (0.5, replayed), (0.2, live_again), (0.2, fresh), (0.1, fresh)])
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=10000)
        await page.wait_for_timeout(800)
        groups = await page.evaluate(GROUPS)
        assert [g["id"] for g in groups] == [fresh["event"]["event_id"]], \
            "a reconnect replays no ray; a repeat is drawn once"
        await browser.close()


@pytest.mark.asyncio
async def test_agent_and_person_rays_share_the_palette_and_never_share_an_id(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [(0.5, _to("s1")), (0.0, _event("outgoing"))])
        await page.wait_for_function(
            "document.querySelectorAll('#ray-lane .ray').length === 2", timeout=10000)
        groups = await page.evaluate(GROUPS)
        rays = await page.evaluate(RAYS)
        assert [g["id"].split(":")[0] for g in groups] == ["agent", "human"]
        assert [r["colour"] for r in rays] == [PRIMARY, SECOND]
        await browser.close()


@pytest.mark.asyncio
async def test_a_burst_shows_five_rays_and_counts_the_rest(page_url):
    burst = [(0.5 if i == 0 else 0.0, _event("incoming", sender=MATT, recipient=TIM))
             for i in range(8)]
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, burst)
        await page.wait_for_function(
            "document.querySelectorAll('#ray-lane .ray').length === 5", timeout=10000)
        await page.wait_for_timeout(300)
        assert len(await page.evaluate(GROUPS)) == 5
        assert await page.evaluate(
            "document.querySelector('#ray-lane .ray-overflow').textContent") == "+3"
        assert (await page.evaluate(STATUS)).endswith("and 3 more")
        await page.wait_for_function("!document.querySelector('#ray-lane .ray')", timeout=10000)
        assert not await page.evaluate("document.querySelector('#ray-lane .ray-overflow')"), \
            "nothing is queued behind a burst"
        await browser.close()


@pytest.mark.asyncio
async def test_a_message_to_oneself_goes_out_and_comes_back_to_its_one_row(page_url):
    out = _event("outgoing", recipient=TIM)
    back = _event("incoming", recipient=TIM)
    back["event"]["message_id"] = out["event"]["message_id"]
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [(0.5, out), (0.0, back)])
        await page.wait_for_function(
            "document.querySelectorAll('#ray-lane .ray').length === 2", timeout=10000)
        rays = await page.evaluate(RAYS)
        target = await _centre(page, ROW)
        for ray in rays:
            sx, sy, top, cx, ey, ex = _segments(ray["d"])
            assert abs(ex - target[0]) < 1 and (sx, sy) != (ex, ey), \
                "to the item's row, never a zero-length path to the orb itself"
        assert [g["direction"] for g in await page.evaluate(GROUPS)] == ["outgoing", "incoming"]
        assert await page.evaluate(STATUS) == \
            "Sending a message to yourself. Your message to yourself is arriving"
        await browser.close()


@pytest.mark.asyncio
async def test_reduced_motion_draws_a_still_route_with_its_words(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [(0.5, _event("incoming", sender=MATT,
                                                                recipient=TIM))], reduced=True)
        await page.wait_for_function("document.querySelector('#ray-lane .ray')", timeout=10000)
        (group,) = await page.evaluate(GROUPS)
        assert group["motion"] == "still" and group["pulses"] == 0 and group["rings"] == []
        assert await page.evaluate(STATUS) == "A message from matt is arriving"
        await browser.close()


@pytest.mark.asyncio
async def test_reduced_motion_stills_a_person_s_ray_and_never_an_agent_s(page_url):
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, [(0.5, _to("s1")), (0.0, _event("outgoing"))],
                                    reduced=True)
        await page.wait_for_function(
            "document.querySelectorAll('#ray-lane .ray').length === 2", timeout=10000)
        motion = {g["id"].split(":")[0]: (g["motion"], g["pulses"])
                  for g in await page.evaluate(GROUPS)}
        assert motion == {"agent": ("animate", 1), "human": ("still", 0)}, motion
        await browser.close()


@pytest.mark.asyncio
async def test_an_agent_s_message_past_the_ray_limit_still_lights_the_agents_tab(page_url):
    """Five rays show; an agent's message counted past them draws nothing
    but still lights the Agents tab while another tab is open."""
    other_tab_open = asyncio.Event()
    burst = [(other_tab_open if i == 0 else 0.0, _event("incoming", sender=MATT, recipient=TIM))
             for i in range(5)] + [(0.0, _to("s1"))]
    async with async_api.async_playwright() as p:
        browser, page = await _open(p, page_url, burst)
        await page.click(".right-tabs [aria-label='Diagnostics']")
        other_tab_open.set()
        await page.wait_for_function(
            "document.querySelector('#ray-lane .ray-overflow')", timeout=10000)
        assert all(g["id"].startswith("human:") for g in await page.evaluate(GROUPS))
        await page.wait_for_function(
            "document.querySelector(\".right-tabs [aria-label='Agents']\").classList.contains('lit')",
            timeout=5000)
        await browser.close()
