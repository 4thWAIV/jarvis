"""The upstream architecture page inside the actual JARVIS PackageView frame."""
import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from jarvis import language_documents, language_projection
from tests.test_brain_page_parity import wired
from tests.test_language_documents import draft, installed_kit
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION

pytestmark = pytest.mark.page


@pytest.fixture
def canonical_server(wired, draft, monkeypatch, tmp_path):
    from jarvis import data_paths, run_store
    run_store.init_db()
    configured = tmp_path / "configured.env"
    configured.write_text("FISH_API_KEY=isolated-test-key\n")
    monkeypatch.setenv("JARVIS_ENV_FILE", str(configured))
    checked = language_documents.Documents().validate(**draft)
    published = language_projection.publish(language_documents.Documents(), **{k: checked[k] for k in ("document_id", "revision", "source_revision")})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    monkeypatch.setenv("JARVIS_PORT", str(port))
    monkeypatch.setenv("JARVIS_TOOL_URL", f"http://127.0.0.1:{port}/internal/tool")
    monkeypatch.setenv("JARVIS_TOOL_TOKEN_FILE", str(data_paths.tool_token_path()))
    data_paths.ensure_tool_token()
    server = uvicorn.Server(uvicorn.Config(wired.server.app, host="127.0.0.1", port=port, lifespan="off", log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    yield wired.server, published, checked, f"http://127.0.0.1:{port}", tmp_path
    server.should_exit = True
    thread.join(5)
    sock.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_upstream_architecture_in_real_frame_with_source_evidence(page_playwright, canonical_server, draft, monkeypatch):
    from tests import dashboard_page
    server, published, checked, base, tmp = canonical_server
    await asyncio.to_thread(dashboard_page.build_bundle)
    out = Path(os.getenv("JARVIS_LANGUAGE_REVIEW_DIR", str(tmp / "review")))
    out.mkdir(parents=True, exist_ok=True)
    async with page_playwright as playwright:
        browser = await playwright.chromium.launch(headless=True, args=BROWSER_ARGS)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        await page.goto(base)
        for _ in range(100):
            if server.ui_clients.pages():
                break
            await asyncio.sleep(.05)
        async with httpx.AsyncClient(base_url=base, timeout=30) as http:
            response = await http.post(f"/api/packages/{published['package_id']}/page", headers={"Origin": base})
            assert response.status_code == 200, response.text
            await page.locator("#package-view iframe").wait_for()
            assert await page.locator("#package-view iframe").get_attribute("sandbox") == "allow-scripts"
            frame = page.frame_locator("#package-view iframe")
            await frame.get_by_text("DocumentStore", exact=True).first.wait_for(timeout=15000)
            await frame.get_by_text("DocumentStore", exact=True).first.click()
            await page.get_by_role("link", name="Read the YAML source").wait_for()
            assert "Proposed; approval is recorded separately" in await page.locator(".package-canonical").inner_text()
            source_url = await page.get_by_role("link", name="Read the YAML source").get_attribute("href")
            evidence_url = await page.get_by_role("link", name="Read the checker evidence").get_attribute("href")
            raw_source = await http.get(source_url)
            raw_check = (await http.get(evidence_url)).json()
            assert "canonical_revision=" + checked["revision"] in source_url
            # The explanation is read in place, through the canonical gate,
            # drawn by the app's Markdown renderer: no raw Markdown tab.
            await page.get_by_role("button", name="Read the explanation").click()
            explained = page.locator("#package-explanation")
            await explained.locator("h3", has_text="A proposed document store").wait_for()
            assert "This proposed architecture has one declaration, DocumentStore." in await explained.inner_text()
            assert "# A proposed" not in await explained.inner_text(), "rendered, not raw"
            await page.screenshot(path=str(out / "explanation-in-place.png"), full_page=True)
            await page.get_by_role("button", name="Hide the explanation").click()
            await explained.wait_for(state="detached")
            (out / "UNDERSTANDING.md").write_text(draft["readable_markdown"])
            assert raw_check["ok"] and raw_check["source_sha256"] == checked["source_revision"]
            assert "DocumentStore" in raw_source.text
            reported = {"kind": "test-browser DOM inspection; no product observer service", "text": await frame.locator("body").inner_text()}
            await page.screenshot(path=str(out / "architecture-frame.png"), full_page=True)
            (out / "frame-observation.json").write_text(json.dumps(reported, indent=2))
            (out / "source.yaml").write_bytes(raw_source.content)
            (out / "check.json").write_text(json.dumps(raw_check, indent=2))
            from tests.test_language_document_followup import exercise_followup
            from tests.test_agent_transmission_pins import wire as wire_fixture
            wire = wire_fixture.__wrapped__(server, monkeypatch)
            monkeypatch.setattr(server, "_inbound_accepted", lambda: True)
            await exercise_followup(server, wire, draft, monkeypatch, original=checked, published=published)
        await browser.close()
