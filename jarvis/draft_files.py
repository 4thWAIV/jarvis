"""Files on this Mac, named by their paths, added to a team-message draft:
the brain's `attach_message_files` and `draft_message`'s `files` both come
here, and from here into the same snapshot step as a file the page uploads
(`FileSnapshots.add_to_draft`) and the same writer as the page's save
(`LocalMessaging.write_draft`). Nothing here sends, uploads or approves.

    source_path   whether a named file may be attached: an absolute path to
                  one regular file, read by the folder catalog's FILES rule
                  (folder_policy.py) as if he had named its folder, so a
                  dot folder he names (`.agents/in-progress`) is his while
                  keys, credentials, unknown dotfiles, JARVIS's own data and
                  the provider homes stay closed. A work package's folder
                  (`data_paths.packages_dir()`) is the one part of JARVIS's
                  data that may be sent: its results go back to the sender.
    Source        the file held open from the check to the last byte,
                  reached from `/` one component at a time without
                  following a link (folder_readers.open_file), so the file
                  checked is the file read. Read in bounded chunks off the
                  event loop; one that changes while it is read is refused.
    DraftFileAttacher
                  a batch onto one draft at the exact revision last read:
                  every file copied first, then all of them added in one
                  save. A refusal, a failure or a cancellation before that
                  save leaves the draft as it was; the copies no draft names
                  are removed by `FileSnapshots.cleanup` after its grace
                  period. The save is made only if the team sign-in is still
                  the one the batch began under and the draft is still at
                  that revision and editable, so a retry after a lost answer
                  is a revision conflict, never a second copy of the files.

What a caller gets back is the saved draft: names, sizes and hashes, never
a file's content or the path of its private copy.
"""
from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Callable, Optional, Sequence
from uuid import UUID

from jarvis import data_paths, folder_policy, folder_readers, repo_read
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.folder_contract import FolderError, Reader
from jarvis.messaging_files import FileSnapshots

if TYPE_CHECKING:
    from jarvis.message_approvals import Identity
    from jarvis.messaging_client import Pairing

_ROOT = Path("/")


class NotAttached(Exception):
    """Why nothing was attached, in words he can hear."""


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _canonical(raw) -> Path:
    """The named file's folder resolved, the file itself not: a link named
    as the file is refused when it is opened."""
    if not isinstance(raw, str) or not raw:
        raise NotAttached("Give each file as its full path.")
    path = Path(raw)
    if not path.is_absolute():
        raise NotAttached(f"{raw!r} is not a full path.")
    if path.name in ("", ".", ".."):
        raise NotAttached(f"{raw} is not a file.")
    return Path(os.path.realpath(path.parent)) / path.name


def source_path(raw) -> Path:
    """The file `raw` names, as it will be read, if it may be attached;
    else NotAttached. Which rule refused is not said: telling a caller that
    is a probing oracle (repo_read.sensitive_reason)."""
    path = _canonical(raw)
    packages = Path(os.path.realpath(data_paths.packages_dir()))
    if _within(path, packages):
        allowed = path != packages and not repo_read.sensitive_reason(path.relative_to(packages))
    else:
        try:
            policy = folder_policy.policy_for(path.parent)
        except FolderError:
            allowed = False
        else:
            allowed = policy.reader == Reader.FILES and folder_policy.entry_visible(
                policy, path.parent, Path(path.name), is_dir=False)
    if not allowed:
        raise NotAttached(f"JARVIS does not send {path.name} from where it is.")
    return path


_OPEN_REFUSED = {
    "missing": "There is no file at {path}.",
    "access_denied": "macOS does not let JARVIS read {path}.",
    "unsupported": "{path} is not an ordinary file.",
    "forbidden": "{path} is a link or is reached through one; name the file itself.",
}


class Source:
    """One named file, held open from its check to its last byte."""

    def __init__(self, path: Path, fd: int, info: os.stat_result):
        self.path, self.info = path, info
        self._fd: Optional[int] = fd
        self._reading: Optional[asyncio.Future] = None

    @classmethod
    def open(cls, raw) -> "Source":
        """Blocking: the check, then the open by the same walk."""
        path = source_path(raw)
        try:
            fd, info = folder_readers.open_file(_ROOT, path.relative_to(_ROOT))
        except FolderError as e:
            raise NotAttached(_OPEN_REFUSED.get(e.code, _OPEN_REFUSED["missing"])
                              .format(path=path)) from None
        source = cls(path, fd, info)
        if info.st_size > wire.FILE_MAX_BYTES:
            source.close()
            raise NotAttached(f"{path.name} is larger than "
                              f"{ports.spoken_size(wire.FILE_MAX_BYTES)}, the most one file "
                              f"may be.")
        return source

    @property
    def identity(self) -> tuple[int, int]:
        return self.info.st_dev, self.info.st_ino

    @property
    def media_type(self) -> Optional[str]:
        return mimetypes.guess_type(self.path.name)[0]

    def _changed(self) -> NotAttached:
        return NotAttached(f"{self.path.name} changed while it was being copied; "
                           f"attach it again once it has finished changing.")

    async def chunks(self) -> AsyncIterator[bytes]:
        """Its bytes in upload-sized chunks, read off the event loop. More
        bytes than it had when it was opened, fewer, or a new size or
        modification time at the end, and it is refused, not copied half
        old and half new."""
        done = 0
        while True:
            self._reading = asyncio.ensure_future(asyncio.to_thread(
                os.pread, self._fd, wire.UPLOAD_CHUNK_BYTES, done))
            # Shielded: cancelling the copy must not mark the read finished
            # while its thread still holds the descriptor.
            chunk = await asyncio.shield(self._reading)
            self._reading = None
            if not chunk:
                break
            done += len(chunk)
            if done > self.info.st_size:
                raise self._changed()
            yield chunk
        after = os.fstat(self._fd)
        if done != self.info.st_size or (after.st_size, after.st_mtime_ns) != \
                (self.info.st_size, self.info.st_mtime_ns):
            raise self._changed()

    def close(self) -> None:
        """Close it now, or once a read still in its thread has finished, so
        a cancelled read never meets a descriptor reused by another open."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        reading, self._reading = self._reading, None
        if reading is not None and not reading.done():
            reading.add_done_callback(lambda _: os.close(fd))
        else:
            os.close(fd)


async def _open(raw) -> Source:
    """Source.open off the event loop. Cancelled while the open is in its
    thread, the file it opens is closed when it arrives."""
    opening = asyncio.ensure_future(asyncio.to_thread(Source.open, raw))
    try:
        return await asyncio.shield(opening)
    except asyncio.CancelledError:
        opening.add_done_callback(
            lambda f: f.cancelled() or f.exception() is not None or f.result().close())
        raise


# The one writer of a draft, bound to the brain: (the draft, the save) -> the
# saved draft, through LocalMessaging.write_draft as written by an agent.
SaveDraft = Callable[[UUID, ports.DraftSave], ports.Draft]


class DraftFileAttacher:
    """Named files onto one draft at an exact revision, all or none.
    `pairing` is the team sign-in now (Delivery.pairing): its server, owner
    and connection generation, so signing out and in again while files are
    copied refuses the save as surely as a switch to another team does."""

    def __init__(self, store: ports.LocalMessagingStore, files: FileSnapshots,
                 pairing: Callable[[], Optional["Pairing"]], save: SaveDraft):
        self.store, self.files, self.pairing, self.save = store, files, pairing, save

    def check(self, raw) -> Path:
        """Whether `raw` may be attached and can be read, before anything is
        saved (draft_message's files); `attach` opens and checks it again."""
        source = Source.open(raw)
        source.close()
        return source.path

    def _editable(self, who: "Identity", message_id: UUID, revision: int) -> ports.Draft:
        current = self.store.draft(*who.ns, message_id)
        if current is None:
            raise wire.ContractError("draft_not_found")
        if current.draft_revision != revision:
            raise wire.ContractError("revision_conflict", current)
        if current.send.state not in ports.SEND_EDITABLE:
            raise ports.SendRefused(current.send.state, "edit")
        return current

    async def attach(self, who: "Identity", message_id: UUID, expected_draft_revision: int,
                     paths: Sequence[str]) -> ports.Draft:
        """The draft saved with these files after the ones it has. Raises
        NotAttached, wire.ContractError (draft_not_found, revision_conflict
        with the draft as `latest`, too_large), ports.SendRefused or
        ports.SnapshotMissing, and then the draft is unchanged."""
        if isinstance(expected_draft_revision, bool) or \
                not isinstance(expected_draft_revision, int) or expected_draft_revision < 1:
            raise NotAttached("Give the draft revision you last read, as a number.")
        if not isinstance(paths, (list, tuple)) or not paths:
            raise NotAttached("Give the files to attach as a list of full paths.")
        began = self.pairing()
        if began is None or began.ns != who.ns:
            raise NotAttached("The team sign-in changed; read the draft again.")
        current = self._editable(who, message_id, expected_draft_revision)
        if len(current.attachments) + len(paths) > wire.ATTACHMENTS_MAX:
            raise NotAttached(f"A message holds at most {wire.ATTACHMENTS_MAX} files, and "
                              f"this draft has {len(current.attachments)} already.")
        total = sum(a.size_bytes for a in current.attachments)
        sources: list[Source] = []
        added: list[wire.AttachmentRef] = []
        try:
            for raw in paths:
                sources.append(await _open(raw))
            seen: dict[tuple[int, int], Path] = {}
            for source in sources:
                if source.identity in seen:
                    raise NotAttached(f"{source.path} is named twice.")
                seen[source.identity] = source.path
            if total + sum(s.info.st_size for s in sources) > wire.PACKAGE_MAX_BYTES:
                raise wire.ContractError("too_large")
            for source in sources:
                ref = await self.files.add_to_draft(*who.ns, message_id, source.path.name,
                                                    source.media_type, source.chunks())
                added.append(ref)
                total += ref.size_bytes
                if total > wire.PACKAGE_MAX_BYTES:
                    raise wire.ContractError("too_large")
        finally:
            for source in sources:
                source.close()
        # From here to the save nothing awaits: the sign-in and the draft
        # checked are the ones written.
        if self.pairing() != began:
            raise NotAttached("The team sign-in changed while the files were being copied.")
        current = self._editable(who, message_id, expected_draft_revision)
        return self.save(message_id, ports.DraftSave.keeping(
            current, attachment_ids=[*(a.attachment_id for a in current.attachments),
                                     *(a.attachment_id for a in added)]))
