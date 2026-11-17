"""What makes a package a decision package: the shared deck's own files.

A decision package (a zip holding `deck-data.js` and `scripts/decide.py`)
reaches Tim only through `python -m jarvis.make_package`, which lays it out,
proves it fits (presentation_proof) and signs it with the agent's own key;
the receiving JARVIS makes that proof again from the zip's own bytes
(server._decision_problem). A deck from before carries no proof and is
admitted on its signer's statement that it passed the old end-to-end check
(package_signature.DECISION_CHECKED). This module once also held the old
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


# What a deck JARVIS laid out carries: its source and the static proof that
# it fits (presentation_proof), which the receiving JARVIS makes again.
PROVEN = ("source.yaml", ".jarvis/layout-proof.json", "target-index.json")


def carries_proof(zip_path: Path) -> bool:
    with bundle_archive.open_archive(zip_path) as archive:
        files = _files(archive)
    return all(name in files for name in PROVEN)


def is_decision_package(zip_path: Path) -> bool:
    with bundle_archive.open_archive(zip_path) as archive:
        files = _files(archive)
    return all(mark in files for mark in MARKS)
