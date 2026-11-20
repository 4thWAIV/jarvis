"""
role_lookups.py — retrieval's GitHub lookups, as tools on its own tool
server (role_mcp.py).

Tim, 2026-11-20: retrieval gets only the lookups it needs as MCP tools on
its own role tool server, and no shell on either brain. Each tool here runs
exactly one fixed, read-only `gh` lookup: the program builds its argument
list, and what the role gives (a repository, a number, search words) goes
in only as data, checked by shape, never a shell string. The call is the
same seam the brain's repository lookups use (`gh_lookup._run_gh`: an
argument list), with no deadline: the design gives these lookups none, so
one answers when GitHub does (a long branch list included).

They find what a reference names and never fetch it (sessions.yaml
RetrievalSession: "it works out what a reference names; it never fetches,
copies, summarises or judges"): lists, searches, branches and each thing's
record enough to say which it is, never a pull request's diff or
description, a CI run's jobs or logs, a commit's diff or a branch's
commits. Those are the program's fetch, gather's resolve_typed
(`evidence_readers.py`), which uses the same checks of shape.

These run in the role's tool server itself, never as the brain's tools:
they are retrieval's alone (`roles.RETRIEVAL_LOOKUPS`).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Callable

from jarvis import gh_lookup

# What each kind of thing is read back with (`--json`), enough to say what a
# reference names.
_PR_FIELDS = "number,title,state,url,headRefName,baseRefName,author,createdAt,mergedAt"
_PR_VIEW_FIELDS = _PR_FIELDS + ",mergeCommit,headRefOid"
_ISSUE_FIELDS = "number,title,state,url,author,createdAt,closedAt"
_RUN_FIELDS = ("databaseId,name,workflowName,displayTitle,status,conclusion,event,"
               "headBranch,headSha,url,createdAt")
_SEARCH_PR_FIELDS = "number,title,state,url,repository,createdAt"
_SEARCH_COMMIT_FIELDS = "sha,url,repository,commit"

_REPO = {"type": "string", "description": "The repository, as owner/name."}
_NUMBER = {"type": "integer", "minimum": 1}
_LIMIT = {"type": "integer", "minimum": 1, "description": "Optional: how many at most."}
_STATE = {"type": "string", "enum": ["open", "closed", "merged", "all"],
          "description": "Optional: which ones (merged is for pull requests)."}
_WORDS = {"type": "string", "description": "The words to search for."}

_DIGITS = re.compile(r"[0-9]{1,20}")
_SHA = re.compile(r"[0-9a-fA-F]{4,40}")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,255}")


class Refused(ValueError):
    """An argument that is not the shape its lookup takes."""


def _repo(args: dict) -> str:
    repo = str(args.get("repo") or "").strip()
    if not gh_lookup.FULL_NAME_RE.fullmatch(repo):
        raise Refused("repo must be owner/name")
    return repo


def _number(args: dict, key: str) -> str:
    value = args.get(key)
    text = str(value).strip() if isinstance(value, (int, str)) and not isinstance(value, bool) \
        else ""
    if not _DIGITS.fullmatch(text) or int(text) < 1:
        raise Refused(f"{key} must be a number")
    return str(int(text))


def _optional(args: dict, key: str, shape: re.Pattern | None = None) -> str | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    text = str(value).strip()
    if shape is not None and not shape.fullmatch(text):
        raise Refused(f"{key} is not a {key}")
    return text


def _state(args: dict, allowed: tuple[str, ...]) -> list[str]:
    state = _optional(args, "state")
    if state is None:
        return []
    if state not in allowed:
        raise Refused(f"state must be one of {', '.join(allowed)}")
    return [f"--state={state}"]


def _limit(args: dict) -> list[str]:
    return [f"--limit={_number(args, 'limit')}"] if args.get("limit") not in (None, "") else []


def _words(args: dict) -> str:
    words = str(args.get("query") or "").strip()
    if not words:
        raise Refused("query must name what to search for")
    return words


def _in_repo(args: dict) -> list[str]:
    return [f"--repo={_repo(args)}"] if args.get("repo") else []


# ── each lookup: its argument list from the role's data ──────────────────

def _pr_list(a: dict) -> list[str]:
    search = _optional(a, "search")
    return (["pr", "list", f"--repo={_repo(a)}", f"--json={_PR_FIELDS}"]
            + _state(a, ("open", "closed", "merged", "all")) + _limit(a)
            + ([f"--search={search}"] if search else []))


def _pr_view(a: dict) -> list[str]:
    return ["pr", "view", _number(a, "number"), f"--repo={_repo(a)}", f"--json={_PR_VIEW_FIELDS}"]


def _issue_list(a: dict) -> list[str]:
    search = _optional(a, "search")
    return (["issue", "list", f"--repo={_repo(a)}", f"--json={_ISSUE_FIELDS}"]
            + _state(a, ("open", "closed", "all")) + _limit(a)
            + ([f"--search={search}"] if search else []))


def _issue_view(a: dict) -> list[str]:
    return ["issue", "view", _number(a, "number"), f"--repo={_repo(a)}",
            f"--json={_ISSUE_FIELDS}"]


def _run_list(a: dict) -> list[str]:
    branch = _optional(a, "branch", _BRANCH)
    commit = _optional(a, "commit", _SHA)
    return (["run", "list", f"--repo={_repo(a)}", f"--json={_RUN_FIELDS}"] + _limit(a)
            + ([f"--branch={branch}"] if branch else [])
            + ([f"--commit={commit}"] if commit else []))


def _run_view(a: dict) -> list[str]:
    return ["run", "view", _number(a, "run_id"), f"--repo={_repo(a)}",
            f"--json={_RUN_FIELDS}"]


def _search_prs(a: dict) -> list[str]:
    return (["search", "prs", f"--json={_SEARCH_PR_FIELDS}"] + _in_repo(a) + _limit(a)
            + ["--", _words(a)])


def _search_issues(a: dict) -> list[str]:
    return (["search", "issues", f"--json={_SEARCH_PR_FIELDS}"] + _in_repo(a) + _limit(a)
            + ["--", _words(a)])


def _search_commits(a: dict) -> list[str]:
    return (["search", "commits", f"--json={_SEARCH_COMMIT_FIELDS}"] + _in_repo(a) + _limit(a)
            + ["--", _words(a)])


def commit_sha(a: dict) -> str:
    sha = _optional(a, "sha", _SHA)
    if sha is None:
        raise Refused("sha must name a commit")
    return sha


def branch_name(a: dict) -> str:
    name = _optional(a, "name", _BRANCH)
    if name is None or ".." in name or name.startswith(("/", "-")) or name.endswith("/"):
        raise Refused("name must name a branch")
    return name


def _commit_view(a: dict) -> list[str]:
    # One commit by its hash, enough to say which it is: its full hash,
    # address, author, date and the first line of its message, read through
    # GitHub's API with a fixed path, GET only, never its diff. `gh search
    # commits` does not find a repository's commits by hash.
    return ["api", "--method=GET", f"repos/{_repo(a)}/commits/{commit_sha(a)}",
            "--jq=" + _COMMIT_JQ]


_COMMIT_JQ = ("{sha, url: .html_url, author: .commit.author.name, "
              "date: .commit.author.date, subject: (.commit.message | split(\"\\n\")[0])}")


def _branches(a: dict) -> list[str]:
    # What `git ls-remote` gave: the repository's branches and the commit
    # each is at, read through GitHub's API with a fixed path, GET only.
    return ["api", "--method=GET", f"repos/{_repo(a)}/branches", "--paginate"]


@dataclass(frozen=True)
class Lookup:
    name: str
    description: str
    properties: dict
    required: tuple[str, ...]
    argv: Callable[[dict], list[str]]

    @property
    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": {"type": "object", "properties": self.properties,
                                "required": list(self.required)}}


LOOKUPS: dict[str, Lookup] = {l.name: l for l in (
    Lookup("github_pr_list", "List a repository's pull requests (gh pr list), optionally by "
           "state and search words.",
           {"repo": _REPO, "state": _STATE, "search": {"type": "string"}, "limit": _LIMIT},
           ("repo",), _pr_list),
    Lookup("github_pr_view", "One pull request by its number (gh pr view).",
           {"repo": _REPO, "number": _NUMBER}, ("repo", "number"), _pr_view),
    Lookup("github_run_list", "List a repository's CI runs (gh run list), optionally on a "
           "branch or for a commit.",
           {"repo": _REPO, "branch": {"type": "string"}, "commit": {"type": "string"},
            "limit": _LIMIT}, ("repo",), _run_list),
    Lookup("github_run_view", "One CI run by its id (gh run view): its workflow, branch, "
           "commit and conclusion.",
           {"repo": _REPO, "run_id": _NUMBER}, ("repo", "run_id"), _run_view),
    Lookup("github_issue_list", "List a repository's issues (gh issue list), optionally by "
           "state and search words.",
           {"repo": _REPO, "state": _STATE, "search": {"type": "string"}, "limit": _LIMIT},
           ("repo",), _issue_list),
    Lookup("github_issue_view", "One issue by its number (gh issue view).",
           {"repo": _REPO, "number": _NUMBER}, ("repo", "number"), _issue_view),
    Lookup("github_search_prs", "Search pull requests by words (gh search prs), optionally "
           "in one repository.",
           {"query": _WORDS, "repo": _REPO, "limit": _LIMIT}, ("query",), _search_prs),
    Lookup("github_search_issues", "Search issues by words (gh search issues), optionally in "
           "one repository.",
           {"query": _WORDS, "repo": _REPO, "limit": _LIMIT}, ("query",), _search_issues),
    Lookup("github_commit_view", "One commit by its hash, full or short: its full hash, "
           "author, date and subject (GitHub's commits API).",
           {"repo": _REPO, "sha": {"type": "string", "description": "The commit's hash."}},
           ("repo", "sha"), _commit_view),
    Lookup("github_search_commits", "Search commit messages by words (gh search commits), "
           "optionally in one repository. A commit named by its hash is "
           "github_commit_view's.",
           {"query": _WORDS, "repo": _REPO, "limit": _LIMIT}, ("query",), _search_commits),
    Lookup("github_branches", "A repository's branches and the commit each is at.",
           {"repo": _REPO}, ("repo",), _branches),
)}

NAMES: tuple[str, ...] = tuple(LOOKUPS)


def argv(name: str, arguments: dict) -> list[str]:
    """The fixed `gh` argument list of one lookup. Raises KeyError for no
    such lookup and Refused for an argument of the wrong shape."""
    return LOOKUPS[name].argv(arguments)


def call(name: str, arguments: dict) -> tuple[bool, str]:
    """Run one lookup: (ok, gh's JSON or why it did not answer)."""
    try:
        args = argv(name, arguments)
    except KeyError:
        return False, f"No lookup called {name!r}."
    except Refused as e:
        return False, f"Refused: {e}."
    if gh_lookup.gh_path() is None:
        return False, "gh is not installed on this Mac."
    try:
        code, out, err = asyncio.run(gh_lookup._run_gh(args, None))
    except OSError as e:
        return False, f"gh could not run: {e}."
    if code != 0:
        return False, f"GitHub answered: {gh_lookup._problem_from(err)}: {err.strip()[:300]}"
    return True, out
