"""Which reader may read a folder, and what that reader shows inside it.

One decision, made from where a folder is and never from what a caller asks
for or the role it is labelled with:

  - a Claude Code history folder, `<config root>/projects/<encoded cwd>` (the
    config roots are the ones history_read and the session watcher already
    read, `history_read.claude_roots`), is read by CLAUDE_HISTORY: its
    transcripts through their structured reader and its memory notes as
    text, nothing else. The config root itself, and anything else in it
    (settings, credentials, plugins), is refused;
  - the user's Codex sessions folder (`codex_sessions.codex_home()/sessions`
    or `archived_sessions`) is read by CODEX_HISTORY: rollout transcripts
    only. The rest of that Codex home, and the brain's own Codex home, is
    refused;
  - the brain's own journal, memory and project notes
    (`history_read.note_roots`) are read by NOTES: Markdown only;
  - anything else of JARVIS's own data (`repo_read.private_reason`: the tool
    token, mcp.json, connections.json, jarvis.db) is refused;
  - anything else is an ordinary folder, read by FILES.

A folder the user names may itself be a hidden folder (`./.agents/in-progress`):
its own path is checked with `repo_read.sensitive_reason(named_dotfiles=True)`,
so `.ssh`, `.aws`, `.env`, keys and credentials are refused however they are
named, and a dot folder JARVIS does not know is his to open because he named
it. Inside any folder the full rule applies to every entry, path by path,
relative to the folder: unknown dotfiles, credentials and keys stay closed,
and so do JARVIS's own data and the provider homes. A parent folder never
opens what its own rules, or a narrower folder inside it, would close.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jarvis import codex_sessions, history_read, repo_read
from jarvis.folder_contract import FolderError, Reader, Role

CODEX_SESSION_DIRS = ("sessions", "archived_sessions")
TRANSCRIPT_SUFFIX = ".jsonl"
NOTE_SUFFIX = ".md"
CODEX_PREFIX = "rollout-"


@dataclass(frozen=True)
class Policy:
    """What one folder's reader is: its kind, the subtree it may read (the
    whole folder, '' , in this increment), and the provider a history
    reader reads."""
    reader: Reader
    permitted_subtree: str = ""
    provider: Optional[str] = None

    def describe(self) -> str:
        return {
            Reader.FILES: "Files and folders, except hidden, credential and key files",
            Reader.NOTES: "JARVIS's notes: Markdown files only",
            Reader.CLAUDE_HISTORY: "Claude Code transcripts and memory notes only",
            Reader.CODEX_HISTORY: "Codex session transcripts only",
        }[self.reader]


def _real(path) -> Path:
    return Path(os.path.realpath(str(path)))


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def claude_roots() -> list[Path]:
    """Every Claude config root, resolved: the one list history_read and
    the session watcher read."""
    return [_real(r) for r in history_read.claude_roots()]


def codex_roots() -> list[Path]:
    """The user's Codex home, resolved. The brain's own Codex home is in
    JARVIS's data, which `repo_read.private_reason` closes."""
    return [_real(codex_sessions.codex_home())]


def provider_home(path: Path) -> Optional[Path]:
    """The provider home `path` is in, if it is in one."""
    for root in claude_roots() + codex_roots():
        if _within(path, root):
            return root
    return None


def _home_relative(path: Path) -> Path:
    home = _real(Path.home())
    if _within(path, home):
        return path.relative_to(home) if path != home else Path(".")
    return Path(*path.parts[1:]) if path.is_absolute() else path


def policy_for(canonical: Path) -> Policy:
    """The reader for a folder at this canonical path, or FolderError
    `forbidden`."""
    for root in claude_roots():
        if _within(canonical, root):
            projects = root / "projects"
            if canonical != projects and _within(canonical, projects):
                if repo_read.sensitive_reason(canonical.relative_to(projects)):
                    raise FolderError("forbidden")
                return Policy(Reader.CLAUDE_HISTORY, provider="anthropic")
            raise FolderError("forbidden")
    for root in codex_roots():
        if _within(canonical, root):
            if any(_within(canonical, root / d) for d in CODEX_SESSION_DIRS):
                return Policy(Reader.CODEX_HISTORY, provider="openai")
            raise FolderError("forbidden")
    for note_root in history_read.note_roots().values():
        if _within(canonical, _real(note_root)):
            if repo_read.sensitive_reason(canonical.relative_to(_real(note_root))):
                raise FolderError("forbidden")
            return Policy(Reader.NOTES)
    if repo_read.private_reason(canonical):
        raise FolderError("forbidden")
    if repo_read.sensitive_reason(_home_relative(canonical), named_dotfiles=True):
        raise FolderError("forbidden")
    return Policy(Reader.FILES)


def proposed_role(canonical: Path, policy: Policy) -> Role:
    """The role a new folder starts with: history for a history reader,
    source for a version-controlled folder, data otherwise. A label only."""
    if policy.reader in (Reader.CLAUDE_HISTORY, Reader.CODEX_HISTORY):
        return Role.HISTORY
    try:
        if (canonical / ".git").exists():
            return Role.SOURCE
    except OSError:
        pass
    return Role.DATA


def entry_visible(policy: Policy, root: Path, relative: Path, is_dir: bool) -> bool:
    """Whether the reader shows this entry of the folder at `root`
    (canonical), named by its path relative to it. The one rule listing,
    reading and the brain's tools all apply, on every request."""
    if repo_read.sensitive_reason(relative):
        return False
    absolute = root / relative
    if repo_read.private_reason(absolute) and policy.reader != Reader.NOTES:
        return False
    name = relative.name.lower()
    if policy.reader == Reader.FILES:
        # A provider home inside an ordinary folder (a home folder holds
        # .claude) is read by its own reader or not at all.
        return provider_home(absolute) is None
    if is_dir:
        return True
    if policy.reader == Reader.NOTES:
        return name.endswith(NOTE_SUFFIX)
    if policy.reader == Reader.CLAUDE_HISTORY:
        return name.endswith(TRANSCRIPT_SUFFIX) or name.endswith(NOTE_SUFFIX)
    if policy.reader == Reader.CODEX_HISTORY:
        return name.startswith(CODEX_PREFIX) and name.endswith(TRANSCRIPT_SUFFIX)
    return False


def is_transcript(policy: Policy, relative: Path) -> bool:
    """Read through the provider's structured transcript reader."""
    return (policy.reader in (Reader.CLAUDE_HISTORY, Reader.CODEX_HISTORY)
            and relative.name.lower().endswith(TRANSCRIPT_SUFFIX))
