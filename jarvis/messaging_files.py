"""Files for human messages on this JARVIS: the owner's chosen files as
private immutable snapshots, their upload to the team server once a
revision is approved, received files downloaded and verified, and the
three page routes for them. `FileSnapshots` is the production
`messaging_ports.AttachmentSnapshots`.

Sending. A file the owner picks is streamed by the page to
`POST /api/team/messages/drafts/{message_id}/files` and copied, hashed as it
is written, into `data_paths.messaging_snapshots_dir()` (owner-only
permissions, written under a partial name and renamed only when complete);
its record is kept only once the copy exists. The manifest entry
(`AttachmentRef`) carries the normalized display name, the media type hint,
the size and the SHA-256, never a local path. What is reviewed, approved
and sent is that copy: the original file is never read again, a changed
original changes nothing, and a copy that is gone or no longer matches is
`SnapshotMissing`, never silently replaced. Choosing the file again makes a
new attachment, so a new manifest, a new digest and a new review.

`ensure_uploaded` runs before each submit of an approved revision: each
file is created on the team server (bound to its message, size and hash),
streamed from its copy from byte 0 and finalized; one already verified
there is not sent again. At most `UPLOAD_STREAMS_MAX` go at once.

Receiving. A file is downloaded only when the owner asks, into a private
partial file, counted and hashed as it arrives, and moved under
`data_paths.messaging_downloads_dir()` only when its size and hash are the
manifest's, and with the macOS quarantine mark a browser download
carries (`quarantine`). JARVIS never opens a received file: the owner's one
action on a verified download is Reveal in Finder (`open -R`), which shows
it without running it. A cancelled download keeps the message and is offered again.

File states (`messaging_ports.FileState`): sending, staged -> uploading ->
available (verified on the team server), or failed (with a code) or
missing (the copy is gone); receiving, available -> downloading ->
verified, or failed. The send's own state is separate (delivery's).

Records: one table, `messaging_files`, in the shared database, one row per
(server, owner, attachment, role), role `snapshot` (a file this owner
chose) or `download` (a file this owner received). Nothing else of a file
is stored, and never its content.

Cleanup (`cleanup`): partial files older than PARTIAL_AFTER_SEC, and
snapshots no longer referenced by an editable draft or an approved send
still unresolved (with a grace period for a file just picked whose draft
is not yet saved). A snapshot an approved pending send names is never
removed.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import stat
import subprocess
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, Iterable, Optional
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from jarvis import data_paths, run_store
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire

log = logging.getLogger("jarvis.messaging_files")

PREFIX = "/api/team/messages"
SNAPSHOT = "snapshot"
DOWNLOAD = "download"
PARTIAL = ".partial-"
# A partial file this old belongs to a transfer that died (uploads give up
# after UPLOAD_IDLE_TIMEOUT_SEC without a byte).
PARTIAL_AFTER_SEC = 3600
# A snapshot no draft names yet: the page saves the draft after the file
# is copied, so a fresh one is kept this long before it counts as abandoned.
SNAPSHOT_GRACE_SEC = 3600
# Progress is written at most this often (the state changes always are).
PROGRESS_EVERY_SEC = 1.0
ON_SERVER = ("verified", "pinned", "published")

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS messaging_files (
        server_id       TEXT NOT NULL,
        owner_github_id INTEGER NOT NULL,
        attachment_id   TEXT NOT NULL,
        role            TEXT NOT NULL,
        message_id      TEXT NOT NULL,
        filename        TEXT NOT NULL,
        media_type      TEXT NOT NULL,
        size_bytes      INTEGER NOT NULL,
        sha256          TEXT NOT NULL,
        state           TEXT NOT NULL,
        bytes_done      INTEGER NOT NULL DEFAULT 0,
        failure         TEXT,
        created_at      REAL NOT NULL,
        updated_at      REAL NOT NULL,
        PRIMARY KEY (server_id, owner_github_id, attachment_id, role)
    );
    CREATE INDEX IF NOT EXISTS messaging_files_by_message
        ON messaging_files (server_id, owner_github_id, message_id);
"""


def init_db() -> None:
    """This module's table, in the same database as runs and messages; made
    once, with the FileSnapshots that uses it."""
    with closing(run_store._connect()) as conn:
        conn.executescript(_SCHEMA)


class Corrupt(Exception):
    """Downloaded bytes that are not the manifest's."""


def private_dir(path: Path) -> Path:
    """`path` and its missing parents, each made owner-only; refused if it
    is a link or someone else's."""
    missing = [p for p in (path, *path.parents) if not p.exists()]
    for p in reversed(missing):
        p.mkdir(mode=0o700, exist_ok=True)
        os.chmod(p, 0o700)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError(f"{path} is not this user's directory")
    os.chmod(path, 0o700)
    return path


def _open_new(path: Path) -> int:
    return os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _unlink(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _hash_file(path: Path) -> Optional[tuple[int, str]]:
    """(size, sha256) of a regular file, never through a link; None if it
    is not there or not a regular file."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        hasher, size = hashlib.sha256(), 0
        while chunk := os.read(fd, wire.UPLOAD_CHUNK_BYTES):
            hasher.update(chunk)
            size += len(chunk)
        return size, hasher.hexdigest()
    finally:
        os.close(fd)


def reveal_in_finder(path: Path) -> None:
    """Show the file selected in a Finder window. `-R` reveals; it never
    opens or runs the file."""
    subprocess.run(["open", "-R", str(path)], check=False, timeout=10,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


QUARANTINE_ATTRIBUTE = "com.apple.quarantine"


def quarantine(path: Path) -> None:
    """Mark a received file as come from someone else, as a browser marks a
    download: opened from Finder, macOS asks first (Gatekeeper). Raises if
    the mark could not be set."""
    value = f"0081;{int(time.time()):x};JARVIS;"
    subprocess.run(["/usr/bin/xattr", "-w", QUARANTINE_ATTRIBUTE, value, str(path)],
                   check=True, timeout=10, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def local_files(refs: Iterable[wire.AttachmentRef], known: list[ports.LocalFile],
                default: ports.FileState) -> list[ports.LocalFile]:
    """A manifest's files as the page shows them, in manifest order: what
    this JARVIS knows of each (`FileSnapshots.files`), else `default`
    (`available` for a message: on the team server, not here)."""
    by_id = {f.attachment_id: f for f in known}
    return [by_id.get(r.attachment_id) or ports.LocalFile(attachment_id=r.attachment_id,
                                                          state=default) for r in refs]


class FileSnapshots:
    """`AttachmentSnapshots` over the shared database and the two private
    folders. `store` is the messaging store (drafts, approved sends and
    messages are read from it, never written); `transport` gives the
    current pairing's ServeTransport; `on_change` is told after any file
    state is committed (delivery turns it into the `messages` hint)."""

    def __init__(self, store: ports.LocalMessagingStore,
                 transport: Callable[[], ports.ServeTransport], *,
                 ids: ports.IdSource = ports.RandomIds(),
                 now: Callable[[], float] = time.time,
                 reveal: Callable[[Path], None] = reveal_in_finder,
                 quarantine: Callable[[Path], None] = quarantine,
                 on_change: Callable[[], None] = lambda: None,
                 snapshots_dir: Optional[Path] = None, downloads_dir: Optional[Path] = None):
        self.store, self.transport, self.reveal_path = store, transport, reveal
        self.quarantine = quarantine
        self.ids = ids
        self.now, self.on_change = now, on_change
        self._snapshots = snapshots_dir
        self._downloads = downloads_dir
        self._streams = asyncio.Semaphore(wire.UPLOAD_STREAMS_MAX)
        self._downloading: set[tuple[str, int, UUID]] = set()
        init_db()

    # -- places ----------------------------------------------------------------------

    def snapshots_dir(self) -> Path:
        return private_dir(self._snapshots or data_paths.messaging_snapshots_dir())

    def downloads_dir(self) -> Path:
        return private_dir(self._downloads or data_paths.messaging_downloads_dir())

    def snapshot_path(self, attachment_id: UUID) -> Path:
        return self.snapshots_dir() / str(attachment_id)

    def download_path(self, ref: wire.AttachmentRef) -> Path:
        """`<downloads>/<attachment id>/<the manifest's safe name>`: the name
        is the sender's, already free of paths and control characters."""
        return self.downloads_dir() / str(ref.attachment_id) / ref.filename

    # -- records -----------------------------------------------------------------------

    def _row(self, server_id: str, owner: int, attachment_id: UUID,
             role: str) -> Optional[sqlite3.Row]:
        with closing(run_store._connect()) as conn:
            return conn.execute(
                "SELECT * FROM messaging_files WHERE server_id=? AND owner_github_id=? "
                "AND attachment_id=? AND role=?",
                (server_id, owner, str(attachment_id), role)).fetchone()

    def _set(self, server_id: str, owner: int, attachment_id: UUID, role: str, *,
             state: str, bytes_done: Optional[int] = None, failure: Optional[str] = None,
             notify: bool = True) -> None:
        with closing(run_store._connect()) as conn:
            conn.execute(
                "UPDATE messaging_files SET state=?, failure=?, "
                "bytes_done=COALESCE(?, bytes_done), updated_at=? WHERE server_id=? "
                "AND owner_github_id=? AND attachment_id=? AND role=?",
                (state, failure, bytes_done, self.now(), server_id, owner, str(attachment_id),
                 role))
            conn.commit()
        if notify:
            self.on_change()

    def _insert(self, server_id: str, owner: int, ref: wire.AttachmentRef, message_id: UUID,
                role: str, state: str) -> None:
        now = self.now()
        with closing(run_store._connect()) as conn:
            conn.execute(
                "INSERT INTO messaging_files (server_id, owner_github_id, attachment_id, role, "
                "message_id, filename, media_type, size_bytes, sha256, state, bytes_done, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?) "
                "ON CONFLICT (server_id, owner_github_id, attachment_id, role) DO UPDATE SET "
                "state=excluded.state, bytes_done=0, failure=NULL, updated_at=excluded.updated_at",
                (server_id, owner, str(ref.attachment_id), role, str(message_id), ref.filename,
                 ref.media_type, ref.size_bytes, ref.sha256, state, now, now))
            conn.commit()
        self.on_change()

    @staticmethod
    def _ref(row: sqlite3.Row) -> wire.AttachmentRef:
        return wire.AttachmentRef(attachment_id=UUID(row["attachment_id"]),
                                  filename=row["filename"], media_type=row["media_type"],
                                  size_bytes=row["size_bytes"], sha256=row["sha256"])

    @staticmethod
    def _local(row: sqlite3.Row) -> ports.LocalFile:
        return ports.LocalFile(attachment_id=UUID(row["attachment_id"]), state=row["state"],
                               bytes_done=row["bytes_done"], failure=row["failure"])

    # -- snapshots ------------------------------------------------------------------------

    async def snapshot(self, server_id: str, owner_github_id: int, message_id: UUID,
                       filename: str, media_type: Optional[str],
                       chunks: AsyncIterator[bytes]) -> wire.AttachmentRef:
        """Copy, hash and name one chosen file. More than FILE_MAX_BYTES is
        `too_large`, whatever the request said its length was; a failed
        copy leaves nothing behind."""
        attachment_id = self.ids.new_id()
        folder = self.snapshots_dir()
        partial = folder / f"{PARTIAL}{attachment_id}"
        fd = _open_new(partial)
        hasher, size = hashlib.sha256(), 0
        try:
            async for chunk in chunks:
                size += len(chunk)
                if size > wire.FILE_MAX_BYTES:
                    raise wire.ContractError("too_large")
                hasher.update(chunk)
                _write_all(fd, chunk)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            _unlink(partial)
            raise
        os.close(fd)
        os.replace(partial, folder / str(attachment_id))
        ref = wire.AttachmentRef(attachment_id=attachment_id,
                                 filename=wire.normalize_filename(filename),
                                 media_type=wire.normalize_media_type(media_type),
                                 size_bytes=size, sha256=hasher.hexdigest())
        self._insert(server_id, owner_github_id, ref, message_id, SNAPSHOT, "staged")
        return ref

    def refs(self, server_id: str, owner_github_id: int, message_id: UUID,
             attachment_ids: list[UUID]) -> list[wire.AttachmentRef]:
        """The manifest entries of files snapshotted for this draft, in the
        order given (a draft save's `attachment_ids`). A file this JARVIS
        has no copy of for this draft, or whose copy is missing, is
        SnapshotMissing: attach it again."""
        refs = []
        for attachment_id in attachment_ids:
            row = self._row(server_id, owner_github_id, attachment_id, SNAPSHOT)
            if row is None or row["message_id"] != str(message_id) or row["state"] == "missing":
                raise ports.SnapshotMissing(attachment_id)
            refs.append(self._ref(row))
        return refs

    def _check(self, server_id: str, owner: int, ref: wire.AttachmentRef) -> None:
        """The copy is still exactly the manifest's, else it is missing."""
        if _hash_file(self.snapshot_path(ref.attachment_id)) != (ref.size_bytes, ref.sha256):
            if self._row(server_id, owner, ref.attachment_id, SNAPSHOT) is not None:
                self._set(server_id, owner, ref.attachment_id, SNAPSHOT, state="missing",
                          failure="snapshot_missing")
            raise ports.SnapshotMissing(ref.attachment_id)

    def _manifest(self, server_id: str, owner: int, message_id: UUID,
                  content_revision: int) -> list[wire.AttachmentRef]:
        approved = self.store.approved(server_id, owner, message_id, content_revision)
        if approved is not None:
            return list(approved.request.attachments)
        draft = self.store.draft(server_id, owner, message_id)
        if draft is None or draft.send.content_revision != content_revision:
            raise wire.ContractError("draft_not_found")
        return list(draft.attachments)

    def verified_attachment_manifest(self, server_id: str, owner_github_id: int,
                                     message_id: UUID,
                                     content_revision: int) -> list[wire.AttachmentRef]:
        """The revision's files (its approved envelope's, else the draft's at
        that revision), each re-hashed from its copy."""
        refs = self._manifest(server_id, owner_github_id, message_id, content_revision)
        for ref in refs:
            self._check(server_id, owner_github_id, ref)
        return refs

    # -- uploading --------------------------------------------------------------------------

    async def _read_copy(self, server_id: str, owner: int,
                         ref: wire.AttachmentRef) -> AsyncIterator[bytes]:
        """The copy in bounded chunks, hashed again as it is read: bytes that
        changed since the check end the upload as SnapshotMissing."""
        try:
            fd = os.open(str(self.snapshot_path(ref.attachment_id)), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            raise ports.SnapshotMissing(ref.attachment_id) from None
        hasher, size, written = hashlib.sha256(), 0, time.monotonic()
        try:
            while chunk := os.read(fd, wire.UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                hasher.update(chunk)
                yield chunk
                if time.monotonic() - written >= PROGRESS_EVERY_SEC:
                    written = time.monotonic()
                    self._set(server_id, owner, ref.attachment_id, SNAPSHOT,
                              state="uploading", bytes_done=size)
        finally:
            os.close(fd)
        if (size, hasher.hexdigest()) != (ref.size_bytes, ref.sha256):
            raise ports.SnapshotMissing(ref.attachment_id)

    async def _upload(self, server_id: str, owner: int, message_id: UUID,
                      ref: wire.AttachmentRef) -> None:
        def set_state(state: str, **kw) -> None:
            self._set(server_id, owner, ref.attachment_id, SNAPSHOT, state=state, **kw)

        async with self._streams:
            transport = self.transport()
            try:
                status = await transport.create_attachment(wire.AttachmentCreate(
                    attachment_id=ref.attachment_id, message_id=message_id,
                    size_bytes=ref.size_bytes, sha256=ref.sha256))
                if status.state not in ON_SERVER:
                    if status.state == "expired":     # cleanup's; gone soon, then again
                        raise ports.TransportError("attachment_not_ready", status=409)
                    set_state("uploading", bytes_done=0)
                    await transport.upload_attachment(
                        ref.attachment_id, self._read_copy(server_id, owner, ref))
                    status = await transport.finalize_attachment(ref.attachment_id)
                    if status.state not in ON_SERVER:
                        raise ports.TransportError("attachment_not_ready", status=409)
            except ports.SnapshotMissing:
                set_state("missing", failure="snapshot_missing")
                raise
            except ports.TransportError as e:
                set_state("failed", failure=e.code)
                raise
            except asyncio.CancelledError:
                set_state("staged")
                raise
            except Exception:
                set_state("failed", failure="transport")
                raise
            set_state("available", bytes_done=ref.size_bytes)

    async def ensure_uploaded(self, server_id: str, owner_github_id: int, message_id: UUID,
                              content_revision: int) -> list[wire.AttachmentRef]:
        """Every file of the approved revision verified on the team server,
        from its copy. The first failure is raised once every file has
        finished trying (SnapshotMissing, or TransportError: delivery decides
        whether to retry, as for the message itself)."""
        refs = self.verified_attachment_manifest(server_id, owner_github_id, message_id,
                                                 content_revision)
        results = await asyncio.gather(
            *(self._upload(server_id, owner_github_id, message_id, ref) for ref in refs),
            return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return refs

    # -- what the page sees ----------------------------------------------------------------

    def files(self, server_id: str, owner_github_id: int,
              message_id: UUID) -> list[ports.LocalFile]:
        """What this JARVIS knows of the message's files: a download where
        there is one, else the snapshot (a file this owner sent). Files it
        knows nothing of are not listed; `local_files` fills them in."""
        with closing(run_store._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM messaging_files WHERE server_id=? AND owner_github_id=? "
                "AND message_id=? ORDER BY created_at, role DESC",
                (server_id, owner_github_id, str(message_id))).fetchall()
        known: dict[str, sqlite3.Row] = {}
        for row in rows:
            if row["attachment_id"] not in known or row["role"] == DOWNLOAD:
                known[row["attachment_id"]] = row
        return [self._local(row) for row in known.values()]

    # -- downloading -------------------------------------------------------------------------

    def _manifest_entry(self, server_id: str, owner: int, message_id: UUID,
                        attachment_id: UUID) -> wire.AttachmentRef:
        held = self.store.message(server_id, owner, message_id)
        ref = next((a for a in (held.message.attachments if held else [])
                    if a.attachment_id == attachment_id), None)
        if ref is None:
            raise wire.ContractError("attachment_not_found")
        return ref

    def _download_file(self, server_id: str, owner: int, attachment_id: UUID) -> ports.LocalFile:
        row = self._row(server_id, owner, attachment_id, DOWNLOAD)
        return self._local(row) if row else ports.LocalFile(attachment_id=attachment_id,
                                                            state="available")

    async def download(self, server_id: str, owner_github_id: int, message_id: UUID,
                       attachment_id: UUID) -> ports.LocalFile:
        """Fetch, verify and keep one received file. A file already verified
        is not fetched again, and one already downloading is not fetched
        twice. Wrong bytes, a refusal or no answer leave no file and a
        `failed` state to retry from; cancelling leaves it `available`."""
        ref = self._manifest_entry(server_id, owner_github_id, message_id, attachment_id)
        key = (server_id, owner_github_id, attachment_id)
        current = self._download_file(server_id, owner_github_id, attachment_id)
        if key in self._downloading or (
                current.state == "verified" and _hash_file(self.download_path(ref))
                == (ref.size_bytes, ref.sha256)):
            return current
        self._downloading.add(key)
        try:
            self._insert(server_id, owner_github_id, ref, message_id, DOWNLOAD, "downloading")
            return await self._fetch(server_id, owner_github_id, message_id, ref)
        finally:
            self._downloading.discard(key)

    async def _fetch(self, server_id: str, owner: int, message_id: UUID,
                     ref: wire.AttachmentRef) -> ports.LocalFile:
        def set_state(state: str, **kw) -> ports.LocalFile:
            self._set(server_id, owner, ref.attachment_id, DOWNLOAD, state=state, **kw)
            return self._download_file(server_id, owner, ref.attachment_id)

        final = self.download_path(ref)
        folder = private_dir(final.parent)
        partial = folder / f"{PARTIAL}{uuid.uuid4()}"
        fd = _open_new(partial)
        hasher, size, written = hashlib.sha256(), 0, time.monotonic()
        try:
            async for chunk in self.transport().download_attachment(message_id,
                                                                    ref.attachment_id):
                size += len(chunk)
                if size > ref.size_bytes:
                    raise Corrupt()
                hasher.update(chunk)
                _write_all(fd, chunk)
                if time.monotonic() - written >= PROGRESS_EVERY_SEC:
                    written = time.monotonic()
                    set_state("downloading", bytes_done=size)
            if (size, hasher.hexdigest()) != (ref.size_bytes, ref.sha256):
                raise Corrupt()
            os.fsync(fd)
        except Corrupt:
            os.close(fd)
            _unlink(partial)
            return set_state("failed", bytes_done=0, failure="attachment_mismatch")
        except ports.TransportError as e:
            os.close(fd)
            _unlink(partial)
            return set_state("failed", bytes_done=0, failure=e.code)
        except asyncio.CancelledError:
            os.close(fd)
            _unlink(partial)
            set_state("available", bytes_done=0)
            raise
        except BaseException:
            os.close(fd)
            _unlink(partial)
            set_state("failed", bytes_done=0, failure="transport")
            raise
        os.close(fd)
        try:
            self.quarantine(partial)
        except (OSError, subprocess.SubprocessError) as e:
            _unlink(partial)
            log.warning("received file not marked as downloaded (%s); not kept",
                        type(e).__name__)
            return set_state("failed", bytes_done=0, failure="quarantine")
        os.replace(partial, final)
        return set_state("verified", bytes_done=size)

    def reveal(self, server_id: str, owner_github_id: int, message_id: UUID,
               attachment_id: UUID) -> None:
        """Show a verified download in Finder, after checking its bytes are
        still the manifest's. Never opens it."""
        ref = self._manifest_entry(server_id, owner_github_id, message_id, attachment_id)
        if self._download_file(server_id, owner_github_id, attachment_id).state != "verified":
            raise wire.ContractError("attachment_not_ready")
        path = self.download_path(ref)
        if _hash_file(path) != (ref.size_bytes, ref.sha256):
            self._set(server_id, owner_github_id, attachment_id, DOWNLOAD, state="failed",
                      failure="attachment_mismatch")
            raise wire.ContractError("attachment_mismatch")
        self.reveal_path(path)

    # -- cleanup -----------------------------------------------------------------------------

    def _still_needed(self, server_id: str, owner: int) -> set[str]:
        """Attachment ids an editable draft or an unresolved approved send
        names."""
        needed: set[str] = set()
        for send in self.store.unresolved_sends(server_id, owner):
            approved = self.store.approved(server_id, owner, send.message_id,
                                           send.content_revision)
            if approved is not None:
                needed.update(str(a.attachment_id) for a in approved.request.attachments)
        return needed

    def cleanup(self, server_id: str, owner_github_id: int) -> int:
        """Remove dead partial files and this owner's abandoned snapshots;
        how many files were removed."""
        removed = 0
        cutoff = self.now() - PARTIAL_AFTER_SEC
        for folder in (self.snapshots_dir(), self.downloads_dir()):
            for path in [*folder.glob(PARTIAL + "*"), *folder.glob("*/" + PARTIAL + "*")]:
                try:
                    if os.lstat(path).st_mtime < cutoff:
                        os.unlink(path)
                        removed += 1
                except FileNotFoundError:
                    continue
        needed = self._still_needed(server_id, owner_github_id)
        with closing(run_store._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM messaging_files WHERE server_id=? AND owner_github_id=? "
                "AND role=?", (server_id, owner_github_id, SNAPSHOT)).fetchall()
        for row in rows:
            if row["attachment_id"] in needed or self.now() - row["created_at"] < \
                    SNAPSHOT_GRACE_SEC:
                continue
            draft = self.store.draft(server_id, owner_github_id, UUID(row["message_id"]))
            if draft is not None and draft.send.state != "accepted" and any(
                    str(a.attachment_id) == row["attachment_id"] for a in draft.attachments):
                continue
            _unlink(self.snapshots_dir() / row["attachment_id"])
            with closing(run_store._connect()) as conn:
                conn.execute(
                    "DELETE FROM messaging_files WHERE server_id=? AND owner_github_id=? "
                    "AND attachment_id=? AND role=?",
                    (server_id, owner_github_id, row["attachment_id"], SNAPSHOT))
                conn.commit()
            removed += 1
        return removed


# -- the page's routes ------------------------------------------------------------------------

@dataclass(frozen=True)
class FilesContext:
    """What a files route works with: the service and whose messaging this is."""
    files: FileSnapshots
    server_id: str
    owner_github_id: int


def refusal(code: str) -> JSONResponse:
    """The team server's error body, with the code's status."""
    spec = wire.ERRORS.get(code) or ports.LOCAL_ERRORS.get(code) or ports.LOCAL_ERRORS["transport"]
    return JSONResponse({"error": {"code": code, "message": spec.shown}},
                        status_code=spec.status)


async def _answer(call):
    try:
        return await call
    except ports.SnapshotMissing:
        return refusal("snapshot_missing")
    except wire.ContractError as e:
        return refusal(e.code)
    except ports.TransportError as e:
        return refusal(e.code if e.code in wire.ERRORS else "transport")


def router(context: Callable[[], Optional[FilesContext]]) -> APIRouter:
    """The three file routes, for delivery's messaging_api to mount behind
    the web boundary. `context` is None while messaging is unavailable
    (`messaging_unavailable`)."""
    routes = APIRouter()

    def ctx() -> FilesContext:
        found = context()
        if found is None:
            raise wire.ContractError("messaging_unavailable")
        return found

    @routes.post(PREFIX + "/drafts/{message_id}/files", status_code=201)
    async def add_file(request: Request, message_id: UUID):
        async def go():
            c = ctx()
            if c.files.store.draft(c.server_id, c.owner_github_id, message_id) is None:
                raise wire.ContractError("draft_not_found")
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > wire.FILE_MAX_BYTES:
                raise wire.ContractError("too_large")
            ref = await c.files.snapshot(
                c.server_id, c.owner_github_id, message_id,
                unquote(request.headers.get("x-filename", "")),
                request.headers.get("content-type"), request.stream())
            return ports.LocalFile(attachment_id=ref.attachment_id,
                                   state="staged").model_dump(mode="json")
        result = await _answer(go())
        return result if isinstance(result, Response) else JSONResponse(result, status_code=201)

    @routes.post(PREFIX + "/{message_id}/files/{attachment_id}/download")
    async def download_file(message_id: UUID, attachment_id: UUID):
        async def go():
            c = ctx()
            got = await c.files.download(c.server_id, c.owner_github_id, message_id,
                                         attachment_id)
            return got.model_dump(mode="json")
        return await _answer(go())

    @routes.post(PREFIX + "/{message_id}/files/{attachment_id}/reveal", status_code=204)
    async def reveal_file(message_id: UUID, attachment_id: UUID):
        async def go():
            c = ctx()
            await asyncio.to_thread(c.files.reveal, c.server_id, c.owner_github_id, message_id,
                                    attachment_id)
            return Response(status_code=204)
        return await _answer(go())

    return routes
