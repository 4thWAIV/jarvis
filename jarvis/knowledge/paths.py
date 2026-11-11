"""An item's path: the one key to all of its streams (UDS, one path).

A path is absolute and slash-separated: `/work/w-1.work`. Its parent is the
path up to its last slash (`/` for a top-level item), its name the last
segment, and its type the suffix of that name after the last dot (`work`);
a name with no suffix is a folder's, and its type is `FOLDER`. Folders are
implicit: an item needs no item at its parent.

One spelling per path, so two spellings never name two items: Unicode NFC,
no empty, `.` or `..` segment, no trailing slash, no control character,
at most `PATH_MAX_BYTES` in UTF-8 and `SEGMENT_MAX_BYTES` a segment.
"""
from __future__ import annotations

import unicodedata
from typing import Optional

PATH_MAX_BYTES = 1024
SEGMENT_MAX_BYTES = 255
FOLDER = "folder"
TYPE_MAX = 32


class PathError(ValueError):
    """Not an item path; the words say why."""


def problem(path) -> Optional[str]:
    """Why `path` is not an item path, or None when it is one."""
    if not isinstance(path, str):
        return "a path is text"
    if not path.startswith("/"):
        return "a path starts with /"
    if path == "/":
        return "/ is the root, not an item"
    if path.endswith("/"):
        return "a path does not end with /"
    if len(path.encode("utf-8")) > PATH_MAX_BYTES:
        return f"a path is at most {PATH_MAX_BYTES} bytes"
    if unicodedata.normalize("NFC", path) != path:
        return "a path is written in Unicode NFC"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        return "a path holds no control character"
    for segment in path[1:].split("/"):
        if segment in ("", ".", ".."):
            return "a path has no empty, . or .. segment"
        if len(segment.encode("utf-8")) > SEGMENT_MAX_BYTES:
            return f"a path segment is at most {SEGMENT_MAX_BYTES} bytes"
    return None


def check(path) -> str:
    found = problem(path)
    if found is not None:
        raise PathError(f"{found}: {path!r}"[:300])
    return path


def is_path(value) -> bool:
    return problem(value) is None


def parent(path: str) -> str:
    head = path.rsplit("/", 1)[0]
    return head or "/"


def name(path: str) -> str:
    return path.rsplit("/", 1)[1]


def type_of(path: str) -> str:
    """The type suffix of the path's name, lowercased, or `FOLDER`. A name
    that starts with its only dot (`.env`) has no suffix."""
    leaf = name(path)
    stem, dot, suffix = leaf.rpartition(".")
    if not dot or not stem or not suffix or len(suffix) > TYPE_MAX:
        return FOLDER
    return suffix.lower()


def ancestors(path: str) -> list[str]:
    """Every folder above the item, nearest the root first, `/` left out."""
    segments = path[1:].split("/")[:-1]
    return ["/" + "/".join(segments[:i]) for i in range(1, len(segments) + 1)]


def depth(path: str) -> int:
    return path.count("/")


def under_prefix(under: str) -> str:
    """The key prefix every item below the folder `under` starts with."""
    if under in ("", "/"):
        return "/"
    check(under)
    return under + "/"


def is_under(path: str, under: str) -> bool:
    return path.startswith(under_prefix(under))
