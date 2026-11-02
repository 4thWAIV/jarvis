"""Reading a received ZIP safely: the one place JARVIS checks, lists, reads
and extracts an archive, used by work packages (work_packages.py) and by
ordinary bundles.

    open_archive   a file as a ZIP, or why it is not one
    list_members   every entry checked before anything is read or written,
                   macOS metadata set aside, and the one wrapper folder, if
                   the archive has one; one bad entry refuses the archive,
                   or, for read-only browsing (`per_entry`), is set aside
                   as refused while the rest stays readable
    read_member    one member's bytes, its budgets enforced on what is
                   actually decompressed, not on what the archive claims
    read_head      a member's first bytes, for telling its format
    extract        the members under one folder of the archive into a new
                   folder: written into a private staging folder beside it
                   and renamed into place only once every member is read,
                   removed on any failure

Nothing here runs anything, clears the quarantine mark or keeps execute
bits unless the caller asks (`keep_modes`, the work-package path only).
"""
from __future__ import annotations

import os
import shutil
import stat
import time
import unicodedata
import uuid
import zipfile
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Optional

# Platform metadata a ZIP made on a Mac carries, set aside when looking for
# a wrapper folder and never extracted.
METADATA_FOLDER = "__MACOSX"
METADATA_FILE = ".DS_Store"
STAGING = ".partial-"
_CHUNK = 64 * 1024
_ENCRYPTED = 0x1


class ArchiveError(ValueError):
    """Why an archive, or one of its members, cannot be read, in words a
    person can hear. `reason` is a short fixed code for callers."""

    def __init__(self, reason: str, message: str, *, kind: str = ""):
        super().__init__(message)
        self.reason = reason
        self.kind = kind          # "bad-kind" only: "link", "device" or "encrypted"


@dataclass(frozen=True)
class Budgets:
    """How much one archive may hold. Entries are counted from the central
    directory; bytes and ratio are counted while decompressing."""
    max_entries: int = 10_000
    max_file_bytes: int = 256 * 1024 * 1024
    max_total_bytes: int = 1024 * 1024 * 1024
    # Expanded bytes per compressed byte, checked once a member is past
    # RATIO_FLOOR bytes (small files of repeated bytes compress far more).
    max_ratio: int = 100
    ratio_floor: int = 1024 * 1024
    max_depth: int = 32
    max_seconds: float = 120.0


DEFAULT_BUDGETS = Budgets()


@dataclass(frozen=True)
class Member:
    """One checked entry: `path` is its normalised name (no trailing '/'),
    `info` the archive's own record of it."""
    path: str
    info: zipfile.ZipInfo
    is_dir: bool

    @property
    def size(self) -> int:
        return self.info.file_size

    @property
    def compressed_size(self) -> int:
        return self.info.compress_size


@dataclass(frozen=True)
class Refused:
    """An entry a per-entry listing set aside: its name as the archive
    gives it and why (`reason`, an ArchiveError code; `message`, the words)."""
    name: str
    reason: str
    message: str
    kind: str = ""            # as ArchiveError.kind


@dataclass(frozen=True)
class Listing:
    """An archive's checked entries. `members` leaves out platform metadata
    (`ignored`); `wrapper` is the one folder every other member is under,
    or "" when the archive has its files at its root. `refused` is empty
    unless the listing was made `per_entry`."""
    members: tuple[Member, ...]
    ignored: tuple[Member, ...] = ()
    wrapper: str = ""
    refused: tuple[Refused, ...] = ()

    @property
    def root(self) -> str:
        """The prefix a member's path has inside the wrapper: "" or "<wrapper>/"."""
        return f"{self.wrapper}/" if self.wrapper else ""

    def files(self) -> dict[str, Member]:
        """Every file member by its path."""
        return {m.path: m for m in self.members if not m.is_dir}



@dataclass
class _Spent:
    """What reading one archive has used so far."""
    budgets: Budgets
    started: float = field(default_factory=time.monotonic)
    total: int = 0

    def check_time(self) -> None:
        if time.monotonic() - self.started > self.budgets.max_seconds:
            raise ArchiveError("too-slow", f"Reading the zip took longer than "
                                           f"{self.budgets.max_seconds:.0f} seconds.")


def open_archive(path: Path) -> zipfile.ZipFile:
    """`path` opened as a ZIP, or ArchiveError("not-zip")."""
    try:
        return zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, ValueError):
        raise ArchiveError("not-zip", "That file is not a zip.") from None


def normalised(name: str) -> str:
    """An entry's name as the path it would be written at, or ArchiveError
    when it could land anywhere but inside the folder it is extracted to."""
    shown = repr(name)
    if not name or "\x00" in name or any(ord(c) < 32 for c in name):
        raise ArchiveError("bad-name", f"{shown} is not a usable file name.")
    if "\\" in name:
        raise ArchiveError("outside", f"{shown} has a backslash in its name, so it "
                                      f"could land outside the folder the zip opens into.")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":" and name[0].isalpha()):
        raise ArchiveError("outside", f"{shown} is an absolute path; it would land "
                                      f"outside the folder the zip opens into.")
    parts = [p for p in PurePosixPath(name).parts if p != "."]
    if ".." in parts:
        raise ArchiveError("outside", f"{shown} climbs out with '..'; it would land "
                                      f"outside the folder the zip opens into.")
    if not parts:
        raise ArchiveError("bad-name", f"{shown} is not a usable file name.")
    return "/".join(parts)


# What kind of entry `_kind_refused` found: "link", "device" or "encrypted".
# A strict listing raises all three as "bad-kind"; a per-entry listing keeps
# the kind as the entry's reason.
def _kind_refused(info: zipfile.ZipInfo) -> Optional[tuple[str, str]]:
    """(kind, words) when an entry is not a plain file or folder, or None."""
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        return "link", "is a link; a zip opened here carries files and folders only"
    if mode and (stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode)
                 or stat.S_ISSOCK(mode)):
        return "device", "is a device or a pipe, not a file"
    if info.flag_bits & _ENCRYPTED:
        return "encrypted", "is encrypted; password-protected zips are not supported"
    return None


def _is_metadata(path: str) -> bool:
    parts = path.split("/")
    return parts[0] == METADATA_FOLDER or parts[-1] == METADATA_FILE


def list_members(archive: zipfile.ZipFile, budgets: Optional[Budgets] = None, *,
                 per_entry: bool = False) -> Listing:
    """Every entry of `archive`, checked: nothing absolute, climbing out or
    with a backslash, no link, device or encrypted entry, no two entries
    landing at the same path (after Unicode normalisation and ignoring
    case, as a Mac's disk would), no file where another entry needs a
    folder, and within the entry, depth and claimed-size budgets.

    One bad entry refuses the whole archive, unless `per_entry` (read-only
    browsing, never extraction): then each bad entry is set aside in
    `refused` with its reason and the rest are listed. The entry count and
    the total claimed size are about the archive as a whole and refuse it
    either way."""
    budgets = budgets or DEFAULT_BUDGETS
    infos = archive.infolist()
    if len(infos) > budgets.max_entries:
        raise ArchiveError("too-many", f"The zip has {len(infos)} entries; at most "
                                       f"{budgets.max_entries} can be opened.")
    members: list[Member] = []
    refused: list[Refused] = []
    seen: dict[str, str] = {}
    claimed = 0
    for info in infos:
        try:
            member = _checked(info, seen, budgets)
        except ArchiveError as e:
            if not per_entry:
                raise
            refused.append(Refused(info.filename, e.reason, str(e), e.kind))
            continue
        if not member.is_dir:
            claimed += info.file_size
        members.append(member)
    if claimed > budgets.max_total_bytes:
        raise ArchiveError("too-large", f"The zip holds {claimed} bytes; at most "
                                        f"{budgets.max_total_bytes} can be opened.")
    for bad in _folder_conflicts(members):
        if not per_entry:
            raise bad[1]
        members.remove(bad[0])
        refused.append(Refused(bad[0].info.filename, bad[1].reason, str(bad[1])))
    kept = tuple(m for m in members if not _is_metadata(m.path))
    ignored = tuple(m for m in members if _is_metadata(m.path))
    return Listing(kept, ignored, _wrapper(kept), tuple(refused))


def _checked(info: zipfile.ZipInfo, seen: dict[str, str], budgets: Budgets) -> Member:
    """One entry as a Member, or ArchiveError saying why it cannot be one.
    `seen` collects the paths taken so far, for collisions."""
    path = normalised(info.filename)
    refused = _kind_refused(info)
    if refused:
        kind, words = refused
        raise ArchiveError("bad-kind", f"{info.filename!r} {words}.", kind=kind)
    if len(path.split("/")) > budgets.max_depth:
        raise ArchiveError("too-deep", f"{info.filename!r} is more than "
                                       f"{budgets.max_depth} folders deep.")
    key = unicodedata.normalize("NFC", path).casefold()
    if key in seen:
        raise ArchiveError("collision", f"{info.filename!r} and {seen[key]!r} would be "
                                        f"the same file once opened.")
    if not info.is_dir() and info.file_size > budgets.max_file_bytes:
        raise ArchiveError("too-large", f"{info.filename!r} is {info.file_size} "
                                        f"bytes; at most {budgets.max_file_bytes} "
                                        f"can be opened.")
    seen[key] = info.filename
    return Member(path, info, info.is_dir())


def _folder_conflicts(members: list[Member]) -> list[tuple[Member, ArchiveError]]:
    """Each entry that needs a folder where another entry is a file."""
    files = {unicodedata.normalize("NFC", m.path).casefold(): m.info.filename
             for m in members if not m.is_dir}
    found = []
    for member in members:
        parts = unicodedata.normalize("NFC", member.path).casefold().split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent in files:
                found.append((member, ArchiveError(
                    "collision", f"{files[parent]!r} is a file, but "
                                 f"{member.info.filename!r} needs it to be a folder.")))
                break
    return found


def _wrapper(members: tuple[Member, ...]) -> str:
    """The one folder every member is in, or "" when there is none (files at
    the root, several top folders, or nothing but one folder's own entry)."""
    tops = {m.path.split("/")[0] for m in members}
    if len(tops) != 1:
        return ""
    (top,) = tops
    inside = [m for m in members if m.path != top]
    if any(m.path == top and not m.is_dir for m in members) or not inside:
        return ""
    return top


def _read_into(archive: zipfile.ZipFile, member: Member, spent: _Spent, sink) -> int:
    """Decompress one member into `sink`, counting what is actually produced
    against the per-file, total, ratio and time budgets."""
    budgets, written = spent.budgets, 0
    try:
        with archive.open(member.info) as stream:
            while chunk := stream.read(_CHUNK):
                written += len(chunk)
                spent.total += len(chunk)
                if written > budgets.max_file_bytes:
                    raise ArchiveError("too-large", f"{member.info.filename!r} expands past "
                                                    f"{budgets.max_file_bytes} bytes.")
                if spent.total > budgets.max_total_bytes:
                    raise ArchiveError("too-large", f"The zip expands past "
                                                    f"{budgets.max_total_bytes} bytes.")
                if written > budgets.ratio_floor and \
                        written > budgets.max_ratio * max(member.compressed_size, 1):
                    raise ArchiveError("ratio", f"{member.info.filename!r} expands more "
                                                f"than {budgets.max_ratio} times its "
                                                f"compressed size.")
                spent.check_time()
                sink(chunk)
    except (zipfile.BadZipFile, EOFError, zlib.error) as e:
        raise ArchiveError("corrupt", f"{member.info.filename!r} is damaged "
                                      f"({type(e).__name__}).") from None
    except NotImplementedError:
        raise ArchiveError("unsupported", f"{member.info.filename!r} uses a compression "
                                          f"method that cannot be read here.") from None
    return written


def read_member(archive: zipfile.ZipFile, member: Member,
                budgets: Optional[Budgets] = None, limit: Optional[int] = None) -> bytes:
    """One file member's bytes, decompressed under the budgets (and at most
    `limit` bytes when given)."""
    budgets = budgets or DEFAULT_BUDGETS
    if member.is_dir:
        raise ArchiveError("not-file", f"{member.info.filename!r} is a folder.")
    if limit is not None:
        budgets = replace(budgets, max_file_bytes=min(budgets.max_file_bytes, limit))
    out = bytearray()
    _read_into(archive, member, _Spent(budgets), out.extend)
    return bytes(out)


def read_head(archive: zipfile.ZipFile, member: Member, size: int) -> bytes:
    """A file member's first `size` bytes at most: what telling its format
    needs, decompressing no more than that."""
    if member.is_dir:
        raise ArchiveError("not-file", f"{member.info.filename!r} is a folder.")
    try:
        with archive.open(member.info) as stream:
            return stream.read(size)
    except (zipfile.BadZipFile, EOFError, zlib.error, OSError) as e:
        raise ArchiveError("corrupt", f"{member.info.filename!r} is damaged "
                                      f"({type(e).__name__}).") from None
    except NotImplementedError:
        raise ArchiveError("unsupported", f"{member.info.filename!r} uses a compression "
                                          f"method that cannot be read here.") from None


def extract(archive: zipfile.ZipFile, listing: Listing, destination: Path, *,
            folder: str = "", keep_modes: bool = False,
            budgets: Optional[Budgets] = None) -> Path:
    """Write the members inside `folder` of the archive ("" for all of
    them) into `destination`, which must not exist, with `folder` taken off
    their paths. Everything is written into a private staging folder beside
    `destination` and renamed into place only once every member has been
    read in full; any failure, or an interruption, removes it. Files are
    owner-only and not executable unless `keep_modes`, which keeps each
    file's permission bits (never set-id or sticky). Raises FileExistsError
    when `destination` already exists, before or at the rename."""
    if destination.exists():
        raise FileExistsError(destination)
    prefix = f"{folder.strip('/')}/" if folder.strip("/") else ""
    staging = destination.parent / f"{STAGING}{uuid.uuid4()}"
    staging.mkdir(mode=0o700)
    spent = _Spent(budgets or DEFAULT_BUDGETS)
    try:
        for member in listing.members:
            if not member.path.startswith(prefix):
                continue
            relative = member.path[len(prefix):]
            target = staging.joinpath(*relative.split("/"))
            if member.is_dir:
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            mode = 0o600
            if keep_modes:
                mode |= (member.info.external_attr >> 16) & 0o777
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as out:
                _read_into(archive, member, spent, out.write)
            os.chmod(target, mode)
        try:
            os.rename(staging, destination)
        except OSError:
            if destination.exists():
                raise FileExistsError(destination) from None
            raise
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination
