"""The proof that a decision package passed its end-to-end check, which a
JARVIS asks for before it unpacks one from a file on this Mac.

A decision package (a zip holding `deck-data.js` and `scripts/decide.py`,
the shared decision deck's) reaches Tim only through
`python -m jarvis.decision_package`, which checks it in an isolated JARVIS
(decision_check.py) and delivers it only if every check passed. So that no
other way delivers one unchecked, the zip carries `decision-check.json`: the
SHA-256 of every other file in it, and an HMAC of that digest keyed by the
receiving JARVIS's own tool token, which only a process that can read that
token (on this Mac, as this user) can make. `_open_local_package` (the
brain's `open_local_package` and `POST /api/packages/local`) refuses a
decision package whose stamp is missing or does not match its files.

The check stamps the zip for the isolated JARVIS's token, so it unpacks
there; once every check has passed, the command stamps the same files again
for the live JARVIS's token. The files checked are the files delivered.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import zipfile
from pathlib import Path
from typing import Optional

from jarvis import bundle_archive

STAMP = "decision-check.json"
# What makes a package a decision package: the shared deck's own files.
MARKS = ("deck-data.js", "scripts/decide.py")
VERSION = 1


def _files(archive: zipfile.ZipFile) -> tuple[str, dict[str, bundle_archive.Member]]:
    listing = bundle_archive.list_members(archive)
    root = listing.root
    return root, {path[len(root):]: m for path, m in listing.files().items()
                  if path.startswith(root)}


def _digest(archive: zipfile.ZipFile, files: dict) -> str:
    """One SHA-256 over every file but the stamp: each one's path and hash."""
    whole = hashlib.sha256()
    for path in sorted(files):
        if path == STAMP:
            continue
        whole.update(f"{path}\0{bundle_archive.digest(archive, files[path])}\n".encode())
    return whole.hexdigest()


def _mac(digest: str, token: str) -> str:
    return hmac.new(token.encode(), f"decision-check:{VERSION}:{digest}".encode(),
                    hashlib.sha256).hexdigest()


def is_decision_package(zip_path: Path) -> bool:
    with bundle_archive.open_archive(zip_path) as archive:
        _, files = _files(archive)
    return all(mark in files for mark in MARKS)


def stamp(source: Path, target: Path, token: str) -> None:
    """Copy the zip at `source` to `target` with a stamp for `token` in place
    of any it had; every other file is copied byte for byte."""
    with bundle_archive.open_archive(source) as archive:
        root, files = _files(archive)
        digest = _digest(archive, files)
        body = json.dumps({"version": VERSION, "files": digest, "mac": _mac(digest, token)},
                          indent=2) + "\n"
        partial = target.with_name(target.name + ".part")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as out:
            for info in archive.infolist():
                if info.filename == root + STAMP:
                    continue
                out.writestr(info, archive.read(info))
            out.writestr(root + STAMP, body)
    partial.replace(target)


def problem(zip_path: Path, token: str) -> Optional[str]:
    """Why this zip may not be unpacked here, or None. Only a decision
    package needs a stamp; it must be this JARVIS's and match its files."""
    try:
        with bundle_archive.open_archive(zip_path) as archive:
            _, files = _files(archive)
            if not all(mark in files for mark in MARKS):
                return None
            if STAMP not in files:
                return ("This is a decision package that has not passed its check. Decision "
                        "packages reach him only through python -m jarvis.decision_package, "
                        "which checks them end to end first.")
            body = json.loads(bundle_archive.read_member(archive, files[STAMP], limit=4096))
            digest = _digest(archive, files)
    except (bundle_archive.ArchiveError, ValueError, OSError, zipfile.BadZipFile) as e:
        return f"Its check stamp could not be read: {e}"
    if not isinstance(body, dict) or body.get("files") != digest:
        return "This decision package was changed after its check; check it again."
    if not hmac.compare_digest(str(body.get("mac") or ""), _mac(digest, token)):
        return ("This decision package was not checked for this JARVIS; send it with "
                "python -m jarvis.decision_package.")
    return None
