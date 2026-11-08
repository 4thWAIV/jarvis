"""The Knowledge view's vector search, by what the user sees (KnowledgeView.tsx,
knowledge.ts): the embedding model missing with its Install and size, the
install's progress read each second until it is in place, then how much is
embedded; and a search's hits each saying which of the words, the meaning
and the graph found it.

Headless Chromium on the built page; /api/knowledge is answered here in the
shapes knowledge_api.py returns, with synthetic words.
"""

import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_centre_nav import _open
from tests.test_voice_page_remaining_nav import _completed

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


def _embedder(installed=False, install=None, rows=0, waiting=0):
    return {"name": "multilingual-e5-small-int8", "label": "multilingual-e5-small",
            "download_bytes": 135390915, "installed": installed, "problem": None if installed else "not installed",
            "runtime_problem": None, "install": install,
            "engine": {"running": installed, "space": "m@761b726d" if installed else None,
                       "embedded": rows, "waiting": waiting, "rows": rows}}


class _Knowledge:
    def __init__(self):
        self.installs = 0
        self.reads = 0

    def embedder_now(self):
        if not self.installs:
            return _embedder()
        if self.reads < 3:
            return _embedder(install={"state": "downloading", "done_bytes": 40 << 20,
                                      "total_bytes": 135390915, "error": None})
        return _embedder(installed=True, install={"state": "installed", "done_bytes": 135390915,
                                                  "total_bytes": 135390915, "error": None},
                         rows=8604, waiting=0)

    async def __call__(self, route):
        url = route.request.url
        path = url.split("/api/knowledge", 1)[1].split("?", 1)[0]
        if path == "/embedder":
            if self.installs:
                self.reads += 1
            body = self.embedder_now()
        elif path == "/embedder/install":
            self.installs += 1
            body = self.embedder_now()
        elif path == "/health":
            body = {"enabled": True, "state": "running", "chunks": 8604, "proposals": 0, "embedder": None}
        elif path == "/search":
            body = {"query": "what did hammer say", "hits": [
                {"key": "chunk:1", "kind": "chunk", "label": "conversation", "source": "jref:1:n:conversation:9",
                 "trust": "untrusted", "status": None, "text": "the zebra parser is done", "heading_path": None,
                 "score": 0.04, "why": {"about": {"rank": 1, "edge": "said_by", "entity": "agent:hammer"},
                                        "near": {"rank": 3, "score": 0.81}}},
                {"key": "chunk:2", "kind": "chunk", "label": "notes", "source": "jref:1:n:note:memory/a.md",
                 "trust": "own", "status": None, "text": "the parser plan", "heading_path": None,
                 "score": 0.03, "why": {"match": {"rank": 1}}}], "notes": [], "partial": False}
        elif path in ("/proposals",):
            body = {"proposals": [], "last": {}}
        elif path == "/topics":
            body = {"topics": []}
        elif path == "/indexes":
            body = {"indexes": []}
        elif path == "/seeds":
            body = {"seeds": []}
        else:
            body = {}
        await route.fulfill(content_type="application/json", body=json.dumps(body))


@pytest.mark.asyncio
async def test_install_the_model_then_search_by_words_meaning_and_the_graph(page_url):
    knowledge = _Knowledge()
    async with async_api.async_playwright() as p:
        browser, page, voice = await _open(p, page_url, ("**/api/knowledge/**", knowledge))
        try:
            _completed(await voice.navigate("knowledge.open", {}))
            missing = page.locator("[data-embedder=missing]")
            await async_api.expect(missing).to_contain_text("multilingual-e5-small")
            await async_api.expect(missing).to_contain_text("129 MB")
            install = page.locator("#knowledge-view button[data-ui-command='knowledge.embedder.install']")
            await async_api.expect(install).to_have_text("Install (129 MB)")
            await install.click()
            await async_api.expect(page.locator("#knowledge-view [role=status]")).to_contain_text("Downloading")
            ready = page.locator("[data-embedder=installed]")
            await async_api.expect(ready).to_contain_text("8,604 passages embedded", timeout=15000)
            await async_api.expect(install).to_have_count(0)
            assert knowledge.installs == 1

            await page.fill("#knowledge-view .knowledge-search input", "what did hammer say")
            await page.click("#knowledge-view .knowledge-search button[type=submit]")
            first = page.locator(".knowledge-hit").first
            await async_api.expect(first).to_contain_text("the zebra parser is done")
            assert await first.locator("[data-why=about]").text_content() == "graph #1"
            assert await first.locator("[data-why=near]").text_content() == "meaning #3"
            assert await page.locator(".knowledge-hit").nth(1).locator("[data-why=match]").text_content() == "words #1"
        finally:
            await browser.close()
