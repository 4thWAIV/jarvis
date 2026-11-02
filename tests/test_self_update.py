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
import os
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


def _run(status: str, conclusion, path: str = ".github/workflows/ci.yml",
         created: str = "2026-11-01T10:00:00Z") -> dict:
    """One GitHub Actions run, as /actions/runs lists it."""
    return {"path": path, "status": status, "conclusion": conclusion, "created_at": created}


HEALTH = ".github/workflows/team-server-health.yml"


class Repos(SimpleNamespace):
    def push(self, files: dict[str, str], subject: str) -> str:
        """A teammate's commit, pushed straight to main on GitHub."""
        sha = _commit(self.upstream, files, subject)
        _git(self.upstream, "push", "origin", "main")
        return sha

    def merge(self, pr: int | None, title: str, files: dict[str, str] | None = None) -> str:
        """A pull request merged on GitHub: its branch's commit, then the
        merge commit GitHub makes, with the title as its body. `pr` None is
        a merge of a branch that is no pull request."""
        branch = f"pr-{pr or 'x'}-{len(self.merged)}"
        _git(self.upstream, "checkout", "-q", "-b", branch)
        _commit(self.upstream, files or {f"pr{len(self.merged)}.py": "x\n"}, f"Work for {title}")
        _git(self.upstream, "checkout", "-q", "main")
        subject = (f"Merge pull request #{pr} from 4thWAIV/{branch}" if pr is not None
                   else f"Merge branch '{branch}'")
        _git(self.upstream, "merge", "--no-ff", "-q", "-m", subject, "-m", title, branch)
        _git(self.upstream, "push", "-q", "origin", "main")
        sha = _git(self.upstream, "rev-parse", "HEAD").strip()
        self.merged.append(sha)
        return sha

    def set_ci(self, sha: str, *runs: dict) -> None:
        """That commit's runs, instead of the default for every commit."""
        self.ci.setdefault("by_sha", {})[sha] = list(runs)

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

    ci = {"workflow_runs": [_run("completed", "success")]}
    asked: list[tuple] = []

    def fake_ci_runs(owner, repo):
        """One request for every commit: the ci workflow's runs on main,
        each commit given the default runs unless `set_ci` gave it its own."""
        asked.append((owner, repo))
        if isinstance(ci.get("raise"), Exception):
            raise ci["raise"]
        shas = _git(remote, "rev-list", "main").split()
        by_sha = ci.get("by_sha", {})
        return {"workflow_runs": [{"head_sha": sha, **r} for sha in shas
                                  for r in by_sha.get(sha, ci["workflow_runs"])]}

    monkeypatch.setattr(self_update, "repo_root", lambda: checkout)
    monkeypatch.setattr(self_update, "is_checkout", lambda: True)
    monkeypatch.setattr(self_update, "serves_built_page", lambda: False)
    monkeypatch.setattr(self_update, "fetch_ci_runs", fake_ci_runs)

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
                 steps=steps, failing=failing, merged=[])


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
    assert status.versions == []
    head, details = self_update.describe(status)
    assert head == "Up to date."
    # HEAD is no pull request's merge: its short sha and subject.
    assert details[0].startswith(f"Running {repos.head()[:7]} First, ")
    assert details[0].endswith(", on main.")


def test_the_versions_are_the_merged_pull_requests_newest_first(repos):
    first = repos.merge(39, "Find the running server")
    _git(repos.checkout, "pull", "-q", "--ff-only")
    a = repos.merge(41, "Draw every small orb")
    b = repos.merge(40, "Record test timings in CI")
    latest = repos.merge(42, "Agent and page work packages")
    status = self_update.check()
    assert status.current.sha == first and status.current.pr == 39
    assert status.current.title == "Find the running server"
    assert [(v.pr, v.title, v.sha) for v in status.versions] == [
        (42, "Agent and page work packages", latest), (40, "Record test timings in CI", b),
        (41, "Draw every small orb", a)]
    assert all(v.date and v.ci == self_update.CI_PASSED for v in status.versions)
    # The work commits inside each pull request are not versions.
    assert status.behind == 6 and len(status.versions) == 3
    assert status.target == latest and status.chosen.pr == 42 and status.can_update
    assert repos.asked == [("4thWAIV", "jarvis")], "one request to GitHub for every version"
    head, details = self_update.describe(status)
    assert head == "3 newer versions on origin/main. PR 42 can be installed."
    assert details[0].startswith(f"Running PR 39: Find the running server ({first[:7]}), ")
    assert details[1].startswith(f"PR 42: Agent and page work packages ({latest[:7]}), ")
    assert details[1].endswith("; its CI passed.")
    body = status.to_json()
    assert body["current"]["name"] == "PR 39" and body["versions"][0]["name"] == "PR 42"
    assert body["versions"][0]["can_choose"] and body["versions"][0]["unchosen"] == ""


def test_a_merge_that_is_no_pull_request_and_a_pushed_latest_are_named_by_sha(repos):
    merged = repos.merge(None, "ignored body")
    latest = repos.push({"b.py": "2\n"}, "Add b")
    status = self_update.check()
    assert [(v.pr, v.title, v.sha) for v in status.versions] == [
        (None, "Add b", latest), (None, "Merge branch 'pr-x-0'", merged)]
    assert status.versions[0].name == latest[:7]
    assert status.target == latest and status.can_update


def test_each_version_has_its_own_ci_from_the_one_request(repos):
    a = repos.merge(41, "Orbs")
    b = repos.merge(40, "Timings")
    c = repos.merge(42, "Packages")
    repos.set_ci(b, _run("completed", "failure"))
    repos.set_ci(c, _run("in_progress", None),
                 _run("completed", "success", HEALTH))
    status = self_update.check()
    assert [(v.pr, v.ci, v.can_choose, v.unchosen) for v in status.versions] == [
        (42, "pending", False, "can be chosen once its tests pass"),
        (40, "failed", False, "can't be chosen: its tests failed"),
        (41, "passed", True, "")]
    assert len(repos.asked) == 1
    # The latest has not passed, so the default update is refused, and the
    # newest that can be chosen is named.
    assert [b.code for b in status.blockers] == [self_update.CI_NOT_PASSED]
    head, _details = self_update.describe(status)
    assert head == ("3 newer versions on origin/main. The update is refused: The latest "
                    "commit's CI has not passed. The newest that can be installed is PR 41.")
    chosen = self_update.check(version="41")
    assert chosen.can_update and chosen.target == a and chosen.blockers == []


def test_github_unreachable_makes_every_version_unknown(repos):
    repos.merge(41, "Orbs")
    repos.ci["raise"] = OSError("offline")
    status = self_update.check()
    assert [v.ci for v in status.versions] == ["unknown"]
    assert status.versions[0].unchosen == "can be chosen once GitHub can be asked about its tests"
    assert not status.can_update


def test_ci_state_judges_the_latest_ci_run_of_that_commit_only():
    runs = [{"head_sha": "a", **_run("completed", "failure", created="2026-11-01T10:00:00Z")},
            {"head_sha": "a", **_run("completed", "success", created="2026-11-01T11:00:00Z")},
            {"head_sha": "b", **_run("completed", "success", HEALTH)},
            {"head_sha": "c", **_run("completed", "success")}]
    assert self_update.ci_state(runs, "a") == "passed"
    assert self_update.ci_state(runs, "b") == "none"
    assert self_update.ci_state(runs, "d") == "none"


def test_the_one_request_is_the_ci_workflows_runs_on_main(monkeypatch):
    seen = []

    class _Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"workflow_runs": []}'

    def fake_urlopen(req, timeout):
        seen.append(req.full_url)
        return _Answer()

    monkeypatch.setattr(self_update.urllib.request, "urlopen", fake_urlopen)
    assert self_update.fetch_ci_runs("4thWAIV", "jarvis") == {"workflow_runs": []}
    assert seen == ["https://api.github.com/repos/4thWAIV/jarvis/actions/workflows/ci.yml/"
                    "runs?branch=main&per_page=100"]


# --- choosing a version ----------------------------------------------------

@pytest.mark.parametrize("asked", ["41", "PR 41", "pr41", "#41", "pull request 41"])
def test_a_version_is_named_by_its_pull_request(repos, asked):
    a = repos.merge(41, "Orbs")
    repos.merge(42, "Packages")
    status = self_update.check(version=asked)
    assert status.target == a and status.chosen.pr == 41 and status.can_update


def test_a_version_is_named_by_its_sha(repos):
    a = repos.merge(41, "Orbs")
    repos.merge(42, "Packages")
    assert self_update.check(version=a[:7]).target == a
    assert self_update.check(version=a.upper()).target == a


def test_an_older_version_is_refused(repos):
    old = repos.merge(38, "Older")
    _git(repos.checkout, "pull", "-q", "--ff-only")
    repos.merge(41, "Orbs")
    for asked in ("38", old[:8], repos.head()):
        status = self_update.check(version=asked)
        assert [(b.code, b.detail) for b in status.blockers] == [
            (self_update.NOT_NEWER, [asked])]
        assert not status.can_update and status.target == ""
    out = self_update.update(version="38")
    assert out.refused == ("That version is not newer than the one running here; going back "
                           "to an older version is not offered.")


@pytest.mark.parametrize("asked", ["99", "deadbeef", "next", "x" * 7])
def test_an_unknown_version_is_refused(repos, asked):
    repos.merge(41, "Orbs")
    status = self_update.check(version=asked)
    assert [b.code for b in status.blockers] == [self_update.NOT_OFFERED]
    before = repos.head()
    out = self_update.update(version=asked)
    assert out.refused == "That is not one of the versions offered." and repos.head() == before


def test_a_commit_not_on_origin_main_is_refused(repos):
    repos.merge(41, "Orbs")
    _git(repos.upstream, "checkout", "-q", "-b", "side")
    side = _commit(repos.upstream, {"side.py": "x\n"}, "Side")
    _git(repos.upstream, "push", "-q", "origin", "side")
    status = self_update.check(version=side)
    assert [b.code for b in status.blockers] == [self_update.NOT_OFFERED]


def test_a_commit_inside_a_pull_request_is_not_a_version(repos):
    repos.merge(41, "Orbs")
    inside = _git(repos.upstream, "rev-parse", "main^2").strip()
    status = self_update.check(version=inside)
    assert [b.code for b in status.blockers] == [self_update.NOT_OFFERED]


def test_a_version_whose_ci_did_not_pass_is_refused_and_named(repos):
    failed = repos.merge(40, "Timings")
    repos.merge(42, "Packages")
    repos.set_ci(failed, _run("completed", "failure"))
    before = repos.head()
    status = self_update.check(version="40")
    assert [(b.code, b.detail) for b in status.blockers] == [
        (self_update.VERSION_CI_NOT_PASSED, ["PR 40"])]
    out = self_update.update(version="40")
    assert out.refused == "That version's CI has not passed." and repos.head() == before


def test_update_to_a_chosen_version_moves_there_only(repos):
    before = repos.head()
    a = repos.merge(41, "Orbs", {"requirements.txt": "fastapi\nhttpx\n"})
    repos.merge(42, "Packages", {"frontend/package-lock.json": '{"v": 9}\n'})
    out = self_update.update(version="41")
    assert out.ok and out.updated and out.restart
    assert (out.from_commit, out.to_commit) == (before, a) and repos.head() == a
    assert out.version.pr == 41 and out.steps[0] == "Moved to PR 41."
    assert [cmd[1:] for cmd, _cwd in repos.steps] == [["install", "-r", "requirements.txt"]]
    assert self_update.describe_outcome(out)[0] == "Updated to PR 41."
    # PR 42 is still offered afterwards, and PR 41 no longer is.
    status = self_update.check()
    assert [v.pr for v in status.versions] == [42] and status.current.pr == 41


def test_the_chosen_versions_ci_is_read_again_at_update_time(repos):
    a = repos.merge(41, "Orbs")
    assert self_update.check(version="41").can_update
    repos.set_ci(a, _run("completed", "failure", created="2026-11-02T10:00:00Z"))
    before = repos.head()
    out = self_update.update(version="41")
    assert out.refused == "That version's CI has not passed." and repos.head() == before


def test_untracked_files_are_judged_against_the_chosen_version(repos):
    repos.merge(41, "Orbs")
    repos.merge(42, "Notes", {"notes.md": "theirs\n"})
    (repos.checkout / "notes.md").write_text("mine\n")
    assert [b.code for b in self_update.check().blockers] == [self_update.WOULD_OVERWRITE]
    assert self_update.check(version="41").blockers == []


@pytest.mark.parametrize("ci,state", [
    ({"workflow_runs": [_run("in_progress", None)]}, "pending"),
    ({"workflow_runs": [_run("completed", "failure")]}, "failed"),
    ({"workflow_runs": [_run("completed", "cancelled")]}, "failed"),
    ({"workflow_runs": []}, "none"),
    ({"raise": OSError("offline")}, "unknown"),
    ({"workflow_runs": [_run("completed", "skipped")]}, "passed"),
    # Only the ci workflow counts: the scheduled health check failing or
    # running on the same commit does not block an update.
    ({"workflow_runs": [_run("completed", "success"), _run("completed", "failure", HEALTH),
                        _run("in_progress", None, HEALTH)]}, "passed"),
    # A docs-only commit skips ci: the health check alone is no CI result.
    ({"workflow_runs": [_run("completed", "success", HEALTH)]}, "none"),
    # A re-run is judged on the newest run.
    ({"workflow_runs": [_run("completed", "failure", created="2026-11-01T10:00:00Z"),
                        _run("completed", "success", created="2026-11-01T11:00:00Z")]},
     "passed"),
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


def test_an_untracked_file_where_upstream_renamed_one_to_is_named(repos):
    _git(repos.upstream, "mv", "README.md", "README.txt")
    _git(repos.upstream, "commit", "-m", "Rename")
    _git(repos.upstream, "push", "origin", "main")
    (repos.checkout / "README.txt").write_text("mine\n")
    before = repos.head()
    status = self_update.check()
    assert [(b.code, b.detail) for b in status.blockers] == [
        (self_update.WOULD_OVERWRITE, ["README.txt"])]
    out = self_update.update()
    assert out.refused and repos.head() == before


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
    assert steps[0] == f"Moved to {latest[:7]}."


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


def test_a_failed_step_moves_back_so_the_next_update_installs_again(repos):
    before = repos.head()
    repos.failing.add("ci")
    latest = repos.push({"requirements.txt": "fastapi\nhttpx\n",
                         "frontend/package-lock.json": '{"v": 3}\n'}, "Deps")
    out = self_update.update()
    assert not out.updated and not out.ok and not out.restart
    assert repos.head() == before, "the checkout is back where it was"
    assert out.failed == "npm ci: npm ERR! broken"
    assert out.steps[-1] == f"Moved back to {before[:7]}."
    head, details = self_update.describe_outcome(out)
    assert head == ("The update failed. The checkout is where it was, "
                    "so the update can be tried again.")
    assert details == ["npm ci: npm ERR! broken"]

    # Still offered, and the next update runs every step again.
    assert self_update.check().can_update
    repos.failing.clear()
    repos.steps.clear()
    again = self_update.update()
    assert again.ok and again.updated and again.restart and repos.head() == latest
    assert [cmd[1:] for cmd, _cwd in repos.steps] == [
        ["install", "-r", "requirements.txt"], ["ci"]]


def test_a_step_that_fails_and_cannot_move_back_says_so(repos, monkeypatch):
    repos.failing.add("ci")
    latest = repos.push({"frontend/package-lock.json": '{"v": 3}\n'}, "Deps")
    real_git = self_update._git

    def no_reset(root, *args, **kwargs):
        if args[0] == "reset":
            raise self_update.UpdateError("cannot reset")
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(self_update, "_git", no_reset)
    out = self_update.update()
    assert out.updated and not out.ok and not out.restart and repos.head() == latest
    assert out.failed.endswith("failed too: cannot reset")
    head, _details = self_update.describe_outcome(out)
    assert head.startswith("The update failed. The checkout was moved to the new version")


def test_a_refused_update_changes_nothing(repos):
    before = repos.head()
    repos.ci.update(workflow_runs=[_run("completed", "failure")])
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
    assert body["versions"][0]["title"] == "Add a" and body["current"]["title"] == "First"


def test_post_api_update_updates_then_restarts(server, repos):
    latest = repos.push({"a.py": "1\n"}, "Add a")
    r = server.client.post("/api/update", headers=server.auth)
    assert r.status_code == 200 and r.json()["restart"] and r.json()["to_commit"] == latest
    assert server.restarts == ["the page's Install and restart"]


def test_post_api_update_installs_the_version_asked_for(server, repos):
    a = repos.merge(41, "Orbs")
    repos.merge(42, "Packages")
    r = server.client.post("/api/update", headers=server.auth, json={"version": 41})
    assert r.status_code == 200 and r.json()["to_commit"] == a
    assert r.json()["version"]["name"] == "PR 41" and repos.head() == a
    assert server.restarts == ["the page's Install and restart"]


def test_post_api_update_refuses_an_older_version_with_409(server, repos):
    repos.merge(38, "Older")
    _git(repos.checkout, "pull", "-q", "--ff-only")
    repos.merge(41, "Orbs")
    before = repos.head()
    r = server.client.post("/api/update", headers=server.auth, json={"version": "38"})
    assert r.status_code == 409 and r.json()["refused"].startswith("That version is not newer")
    assert repos.head() == before and server.restarts == []


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
    latest = _git(repos.checkout, "rev-parse", "origin/main").strip()
    assert printed.startswith(f"1 newer version on origin/main. {latest[:7]} can be installed.")
    assert "Add a" in printed and repos.head() == before


def test_by_voice_it_installs_the_version_he_chose(server, repos, monkeypatch):
    monkeypatch.setattr(server.module, "speech", _Speech())
    a = repos.merge(41, "Orbs")
    repos.merge(42, "Packages")
    asyncio.run(server.module._update_by_voice("41"))
    assert repos.head() == a and server.restarts == ["the brain's update_jarvis"]


def test_the_command_lists_the_versions(repos, capsys):
    repos.merge(41, "Orbs")
    latest = repos.merge(42, "Packages")
    assert update_cli.main(["--check"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == "2 newer versions on origin/main. PR 42 can be installed."
    assert printed[2].startswith(f"  PR 42: Packages ({latest[:7]}), ")
    assert printed[3].startswith("  PR 41: Orbs (")


def test_the_command_updates_to_the_version_given(repos, monkeypatch, capsys):
    monkeypatch.setattr(update_cli, "ask_server", lambda url, version=None: None)
    a = repos.merge(41, "Orbs")
    repos.merge(42, "Packages")
    assert update_cli.main(["--to", "41"]) == 0
    assert repos.head() == a and "Updated to PR 41." in capsys.readouterr().out
    assert update_cli.main(["--to", "41"]) == 1, "PR 41 is the one running now"


def test_the_command_asks_the_server_for_the_version_given(monkeypatch):
    sent = []

    class _Answer:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"status": {}}'

    def fake_urlopen(req, timeout, context):
        sent.append(json.loads(req.data))
        return _Answer()

    monkeypatch.setattr(update_cli.urllib.request, "urlopen", fake_urlopen)
    update_cli.ask_server("https://127.0.0.1:8340", "41")
    update_cli.ask_server("https://127.0.0.1:8340")
    assert sent == [{"version": "41"}, {}]


def test_with_the_server_stopped_the_command_only_updates(repos, monkeypatch, capsys):
    monkeypatch.setattr(update_cli, "ask_server", lambda url, version=None: None)
    latest = repos.push({"a.py": "1\n"}, "Add a")
    assert update_cli.main([]) == 0
    assert repos.head() == latest
    assert "JARVIS is not running; start it to use the new code." in capsys.readouterr().out


def test_with_the_server_running_the_command_asks_it(repos, monkeypatch, capsys):
    asked = []
    latest = "e" * 40

    def server_answers(url, version=None):
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


def test_a_refused_connection_is_no_server(monkeypatch):
    def refused(*a, **k):
        raise update_cli.urllib.error.URLError(ConnectionRefusedError(61, "refused"))

    monkeypatch.setattr(update_cli.urllib.request, "urlopen", refused)
    assert update_cli.ask_server("http://127.0.0.1:1") is None


@pytest.mark.parametrize("reason", [
    __import__("ssl").SSLError(1, "wrong version number"),
    TimeoutError("timed out"),
    "unknown url type",
])
def test_something_else_at_the_address_stops_the_command(monkeypatch, reason):
    def broken(*a, **k):
        raise update_cli.urllib.error.URLError(reason)

    monkeypatch.setattr(update_cli.urllib.request, "urlopen", broken)
    with pytest.raises(SystemExit, match="not updating under it"):
        update_cli.ask_server("https://127.0.0.1:8340")


def _http_error(code: int, body: dict):
    import io
    return update_cli.urllib.error.HTTPError(
        "https://127.0.0.1:8340/api/update", code, "x", {},
        io.BytesIO(json.dumps(body).encode()))


@pytest.mark.parametrize("code,body", [
    (403, {"detail": "Forbidden"}),
    (404, {"detail": "Not Found"}),
    (409, {"detail": "no status here"}),
])
def test_an_answer_that_is_not_an_outcome_is_not_read_as_one(monkeypatch, repos, capsys,
                                                            code, body):
    def answers(*a, **k):
        raise _http_error(code, body)

    monkeypatch.setattr(update_cli.urllib.request, "urlopen", answers)
    before = repos.head()
    repos.push({"a.py": "1\n"}, "Add a")
    with pytest.raises(SystemExit, match=f"JARVIS answered {code}"):
        update_cli.main([])
    assert "Already up to date" not in capsys.readouterr().out
    assert repos.head() == before, "nothing updated in place"


def test_a_refusal_or_failed_step_still_carries_the_outcome(monkeypatch):
    outcome = self_update.Outcome(status=self_update.Status(source="github"),
                                  refused="This checkout is not on main.").to_json()

    def answers(*a, **k):
        raise _http_error(409, outcome)

    monkeypatch.setattr(update_cli.urllib.request, "urlopen", answers)
    assert update_cli.ask_server("https://127.0.0.1:8340") == outcome


def test_the_command_looks_where_the_tool_channel_does(monkeypatch):
    monkeypatch.delenv("JARVIS_URL", raising=False)
    monkeypatch.setenv("JARVIS_TOOL_URL", "https://[::1]:8341/internal/tool")
    assert update_cli.server_url() == "https://[::1]:8341"
    monkeypatch.setenv("JARVIS_URL", "https://127.0.0.1:9000/")
    assert update_cli.server_url() == "https://127.0.0.1:9000"


# --- where the running server is (server_address.py) ----------------------

def _record(pid: int, scheme="https", host="::1", port=8341) -> None:
    from jarvis import data_paths
    data_paths.server_address_path().parent.mkdir(parents=True, exist_ok=True)
    data_paths.server_address_path().write_text(json.dumps(
        {"scheme": scheme, "host": host, "port": port, "pid": pid}))


def _dead_pid() -> int:
    done = subprocess.Popen(["true"])
    done.wait()
    return done.pid


def test_the_server_records_its_address_at_startup_and_removes_it_on_shutdown(
        server, monkeypatch):
    from jarvis import data_paths, server_address
    monkeypatch.setenv("JARVIS_SCHEME", "https")
    monkeypatch.setenv("JARVIS_BIND_HOST", "::1")
    monkeypatch.setenv("JARVIS_PORT", "8341")
    with TestClient(server.module.app):
        found = server_address.running()
        assert found is not None and found.pid == os.getpid()
        assert found.url == "https://[::1]:8341"
    assert not data_paths.server_address_path().exists()


@pytest.mark.parametrize("bind,url", [
    ("0.0.0.0", "http://127.0.0.1:8341"),
    ("::", "http://[::1]:8341"),
    ("[::1]", "http://[::1]:8341"),
    ("localhost", "http://localhost:8341"),
])
def test_the_recorded_address_is_one_that_can_be_dialled(monkeypatch, bind, url):
    from jarvis import server_address
    monkeypatch.setenv("JARVIS_SCHEME", "http")
    monkeypatch.setenv("JARVIS_BIND_HOST", bind)
    monkeypatch.setenv("JARVIS_PORT", "8341")
    server_address.record()
    assert server_address.running().url == url


def test_shutdown_leaves_another_servers_record_alone():
    from jarvis import data_paths, server_address
    other = os.getppid()
    _record(other)
    server_address.forget()
    assert data_paths.server_address_path().exists()
    assert server_address.running().pid == other


@pytest.mark.parametrize("text", ["", "not json", "{}", '{"scheme": "http", "pid": "x"}'])
def test_an_unreadable_record_is_no_record(text):
    from jarvis import data_paths, server_address
    data_paths.server_address_path().parent.mkdir(parents=True, exist_ok=True)
    data_paths.server_address_path().write_text(text)
    assert server_address.running() is None


def test_a_stale_record_is_ignored(monkeypatch, repos, capsys):
    from jarvis import server_address
    _record(_dead_pid())
    assert server_address.running() is None
    asked = []
    monkeypatch.setattr(update_cli, "ask_server",
                        lambda url, version=None: asked.append(url))
    monkeypatch.delenv("JARVIS_URL", raising=False)
    monkeypatch.delenv("JARVIS_TOOL_URL", raising=False)
    latest = repos.push({"a.py": "1\n"}, "Add a")
    assert update_cli.main([]) == 0
    assert asked and asked[0].endswith("://127.0.0.1:8340")
    assert repos.head() == latest
    assert "JARVIS is not running" in capsys.readouterr().out


def test_the_command_asks_the_server_where_it_recorded_itself(monkeypatch, repos, capsys):
    _record(os.getpid())
    monkeypatch.setenv("JARVIS_TOOL_URL", "http://127.0.0.1:8340/internal/tool")
    asked = []

    def server_answers(url, version=None):
        asked.append(url)
        return self_update.Outcome(status=self_update.Status(source="github"), ok=True,
                                   updated=True, to_commit="e" * 40).to_json()

    monkeypatch.setattr(update_cli, "ask_server", server_answers)
    before = repos.head()
    assert update_cli.main([]) == 0
    assert asked == ["https://[::1]:8341"]
    assert repos.head() == before, "the server did the work, not the command"
    assert "restarting on the new code" in capsys.readouterr().out


def test_a_recorded_server_that_cannot_be_reached_is_not_updated_under(monkeypatch, repos):
    _record(os.getpid())

    def refused(*a, **k):
        raise update_cli.urllib.error.URLError(ConnectionRefusedError(61, "refused"))

    monkeypatch.setattr(update_cli.urllib.request, "urlopen", refused)
    before = repos.head()
    repos.push({"a.py": "1\n"}, "Add a")
    with pytest.raises(SystemExit, match=r"recorded https://\[::1\]:8341 .*not updating under it"):
        update_cli.main([])
    assert repos.head() == before
