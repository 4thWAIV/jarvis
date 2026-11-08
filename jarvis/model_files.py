"""Fetching a model's files: one download, one SHA-256 check, the refusal
both say when a file is not the one published. Shared by the speech models
(speech_models.py) and the embedding model (embedding_models.py), which
each then install their files into place with one rename."""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path
from typing import Callable

# (state, bytes done, bytes in all): "downloading", "verifying", "installing".
Progress = Callable[[str, int, int], None]
# How long one read of the download may wait. Without it a connection that
# goes dead hangs the install for good, and while it shows as downloading a
# new install of that model is not started.
DOWNLOAD_TIMEOUT_SEC = 60


class InstallRefused(Exception):
    """A file that is not the release's, or not safe to put in place."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path, progress: Progress) -> None:
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SEC) as response, \
            dest.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        for block in iter(lambda: response.read(1 << 20), b""):
            out.write(block)
            done += len(block)
            progress("downloading", done, total)


def quiet(_state: str, _done: int, _total: int) -> None:
    pass
