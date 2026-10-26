#!/usr/bin/env python3
"""Install the local speech-input model JARVIS listens with.

    .venv/bin/python scripts/setup_speech_input.py [--archive PATH]

Downloads Banafo's Kroko streaming Zipformer English model, as sherpa-onnx
exports it (about 55 MiB), from the sherpa-onnx GitHub release,
checks it against the SHA-256 GitHub publishes for that asset, extracts it
into a temporary folder beside the destination, and renames the complete
folder into place, so an interrupted run never looks installed. The
destination is `transcriber.model_dir()`: `<data>/models/<model>` or
JARVIS_STT_MODEL_DIR.

--archive uses an archive already on disk (a previous download) instead of
downloading; it is checked against the same digest. Nothing is downloaded
anywhere but here: JARVIS never fetches the model on its own.

The model is Banafo's community model, under CC-BY-SA; the sherpa-onnx
engine is Apache-2.0. See https://huggingface.co/Banafo/Kroko-ASR
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import transcriber  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(dest: Path) -> None:
    print(f"Downloading {transcriber.MODEL_URL}")
    with urllib.request.urlopen(transcriber.MODEL_URL) as response, dest.open("wb") as out:
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
