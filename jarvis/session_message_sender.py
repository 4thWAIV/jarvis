"""Who sent a message a session dropped in `data/messages/<project>/`.

A file in a project folder carries no session id of its own, and a project
often has several agents working in it, so the folder alone never says who
wrote a file. The evidence, most reliable first:

1. `transcript`: the session whose own transcript shows it writing that file
   (its path in a tool call's input: a Write, or a command that writes it).
   A session cannot write into another session's transcript, so this is the
   one fact no session can fake about another.
2. `from_line`: the file's `From: <session id>` line (session_messages.parse),
   which the talk-to-jarvis skill tells sessions to write and make_package
   writes from its --session. It is a claim, so it counts only for a session
   the agent directory knows, working in that same project, and only when no
   transcript shows the writer.
3. `project`: neither is known. The sender is "an agent in <project>"; never
   several agents listed as if they all sent it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from jarvis import session_watch

TRANSCRIPT, FROM_LINE, PROJECT = "transcript", "from_line", "project"
# How much of a transcript's end is searched for the file's path: a message
# is picked up seconds after it is written.
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Sender:
    session_id: Optional[str]
    name: str                 # the agent's one name, or "an agent in <project>"
    evidence: str             # transcript, from_line or project


def unknown(project: str) -> Sender:
    return Sender(None, f"an agent in {project}", PROJECT)


def _tail(path: Path, nbytes: int) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - nbytes))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def wrote(transcript: Path, file_path: Path) -> bool:
    """Whether this transcript's end shows its session writing `file_path`:
    the file's full path in its own lines (a tool call's input)."""
    text = _tail(transcript, TRANSCRIPT_TAIL_BYTES)
    if not text:
        return False
    try:
        real = str(file_path.resolve())
    except OSError:
        real = str(file_path)
    return str(file_path) in text or real in text


def sender_of(project: str, file_path: Path, claimed: Optional[str], sessions: Iterable,
              roots: Iterable[Path], names: dict) -> Sender:
    """Who sent the file `file_path` dropped in `project`'s folder.

    `sessions` are the watcher's sessions (each with session_id, cwd,
    project); `roots` the config roots their transcripts are under; `names`
    the agent directory's one name per session id."""
    in_project = [s for s in sessions if str(getattr(s, "project", "")).lower() == project.lower()]
    for s in in_project:
        for root in roots:
            path = session_watch.transcript_path(root, s.cwd, s.session_id)
            if wrote(path, file_path):
                return Sender(s.session_id, names.get(s.session_id) or project, TRANSCRIPT)
    if claimed and claimed in names and any(s.session_id == claimed for s in in_project):
        return Sender(claimed, names[claimed], FROM_LINE)
    return unknown(project)
