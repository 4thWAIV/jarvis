"""Subagents in the Workflows area, and the Agents panel's count.

A subagent whose session reports to a running workflow is shown inside that
workflow: its detail lists it under the Overview, and its Transcript tab
shows the subagent's transcript after the attached agents'. Any other
subagent has its own row in the Workflows list, drawn as a workflow's is,
and clicking it opens its detail, with its transcript, in the centre area.
In the right panel, an agent whose subagents are working says how many on
its box, and its row stays the size every other row is, so the list still
fits the page with no scroll bar.

Headless Chromium on the built page, with the routes and both sockets
replaced by Playwright.
"""

import time
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_agents_list_fits import FITS, async_api, page_url  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice
from tests.test_voice_page_workflows import _workflow

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


def subagent_row(session_id, session_name, agent_id, description, state, *, now,
                 started, written, kind="general-purpose", background=False):
    from jarvis import workflow_store
    return {"key": f"agent:{session_id}/{agent_id}", "session_id": session_id,
            "session_name": session_name, "project": "example-project", "id": agent_id,
            "description": description, "type": kind, "background": background, "depth": 1,
            "state": state, "started": started, "written": written,
            "dismiss_at": None if state == "working" else written + workflow_store.LINGER_SEC}


def workflow_subagents(now):
    """What the workflow's attached session dispatched. Fictional;
    scripts/capture_docs.py draws the same."""
    return [
        subagent_row("s1", "Interface", "a1", "Write the release notes", "working", now=now,
                     started=now - 420, written=now - 4, background=True),
        subagent_row("s1", "Interface", "a2", "Check the layout at phone width", "working",
                     now=now, started=now - 95, written=now - 2, kind="Explore"),
        subagent_row("s1", "Interface", "a3", "Find every caller of the old route", "finished",
                     now=now, started=now - 400, written=now - 60, kind="Explore"),
    ]


def lone_subagents(now):
    """A subagent of a session that reports to no workflow."""
    return [subagent_row("s2", "Documentation", "b1", "Proofread the setup guide", "working",
                         now=now, started=now - 200, written=now - 8)]


def agents_with_counts():
    return {"agents": [
        {"session_id": "s1", "name": "Interface", "active": True, "state": "working",
         "needs": 0, "provider": "anthropic", "subagents_working": 2, "subagents_capped": False},
        {"session_id": "s2", "name": "Documentation", "active": True, "state": "working",
         "needs": 0, "provider": "anthropic", "subagents_working": 1, "subagents_capped": False},
        {"session_id": "s3", "name": "Review", "active": True, "state": "idle",
         "needs": 1, "provider": "openai"},
    ]}


def subagent_transcript(now, description):
    return [
        {"offset": 0, "at": now - 420, "role": "you", "tool": None,
         "text": f"{description}, then report back."},
        {"offset": 900, "at": now - 400, "role": "tool", "tool": "Read", "text": "docs/whats-new.md"},
        {"offset": 1400, "at": now - 399, "role": "result", "tool": "Read",
         "text": "# What's new\n## The React workspace"},
        {"offset": 2400, "at": now - 4, "role": "agent", "tool": None,
         "text": "Drafted it; checking against the roadmap now."},
    ]


class _Api:
    """The routes the Workflows area and the agents list read."""

    def __init__(self, now):
        self.now = now
        self.transcripts = []

    def body(self, path, query):
        now = self.now
        wf = dict(_workflow("running"), last_event_at=now - 30)
        rows = workflow_subagents(now) + lone_subagents(now)
        if path == "/api/workflows":
            return {"workflows": [wf], "subagents": lone_subagents(now)}
        if path == "/api/workflows/wf-1":
            return {"workflow": wf, "subagents": workflow_subagents(now)}
        if path == "/api/workflows/wf-1/transcript":
            return {"agents": [{"session_id": "s1", "name": "Interface", "state": "working",
                                "last_prompt": None, "recent_tools": [], "last_text": None,
                                "last_reply": None}],
                    "subagents": workflow_subagents(now)}
        if path.startswith("/api/workflows/"):
            return {"events": []}
        if path.endswith("/transcript"):
            agent = query.get("subagent", [None])[0]
            self.transcripts.append((path, agent))
            row = next((r for r in rows if r["id"] == agent), None)
            if row is None:
                return {"session_id": "s1", "subagent": None, "name": "Interface",
                        "state": "working", "entries": [], "earlier": None}
            return {"session_id": row["session_id"], "subagent": agent, "name": row["description"],
                    "state": row["state"], "session_name": row["session_name"],
                    "entries": subagent_transcript(now, row["description"]), "earlier": None}
        if path == "/api/agents":
            return agents_with_counts()
        if path == "/api/conversation":
            return {"items": [], "earlier": None}
        return {}

    async def __call__(self, route):
        url = urlsplit(route.request.url)
        await route.fulfill(json=self.body(url.path, parse_qs(url.query)))


async def _open(p, url):
    try:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
    except Exception as e:                              # pragma: no cover
        pytest.skip(f"no headless Chromium: {e}")
    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    await page.add_init_script(FAKE_TRANSCRIPTION)
    await page.route_web_socket("**/ws/voice", _Voice())
    await page.route_web_socket("**/ws/sessions", lambda ws: ws.on_message(lambda _: None))
    api = _Api(time.time())
    await page.route("**/api/**", api)
    await page.goto(url)
    return browser, page, api


@pytest.mark.asyncio
async def test_a_lone_subagent_has_a_row_like_a_workflow_and_opens_its_transcript(page_url):
    async with async_api.async_playwright() as p:
        browser, page, api = await _open(p, page_url)
        await page.click(".right-tabs [role=tab][title=Workflows]")
        row = page.locator(".subagent-row[data-subagent-key='agent:s2/b1']")
        await row.wait_for(timeout=10000)
        got = await page.evaluate("""() => {
          const sub = document.querySelector('.subagent-row');
          const wf = document.querySelector('.workflow-row[data-workflow-id=wf-1]');
          return {name: sub.querySelector('.workflow-name').textContent,
                  parent: sub.querySelector('.subagent-parent').textContent,
                  state: sub.dataset.state,
                  orb: !!sub.querySelector('.workflow-orb canvas'),
                  heights: [sub.getBoundingClientRect().height, wf.getBoundingClientRect().height],
                  order: [...document.querySelectorAll('.workflows-list > li > button')].map(
                    b => b.dataset.workflowId || b.dataset.subagentKey)};
        }""")
        assert got["name"] == "Proofread the setup guide"
        assert got["parent"].strip() == "Subagent of Documentation"
        assert got["state"] == "working" and got["orb"]
        assert got["heights"][0] == got["heights"][1], got
        assert got["order"] == ["wf-1", "agent:s2/b1"]

        await row.click()
        await page.wait_for_selector(".subagent-detail .transcript-entry.role-agent")
        assert ("/api/sessions/s2/transcript", "b1") in api.transcripts
        detail = await page.evaluate("""() => ({
          name: document.querySelector('.subagent-detail .workflow-detail-name').textContent,
          pills: document.querySelector('.subagent-detail .workflow-detail-pills').textContent,
          tab: document.querySelector('.centre-tabs [aria-selected=true]').textContent,
          entries: [...document.querySelectorAll('.subagent-detail .transcript-entry pre')].map(e => e.textContent)})""")
        assert detail["name"] == "Proofread the setup guide"
        assert "Working" in detail["pills"] and "Subagent of Documentation" in detail["pills"]
        assert detail["tab"] == "Subagent"
        assert "Drafted it; checking against the roadmap now." in detail["entries"]

        await page.click(".subagent-detail .workflow-close")
        await page.wait_for_selector(".subagent-detail", state="detached")
        await browser.close()


@pytest.mark.asyncio
async def test_a_workflows_subagents_are_listed_in_it_and_their_transcripts_shown(page_url):
    async with async_api.async_playwright() as p:
        browser, page, api = await _open(p, page_url)
        await page.click(".right-tabs [role=tab][title=Workflows]")
        await page.click(".workflow-row[data-workflow-id=wf-1]")
        await page.wait_for_selector("#workflow-detail .workflow-subagent")
        listed = await page.evaluate("""() => [...document.querySelectorAll('.workflow-subagent')].map(
          b => [b.dataset.subagentKey, b.dataset.state, b.querySelector('.workflow-subagent-name').textContent])""")
        assert listed == [
            ["agent:s1/a1", "working", "Write the release notes"],
            ["agent:s1/a2", "working", "Check the layout at phone width"],
            ["agent:s1/a3", "finished", "Find every caller of the old route"]]

        await page.click(".workflow-tabs [role=tab][title=Transcript]")
        await page.wait_for_function(
            "() => document.querySelectorAll('.workflow-transcript .workflow-agent').length === 4")
        await page.wait_for_function(
            "() => [...document.querySelectorAll('.workflow-transcript h4')].some("
            "h => h.textContent.includes('subagent of Interface'))")
        for agent in ("a1", "a2", "a3"):
            assert ("/api/sessions/s1/transcript", agent) in api.transcripts

        # A subagent in the list opens its own detail.
        await page.click(".workflow-tabs [role=tab][title=Overview]")
        await page.click(".workflow-subagent[data-subagent-key='agent:s1/a1']")
        await page.wait_for_selector(".subagent-detail .transcript-entry")
        assert await page.text_content(".subagent-detail .workflow-detail-name") == "Write the release notes"
        await browser.close()


@pytest.mark.asyncio
async def test_the_agents_row_says_how_many_are_working_at_the_same_size(page_url):
    async with async_api.async_playwright() as p:
        browser, page, _api = await _open(p, page_url)
        await page.wait_for_selector(".agents-list li[data-session-id=s1] .agent-subagents")
        rows = await page.evaluate("""() => [...document.querySelectorAll('.agents-list li')].map(li => {
          const count = li.querySelector('.agent-subagents');
          const box = li.querySelector('.agent-entry').getBoundingClientRect();
          const c = count && count.getBoundingClientRect();
          return {id: li.dataset.sessionId, height: li.getBoundingClientRect().height,
                  count: count ? count.textContent : null,
                  inside: !c || (c.left >= box.left && c.right <= box.right
                                 && c.top >= box.top && c.bottom <= box.bottom)};
        })""")
        by_id = {r["id"]: r for r in rows}
        assert by_id["s1"]["count"] == "2 working"
        assert by_id["s2"]["count"] == "1 working"
        assert by_id["s3"]["count"] is None
        assert len({r["height"] for r in rows}) == 1, rows
        assert all(r["inside"] for r in rows), rows
        assert await page.evaluate(FITS)
        await browser.close()
