"""What makes a package a decision package: the shared deck's own files.

A decision package (a zip holding `deck-data.js` and `scripts/decide.py`)
reaches Tim only through `python -m jarvis.make_package`, which checks it end
to end in an isolated JARVIS (decision_check.py) and then signs it with the
agent's own key, vouching that it passed (package_signature.DECISION_CHECKED).
A JARVIS unpacks one only with that signed statement from an onboarded
agent's key (server._decision_problem). This module once also held the old
stamp, an HMAC keyed by the receiving JARVIS's tool token, which the signed
statement replaced; only the marks are left.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from jarvis import bundle_archive

# What makes a package a decision package: the shared deck's own files.
MARKS = ("deck-data.js", "scripts/decide.py")


def _files(archive: zipfile.ZipFile) -> dict:
    listing = bundle_archive.list_members(archive)
    root = listing.root
    return {path[len(root):]: m for path, m in listing.files().items() if path.startswith(root)}


def is_decision_package(zip_path: Path) -> bool:
    with bundle_archive.open_archive(zip_path) as archive:
        files = _files(archive)
    return all(mark in files for mark in MARKS)
