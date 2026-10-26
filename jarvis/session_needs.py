"""The session needs board: what each session needs from the user.

One Markdown file per project in `data_paths.needs_dir()`, written by the
sessions themselves (the talk-to-jarvis skill, session_skill.py, tells them how) and
read here. A file holds three headings — Decisions, Questions, Blockers — and
one `- ` line per item. Pure reading and change tracking; no I/O beyond the
folder, and nothing here speaks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jarvis.session_needs")

# A file is somebody else's text going into the brain's context. The format
# asks for at most ten one-line items; this bounds a file that ignores it.
MAX_FILE_CHARS = 4000


@dataclass
class Board:
    project: str        # the file's stem: the project the session works in
    text: str
    mtime: float

    @property
    def items(self) -> int:
        """How many things it is waiting on: one per `- ` line."""
        return sum(1 for line in self.text.splitlines()
                   if line.lstrip().startswith("- "))


def read_boards(folder: Path) -> list[Board]:
    """Every board in the folder, by project name. Never raises: an
    unreadable file is skipped, and a missing folder is an empty board."""
    try:
        paths = sorted(folder.glob("*.md"))
    except OSError:
        return []
    boards = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except OSError as e:
            log.warning(f"session needs: {path.name} unreadable ({e})")
            continue
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS].rstrip() + "\n… (the rest of this file was cut)"
        boards.append(Board(project=path.stem, text=text.strip(), mtime=mtime))
    return boards


class ChangeTracker:
    """Which boards are new or rewritten since the last look.

    The first look only records: JARVIS must not greet the user by reading
    out every board that was already there when he started.
    """

    def __init__(self) -> None:
        self._seen: dict[str, float] | None = None

    def changed(self, boards: list[Board]) -> list[Board]:
        current = {b.project: b.mtime for b in boards}
        if self._seen is None:
            self._seen = current
            return []
        out = [b for b in boards
               if b.items and self._seen.get(b.project) != b.mtime]
        self._seen = current
        return out
