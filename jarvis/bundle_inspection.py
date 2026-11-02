"""The one service that looks inside a file someone sent: what it is, what
an archive holds, a member's text a page at a time, a preview and a
rendered page. The page's bundle routes (bundle_api.py) and the brain's
bundle tools (server.py) both call it, and get the same shapes
(bundle_contract.py).

A file is named by (message id, attachment id) for the owner signed in now
(`Owner`), and reached only through messaging_files: `open` downloads and
verifies it once if it is not here (the owner's instruction to look);
every other call needs it already verified and re-checks its hash. A member
is named by its `member_id`, never by a path the caller gives.

Read-only inspection never extracts into the packages folder, never clears
the quarantine mark, keeps no execute bit, runs no script, loads no macro
and executes no HTML. A runnable work package is recognised and badged
(work_packages.recognise); opening and running it stays open_package's.

An archive is read in place: listed with bundle_archive's per-entry checks
(a bad entry is a `blocked` member with its reason, the rest stay
readable; only a whole-archive refusal fails it), each member's format
told from its first bytes, members read through bundle_archive's budgets.
A reader that needs a file on disk gets a private 0600 copy in the cache's
scratch folder, removed when it is done. A nested archive is a member
(`nested_archive`), never expanded. Office documents are documents, though
they are zips. Tar, gzip, 7z, RAR and split archives are `unsupported`
(`reader_unavailable`); an encrypted zip's entries are `blocked`
(`encrypted`).

What the readers derive (the listing, descriptions, text pages, renders)
is kept in the DerivedCache under the owner, the attachment's SHA-256, the
member, the reader's version and the options.

Each outcome is handed to `observer` (bundle_status.py turns them into the
openability record the sender hears about); the service itself sends
nothing.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Optional
from urllib.parse import quote
from uuid import UUID

from jarvis import bundle_archive, bundle_readers, work_packages
from jarvis import messaging_wire as wire
from jarvis.bundle_cache import CacheKey, DerivedCache
from jarvis.bundle_contract import (ATTACHMENT_PREFIX, MEMBER_PAGE_DEFAULT, MEMBER_PAGE_MAX,
                                    PATH_MAX_CHARS, PREVIEW_REFS_MAX, TEXT_PAGE_DEFAULT_CHARS,
                                    TEXT_PAGE_MAX_CHARS, AttachmentInfo, Classification,
                                    Extent, InspectionRefused, Inventory, Location, MemberInfo,
                                    MemberPage, Operation, PackageBadge, PageLocation, Preview,
                                    PreviewRef,
                                    Reason, ReaderId, RenderedPage, RenderedPageInfo,
                                    RenderOptions, Support, TextChunk, TextPage,
                                    decode_continuation, encode_continuation)
from jarvis.bundle_contract import member_id as make_member_id
from jarvis.bundle_readers import (ARCHIVES, MEDIA_TYPES, SNIFF_BYTES, Description, Detected,
                                   Reader, Unsupported, archive_reason, detect, multipart,
                                   reader_for)
from jarvis.bundle_readers import html as html_reader
from jarvis.bundle_readers import image as image_reader
from jarvis.bundle_readers import office

log = logging.getLogger("jarvis.bundle_inspection")

# The listing's own version: bump it when what classify or list derive changes.
LISTING_VERSION = 1
# A static preview's files: the page itself and what it may load.
ASSET_MAX_BYTES = 16 * 1024 * 1024
_ASSET_TYPES = {"css": "text/css", "woff": "font/woff", "woff2": "font/woff2",
                "ttf": "font/ttf", "otf": "font/otf"}
_ASSET_FORMATS = frozenset({"html", "svg", "png", "jpeg", "gif", "webp"})
_OPERATION_ORDER: tuple[Operation, ...] = ("download", "list_archive", "extract", "read_text",
                                           "render", "ocr", "edit_export", "execute")


@dataclass(frozen=True)
class Owner:
    """Whose messaging: the files service's namespace."""
    server_id: str
    owner_github_id: int


@dataclass(frozen=True)
class AttachmentKey:
    message_id: UUID
    attachment_id: UUID


@dataclass(frozen=True)
class Observation:
    """One inspection outcome: which operation on which member (None for
    the attachment as a whole) and how it went."""
    owner: Owner
    key: AttachmentKey
    attachment_sha256: str
    operation: Operation
    member_id: Optional[str]
    support: Support
    reason: Optional[Reason] = None
    # Where a read reached: a text page's chunks, a rendered page.
    locations: tuple[Location, ...] = ()


# -- where a member's bytes come from ------------------------------------------------------

def _private_copy(scratch: Path, write: Callable[[int], None]) -> Iterator[Path]:
    """A new 0600 file in `scratch`, filled by `write(fd)`, removed after."""
    path = scratch / f"member-{uuid.uuid4().hex}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as out:
            write(out)
        yield path
    finally:
        path.unlink(missing_ok=True)


class _FileSource:
    """The attachment itself: a file that is not an archive."""

    def __init__(self, path: Path, sha256: str, member: str, scratch: Callable[[], Path]):
        self._file, self._scratch = path, scratch
        self.attachment_sha256, self.member_id, self.path = sha256, member, ""
        self.size = path.stat().st_size

    def head(self) -> bytes:
        with open(self._file, "rb") as f:
            return f.read(SNIFF_BYTES)

    def read(self, limit: Optional[int] = None) -> bytes:
        if limit is not None and self.size > limit:
            raise bundle_archive.ArchiveError("too-large", "The file is larger than read here.")
        with open(self._file, "rb") as f:
            return f.read()

    @contextlib.contextmanager
    def materialize(self) -> Iterator[Path]:
        def write(out) -> None:
            with open(self._file, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    out.write(chunk)
        yield from _private_copy(self._scratch(), write)


class _ZipSource:
    """One file member of a zip, read from the archive in place."""

    def __init__(self, archive: Path, member: bundle_archive.Member, sha256: str, mid: str,
                 budgets: bundle_archive.Budgets, scratch: Callable[[], Path]):
        self._archive, self._member, self._budgets = archive, member, budgets
        self._scratch = scratch
        self.attachment_sha256, self.member_id, self.path = sha256, mid, member.path
        self.size = member.size

    def head(self) -> bytes:
        with bundle_archive.open_archive(self._archive) as archive:
            return bundle_archive.read_head(archive, self._member, SNIFF_BYTES)

    def read(self, limit: Optional[int] = None) -> bytes:
        with bundle_archive.open_archive(self._archive) as archive:
            return bundle_archive.read_member(archive, self._member, self._budgets, limit)

    @contextlib.contextmanager
    def materialize(self) -> Iterator[Path]:
        data = self.read()
        yield from _private_copy(self._scratch(), lambda out: out.write(data))


# -- what classify works out, kept in the cache ----------------------------------------------

@dataclass
class _Entry:
    """A member, the format it was found to be, and the folder it is listed
    in (its parent; a refused entry whose name has no safe parent is listed
    at the top)."""
    info: MemberInfo
    detected: Detected
    folder: str = ""


@dataclass
class _Survey:
    """An attachment classified: its members by id (the attachment itself
    as the member at path "" when it is not an archive)."""
    classification: Classification
    entries: dict[str, _Entry]

    def dump(self) -> bytes:
        return json.dumps({
            "classification": self.classification.model_dump(mode="json"),
            "entries": [{"info": e.info.model_dump(mode="json"), "format": e.detected.format,
                         "media_type": e.detected.media_type, "folder": e.folder}
                        for e in self.entries.values()],
        }).encode("utf-8")

    @classmethod
    def load(cls, data: bytes) -> "_Survey":
        raw = json.loads(data)
        entries = {}
        for e in raw["entries"]:
            info = MemberInfo.model_validate(e["info"])
            entries[info.member_id] = _Entry(info, Detected(e["format"], e["media_type"]),
                                             e["folder"])
        return cls(Classification.model_validate(raw["classification"]), entries)


def _operations(ops) -> list[Operation]:
    return [op for op in _OPERATION_ORDER if op in ops]


def _quick_support(detected: Detected) -> tuple[Support, Optional[Reason], list[Operation]]:
    """A member's support from its format and this Mac's readers, without
    reading it (a listing reads only heads)."""
    if detected.format in ARCHIVES:
        return "unsupported", "nested_archive", []
    reader = reader_for(detected.format)
    if reader is None:
        reason = "unknown_format" if detected.format == "unknown" else "reader_unavailable"
        return "unsupported", reason, []
    state = reader.health()
    if not state.available:
        return "unsupported", state.reason or "reader_unavailable", []
    return "readable", None, _operations(set(state.operations))


def _clip(path: str) -> str:
    return path[:PATH_MAX_CHARS]


def _name(path: str) -> str:
    return _clip(path.rstrip("/").rsplit("/", 1)[-1] or path)


def _refusal_for(reason: Optional[str], shown: str) -> InspectionRefused:
    if reason == "page_out_of_range":
        return InspectionRefused("page_out_of_range", "There is no such page.")
    code = {"corrupt": "corrupt", "budget": "budget", "too_large": "budget",
            "not_zip": "corrupt"}.get(reason or "", "unsupported")
    return InspectionRefused(code, shown, reason)


class BundleInspection:
    def __init__(self, files, cache: DerivedCache, *,
                 observer: Callable[[Observation], None] = lambda o: None,
                 budgets: bundle_archive.Budgets = bundle_archive.DEFAULT_BUDGETS):
        """`files` is messaging_files.FileSnapshots."""
        self.files, self.cache, self.observer, self.budgets = files, cache, observer, budgets

    # -- reaching the file -------------------------------------------------------------

    def _info(self, owner: Owner, key: AttachmentKey) -> AttachmentInfo:
        try:
            ref = self.files.attachment_ref(owner.server_id, owner.owner_github_id,
                                            key.message_id, key.attachment_id)
        except wire.ContractError as e:
            raise self._refused(e) from None
        return AttachmentInfo(message_id=key.message_id, attachment_id=key.attachment_id,
                              filename=ref.filename, declared_media_type=ref.media_type,
                              size_bytes=ref.size_bytes, sha256=ref.sha256)

    @staticmethod
    def _refused(error: wire.ContractError) -> InspectionRefused:
        if error.code == "attachment_not_found":
            return InspectionRefused("attachment_not_found", "That message has no such file.")
        if error.code == "attachment_mismatch":
            return InspectionRefused("corrupt", "The file here no longer matches what was "
                                                "sent.", "corrupt")
        return InspectionRefused("not_local", "It is not downloaded here yet.", "not_local")

    def _verified(self, owner: Owner, key: AttachmentKey) -> tuple[AttachmentInfo, Path]:
        info = self._info(owner, key)
        try:
            path = self.files.verified_path(owner.server_id, owner.owner_github_id,
                                            key.message_id, key.attachment_id)
        except wire.ContractError as e:
            refused = self._refused(e)
            if refused.code == "corrupt":
                self._observe(owner, key, info.sha256, "download", None, "blocked", "corrupt")
            raise refused from None
        return info, Path(path)

    def _observe(self, owner: Owner, key: AttachmentKey, sha256: str, operation: Operation,
                 member: Optional[str], support: Support, reason: Optional[str] = None,
                 locations: tuple = ()) -> None:
        try:
            self.observer(Observation(owner, key, sha256, operation, member, support, reason,
                                      tuple(locations)))
        except Exception:                                  # noqa: BLE001 - never the caller's
            log.exception("bundle observer failed")

    def _scratch(self, owner: Owner) -> Callable[[], Path]:
        return lambda: self.cache.scratch(owner.server_id, owner.owner_github_id)

    def _key(self, owner: Owner, info: AttachmentInfo, member: str, reader: str, version: int,
             kind: str, options: str = "") -> CacheKey:
        return CacheKey(owner.server_id, owner.owner_github_id, info.sha256, member, reader,
                        version, kind, options)

    # -- classify ------------------------------------------------------------------------

    async def open(self, owner: Owner, key: AttachmentKey) -> Classification:
        """Download and verify it if it is not here, then classify it."""
        info = self._info(owner, key)
        try:
            await self.files.downloaded(owner.server_id, owner.owner_github_id,
                                        key.message_id, key.attachment_id)
        except wire.ContractError as e:
            refused = self._refused(e)
            reason = "corrupt" if refused.code == "corrupt" else "not_local"
            self._observe(owner, key, info.sha256, "download", None, "blocked", reason)
            if refused.code == "not_local":
                raise InspectionRefused("not_local", "It could not be downloaded.",
                                        "not_local") from None
            raise refused from None
        self._observe(owner, key, info.sha256, "download", None, "readable")
        return await asyncio.to_thread(self.classify, owner, key)

    def classify(self, owner: Owner, key: AttachmentKey) -> Classification:
        info, path = self._verified(owner, key)
        survey = self._survey(owner, info, path)
        c = survey.classification
        if c.container == "zip":
            self._observe(owner, key, info.sha256, "list_archive", None, c.support, c.reason)
        return c

    def _survey(self, owner: Owner, info: AttachmentInfo, path: Path) -> _Survey:
        root = make_member_id(info.sha256, "")
        readers = ",".join(f"{fmt}:{int(state.available)}" for fmt, state
                           in sorted(bundle_readers.health().items()))
        cache_key = self._key(owner, info, root, "inspection", LISTING_VERSION, "listing",
                              readers)
        cached = self.cache.get(cache_key)
        if cached is not None:
            try:
                return _Survey.load(cached)
            except (ValueError, KeyError, TypeError):
                pass
        survey = self._classify(owner, info, path)
        self.cache.put(cache_key, survey.dump())
        return survey

    def _classify(self, owner: Owner, info: AttachmentInfo, path: Path) -> _Survey:
        root = make_member_id(info.sha256, "")
        with open(path, "rb") as f:
            head = f.read(SNIFF_BYTES)
        detected = detect(info.filename, head)

        def single(container: str, support: Support, reason: Optional[str],
                   ops: list[Operation], found: Detected = detected) -> _Survey:
            member = MemberInfo(member_id=root, path="", name=_name(info.filename),
                                is_dir=False, size_bytes=info.size_bytes,
                                compressed_size=info.size_bytes, format=found.format,
                                media_type=found.media_type, support=support, reason=reason,
                                operations=ops)
            c = Classification(attachment=info, format=found.format,
                               media_type=found.media_type, container=container,
                               support=support, reason=reason, root_member_id=root)
            return _Survey(c, {root: _Entry(member, found)})

        if multipart(info.filename) or detected.format in ARCHIVES - {"zip"}:
            return single("unsupported", "unsupported", "reader_unavailable", ["download"])
        if detected.format == "zip":
            try:
                archive = bundle_archive.open_archive(path)
            except bundle_archive.ArchiveError:
                return single("zip", "unsupported", "corrupt", ["download"])
            with archive:
                fmt = self._office_format(archive)
                if fmt is None:
                    return self._archive(owner, info, path, archive, detected)
            detected = Detected(fmt, MEDIA_TYPES[fmt])
        reader = reader_for(detected.format)
        if reader is None:
            support, reason, ops = _quick_support(detected)
            return single("none", support, reason, ["download", *ops], detected)
        source = _FileSource(path, info.sha256, root, self._scratch(owner))
        description = self._describe(owner, info, source, detected, reader)
        return single("none", description.support, description.reason,
                      _operations({"download", *description.operations}), detected)

    @staticmethod
    def _office_format(archive) -> Optional[str]:
        names = set(archive.namelist())
        if office.CONTENT_TYPES not in names:
            return None
        try:
            with archive.open(office.CONTENT_TYPES) as f:
                data = f.read(1024 * 1024)
        except Exception:                                   # noqa: BLE001 - not Office, then
            return None
        return office.ooxml_format(names, data)

    def _archive(self, owner: Owner, info: AttachmentInfo, path: Path, archive,
                 detected: Detected) -> _Survey:
        root = make_member_id(info.sha256, "")

        def whole(support: Support, reason: str) -> _Survey:
            c = Classification(attachment=info, format="zip", media_type=detected.media_type,
                               container="zip", support=support, reason=reason,
                               root_member_id=root)
            return _Survey(c, {})

        try:
            listing = bundle_archive.list_members(archive, self.budgets, per_entry=True)
        except bundle_archive.ArchiveError as e:
            return whole("blocked", archive_reason(e))
        entries: dict[str, _Entry] = {}
        folders: set[str] = set()
        for m in listing.members:
            parts = m.path.split("/")
            folders.update("/".join(parts[:n]) for n in range(1, len(parts)))
            if m.is_dir:
                folders.add(m.path)
        for folder in sorted(folders):
            mid = make_member_id(info.sha256, folder)
            entries[mid] = _Entry(MemberInfo(
                member_id=mid, path=_clip(folder), name=_name(folder), is_dir=True,
                size_bytes=0, compressed_size=0, format="folder",
                media_type=MEDIA_TYPES["folder"], support="readable"),
                Detected("folder", MEDIA_TYPES["folder"]), _parent(folder))
        for m in listing.members:
            if m.is_dir:
                continue
            mid = make_member_id(info.sha256, m.path)
            try:
                found = detect(m.path, bundle_archive.read_head(archive, m, SNIFF_BYTES))
                support, reason, ops = _quick_support(found)
            except bundle_archive.ArchiveError as e:
                found = detect(m.path, b"")
                support, reason, ops = "blocked", archive_reason(e), []
            entries[mid] = _Entry(MemberInfo(
                member_id=mid, path=_clip(m.path), name=_name(m.path), is_dir=False,
                size_bytes=m.size, compressed_size=m.compressed_size, format=found.format,
                media_type=found.media_type, support=support, reason=reason, operations=ops),
                found, _parent(m.path))
        for bad in listing.refused:
            mid = make_member_id(info.sha256, bad.name)
            found = detect(bad.name, b"")
            entries[mid] = _Entry(MemberInfo(
                member_id=mid, path=_clip(bad.name), name=_name(bad.name),
                is_dir=bad.name.endswith("/"), size_bytes=0, compressed_size=0,
                format=found.format, media_type=found.media_type, support="blocked",
                reason=archive_reason(bad)), found, _shown_in(bad.name, folders))
        files = [e.info for e in entries.values() if not e.info.is_dir]
        counts = {s: sum(1 for f in files if f.support == s)
                  for s in ("readable", "partial", "unsupported", "blocked")}
        inventory = Inventory(files=len(files), folders=len(folders),
                              expanded_bytes=sum(f.size_bytes for f in files),
                              wrapper=listing.wrapper, ignored=len(listing.ignored), **counts)
        support, reason = self._overall(files)
        c = Classification(attachment=info, format="zip", media_type=detected.media_type,
                           container="zip", support=support, reason=reason,
                           root_member_id=root, inventory=inventory,
                           package=self._badge(archive, listing))
        return _Survey(c, entries)

    @staticmethod
    def _overall(files: list[MemberInfo]) -> tuple[Support, Optional[str]]:
        unreadable = [f for f in files if f.support != "readable"]
        if not unreadable:
            return "readable", None
        if len(unreadable) == len(files) and all(f.reason == "encrypted" for f in files):
            return "blocked", "encrypted"
        return "partial", unreadable[0].reason

    @staticmethod
    def _badge(archive, listing: bundle_archive.Listing) -> Optional[PackageBadge]:
        """A work-package badge when the zip has a package.json at its root
        or in one folder; None when it has none."""
        if not any(p.split("/")[-1] == work_packages.MANIFEST and p.count("/") <= 1
                   for p in listing.files()):
            return None
        found = work_packages.recognise(archive, listing)
        if found.manifest is None:
            return PackageBadge(recognised=False, reason=found.reason[:500])
        if listing.refused:
            return PackageBadge(recognised=False, runs=found.manifest.runs,
                                reason="Some of its entries cannot be opened, so it cannot run.")
        return PackageBadge(recognised=True, runs=found.manifest.runs)

    # -- members -----------------------------------------------------------------------------

    def _entry(self, survey: _Survey, mid: str) -> _Entry:
        entry = survey.entries.get(mid)
        if entry is None:
            raise InspectionRefused("member_not_found", "There is no such member.")
        return entry

    def list_members(self, owner: Owner, key: AttachmentKey, folder: str = "",
                     continuation: Optional[str] = None,
                     limit: int = MEMBER_PAGE_DEFAULT) -> MemberPage:
        """One folder of an archive, a page at a time ("" is the top, the
        wrapper folder included as a folder). A file that is not an
        archive is its own one member."""
        info, path = self._verified(owner, key)
        survey = self._survey(owner, info, path)
        folder = folder.strip("/")
        if folder and not any(e.info.is_dir and e.info.path == folder
                              for e in survey.entries.values()):
            raise InspectionRefused("member_not_found", "There is no such folder.")
        limit = max(1, min(int(limit), MEMBER_PAGE_MAX))
        folder_id = make_member_id(info.sha256, folder)
        try:
            start = decode_continuation(continuation, folder_id, LISTING_VERSION)["offset"] \
                if continuation else 0
            if not isinstance(start, int) or start < 0:
                raise ValueError
        except (ValueError, KeyError):
            raise InspectionRefused("bad_continuation", "That continuation is not this "
                                                        "folder's.") from None
        inside = sorted((e.info for e in survey.entries.values()
                         if e.folder == folder and (e.info.path or not folder)),
                        key=lambda m: (not m.is_dir, unicodedata.normalize("NFC", m.name)
                                       .casefold(), m.path))
        page = inside[start:start + limit]
        after = start + len(page)
        more = encode_continuation(folder_id, LISTING_VERSION, {"offset": after}) \
            if after < len(inside) else None
        if survey.classification.container == "zip":
            self._observe(owner, key, info.sha256, "list_archive", None,
                          survey.classification.support, survey.classification.reason)
        return MemberPage(attachment=info, folder=folder, members=page, total=len(inside),
                          continuation=more)

    def _readable(self, owner: Owner, key: AttachmentKey, mid: str,
                  operation: Operation) -> tuple[AttachmentInfo, _Entry, Reader, object]:
        """The member, its reader and a source for it, or the refusal."""
        info, path = self._verified(owner, key)
        survey = self._survey(owner, info, path)
        entry = self._entry(survey, mid)
        member = entry.info

        def refuse(refusal: InspectionRefused) -> InspectionRefused:
            self._observe(owner, key, info.sha256, operation, mid,
                          "blocked" if refusal.code == "blocked" else "unsupported",
                          refusal.reason)
            return refusal

        if member.is_dir:
            raise refuse(InspectionRefused("unsupported", "That is a folder."))
        if member.support == "blocked":
            raise refuse(InspectionRefused("blocked", "That member cannot be opened safely.",
                                           member.reason))
        if entry.detected.format in ARCHIVES or (member.path == ""
                                                  and survey.classification.container != "none"):
            reason = "nested_archive" if member.path else member.reason or "reader_unavailable"
            raise refuse(InspectionRefused("unsupported", "That is an archive; list its "
                                                          "members instead.", reason))
        reader = reader_for(entry.detected.format)
        if reader is None or not reader.health().available:
            raise refuse(InspectionRefused("unsupported", "There is no reader for it here.",
                                           member.reason or "reader_unavailable"))
        if member.path == "":
            source = _FileSource(path, info.sha256, mid, self._scratch(owner))
        else:
            source = self._zip_source(owner, info, path, member.path)
        return info, entry, reader, source

    def _zip_source(self, owner: Owner, info: AttachmentInfo, path: Path,
                    member_path: str) -> _ZipSource:
        with bundle_archive.open_archive(path) as archive:
            listing = bundle_archive.list_members(archive, self.budgets, per_entry=True)
        member = listing.files().get(member_path)
        if member is None:
            raise InspectionRefused("member_not_found", "There is no such member.")
        return _ZipSource(path, member, info.sha256, make_member_id(info.sha256, member_path),
                          self.budgets, self._scratch(owner))

    def _describe(self, owner: Owner, info: AttachmentInfo, source, detected: Detected,
                  reader: Reader) -> Description:
        key = self._key(owner, info, source.member_id, reader.name, reader.version, "describe",
                        detected.format)
        cached = self.cache.get(key)
        if cached is not None:
            raw = json.loads(cached)
            return Description(raw["support"], Extent.model_validate(raw["extent"]),
                               raw["reason"], frozenset(raw["operations"]))
        try:
            found = reader.describe(source, detected)
        except bundle_archive.ArchiveError as e:
            found = Description("blocked", Extent(), archive_reason(e))
        self.cache.put(key, json.dumps({"support": found.support,
                                        "extent": found.extent.model_dump(mode="json"),
                                        "reason": found.reason,
                                        "operations": sorted(found.operations)}).encode())
        return found

    def _run(self, owner: Owner, key: AttachmentKey, info: AttachmentInfo, mid: str,
             operation: Operation, call):
        """A reader's call, its failures turned into refusals and observed."""
        try:
            return call()
        except Unsupported as e:
            refusal = _refusal_for(e.reason, "It could not be read.")
        except bundle_archive.ArchiveError as e:
            reason = archive_reason(e)
            refusal = _refusal_for(reason, "It could not be read within the safe limits.")
        if refusal.code != "page_out_of_range":
            self._observe(owner, key, info.sha256, operation, mid,
                          "blocked" if refusal.code == "budget" else "unsupported",
                          refusal.reason)
        raise refusal

    def read_member(self, owner: Owner, key: AttachmentKey, member_id: str,
                    continuation: Optional[str] = None,
                    max_chars: int = TEXT_PAGE_DEFAULT_CHARS) -> TextPage:
        info, entry, reader, source = self._readable(owner, key, member_id, "read_text")
        max_chars = max(1, min(int(max_chars), TEXT_PAGE_MAX_CHARS))
        try:
            cursor = decode_continuation(continuation, member_id, reader.version) \
                if continuation else None
        except ValueError:
            raise InspectionRefused("bad_continuation", "That continuation is not this "
                                                        "member's.") from None
        description = self._run(owner, key, info, member_id, "read_text",
                                lambda: self._describe(owner, info, source, entry.detected,
                                                       reader))
        options = json.dumps({"cursor": cursor, "max": max_chars}, sort_keys=True)
        cache_key = self._key(owner, info, member_id, reader.name, reader.version, "text",
                              options)
        cached = self.cache.get(cache_key)
        if cached is not None:
            page = TextPage.model_validate_json(cached)
        else:
            try:
                got = self._run(owner, key, info, member_id, "read_text",
                                lambda: reader.read_text(source, entry.detected, cursor,
                                                         max_chars))
            except ValueError:
                raise InspectionRefused("bad_continuation", "That continuation is past the "
                                                            "end.") from None
            chunks = [TextChunk(location=loc, text=text) for loc, text in got.chunks]
            more = encode_continuation(member_id, reader.version, got.next_cursor) \
                if got.next_cursor is not None else None
            page = TextPage(attachment=info, member=entry.info,
                            reader=ReaderId(name=reader.name, version=reader.version),
                            chunks=chunks, chars=sum(len(c.text) for c in chunks),
                            extent=description.extent, complete=more is None,
                            continuation=more, notes=list(dict.fromkeys(got.notes))[:8])
            self.cache.put(cache_key, page.model_dump_json().encode())
        partial = {"page_failed", "scanned_no_text", "too_large"} & set(page.notes)
        self._observe(owner, key, info.sha256, "read_text", member_id,
                      "partial" if partial else "readable",
                      next(iter(sorted(partial)), None) if partial else None,
                      tuple(c.location for c in page.chunks))
        return page

    def _url(self, key: AttachmentKey, member: str, tail: str) -> str:
        prefix = ATTACHMENT_PREFIX.format(message_id=key.message_id,
                                          attachment_id=key.attachment_id)
        return f"{prefix}/bundle/members/{member}/{tail}"

    def _asset_url(self, key: AttachmentKey, info: AttachmentInfo, entry: _Entry) -> str:
        own = entry.info.path or info.filename
        return self._url(key, entry.info.member_id,
                         "html/" + "/".join(quote(p, safe="") for p in own.split("/")))

    def preview_member(self, owner: Owner, key: AttachmentKey, member_id: str,
                       continuation: Optional[str] = None) -> Preview:
        info, entry, reader, source = self._readable(owner, key, member_id, "render")
        fmt = entry.detected.format
        description = self._run(owner, key, info, member_id, "render",
                                lambda: self._describe(owner, info, source, entry.detected,
                                                       reader))
        if description.support in ("unsupported", "blocked"):
            self._observe(owner, key, info.sha256, "render", member_id, description.support,
                          description.reason)
            raise _refusal_for(description.reason, "It cannot be shown.")
        refs, blocked, more = [], [], None
        if fmt == "pdf":
            try:
                start = decode_continuation(continuation, member_id, reader.version)["page"] \
                    if continuation else 1
                if not isinstance(start, int) or start < 1:
                    raise ValueError
            except (ValueError, KeyError):
                raise InspectionRefused("bad_continuation", "That continuation is not this "
                                                            "member's.") from None
            pages = description.extent.pages or 0
            last = min(pages, start + PREVIEW_REFS_MAX - 1)
            refs = [PreviewRef(kind="page_image", member_id=member_id, page=n,
                               url=self._url(key, member_id, f"pages/{n}"))
                    for n in range(start, last + 1)]
            if last < pages:
                more = encode_continuation(member_id, reader.version, {"page": last + 1})
        elif fmt in image_reader.ImageReader.formats:
            width, height = self._run(owner, key, info, member_id, "render",
                                      lambda: reader.size(source, entry.detected))
            refs = [PreviewRef(kind="image", member_id=member_id, page=1,
                               url=self._asset_url(key, info, entry), width=width,
                               height=height)]
        elif fmt in html_reader.HtmlReader.formats:
            _, blocked = self._run(owner, key, info, member_id, "render",
                                   lambda: reader.parsed(source))
            refs = [PreviewRef(kind="html", member_id=member_id,
                               url=self._asset_url(key, info, entry))]
        elif "read_text" in description.operations:
            # Text with nothing to draw (Markdown, CSV, an Office file): its
            # preview is its text, so there are no refs, and having no
            # picture is no failure to read it (nothing is observed).
            return Preview(attachment=info, member=entry.info, refs=[],
                           extent=description.extent)
        else:
            self._observe(owner, key, info.sha256, "render", member_id, "unsupported",
                          "reader_unavailable")
            raise InspectionRefused("unsupported", "There is no preview for it; read its "
                                                   "text instead.", "reader_unavailable")
        self._observe(owner, key, info.sha256, "render", member_id, "readable")
        return Preview(attachment=info, member=entry.info, refs=refs,
                       extent=description.extent, blocked_resources=blocked,
                       continuation=more)

    def render_page(self, owner: Owner, key: AttachmentKey, member_id: str, page: int,
                    options: RenderOptions = RenderOptions()) -> RenderedPage:
        """A PDF page as PNG, or an image as its own page 1, as it was sent."""
        info, entry, reader, source = self._readable(owner, key, member_id, "render")
        description = self._run(owner, key, info, member_id, "render",
                                lambda: self._describe(owner, info, source, entry.detected,
                                                       reader))
        pages = description.extent.pages
        if page < 1 or (pages is not None and page > pages):
            raise InspectionRefused("page_out_of_range", f"It has {pages or 0} pages.")
        cache_key = self._key(owner, info, member_id, reader.name, reader.version, "render",
                              f"{options.digest()}:{page}")
        cached = self.cache.get(cache_key)
        if cached is not None:
            size = int.from_bytes(cached[:4], "big")
            rendered = RenderedPage(RenderedPageInfo.model_validate_json(cached[4:4 + size]),
                                    cached[4 + size:])
        else:
            rendered = self._run(owner, key, info, member_id, "render",
                                 lambda: reader.render(source, entry.detected, page, options))
            head = rendered.info.model_dump_json().encode()
            self.cache.put(cache_key, len(head).to_bytes(4, "big") + head + rendered.data)
        self._observe(owner, key, info.sha256, "render", member_id, "readable",
                      locations=(PageLocation(page=page),))
        return rendered

    def html_asset(self, owner: Owner, key: AttachmentKey, member_id: str,
                   relative: str) -> tuple[bytes, str]:
        """A static preview's own file (an HTML page, an SVG or an image), or
        a file an HTML page loads, by its path in the archive as the
        preview's url gives it (the browser resolves the page's relative
        links against the page's own path). Only from the same archive,
        only a preview's kinds of file (pages, images, styles, fonts), and
        never a blocked member."""
        info, path = self._verified(owner, key)
        survey = self._survey(owner, info, path)
        preview = self._entry(survey, member_id)
        if preview.detected.format not in _ASSET_FORMATS or preview.info.is_dir:
            raise InspectionRefused("unsupported", "That member has no static preview.")
        if preview.info.path == "":
            if relative not in ("", info.filename):
                raise InspectionRefused("member_not_found", "A single file has no other "
                                                            "files.")
            data = self._run(owner, key, info, member_id, "render",
                             lambda: _FileSource(path, info.sha256, member_id,
                                                 self._scratch(owner)).read(ASSET_MAX_BYTES))
            return data, preview.detected.media_type
        try:
            wanted = bundle_archive.normalised(relative)
        except bundle_archive.ArchiveError:
            raise InspectionRefused("member_not_found", "That file is outside the archive.") \
                from None
        entry = survey.entries.get(make_member_id(info.sha256, wanted))
        if entry is None or entry.info.is_dir:
            raise InspectionRefused("member_not_found", "There is no such file in the "
                                                        "archive.")
        if entry.info.support == "blocked":
            raise InspectionRefused("blocked", "That member cannot be opened safely.",
                                    entry.info.reason)
        suffix = PurePosixPath(wanted.lower()).suffix.lstrip(".")
        if entry.detected.format in _ASSET_FORMATS:
            media_type = entry.detected.media_type
        elif suffix in _ASSET_TYPES:
            media_type = _ASSET_TYPES[suffix]
        else:
            raise InspectionRefused("unsupported", "A preview loads only pages, images, "
                                                   "styles and fonts.")
        source = self._zip_source(owner, info, path, wanted)
        data = self._run(owner, key, info, entry.info.member_id, "render",
                         lambda: source.read(ASSET_MAX_BYTES))
        return data, media_type


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _shown_in(name: str, folders: set[str]) -> str:
    """Where a refused entry is listed: its parent folder when its name is a
    safe path whose parent is listed, else the top."""
    try:
        parent = _parent(bundle_archive.normalised(name))
    except bundle_archive.ArchiveError:
        return ""
    return parent if parent in folders else ""
