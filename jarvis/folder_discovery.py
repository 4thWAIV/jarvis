"""Where the user's sessions have worked, from the providers' own structured
records, as observations for the folder catalog (folder_store.observe).

Read-only, and only metadata:

  - Claude Code's roster (`session_watch.read_roster`): each live session's
    cwd and session ID, under the same config roots history_read and the
    session watcher use (`history_read.claude_roots`);
  - Claude Code's history: the `cwd` and `sessionId` each transcript in
    `<root>/projects/<folder>/` records in its first records. The folder's
    own name is the CLI's lossy encoding of a cwd and is never decoded: a
    transcript with no recorded cwd stays unresolved. The history folder is
    observed as a place of its own, beside the cwd, with the memory notes
    and subagent transcripts it holds;
  - Codex's thread database (`codex_sessions.threads`) in the user's Codex
    home (never the brain's own): each thread's cwd and rollout file;
  - the session watcher's current snapshot, for which sessions are live.

Bounded: one pass lists at most DISCOVERY_LIST_MAX history files within
DISCOVERY_SECONDS and reads the head of at most DISCOVERY_FILES_PER_PASS of
them, newest first; the rest wait for the next pass, and each source's
progress (listed, read, pending, complete) is recorded. A transcript caught
mid-write, or one that does not parse, is read again when it grows; one
whose cwd was found is never read again. Nothing already observed is ever
dropped. No session is renamed, activated, launched, steered or stopped.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from jarvis import codex_sessions, history_read, session_watch
from jarvis.folder_contract import (DISCOVERY_FILES_PER_PASS, DISCOVERY_HEAD_BYTES,
                                    DISCOVERY_LIST_MAX, DISCOVERY_SECONDS, Role)
from jarvis.folder_store import FolderStore, Observation

log = logging.getLogger("jarvis.folders")

CLAUDE = "anthropic"
CODEX = "openai"
DONE = -1                       # folder_history_files: its cwd was found
SUBAGENTS_COUNT_MAX = 300       # session_watch.MAX_AGENT_FILES


@dataclass
class SourceReport:
    source: str
    listed: int = 0
    read: int = 0
    pending: int = 0
    complete: bool = True
    error: Optional[str] = None
    cursor: Optional[str] = None


@dataclass
class Report:
    sources: list[SourceReport] = field(default_factory=list)
    changed: int = 0

    @property
    def complete(self) -> bool:
        return all(s.complete and not s.error for s in self.sources)


def path_key(raw: str) -> Optional[str]:
    """The catalog's key for a path a record named: its real path when it
    is there, else the path as recorded, normalised. Never a guess."""
    if not raw or "\x00" in raw or not os.path.isabs(raw):
        return None
    try:
        return os.path.realpath(raw) if os.path.exists(raw) else os.path.normpath(raw)
    except (OSError, ValueError):
        return None


def cwd_role(key: str) -> str:
    try:
        return (Role.SOURCE if os.path.isdir(os.path.join(key, ".git"))
                or os.path.isfile(os.path.join(key, ".git")) else Role.DATA).value
    except OSError:
        return Role.DATA.value


def _is_brain(key: str) -> bool:
    """JARVIS's own brain home: never one of the user's workspaces."""
    return key in (os.path.realpath(session_watch.brain_cwd()),
                   os.path.normpath(session_watch.brain_cwd()))


def _observation(provider: str, profile_root: Path, session_id: str, cwd: str, *,
                 history_dir: str = "", history_file: str = "", evidence: str,
                 live: bool = False, associated: Optional[dict] = None) -> Optional[Observation]:
    key = path_key(cwd)
    if key is None or _is_brain(key):
        return None
    return Observation(provider, str(profile_root), session_id, cwd, key,
                       history_dir=history_dir, history_file=history_file, evidence=evidence,
                       live=live, associated=associated or {}, cwd_role=cwd_role(key))


# ── Claude Code ──────────────────────────────────────────────────────────

def _history_dir(root: Path, cwd: str, session_id: str) -> tuple[str, str]:
    """(history folder, transcript) for a live session, by the CLI's own
    encoding of its cwd (forward, as the session watcher finds transcripts),
    when that transcript is there."""
    path = session_watch.transcript_path(root, cwd, session_id)
    if path.is_file():
        return os.path.realpath(path.parent), str(path)
    return "", ""


def claude_live(roots: list[Path]) -> list[Observation]:
    out = []
    for entry in session_watch.read_roster(roots):
        hist, transcript = _history_dir(entry.root, entry.cwd, entry.session_id)
        o = _observation(CLAUDE, entry.root, entry.session_id, entry.cwd, history_dir=hist,
                         history_file=transcript, evidence="roster", live=True)
        if o:
            out.append(o)
    return out


def _head_record(path: Path) -> Optional[dict]:
    """The first record in a transcript's head that names its cwd."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(DISCOVERY_HEAD_BYTES)
    except OSError:
        return None
    lines = head.split(b"\n")
    if len(head) == DISCOVERY_HEAD_BYTES:
        lines = lines[:-1]              # the last may be cut off
    for raw in lines:
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("cwd"), str) and obj["cwd"]:
            return obj
    return None


def _associated(hist: Path, session_id: str) -> dict:
    """The memory notes and subagent transcripts this history keeps for a
    session, where they are."""
    out: dict = {}
    if (hist / "memory").is_dir():
        out["memory"] = str(hist / "memory")
    subagents = hist / session_id / "subagents"
    if subagents.is_dir():
        n = 0
        try:
            with os.scandir(subagents) as it:
                for e in it:
                    if e.name.endswith(".jsonl"):
                        n += 1
                        if n >= SUBAGENTS_COUNT_MAX:
                            break
        except OSError:
            pass
        out["subagents"] = n
        out["subagents_dir"] = str(subagents)
    return out


def _list_transcripts(projects: Path, deadline: float, cap: int) -> tuple[list[tuple[float, int, Path]], bool]:
    """(mtime, size, path) of each top-level transcript under `projects`,
    and whether the listing finished."""
    found: list[tuple[float, int, Path]] = []
    try:
        folders = list(os.scandir(projects))
    except OSError:
        return found, True
    for folder in folders:
        if time.monotonic() > deadline or len(found) >= cap:
            return found, False
        try:
            if not folder.is_dir(follow_symlinks=False):
                continue
            with os.scandir(folder.path) as it:
                for e in it:
                    if e.name.endswith(".jsonl") and e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        found.append((st.st_mtime, st.st_size, Path(e.path)))
                        if len(found) >= cap:
                            return found, False
        except OSError:
            continue
    return found, True


def claude_history(store: FolderStore, root: Path, deadline: float,
                   budget: int) -> tuple[list[Observation], SourceReport]:
    report = SourceReport(f"claude:{root}")
    projects = root / "projects"
    if not projects.is_dir():
        return [], report
    listed, report.complete = _list_transcripts(projects, deadline, DISCOVERY_LIST_MAX)
    report.listed = len(listed)
    known = store.history_files_read([str(p) for _m, _s, p in listed])
    todo = sorted(((m, s, p) for m, s, p in listed
                   if known.get(str(p)) not in (DONE, s)), reverse=True)
    out: list[Observation] = []
    marks: list[tuple[str, int]] = []
    for _mtime, size, path in todo[:budget]:
        if time.monotonic() > deadline:
            report.complete = False
            break
        report.read += 1
        record = _head_record(path)
        if record is None:
            marks.append((str(path), size))     # mid-write or none: again when it grows
            continue
        hist = Path(os.path.realpath(path.parent))
        session_id = str(record.get("sessionId") or path.stem)
        o = _observation(CLAUDE, root, session_id, record["cwd"], history_dir=str(hist),
                         history_file=str(path), evidence="transcript",
                         associated=_associated(hist, session_id))
        if o:
            out.append(o)
        marks.append((str(path), DONE))
    store.mark_history_files(marks)
    report.pending = max(0, len(todo) - report.read)
    report.complete = report.complete and report.pending == 0
    return out, report


# ── Codex ────────────────────────────────────────────────────────────────

def codex_history(home: Path, cursor: Optional[str]) -> tuple[list[Observation], SourceReport]:
    """Codex threads updated since `cursor` (the last pass's newest
    `updated_at_ms`)."""
    report = SourceReport(f"codex:{home}")
    try:
        threads = codex_sessions.threads(home)
    except Exception as e:                      # a locked or older database
        report.error, report.complete = type(e).__name__, False
        return [], report
    since = int(cursor) if cursor and cursor.isdigit() else 0
    sessions_dir = os.path.realpath(home / "sessions")
    out: list[Observation] = []
    newest = since
    for t in threads.values():
        report.listed += 1
        updated = int(t.get("updated_at_ms") or 0)
        if updated <= since:
            continue
        newest = max(newest, updated)
        report.read += 1
        rollout = str(t.get("rollout_path") or "")
        in_sessions = bool(rollout) and os.path.realpath(rollout).startswith(sessions_dir + os.sep)
        o = _observation(CODEX, home, str(t["id"]), str(t.get("cwd") or ""),
                         history_dir=sessions_dir if in_sessions else "",
                         history_file=rollout if in_sessions else "",
                         evidence="codex_thread",
                         associated={"subagent": True} if t.get("thread_source") == "subagent"
                         else {})
        if o:
            out.append(o)
    report.cursor = str(newest)
    return out, report


# ── the live sessions ────────────────────────────────────────────────────

def live(sessions: Iterable) -> list[Observation]:
    """The session watcher's Codex sessions (session_watch.SessionState), as
    live observations of their cwds. Claude Code's live sessions are read
    from its roster (`claude_live`), which says which config root each is
    under."""
    out = []
    for s in sessions:
        if s.provider != CODEX:
            continue
        o = _observation(CODEX, codex_sessions.codex_home(), s.session_id, s.cwd,
                         evidence="live", live=True)
        if o:
            out.append(o)
    return out


# ── one pass ─────────────────────────────────────────────────────────────

def discover(store: FolderStore, node_id: str, live_sessions: Iterable = (),
             now: Optional[float] = None) -> Report:
    """One bounded pass over every source, recorded. Never raises for a
    source that cannot be read: its report says so."""
    report = Report()
    deadline = time.monotonic() + DISCOVERY_SECONDS
    cursors = {p.source: p.cursor for p in store.progress()}
    seen: list[Observation] = []
    roots = history_read.claude_roots()

    live_report = SourceReport("live")
    try:
        found = claude_live(roots) + live(live_sessions)
        live_report.listed = live_report.read = len(found)
        seen += found
    except Exception as e:
        log.warning("folder discovery: live sessions: %s", e)
        live_report.error, live_report.complete = type(e).__name__, False
    report.sources.append(live_report)

    budget = DISCOVERY_FILES_PER_PASS
    for root in roots:
        try:
            found, r = claude_history(store, root, deadline, budget)
        except Exception as e:
            log.warning("folder discovery: %s: %s", root, e)
            found, r = [], SourceReport(f"claude:{root}", complete=False, error=type(e).__name__)
        budget = max(0, budget - r.read)
        seen += found
        report.sources.append(r)

    home = codex_sessions.codex_home()
    source = f"codex:{home}"
    if (home / "state_5.sqlite").exists():
        found, r = codex_history(home, cursors.get(source))
        seen += found
        report.sources.append(r)

    report.changed = store.observe(node_id, seen, now=now)
    for r in report.sources:
        store.record_progress(r.source, listed=r.listed, read=r.read, pending=r.pending,
                              complete=r.complete, error=r.error, cursor=r.cursor, now=now)
    return report
