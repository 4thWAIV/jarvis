"""Self-update (self_update.py), its routes, its command and the one restart.

Every repository here is temporary: a bare "remote", a clone of it that
plays this checkout (its origin is a github.com URL that git rewrites to the
bare one), and a second clone that pushes new commits. GitHub's CI lookup is
faked, and so are pip, npm and the restart, at the subprocess and `os.execv`
seams. Nothing here touches the developer's checkout or a real server.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis import self_update, update as update_cli

GITHUB_URL = "https://github.com/4thWAIV/jarvis.git"
REAL_RUN = subprocess.run


def _git(cwd: Path, *args: str) -> str:
    return REAL_RUN(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                    text=True).stdout


def _commit(repo: Path, files: dict[str, str], subject: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "HEAD").strip()


class Repos(SimpleNamespace):
    def push(self, files: dict[str, str], subject: str) -> str:
        """A teammate's commit, merged to main on GitHub."""
        sha = _commit(self.upstream, files, subject)
        _git(self.upstream, "push", "origin", "main")
        return sha

    def head(self) -> str:
        return _git(self.checkout, "rev-parse", "HEAD").strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    home = tmp_path / "git-home"
    home.mkdir()
    (home / "gitconfig").write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    upstream = tmp_path / "upstream"
    _git(tmp_path, "clone", str(remote), str(upstream))
    _git(upstream, "checkout", "-b", "main")
    _commit(upstream, {"README.md": "jarvis\n", "requirements.txt": "fastapi\n",
                       "frontend/package-lock.json": "{}\n", "frontend/src/a.ts": "1\n"},
            "First")
    _git(upstream, "push", "-u", "origin", "main")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", str(remote), str(checkout))
    # This checkout's origin says github.com, and git fetches it from the bare one.
    _git(checkout, "remote", "set-url", "origin", GITHUB_URL)
    _git(checkout, "config", f"url.{remote}.insteadOf", GITHUB_URL)

    ci = {"check_runs": [{"name": "test", "status": "completed", "conclusion": "success"}]}
    asked: list[tuple] = []

    def fake_check_runs(owner, repo, sha):
        asked.append((owner, repo, sha))
        if isinstance(ci.get("raise"), Exception):
            raise ci["raise"]
        return ci

    monkeypatch.setattr(self_update, "repo_root", lambda: checkout)
    monkeypatch.setattr(self_update, "is_checkout", lambda: True)
    monkeypatch.setattr(self_update, "serves_built_page", lambda: False)
    monkeypatch.setattr(self_update, "fetch_check_runs", fake_check_runs)

    # pip and npm at the subprocess seam: recorded, and answered as told.
    steps: list[tuple[list[str], str]] = []
    failing: set[str] = set()

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "git":
            return REAL_RUN(cmd, *args, **kwargs)
        steps.append((list(cmd), kwargs.get("cwd")))
        bad = any(word in cmd for word in failing)
        return subprocess.CompletedProcess(cmd, 1 if bad else 0, "",
                                           "npm ERR! broken" if bad else "")

    monkeypatch.setattr(self_update.subprocess, "run", fake_run)
    return Repos(remote=remote, upstream=upstream, checkout=checkout, ci=ci, asked=asked,
                 steps=steps, failing=failing)


# --- where it comes from -------------------------------------------------

@pytest.mark.parametrize("url,want", [
    ("git@github.com:4thWAIV/jarvis.git", ("4thWAIV", "jarvis")),
    ("https://github.com/4thWAIV/jarvis.git", ("4thWAIV", "jarvis")),
    ("https://github.com/4thWAIV/jarvis", ("4thWAIV", "jarvis")),
    ("ssh://git@github.com/4thWAIV/jarvis.git", ("4thWAIV", "jarvis")),
    ("https://gitlab.com/4thWAIV/jarvis.git", None),
    ("https://github.com.evil.example/4thWAIV/jarvis.git", None),
    ("/tmp/remote.git", None),
])
def test_only_a_github_origin_is_github(url, want):
    assert self_update.github_repo(url) == want


def test_an_installed_copy_or_another_origin_is_told_why_not(repos, monkeypatch):
    assert self_update.source() == self_update.GITHUB
    _git(repos.checkout, "remote", "set-url", "origin", str(repos.remote))
    other = self_update.check()
    assert other.source == self_update.OTHER and "not on GitHub" in other.reason
    assert not other.can_update and repos.asked == []
    monkeypatch.setattr(self_update, "is_checkout", lambda: False)
    installed = self_update.check()
    assert installed.source == self_update.INSTALLED and "installed copy" in installed.reason
    out = self_update.update()
    assert not out.ok and out.refused == installed.reason


def test_under_test_the_real_checkout_is_never_the_one_checked():
    """conftest points self_update somewhere that is not a checkout."""
    assert self_update.check().source == self_update.OTHER


# --- check ----------------------------------------------------------------

def test_up_to_date(repos):
    status = self_update.check()
    assert status.up_to_date and not status.can_update and status.behind == 0
    assert status.branch == "main" and status.commit == repos.head()
    assert status.ci == "" and repos.asked == [], "nothing new, nothing to ask GitHub"
    head, details = self_update.describe(status)
    assert head == "Up to date." and details == [f"At {repos.head()[:7]} on main."]


def test_new_commits_whose_ci_passed_can_be_updated(repos):
    repos.push({"a.py": "1\n"}, "Add a")
    latest = repos.push({"b.py": "2\n"}, "Add b")
    status = self_update.check()
    assert status.behind == 2 and status.latest == latest
    assert [c.subject for c in status.commits] == ["Add b", "Add a"]
    assert status.ci == self_update.CI_PASSED and status.blockers == []
    assert status.can_update
    assert repos.asked == [("4thWAIV", "jarvis", latest)]
    head, details = self_update.describe(status)
    assert head == "2 new commits on origin/main; its CI passed. It can be updated."
    assert details == [f"{latest[:7]} Add b", f"{status.commits[1].sha[:7]} Add a"]
    body = status.to_json()
    assert body["can_update"] and body["commits"][0] == {"sha": latest, "subject": "Add b"}


@pytest.mark.parametrize("ci,state", [
    ({"check_runs": [{"status": "in_progress", "conclusion": None}]}, "pending"),
    ({"check_runs": [{"status": "completed", "conclusion": "success"},
                     {"status": "completed", "conclusion": "failure"}]}, "failed"),
    ({"check_runs": []}, "none"),
    ({"raise": OSError("offline")}, "unknown"),
    ({"check_runs": [{"status": "completed", "conclusion": "skipped"}]}, "passed"),
])
def test_only_a_commit_whose_ci_passed_is_updated_to(repos, ci, state):
    repos.ci.clear()
    repos.ci.update(ci)
    repos.push({"a.py": "1\n"}, "Add a")
    status = self_update.check()
    assert status.ci == state
    blocked = [b.code for b in status.blockers]
    assert blocked == ([] if state == "passed" else [self_update.CI_NOT_PASSED])


def test_each_blocker_is_named(repos):
    repos.push({"docs/planning/notes.md": "theirs\n"}, "Add notes")
    # Untracked here, and the update would add it.
    (repos.checkout / "docs" / "planning").mkdir(parents=True)
    (repos.checkout / "docs" / "planning" / "notes.md").write_text("mine\n")
    (repos.checkout / "README.md").write_text("edited\n")
    status = self_update.check()
    assert [(b.code, b.detail) for b in status.blockers] == [
        (self_update.MODIFIED, ["README.md"]),
        (self_update.WOULD_OVERWRITE, ["docs/planning/notes.md"])]
    assert not status.can_update
    head, details = self_update.describe(status)
    assert head.endswith("The update is refused: Tracked files have been changed here. "
                         "Untracked files here would be overwritten by the update.")
    assert "Tracked files have been changed here. README.md" in details


def test_a_local_commit_or_another_branch_is_refused(repos):
    repos.push({"a.py": "1\n"}, "Add a")
    _commit(repos.checkout, {"mine.py": "x\n"}, "Mine")
    assert [b.code for b in self_update.check().blockers] == [self_update.LOCAL_COMMITS]
    _git(repos.checkout, "checkout", "-b", "work")
    codes = [b.code for b in self_update.check().blockers]
    assert codes[0] == self_update.NOT_ON_MAIN


def test_a_fetch_that_fails_is_said_not_raised(repos):
    _git(repos.checkout, "config", f"url.{repos.checkout / 'gone.git'}.insteadOf", GITHUB_URL)
    _git(repos.checkout, "config", "--unset", f"url.{repos.remote}.insteadOf")
    status = self_update.check()
    assert status.error.startswith("Could not read the checkout or reach GitHub")
    assert self_update.describe(status)[0] == self_update.CANNOT_CHECK
    out = self_update.update()
    assert out.refused == self_update.CANNOT_CHECK and not out.ok


# --- update ---------------------------------------------------------------

def test_update_moves_to_the_checked_commit_and_installs_what_changed(repos):
    before = repos.head()
    latest = repos.push({"requirements.txt": "fastapi\nhttpx\n",
                         "frontend/package-lock.json": '{"v": 2}\n'}, "Deps")
    out = self_update.update()
    assert out.ok and out.updated and out.restart
    assert (out.from_commit, out.to_commit) == (before, latest) and repos.head() == latest
    assert repos.steps == [
        ([str(repos.checkout / ".venv" / "bin" / "pip"), "install", "-r", "requirements.txt"],
         str(repos.checkout)),
        (["npm", "ci"], str(repos.checkout / "frontend"))]
    assert out.notes == ["The frontend's packages changed: restart npm run dev."]
    head, steps = self_update.describe_outcome(out)
    assert head == (f"Updated to {latest[:7]}. "
                    "The frontend's packages changed: restart npm run dev.")
    assert steps[0] == "Moved to the latest commit."


def test_a_page_served_from_built_files_is_built_again(repos, monkeypatch):
    monkeypatch.setattr(self_update, "serves_built_page", lambda: True)
    repos.push({"frontend/src/a.ts": "2\n"}, "Page")
    out = self_update.update()
    assert out.ok and repos.steps == [(["npm", "run", "build"], str(repos.checkout / "frontend"))]
    assert out.notes == []


def test_nothing_installed_when_nothing_needs_it(repos):
    repos.push({"jarvis.py": "x\n"}, "Code only")
    out = self_update.update()
    assert out.ok and out.updated and repos.steps == []


def test_a_failed_step_is_said_and_nothing_restarts(repos):
    repos.failing.add("ci")
    latest = repos.push({"frontend/package-lock.json": '{"v": 3}\n'}, "Deps")
    out = self_update.update()
    assert out.updated and not out.ok and not out.restart and repos.head() == latest
    assert out.failed == "npm ci: npm ERR! broken"
    head, details = self_update.describe_outcome(out)
    assert head.startswith("The update failed. The checkout was moved")
    assert details == ["npm ci: npm ERR! broken"]


def test_a_refused_update_changes_nothing(repos):
    before = repos.head()
    repos.ci.update(check_runs=[{"status": "completed", "conclusion": "failure"}])
    repos.push({"a.py": "1\n"}, "Add a")
    out = self_update.update()
    assert not out.ok and not out.updated and repos.head() == before
    assert out.refused == "The latest commit's CI has not passed."
    assert self_update.describe_outcome(out)[0] == (
        "Not updated. The latest commit's CI has not passed.")


def test_up_to_date_is_ok_and_does_nothing(repos):
    out = self_update.update()
    assert out.ok and not out.updated and not out.restart
    assert self_update.describe_outcome(out) == ("Already up to date; nothing to do.", [])


def test_one_update_at_a_time(repos):
    assert self_update._updating.acquire(blocking=False)
    try:
        out = self_update.update()
    finally:
        self_update._updating.release()
    assert out.refused == "An update is already under way." and not out.ok


def test_an_outcome_reads_back_as_it_was_written(repos):
    repos.push({"requirements.txt": "x\n"}, "Deps")
    out = self_update.update()
    again = self_update.Outcome.from_json(json.loads(json.dumps(out.to_json())))
    assert again.to_json() == out.to_json()


# --- the routes and the one restart --------------------------------------

@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    restarts: list[str] = []
    monkeypatch.setattr(server_module, "_restart_server", restarts.append)
    return SimpleNamespace(module=server_module, client=TestClient(server_module.app),
                           restarts=restarts,
                           auth={"Authorization": f"Bearer {data_paths.ensure_tool_token()}"})


def test_get_api_update_is_the_check(server, repos):
    repos.push({"a.py": "1\n"}, "Add a")
    body = server.client.get("/api/update").json()
    assert body["behind"] == 1 and body["can_update"] and body["ci"] == "passed"
    assert body["commits"][0]["subject"] == "Add a"


def test_post_api_update_updates_then_restarts(server, repos):
    latest = repos.push({"a.py": "1\n"}, "Add a")
    r = server.client.post("/api/update", headers=server.auth)
    assert r.status_code == 200 and r.json()["restart"] and r.json()["to_commit"] == latest
    assert server.restarts == ["the page's Update and restart"]


def test_post_api_update_refused_is_409_and_does_not_restart(server, repos):
    (repos.checkout / "README.md").write_text("edited\n")
    repos.push({"a.py": "1\n"}, "Add a")
    r = server.client.post("/api/update", headers=server.auth)
    assert r.status_code == 409 and not r.json()["ok"]
    assert r.json()["status"]["blockers"][0]["text"] == "Tracked files have been changed here."
    assert server.restarts == []


def test_post_api_update_needs_the_page_or_the_token(server, repos):
    repos.push({"a.py": "1\n"}, "Add a")
    before = repos.head()
    assert server.client.post("/api/update").status_code == 403
    assert server.client.post("/api/update",
                              headers={"Origin": "http://evil.example"}).status_code == 403
    assert repos.head() == before and server.restarts == []


def test_api_restart_is_the_one_restart(server):
    assert server.client.post("/api/restart", headers=server.auth).json() == {
        "status": "restarting"}
    assert server.restarts == ["the page's Restart Server"]


def test_the_restart_execs_with_the_arguments_it_was_given(monkeypatch):
    from jarvis import server as server_module
    execs: list[tuple] = []
    monkeypatch.setattr(server_module.os, "execv", lambda *a: execs.append(a))
    monkeypatch.setattr(server_module, "RESTART_AFTER_SEC", 0)
    monkeypatch.setattr(server_module.sys, "argv", ["jarvis", "--port", "8341"])

    async def go():
        server_module._restart_server("test")
        await asyncio.sleep(0.05)

    asyncio.run(go())
    exe = server_module.sys.executable
    assert execs == [(exe, [exe, "-m", "jarvis", "--port", "8341"])]


class _Speech:
    def __init__(self):
        self.said, self.waited = [], []

    async def say(self, line, priority=None, immediate=None, label=None):
        self.said.append(line)
        return line

    async def wait_for(self, utt, timeout=60.0):
        self.waited.append(utt)
        return True


def test_by_voice_it_says_it_is_updating_and_restarts_once_heard(server, repos, monkeypatch):
    speech = _Speech()
    monkeypatch.setattr(server.module, "speech", speech)
    latest = repos.push({"a.py": "1\n"}, "Add a")
    asyncio.run(server.module._update_by_voice())
    assert speech.said == ["Updating myself from GitHub, sir; I'll restart once it's in."]
    assert speech.waited == speech.said, "the restart waits until it has been heard"
    assert repos.head() == latest and server.restarts == ["the brain's update_jarvis"]


def test_by_voice_an_update_that_did_not_go_through_is_said(server, repos, monkeypatch):
    speech = _Speech()
    monkeypatch.setattr(server.module, "speech", speech)
    repos.failing.add("ci")
    repos.push({"frontend/package-lock.json": "{}\n{}\n"}, "Deps")
    asyncio.run(server.module._update_by_voice())
    assert speech.said[-1] == ("The update did not go through, sir; Settings, under Updates, "
                               "says why.")
    assert server.restarts == []
    assert server.module._notes_for_brain[-1].startswith(
        "Since your last turn you told him the update did not go through. The update failed.")


# --- the command ----------------------------------------------------------

def test_the_command_checks_only(repos, capsys):
    repos.push({"a.py": "1\n"}, "Add a")
    before = repos.head()
    assert update_cli.main(["--check"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("1 new commit on origin/main; its CI passed. It can be updated.")
    assert "Add a" in printed and repos.head() == before


def test_with_the_server_stopped_the_command_only_updates(repos, monkeypatch, capsys):
    monkeypatch.setattr(update_cli, "ask_server", lambda url: None)
    latest = repos.push({"a.py": "1\n"}, "Add a")
    assert update_cli.main([]) == 0
    assert repos.head() == latest
    assert "JARVIS is not running; start it to use the new code." in capsys.readouterr().out


def test_with_the_server_running_the_command_asks_it(repos, monkeypatch, capsys):
    asked = []
    latest = "e" * 40

    def server_answers(url):
        asked.append(url)
        return self_update.Outcome(status=self_update.Status(source="github"), ok=True,
                                   updated=True, to_commit=latest).to_json()

    monkeypatch.setattr(update_cli, "ask_server", server_answers)
    monkeypatch.delenv("JARVIS_URL", raising=False)
    before = repos.head()
    assert update_cli.main([]) == 0
    printed = capsys.readouterr().out
    assert "Updated to eeeeeee." in printed and "restarting on the new code" in printed
    assert repos.head() == before, "the server did the work, not the command"
    assert asked and asked[0].endswith("://127.0.0.1:8340")


def test_no_server_answering_is_none(monkeypatch):
    def refused(*a, **k):
        raise update_cli.urllib.error.URLError("refused")

    monkeypatch.setattr(update_cli.urllib.request, "urlopen", refused)
    assert update_cli.ask_server("http://127.0.0.1:1") is None
