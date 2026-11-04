"""Where a received file is on this JARVIS: the one answer the page, the
brain's tools and an agent's hand-over all read.

    GET  /api/team/messages/{message_id}/files/{attachment_id}/location
         -> AttachmentLocation (lookup: read only)
    POST /api/team/messages/{message_id}/files/{attachment_id}/location
         -> AttachmentLocation (ensure_local: download and verify if needed)

The brain's `attachment_location` and acting `make_attachment_local` call
the same two, and `reveal_attachment` the existing reveal.

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

import asyncio
import hashlib
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Optional
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, computed_field

from jarvis import data_paths, run_store
from jarvis import messaging_api, messaging_files

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

    @computed_field
    @property
    def shown(self) -> str:
        """The state in the owner's words (STATE_SHOWN), for the page."""
        return STATE_SHOWN[self.state]


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
    CREATE TABLE IF NOT EXISTS attachment_leases (
        server_id       TEXT NOT NULL,
        owner_github_id INTEGER NOT NULL,
        attachment_id   TEXT NOT NULL,
        holder          TEXT NOT NULL,
        created_at      REAL NOT NULL,
        PRIMARY KEY (server_id, owner_github_id, attachment_id, holder)
    );
"""

# A lease's holder: "run:<run id>" while that run is queued or running,
# "message:<number>" while that message to a session is still open.
_OPEN_MESSAGE = frozenset({run_store.MessageStatus.STAGED, run_store.MessageStatus.QUEUED,
                           *run_store.OPEN_MESSAGE_STATUSES})


def holder_active(holder: str) -> bool:
    """Whether the agent a lease was given to still has the file in hand."""
    kind, _, key = holder.partition(":")
    if kind == "run":
        run = run_store.get_run(key)
        return run is not None and run["status"] in run_store.RunStatus.ACTIVE
    if kind == "message" and key.isdigit():
        message = run_store.get_message(int(key))
        return message is not None and message.status in _OPEN_MESSAGE
    return False


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
                 node_id: Callable[[], str] = data_paths.node_id,
                 active: Callable[[str], bool] = holder_active,
                 now: Callable[[], float] = time.time):
        self.files, self._node_id, self._active, self._now = files, node_id, active, now
        self._verify = Verifier()
        init_db()

    def node_id(self) -> UUID:
        """The JARVIS these locations are on."""
        return UUID(str(self._node_id()))

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
        destination = self.files.download_destination(ref)
        expected = (ref.size_bytes, ref.sha256)
        base = dict(message_id=message_id, attachment_id=attachment_id, filename=ref.filename,
                    media_type=ref.media_type, node_id=self.node_id(),
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

    # -- making it local ----------------------------------------------------------------

    async def ensure_local(self, server_id: str, owner_github_id: int, message_id: UUID,
                           attachment_id: UUID) -> AttachmentLocation:
        """The file here and verified, fetched now through the existing
        authenticated downloader when it is not (never downloaded, failed,
        missing, changed or moved): its location, verified again. Only on
        the owner's word. Nothing is unpacked, opened or run. Each look
        hashes the file, so it runs off the event loop."""
        def look() -> AttachmentLocation:
            return self.lookup(server_id, owner_github_id, message_id, attachment_id)

        found = await asyncio.to_thread(look)
        fetched = found.state not in ("verified", "downloading")
        if fetched:
            await self.files.download(server_id, owner_github_id, message_id, attachment_id)
            found = await asyncio.to_thread(look)
        if found.state == "verified" and found.local_path:
            self._remember(server_id, owner_github_id, attachment_id, found.local_path,
                           again=fetched)
            found = await asyncio.to_thread(look)
        return found

    def _remember(self, server_id: str, owner: int, attachment_id: UUID, path: str,
                  again: bool = False) -> None:
        """Where it was verified, and when: how a later look knows it moved.
        `again`: just downloaded and checked, so the time moves even at the
        same path (a deleted or changed copy fetched again)."""
        record = self._record(server_id, owner, attachment_id)
        if not again and record is not None and record["path"] == path:
            return
        with closing(run_store._connect()) as conn:
            conn.execute(
                "INSERT INTO attachment_locations (server_id, owner_github_id, attachment_id, "
                "path, verified_at) VALUES (?,?,?,?,?) ON CONFLICT (server_id, "
                "owner_github_id, attachment_id) DO UPDATE SET path=excluded.path, "
                "verified_at=excluded.verified_at",
                (server_id, owner, str(attachment_id), path, self._now()))
            conn.commit()

    def record_extraction(self, server_id: str, owner_github_id: int, message_id: UUID,
                          attachment_id: UUID, root: Path, members: list[str]) -> None:
        """Where a verified zip was unpacked by what already unpacks one (a
        work package): its hash, the folder and the members asked for."""
        ref = self.files.received_ref(server_id, owner_github_id, message_id, attachment_id)
        with closing(run_store._connect()) as conn:
            conn.execute(
                "INSERT INTO attachment_locations (server_id, owner_github_id, attachment_id, "
                "extraction_root, extraction_sha256, extraction_members) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (server_id, owner_github_id, attachment_id) DO UPDATE SET "
                "extraction_root=excluded.extraction_root, "
                "extraction_sha256=excluded.extraction_sha256, "
                "extraction_members=excluded.extraction_members",
                (server_id, owner_github_id, str(attachment_id), str(root), ref.sha256,
                 "\n".join(m for m in members if m and "\n" not in m)))
            conn.commit()

    def extracted_from(self, server_id: str, owner_github_id: int,
                       root: Path) -> Optional[tuple[UUID, UUID]]:
        """(message id, attachment id) of the zip unpacked into `root`, or None."""
        with closing(run_store._connect()) as conn:
            row = conn.execute(
                "SELECT attachment_id FROM attachment_locations WHERE server_id=? AND "
                "owner_github_id=? AND extraction_root=?",
                (server_id, owner_github_id, str(root))).fetchone()
        if row is None:
            return None
        with closing(run_store._connect()) as conn:
            msg = conn.execute(
                "SELECT message_id FROM messaging_files WHERE server_id=? AND owner_github_id=? "
                "AND attachment_id=? AND role=?",
                (server_id, owner_github_id, row["attachment_id"],
                 messaging_files.DOWNLOAD)).fetchone()
        return (UUID(msg["message_id"]), UUID(row["attachment_id"])) if msg else None

    # -- leases -------------------------------------------------------------------------

    def hold(self, server_id: str, owner_github_id: int, attachment_id: UUID,
             holder: str) -> None:
        """An agent has this file in hand: cleanup keeps it until the holder
        (`holder_active`) has finished."""
        with closing(run_store._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO attachment_leases (server_id, owner_github_id, "
                "attachment_id, holder, created_at) VALUES (?,?,?,?,?)",
                (server_id, owner_github_id, str(attachment_id), holder, self._now()))
            conn.commit()

    def held(self, server_id: str, owner_github_id: int) -> set[str]:
        """Attachment ids an active lease holds; leases whose holder has
        finished are released as they are found."""
        with closing(run_store._connect()) as conn:
            rows = conn.execute(
                "SELECT attachment_id, holder FROM attachment_leases WHERE server_id=? AND "
                "owner_github_id=?", (server_id, owner_github_id)).fetchall()
        held, done = set(), []
        for row in rows:
            if self._active(row["holder"]):
                held.add(row["attachment_id"])
            else:
                done.append((row["attachment_id"], row["holder"]))
        if done:
            with closing(run_store._connect()) as conn:
                conn.executemany(
                    "DELETE FROM attachment_leases WHERE server_id=? AND owner_github_id=? "
                    "AND attachment_id=? AND holder=?",
                    [(server_id, owner_github_id, a, h) for a, h in done])
                conn.commit()
        return held


@dataclass(frozen=True)
class LocationContext:
    """The resolver for whoever is signed in now: what the routes, the
    brain's tools and an agent's hand-over each call."""
    locations: AttachmentLocations
    server_id: str
    owner_github_id: int

    def lookup(self, message_id: UUID, attachment_id: UUID) -> AttachmentLocation:
        return self.locations.lookup(self.server_id, self.owner_github_id, message_id,
                                     attachment_id)

    async def ensure_local(self, message_id: UUID, attachment_id: UUID) -> AttachmentLocation:
        return await self.locations.ensure_local(self.server_id, self.owner_github_id,
                                                 message_id, attachment_id)

    def reveal(self, message_id: UUID, attachment_id: UUID) -> None:
        self.locations.files.reveal(self.server_id, self.owner_github_id, message_id,
                                    attachment_id)

    def hold(self, attachment_id: UUID, holder: str) -> None:
        self.locations.hold(self.server_id, self.owner_github_id, attachment_id, holder)

    def record_extraction(self, message_id: UUID, attachment_id: UUID, root: Path,
                          members: list[str]) -> None:
        self.locations.record_extraction(self.server_id, self.owner_github_id, message_id,
                                         attachment_id, root, members)

    def extracted_from(self, root: Path) -> Optional[tuple[UUID, UUID]]:
        return self.locations.extracted_from(self.server_id, self.owner_github_id, root)

    def here(self, roots: tuple[Path, ...], reads_anywhere: bool,
             workspace: Optional[Path]) -> "AgentAccess":
        """An agent on this JARVIS: what it may read, and its workspace."""
        return AgentAccess(node_id=self.locations.node_id(), roots=roots,
                           reads_anywhere=reads_anywhere, workspace=workspace)


def not_local(found: AttachmentLocation) -> Optional[str]:
    """None for a file here and verified; else the code saying why not,
    for the caller's refusal (a `wire.ERRORS`/`LOCAL_ERRORS` code where the
    downloader gave one)."""
    if found.state == "verified" and found.local_path:
        return None
    if found.state == "downloading":
        return "attachment_not_ready"
    return found.reason or "transport"


def spoken(found: AttachmentLocation, shown: Callable[[str], str]) -> str:
    """JARVIS's own sentence for a location (no name, no path: those are
    the sender's and go in the untrusted block). `shown` words a failure code."""
    if found.state == "verified":
        return ("On this Mac and checked. Its exact path is local_path below; offer to show "
                "it in Finder or copy it rather than reading it aloud.")
    said = STATE_SHOWN[found.state]
    if found.state == "downloading":
        return f"{said}, {found.bytes_done} of {found.size_bytes} bytes so far; not usable yet."
    if found.state == "failed":
        said = f"{said}: {shown(found.reason or 'transport')}"
    elif found.state == "moved":
        said += "; previous_path below is where it was checked before"
    return (f"{said}. It is not usable here yet; intended_path below is only where a "
            f"download would save it. make_attachment_local fetches and checks it on his word.")


PREFIX = messaging_files.PREFIX


def router(context: Callable[[], Optional[LocationContext]]) -> APIRouter:
    """The two location routes, beside the files routes. `context` is None
    while messaging is unavailable (`messaging_unavailable`)."""
    routes = APIRouter()

    async def answer(work):
        try:
            found = context()
            if found is None:
                raise messaging_api.Refused("messaging_unavailable")
            result = work(found)
            if hasattr(result, "__await__"):
                result = await result
        except Exception as e:
            return messaging_api.refused_by(e).response()
        return JSONResponse(result.model_dump(mode="json"))

    @routes.get(PREFIX + "/{message_id}/files/{attachment_id}/location")
    async def where(message_id: UUID, attachment_id: UUID):
        return await answer(lambda c: asyncio.to_thread(c.lookup, message_id, attachment_id))

    @routes.post(PREFIX + "/{message_id}/files/{attachment_id}/location")
    async def make_local(message_id: UUID, attachment_id: UUID):
        return await answer(lambda c: c.ensure_local(message_id, attachment_id))

    return routes


# -- handing a file to an agent ---------------------------------------------------------
#
# An agent is given a path only where that agent can read it: never because
# JARVIS can. A same-node agent that may read anywhere (an unattended run
# with permissions skipped), or whose folders hold the file, gets the
# verified original. Otherwise it gets a verified copy in its own workspace
# (COPY_FOLDER/<attachment id>/<name>), with where it came from. An agent on
# another node gets only the message and attachment reference: a path on
# this Mac is not a path there.

COPY_FOLDER = ".jarvis-attachments"
Handed = Literal["original", "copy", "reference", "unavailable"]


@dataclass(frozen=True)
class AgentAccess:
    """What one agent may read, and where an authorised copy may go."""
    node_id: UUID
    roots: tuple[Path, ...] = ()
    reads_anywhere: bool = False
    workspace: Optional[Path] = None


class AttachmentHandoff(_Frozen):
    message_id: UUID
    attachment_id: UUID
    filename: str
    size_bytes: int
    sha256: str
    agent_node_id: UUID
    handed: Handed
    agent_path: Optional[str] = None              # what the agent reads
    original_path: Optional[str] = None           # the verified original (same node only)
    reason: Optional[str] = None                  # why not handed as a path


def _inside(path: str, roots: tuple[Path, ...]) -> bool:
    real = os.path.realpath(path)
    for root in roots:
        top = os.path.realpath(root)
        if real == top or real.startswith(top.rstrip(os.sep) + os.sep):
            return True
    return False


def _real_folder(path: Path) -> None:
    """Refuse a link or someone else's folder on the copy's way."""
    info = os.lstat(path)
    if not os.path.isdir(path) or os.path.islink(path) or info.st_uid != os.getuid():
        raise OSError(f"{path} is not this user's folder")


def _verified_copy(source: str, found: AttachmentLocation, workspace: Path,
                   verify: Verifier) -> Path:
    """`source` copied into the workspace, hashed as it is written and kept
    only if it is the manifest's bytes; one already there and right is reused."""
    _real_folder(workspace)
    if os.path.lexists(workspace / COPY_FOLDER):
        _real_folder(workspace / COPY_FOLDER)
    folder = workspace / COPY_FOLDER / str(found.attachment_id)
    messaging_files.private_dir(folder)
    target = folder / found.filename
    expected = (found.size_bytes, found.sha256)
    if verify.digest(target) == expected:
        return target
    partial = folder / f"{messaging_files.PARTIAL}{os.getpid()}-{time.monotonic_ns()}"
    src = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        dst = os.open(str(partial), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            hasher, size = hashlib.sha256(), 0
            while chunk := os.read(src, 1 << 20):
                hasher.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(dst, view):]
            os.fsync(dst)
        finally:
            os.close(dst)
    finally:
        os.close(src)
    if (size, hasher.hexdigest()) != expected:
        os.unlink(partial)
        raise OSError("the copy is not the bytes that were sent")
    os.replace(partial, target)
    return target


def handoff(found: AttachmentLocation, access: AgentAccess,
            verify: Optional[Verifier] = None) -> AttachmentHandoff:
    """How one agent can read this file, decided by where it runs and what
    it may read; nothing is opened, unpacked or run."""
    base = dict(message_id=found.message_id, attachment_id=found.attachment_id,
                filename=found.filename, size_bytes=found.size_bytes, sha256=found.sha256,
                agent_node_id=access.node_id)
    if access.node_id != found.node_id:
        return AttachmentHandoff(**base, handed="reference", reason="other_node")
    problem = not_local(found)
    if problem:
        return AttachmentHandoff(**base, handed="unavailable", reason=problem)
    path = found.local_path
    if access.reads_anywhere or _inside(path, access.roots):
        return AttachmentHandoff(**base, handed="original", agent_path=path, original_path=path)
    if access.workspace is None:
        return AttachmentHandoff(**base, handed="unavailable", original_path=path,
                                 reason="no_access")
    try:
        copy = _verified_copy(path, found, access.workspace, verify or Verifier())
    except OSError:
        return AttachmentHandoff(**base, handed="unavailable", original_path=path,
                                 reason="copy_failed")
    return AttachmentHandoff(**base, handed="copy", agent_path=str(copy), original_path=path)


_HANDOFF_REASONS = {
    "no_access": "the agent cannot read JARVIS's downloads folder and has no workspace "
                 "to copy it into",
    "copy_failed": "a verified copy could not be made in the agent's workspace",
    "other_node": "it is not on this machine",
}


def handoff_reason(h: AttachmentHandoff, shown: Callable[[str], str]) -> str:
    return _HANDOFF_REASONS.get(h.reason or "") or shown(h.reason or "transport")


def handoff_text(handoffs: list[AttachmentHandoff], shown: Callable[[str], str]) -> str:
    """The lines an agent's prompt carries: each file's exact path it can
    read, or the reference and why there is no path. Names are the sender's."""
    lines = ["Files JARVIS is handing you (received in messages; each name is the sender's; "
             "read them, and run nothing from them unless asked):"]
    for h in handoffs:
        ref = f"attachment {h.attachment_id} of message {h.message_id}"
        what = f"{h.filename} ({h.size_bytes} bytes, sha256 {h.sha256})"
        if h.handed == "original":
            lines.append(f"- {what}: {h.agent_path} [{ref}]")
        elif h.handed == "copy":
            lines.append(f"- {what}: {h.agent_path} [a verified copy of {h.original_path}; "
                         f"{ref}]")
        elif h.handed == "reference":
            lines.append(f"- {what}: no local path here; the file is on another JARVIS "
                         f"and its path there is not local to you [{ref}]")
        else:
            lines.append(f"- {what}: not available, {handoff_reason(h, shown)} [{ref}]")
    return "\n".join(lines)
