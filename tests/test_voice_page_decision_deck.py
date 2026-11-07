"""Voting in a decision package, in its real sandboxed frame on the voice page.

The example decision deck (examples/packages/decision-deck), built by its own
new_deck.py, is unpacked by work_packages and shown the way JARVIS shows it
(a `package_page` frame on the voice socket). Every /api/packages request,
the frame's own files and its saves included, is answered by JARVIS's real
app, with the page's Origin, so a Choose goes the whole way: the deck's
postMessage, packageBridge.ts, saveResult, POST /api/packages/<id>/results,
work_packages.save_result. An answer recorded by voice (scripts/decide.py,
as run_package_command runs it) then shows in the frame.
"""

import asyncio
import importlib
import json
import re
import os
import subprocess
import sys

import pytest

from tests.test_agents_list_fits import async_api  # noqa: F401
from tests.test_package_modes import EXAMPLES
from tests.test_voice_page_captions import BROWSER_ARGS

# A browser page test: run with `pytest -m page` (see pytest.ini).
pytestmark = pytest.mark.page

DECK = EXAMPLES / "decision-deck"
PAGE_ORIGIN = {"origin": "http://localhost:5173"}


@pytest.fixture
def deck_app(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    from jarvis import data_paths, run_store, work_packages
    importlib.reload(data_paths)
    importlib.reload(run_store)
    from jarvis import server
    importlib.reload(server)
    archive = tmp_path / "deck.zip"
    built = subprocess.run([sys.executable, str(DECK / "new_deck.py"), str(DECK / "deck-data.js"),
                            str(tmp_path / "deck"), str(archive)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stdout + built.stderr
    package, _ = work_packages.unpack(archive, "d3c1s10n", clear_mark=lambda _f: None)
    from fastapi.testclient import TestClient
    return server, TestClient(server.app), package


def _through(client):
    """The page's /api/packages requests, body and all, answered by JARVIS's
    own app as from the page's origin."""
    async def handle(route):
        request = route.request
        path = "/" + request.url.split("://", 1)[1].split("/", 1)[1]
        headers = {**PAGE_ORIGIN}
        if request.headers.get("content-type"):
            headers["content-type"] = request.headers["content-type"]
        r = await asyncio.to_thread(client.request, request.method, path,
                                    content=request.post_data_buffer, headers=headers)
        await route.fulfill(status=r.status_code, headers=dict(r.headers), body=r.content)
    return handle


def _opening(package):
    from jarvis import packages_api
    return {"type": "package_page", "package": packages_api.describe(package)}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("size", [(1440, 813), (1280, 713)])
async def test_he_votes_in_the_frame_and_by_voice(page_playwright, page_url, deck_app, size):
    server, client, package = deck_app
    async with page_playwright as p:
        try:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        except Exception as e:                      # pragma: no cover
            pytest.skip(f"no headless Chromium: {e}")
        page = await browser.new_page(viewport={"width": size[0], "height": size[1]})
        try:
            await page.route_web_socket(
                "**/ws/voice", lambda ws: ws.send(json.dumps(_opening(package))))
            await page.route_web_socket("**/ws/sessions", lambda ws: None)
            await page.route("**/api/packages/**", _through(client))
            await page.goto(page_url)
            iframe = page.locator("#package-view iframe")
            await async_api.expect(iframe).to_have_attribute("sandbox", "allow-scripts")
            frame = page.frame_locator("#package-view iframe")

            # The first decision's slide, from the jump strip: its voting
            # area is inside the frame's visible box.
            await frame.locator("#go-3").click()
            slide = frame.locator("#d-kettle-kind")
            await async_api.expect(slide).to_have_class(re.compile(r"\bcurrent\b"))
            choose = frame.locator("#opt-kettle-kind-tap .choose")
            box, view = await choose.bounding_box(), await iframe.bounding_box()
            assert view["y"] <= box["y"] and box["y"] + box["height"] <= view["y"] + view["height"]
            later = await frame.locator("#d-kettle-kind .later").bounding_box()
            assert later["y"] + later["height"] <= view["y"] + view["height"]

            await frame.locator("#d-kettle-kind .words").fill("Cheaper to fit later")
            await choose.click()
            await async_api.expect(frame.locator("#d-kettle-kind .recorded")).to_have_text(
                "Recorded: A hot water tap. Your words: “Cheaper to fit later”")
            await async_api.expect(frame.locator("#status")).to_have_text("Saved.")
            record = json.loads((package.folder / "results" / "decisions.json").read_text())
            assert record["kettle-kind"]["answer"] == "tap"
            assert record["kettle-kind"]["note"] == "Cheaper to fit later"
            assert (package.folder / "results" / "decisions.md").is_file()

            # By voice: decide.py, as run_package_command runs it, and the
            # frame shows it within its few seconds.
            from jarvis import work_packages
            env = {**os.environ, **work_packages.command_env(package)}
            said = subprocess.run('"$JARVIS_PYTHON" scripts/decide.py tea-kinds recommended',
                                  shell=True, cwd=package.folder, env=env,
                                  capture_output=True, text=True)
            assert said.stdout.startswith("Recorded: tea-kinds: A small selection"), said.stderr
            await frame.locator("#go-6").click()
            await async_api.expect(frame.locator("#answer-tea-kinds .pick")).to_have_value(
                "selection", timeout=8000)
            await async_api.expect(frame.locator("#tally")).to_have_text("2 of 3 answered")

            # Approve all recommended asks once, then fills the one left.
            await frame.locator("#approve-all").click()
            await async_api.expect(frame.locator(".confirm")).to_be_visible()
            await frame.locator(".confirm .yes").click()
            await async_api.expect(frame.locator("#tally")).to_have_text("3 of 3 answered")
            # The tally moves before the save is back: wait for the record.
            wanted = {"kettle-kind": "tap", "tea-kinds": "selection", "selection-size": "three"}
            answers: dict = {}
            for _ in range(80):
                record = json.loads((package.folder / "results" / "decisions.json").read_text())
                answers = {k: v["answer"] for k, v in record.items()}
                if answers == wanted:
                    break
                await asyncio.sleep(0.1)
            assert answers == wanted
            await async_api.expect(frame.locator("#status")).to_have_text("Saved.")
        finally:
            await browser.close()
