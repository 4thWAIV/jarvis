"""Updating a checkout of JARVIS from GitHub: the one service.

The command (`python -m jarvis.update`), the page's routes (`GET` and
`POST /api/update`) and the brain's `update_status` and `update_jarvis` all
call `check` and `update` here, and say what they return through
`describe` / `describe_outcome`.

Only a checkout whose `origin` is on github.com is offered the update; an
installed copy, or a checkout of somewhere else, is told why not. An update
goes only to `origin/main`'s latest commit, and only when its CI passed: the
GitHub check runs of that commit, read without a login (the repository is
public). It is refused, with the reason, when this checkout is not on main,
has modified tracked files, has commits of its own not on origin/main, has
untracked files the update would overwrite, or the latest commit's CI has
not passed.

`update` moves the checkout to the commit it checked (fast-forward only),
then installs what changed: `requirements.txt` through `.venv/bin/pip`,
`frontend/package-lock.json` through `npm ci`, and the built page through
`npm run build` when the page is served from built files. Restarting is the
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
from pathlib import Path
from typing import Optional

from jarvis import data_paths

BRANCH = "main"
REMOTE = "origin"
UPSTREAM = f"{REMOTE}/{BRANCH}"

# Where each kind of source stands.
GITHUB, INSTALLED, OTHER = "github", "installed", "other"

# The latest commit's CI, as GitHub's check runs say it.
CI_PASSED, CI_PENDING, CI_FAILED, CI_NONE, CI_UNKNOWN = (
    "passed", "pending", "failed", "none", "unknown")
_CI_GOOD = {"success", "skipped", "neutral"}

# What stops an update, each with its own fixed sentence.
NOT_ON_MAIN = "not_on_main"
MODIFIED = "modified"
LOCAL_COMMITS = "local_commits"
WOULD_OVERWRITE = "would_overwrite"
CI_NOT_PASSED = "ci_not_passed"
BLOCKER_WORDS = {
    NOT_ON_MAIN: "This checkout is not on main.",
    MODIFIED: "Tracked files have been changed here.",
    LOCAL_COMMITS: "This checkout has commits of its own that are not on origin/main.",
    WOULD_OVERWRITE: "Untracked files here would be overwritten by the update.",
    CI_NOT_PASSED: "The latest commit's CI has not passed.",
}
_CI_WORDS = {
    CI_PASSED: "its CI passed",
    CI_PENDING: "its CI is still running",
    CI_FAILED: "its CI failed",
    CI_NONE: "it has no CI result",
    CI_UNKNOWN: "GitHub could not be asked about its CI",
}

CANNOT_CHECK = "Could not check for updates."

GIT_TIMEOUT_SEC = 60
STEP_TIMEOUT_SEC = 900
GITHUB_TIMEOUT_SEC = 10
MAX_COMMITS_LISTED = 50

_GITHUB_URL = re.compile(
    r"(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")

# One update at a time: a second asked for while one runs is refused.
_updating = threading.Lock()


@dataclass
class Commit:
    sha: str
    subject: str


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
    commits: list[Commit] = field(default_factory=list)
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
        out.update(up_to_date=self.up_to_date, can_update=self.can_update)
        return out

    @classmethod
    def from_json(cls, data: dict) -> "Status":
        """`to_json` read back (the command reads the server's answer)."""
        return cls(source=data.get("source", ""), reason=data.get("reason", ""),
                   error=data.get("error", ""), branch=data.get("branch", ""),
                   commit=data.get("commit", ""), latest=data.get("latest", ""),
                   behind=int(data.get("behind") or 0),
                   commits=[Commit(c["sha"], c["subject"]) for c in data.get("commits") or []],
                   ci=data.get("ci", ""),
                   blockers=[Blocker(b["code"], b.get("detail") or [])
                             for b in data.get("blockers") or [] if b.get("code") in BLOCKER_WORDS])


@dataclass
class Outcome:
    """What `update` did. `updated` is true once the checkout moved; `ok` is
    false when it was refused or a step failed. `restart` says the server
    should now restart to run the new code."""
    status: Status
    ok: bool = False
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
                "refused": self.refused, "failed": self.failed,
                "from_commit": self.from_commit, "to_commit": self.to_commit,
                "steps": self.steps, "notes": self.notes, "restart": self.restart}

    @classmethod
    def from_json(cls, data: dict) -> "Outcome":
        """`to_json` read back."""
        return cls(status=Status.from_json(data.get("status") or {}),
                   ok=bool(data.get("ok")), updated=bool(data.get("updated")),
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

def fetch_check_runs(owner: str, repo: str, sha: str) -> dict:
    """GitHub's check runs for one commit, without a login."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}/check-runs?per_page=100",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "jarvis-self-update"})
    with urllib.request.urlopen(req, timeout=GITHUB_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def ci_state(owner: str, repo: str, sha: str) -> str:
    """passed only when every check run of the commit finished well."""
    try:
        runs = fetch_check_runs(owner, repo, sha).get("check_runs") or []
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return CI_UNKNOWN
    if not runs:
        return CI_NONE
    if any(r.get("status") != "completed" for r in runs):
        return CI_PENDING
    if any(r.get("conclusion") not in _CI_GOOD for r in runs):
        return CI_FAILED
    return CI_PASSED


# --- check ----------------------------------------------------------------

def check(root: Optional[Path] = None) -> Status:
    """Fetch origin and say where this checkout stands and what stops an
    update. Never raises: a failure is the status's `error`."""
    root = root or repo_root()
    where = source(root)
    if where != GITHUB:
        return Status(source=where, reason=_SOURCE_REASONS[where])
    status = Status(source=GITHUB)
    try:
        _git(root, "fetch", REMOTE)
        status.branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        status.commit = _git(root, "rev-parse", "HEAD").strip()
        status.latest = _git(root, "rev-parse", UPSTREAM).strip()
        status.behind = int(_git(root, "rev-list", "--count", f"HEAD..{UPSTREAM}"))
        ahead = int(_git(root, "rev-list", "--count", f"{UPSTREAM}..HEAD"))
        status.commits = [Commit(*line.split("\x1f", 1)) for line in _git(
            root, "log", "--no-merges", f"--max-count={MAX_COMMITS_LISTED}",
            "--format=%H%x1f%s", f"HEAD..{UPSTREAM}").splitlines() if "\x1f" in line]
        modified = [ln[3:] for ln in _git(
            root, "status", "--porcelain", "--untracked-files=no").splitlines() if ln.strip()]
        overwrite: list[str] = []
        if status.behind:
            added = set(_git(root, "diff", "--name-only", "--diff-filter=A",
                             "HEAD", UPSTREAM).splitlines())
            untracked = set(_git(root, "ls-files", "--others",
                                 "--exclude-standard").splitlines())
            overwrite = sorted(added & untracked)
    except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as e:
        status.error = f"Could not read the checkout or reach GitHub: {e}"
        return status

    if status.branch != BRANCH:
        status.blockers.append(Blocker(NOT_ON_MAIN, [status.branch]))
    if modified:
        status.blockers.append(Blocker(MODIFIED, modified))
    if ahead:
        status.blockers.append(Blocker(LOCAL_COMMITS, [f"{ahead} commits"]))
    if overwrite:
        status.blockers.append(Blocker(WOULD_OVERWRITE, overwrite))
    if status.behind:
        status.ci = ci_state(*github_repo(origin_url(root)), status.latest)
        if status.ci != CI_PASSED:
            status.blockers.append(Blocker(CI_NOT_PASSED))
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


def update(root: Optional[Path] = None) -> Outcome:
    """Check, then move to the checked commit and install what changed.
    Never restarts anything and never raises."""
    root = root or repo_root()
    if not _updating.acquire(blocking=False):
        return Outcome(status=Status(source=GITHUB),
                       refused="An update is already under way.")
    try:
        return _update(root)
    finally:
        _updating.release()


def _update(root: Path) -> Outcome:
    status = check(root)
    out = Outcome(status=status, from_commit=status.commit)
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
        _git(root, "merge", "--ff-only", status.latest)
        out.updated, out.to_commit = True, status.latest
        out.steps.append("Moved to the latest commit.")
        changed = set(_git(root, "diff", "--name-only", status.commit, status.latest)
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
        return out
    out.ok = True
    return out


# --- saying it --------------------------------------------------------------

def describe(status: Status) -> tuple[str, list[str]]:
    """(a head in fixed words, the details that quote commits and files)."""
    if status.source != GITHUB:
        return status.reason, []
    if status.error:
        return CANNOT_CHECK, [status.error]
    if status.up_to_date:
        head = "Up to date."
        details = [f"At {status.commit[:7]} on {status.branch}."]
    else:
        head = f"{status.behind} new commit{'s' if status.behind != 1 else ''} on origin/main"
        head += f"; {_CI_WORDS[status.ci]}." if status.ci else "."
        details = [f"{c.sha[:7]} {c.subject}" for c in status.commits]
    if status.blockers:
        head += " The update is refused: " + " ".join(
            BLOCKER_WORDS[b.code] for b in status.blockers)
        details += _blocker_lines(status)
    elif status.can_update:
        head += " It can be updated."
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
        moved = (" The checkout was moved to the latest commit, but an install step "
                 "failed, so nothing was restarted." if out.updated else "")
        return "The update failed." + moved, [out.failed]
    if not out.updated:
        return "Already up to date; nothing to do.", []
    head = f"Updated to {out.to_commit[:7]}." + ("" if not out.notes else " " + " ".join(out.notes))
    return head, out.steps
