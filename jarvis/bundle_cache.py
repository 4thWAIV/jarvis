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

The owner's `scratch` folder beside them holds the private copies a reader
needs on disk (MemberSource.materialize); each is removed when its reader
is done, and `trim` removes any a crash left behind.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional

from jarvis import data_paths

CACHE_MAX_BYTES = 512 * 1024 * 1024

DerivedKind = Literal["describe", "listing", "text", "render", "preview", "digest"]


@dataclass(frozen=True)
class LocalOwner:
    installation_id: str


@dataclass(frozen=True)
class TeamOwner:
    server_id: str
    owner_github_id: int


OwnerScope = LocalOwner | TeamOwner


@dataclass(frozen=True)
class ScopedCacheKey:
    owner: OwnerScope
    attachment_sha256: str
    member_id: str
    reader: str
    reader_version: int
    kind: str
    options: str = ""

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()

PARTIAL = ".partial-"
SCRATCH = "scratch"
# A scratch copy or partial file older than this was left by a crash.
LEFTOVER_AFTER_SEC = 3600
_SCOPE_LEASES: dict[str, dict[str, tuple[float, set[Path]]]] = {}


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

    def digest(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _segment(value: str) -> str:
    """A folder name for a server id: the id's own hash, so nothing a server
    calls itself can make a path."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _private(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


class DerivedCache:
    def __init__(self, root: Optional[Path] = None, max_bytes: int = CACHE_MAX_BYTES):
        self._root, self.max_bytes = root, max_bytes
        self._used: Optional[int] = None    # bytes held, counted once, then kept up to date


    def lease(self, lease_id: str, keys, seconds: float = 300) -> None:
        leases = _SCOPE_LEASES.setdefault(str(self.root().resolve()), {})
        leases[lease_id] = (time.monotonic() + seconds, {self.path_for(key) for key in keys})

    def release(self, lease_id: str) -> None:
        _SCOPE_LEASES.setdefault(str(self.root().resolve()), {}).pop(lease_id, None)

    def _leased(self) -> set[Path]:
        now = time.monotonic()
        root = str(self.root().resolve())
        leases = {k: v for k, v in _SCOPE_LEASES.setdefault(root, {}).items() if v[0] > now}
        _SCOPE_LEASES[root] = leases
        return set().union(*(v[1] for v in leases.values())) if leases else set()

    def root(self) -> Path:
        return self._root or data_paths.bundle_cache_dir()

    def _owner_dir(self, server_id: str, owner_github_id: int) -> Path:
        return self.root() / _segment(server_id) / str(int(owner_github_id))

    def scope_dir(self, owner: OwnerScope) -> Path:
        if isinstance(owner, LocalOwner):
            return self.root() / "local" / _segment(owner.installation_id)
        return self._owner_dir(owner.server_id, owner.owner_github_id)

    def scratch_scope(self, owner: OwnerScope) -> Path:
        return _private(self.scope_dir(owner) / SCRATCH)

    def path_for(self, key: CacheKey | ScopedCacheKey) -> Path:
        sha = key.attachment_sha256.lower()
        folder = (self.scope_dir(key.owner) if isinstance(key, ScopedCacheKey)
                  else self._owner_dir(key.server_id, key.owner_github_id))
        return folder / sha[:2] / key.digest()

    def get(self, key: CacheKey) -> Optional[bytes]:
        path = self.path_for(key)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        with os.fdopen(fd, "rb") as f:
            data = f.read()
        try:
            os.utime(path)          # trim removes the least recently used first
        except OSError:
            pass
        return data

    def put(self, key: CacheKey, data: bytes) -> None:
        final = self.path_for(key)
        leased = self._leased()
        if final in leased:
            pinned = sum(p.stat().st_size for p in leased if p != final and p.is_file())
            if pinned + len(data) > self.max_bytes:
                raise CacheBudgetError("The cache cannot admit these bytes while its active views are leased.")
        previous = final.stat().st_size if final.is_file() else 0
        folder = _private(final.parent)
        partial = folder / f"{PARTIAL}{uuid.uuid4()}"
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(partial, final)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        self._used = sum(size for _, size, _ in self._files(self.root()))
        if self._used > self.max_bytes:
            self.trim()

    def scratch(self, server_id: str, owner_github_id: int) -> Path:
        """The owner's private folder for MemberSource.materialize copies."""
        return _private(self._owner_dir(server_id, owner_github_id) / SCRATCH)

    def forget_owner(self, server_id: str, owner_github_id: int) -> int:
        """Remove everything derived for one owner; the bytes removed."""
        folder = self._owner_dir(server_id, owner_github_id)
        removed = sum(size for _, size, _ in self._files(folder))
        shutil.rmtree(folder, ignore_errors=True)
        self._used = None
        return removed

    def trim(self) -> int:
        """Remove leftovers (partial files and scratch copies older than an
        hour) and then the least recently used files until the cache is
        within max_bytes; the bytes removed."""
        removed, now = 0, time.time()
        leased = self._leased()
        kept = []
        for path, size, used in self._files(self.root()):
            leftover = path.name.startswith(PARTIAL) or path.parent.name == SCRATCH
            if leftover:
                if now - used > LEFTOVER_AFTER_SEC:
                    path.unlink(missing_ok=True)
                    removed += size
                continue
            kept.append((used, size, path))
        total = sum(size for _, size, _ in kept)
        for used, size, path in sorted(kept):
            if total <= self.max_bytes:
                break
            if path in leased:
                continue
            path.unlink(missing_ok=True)
            total -= size
            removed += size
        self._used = total
        return removed

    @staticmethod
    def _files(folder: Path):
        """(path, size, last used) of every file under `folder`, links skipped."""
        for dirpath, directories, names in os.walk(folder):
            directories[:] = [d for d in directories if not (Path(dirpath) / d).is_symlink()]
            for name in names:
                path = Path(dirpath) / name
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if path.is_symlink():
                    continue
                yield path, st.st_size, st.st_mtime


class CacheBudgetError(ValueError):
    pass
