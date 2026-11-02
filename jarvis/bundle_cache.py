"""Derived output of bundle readers (text pages, renders, listings), kept so
a second read is cheap. Never an original: received bytes stay where
messaging_files verified them.

A derived file is keyed by everything that decides its content: the owner
(server and GitHub id, so one owner's derived text is never served to
another), the attachment's SHA-256 (a changed attachment is a new revision,
never an overwrite), the member, the reader and its version, the options
and what kind of output it is. Files live under
`<data>/bundle-cache/<server>/<owner>/`, owner-only, written under a partial
name and renamed into place, trimmed oldest first past CACHE_MAX_BYTES.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

CACHE_MAX_BYTES = 512 * 1024 * 1024

DerivedKind = Literal["describe", "listing", "text", "render", "preview"]


@dataclass(frozen=True)
class CacheKey:
    server_id: str
    owner_github_id: int
    attachment_sha256: str
    member_id: str
    reader: str
    reader_version: int
    kind: DerivedKind
    options: str = ""                 # e.g. RenderOptions.digest() plus the page


class DerivedCache:
    def __init__(self, root: Optional[Path] = None, max_bytes: int = CACHE_MAX_BYTES):
        self._root, self.max_bytes = root, max_bytes

    def path_for(self, key: CacheKey) -> Path:
        raise NotImplementedError("readers lane")

    def get(self, key: CacheKey) -> Optional[bytes]:
        raise NotImplementedError("readers lane")

    def put(self, key: CacheKey, data: bytes) -> None:
        raise NotImplementedError("readers lane")

    def scratch(self, server_id: str, owner_github_id: int) -> Path:
        """The owner's private folder for MemberSource.materialize copies."""
        raise NotImplementedError("readers lane")

    def forget_owner(self, server_id: str, owner_github_id: int) -> int:
        raise NotImplementedError("readers lane")

    def trim(self) -> int:
        raise NotImplementedError("readers lane")
