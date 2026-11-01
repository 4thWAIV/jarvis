"""A page work package on the page (PackageView.tsx, packageBridge.ts), by
what happens in a real browser: the example mic-check-page package, opened
by a `package_page` frame on the voice socket as `_open_package_page` sends
it, runs in a frame with no origin, records through the page's own
microphone (Chromium's fake device) and saves its results into the
package's folder.

Headless Chromium on the built page. Every /api/packages request is
answered by JARVIS's real app (TestClient) over a temporary
JARVIS_DATA_DIR, so the frame gets the real files and headers and a save is
a real write; the save carries the tool token in place of the page origin
the test's static server cannot have.
"""

import asyncio
import importlib
import json

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_voice_page_captions import BROWSER_ARGS, FAKE_TRANSCRIPTION
from tests.test_voice_page_conversation import _Voice
from tests.test_package_modes import EXAMPLES

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page


@pytest.fixture
def jarvis_app(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    from jarvis import data_paths, run_store, work_packages
    importlib.reload(data_paths)
    importlib.reload(run_store)
    from jarvis import server
    importlib.reload(server)
    import sys
    sys.path.insert(0, str(EXAMPLES))
    try:
        import zip_package
    finally:
        sys.path.remove(str(EXAMPLES))
    archive = tmp_path / "mic-check-page.zip"
    assert zip_package.main([str(EXAMPLES / "mic-check-page"), str(archive)]) == 0
    package, _ = work_packages.unpack(archive, "0f1e2d3c", clear_mark=lambda _f: None)
    from fastapi.testclient import TestClient
    return TestClient(server.app), data_paths.ensure_tool_token(), package


def _through(client, token):
    """Answer the page's /api/packages requests with JARVIS's own app."""
    async def handle(route):
        request = route.request
        path = request.url.split("://", 1)[1].split("/", 1)[1]
        headers = {"Authorization": f"Bearer {token}"} if request.method == "POST" else {}
        r = await asyncio.to_thread(client.request, request.method, "/" + path,
                                    content=request.post_data_buffer, headers=headers)
        await route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)
    return handle


@pytest.mark.asyncio
async def test_the_package_page_runs_without_an_origin_and_saves_through_the_bridge(
        page_url, jarvis_app):
    client, token, package = jarvis_app
    from jarvis import packages_api
    async with async_api.async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                          # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.add_init_script(FAKE_TRANSCRIPTION)
        voice = _Voice()
        await page.route_web_socket("**/ws/voice", voice)
        await page.route_web_socket("**/ws/sessions", lambda ws: None)
        await page.route("**/api/packages/**", _through(client, token))
        await page.goto(page_url)
        await page.wait_for_function("window.__transcribers.some((t) => t.readyState === 1)",
                                     timeout=10000)

        voice.send({"type": "package_page", "package": packages_api.describe(package)})
        await async_api.expect(page.locator(".centre-tabs [aria-label='mic-check-page']")) \
            .to_have_attribute("aria-selected", "true")
        frame_el = page.locator("#package-view iframe")
        assert await frame_el.get_attribute("sandbox") == "allow-scripts"
        assert await frame_el.get_attribute("allow") is None
        frame = page.frame_locator("#package-view iframe")
        await async_api.expect(frame.locator("h1")).to_have_text("Microphone check")
        inner = next(f for f in page.frames if f.url.endswith("/files/index.html"))
        # No origin: none of JARVIS's routes, and no microphone of its own.
        assert await inner.evaluate("self.origin") == "null"
        refused = await inner.evaluate("""() => navigator.mediaDevices.getUserMedia({audio: true})
            .then(() => "granted", (e) => e.name)""")
        assert refused != "granted"

        # Through the bridge: three seconds of the page's microphone, then
        # the clip, its levels and a note saved into results/.
        await frame.locator("#record").click()
        await async_api.expect(frame.locator("#level")).to_contain_text("dBFS", timeout=15000)
        await async_api.expect(page.locator("#package-view .package-status")).to_have_text("")
        await frame.locator("#note").fill("<b>heard fine</b>")
        await frame.locator("#save").click()
        await async_api.expect(frame.locator("#status")).to_contain_text("Saved results/clip.wav")
        results = package.folder / "results"
        assert (results / "note.txt").read_text() == "<b>heard fine</b>\n"
        level = json.loads((results / "level.json").read_text())
        assert level["seconds"] == 3 and level["sample_rate"] == 16000
        assert (results / "clip.wav").read_bytes()[:4] == b"RIFF"
        assert (results / "clip.wav").stat().st_size == 44 + 3 * 16000 * 2
        await async_api.expect(page.locator("#package-view .package-status")) \
            .to_contain_text("Saved results/")

        # A name that would leave results/ is refused, and said to the frame.
        answer = await inner.evaluate("""() => new Promise((resolve) => {
            addEventListener("message", (e) => { if (e.data.type === "saved" && e.data.id === "x")
              resolve(e.data); });
            parent.postMessage({jarvis: "package", type: "save", id: "x", name: "../out.txt",
                                data: "no"}, "*");
        })""")
        assert answer["ok"] is False and "not a name a result" in answer["error"]
        assert not (package.folder / "out.txt").exists()

        # Kept while another view is shown, and closed by its button.
        await page.click(".centre-tabs [aria-label='Conversation']")
        await page.click(".centre-tabs [aria-label='mic-check-page']")
        await async_api.expect(frame.locator("#status")).to_contain_text("Saved results/clip.wav")
        await page.click("#package-view .workflow-close")
        await async_api.expect(page.locator("#package-view")).to_have_count(0)
        await browser.close()
