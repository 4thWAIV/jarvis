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


@pytest.mark.parametrize("manifest,files,said", [
    (AGENT, {}, "brief file 'BRIEF.md' is not in the zip"),
    ({**AGENT, "brief": ""}, {"BRIEF.md": "x"}, "has no brief"),
    (PAGE, {}, "entry file 'web/index.html' is not in the zip"),
    ({**PAGE, "skill": "SKILL.md"}, {"web/index.html": "x"}, "skill file 'SKILL.md'"),
    ({**PAGE, "runs": "daemon"}, {"web/index.html": "x"}, "runs is not one of"),
    ({**MANIFEST, "runs": "agent"}, {"SKILL.md": "x"}, "has no brief"),
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
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    importlib.reload(data_paths)
    from jarvis import run_executor, run_store
    importlib.reload(run_store)
    run_store.init_db()
    from jarvis import server
    importlib.reload(server)
    speech = _Speech()
    monkeypatch.setattr(server, "speech", speech)
    server._notes_for_brain.clear()

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
    assert speech.said == ["The work package stats has finished, sir. Shall I draft the "
                           "reply with its results?"]
    (note,) = server._notes_for_brain
    assert f"package_results with package {package.id}" in note and "draft_message" in note
    assert [p.name for p in package.results()] == ["summary.md"]


@pytest.mark.asyncio
async def test_a_failed_agent_package_says_so_and_retry_keeps_the_package(agent_run):
    server, run_store, start, package, speech = agent_run
    ex = start(exit_code=3)
    await server.tool_run_package_agent({"package": package.id})
    (run,) = run_store.list_runs(package_id=package.id)
    await ex.wait_for(run["id"])
    await _drain(server)
    assert run_store.get_run(run["id"])["status"] == run_store.RunStatus.FAILED
    assert speech.said == ["The work package stats failed, sir."]
    assert "retry_run" in server._notes_for_brain[0]
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


def test_the_package_column_is_added_to_an_old_database(tmp_path, monkeypatch):
    import sqlite3
    from jarvis import run_store
    importlib.reload(run_store)
    run_store.init_db()
    with sqlite3.connect(data_paths.db_path()) as conn:
        conn.execute("ALTER TABLE runs DROP COLUMN package_id")
    run_store.init_db()
    with sqlite3.connect(data_paths.db_path()) as conn:
        assert "package_id" in {r[1] for r in conn.execute("PRAGMA table_info(runs)")}


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
                         "entry": "web/index.html", "source": "ab12cd34"}


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
    from tests.test_work_packages import _sent_package
    w, message, attachment = _sent_package(tmp_path, make_zip(
        {"BRIEF.md": "Count them."}, AGENT))
    wired.server.messaging = SimpleNamespace(
        files_context=lambda: FilesContext(w.matt.files, SERVER, MATT))
    text = _call(wired, "open_package", message=str(message), attachment=str(attachment))["text"]
    assert "run_package_agent" in text and "Brief (BRIEF.md):\nCount them." in text
    assert f"reply_to {message}" in text
    assert wired.hints == [{"type": "packages"}], "the page reads its packages again"
