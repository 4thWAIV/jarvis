"""A package's signature: the agent that sent it, inside the zip itself.

`agent-signature.json`, at the package's root, holds the SHA-256 of every
other file in the zip (each one's path and hash, as decision_stamp's
digest), who signed it (the key's fingerprint and the session it is for,
and a subagent's own words for itself, `worker`), what the signer vouches
for (`statements`: `decision-check v<n>` once a deck passed its end-to-end
check), an optional work-item key grant (`work_key`: the public half of a
key made for one Work item, which may then report on that item alone),
and the agent's SSH signature over all of that under `jarvis-package@v1`.

JARVIS checks it against the public key it holds for that fingerprint,
never one the file names: a signature only proves anything for a key Tim
onboarded (agent_key_store.py). One signature replaces the transcript proof
of who sent a package and the decision stamp's HMAC.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jarvis import agent_keys, bundle_archive

FILE = "agent-signature.json"
VERSION = 1
# What a deck that passed decision_check.py vouches for.
DECISION_CHECKED = "decision-check v1"


class SignatureError(ValueError):
    """A signature that cannot be made or read, with why."""


@dataclass(frozen=True)
class Signed:
    """What a package's signature says, and whether it checks."""
    fingerprint: str
    session: str
    public_key: str = ""              # as the file names it: proves only that key signed it
    worker: str = ""
    statements: tuple[str, ...] = ()
    work_key: Optional[dict] = None
    files_match: bool = False         # the files are the ones signed
    manifest: dict = field(default_factory=dict)
    signature: str = ""

    def checks(self, public_key: str) -> bool:
        """Whether the files are the ones signed and the signature is the
        holder of `public_key`'s."""
        return self.files_match and agent_keys.verify(public_key, agent_keys.PACKAGE,
                                                      _text(self.manifest), self.signature)


def _files(archive: zipfile.ZipFile) -> tuple[str, dict]:
    listing = bundle_archive.list_members(archive)
    root = listing.root
    return root, {path[len(root):]: m for path, m in listing.files().items()
                  if path.startswith(root)}


def _digest(archive: zipfile.ZipFile, files: dict) -> str:
    whole = hashlib.sha256()
    for path in sorted(files):
        if path == FILE:
            continue
        whole.update(f"{path}\0{bundle_archive.digest(archive, files[path])}\n".encode())
    return whole.hexdigest()


def _text(manifest: dict) -> bytes:
    """What the signature is over: the namespace and the manifest, sorted."""
    return (agent_keys.PACKAGE + "\n").encode() + json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(source: Path, target: Path, private: Path, public_key: str, session: str,
         worker: str = "", statements: tuple[str, ...] = (),
         work_key: Optional[dict] = None) -> None:
    """Copy the zip at `source` to `target` with a signature by the key at
    `private` in place of any it had; every other file byte for byte."""
    with bundle_archive.open_archive(source) as archive:
        root, files = _files(archive)
        manifest = {"version": VERSION, "key": agent_keys.fingerprint(public_key),
                    "public_key": agent_keys.normalise(public_key), "session": session, "worker": " ".join(str(worker or "").split())[:80],
                    "files": _digest(archive, files), "statements": sorted(set(statements))}
        if work_key:
            manifest["work_key"] = dict(work_key)
        sig = agent_keys.compact(agent_keys.sign(private, agent_keys.PACKAGE, _text(manifest)))
        body = json.dumps({**manifest, "signature": sig}, indent=2) + "\n"
        partial = target.with_name(target.name + ".part")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as out:
            for info in archive.infolist():
                if info.filename == root + FILE:
                    continue
                out.writestr(info, archive.read(info))
            out.writestr(root + FILE, body)
    partial.replace(target)


def add_files(archive_path: Path, files: dict[str, bytes]) -> None:
    """Add `files` (paths relative to the package's root) to the zip in
    place, replacing any of the same name."""
    with bundle_archive.open_archive(archive_path) as archive:
        root, _ = _files(archive)
        partial = archive_path.with_name(archive_path.name + ".part")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as out:
            for info in archive.infolist():
                if info.filename[len(root):] in files:
                    continue
                out.writestr(info, archive.read(info))
            for name, data in files.items():
                out.writestr(root + name, data)
    partial.replace(archive_path)


def read(zip_path: Path) -> Optional[Signed]:
    """The package's signature, or None when it has none. SignatureError
    for one that cannot be read."""
    try:
        with bundle_archive.open_archive(zip_path) as archive:
            _, files = _files(archive)
            if FILE not in files:
                return None
            body = json.loads(bundle_archive.read_member(archive, files[FILE], limit=16384))
            digest = _digest(archive, files)
    except (bundle_archive.ArchiveError, ValueError, OSError, zipfile.BadZipFile) as e:
        raise SignatureError(f"Its signature could not be read: {e}")
    if not isinstance(body, dict) or not isinstance(body.get("signature"), str):
        raise SignatureError("Its signature file is not one JARVIS reads.")
    manifest = {k: v for k, v in body.items() if k != "signature"}
    statements = manifest.get("statements") or []
    work_key = manifest.get("work_key")
    return Signed(str(manifest.get("key") or ""), str(manifest.get("session") or ""),
                  str(manifest.get("public_key") or ""), str(manifest.get("worker") or ""),
                  tuple(str(s) for s in statements if isinstance(s, str)),
                  work_key if isinstance(work_key, dict) else None,
                  manifest.get("files") == digest, manifest, body["signature"])
