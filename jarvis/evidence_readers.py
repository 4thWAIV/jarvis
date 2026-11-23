"""Each typed evidence entry read by a program (workflow.yaml request
entry_types and gather resolve_typed, approved by Tim 2026-11-19).

Every type but `label` (free text, RetrievalSession's) and `file` and
`content` (copied and kept by the gather step) has its reader here, and
each answers with a `Read`: the text gathered for the stored file, named
`<type>-<id>.md` (gather layout `retrieved/`), and where it came from, or
not_found or failed with the reason. Nothing is guessed: a reference that
names nothing is not_found, saying what was looked for.

  url        one standard fetch: a plain fetch, its bytes read by the one
             file reader (content_sections.extract), then the page reader
             (browser.read_page) for a page that gave no readable text; a
             GitHub address is read as its own type (`github_entry`)
  work_item  JARVIS's Work item reader (`work_item_text`)
  package    JARVIS's package reader: the inbox row, its questions and what
             is recorded for each
  message    JARVIS's message reader (messages_api.message_json)
  workflow   JARVIS's workflow reader (workflow_store.snapshot)
  run        JARVIS's run reader: the run's record and its output stream
  pr         gh pr view and gh pr diff
  ci_run     gh run view and its failed logs
  commit     git show, in a checkout of the repository on this Mac
  issue      gh issue view
  branch     its commits not on main (GitHub's compare API)

Every gh call is a fixed argument list through the one seam
(gh_lookup._run_gh), what the entry names going in only as data, checked
by shape (role_lookups' checks). These fetches are the program's alone:
none is offered to a model (retrieval's own lookups only find what a
reference names, sessions.yaml RetrievalSession).
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from jarvis import gh_lookup, role_lookups

OK, NOT_FOUND, FAILED = "ok", "not_found", "failed"


@dataclass(frozen=True)
class Read:
    resolved: str                 # OK, NOT_FOUND or FAILED
    text: str = ""                # what is stored, when OK
    reason: str = ""              # why not, when not OK
    source: str = ""              # the record's path, the address, or repo and id
    # A url that is a GitHub address: the entry of its own type it names,
    # which the gather step reads as that type (one file for both).
    entry: Optional[dict] = None


def name_of(entry: dict) -> str:
    """The stored file's name in `retrieved/`, `<type>-<id>.md`: the id is
    the entry's own (a PR's number, a commit's hash, a Work item's id), a
    url's the first 12 hex of its address's SHA-256."""
    kind = entry["type"]
    if kind == "url":
        import hashlib
        key = hashlib.sha256(entry["url"].encode("utf-8")).hexdigest()[:12]
    else:
        from jarvis.evidence_types import FIELDS
        key = entry[FIELDS[kind][-1]]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(key)).strip("-.") or "entry"
    return f"{kind}-{safe}.md"


def key_of(entry: dict) -> tuple:
    """What makes two entries one reference: its type and its fields."""
    from jarvis.evidence_types import FIELDS
    return (entry["type"],) + tuple(str(entry.get(f, "")).strip() for f in FIELDS[entry["type"]])


def _md(title: str, source: str, parts: list[tuple[str, str]]) -> str:
    out = [f"# {title}", "", f"Source: {source}", ""]
    for heading, body in parts:
        out += [f"## {heading}", "", body.rstrip(), ""]
    return "\n".join(out)


def _json(value) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n```"


# ── GitHub ─────────────────────────────────────────────────────────────────

def _gh(args: list[str]) -> tuple[bool, str]:
    """One fixed gh call: (ok, its output or why it did not answer)."""
    if gh_lookup.gh_path() is None:
        return False, "gh is not installed on this Mac."
    try:
        code, out, err = asyncio.run(gh_lookup._run_gh(args, None))
    except OSError as e:
        return False, f"gh could not run: {e}."
    if code != 0:
        return False, f"GitHub answered: {gh_lookup._problem_from(err)}: {err.strip()[:300]}"
    return True, out


def _not_found(said: str) -> bool:
    return said.startswith("GitHub answered: not_found")


def _gh_read(args: list[str], what: str, source: str) -> tuple[Optional[str], Optional[Read]]:
    ok, said = _gh(args)
    if ok:
        return said, None
    return None, Read(NOT_FOUND if _not_found(said) else FAILED, reason=f"{what}: {said}",
                      source=source)


_PR_FIELDS = ("number,title,state,url,author,body,headRefName,baseRefName,createdAt,"
              "mergedAt,mergeCommit,headRefOid")
_ISSUE_FIELDS = "number,title,state,url,author,body,createdAt,closedAt,comments"
_RUN_FIELDS = ("databaseId,name,workflowName,displayTitle,status,conclusion,event,"
               "headBranch,headSha,url,createdAt,jobs")
_BRANCH_COMMITS_JQ = (".commits[] | {sha, author: .commit.author.name, "
                      "date: .commit.author.date, message: .commit.message}")


def pr_diff_args(repo: str, number: str) -> list[str]:
    # A pr's diff (entry_types: "gh pr view and gh pr diff").
    return ["pr", "diff", number, f"--repo={repo}", "--color=never"]


def run_view_args(repo: str, run_id: str) -> list[str]:
    # A ci_run's record and its jobs (entry_types: "gh run view and its
    # failed logs"; gather: "a CI run's jobs and failed tests").
    return ["run", "view", run_id, f"--repo={repo}", f"--json={_RUN_FIELDS}"]


def run_failed_logs_args(repo: str, run_id: str) -> list[str]:
    return ["run", "view", run_id, f"--repo={repo}", "--log-failed"]


def branch_commits_args(repo: str, name: str) -> list[str]:
    # A branch's commits not on main (entry_types: "branch: {repo, name}:
    # its commits not on main"), through GitHub's compare API with a fixed
    # path, GET only: each commit's hash, author, date and message.
    return ["api", "--method=GET", f"repos/{repo}/compare/main...{name}", "--paginate",
            "--jq=" + _BRANCH_COMMITS_JQ]


def pr(entry: dict) -> Read:
    args = {"repo": entry["repo"], "number": entry["number"]}
    repo, number = role_lookups._repo(args), role_lookups._number(args, "number")
    source = f"https://github.com/{repo}/pull/{number}"
    view, failed = _gh_read(["pr", "view", number, f"--repo={repo}", f"--json={_PR_FIELDS}"],
                            "gh pr view", source)
    if failed:
        return failed
    diff, failed = _gh_read(pr_diff_args(repo, number), "gh pr diff", source)
    if failed:
        return failed
    data = json.loads(view)
    return Read(OK, _md(f"Pull request {repo}#{number}: {data.get('title', '')}", source,
                        [("Description", str(data.pop("body", "") or "")),
                         ("Record", _json(data)), ("Diff", f"```diff\n{diff}\n```")]),
                source=source)


def ci_run(entry: dict) -> Read:
    args = {"repo": entry["repo"], "run_id": entry["id"]}
    repo, run_id = role_lookups._repo(args), role_lookups._number(args, "run_id")
    source = f"https://github.com/{repo}/actions/runs/{run_id}"
    view, failed = _gh_read(run_view_args(repo, run_id), "gh run view", source)
    if failed:
        return failed
    data = json.loads(view)
    parts = [("Record and jobs", _json(data))]
    if str(data.get("conclusion") or "") not in ("success", "skipped", ""):
        ok, logs = _gh(run_failed_logs_args(repo, run_id))
        parts.append(("Failed logs", f"```\n{logs}\n```" if ok else
                      f"The failed logs could not be read: {logs}"))
    return Read(OK, _md(f"CI run {repo} {run_id}: {data.get('displayTitle') or data.get('name')}",
                        source, parts), source=source)


def issue(entry: dict) -> Read:
    args = {"repo": entry["repo"], "number": entry["number"]}
    repo, number = role_lookups._repo(args), role_lookups._number(args, "number")
    source = f"https://github.com/{repo}/issues/{number}"
    view, failed = _gh_read(["issue", "view", number, f"--repo={repo}",
                             f"--json={_ISSUE_FIELDS}"], "gh issue view", source)
    if failed:
        return failed
    data = json.loads(view)
    return Read(OK, _md(f"Issue {repo}#{number}: {data.get('title', '')}", source,
                        [("Description", str(data.pop("body", "") or "")),
                         ("Record and comments", _json(data))]), source=source)


def branch(entry: dict) -> Read:
    args = {"repo": entry["repo"], "name": entry["name"]}
    repo, name = role_lookups._repo(args), role_lookups.branch_name(args)
    source = f"https://github.com/{repo}/tree/{name}"
    commits, failed = _gh_read(branch_commits_args(repo, name), "its commits not on main",
                               source)
    if failed:
        return failed
    return Read(OK, _md(f"Branch {name} of {repo}: its commits not on main", source,
                        [("Commits", f"```json\n{commits}\n```")]), source=source)


# ── a commit: git show in a checkout here ─────────────────────────────────

def _origin(folder: Path) -> str:
    try:
        done = subprocess.run(["git", "-C", str(folder), "remote", "get-url", "origin"],
                              capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _names_repo(url: str, repo: str) -> bool:
    match = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?\Z", url or "")
    return bool(match) and match.group(1).lower() == repo.lower()


def checkouts() -> list[Path]:
    """The git checkouts on this Mac a commit is looked for in: JARVIS's own
    and every active registered folder of this node."""
    from jarvis import data_paths
    found = [data_paths.PACKAGE_DIR.parent]
    try:
        from jarvis import folder_store
        found += [Path(f.location.canonical_path) for f in
                  folder_store.FolderStore().located(data_paths.node_id())]
    except Exception:  # noqa: BLE001 - the records may be closed: JARVIS's own still counts
        pass
    return list(dict.fromkeys(p for p in found if (p / ".git").exists()))


CHECKOUTS: Callable[[], list[Path]] = checkouts


def commit(entry: dict) -> Read:
    repo = role_lookups._repo({"repo": entry["repo"]})
    sha = str(entry["sha"]).strip()
    source = f"https://github.com/{repo}/commit/{sha}"
    if not role_lookups._SHA.fullmatch(sha):
        return Read(NOT_FOUND, reason=f"{sha!r} is not a commit's hash.", source=source)
    places = [p for p in CHECKOUTS() if _names_repo(_origin(p), repo)]
    for place in places:
        done = subprocess.run(["git", "-C", str(place), "show", "--no-color", "--stat",
                               "--patch", sha], capture_output=True, text=True, check=False)
        if done.returncode == 0:
            return Read(OK, _md(f"Commit {repo}@{sha}", source,
                                [("git show", f"```diff\n{done.stdout}\n```")]),
                        source=source)
    where = (", ".join(str(p) for p in places) if places
             else f"no checkout of {repo} is on this Mac")
    return Read(NOT_FOUND, reason=f"git show {sha} found no such commit ({where}).",
                source=source)


# ── a url ─────────────────────────────────────────────────────────────────

# A whole address on one line: no line separator of any kind.
_GITHUB = re.compile(r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s]+)/"
                     r"([^\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029]+?)/?")


def github_entry(url: str) -> Optional[dict]:
    """The entry of its own type a GitHub address names (a pull request, a
    CI run, a commit, an issue or a branch), else None."""
    match = _GITHUB.fullmatch(url.strip())
    if not match:
        return None
    repo, rest = f"{match.group(1)}/{match.group(2)}", match.group(3).split("#", 1)[0]
    parts = rest.split("/")
    if len(parts) >= 2 and parts[0] == "pull" and parts[1].isdigit():
        return {"type": "pr", "repo": repo, "number": parts[1]}
    if len(parts) >= 3 and parts[:2] == ["actions", "runs"] and parts[2].isdigit():
        return {"type": "ci_run", "repo": repo, "id": parts[2]}
    if len(parts) >= 2 and parts[0] == "commit":
        return {"type": "commit", "repo": repo, "sha": parts[1]}
    if len(parts) >= 2 and parts[0] == "issues" and parts[1].isdigit():
        return {"type": "issue", "repo": repo, "number": parts[1]}
    if len(parts) >= 2 and parts[0] == "tree":
        return {"type": "branch", "repo": repo, "name": urllib.parse.unquote("/".join(parts[1:]))}
    return None


def _plain_fetch(url: str) -> tuple[Optional[str], str]:
    """(the page's readable text, "") or (None, why not)."""
    import httpx
    from jarvis import content_sections
    try:
        response = httpx.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        return None, f"the plain fetch failed: {type(e).__name__}: {e}"
    if response.status_code != 200:
        return None, f"the plain fetch answered HTTP {response.status_code}"
    name = Path(urllib.parse.urlparse(str(response.url)).path).name or "page"
    kind = response.headers.get("content-type", "").split(";")[0].strip()
    if "." not in name:
        name += {"text/html": ".html", "application/json": ".json", "application/pdf": ".pdf",
                 "text/plain": ".txt"}.get(kind, ".html" if "html" in kind else ".txt")
    found = content_sections.extract(response.content, name, "text")
    if isinstance(found, content_sections.Unreadable) or not found.text.strip():
        why = getattr(found, "reason", "no readable text")
        return None, f"the plain fetch gave no readable text ({why})"
    return found.text, ""


def _page_reader(url: str) -> tuple[Optional[str], str]:
    from jarvis import browser
    try:
        page = asyncio.run(browser.read_page(url))
    except Exception as e:  # noqa: BLE001 - PageError, or no browser installed
        return None, f"the page reader failed: {e}"
    text = getattr(page, "text", "") or ""
    return (text, "") if text.strip() else (None, "the page reader found no readable text")


FETCHERS: tuple[Callable[[str], tuple[Optional[str], str]], ...] = (_plain_fetch, _page_reader)


def url(entry: dict) -> Read:
    address = entry["url"].strip()
    own = github_entry(address)
    if own is not None:
        return Read(OK, source=address, entry=own)
    if urllib.parse.urlparse(address).scheme not in ("http", "https"):
        return Read(NOT_FOUND, reason=f"{address!r} is not an http or https address.",
                    source=address)
    tried = []
    for fetch in FETCHERS:
        text, why = fetch(address)
        if text is not None:
            return Read(OK, _md(f"Address {address}", address, [("Text", text)]), source=address)
        tried.append(why)
    return Read(FAILED, reason="; ".join(tried), source=address)


# ── JARVIS's own records ──────────────────────────────────────────────────

def _evidence_line(e) -> str:
    return f"{e.kind} {e.value}"


def work_item_text(item_id: str) -> Optional[str]:
    """A Work item as its record holds it: its pieces with their notes,
    reporters, order, evidence and holds, its decisions open and answered,
    his review, and its whole history (every event, oldest first). None
    when there is no such item."""
    from jarvis import work_store
    item = work_store.get(item_id)
    if item is None:
        return None
    head = [f"State: {item.state}" + (", closed" if item.closed_at else "")
            + (", in History" if item.history else ""),
            f"Source: {item.source_kind} {item.source_path}",
            f"Area: {item.area or 'none'}",
            f"Latest update: {item.latest_update}"]
    if item.review_note:
        head.append(f"The current user's note when they said it is not done yet: {item.review_note}")
    pieces = []
    for p in item.pieces:
        who = f"{p.who.kind}:{p.who.ref}" if p.who.ref else p.who.kind
        lines = [f"### {p.key}: {p.title}", f"- state: {p.state}", f"- on it: {who}"]
        for label, value in (("worker (the reporter's words)", p.worker),
                             ("reported by", p.reporter), ("latest note", p.note)):
            if value:
                lines.append(f"- {label}: {value}")
        if p.after:
            lines.append(f"- comes after: {', '.join(p.after)}")
        if p.until:
            lines.append(f"- held by: {', '.join(p.until)}")
        lines += [f"- evidence: {_evidence_line(e)}" for e in p.evidence]
        pieces.append("\n".join(lines))
    decisions = [f"- open {d.key}: {d.question}"
                 + (f" Options: {', '.join(d.options)}." if d.options else "")
                 + (f" Recommended: {d.recommendation}." if d.recommendation else "")
                 for d in item.decisions]
    decisions += [f"- {a.where} {a.key}: {a.question} Answer: {a.answer}" for a in item.answered]
    events, after = [], 0
    while True:
        page = work_store.events(item_id, after=after, limit=500)
        if not page:
            break
        for e in page:
            events.append(f"- {e.seq} {e.kind}" + (f" ({e.reporter})" if e.reporter else "")
                          + f": {e.summary}"
                          + (f" {json.dumps(e.detail, ensure_ascii=False)}" if e.detail else ""))
        after = page[-1].seq
    return _md(f"Work item {item.id}: {item.title}", work_store.path_of(item.id),
               [("The item", "\n".join(head)), ("Pieces", "\n\n".join(pieces) or "None."),
                ("Decisions", "\n".join(decisions) or "None."),
                ("History", "\n".join(events) or "None.")])


def work_item(entry: dict) -> Read:
    from jarvis import work_store
    text = work_item_text(entry["id"])
    if text is None:
        return Read(NOT_FOUND, reason=f"There is no Work item {entry['id']}.")
    return Read(OK, text, source=work_store.path_of(entry["id"]))


def package_questions(entry) -> list:
    """The package's questions, each with what is recorded for it, or []
    when the package is no longer here."""
    from jarvis import package_inbox, work_packages
    try:
        return package_inbox.questions(work_packages.find(entry.package_id))
    except work_packages.PackageError:
        return []


def package(entry: dict) -> Read:
    from jarvis import package_inbox
    row = package_inbox.get(entry["id"])
    if row is None:
        return Read(NOT_FOUND, reason=f"There is no package {entry['id']} in the inbox.")
    asked = [{"id": q.id, "question": q.question, "options": q.options,
              "recommended": q.recommended, "answer": q.answer} for q in package_questions(row)]
    return Read(OK, _md(f"Package {row.id}: {row.title}", package_inbox.path_of(row.id),
                        [("Record", _json(dataclasses.asdict(row))),
                         ("Questions and what is recorded", _json(asked))]),
                source=package_inbox.path_of(row.id))


def message(entry: dict) -> Read:
    from jarvis import conversation_log, messages_api
    try:
        item = conversation_log.get(int(entry["id"]))
    except (TypeError, ValueError):
        item = None
    if item is None:
        return Read(NOT_FOUND, reason=f"There is no message {entry['id']}.")
    held = conversation_log._by_id(item.id)
    source = held[0].path if held else f"message:{item.id}"
    return Read(OK, _md(f"Message {item.id}", source,
                        [("Message", _json(messages_api.message_json(item)))]), source=source)


def workflow(entry: dict) -> Read:
    from jarvis import workflow_store
    snap = workflow_store.snapshot(entry["id"])
    if snap is None:
        return Read(NOT_FOUND, reason=f"There is no workflow {entry['id']}.")
    return Read(OK, _md(f"Workflow {snap.id}: {snap.name}", workflow_store.path_of(snap.id),
                        [("Record", _json(dataclasses.asdict(snap)))]),
                source=workflow_store.path_of(snap.id))


def run(entry: dict) -> Read:
    from jarvis import run_store
    found = run_store.get_run(entry["id"])
    if found is None:
        return Read(NOT_FOUND, reason=f"There is no run {entry['id']}.")
    events, after = [], 0
    while True:
        page = run_store.get_events(entry["id"], after_seq=after, limit=500)
        if not page:
            break
        events += page
        after = page[-1]["seq"]
    source = run_store._run_path(entry["id"])
    return Read(OK, _md(f"Run {entry['id']}", source,
                        [("Record", _json(found)), ("Output", _json(events))]), source=source)


READERS: dict[str, Callable[[dict], Read]] = {
    "url": url, "work_item": work_item, "package": package, "message": message,
    "workflow": workflow, "run": run, "pr": pr, "ci_run": ci_run, "commit": commit,
    "issue": issue, "branch": branch}


def read(entry: dict) -> Read:
    """The entry read by its type's reader; a reader that raises is failed,
    saying what it raised."""
    try:
        return READERS[entry["type"]](entry)
    except role_lookups.Refused as e:
        return Read(NOT_FOUND, reason=f"{entry['type']}: {e}.")
    except Exception as e:  # noqa: BLE001 - the manifest says why, the run goes on
        return Read(FAILED, reason=f"{entry['type']}: {type(e).__name__}: {e}")
