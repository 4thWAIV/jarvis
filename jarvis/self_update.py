"""Updating a checkout of JARVIS from GitHub: the one service.

The command (`python -m jarvis.update`), the page's routes (`GET` and
`POST /api/update`) and the brain's `update_status` and `update_jarvis` all
call `check` and `update` here, and say what they return through
`describe` / `describe_outcome`.

Only a checkout whose `origin` is on github.com is offered the update; an
installed copy, or a checkout of somewhere else, is told why not.

A version is a pull request merged to main: each first-parent merge commit
on `origin/main` newer than this checkout (`newer_versions`), named by its
PR number and title, with its date and its CI. origin/main's latest commit
is listed too when it is not a merge. The version running here is described
the same way (`current_version`). An update goes forward only, to one of
those versions (origin/main's latest unless another is chosen), and only
when its CI passed: the run of the `ci` workflow
(`.github/workflows/ci.yml`) for that commit, read from GitHub's Actions
runs without a login (the repository is public), one request for every
version listed (`ci_lookup`). Other workflows that attach to the commit,
such as the scheduled team server health check, do not count. It is
refused, with the reason, when this checkout is not on main, has modified
tracked files, has commits of its own not on origin/main, has untracked
files the update would overwrite, the version chosen is not one offered (an
older one included), or its CI has not passed.

`update` moves the checkout to the version it checked (fast-forward only),
then installs what changed: `requirements.txt` through `.venv/bin/pip`,
`frontend/package-lock.json` through `npm ci`, and the built page through
`npm run build` when the page is served from built files. When a step
fails, the checkout is moved back to where it was (`git reset --keep`), so
the next update runs every step again. Restarting is the
caller's: the server restarts itself (`server._restart_server`), and the
command run with the server stopped only updates.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from jarvis import data_paths

BRANCH = "main"
REMOTE = "origin"
UPSTREAM = f"{REMOTE}/{BRANCH}"

# Where each kind of source stands.
GITHUB, INSTALLED, OTHER = "github", "installed", "other"

# The latest commit's CI: its run of the ci workflow, as GitHub says it.
CI_WORKFLOW = ".github/workflows/ci.yml"
CI_PASSED, CI_PENDING, CI_FAILED, CI_NONE, CI_UNKNOWN = (
    "passed", "pending", "failed", "none", "unknown")
_CI_GOOD = {"success", "skipped", "neutral"}

# What stops an update, each with its own fixed sentence.
NOT_ON_MAIN = "not_on_main"
MODIFIED = "modified"
LOCAL_COMMITS = "local_commits"
WOULD_OVERWRITE = "would_overwrite"
CI_NOT_PASSED = "ci_not_passed"
NOT_NEWER = "not_newer"
NOT_OFFERED = "not_offered"
VERSION_CI_NOT_PASSED = "version_ci_not_passed"
BLOCKER_WORDS = {
    NOT_ON_MAIN: "This checkout is not on main.",
    MODIFIED: "Tracked files have been changed here.",
    LOCAL_COMMITS: "This checkout has commits of its own that are not on origin/main.",
    WOULD_OVERWRITE: "Untracked files here would be overwritten by the update.",
    CI_NOT_PASSED: "The latest commit's CI has not passed.",
    NOT_NEWER: "That version is not newer than the one running here; going back to an "
               "older version is not offered.",
    NOT_OFFERED: "That is not one of the versions offered.",
    VERSION_CI_NOT_PASSED: "That version's CI has not passed.",
}
_CI_WORDS = {
    CI_PASSED: "its CI passed",
    CI_PENDING: "its CI is still running",
    CI_FAILED: "its CI failed",
    CI_NONE: "it has no CI result",
    CI_UNKNOWN: "GitHub could not be asked about its CI",
}

# Why a listed version cannot be chosen, by its CI.
_UNCHOSEN_WORDS = {
    CI_PENDING: "can be chosen once its tests pass",
    CI_FAILED: "can't be chosen: its tests failed",
    CI_NONE: "can't be chosen: it has no test result",
    CI_UNKNOWN: "can be chosen once GitHub can be asked about its tests",
}

CANNOT_CHECK = "Could not check for updates."

GIT_TIMEOUT_SEC = 60
STEP_TIMEOUT_SEC = 900
GITHUB_TIMEOUT_SEC = 10
MAX_VERSIONS_LISTED = 50
CI_RUNS_PER_REQUEST = 100

# A merge commit GitHub made for a pull request; its body's first line is
# the pull request's title.
_PR_MERGE = re.compile(r"Merge pull request #(\d+)\b")
# One commit per record: sha, parents, committer date, subject, body.
_LOG_FORMAT = "--format=%H%x1f%P%x1f%cI%x1f%s%x1f%b%x1e"

_GITHUB_URL = re.compile(
    r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")

# One update at a time: a second asked for while one runs is refused.
_updating = threading.Lock()


@dataclass
class Version:
    """One version: a pull request merged to main, or, when a commit is not
    a pull request's merge, that commit by its short sha and subject."""
    sha: str
    pr: Optional[int] = None
    title: str = ""
    date: str = ""
    ci: str = ""

    @property
    def name(self) -> str:
        """In fixed words: "PR 42", or the short sha."""
        return f"PR {self.pr}" if self.pr is not None else self.sha[:7]

    @property
    def can_choose(self) -> bool:
        return self.ci == CI_PASSED

    @property
    def unchosen(self) -> str:
        """Why it cannot be chosen, or "" when it can."""
        return "" if self.can_choose else _UNCHOSEN_WORDS.get(self.ci, "")

    def to_json(self) -> dict:
        return {**asdict(self), "name": self.name, "can_choose": self.can_choose,
                "unchosen": self.unchosen}

    @classmethod
    def from_json(cls, data: Optional[dict]) -> Optional["Version"]:
        if not data or not data.get("sha"):
            return None
        pr = data.get("pr")
        return cls(sha=data["sha"], pr=int(pr) if pr is not None else None,
                   title=data.get("title", ""), date=data.get("date", ""),
                   ci=data.get("ci", ""))


@dataclass
class Blocker:
    code: str
    detail: list[str] = field(default_factory=list)


@dataclass
class Status:
    source: str
    reason: str = ""
    error: str = ""
    branch: str = ""
    commit: str = ""
    latest: str = ""
    behind: int = 0
    current: Optional[Version] = None
    versions: list[Version] = field(default_factory=list)
    # What an update would move to: origin/main's latest, or the version
    # asked for; `chosen` is that commit as a listed version, and `ci` its CI.
    target: str = ""
    chosen: Optional[Version] = None
    ci: str = ""
    blockers: list[Blocker] = field(default_factory=list)

    @property
    def up_to_date(self) -> bool:
        return self.source == GITHUB and not self.error and self.behind == 0

    @property
    def can_update(self) -> bool:
        return (self.source == GITHUB and not self.error and self.behind > 0
                and not self.blockers)

    def to_json(self) -> dict:
        out = asdict(self)
        out["blockers"] = [{**asdict(b), "text": BLOCKER_WORDS[b.code]} for b in self.blockers]
        out["current"] = self.current.to_json() if self.current else None
        out["versions"] = [v.to_json() for v in self.versions]
        out["chosen"] = self.chosen.to_json() if self.chosen else None
        out.update(up_to_date=self.up_to_date, can_update=self.can_update)
        return out

    @classmethod
    def from_json(cls, data: dict) -> "Status":
        """`to_json` read back (the command reads the server's answer)."""
        return cls(source=data.get("source", ""), reason=data.get("reason", ""),
                   error=data.get("error", ""), branch=data.get("branch", ""),
                   commit=data.get("commit", ""), latest=data.get("latest", ""),
                   behind=int(data.get("behind") or 0),
                   current=Version.from_json(data.get("current")),
                   versions=[v for v in map(Version.from_json, data.get("versions") or []) if v],
                   target=data.get("target", ""),
                   chosen=Version.from_json(data.get("chosen")),
                   ci=data.get("ci", ""),
                   blockers=[Blocker(b["code"], b.get("detail") or [])
                             for b in data.get("blockers") or [] if b.get("code") in BLOCKER_WORDS])


@dataclass
class Outcome:
    """What `update` did. `updated` is true when the checkout was left on
    the new commit (a failed step moves it back, see `_move_back`); `ok` is
    false when it was refused or a step failed. `restart` says the server
    should now restart to run the new code."""
    status: Status
    ok: bool = False
    version: Optional[Version] = None
    updated: bool = False
    refused: str = ""
    failed: str = ""
    from_commit: str = ""
    to_commit: str = ""
    steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def restart(self) -> bool:
        return self.ok and self.updated

    def to_json(self) -> dict:
        return {"status": self.status.to_json(), "ok": self.ok, "updated": self.updated,
                "version": self.version.to_json() if self.version else None,
                "refused": self.refused, "failed": self.failed,
                "from_commit": self.from_commit, "to_commit": self.to_commit,
                "steps": self.steps, "notes": self.notes, "restart": self.restart}

    @classmethod
    def from_json(cls, data: dict) -> "Outcome":
        """`to_json` read back."""
        return cls(status=Status.from_json(data.get("status") or {}),
                   ok=bool(data.get("ok")), updated=bool(data.get("updated")),
                   version=Version.from_json(data.get("version")),
                   refused=data.get("refused", ""), failed=data.get("failed", ""),
                   from_commit=data.get("from_commit", ""), to_commit=data.get("to_commit", ""),
                   steps=list(data.get("steps") or []), notes=list(data.get("notes") or []))


# --- where JARVIS's source is -------------------------------------------

def repo_root() -> Path:
    """The checkout this JARVIS runs from."""
    return data_paths.SOURCE_ROOT


def is_checkout() -> bool:
    return data_paths.CHECKOUT


def serves_built_page() -> bool:
    """True when the server serves the page from built files (no Vite)."""
    return data_paths.FRONTEND_DIST.is_dir()


def _git(root: Path, *args: str, timeout: float = GIT_TIMEOUT_SEC) -> str:
    done = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                          timeout=timeout)
    if done.returncode != 0:
        raise UpdateError(_last_line(done.stderr) or f"git {args[0]} failed")
    return done.stdout


class UpdateError(Exception):
    """A git command or an install step that did not succeed, with why."""


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def origin_url(root: Path) -> str:
    """origin's URL as configured (not rewritten by any insteadOf)."""
    try:
        return _git(root, "config", "--get", f"remote.{REMOTE}.url").strip()
    except (UpdateError, OSError, subprocess.SubprocessError):
        return ""


def github_repo(url: str) -> Optional[tuple[str, str]]:
    """(owner, repository) of a github.com remote URL, else None."""
    m = _GITHUB_URL.fullmatch(url.strip())
    return (m.group(1), m.group(2)) if m else None


def source(root: Optional[Path] = None) -> str:
    """github, installed or other."""
    if not is_checkout():
        return INSTALLED
    return GITHUB if github_repo(origin_url(root or repo_root())) else OTHER


_SOURCE_REASONS = {
    INSTALLED: "This JARVIS is an installed copy, not a checkout, so it does not update "
               "through GitHub.",
    OTHER: "This checkout's origin is not on GitHub, so it does not update through GitHub.",
}


# --- the CI of a commit ---------------------------------------------------

def fetch_ci_runs(owner: str, repo: str) -> dict:
    """The ci workflow's latest runs on main, without a login: one request
    answers for every version listed."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
        f"{CI_WORKFLOW.rsplit('/', 1)[-1]}/runs?branch={BRANCH}&per_page={CI_RUNS_PER_REQUEST}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "jarvis-self-update"})
    with urllib.request.urlopen(req, timeout=GITHUB_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def ci_state(runs: list, sha: str) -> str:
    """A commit's latest run of the ci workflow among `runs`: passed only
    when it finished well, none when the commit has no such run (a
    docs-only commit skips it). Runs of any other workflow are not looked
    at."""
    mine = [r for r in runs if isinstance(r, dict)
            and r.get("path") == CI_WORKFLOW and r.get("head_sha") == sha]
    if not mine:
        return CI_NONE
    latest = max(mine, key=lambda r: (str(r.get("created_at") or ""), r.get("id") or 0))
    if latest.get("status") != "completed":
        return CI_PENDING
    if latest.get("conclusion") not in _CI_GOOD:
        return CI_FAILED
    return CI_PASSED


def ci_lookup(owner: str, repo: str) -> Callable[[str], str]:
    """Each commit's CI, from one request to GitHub; unknown for every
    commit when GitHub cannot be asked."""
    try:
        runs = fetch_ci_runs(owner, repo).get("workflow_runs") or []
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return lambda sha: CI_UNKNOWN
    if not isinstance(runs, list):
        return lambda sha: CI_UNKNOWN
    return lambda sha: ci_state(runs, sha)


# --- versions ---------------------------------------------------------------

def _read_log(text: str) -> list[tuple[Version, bool]]:
    """`_LOG_FORMAT` records as (version, whether it is a merge)."""
    out = []
    for record in text.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, date, subject, body = (record.split("\x1f", 4) + [""] * 5)[:5]
        merge = len(parents.split()) > 1
        pr = _PR_MERGE.match(subject) if merge else None
        title = next((ln.strip() for ln in body.splitlines() if ln.strip()), "") if pr else ""
        out.append((Version(sha=sha, pr=int(pr.group(1)) if pr else None,
                            title=title or subject, date=date), merge))
    return out


def current_version(root: Path) -> Version:
    """The version running here: HEAD, by its pull request when it is one's
    merge, else by its short sha and subject."""
    return _read_log(_git(root, "log", "-1", _LOG_FORMAT, "HEAD"))[0][0]


def newer_versions(root: Path) -> list[Version]:
    """Newest first: each first-parent merge on origin/main newer than HEAD,
    and origin/main's latest commit when it is not a merge, so the default
    update is always one of them."""
    listed = _read_log(_git(root, "log", "--first-parent", _LOG_FORMAT, f"HEAD..{UPSTREAM}"))
    return [v for i, (v, merge) in enumerate(listed) if merge or i == 0][:MAX_VERSIONS_LISTED]


def _is_ancestor(root: Path, older: str, newer: str) -> bool:
    done = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], cwd=str(root),
                          capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC)
    if done.returncode not in (0, 1):
        raise UpdateError(_last_line(done.stderr) or "git merge-base failed")
    return done.returncode == 0


_ASKED_PR = re.compile(r"(?:pr\s*|pull request\s*|#)?(\d{1,6})", re.IGNORECASE)
_ASKED_SHA = re.compile(r"[0-9a-f]{7,40}")


def _find_version(root: Path, versions: list[Version], asked: str) -> tuple[Optional[Version], str]:
    """The listed version `asked` names (a PR number, or a sha of at least
    seven characters), else why not: NOT_NEWER for the one running here or
    an older one, NOT_OFFERED for anything else."""
    text = asked.strip().lower()
    pr = _ASKED_PR.fullmatch(text)
    if pr:
        number = int(pr.group(1))
        found = next((v for v in versions if v.pr == number), None)
        if found:
            return found, ""
        older = _read_log(_git(root, "log", "--first-parent", "--merges", _LOG_FORMAT, "HEAD"))
        return None, NOT_NEWER if any(v.pr == number for v, _m in older) else NOT_OFFERED
    if _ASKED_SHA.fullmatch(text):
        found = next((v for v in versions if v.sha.startswith(text)), None)
        if found:
            return found, ""
        try:
            sha = _git(root, "rev-parse", "--verify", "--quiet", f"{text}^{{commit}}").strip()
        except UpdateError:
            return None, NOT_OFFERED
        return None, NOT_NEWER if _is_ancestor(root, sha, "HEAD") else NOT_OFFERED
    return None, NOT_OFFERED


# --- check ----------------------------------------------------------------

def check(root: Optional[Path] = None, version: Optional[str] = None) -> Status:
    """Fetch origin and say where this checkout stands, the versions newer
    than it, and what stops an update to `version` (a PR number or a sha;
    origin/main's latest when none is given). Never raises: a failure is the
    status's `error`."""
    root = root or repo_root()
    where = source(root)
    if where != GITHUB:
        return Status(source=where, reason=_SOURCE_REASONS[where])
    status = Status(source=GITHUB)
    asked = (version or "").strip()
    not_chosen = ""
    try:
        _git(root, "fetch", REMOTE)
        status.branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        status.commit = _git(root, "rev-parse", "HEAD").strip()
        status.latest = _git(root, "rev-parse", UPSTREAM).strip()
        status.behind = int(_git(root, "rev-list", "--count", f"HEAD..{UPSTREAM}"))
        ahead = int(_git(root, "rev-list", "--count", f"{UPSTREAM}..HEAD"))
        status.current = current_version(root)
        status.versions = newer_versions(root) if status.behind else []
        if asked:
            status.chosen, not_chosen = _find_version(root, status.versions, asked)
        else:
            status.chosen = next((v for v in status.versions if v.sha == status.latest), None)
        status.target = status.chosen.sha if status.chosen else ""
        modified = [ln[3:] for ln in _git(
            root, "status", "--porcelain", "--untracked-files=no").splitlines() if ln.strip()]
        overwrite: list[str] = []
        if status.target:
            # --no-renames: a path upstream renamed or copied a file to is new too.
            added = set(_git(root, "diff", "--name-only", "--no-renames",
                             "--diff-filter=A", "HEAD", status.target).splitlines())
            untracked = set(_git(root, "ls-files", "--others",
                                 "--exclude-standard").splitlines())
            overwrite = sorted(added & untracked)
    except (UpdateError, OSError, ValueError, IndexError, subprocess.SubprocessError) as e:
        status.error = f"Could not read the checkout or reach GitHub: {e}"
        return status

    if status.versions:
        ci = ci_lookup(*github_repo(origin_url(root)))
        for listed in status.versions:
            listed.ci = ci(listed.sha)
    if status.branch != BRANCH:
        status.blockers.append(Blocker(NOT_ON_MAIN, [status.branch]))
    if modified:
        status.blockers.append(Blocker(MODIFIED, modified))
    if ahead:
        status.blockers.append(Blocker(LOCAL_COMMITS, [f"{ahead} commits"]))
    if overwrite:
        status.blockers.append(Blocker(WOULD_OVERWRITE, overwrite))
    if not_chosen:
        status.blockers.append(Blocker(not_chosen, [asked]))
    elif status.chosen:
        status.ci = status.chosen.ci
        if not status.chosen.can_choose:
            status.blockers.append(Blocker(VERSION_CI_NOT_PASSED, [status.chosen.name])
                                   if asked else Blocker(CI_NOT_PASSED))
    return status


# --- update ---------------------------------------------------------------

def _step(cmd: list[str], cwd: Path) -> None:
    """Run one install step; a failure raises with its last line."""
    try:
        done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              timeout=STEP_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError(f"{' '.join(cmd)}: {e}") from e
    if done.returncode != 0:
        raise UpdateError(f"{' '.join(cmd)}: "
                          + (_last_line(done.stderr) or _last_line(done.stdout)
                             or f"exit {done.returncode}"))


def update(root: Optional[Path] = None, version: Optional[str] = None) -> Outcome:
    """Check, then move to the version checked (origin/main's latest unless
    `version` names another) and install what changed. Never restarts
    anything and never raises."""
    root = root or repo_root()
    if not _updating.acquire(blocking=False):
        return Outcome(status=Status(source=GITHUB),
                       refused="An update is already under way.")
    try:
        return _update(root, version)
    finally:
        _updating.release()


def _update(root: Path, version: Optional[str]) -> Outcome:
    status = check(root, version)
    out = Outcome(status=status, from_commit=status.commit, version=status.chosen)
    if status.source != GITHUB:
        out.refused = status.reason
        return out
    if status.error:
        out.refused = CANNOT_CHECK
        return out
    if status.blockers:
        out.refused = " ".join(BLOCKER_WORDS[b.code] for b in status.blockers)
        return out
    if not status.behind:
        out.ok = True
        out.to_commit = status.commit
        return out
    try:
        # The commit that was checked, not whatever origin/main is by now:
        # the update goes only to a commit whose CI passed.
        _git(root, "merge", "--ff-only", status.target)
        out.updated, out.to_commit = True, status.target
        out.steps.append(f"Moved to {status.chosen.name}.")
        changed = set(_git(root, "diff", "--name-only", status.commit, status.target)
                      .splitlines())
        if "requirements.txt" in changed:
            _step([str(root / ".venv" / "bin" / "pip"), "install", "-r", "requirements.txt"],
                  root)
            out.steps.append("Installed the Python requirements.")
        frontend = root / "frontend"
        if "frontend/package-lock.json" in changed:
            _step(["npm", "ci"], frontend)
            out.steps.append("Installed the frontend's packages.")
            if not serves_built_page():
                out.notes.append("The frontend's packages changed: restart npm run dev.")
        if serves_built_page() and any(p.startswith("frontend/") for p in changed):
            _step(["npm", "run", "build"], frontend)
            out.steps.append("Built the page.")
    except (UpdateError, OSError, subprocess.SubprocessError) as e:
        out.failed = str(e)
        if out.updated:
            _move_back(root, out)
        return out
    out.ok = True
    return out


def _move_back(root: Path, out: Outcome) -> None:
    """After a failed step, put the checkout back on the commit it was on,
    keeping anything changed since, so the update is offered again and its
    next run does every step."""
    try:
        _git(root, "reset", "--keep", out.from_commit)
    except (UpdateError, OSError, subprocess.SubprocessError) as e:
        out.failed += f" Moving back to {out.from_commit[:7]} failed too: {e}"
        return
    out.updated, out.to_commit = False, ""
    out.steps.append(f"Moved back to {out.from_commit[:7]}.")


# --- saying it --------------------------------------------------------------

def _day(iso: str) -> str:
    """"1 Nov" from a commit's ISO date."""
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return f"{when.day} {when:%b}"


def version_line(listed: Version) -> str:
    """One version as the details say it: its name, title, short sha, date
    and CI. The title is a teammate's words."""
    label = (f"{listed.name}: {listed.title} ({listed.sha[:7]})" if listed.pr is not None
             else f"{listed.sha[:7]} {listed.title}")
    ci = f"; {_CI_WORDS[listed.ci]}" if listed.ci in _CI_WORDS else ""
    return f"{label}, {_day(listed.date)}{ci}"


def newest_choosable(status: Status) -> Optional[Version]:
    return next((v for v in status.versions if v.can_choose), None)


def describe(status: Status) -> tuple[str, list[str]]:
    """(a head in fixed words, the details that quote titles and files)."""
    if status.source != GITHUB:
        return status.reason, []
    if status.error:
        return CANNOT_CHECK, [status.error]
    running = ([f"Running {version_line(status.current)}, on {status.branch}."]
               if status.current else [])
    if status.up_to_date:
        head = "Up to date."
        details = running
    else:
        count = len(status.versions)
        head = f"{count} newer version{'s' if count != 1 else ''} on origin/main."
        details = running + [f"{version_line(v)}." for v in status.versions]
    if status.blockers:
        head += " The update is refused: " + " ".join(
            BLOCKER_WORDS[b.code] for b in status.blockers)
        details += _blocker_lines(status)
        best = newest_choosable(status)
        if best and all(b.code in (CI_NOT_PASSED, VERSION_CI_NOT_PASSED, NOT_NEWER, NOT_OFFERED)
                        for b in status.blockers):
            head += f" The newest that can be installed is {best.name}."
    elif status.can_update:
        head += f" {status.chosen.name} can be installed."
    return head, details


def _blocker_lines(status: Status) -> list[str]:
    return [f"{BLOCKER_WORDS[b.code]} {', '.join(b.detail)}"
            for b in status.blockers if b.detail]


def describe_outcome(out: Outcome) -> tuple[str, list[str]]:
    """What an update did, as `describe` says a check."""
    if out.refused:
        error = [out.status.error] if out.status.error else []
        return f"Not updated. {out.refused}", _blocker_lines(out.status) + error
    if out.failed:
        where = (" The checkout was moved to the new version, but an install step "
                 "failed and it could not be moved back, so nothing was restarted."
                 if out.updated else
                 " The checkout is where it was, so the update can be tried again.")
        return "The update failed." + where, [out.failed]
    if not out.updated:
        return "Already up to date; nothing to do.", []
    name = out.version.name if out.version else out.to_commit[:7]
    head = f"Updated to {name}." + ("" if not out.notes else " " + " ".join(out.notes))
    return head, out.steps
