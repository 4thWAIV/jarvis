"""Where a received file is on this JARVIS: the one answer the page, the
brain's tools and an agent's hand-over all read.

A file is named by its message and attachment ids, for the owner signed in
now, never by its name: two files called report.pdf are two attachments.
The answer (`AttachmentLocation`) comes from the messaging-files service's
own record and its configured downloads folder (messaging_files.py); there
is no second registry. `local_path` is set only for a file that is here and
whose size and SHA-256 are the manifest's now. Where a download would save
it is `intended_path`, which is never a saved file.

    remote_only   on the team server, not downloaded here
    downloading   being fetched now
    verified      here, size and hash checked: `local_path`
    missing       downloaded once, and gone from its place
    changed       at its place, but not the bytes that were sent
    moved         not at its place, but still where it was verified before
                  (`previous_path`): the downloads folder changed
    failed        the last download failed (`reason` is its code)

Locating never unpacks, opens or runs a file.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from jarvis import data_paths, run_store
from jarvis import messaging_files

LocationState = Literal["remote_only", "downloading", "verified", "missing", "changed",
                        "moved", "failed"]
# What can be done about it: fetch it here (again), show it in Finder, copy its path.
LocationAction = Literal["make_local", "reveal", "copy_path"]

# The owner's words for each state that is not "here and checked", the page
# and the brain alike. A failed download says its own code's words instead.
STATE_SHOWN: dict[str, str] = {
    "remote_only": "Not downloaded",
    "downloading": "Downloading",
    "verified": "On this Mac, checked",
    "missing": "Missing from this Mac",
    "changed": "Changed since it was checked",
    "moved": "No longer in the downloads folder",
    "failed": "Download failed",
}


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AttachmentExtraction(_Frozen):
    """Where a received zip was unpacked, when JARVIS unpacked it (a work
    package): the zip's verified hash, the folder and the member paths
    asked for, relative to it. Only what extraction already did."""
    root: str
    source_sha256: str
    members: list[str] = Field(default_factory=list)
    root_exists: bool


class AttachmentLocation(_Frozen):
    message_id: UUID
    attachment_id: UUID
    filename: str                                 # the sender's name, safe as a path part
    media_type: str
    node_id: UUID                                 # the JARVIS this answer is about
    state: LocationState
    local_path: Optional[str] = None              # only a file here, verified now
    intended_path: Optional[str] = None           # where make_local saves it; not a file yet
    previous_path: Optional[str] = None           # moved: where it was verified before
    size_bytes: int = Field(ge=0)
    sha256: str
    bytes_done: int = Field(default=0, ge=0)
    verified_at: Optional[datetime] = None
    reason: Optional[str] = None                  # a state's code, or the failure's
    actions: list[LocationAction] = Field(default_factory=list)
    extraction: Optional[AttachmentExtraction] = None


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS attachment_locations (
        server_id          TEXT NOT NULL,
        owner_github_id    INTEGER NOT NULL,
        attachment_id      TEXT NOT NULL,
        path               TEXT,
        verified_at        REAL,
        extraction_root    TEXT,
        extraction_sha256  TEXT,
        extraction_members TEXT,
        PRIMARY KEY (server_id, owner_github_id, attachment_id)
    );
"""


def init_db() -> None:
    """This module's table, beside messaging_files in the shared database."""
    with closing(run_store._connect()) as conn:
        conn.executescript(_SCHEMA)


def _at(when: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(when, timezone.utc) if when is not None else None


class Verifier:
    """(size, sha256) of a file, hashed again whenever the file's identity
    or times differ from the last time it was hashed; never through a link."""

    def __init__(self):
        self._seen: dict[str, tuple[tuple, tuple[int, str]]] = {}

    def digest(self, path: Path) -> Optional[tuple[int, str]]:
        try:
            info = os.lstat(path)
        except OSError:
            self._seen.pop(str(path), None)
            return None
        key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        seen = self._seen.get(str(path))
        if seen is not None and seen[0] == key:
            return seen[1]
        got = messaging_files.file_digest(path)
        if got is None:
            self._seen.pop(str(path), None)
        else:
            self._seen[str(path)] = (key, got)
        return got


class AttachmentLocations:
    """The shared resolver over one `FileSnapshots`. `node_id` is this
    JARVIS's network node."""

    def __init__(self, files: messaging_files.FileSnapshots, *,
                 node_id: Callable[[], str] = data_paths.node_id):
        self.files, self._node_id = files, node_id
        self._verify = Verifier()
        init_db()

    # -- the record ------------------------------------------------------------------

    def _record(self, server_id: str, owner: int, attachment_id: UUID) -> Optional[sqlite3.Row]:
        with closing(run_store._connect()) as conn:
            return conn.execute(
                "SELECT * FROM attachment_locations WHERE server_id=? AND owner_github_id=? "
                "AND attachment_id=?", (server_id, owner, str(attachment_id))).fetchone()

    def _extraction(self, row: Optional[sqlite3.Row]) -> Optional[AttachmentExtraction]:
        if row is None or not row["extraction_root"]:
            return None
        members = [m for m in (row["extraction_members"] or "").split("\n") if m]
        return AttachmentExtraction(root=row["extraction_root"],
                                    source_sha256=row["extraction_sha256"], members=members,
                                    root_exists=Path(row["extraction_root"]).is_dir())

    # -- the answer ------------------------------------------------------------------

    def lookup(self, server_id: str, owner_github_id: int, message_id: UUID,
               attachment_id: UUID) -> AttachmentLocation:
        """Where this owner's received file is now, read only: nothing is
        fetched, written or opened. A message or file this owner does not
        hold is `attachment_not_found` (wire.ContractError)."""
        ref = self.files.received_ref(server_id, owner_github_id, message_id, attachment_id)
        known, changed_at = self.files.download_record(server_id, owner_github_id,
                                                       attachment_id)
        record = self._record(server_id, owner_github_id, attachment_id)
        destination = self.files.download_path(ref)
        expected = (ref.size_bytes, ref.sha256)
        base = dict(message_id=message_id, attachment_id=attachment_id, filename=ref.filename,
                    media_type=ref.media_type, node_id=UUID(str(self._node_id())),
                    size_bytes=ref.size_bytes, sha256=ref.sha256,
                    extraction=self._extraction(record))
        fetch_again: list[LocationAction] = ["make_local"]

        if known.state == "downloading":
            return AttachmentLocation(**base, state="downloading", bytes_done=known.bytes_done,
                                      intended_path=str(destination), reason="downloading")
        if known.state == "failed":
            return AttachmentLocation(**base, state="failed", intended_path=str(destination),
                                      reason=known.failure or "transport", actions=fetch_again)
        if known.state != "verified":
            return AttachmentLocation(**base, state="remote_only",
                                      intended_path=str(destination), reason="not_downloaded",
                                      actions=fetch_again)

        here = self._verify.digest(destination)
        if here == expected:
            verified = record["verified_at"] if record is not None and record["path"] == \
                str(destination) else changed_at
            return AttachmentLocation(**base, state="verified", local_path=str(destination),
                                      bytes_done=ref.size_bytes, verified_at=_at(verified),
                                      actions=["reveal", "copy_path"])
        if here is not None or os.path.lexists(destination):
            return AttachmentLocation(**base, state="changed", intended_path=str(destination),
                                      reason="changed", actions=fetch_again)
        previous = record["path"] if record is not None else None
        if previous and previous != str(destination) and \
                self._verify.digest(Path(previous)) == expected:
            return AttachmentLocation(**base, state="moved", intended_path=str(destination),
                                      previous_path=previous, reason="moved",
                                      actions=fetch_again)
        return AttachmentLocation(**base, state="missing", intended_path=str(destination),
                                  reason="missing", actions=fetch_again)
