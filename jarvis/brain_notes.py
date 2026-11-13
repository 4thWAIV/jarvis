"""The brain's notes as the engine holds them (UDS round 3).

The brain's memory, journal, project notes, topics and the knowledge
settings note stay Markdown files he can read and edit (jarvis_memory,
knowledge_links write them). The content child indexes them in place as
items under `BRAIN` (knowledge_content's root over `data_paths.brain_home()`,
`BRAIN_NOTES` only): `/jarvis/brain/<folder>/<name>.md`, with their text,
sections and vectors, the note's reference (`jref:...:note:<folder>/<name>`)
as the content's `ref`.

What reads notes as knowledge (the context pack's tier 0, its topics) reads
them here: a note's words come from its item when the item is the file as
it is now (its size, modification time and inode), and from the file only
when the engine has not indexed that version yet (just written, or the
engine's ingest switched off). The writers (jarvis_memory, knowledge_links)
keep reading the files they replace: a write must see the bytes it
replaces, and an item can lag its file by the watcher's latency.
"""
from __future__ import annotations

import os
from typing import Optional

from jarvis import data_paths
from jarvis.knowledge.oscompat import O_BINARY, O_NOFOLLOW

BRAIN = "/jarvis/brain"
BRAIN_NOTES = ("memory", "journal", "projects", "topics", "knowledge.md")
READ_MAX = 512 << 10
OWNER = "tim"


def item_path(rel: str) -> str:
    """The item a note is indexed at: `rel` is its path under the brain's home."""
    return f"{BRAIN}/{rel}"


def _stat(rel: str) -> Optional[os.stat_result]:
    try:
        info = os.lstat(data_paths.brain_home() / rel)
    except OSError:
        return None
    return info if info.st_mode & 0o170000 == 0o100000 else None


def _file_text(rel: str) -> Optional[str]:
    try:
        fd = os.open(str(data_paths.brain_home() / rel), os.O_RDONLY | O_NOFOLLOW | O_BINARY)
    except OSError:
        return None
    try:
        if not (os.fstat(fd).st_mode & 0o170000 == 0o100000):
            return None
        return os.read(fd, READ_MAX).decode("utf-8", "replace")
    finally:
        os.close(fd)


def indexed(rel: str):
    """The note's item when it is the file as it is now, else None."""
    info = _stat(rel)
    if info is None:
        return None
    try:
        from jarvis import records
        from jarvis.knowledge.uds import Caller, Stream
        item = records.uds().get(Caller(OWNER), item_path(rel), {Stream.DATA, Stream.CONTENT, Stream.SECTIONS})
    except Exception:  # noqa: BLE001 - the engine not answering: the file says it
        return None
    data = (item.data or {}) if item is not None else {}
    if item is None or data.get("state") != "indexed" or item.content is None:
        return None
    try:
        same = (int(data["size"]), int(data["mtime_ns"]), int(data["inode"])) == \
            (info.st_size, info.st_mtime_ns, info.st_ino)
    except (KeyError, TypeError, ValueError):
        return None
    return item if same else None


def read(rel: str) -> Optional[str]:
    """A note's words (`rel` under the brain's home, e.g. `projects/jarvis.md`):
    from its item when that is current, else from the file; None when there
    is no such note (or it is a link)."""
    if _stat(rel) is None:
        return None
    item = indexed(rel)
    if item is not None:
        return str(item.content.get("text") or "")
    return _file_text(rel)


def names(folder: str) -> list[str]:
    """The notes in one of the brain's folders (`topics`, `memory`...), by
    file name, never through a link."""
    base = data_paths.brain_home() / folder
    if not base.is_dir() or base.is_symlink():
        return []
    return sorted(p.name for p in base.glob("*.md") if not p.is_symlink())
