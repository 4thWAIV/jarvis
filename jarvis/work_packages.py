"""Work packages: a zip a teammate sends in an ordinary message, which this
JARVIS unpacks and runs with the person, sending the results back as a
reply. The format is in docs/work-packages.md.

The team is a few trusted people on a closed network, so there is no
sandbox: the person's spoken yes (the brain's acting tools) is the gate
before a package is opened and before each step runs, and the results go
back through the ordinary draft, review and approval. What is kept is that
a zip entry may not land outside its package's folder.

    recognise    whether a checked zip (bundle_archive.py) is a package: its
                 package.json at the root or in its one wrapper folder
    unpack       a downloaded zip into `<packages>/<name>-<version>-<id>/`,
                 through bundle_archive's checks and staging, once per
                 archive's bytes, the quarantine mark taken off so its
                 scripts can run
    find, every  the packages unpacked here, by folder name (or by the
                 package's own name, when only one has it)
    run          one shell command in a package's folder, with a timeout,
                 its exit code and the last of its output
    results      the files a package's `returns` globs match, which go back
                 through draft_files.py (a package's folder is the one part
                 of JARVIS's data a draft may attach from)
    page_file,   a page package's own files, served to the page's sandboxed
    save_result  frame, and what that frame saves into its results/ folder:
                 both walk the folder one component at a time without
                 following a link (folder_readers)

A package runs in one of three ways, its `runs` (MODES): "conversation",
the default, where the brain follows its skill with the person step by
step; "agent", where one Claude Code run (the run pipeline) carries out its
brief in the package's folder; and "page", where its entry HTML opens in
the page's centre area in a frame without an origin. A page package may
also name a story (package_story.py): what JARVIS says over the page, and
what it points at while it says it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from jarvis import bundle_archive, data_paths, folder_readers, package_story
from jarvis.folder_contract import FolderError
from jarvis.messaging_files import QUARANTINE_ATTRIBUTE

log = logging.getLogger("jarvis.work_packages")

MANIFEST = "package.json"
# A step that runs longer than this is stopped (its whole process group).
COMMAND_TIMEOUT_SEC = 600
# How much of a step's output comes back: its end, where the result is.
OUTPUT_TAIL_CHARS = 4000
PARTIAL = ".partial-"
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

# How a package runs, its package.json `runs`, and the file each way needs.
CONVERSATION, AGENT, PAGE = "conversation", "agent", "page"
MODES = {CONVERSATION: "skill", AGENT: "brief", PAGE: "entry"}
# Fields only a JARVIS work package's package.json has; one with none of
# them is some other package.json (a source project's npm manifest).
_JARVIS_FIELDS = ("purpose", "returns", "runs", *MODES.values())
# The optional story a page package tells (package_story.py).
STORY = "story"
# What a page package's frame may save: a name of at most RESULT_MAX_PARTS
# plain parts under results/, and at most RESULT_MAX_BYTES each.
RESULTS = "results"
RESULT_MAX_BYTES = 20 * 1024 * 1024
RESULT_MAX_PARTS = 4
_RESULT_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# The largest page file served to the frame.
PAGE_FILE_MAX_BYTES = 32 * 1024 * 1024


class PackageError(ValueError):
    """Why a package cannot be opened, found or run, in words he can hear."""


@dataclass(frozen=True)
class Manifest:
    """A package's package.json. `runs` is one of MODES; the file it needs
    (`skill`, `brief` or `entry`) is required, the other two optional. A
    page package may name a `story` file too."""
    name: str
    version: str
    purpose: str
    returns: tuple[str, ...]
    runs: str = CONVERSATION
    skill: Optional[str] = None
    brief: Optional[str] = None
    entry: Optional[str] = None
    story: Optional[str] = None

    @classmethod
    def parse(cls, raw: bytes) -> "Manifest":
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PackageError("Its package.json is not valid JSON.") from None
        if not isinstance(body, dict):
            raise PackageError("Its package.json is not an object.")
        if not any(key in body for key in _JARVIS_FIELDS):
            raise PackageError("Its package.json is not a JARVIS work package's: it has none "
                               "of purpose, returns, runs, skill, brief or entry, so it is "
                               "some other project's package.json.")
        runs = body.get("runs", CONVERSATION)
        if runs not in MODES:
            raise PackageError(f"Its package.json's runs is not one of {', '.join(MODES)}.")
        fields = {}
        for key in ("name", "version", "purpose", *MODES.values()):
            value = body.get(key)
            if value is None and key in MODES.values() and key != MODES[runs]:
                continue
            if not isinstance(value, str) or not value.strip():
                raise PackageError(f"Its package.json has no {key}.")
            fields[key] = value.strip()
        returns = body.get("returns")
        if not isinstance(returns, list) or not all(isinstance(r, str) and r for r in returns):
            raise PackageError("Its package.json's returns is not a list of file patterns.")
        story = body.get(STORY)
        if story is not None:
            if not isinstance(story, str) or not story.strip():
                raise PackageError("Its package.json's story is not a file's path.")
            if runs != PAGE:
                raise PackageError("Its package.json names a story, but only a page package "
                                   "tells one.")
            fields[STORY] = story.strip()
        return cls(returns=tuple(returns), runs=runs, **fields)

    def named_files(self) -> dict[str, str]:
        """The files package.json names, by field, which the zip must hold."""
        return {key: getattr(self, key) for key in (*MODES.values(), STORY)
                if getattr(self, key)}

    def as_json(self) -> str:
        return json.dumps({"name": self.name, "version": self.version, "purpose": self.purpose,
                           "runs": self.runs, **self.named_files(),
                           "returns": list(self.returns)}, indent=2)


def _inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def _within(root: Path, relative: str) -> Path:
    """`relative` under `root`, refused if it is absolute or climbs out."""
    pure = PurePosixPath(relative)
    target = (root / relative).resolve()
    if pure.is_absolute() or "\\" in relative or ".." in pure.parts \
            or not _inside(root.resolve(), target):
        raise PackageError(f"{relative!r} is outside the package.")
    return target


@dataclass(frozen=True)
class Package:
    folder: Path
    manifest: Manifest
    # The attachments it was opened from (their ids' letters and digits),
    # first one first; a package unpacked before these were recorded has
    # only the start of its first one's, from its folder's name.
    sources: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.folder.name

    @property
    def source(self) -> str:
        """The start of the attachment id it was first unpacked from."""
        return (self.sources[0] if self.sources else self.id.rsplit("-", 1)[-1])[:8]

    def text(self, relative: Optional[str]) -> str:
        """One of its Markdown files (its skill or brief), or "" for none."""
        if not relative:
            return ""
        return _within(self.folder, relative).read_text(encoding="utf-8", errors="replace")

    def skill_text(self) -> str:
        return self.text(self.manifest.skill)

    def brief_text(self) -> str:
        return self.text(self.manifest.brief)

    def story(self) -> Optional[package_story.Story]:
        """The story it tells, or None for a package without one; a story
        file changed since it was unpacked and no longer valid is refused."""
        if not self.manifest.story:
            return None
        try:
            raw = page_file(self, self.manifest.story)
            return package_story.parse(raw)
        except package_story.StoryError as e:
            raise PackageError(f"{self.id}'s story cannot be told: {e}") from None

    def results(self) -> list[Path]:
        """Every regular file a `returns` pattern matches, in the package's
        folder, each once, in order."""
        root, found = self.folder.resolve(), []
        for pattern in self.manifest.returns:
            if PurePosixPath(pattern).is_absolute() or ".." in PurePosixPath(pattern).parts:
                continue
            for path in sorted(root.glob(pattern)):
                if path.is_file() and not path.is_symlink() and _inside(root, path.resolve()) \
                        and path not in found:
                    found.append(path)
        return found


def _load(folder: Path) -> Package:
    try:
        raw = (folder / MANIFEST).read_bytes()
    except OSError:
        raise PackageError(f"{folder.name} has no {MANIFEST}.") from None
    recorded = (_record(folder.name) or {}).get("attachments")
    sources = tuple(a for a in recorded if isinstance(a, str)) if isinstance(recorded, list) \
        else ()
    return Package(folder, Manifest.parse(raw), sources or (folder.name.rsplit("-", 1)[-1],))


# -- recognising and unpacking --------------------------------------------------------

# The largest package.json read.
MANIFEST_MAX_BYTES = 256 * 1024


@dataclass(frozen=True)
class Recognition:
    """Whether a checked archive is a work package. `manifest` is None when
    it is not, and `reason` says why; `root` is the folder of the archive its
    package.json is in ("" or "<wrapper>/"), which every path package.json
    names is relative to."""
    manifest: Optional[Manifest]
    root: str = ""
    reason: str = ""


def recognise(archive: zipfile.ZipFile, listing: bundle_archive.Listing) -> Recognition:
    """Find a work package's package.json in a checked archive: at its root,
    or in its one wrapper folder (macOS metadata set aside). A zip without
    one, with several candidates, or whose package.json is not a JARVIS
    manifest is not a package; it is still an ordinary archive."""
    files = listing.files()
    candidates = sorted(path for path in files
                        if path.split("/")[-1] == MANIFEST and path.count("/") <= 1)
    root = listing.root
    if MANIFEST in files:
        root = ""
    elif f"{root}{MANIFEST}" not in files:
        if len(candidates) > 1:
            return Recognition(None, reason=(
                f"That zip has several {MANIFEST} files ({', '.join(candidates)}) and none at "
                f"its root, so which package it is cannot be told."))
        if candidates:
            return Recognition(None, reason=(
                f"Its {MANIFEST} is in {candidates[0].split('/')[0]}/, but that is not the "
                f"zip's only folder, so it is not a package."))
        return Recognition(None, reason=(f"That zip has no {MANIFEST} at its root or in one "
                                         f"folder, so it is not a package."))
    try:
        raw = bundle_archive.read_member(archive, files[f"{root}{MANIFEST}"],
                                         limit=MANIFEST_MAX_BYTES)
        manifest = Manifest.parse(raw)
    except bundle_archive.ArchiveError as e:
        return Recognition(None, root, f"Its {MANIFEST} cannot be read: {e}")
    except PackageError as e:
        return Recognition(None, root, str(e))
    for key, named in manifest.named_files().items():
        member = files.get(f"{root}{named}")
        if member is None:
            return Recognition(None, root, f"Its {key} file {named!r} is not in the zip.")
    if manifest.story:
        try:
            package_story.parse(bundle_archive.read_member(
                archive, files[f"{root}{manifest.story}"],
                limit=package_story.STORY_MAX_BYTES))
        except bundle_archive.ArchiveError as e:
            return Recognition(None, root, f"Its story file cannot be read: {e}")
        except package_story.StoryError as e:
            return Recognition(None, root, f"Its story file {manifest.story!r} is not valid: {e}")
    return Recognition(manifest, root)


def unquarantine(folder: Path) -> None:
    """Take macOS's downloaded-file mark off everything in `folder`, so its
    scripts run without Gatekeeper asking."""
    subprocess.run(["/usr/bin/xattr", "-dr", QUARANTINE_ATTRIBUTE, str(folder)],
                   check=False, timeout=30, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


# Which archive each package was unpacked from: one JSON record per package
# folder, {"sha256": ..., "attachments": [...]}, in a folder `every` skips.
RECORDS = ".records"


def _record_path(folder_name: str) -> Path:
    return data_paths.packages_dir() / RECORDS / f"{folder_name}.json"


def _record(folder_name: str) -> Optional[dict]:
    try:
        body = json.loads(_record_path(folder_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _write_record(folder_name: str, sha256: str, attachment: str) -> None:
    """Record that `attachment` (whose bytes hash to `sha256`) is this
    package, keeping the attachments recorded before it."""
    body = _record(folder_name) or {}
    attachments = [a for a in body.get("attachments", []) if isinstance(a, str)]
    if attachment and attachment not in attachments:
        attachments.append(attachment)
    path = _record_path(folder_name)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    temp = path.with_name(f"{PARTIAL}{uuid.uuid4()}")
    temp.write_text(json.dumps({"sha256": sha256, "attachments": attachments}),
                    encoding="utf-8")
    os.replace(temp, path)


def _unpacked_from(sha256: str) -> Optional[Path]:
    """The package folder already unpacked from an archive with these bytes."""
    records = data_paths.packages_dir() / RECORDS
    if not records.is_dir():
        return None
    for path in sorted(records.glob("*.json")):
        folder = data_paths.packages_dir() / path.stem
        body = _record(path.stem)
        if body and body.get("sha256") == sha256 and folder.is_dir() \
                and not folder.is_symlink():
            return folder
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _attachment_key(attachment_id: str) -> str:
    """An attachment id as it is recorded: its letters and digits, lowercase."""
    return re.sub(r"[^A-Za-z0-9]", "", str(attachment_id or "")).lower()


def _label(manifest: Manifest, suffix: str) -> str:
    return "-".join(_UNSAFE_NAME.sub("-", part).strip(".-")[:40] or "package"
                    for part in (manifest.name, manifest.version, suffix or "x"))


def unpack(archive_path: Path, attachment_id: str,
           clear_mark: Optional[Callable[[Path], None]] = None) -> tuple[Package, bool]:
    """Unpack a package zip into its own folder: (the package, whether it
    was unpacked now rather than already). Its package.json is at the zip's
    root or in its one wrapper folder, and every file it names is beside
    it; the package's folder holds what is in that folder. Opening the same
    bytes again, from this attachment or another, gives the package already
    unpacked from them."""
    sha256 = _sha256(archive_path)
    attachment = _attachment_key(attachment_id)
    already = _unpacked_from(sha256)
    if already is not None:
        _write_record(already.name, sha256, attachment)
        return _load(already), False
    try:
        with bundle_archive.open_archive(archive_path) as archive:
            listing = bundle_archive.list_members(archive)
            found = recognise(archive, listing)
            if found.manifest is None:
                raise PackageError(found.reason)
            final = data_paths.packages_dir() / _label(found.manifest, attachment[:8])
            if final.is_dir():
                recorded = _record(final.name)
                if recorded is None or recorded.get("sha256") == sha256:
                    # Unpacked before records were kept, from this attachment.
                    _write_record(final.name, sha256, attachment)
                    return _load(final), False
                final = data_paths.packages_dir() / _label(found.manifest, sha256[:16])
            try:
                bundle_archive.extract(archive, listing, final, folder=found.root,
                                       keep_modes=True)
            except FileExistsError:
                return _load(final), False
    except bundle_archive.ArchiveError as e:
        raise PackageError(str(e)) from None
    _write_record(final.name, sha256, attachment)
    try:
        (clear_mark or unquarantine)(final)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("package %s: the quarantine mark was not removed (%s)", final.name,
                    type(e).__name__)
    log.info("package %s unpacked from attachment %s", final.name, attachment[:8])
    return _load(final), True


# -- the packages here ----------------------------------------------------------------

def every() -> list[Package]:
    """Every package unpacked here, newest first; a folder that does not
    hold a valid package is left out."""
    out = []
    folders = [p for p in data_paths.packages_dir().iterdir()
               if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")]
    for folder in sorted(folders, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(_load(folder))
        except PackageError:
            continue
    return out


def find(reference: str, runs: Optional[str] = None) -> Package:
    """A package by its folder name (its id), or by its own name when only
    one package here has it; with `runs`, only one that runs that way."""
    reference = str(reference or "").strip()
    packages = every()
    found = ([p for p in packages if p.id == reference]
             or [p for p in packages if p.manifest.name == reference])
    if len(found) == 1:
        if runs and found[0].manifest.runs != runs:
            raise PackageError(f"{found[0].id} runs as {found[0].manifest.runs}, "
                               f"not as {runs}.")
        return found[0]
    ids = ", ".join(p.id for p in (found or packages)) or "none"
    if found:
        raise PackageError(f"More than one package is called {reference}: {ids}. Use its id.")
    raise PackageError(f"There is no package {reference!r} here. Packages here: {ids}.")


# -- running a step -------------------------------------------------------------------

def command_env(package: Package) -> dict[str, str]:
    """What a step finds besides the person's own environment: its folder,
    the Python JARVIS runs on, and JARVIS's source to import from."""
    return {"PACKAGE_DIR": str(package.folder), "JARVIS_PYTHON": sys.executable,
            "JARVIS_SOURCE": str(data_paths.PACKAGE_DIR.parent)}


def terminal_command(package: Package, command: str) -> str:
    """The same step, typed into a Terminal window he can see and answer."""
    exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in command_env(package).items())
    return f"cd {shlex.quote(str(package.folder))} && export {exports} && {command}"


@dataclass(frozen=True)
class Ran:
    exit_code: Optional[int]          # None: stopped at the timeout
    output: str                       # the last OUTPUT_TAIL_CHARS of stdout and stderr


async def run(package: Package, command: str,
              timeout: float = COMMAND_TIMEOUT_SEC) -> Ran:
    """Run one shell command in the package's folder, stdout and stderr
    together, no input; stopped, with everything it started, at `timeout`."""
    proc = await asyncio.create_subprocess_exec(
        "/bin/bash", "-c", command, cwd=str(package.folder),
        env={**os.environ, **command_env(package)}, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True)
    tail = bytearray()

    async def read() -> None:
        while chunk := await proc.stdout.read(65536):
            tail.extend(chunk)
            del tail[:-OUTPUT_TAIL_CHARS * 4]

    try:
        await asyncio.wait_for(asyncio.gather(read(), proc.wait()), timeout)
        code: Optional[int] = proc.returncode
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        code = None
    text = tail.decode("utf-8", errors="replace")[-OUTPUT_TAIL_CHARS:]
    log.info("package %s: a step finished (%s)", package.id,
             "timed out" if code is None else f"exit {code}")
    return Ran(code, text)



# -- a page package's frame -------------------------------------------------------------

def page_file(package: Package, relative: str) -> bytes:
    """One of a page package's files, for its frame: read without following
    a link at any step, and refused outside its folder, hidden, not a
    regular file, or over PAGE_FILE_MAX_BYTES."""
    pure = PurePosixPath(relative or "")
    refused = PackageError(f"{relative!r} is not a file of {package.id}'s page.")
    if not pure.parts or pure.is_absolute() or "\\" in relative \
            or any(p.startswith(".") for p in pure.parts):
        raise refused
    try:
        with folder_readers.opened_file(package.folder, Path(*pure.parts)) as (fd, info):
            if info.st_size > PAGE_FILE_MAX_BYTES:
                raise PackageError(f"{relative!r} is too large to serve.")
            return os.pread(fd, info.st_size, 0) if info.st_size else b""
    except FolderError:
        raise refused from None


def result_name(name: str) -> list[str]:
    """The parts of a name the page may save under results/: at most
    RESULT_MAX_PARTS, separated by '/', each letters, digits, '.', '_' and
    '-' and starting with a letter or a digit."""
    parts = str(name or "").split("/")
    if not 1 <= len(parts) <= RESULT_MAX_PARTS \
            or not all(_RESULT_PART.fullmatch(p) for p in parts):
        raise PackageError(f"{name!r} is not a name a result can be saved under: letters, "
                           f"digits, '.', '_' and '-', in at most {RESULT_MAX_PARTS} parts "
                           f"separated by '/'.")
    return parts


def save_result(package: Package, name: str, data: bytes) -> Path:
    """Write `data` as results/<name> in the package's folder, replacing a
    file of that name, and return its path. Every folder on the way is made
    or opened without following a link, and the file is written beside its
    name and renamed into place, so it is never seen half written."""
    parts = result_name(name)
    if len(data) > RESULT_MAX_BYTES:
        raise PackageError(f"That result is {len(data)} bytes; at most {RESULT_MAX_BYTES} "
                           f"may be saved.")
    folders = [RESULTS, *parts[:-1]]
    try:
        for depth, part in enumerate(folders):
            with folder_readers.opened_dir(package.folder, Path(*folders[:depth])) as fd:
                try:
                    os.mkdir(part, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
        with folder_readers.opened_dir(package.folder, Path(*folders)) as fd:
            temp = f"{PARTIAL}{uuid.uuid4()}"
            out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                          dir_fd=fd)
            try:
                with os.fdopen(out, "wb") as f:
                    f.write(data)
                os.replace(temp, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)
            except BaseException:
                try:
                    os.unlink(temp, dir_fd=fd)
                except OSError:
                    pass
                raise
    except (FolderError, OSError):
        raise PackageError(f"{name!r} could not be saved in {package.id}'s results.") from None
    log.info("package %s: its page saved a result (%d bytes)", package.id, len(data))
    return package.folder / RESULTS / Path(*parts)


# -- an agent package's run ---------------------------------------------------------------

def agent_brief(package: Package) -> str:
    """What an agent package's run is asked to do: its brief, and where
    the files that go back to the sender must be."""
    return (f"{package.brief_text()}\n\n"
            f"(You are in the folder of work package {package.id}. The files that go back "
            f"to the person who sent it are the ones matching: "
            f"{', '.join(package.manifest.returns)}. Write them there, relative to this "
            f"folder.)")
