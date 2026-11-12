"""`FORMAT`, `CURRENT` and the manifests (design sections 7.5 to 7.7).

`FORMAT` says `jkn <version>` and the store's `engine_id`, a uuid4 minted
when the directory is made: LSNs restart at 1 in a rebuilt directory, so an
LSN means nothing without its `engine_id`.

A manifest is canonical JSON and a last line with its own SHA-256. It
names the live segment set (each file's generation, family, SHA-256, size
and LSN range), the checkpoint LSN (everything at or below it is in
segments), the next generation and whatever the parts keep beside it.
`CURRENT` names the live manifest and is replaced by write-temp,
`F_FULLFSYNC`, rename and an fsync of the folder; the previous manifest is
kept, so a current one that fails its checksum falls back to it.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jarvis.knowledge import FORMAT_NAME, FORMAT_VERSION
from jarvis.knowledge.oscompat import O_BINARY, O_NOFOLLOW
from jarvis.knowledge.wal import fsync_dir, full_fsync


class FormatTooNew(RuntimeError):
    """The directory was written by a newer engine; it is left untouched."""


class ManifestCorrupt(RuntimeError):
    pass


@dataclass
class Manifest:
    number: int = 0
    checkpoint_lsn: int = 0
    next_gen: int = 1
    segments: list[dict] = field(default_factory=list)
    parts: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"format": FORMAT_VERSION, "number": self.number,
                "checkpoint_lsn": self.checkpoint_lsn, "next_gen": self.next_gen,
                "segments": self.segments, "parts": self.parts}

    @classmethod
    def from_json(cls, body: dict) -> "Manifest":
        if int(body.get("format", 0)) > FORMAT_VERSION:
            raise FormatTooNew(f"manifest format {body.get('format')} is newer than {FORMAT_VERSION}")
        return cls(number=int(body["number"]), checkpoint_lsn=int(body["checkpoint_lsn"]),
                   next_gen=int(body["next_gen"]), segments=list(body["segments"]),
                   parts=dict(body.get("parts") or {}))

    def generations(self) -> list[int]:
        return sorted({int(s["gen"]) for s in self.segments})


def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def write_durably(path: Path, data: bytes) -> None:
    """Write-temp, F_FULLFSYNC, rename, fsync the folder."""
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_BINARY, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        full_fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    fsync_dir(path.parent)


def ensure_format(directory: Path) -> str:
    """The store's engine_id, minting FORMAT on a new directory; refuses a
    newer format without touching it."""
    path = directory / "FORMAT"
    if path.exists():
        lines = path.read_text().split()
        if len(lines) < 3 or lines[0] != FORMAT_NAME:
            raise ManifestCorrupt("FORMAT is not a knowledge engine's")
        if int(lines[1]) > FORMAT_VERSION:
            raise FormatTooNew(f"format {lines[1]} is newer than {FORMAT_VERSION}")
        return lines[2]
    engine_id = uuid.uuid4().hex
    write_durably(path, f"{FORMAT_NAME} {FORMAT_VERSION}\n{engine_id}\n".encode())
    return engine_id


def _encode(manifest: Manifest) -> bytes:
    body = canonical(manifest.to_json())
    return body + b"\n" + hashlib.sha256(body).hexdigest().encode() + b"\n"


def _decode(data: bytes) -> Manifest:
    body, _, rest = data.rstrip(b"\n").rpartition(b"\n")
    if not body or hashlib.sha256(body).hexdigest().encode() != rest.strip():
        raise ManifestCorrupt("manifest fails its checksum")
    return Manifest.from_json(json.loads(body))


def manifest_path(directory: Path, number: int) -> Path:
    return directory / f"MANIFEST-{number}"


def load(directory: Path) -> tuple[Optional[Manifest], list[str]]:
    """The live manifest, or the previous one when the current fails its
    checksum; None for a new directory. The second value lists what was
    wrong on the way."""
    problems: list[str] = []
    current = directory / "CURRENT"
    if not current.exists():
        return None, problems
    name = current.read_text().strip()
    try:
        number = int(name.split("-", 1)[1])
    except (IndexError, ValueError):
        raise ManifestCorrupt(f"CURRENT names {name!r}")
    for n in (number, number - 1):
        path = manifest_path(directory, n)
        try:
            return _decode(path.read_bytes()), problems
        except FormatTooNew:
            raise
        except (OSError, ValueError, KeyError, ManifestCorrupt) as e:
            problems.append(f"{path.name}: {e}")
    raise ManifestCorrupt("; ".join(problems))


def install(directory: Path, manifest: Manifest) -> None:
    """Write the next manifest, then point CURRENT at it; keep one previous."""
    write_durably(manifest_path(directory, manifest.number), _encode(manifest))
    write_durably(directory / "CURRENT", f"MANIFEST-{manifest.number}\n".encode())
    for old in directory.glob("MANIFEST-*"):
        try:
            n = int(old.name.split("-", 1)[1])
        except ValueError:
            continue
        if n < manifest.number - 1:
            old.unlink(missing_ok=True)


def read_file(path: Path) -> Manifest:
    return _decode(path.read_bytes())


def write_file(path: Path, manifest: Manifest) -> None:
    write_durably(path, _encode(manifest))
