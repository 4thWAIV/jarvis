"""The two ways a work package runs besides a conversation
(`jarvis/work_packages.py`, `jarvis/packages_api.py` and the brain's tools in
server.py): "agent", one Claude Code run through the run pipeline on its
brief in its folder, announced when it ends; and "page", its entry HTML in
the page's sandboxed frame, with its files served only from inside its
folder and what the frame saves written only into its results.

Each test has its own JARVIS_DATA_DIR (conftest). The agent run is a real
RunExecutor over a fake `claude` (a Python script), never the real one;
nothing runs xattr."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from jarvis import data_paths, web_auth, work_packages
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (the fixture)
from tests.test_work_packages import MANIFEST, make_zip, marks, write_zip  # noqa: F401
from tests.settings_fixtures import hold_setting

EXAMPLES = Path(__file__).parent.parent / "examples" / "packages"
BROWSER = {"Origin": "http://localhost:5173"}
AGENT = {**MANIFEST, "name": "stats", "runs": "agent", "brief": "BRIEF.md",
         "returns": ["results/*.md"]}
PAGE = {**MANIFEST, "name": "check", "runs": "page", "entry": "web/index.html",
        "returns": ["results/**/*"]}
del AGENT["skill"], PAGE["skill"]


def unpacked(tmp_path, manifest, files, name="package.zip"):
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip(files, manifest), name),
                                      "ab12cd34")
    return package


# -- the format ------------------------------------------------------------------------

def test_a_package_without_runs_is_a_conversation_as_before(tmp_path, marks):
    package = unpacked(tmp_path, MANIFEST, {"SKILL.md": "x"})
    m = package.manifest
    assert (m.runs, m.skill, m.brief, m.entry) == ("conversation", "SKILL.md", None, None)
    assert json.loads(m.as_json())["runs"] == "conversation"


def test_a_page_says_the_room_it_is_made_for_and_the_page_is_told(tmp_path, marks):
    from jarvis import packages_api
    plain = unpacked(tmp_path, PAGE, {"web/index.html": "x"})
    assert plain.manifest.layout == "centre"
    assert "layout" not in json.loads(plain.manifest.as_json())
    assert packages_api.describe(plain)["layout"] == "centre"
    wide = unpacked(tmp_path, {**PAGE, "name": "wide-page", "layout": "wide"}, {"web/index.html": "x"}, name="wide.zip")
    assert wide.manifest.layout == "wide"
    assert json.loads(wide.manifest.as_json())["layout"] == "wide"
    assert packages_api.describe(wide)["layout"] == "wide"


@pytest.mark.parametrize("manifest,files,said", [
    (AGENT, {}, "brief file 'BRIEF.md' is not in the zip"),
    ({**AGENT, "brief": ""}, {"BRIEF.md": "x"}, "has no brief"),
    (PAGE, {}, "entry file 'web/index.html' is not in the zip"),
    ({**PAGE, "skill": "SKILL.md"}, {"web/index.html": "x"}, "skill file 'SKILL.md'"),
    ({**PAGE, "runs": "daemon"}, {"web/index.html": "x"}, "runs is not one of"),
    ({**MANIFEST, "runs": "agent"}, {"SKILL.md": "x"}, "has no brief"),
    ({**PAGE, "layout": "huge"}, {"web/index.html": "x"}, "layout is not one of centre, wide"),
    ({**MANIFEST, "layout": "wide"}, {"SKILL.md": "x"}, "only a page package has a page"),
])
def test_each_way_needs_its_own_file(tmp_path, marks, manifest, files, said):
    with pytest.raises(work_packages.PackageError, match=said):
        unpacked(tmp_path, manifest, files)
    assert list(data_paths.packages_dir().iterdir()) == []


def test_the_zip_script_knows_the_same_ways_and_builds_both_examples(tmp_path, marks):
    sys.path.insert(0, str(EXAMPLES))
    try:
        import zip_package
    finally:
        sys.path.remove(str(EXAMPLES))
    assert zip_package.MODES == work_packages.MODES
    for folder, runs in (("text-stats-agent", "agent"), ("mic-check-page", "page")):
        out = tmp_path / f"{folder}.zip"
        assert zip_package.main([str(EXAMPLES / folder), str(out)]) == 0
        package, _ = work_packages.unpack(out, folder[:8])
        assert package.manifest.runs == runs and package.manifest.name == folder


# -- agent packages ----------------------------------------------------------------------

def _fake_claude(tmp_path: Path, exit_code: int = 0) -> str:
    """A `claude` that records its folder and prompt, writes a result there
    as an agent would, and streams a successful run."""
    fixture = Path(__file__).parent / "fixtures" / "stream_success.jsonl"
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import os, sys, pathlib\n"
        f"seen = pathlib.Path({str(tmp_path / 'seen')!r})\n"
        "seen.write_text(os.getcwd() + '\\n' + sys.stdin.read())\n"
        "pathlib.Path('results').mkdir(exist_ok=True)\n"
        "pathlib.Path('results/summary.md').write_text('# Counted\\n')\n"
        f"sys.stdout.write(open({str(fixture)!r}).read())\n"
        f"sys.exit({exit_code})\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return f"{sys.executable} {script}"


class _Speech:
    def __init__(self):
        self.said = []

    async def say(self, text, priority=None, **_):
        self.said.append(text)


async def _drain(server):
    for _ in range(10):
        pending = [t for t in server._bg_tasks if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
def agent_run(monkeypatch, tmp_path, marks):
    hold_setting("brain_autostart", "0")
    importlib.reload(data_paths)
    from jarvis import run_executor, run_store
    importlib.reload(run_store)
    run_store.init_db()
    from jarvis import server
    importlib.reload(server)
    speech = _Speech()
    monkeypatch.setattr(server, "speech", speech)
    server._notes_for_brain.clear()
    from jarvis.attention_store import AttentionStore
    monkeypatch.setattr(server, "attention_record", AttentionStore())

    def start(exit_code=0):
        ex = run_executor.RunExecutor(run_store, claude_path=_fake_claude(tmp_path, exit_code))
        ex.subscribe(server._on_run_event)
        monkeypatch.setattr(server, "run_executor_instance", ex)
        return ex
    package = unpacked(tmp_path, AGENT, {"BRIEF.md": "Count the words in texts/."})
    return server, run_store, start, package, speech


@pytest.mark.asyncio
async def test_an_agent_package_runs_through_the_pipeline_in_its_folder(agent_run, tmp_path):
    server, run_store, start, package, speech = agent_run
    ex = start()
    said = await server.tool_run_package_agent({"package": "stats"})
    assert said.startswith(f"Started {package.id} as an agent run in its folder")
    (run,) = run_store.list_runs(package_id=package.id)
    assert (run["origin"], run["project_name"], run["project_path"]) == (
        "voice", package.id, str(package.folder))
    await ex.wait_for(run["id"])
    await _drain(server)
    cwd, prompt = (tmp_path / "seen").read_text().split("\n", 1)
    assert Path(cwd).resolve() == package.folder.resolve()
    assert prompt.startswith(server.UNATTENDED_PREAMBLE)
    assert "Count the words in texts/." in prompt and "results/*.md" in prompt
    assert run_store.get_run(run["id"])["status"] == run_store.RunStatus.SUCCEEDED
    assert speech.said == [] and server._notes_for_brain == []
    (item,) = server.attention_record.items()
    assert (item.kind, item.source_id, item.event) == ("package_run", run["id"], "succeeded")
    assert item.facts == {"package_id": package.id} and item.untrusted == {"package": "stats"}
    assert [p.name for p in package.results()] == ["summary.md"]


@pytest.mark.asyncio
async def test_a_failed_agent_package_is_an_update_and_retry_keeps_the_package(agent_run):
    server, run_store, start, package, speech = agent_run
    ex = start(exit_code=3)
    await server.tool_run_package_agent({"package": package.id})
    (run,) = run_store.list_runs(package_id=package.id)
    await ex.wait_for(run["id"])
    await _drain(server)
    assert run_store.get_run(run["id"])["status"] == run_store.RunStatus.FAILED
    assert speech.said == []
    assert [i.event for i in server.attention_record.items()] == ["failed"]
    start()
    new_id, problem = await server._retry_run(run_store.get_run(run["id"]), "voice")
    assert problem is None and run_store.get_run(new_id)["package_id"] == package.id


@pytest.mark.asyncio
async def test_one_agent_run_per_package_and_only_an_agent_package(agent_run, tmp_path):
    server, run_store, start, package, _ = agent_run
    start()
    run_id = run_store.create_run("x", package.id, str(package.folder), "voice",
                                  package_id=package.id)
    run_store.update_run(run_id, status=run_store.RunStatus.RUNNING)
    assert "already running" in await server.tool_run_package_agent({"package": package.id})
    assert len(run_store.list_runs(package_id=package.id)) == 1
    page = unpacked(tmp_path, PAGE, {"web/index.html": "x"}, "page.zip")
    said = await server.tool_run_package_agent({"package": page.id})
    assert said == f"{page.id} runs as page, not as agent. Nothing was started."


def test_a_run_written_before_the_package_field_reads_none(tmp_path, monkeypatch):
    from jarvis import records, run_store
    importlib.reload(run_store)
    run_store.init_db()
    run_id = run_store.create_run("x", "proj", str(tmp_path), "voice")
    path = run_store._run_path(run_id)
    old = {k: v for k, v in records.get(path).data.items() if k != "package_id"}
    records.write(path, old)                      # a run from before the field
    run_store.init_db()
    assert "package_id" not in records.get(path).data
    assert run_store.get_run(run_id)["package_id"] is None
    assert run_store.list_runs(package_id="pkg") == []
    run_store.update_run(run_id, status=run_store.RunStatus.RUNNING)
    assert [r["id"] for r in run_store.list_runs(status=[run_store.RunStatus.RUNNING])] == [run_id]


# -- page packages: its files, and what its frame saves ----------------------------------

@pytest.fixture
def page(tmp_path, marks):
    return unpacked(tmp_path, PAGE, {"web/index.html": "<p>hi</p>", "web/app.js": "1;",
                                     "web/.secret": "no"})


def test_a_page_s_files_are_served_from_inside_its_folder_only(page, tmp_path):
    assert work_packages.page_file(page, "web/index.html") == b"<p>hi</p>"
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    os.symlink(outside, page.folder / "web" / "link.txt")
    os.symlink(tmp_path, page.folder / "web" / "linked-dir")
    for relative in ("../package.json", "web/../../outside.txt", "/etc/hosts", "web/.secret",
                     "web/link.txt", "web/linked-dir/outside.txt", "web", "", "web\\app.js",
                     "web/missing.js"):
        with pytest.raises(work_packages.PackageError, match="is not a file of"):
            work_packages.page_file(page, relative)


@pytest.mark.parametrize("name", ["../x", "a/../b", ".hidden", "a//b", "", "/abs",
                                  "a/b/c/d/e", "space name", "x" * 101, "dir/.partial-x"])
def test_a_result_name_must_be_plain(page, name):
    with pytest.raises(work_packages.PackageError, match="not a name a result"):
        work_packages.save_result(page, name, b"x")
    assert not (page.folder / "results").exists() or not any(
        (page.folder / "results").rglob("*"))


def test_a_result_is_written_into_results_and_replaced_whole(page, monkeypatch):
    saved = work_packages.save_result(page, "clips/one.txt", b"first")
    assert saved == page.folder / "results" / "clips" / "one.txt"
    work_packages.save_result(page, "clips/one.txt", b"second")
    assert saved.read_bytes() == b"second"
    assert sorted(p.name for p in (page.folder / "results" / "clips").iterdir()) == ["one.txt"]
    monkeypatch.setattr(work_packages, "RESULT_MAX_BYTES", 4)
    with pytest.raises(work_packages.PackageError, match="at most 4"):
        work_packages.save_result(page, "big.txt", b"12345")


def test_a_result_never_lands_through_a_link(page, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, page.folder / "results")
    with pytest.raises(work_packages.PackageError, match="could not be saved"):
        work_packages.save_result(page, "x.txt", b"x")
    os.unlink(page.folder / "results")
    (page.folder / "results").mkdir()
    os.symlink(elsewhere, page.folder / "results" / "sub")
    with pytest.raises(work_packages.PackageError, match="could not be saved"):
        work_packages.save_result(page, "sub/x.txt", b"x")
    assert list(elsewhere.iterdir()) == []


# -- page packages: the routes, the frame's headers and opening it -----------------------

def test_the_page_s_files_are_framed_only_by_jarvis_and_sandboxed(wired, page):
    c = wired.client
    r = c.get(f"/api/packages/{page.id}/files/web/index.html")
    assert r.status_code == 200 and r.text == "<p>hi</p>"
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["content-security-policy"] == \
        "frame-ancestors 'self'; sandbox allow-scripts"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get(f"/api/packages/{page.id}/files/web/app.js").headers["content-type"] == \
        "text/javascript; charset=utf-8"
    assert c.get(f"/api/packages/{page.id}/files/web/.secret").status_code == 404
    listed = c.get("/api/packages")
    assert listed.headers["x-frame-options"] == "DENY", "everything else is never framed"
    (described,) = listed.json()["packages"]
    assert described == {"id": page.id, "name": "check", "version": "1.0.0",
                         "purpose": MANIFEST["purpose"], "runs": "page",
                         "entry": "web/index.html", "layout": "centre", "source": "ab12cd34",
                         "sources": ["ab12cd34"], "story": None, "vote": None}


def test_is_package_page_is_exactly_a_page_s_files():
    assert web_auth.is_package_page("/api/packages/x-1/files/index.html")
    for path in ("/api/packages", "/api/packages/x/results", "/api/packages//files/a",
                 "/api/packages/x/page", "/api/agents/files/x", "/api/packages/x/filesx/a"):
        assert not web_auth.is_package_page(path), path


def test_saving_a_result_needs_the_page_s_origin_and_a_plain_name(wired, page, monkeypatch):
    c = wired.client
    route = f"/api/packages/{page.id}/results"
    assert c.post(route, params={"name": "a.txt"}, content=b"x").status_code == 403
    r = c.post(route, params={"name": "notes/a.txt"}, content=b"hello", headers=BROWSER)
    assert r.json() == {"saved": "results/notes/a.txt", "bytes": 5}
    assert (page.folder / "results" / "notes" / "a.txt").read_bytes() == b"hello"
    r = c.post(route, params={"name": "../a.txt"}, content=b"x", headers=BROWSER)
    assert r.status_code == 400 and "not a name a result" in r.json()["error"]
    monkeypatch.setattr(work_packages, "RESULT_MAX_BYTES", 3)
    r = c.post(route, params={"name": "b.txt"}, content=b"1234", headers=BROWSER)
    assert r.status_code == 400 and "over 3 bytes" in r.json()["error"]
    assert not (page.folder / "results" / "b.txt").exists()


def test_a_conversation_package_has_no_page(wired, tmp_path, marks):
    package = unpacked(tmp_path, MANIFEST, {"SKILL.md": "x"})
    c = wired.client
    assert c.get(f"/api/packages/{package.id}/files/SKILL.md").status_code == 404
    r = c.post(f"/api/packages/{package.id}/results", params={"name": "a"}, content=b"x",
               headers=BROWSER)
    assert r.status_code == 404 and not (package.folder / "results").exists()


def test_the_page_and_the_brain_open_it_through_one_function(wired, page, monkeypatch):
    server, sent = wired.server, []

    async def emit(message):
        sent.append(message)
    monkeypatch.setattr(server, "_voice_emit", emit)
    r = wired.client.post(f"/api/packages/{page.id}/page", headers=BROWSER)
    assert r.status_code == 404 and r.json()["error"] == "No JARVIS page is open to show it on."
    assert "No JARVIS page is open" in _call(wired, "open_package_page", package=page.id)["text"]
    monkeypatch.setattr(server, "voice_clients", {object()})
    r = wired.client.post(f"/api/packages/{page.id}/page", headers=BROWSER)
    assert r.status_code == 200 and r.json()["package"]["id"] == page.id
    got = _call(wired, "open_package_page", package="check")
    assert got["ok"] and got["text"].startswith(f"{page.id} is open in the centre")
    assert sent == [{"type": "package_page", "package": r.json()["package"]}] * 2


@pytest.mark.parametrize("tool", ["run_package_agent", "open_package_page"])
def test_both_new_tools_act_only_on_his_own_turn(wired, page, tool, monkeypatch):
    from jarvis import claude_provider, jarvis_mcp
    sent = []

    async def emit(message):
        sent.append(message)
    monkeypatch.setattr(wired.server, "_voice_emit", emit)
    monkeypatch.setattr(wired.server, "voice_clients", {object()})
    refused = _call(wired, tool, origin="event", package=page.id)
    assert not refused["ok"] and "not_allowed_from_event" in refused["text"] and sent == []
    assert tool in wired.server.ACTING_TOOLS and tool in wired.server.TAINT_EXEMPT_TOOLS
    assert tool in {t["name"] for t in jarvis_mcp.TOOL_SPECS}
    assert f"mcp__jarvis__{tool}" in claude_provider.ALLOWED_TOOLS


def test_open_package_tells_the_brain_how_each_way_runs(wired, tmp_path, marks):
    from types import SimpleNamespace
    from jarvis.messaging_files import FilesContext
    from tests.test_messaging_files import MATT, SERVER
    from tests.test_work_packages import _sent_package, locations_for
    w, message, attachment = _sent_package(tmp_path, make_zip(
        {"BRIEF.md": "Count them."}, AGENT))
    wired.server.messaging = SimpleNamespace(
        files_context=lambda: FilesContext(w.matt.files, SERVER, MATT),
        location_context=lambda: locations_for(w.matt.files, SERVER, MATT))
    text = _call(wired, "open_package", message=str(message), attachment=str(attachment))["text"]
    assert "run_package_agent" in text and "Brief (BRIEF.md):\nCount them." in text
    assert f"reply_to {message}" in text
    assert wired.hints == [{"type": "packages"}], "the page reads its packages again"


def test_the_queue_s_open_package_and_the_brain_unpack_through_one_function(
        wired, tmp_path, marks):
    """A received package opens from the page too (the Queue's Open package,
    POST /api/packages/received), through the same `_open_received_package`
    as the brain's open_package: unpacked once, the page told to read its
    packages again, then found by its attachment for Open page."""
    from types import SimpleNamespace
    from jarvis.messaging_files import FilesContext
    from tests.test_messaging_files import MATT, SERVER
    from tests.test_work_packages import _sent_package, locations_for
    w, message, attachment = _sent_package(tmp_path, make_zip(
        {"web/index.html": "<p>hi</p>"}, PAGE))
    wired.server.messaging = SimpleNamespace(
        files_context=lambda: FilesContext(w.matt.files, SERVER, MATT),
        location_context=lambda: locations_for(w.matt.files, SERVER, MATT))
    route = "/api/packages/received"
    body = {"message_id": str(message), "attachment_id": str(attachment)}
    assert wired.client.post(route, json=body).status_code == 403, "the page's origin only"
    r = wired.client.post(route, json=body, headers=BROWSER)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["unpacked"] is True and got["package"]["runs"] == "page"
    assert got["package"]["sources"] == [attachment.hex]
    assert wired.hints == [{"type": "packages"}]
    again = wired.client.post(route, json=body, headers=BROWSER).json()
    assert again == {**got, "unpacked": False}, "the same bytes are the same package"
    text = _call(wired, "open_package", message=str(message), attachment=str(attachment))["text"]
    assert text.startswith(f"Package {got['package']['id']} was already unpacked")
    assert wired.hints == [{"type": "packages"}], "only an unpacking is a hint"
    r = wired.client.post(route, json={"message_id": "x"}, headers=BROWSER)
    assert r.status_code == 400
    wired.server.messaging = None
    r = wired.client.post(route, json=body, headers=BROWSER)
    assert r.status_code == 409 and r.json()["error"] == wired.server.MESSAGING_OFF


# -- a package zip that is a file on this Mac -------------------------------------------

@pytest.fixture
def shown(wired, monkeypatch):
    """Every page frame the server sends, with one page open."""
    sent: list[dict] = []

    async def emit(message):
        sent.append(message)
    monkeypatch.setattr(wired.server, "_voice_emit", emit)
    monkeypatch.setattr(wired.server, "voice_clients", {object()})
    return sent


def test_the_folders_view_and_the_brain_open_a_local_zip_through_one_function(
        wired, shown, tmp_path, marks):
    """Open package on a .zip in the Folders view (POST /api/packages/local)
    and the brain's open_local_package: the file read by the ordinary-file
    rule, unpacked exactly as a received zip, and a page package shown in
    the centre at once."""
    folder = tmp_path / "Projects" / "deck"
    folder.mkdir(parents=True)
    archive = write_zip(folder, make_zip({"web/index.html": "<p>hi</p>"}, PAGE), "check.zip")
    route = "/api/packages/local"
    assert wired.client.post(route, json={"path": str(archive)}).status_code == 403
    r = wired.client.post(route, json={"path": str(archive)}, headers=BROWSER)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["unpacked"] is True and got["problem"] is None
    package = work_packages.find(got["package"]["id"])
    assert package.manifest.runs == "page" and package.sources[0].startswith("local")
    assert shown == [{"type": "package_page", "package": got["package"]}]
    assert wired.hints == [{"type": "packages"}]
    assert not [p for p in data_paths.packages_dir().iterdir() if p.name.endswith(".zip")], \
        "the private copy is removed"
    text = _call(wired, "open_local_package", path=str(archive))["text"]
    assert text.startswith(f"Package {package.id} was already unpacked")
    assert "It is open in the centre of his screen" in text
    assert shown[-1] == shown[0] and len(shown) == 2


def test_a_local_zip_is_read_by_the_ordinary_file_rule(wired, shown, tmp_path, marks):
    folder = tmp_path / "Projects"
    folder.mkdir()
    archive = write_zip(folder, make_zip({"web/index.html": "x"}, PAGE), "check.zip")
    os.symlink(archive, folder / "link.zip")
    secret = folder / ".ssh"
    secret.mkdir()
    write_zip(secret, make_zip({"web/index.html": "x"}, PAGE), "id.zip")
    (folder / "notes.zip").write_bytes(b"not a zip")
    own = data_paths.data_dir() / "x.zip"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_bytes(archive.read_bytes())
    for path, words in ((folder / "link.zip", "is a link"),
                        (secret / "id.zip", "does not open id.zip from where it is"),
                        (own, "does not open x.zip from where it is"),
                        (folder / "gone.zip", "There is no file"),
                        ("check.zip", "is not a full path")):
        said = _call(wired, "open_local_package", path=str(path))["text"]
        assert words in said and said.endswith("Nothing was opened."), said
    r = wired.client.post("/api/packages/local", json={"path": str(folder / "notes.zip")},
                          headers=BROWSER)
    assert r.status_code == 409 and r.json()["error"]
    assert shown == [] and work_packages.every() == []


def test_a_page_that_connects_later_is_shown_the_package_until_it_is_closed(
        wired, shown, tmp_path, marks):
    """The package shown is held by the server: a page that connects after
    it was opened (a reload) is sent it as its voice socket opens, and once
    a page closes it, no longer."""
    folder = tmp_path / "Projects"
    folder.mkdir()
    archive = write_zip(folder, make_zip({"web/index.html": "x"}, PAGE), "check.zip")
    described = wired.client.post("/api/packages/local", json={"path": str(archive)},
                                  headers=BROWSER).json()["package"]
    frame = {"type": "package_page", "package": described}
    assert frame in wired.server._opening_frames()
    with wired.client.websocket_connect("/ws/voice", headers=BROWSER) as ws:
        opening = [ws.receive_json() for _ in range(3)]
    assert opening[2] == frame, opening
    r = wired.client.delete(f"/api/packages/{described['id']}/page", headers=BROWSER)
    assert r.status_code == 204
    assert frame not in wired.server._opening_frames()


# -- the guard: a package reaches his screen only on his own act -------------------------

def _token():
    return {"Authorization": f"Bearer {data_paths.ensure_tool_token()}"}


def test_a_tool_token_caller_unpacks_a_local_page_package_but_never_shows_it(
        wired, shown, tmp_path, marks):
    """A caller on the tool token is not acting for him: the zip is unpacked
    and recorded, the page told to read its packages again, and nothing is
    put on his screen or held for a page that connects later."""
    from jarvis import packages_api
    folder = tmp_path / "Projects"
    folder.mkdir()
    archive = write_zip(folder, make_zip({"web/index.html": "x"}, PAGE), "check.zip")
    r = wired.client.post("/api/packages/local", json={"path": str(archive)},
                          headers=_token())
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["unpacked"] is True and got["problem"] == packages_api.NOT_HIS_ACT
    assert work_packages.find(got["package"]["id"]).manifest.runs == "page"
    assert shown == [] and wired.server._package_shown is None
    assert not [f for f in wired.server._opening_frames() if f.get("type") == "package_page"]
    assert wired.hints == [{"type": "packages"}]
    r = wired.client.post("/api/packages/local", json={"path": str(archive)},
                          headers=BROWSER)
    assert r.json()["problem"] is None and len(shown) == 1, "his own click still shows it"


def test_a_tool_token_caller_may_not_open_a_page_or_tell_its_story(wired, shown, page):
    from jarvis import packages_api
    r = wired.client.post(f"/api/packages/{page.id}/page", headers=_token())
    assert r.status_code == 403 and r.json()["error"] == packages_api.NOT_HIS_ACT
    for body in ({"action": "tell"}, {}, {"action": "tell", "slide": 1}):
        r = wired.client.post(f"/api/packages/{page.id}/story", json=body, headers=_token())
        assert r.status_code == 403 and r.json()["error"] == packages_api.NOT_HIS_ACT
    assert shown == [] and wired.server._package_shown is None
    r = wired.client.post(f"/api/packages/{page.id}/story", json={"action": "stop"},
                          headers=_token())
    assert r.status_code != 403, "stopping a story shows nothing, so anyone may"
    r = wired.client.post(f"/api/packages/{page.id}/page", headers=BROWSER)
    assert r.status_code == 200 and len(shown) == 1, "his own click still shows it"


def test_the_brain_shows_a_local_page_package_only_on_his_turn(wired, shown, tmp_path, marks):
    folder = tmp_path / "Projects"
    folder.mkdir()
    archive = write_zip(folder, make_zip({"web/index.html": "x"}, PAGE), "check.zip")
    refused = _call(wired, "open_local_package", origin="event", path=str(archive))
    assert not refused["ok"] and "not_allowed_from_event" in refused["text"]
    assert shown == [] and work_packages.every() == []
    text = _call(wired, "open_local_package", path=str(archive))["text"]
    assert "It is open in the centre of his screen" in text and len(shown) == 1


# -- finding the package he asks for -------------------------------------------------------

def _many_pages(tmp_path):
    """Twenty-five page packages with purposes as long as tonight's live ones,
    then two versions of the one he asks for, the newer unpacked last."""
    def page_package(name, version, purpose, suffix, when):
        manifest = {**PAGE, "name": name, "version": version, "purpose": purpose}
        package, _ = work_packages.unpack(
            write_zip(tmp_path, make_zip({"web/index.html": "x"}, manifest), f"{suffix}.zip"),
            suffix)
        os.utime(package.folder, (when, when))
        return package
    for i in range(25):
        page_package(f"design-study-{i}", "1.0.0",
                     "Show the design and plan for letting a workflow's detail or a page "
                     "package take over the centre and the right side of the page, and "
                     f"record the owner's answer to each of its ten decisions ({i}).",
                     f"study{i:02d}", 1_000_000 + i)
    older = page_package("denice-globe-numbers", "1.0.0", "The brain globe numbers, before "
                         "and after: lower and larger.", "globe100", 2_000_000)
    newer = page_package("denice-globe-numbers", "1.0.1", "The brain globe numbers, before "
                         "and after: lower and larger.", "globe101", 2_000_001)
    return older, newer


def test_every_package_here_can_be_listed_and_none_is_cut_off(wired, tmp_path, marks):
    # Tonight list_packages cut its block at 1,200 characters: 25 packages
    # unpacked, four shown, the rest "… (truncated)", and no way to the rest.
    _many_pages(tmp_path)
    first = _call(wired, "list_packages")["text"]
    assert first.startswith("27 packages unpacked here, newest first")
    assert "page 1 of " in first and "(truncated)" not in first
    pages = int(first.split("page 1 of ")[1].split(" ")[0])
    listed = first + "".join(_call(wired, "list_packages", page=n)["text"]
                             for n in range(2, pages + 1))
    assert "(truncated)" not in listed
    assert all(p.id in listed for p in work_packages.every())


def test_he_names_a_package_and_the_brain_opens_its_newest_version(wired, tmp_path, marks,
                                                                   monkeypatch):
    older, newer = _many_pages(tmp_path)
    found = _call(wired, "list_packages", name="the globe numbers package")["text"]
    assert found.startswith("2 packages here have those words, newest first")
    assert found.index(newer.id) < found.index(older.id) and "design-study" not in found
    assert "No package here" in _call(wired, "list_packages", name="spaceship")["text"]
    sent = []

    async def emit(message):
        sent.append(message)
    monkeypatch.setattr(wired.server, "_voice_emit", emit)
    monkeypatch.setattr(wired.server, "voice_clients", {object()})
    got = _call(wired, "open_package_page", package="denice-globe-numbers")
    assert got["ok"] and got["text"].startswith(f"{newer.id} is open in the centre")
    assert [m["package"]["id"] for m in sent] == [newer.id]
    assert work_packages.find(older.id) == older
    assert work_packages.find("denice-globe-numbers-1.0.0") == older
    # His own words, not a name: the one they fit, or the ones they fit named.
    assert work_packages.find("the globe numbers") == newer
    with pytest.raises(work_packages.PackageError, match="More than one package fits"):
        work_packages.find("design study")
    sent.clear()
    got = _call(wired, "open_package_page", package="globe numbers")
    assert got["ok"] and [m["package"]["id"] for m in sent] == [newer.id]


def test_a_decision_package_unpacks_here_only_signed_as_checked_by_an_onboarded_agent(
        wired, shown, tmp_path, marks):
    """A decision package (the shared deck's files) is unpacked from a file
    on this Mac only once make_package has checked it and signed it, with
    the statement that it passed, by an agent's key this JARVIS onboarded."""
    from jarvis import agent_keys, package_signature
    from tests.agent_key_fixtures import onboarded_auth
    folder = tmp_path / "Projects"
    folder.mkdir()
    files = {"web/index.html": "x", "deck-data.js": "window.DECK = {};",
             "scripts/decide.py": "print()"}
    archive = write_zip(folder, make_zip(files, PAGE), "deck.zip")
    r = wired.client.post("/api/packages/local", json={"path": str(archive)}, headers=_token())
    assert r.status_code == 409 and "has not passed its check" in r.json()["error"]
    signed = folder / "signed.zip"
    stranger = agent_keys.make_pair(tmp_path / "stranger", "x")
    package_signature.sign(archive, signed, *stranger, "sess-x", (package_signature.DECISION_CHECKED,))
    r = wired.client.post("/api/packages/local", json={"path": str(signed)}, headers=_token())
    assert r.status_code == 409 and "has not passed its check" in r.json()["error"], \
        "a key JARVIS never onboarded vouches for nothing"
    agent = onboarded_auth(session="sess-deck").agent
    package_signature.sign(archive, signed, agent.private, agent.public, "sess-deck")
    r = wired.client.post("/api/packages/local", json={"path": str(signed)}, headers=_token())
    assert r.status_code == 409, "signed, but not as checked"
    from tests.author_fixtures import sign_authored
    sign_authored(archive, signed, agent, "sess-deck", (package_signature.DECISION_CHECKED,))
    r = wired.client.post("/api/packages/local", json={"path": str(signed)}, headers=_token())
    assert r.status_code == 200, r.text
