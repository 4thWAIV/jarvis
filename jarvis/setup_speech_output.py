#!/usr/bin/env python3
"""Install the local Kokoro speech-output model.

    .venv/bin/python -m jarvis.setup_speech_output [--archive PATH]

Downloads the pinned 126 MiB Kokoro v1.0 int8 CPU export, verifies the
release SHA-256, and atomically installs it under JARVIS data/models.
Existing incomplete folders are preserved and reported, never overwritten.
Model weights: Apache-2.0 (hexgrad/Kokoro-82M); the archive also includes
espeak-ng linguistic data and its notices. Keep the downloaded LICENSE
and all data notices when redistributing. No voice cloning or API key.
Use --archive PATH to install an already downloaded, verified archive.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from jarvis import local_tts as transcriber  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(dest: Path) -> None:
    print(f"Downloading {transcriber.MODEL_URL}")
    with urllib.request.urlopen(transcriber.MODEL_URL, timeout=60) as response, dest.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        for block in iter(lambda: response.read(1 << 20), b""):
            out.write(block)
            done += len(block)
            if total:
                print(f"\r  {done >> 20} / {total >> 20} MiB", end="", flush=True)
    print()


def _extract(archive: Path, into: Path) -> Path:
    """Extract only regular files and folders under the model's own folder;
    refuse links, absolute paths and anything that climbs out."""
    prefix = transcriber.MODEL_NAME + "/"
    with tarfile.open(archive, "r:bz2") as tar:
        members = tar.getmembers()
        for m in members:
            name = m.name
            if not (name == transcriber.MODEL_NAME or name.startswith(prefix)):
                raise SystemExit(f"Refused: unexpected entry {name!r} in the archive.")
            if ".." in Path(name).parts or Path(name).is_absolute():
                raise SystemExit(f"Refused: unsafe path {name!r} in the archive.")
            if not (m.isfile() or m.isdir()):
                raise SystemExit(f"Refused: {name!r} is a link or device, not a file.")
        tar.extractall(into, members=members, filter="data")
    return into / transcriber.MODEL_NAME


def install(archive: Path | None = None) -> Path:
    destination = transcriber.model_dir()
    if transcriber.model_problem(destination) is None:
        print(f"Already installed: {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".speech-model-",
                                     dir=destination.parent) as work:
        work_dir = Path(work)
        if archive is None:
            archive = work_dir / "model.tar.bz2"
            _download(archive)
        digest = _sha256(archive)
        if digest != transcriber.MODEL_SHA256:
            raise SystemExit(f"Refused: {archive} has SHA-256 {digest}, but the "
                             f"release publishes {transcriber.MODEL_SHA256}.")
        print(f"SHA-256 matches the release: {digest}")
        extracted = _extract(archive, work_dir / "extract")
        problem = transcriber.model_problem(extracted)
        if problem:
            raise SystemExit(f"Refused: the archive is incomplete. {problem}")
        if destination.exists():
            raise SystemExit(f"Refused: {destination} exists but is incomplete "
                             "and is not removed for you; move it aside and run again.")
        extracted.rename(destination)
    print(f"Installed: {destination}")
    return destination


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path,
                        help="an archive already downloaded, checked like a download")
    args = parser.parse_args(argv)
    install(args.archive)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
