"""Work packages: a zip a teammate sends in an ordinary message, which this
JARVIS unpacks and runs with the person, sending the results back as a
reply. The format is in docs/work-packages.md.

The team is a few trusted people on a closed network, so there is no
sandbox: the person's spoken yes (the brain's acting tools) is the gate
before a package is opened and before each step runs, and the results go
back through the ordinary draft, review and approval. What is kept is that
a zip entry may not land outside its package's folder.

    unpack       a downloaded zip into `<packages>/<name>-<version>-<id>/`,
                 every entry checked first, nothing written if one escapes,
                 the quarantine mark taken off so its scripts can run
    find, every  the packages unpacked here, by folder name (or by the
                 package's own name, when only one has it)
    run          one shell command in a package's folder, with a timeout,
                 its exit code and the last of its output
    results      the files a package's `returns` globs match, which go back
                 through draft_files.py (a package's folder is the one part
                 of JARVIS's data a draft may attach from)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from jarvis import data_paths
from jarvis.messaging_files import QUARANTINE_ATTRIBUTE

log = logging.getLogger("jarvis.work_packages")

MANIFEST = "package.json"
# A step that runs longer than this is stopped (its whole process group).
COMMAND_TIMEOUT_SEC = 600
# How much of a step's output comes back: its end, where the result is.
OUTPUT_TAIL_CHARS = 4000
PARTIAL = ".partial-"
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class PackageError(ValueError):
    """Why a package cannot be opened, found or run, in words he can hear."""


@dataclass(frozen=True)
class Manifest:
    """A package's package.json."""
    name: str
    version: str
    purpose: str
    skill: str
    returns: tuple[str, ...]

    @classmethod
    def parse(cls, raw: bytes) -> "Manifest":
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PackageError("Its package.json is not valid JSON.") from None
        if not isinstance(body, dict):
            raise PackageError("Its package.json is not an object.")
        fields = {}
        for key in ("name", "version", "purpose", "skill"):
            value = body.get(key)
            if not isinstance(value, str) or not value.strip():
                raise PackageError(f"Its package.json has no {key}.")
            fields[key] = value.strip()
        returns = body.get("returns")
        if not isinstance(returns, list) or not all(isinstance(r, str) and r for r in returns):
            raise PackageError("Its package.json's returns is not a list of file patterns.")
        return cls(returns=tuple(returns), **fields)

    def as_json(self) -> str:
        return json.dumps({"name": self.name, "version": self.version, "purpose": self.purpose,
                           "skill": self.skill, "returns": list(self.returns)}, indent=2)


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

    @property
    def id(self) -> str:
        return self.folder.name

    def skill_text(self) -> str:
        return _within(self.folder, self.manifest.skill).read_text(encoding="utf-8",
                                                                    errors="replace")

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
    return Package(folder, Manifest.parse(raw))


# -- unpacking ------------------------------------------------------------------------

def _entries(archive: zipfile.ZipFile, root: Path) -> list[zipfile.ZipInfo]:
    """Every entry, checked before anything is written: one that would land
    outside `root`, or a link, refuses the whole zip."""
    entries = archive.infolist()
    for info in entries:
        _within(root, info.filename)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise PackageError(f"{info.filename!r} is a link; packages carry files only.")
    return entries


def unquarantine(folder: Path) -> None:
    """Take macOS's downloaded-file mark off everything in `folder`, so its
    scripts run without Gatekeeper asking."""
    subprocess.run(["/usr/bin/xattr", "-dr", QUARANTINE_ATTRIBUTE, str(folder)],
                   check=False, timeout=30, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def unpack(archive_path: Path, short_id: str,
           clear_mark: Optional[Callable[[Path], None]] = None) -> tuple[Package, bool]:
    """Unpack a package zip into its own folder: (the package, whether it
    was unpacked now rather than already). Its package.json must be at the
    zip's root and name a skill file inside it."""
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile):
        raise PackageError("That file is not a zip.") from None
    with archive:
        try:
            manifest = Manifest.parse(archive.read(MANIFEST))
        except KeyError:
            raise PackageError(f"That zip has no {MANIFEST} at its root, so it is not "
                               f"a package.") from None
        names = set(archive.namelist())
        if manifest.skill not in names:
            raise PackageError(f"Its skill file {manifest.skill!r} is not in the zip.")
        label = "-".join(_UNSAFE_NAME.sub("-", part).strip(".-")[:40] or "package"
                         for part in (manifest.name, manifest.version,
                                      _UNSAFE_NAME.sub("", short_id)[:8] or "x"))
        final = data_paths.packages_dir() / label
        if final.is_dir():
            return _load(final), False
        staging = data_paths.packages_dir() / f"{PARTIAL}{uuid.uuid4()}"
        entries = _entries(archive, staging)
        staging.mkdir(mode=0o700)
        try:
            for info in entries:
                written = Path(archive.extract(info, staging))
                mode = (info.external_attr >> 16) & 0o777
                if mode and not info.is_dir():
                    os.chmod(written, mode | 0o600)
            os.replace(staging, final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    try:
        (clear_mark or unquarantine)(final)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("package %s: the quarantine mark was not removed (%s)", label,
                    type(e).__name__)
    log.info("package %s unpacked from attachment %s", label, short_id)
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


def find(reference: str) -> Package:
    """A package by its folder name (its id), or by its own name when only
    one package here has it."""
    reference = str(reference or "").strip()
    packages = every()
    found = ([p for p in packages if p.id == reference]
             or [p for p in packages if p.manifest.name == reference])
    if len(found) == 1:
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

