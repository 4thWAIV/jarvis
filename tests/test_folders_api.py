"""The folder catalog's two doors: the page's /api/folders routes
(folders_api.py, mounted on the real app behind web_auth's gate) and the
brain's folder tools through the real /internal/tool. Both call the one
folder service, so both refuse the same paths for the same reasons.

Synthetic folders under tmp_path only; the server's lifespan is never
entered, so no session watcher or discovery reads anything real.
"""

import asyncio
import importlib
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BROWSER = {"Origin": "http://localhost:5173"}


class _Brain:
    ready = False

    def __init__(self, origin="user"):
        self.current_origin = origin
        self.marks: list[str] = []

    turn_untrusted_source = None
    turn_is_tainted = False

    def mark_untrusted_content(self, source: str) -> None:
        self.marks.append(source)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    from jarvis import folder_service, folder_store, history_read
    claude = tmp_path / "claude-home"
    (claude / "projects").mkdir(parents=True)
    monkeypatch.setattr(history_read, "claude_roots", lambda: [claude])
    server_module.folder_catalog = folder_service.FolderService(
        folder_store.FolderStore(), data_paths.node_id)
    hints: list[dict] = []
    monkeypatch.setattr(server_module, "_send_to_session_clients", hints.append)
    folder = tmp_path / "work" / "notes"
    (folder / "sub").mkdir(parents=True)
    (folder / "plan.md").write_text("# Plan\nship it\n")
    (folder / ".env").write_text("TOKEN=do-not-read\n")
    yield server_module, TestClient(server_module.app), folder, hints
    server_module.folder_catalog = None


def _register(client, folder, key="k1", headers=BROWSER, **extra):
    return client.post("/api/folders", headers=headers,
                       json={"request_key": key, "path": str(folder), **extra})


def test_register_browse_preview_edit_and_remove_over_http(wired):
    _server, client, folder, hints = wired
    shown = client.get("/api/folders/resolve", params={"path": str(folder)})
    assert shown.status_code == 200 and shown.json()["access"]["reader"] == "files"
    assert shown.json()["existing"] is None

    made = _register(client, folder, reader="files")
    assert made.status_code == 201, made.text
    f = made.json()["folder"]
    assert hints == [{"type": "folders"}]
    assert _register(client, folder).json()["outcome"] == "replayed"

    listed = client.get("/api/folders", params={"query": "notes"}).json()
    assert [x["id"] for x in listed["folders"]] == [f["id"]]

    entries = client.get(f"/api/folders/{f['id']}/entries").json()
    assert [e["name"] for e in entries["entries"]] == ["sub", "plan.md"]
    assert entries["withheld"] == 1 and entries["location_revision"] == 1

    preview = client.get(f"/api/folders/{f['id']}/preview", params={"path": "plan.md"}).json()
    assert preview["lines"] == ["# Plan", "ship it"] and preview["path"] == "plan.md"
    denied = client.get(f"/api/folders/{f['id']}/preview", params={"path": ".env"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"
    assert "do-not-read" not in denied.text

    stale = client.patch(f"/api/folders/{f['id']}", headers=BROWSER,
                         json={"expected_revision": 99, "display_name": "x"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_revision"
    edited = client.patch(f"/api/folders/{f['id']}", headers=BROWSER,
                          json={"expected_revision": f["revision"], "display_name": "Plans"})
    assert edited.json()["folder"]["display_name"] == "Plans"

    gone = client.delete(f"/api/folders/{f['id']}", headers=BROWSER)
    assert gone.status_code == 200 and gone.json()["folder"]["lifecycle"] == "removed"
    assert folder.joinpath("plan.md").exists()
    again = _register(client, folder, key="k2")
    assert again.status_code == 409 and again.json()["error"]["code"] == "removed_exists"
    back = _register(client, folder, key="k3", reactivate=True)
    assert back.json()["outcome"] == "reactivated" and back.json()["folder"]["id"] == f["id"]


def test_a_reader_other_than_the_one_shown_is_refused(wired):
    _server, client, folder, _hints = wired
    r = _register(client, folder, reader="claude_history")
    assert r.status_code == 409 and r.json()["error"]["code"] == "reader_changed"


def test_fixed_routes_are_not_taken_for_a_folder_id(wired):
    _server, client, _folder, _hints = wired
    assert client.get("/api/folders/candidates").json()["candidates"] == []
    assert client.get("/api/folders/nope").status_code == 404
    r = client.post("/api/folders/candidates/nope", headers=BROWSER, json={"action": "ignore"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "candidate_not_found"


def test_changes_need_the_page_s_origin_or_the_tool_token(wired):
    server, client, folder, _hints = wired
    from jarvis import access_keys, data_paths
    assert _register(client, folder, headers={}).status_code == 403
    for category in access_keys.CATEGORIES:
        key = access_keys.create(f"agent-{category}", [category])
        r = _register(client, folder, key=f"k-{category}",
                      headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 403, category
    token = data_paths.ensure_tool_token()
    ok = _register(client, folder, key="k-token", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 201
    fid = ok.json()["folder"]["id"]
    assert client.delete(f"/api/folders/{fid}").status_code == 403
    assert client.post(f"/api/folders/{fid}/refresh").status_code == 403


def test_a_switched_off_catalog_says_so(wired):
    server, client, folder, _hints = wired
    server.folder_catalog = None
    r = client.get("/api/folders")
    assert r.status_code == 503 and r.json()["error"]["code"] == "switched_off"


def test_a_slow_listing_never_holds_the_event_loop():
    """The routes run the service in a worker thread: a listing that takes
    half a second leaves the loop free to do everything else meanwhile."""
    from jarvis import folders_api

    class Slow:
        def entries(self, folder_id, **kw):
            time.sleep(0.5)
            return {"entries": []}

    app = FastAPI()
    folders_api.mount(app, lambda: Slow())

    async def scenario():
        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        ticker = asyncio.create_task(tick())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/api/folders/x/entries")
        ticker.cancel()
        return r.status_code, ticks

    status, ticks = asyncio.run(scenario())
    assert status == 200 and ticks >= 10


# ── the brain's tools, through the real /internal/tool ───────────────────

def _call(server, client, tool, origin="user", **arguments):
    from jarvis import data_paths
    server.brain_instance = _Brain(origin)
    token = data_paths.ensure_tool_token()
    r = client.post("/internal/tool", headers={"Authorization": f"Bearer {token}"},
                    json={"tool": tool, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_tools_use_the_same_service_and_the_same_walls(wired):
    server, client, folder, hints = wired
    refused = _call(server, client, "register_folder", origin="event", path=str(folder))
    assert not refused["ok"] and "not_allowed_from_event" in refused["text"]

    made = _call(server, client, "register_folder", path=str(folder))
    assert made["ok"] and "No file was created, moved or changed" in made["text"]
    fid = server.folder_catalog.list_folders()["folders"][0]["id"]
    assert fid in made["text"] and hints

    found = _call(server, client, "find_folder", name="notes")
    assert fid in found["text"] and 'untrusted="true"' in found["text"]

    browsed = _call(server, client, "browse_folder", folder_id=fid)
    assert "plan.md" in browsed["text"] and ".env" not in browsed["text"]
    assert "1 not shown" in browsed["text"]

    read = _call(server, client, "read_folder_file", folder_id=fid, path="plan.md")
    assert "ship it" in read["text"] and 'untrusted="true"' in read["text"]
    assert server.brain_instance.marks == ["a file in one of your folders"]

    secret = _call(server, client, "read_folder_file", folder_id=fid, path=".env")
    assert "forbidden" in secret["text"] and "do-not-read" not in secret["text"]
    escape = _call(server, client, "read_folder_file", folder_id=fid, path="../../x")
    assert "forbidden" in escape["text"]
    raw = _call(server, client, "read_folder_file", folder_id=str(folder), path="plan.md")
    assert "Give me its ID" in raw["text"]

    removed = _call(server, client, "unregister_folder", folder_id=fid)
    assert removed["ok"] and "untouched" in removed["text"]
    assert folder.joinpath("plan.md").read_text().startswith("# Plan")
    after = _call(server, client, "read_folder_file", folder_id=fid, path="plan.md")
    assert "removed" in after["text"]


def test_the_brain_is_granted_every_folder_tool(wired):
    """Defined in jarvis_mcp and handled in server.py is not enough: the
    brain is launched with ALLOWED_TOOLS, and without them it answered
    "which folder?" with list_projects and could not find a registered one.
    The reads keep their untrusted handling; the two that change the
    catalog stay acting tools."""
    from jarvis import claude_provider
    server = wired[0]
    reads = ("list_folders", "find_folder", "browse_folder", "read_folder_file")
    acts = ("register_folder", "unregister_folder")
    for name in reads + acts:
        assert f"mcp__jarvis__{name}" in claude_provider.ALLOWED_TOOLS
        assert name in server.TOOL_HANDLERS
    assert set(reads) <= set(server.TAINTING_TOOLS)
    assert set(acts) <= server.ACTING_TOOLS


def test_a_folder_run_is_retried_only_while_the_folder_can_take_it(wired, monkeypatch):
    """A retry goes back through the folder service: a folder removed since
    the run is refused, not worked in at its old path."""
    server, client, folder, _hints = wired
    from jarvis import run_store
    fid = server.folder_catalog.register(request_key="k", path=str(folder))["folder"]["id"]
    spawned = []

    async def fake_spawn(prompt, name, path, origin, resume_from=None, folder_id=None):
        spawned.append((path, folder_id))
        return "new-run"

    monkeypatch.setattr(server.run_executor_instance, "spawn", fake_spawn)
    run_id = run_store.create_run("go", "notes", str(folder), "voice", folder_id=fid)
    run_store.update_run(run_id, status=run_store.RunStatus.SUCCEEDED)

    assert client.post(f"/api/runs/{run_id}/retry", headers=BROWSER).status_code == 200
    assert spawned == [(str(folder.resolve()), fid)]
    server.folder_catalog.unregister(fid)
    refused = client.post(f"/api/runs/{run_id}/retry", headers=BROWSER)
    assert refused.status_code == 409 and len(spawned) == 1


def test_the_brain_is_told_folders_are_not_projects():
    """Asked to store research in a registered folder, the brain looked in
    list_projects alone and said it could not find the folder system."""
    from pathlib import Path
    persona = (Path(__file__).resolve().parents[1] / "jarvis" / "jarvis_home"
               / "CLAUDE.md").read_text()
    for name in ("list_folders", "find_folder", "browse_folder", "read_folder_file",
                 "`folder_id`"):
        assert name in persona, name
    assert "## Folders" in persona
    assert "before you say you cannot find it" in persona
