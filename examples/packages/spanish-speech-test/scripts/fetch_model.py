#!/usr/bin/env python3
"""Download the Spanish Kroko streaming model into this package's folder.

    "$JARVIS_PYTHON" scripts/fetch_model.py [--archive PATH]

Downloads the sherpa-onnx release asset (about 120 MiB), checks it against
the SHA-256 GitHub publishes for it, and extracts only the model's own
regular files into model/<MODEL_NAME>/. --archive uses an archive already
on disk instead, checked the same way. Nothing is installed into JARVIS:
the model stays in this package's folder.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
MODEL_NAME = "sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             f"asr-models/{MODEL_NAME}.tar.bz2")
MODEL_SHA256 = "31b2230a95d23290b308b393da930015a4b2105cb3abb9367aed35f7fcf29cf1"
MODEL_FILES = ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")
DESTINATION = HERE / "model" / MODEL_NAME


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(dest: Path) -> None:
    print(f"Downloading {MODEL_URL}", flush=True)
    with urllib.request.urlopen(MODEL_URL) as response, dest.open("wb") as out:
        for block in iter(lambda: response.read(1 << 20), b""):
            out.write(block)


def _extract(archive: Path, into: Path) -> None:
    prefix = MODEL_NAME + "/"
    with tarfile.open(archive, "r:bz2") as tar:
        members = tar.getmembers()
        for m in members:
            if not (m.name == MODEL_NAME or m.name.startswith(prefix)) \
                    or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                raise SystemExit(f"Refused: unexpected entry {m.name!r} in the archive.")
        tar.extractall(into, members=members, filter="data")


def installed() -> bool:
    return all((DESTINATION / n).is_file() and (DESTINATION / n).stat().st_size
               for n in MODEL_FILES)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, help="an archive already downloaded")
    args = parser.parse_args(argv)
    if installed():
        print(f"Already here: {DESTINATION}")
        return 0
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DESTINATION.parent) as scratch:
        archive = args.archive.expanduser() if args.archive else Path(scratch) / "model.tar.bz2"
        if not args.archive:
            _download(archive)
        found = _sha256(archive)
        if found != MODEL_SHA256:
            print(f"Refused: the archive's SHA-256 is {found}, expected {MODEL_SHA256}.")
            return 1
        _extract(archive, Path(scratch))
        (Path(scratch) / MODEL_NAME).replace(DESTINATION)
    if not installed():
        print(f"The archive did not hold the model's files ({', '.join(MODEL_FILES)}).")
        return 1
    print(f"The Spanish model is in {DESTINATION} (SHA-256 checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
