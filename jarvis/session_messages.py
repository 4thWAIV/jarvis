"""Messages a session sends the user: the half of two-way communication a session
starts.

A session drops one Markdown file per message into
`data_paths.session_messages_dir()/<project>/`, named as it likes. The first
line is `# <subject>`; the rest is the message. The talk-to-jarvis skill
(session_skill.py) tells sessions how, as it does for the needs board. This module only
reads the folder: the server records each message, deletes its file (which is
the session's sign it was picked up) and says it came in. Nothing here speaks.
"""

from __future__ import annotations

import logging
import stat
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jarvis.session_messages")

# A file changed this recently may still be being written: left for the next
# look rather than taken half-written.
SETTLE_SEC = 2.0

# A message is somebody else's text going into the brain's context. This
# bounds a file far longer than any message.
MAX_FILE_CHARS = 20000

# How many files one look takes. The rest wait for the next.
MAX_PER_LOOK = 20


@dataclass(frozen=True)
class Dropped:
    """One message file, read and not yet recorded."""
    path: Path
    project: str        # the folder it was dropped in
    subject: str
    body: str


def parse(text: str) -> tuple[str, str]:
    """(subject, body). The subject is the first line without its `#`s; a
    file with no heading line has no subject and the whole text is the body."""
    text = text.strip()
    first, _, rest = text.partition("\n")
    if first.startswith("#"):
        return first.lstrip("#").strip(), rest.strip()
    return "", text


def collect(folder: Path, now: float) -> list[Dropped]:
    """Every settled message file under `folder`, oldest first. Never raises:
    an unreadable file is skipped and left for the next look, and a missing
    folder holds nothing. Symlinks and anything not a regular file are never
    read, so a link cannot pull a file from elsewhere into the brain."""
    found: list[tuple[float, Path, str]] = []
    try:
        projects = [d for d in folder.iterdir()
                    if d.is_dir() and not d.is_symlink()]
    except OSError:
        return []
    for project in projects:
        try:
            paths = list(project.glob("*.md"))
        except OSError:
            continue
        for path in paths:
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if now - info.st_mtime < SETTLE_SEC:
                continue
            found.append((info.st_mtime, path, project.name))
    found.sort()
    out = []
    for _mtime, path, project in found[:MAX_PER_LOOK]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning(f"session messages: {path.name} unreadable ({e})")
            continue
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS].rstrip() + "\n… (the rest of this message was cut)"
        subject, body = parse(text)
        out.append(Dropped(path=path, project=project, subject=subject, body=body))
    return out
