"""Versioned upstream language kit, outside application source and the inbox.

The publisher is trusted executable code; documents are untrusted data. Node's
filesystem permissions contain imports, and the macOS process sandbox denies
network access and writes outside the private operation directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import resource   # POSIX only; the kit runs only under the macOS sandbox
except ImportError:   # Windows: the kit is unavailable there (no sandbox-exec)
    resource = None

from jarvis import bundle_archive, data_paths

REPOSITORY = "4thWAIV/agent-guard"
PINNED_VERSION = "0.1.108172209-pre-release"
PINNED_COMMIT = "c918581ffda7a6fad7c05dde13f54aeb5760fdfb"
PINNED_DIGEST = "0112ee38bec63affd08d952d249336c6dbf8602076baab94ff7982b996297ef0"
ASSET = "evolang-kit.zip"
REQUIRED_CHECKS = frozenset({"gate", "release"})
REQUIRED_FILES = frozenset({"kit.js", "page.js", "metalang.yaml", "arch.yaml",
                            "propose.yaml", "style.css", "syntax.css", "package.json", "README.md"})
SUPPORTED_NODE_MAJORS = frozenset({22, 23, 24})
MAX_SOURCE_BYTES = 1024 * 1024
MAX_VIEW_BYTES = 8 * 1024 * 1024
BUDGETS = bundle_archive.Budgets(max_entries=32, max_file_bytes=2 * 1024 * 1024,
                                 max_total_bytes=8 * 1024 * 1024, max_depth=3,
                                 max_seconds=10)


class KitError(ValueError):
    """A visible installation, runtime or document validation diagnostic."""


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def root() -> Path:
    return data_paths.data_dir() / "language-kits"


def _atomic_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".partial-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(body, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@dataclass(frozen=True)
class Release:
    version: str
    commit: str
    digest: str
    checks: tuple[str, ...]
    repository: str = REPOSITORY

    def validate(self) -> None:
        import re
        if self.repository != REPOSITORY:
            raise KitError("The kit publisher is not the configured repository.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", self.version):
            raise KitError("The kit version is not a safe release tag.")
        if not re.fullmatch(r"[0-9a-f]{40}", self.commit) or not re.fullmatch(r"[0-9a-f]{64}", self.digest):
            raise KitError("The release needs its resolved source commit and SHA256 digest.")
        if not REQUIRED_CHECKS <= set(self.checks):
            raise KitError("The resolved source commit has not passed the required gate and release checks.")


@dataclass(frozen=True)
class RunResult:
    ok: bool
    exit_code: int
    diagnostics: str
    runtime: str
    view: bytes | None = None


class Kit:
    def __init__(self, directory: Path):
        if directory.is_symlink():
            raise KitError("An installed kit version cannot be a symbolic link.")
        self.directory = directory.resolve()
        try:
            self.record = json.loads((self.directory / "installation.json").read_text())
            self.release = Release(**self.record["release"])
            self.release.validate()
            self.verify()
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise KitError(f"The installed kit cannot be read: {error}") from error

    def verify(self) -> None:
        import re
        if not isinstance(self.record.get("files"), dict):
            raise KitError("The installed kit file manifest is malformed.")
        for name, digest in self.record["files"].items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise KitError("The installed kit file identity is malformed.")
            path = self.directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > BUDGETS.max_file_bytes or sha256(path.read_bytes()) != digest:
                raise KitError(f"The installed kit file {name} changed; restore the pinned kit.")
        if not REQUIRED_FILES <= set(self.record["files"]):
            raise KitError("The installed kit is incomplete.")

    def run(self, command: str, source: bytes) -> RunResult:
        if command not in {"check", "render"}:
            raise KitError("The kit supports check and render.")
        if len(source) > MAX_SOURCE_BYTES:
            raise KitError(f"The document exceeds {MAX_SOURCE_BYTES} bytes.")
        self.verify()
        node = shutil.which("node")
        if node is None:
            raise KitError("Node is missing; install a supported Node runtime.")
        try:
            runtime = subprocess.run([node, "--version"], capture_output=True, text=True,
                                     timeout=5, check=True).stdout.strip()
            major = int(runtime.lstrip("v").split(".")[0])
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise KitError(f"The Node runtime could not be inspected: {error}") from error
        if major not in SUPPORTED_NODE_MAJORS:
            raise KitError(f"Node {runtime} is outside the runtime policy: Node 22, 23 or 24.")
        sandbox = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
        if sandbox is None:
            raise KitError("The supported macOS process sandbox is unavailable; the document was not executed.")
        with tempfile.TemporaryDirectory(prefix="language-kit-") as name:
            work = Path(name).resolve()
            document, view = work / "document.yaml", work / "view.html"
            document.write_bytes(source)
            node = str(Path(node).resolve())
            read_roots = (self.directory, work, Path(node).parent.parent)
            reads = " ".join(f"(subpath {json.dumps(str(path))})" for path in read_roots)
            # Keep macOS runtime/system reads, while denying user files and
            # other temporary documents. Node further restricts fs reads to
            # the kit and this operation's input root on every platform path.
            profile = ('(version 1)(allow default)(deny network*)(deny file-write*)'
                       '(deny file-read-data (subpath "/Users") (subpath "/private/tmp") (subpath "/private/var/folders"))'
                       f'(allow file-read-data {reads})'
                       f'(allow file-write* (subpath {json.dumps(str(work))}))')
            args = [sandbox, "-p", profile, node, "--permission" if major >= 24 else "--experimental-permission",
                    f"--allow-fs-read={self.directory}", f"--allow-fs-read={work}",
                    f"--allow-fs-write={view}", "--max-old-space-size=128",
                    str(self.directory / "kit.js"), command, str(document)]
            if command == "render":
                args.append(str(view))
            # Strip inherited Node hooks and executable search paths. The bundle
            # is self-contained; it needs neither credentials nor agent context.
            env = {"PATH": str(Path(node).parent), "HOME": str(work), "TMPDIR": str(work),
                   "LANG": "en_US.UTF-8"}
            def limits():
                resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
                resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_VIEW_BYTES, MAX_VIEW_BYTES))
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(args, cwd=work, env=env, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True,
                                           preexec_fn=limits)
                try:
                    code = process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise KitError("The kit exceeded its 15 second runtime limit.") from None
                output.seek(0)
                raw_diagnostics = output.read(64 * 1024 + 1)
                diagnostics = raw_diagnostics[:64 * 1024].decode("utf-8", "replace")
                if len(raw_diagnostics) > 64 * 1024:
                    diagnostics += "\n[Diagnostics truncated after 65536 bytes; revise the draft and rerun for remaining findings.]"
                diagnostics = diagnostics.replace(str(work), "<document-root>").replace(str(self.directory), "<pinned-kit>")
            rendered = view.read_bytes() if code == 0 and command == "render" and view.exists() else None
            return RunResult(code == 0, code, diagnostics, runtime, rendered)


def stage(archive: Path, release: Release) -> Kit:
    """Verify, safely extract and smoke-test an immutable candidate; never activate it."""
    release.validate()
    if archive.stat().st_size > 8 * 1024 * 1024 or sha256(archive.read_bytes()) != release.digest:
        raise KitError("The kit asset SHA256 differs from the release metadata.")
    versions = root() / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    target = versions / release.digest
    if target.exists():
        existing = Kit(target)
        if (existing.release.version, existing.release.commit, existing.release.repository) != (release.version, release.commit, release.repository):
            raise KitError("The installed asset digest is already bound to different release provenance.")
        return existing
    with tempfile.TemporaryDirectory(dir=versions, prefix=".stage-") as name:
        unpacked = Path(name) / "kit"
        try:
            with bundle_archive.open_archive(archive) as zipped:
                listing = bundle_archive.list_members(zipped, BUDGETS)
                bundle_archive.extract(zipped, listing, unpacked, folder=listing.wrapper, budgets=BUDGETS)
        except bundle_archive.ArchiveError as error:
            raise KitError(f"The kit archive was refused: {error}") from error
        manifest = json.loads((unpacked / "package.json").read_text())
        if manifest != {"name": "evolang-kit", "version": release.version,
                        "private": True, "type": "module"}:
            raise KitError("The kit manifest does not match the release.")
        files = {p.name: sha256(p.read_bytes()) for p in unpacked.iterdir() if p.is_file()}
        if not REQUIRED_FILES <= files.keys() or any(p.is_dir() for p in unpacked.iterdir()):
            raise KitError("The kit must carry the required flat distribution files.")
        _atomic_json(unpacked / "installation.json", {"release": asdict(release), "files": files,
                                                      "installed_at": time.time()})
        candidate = Kit(unpacked)
        source = {"scope": ["kit_smoke"], "language": "arch", "import": [["metalang"]],
                  "title": "Kit adoption fixture", "declare": [{"constant": {"id": "Sample", "is": "element", "value": {
                      "form": "record", "does": "Carries the adoption fixture data.", "reach": "internal", "exactness": "loose",
                      "groups": "none", "members": "none", "sits": {"package": "fixture", "assembly": "none", "namespace": "none"},
                      "relates": {"implements": "none", "answers": "none", "holds": "none"}}}}]}
        raw = json.dumps(source).encode()
        fixtures = {"valid": (raw, True), "malformed": (b"language: [", False)}
        for name, field, value in (("invalid-form", "form", "spaceship"), ("pending-members", "members", "pending")):
            changed = json.loads(raw)
            changed["declare"][0]["constant"]["value"][field] = value
            fixtures[name] = (json.dumps(changed).encode(), False)
        fixtures["missing-language"] = (json.dumps({**source, "language": "jarvis_missing_language_fixture"}).encode(), False)
        fixtures["import-escape"] = (json.dumps({**source, "import": [["..", "..", "outside"]]}).encode(), False)
        outcomes = {}
        for name, (document, expected) in fixtures.items():
            check = candidate.run("check", document)
            outcomes[name] = check.exit_code == (0 if expected else 1)
            if not outcomes[name]:
                raise KitError(f"The kit fixture {name} failed (exit {check.exit_code}): {check.diagnostics}")
        render = candidate.run("render", raw)
        if not render.ok or not render.view:
            raise KitError(f"The kit render fixture failed (exit {render.exit_code}): {render.diagnostics}")
        _atomic_json(unpacked / "smoke.json", {"runtime": check.runtime, "fixtures": outcomes,
                                               "view_sha256": sha256(render.view)})
        os.rename(unpacked, target)
    return Kit(target)


def active(digest: str | None = None) -> Kit:
    try:
        key = digest or json.loads((root() / "active.json").read_text())["active"]
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise KitError("The pinned kit digest is invalid.")
        return Kit(root() / "versions" / key)
    except (OSError, KeyError, ValueError) as error:
        raise KitError(f"No usable pinned language kit: {error}") from error


def _select(digest: str, previous: str | None, held: list[str]) -> dict:
    state = {"active": digest, "previous": previous, "held_digests": sorted(set(held)), "activated_at": time.time()}
    _atomic_json(root() / "active.json", state)
    return state


def activate(kit: Kit, *, compatibility_reviewed: bool = False) -> dict:
    kit.verify()
    path = root() / "active.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    previous = old.get("active")
    held = old.get("held_digests", [])
    if kit.release.digest in held and not compatibility_reviewed:
        raise KitError("This candidate is held after rollback; review it before explicit activation.")
    if previous and previous != kit.release.digest and not compatibility_reviewed:
        prior = active(previous)
        changed = [name for name in ("metalang.yaml", "arch.yaml", "propose.yaml")
                   if prior.record["files"][name] != kit.record["files"][name]]
        if changed:
            raise KitError(f"Language compatibility review is required: {', '.join(changed)}.")
    return _select(kit.release.digest, previous if previous != kit.release.digest else old.get("previous"),
                   [digest for digest in held if digest != kit.release.digest] if compatibility_reviewed else held)


def rollback() -> dict:
    state = json.loads((root() / "active.json").read_text())
    if not state.get("previous"):
        raise KitError("There is no prior active kit to restore.")
    restored = active(state["previous"])
    held = state.get("held_digests", [])
    if restored.release.digest in held:
        raise KitError("The retained version was held after rollback; explicit reviewed activation is required.")
    return _select(restored.release.digest, state["active"], held + [state["active"]])
