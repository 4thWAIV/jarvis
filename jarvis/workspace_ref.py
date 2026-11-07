"""Workspace identity and references (design section 4): the one place they
are defined, parsed, printed, hashed and resolved.

A reference names one thing a workspace can find again:

    jref:1:<node>:<kind>:<id>[@<revision>][/<percent-encoded path>][#sha256=<hex>|#tree=<hex>]

`parse` and `format` are the only parser and printer; the `1` lets the
grammar change later. A path is relative, POSIX and NFC, and passes
`bundle_archive`'s entry rule (`check_path`), so a reference can always
become a member path. A folder reference names the folder's ID, never its
location: the location is looked up when it is resolved, so a reconnected
folder keeps every reference, and `revision` is the location revision
current when it was made, so the resolver can say the folder moved.

Hashes: a file is SHA-256 of its bytes; a tree is a Merkle hash (each entry
the line `<mode> <hash> <name>\\n`, mode `f`, `x` or `d`, entries sorted by
their name's UTF-8 bytes; the tree's hash is SHA-256 of the joined lines);
a record is SHA-256 of its canonical JSON (`canonical_json`: sorted keys,
no spaces, UTF-8, local bookkeeping such as `revision` removed).

`resolve` is the one resolver: nothing else turns a reference into bytes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, unquote

from jarvis import bundle_archive, data_paths
from jarvis.bundle_contract import PATH_MAX_CHARS

log = logging.getLogger(__name__)

VERSION = "1"
PREFIX = f"jref:{VERSION}:"
LOCAL = "local"

# A tree is walked within these, or refused as too large: a reference is
# made to something a person picked, not to a whole disk.
TREE_ENTRIES_MAX = 20_000
TREE_SECONDS = 30.0
# Git-ignored names a tree's listing summarises (design 4.3).
IGNORED_NAMES_MAX = 200
GIT_SECONDS = 10.0
# Bookkeeping a record's hash leaves out: it changes with every write and
# says nothing about what the record is.
LOCAL_FIELDS = frozenset({"revision", "updated_at", "created_at", "node_id", "owner_id"})


class RefKind(StrEnum):
    FOLDER_FILE = "folder_file"        # one file in a registered folder
    FOLDER_TREE = "folder_tree"        # a subtree of a registered folder, by Merkle tree hash
    NOTE = "note"                      # a Markdown note under brain_home: memory, projects, journal, topics
    PROJECT = "project"
    WORK = "work"
    ACCOMPLISHMENT = "accomplishment"
    RUN = "run"
    AGENT = "agent"                    # by session id
    WORKFLOW = "workflow"
    ATTACHMENT = "attachment"          # message id and attachment id, optionally a member path
    PACKAGE = "package"                # by package hash, or a draft id on this node
    TOPIC = "topic"                    # by slug
    KNOWLEDGE = "knowledge"            # an engine node version, by content hash
    # What the knowledge engine reads that the design's list leaves out:
    # one conversation line, one day of it, one workflow event.
    CONVERSATION = "conversation"
    CONVERSATION_DAY = "conversation_day"
    WORKFLOW_EVENT = "workflow_event"
    CANDIDATE = "candidate"            # a project candidate (project_events)


class RefError(ValueError):
    """A reference that does not parse, or a path the rule refuses."""


# ── identity ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Workspace:
    """What packages, the engine and references carry: this node, its
    owner, and the paired team server's pinned ID when signed in."""
    node_id: str
    owner: str
    server_id: Optional[str] = None


def owner_of(github_id: Optional[int]) -> str:
    """The one converter from a GitHub ID (as `attachment_locations` keeps
    it) to an owner: `github:<id>`, else `local`."""
    return f"github:{github_id}" if isinstance(github_id, int) and not isinstance(github_id, bool) else LOCAL


def is_self(node: str) -> bool:
    """Whether a reference made on `node` is this workspace's: the node ID
    now, or any this data directory had before (`node-id.previous`)."""
    return node == data_paths.node_id() or node in data_paths.previous_node_ids()


# ── the path rule ──────────────────────────────────────────────────────────

def check_path(path: str) -> str:
    """The path as a reference holds it: `bundle_archive`'s entry rule (no
    absolute path, backslash, `..` or control character), and canonical:
    NFC, no empty or `.` component, at most PATH_MAX_CHARS. Anything else
    is refused, never repaired, so one path has one spelling."""
    text = unicodedata.normalize("NFC", str(path or ""))
    try:
        clean = bundle_archive.normalised(text)
    except bundle_archive.ArchiveError as e:
        raise RefError(f"not a usable path: {e.reason}") from None
    if clean != text:
        raise RefError("a path is written relative, with no empty or '.' part")
    if len(clean) > PATH_MAX_CHARS:
        raise RefError(f"a path is at most {PATH_MAX_CHARS} characters")
    return clean


# ── the reference ──────────────────────────────────────────────────────────

_ID = re.compile(r"[A-Za-z0-9._:-]{1,200}")
_NODE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_HEX = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class WorkspaceRef:
    node: str                          # the workspace that can resolve it
    kind: RefKind
    id: str
    revision: Optional[int] = None     # the record's local revision when the reference was made
    path: Optional[str] = None         # relative, POSIX, NFC (check_path)
    sha256: Optional[str] = None       # file bytes, tree hash, or canonical record hash
    size: Optional[int] = None         # not in the text form
    tree: bool = False                 # sha256 is a tree hash

    def __post_init__(self) -> None:
        if not _NODE.fullmatch(self.node or ""):
            raise RefError("a reference's node is an ID")
        if not _ID.fullmatch(self.id or ""):
            raise RefError("a reference's id is 1 to 200 letters, digits or . _ : -")
        if self.revision is not None and (not isinstance(self.revision, int) or self.revision < 0):
            raise RefError("a revision is a whole number")
        if self.path is not None:
            check_path(self.path)
        if self.sha256 is not None and not _HEX.fullmatch(self.sha256):
            raise RefError("a hash is 64 lowercase hex digits")

    def __str__(self) -> str:
        return format(self)


def ref(kind: str, ident: str, path: Optional[str] = None, *, node: Optional[str] = None,
        revision: Optional[int] = None, sha256: Optional[str] = None, size: Optional[int] = None,
        tree: bool = False) -> WorkspaceRef:
    """A reference made here (this node unless another is given)."""
    return WorkspaceRef(node or data_paths.node_id(), RefKind(kind), str(ident), revision, path,
                        sha256, size, tree)


def format(r: WorkspaceRef) -> str:  # noqa: A001 - the design's name for the one printer
    out = f"{PREFIX}{r.node}:{r.kind.value}:{r.id}"
    if r.revision is not None:
        out += f"@{r.revision}"
    if r.path is not None:
        out += "/" + quote(r.path, safe="/")
    if r.sha256 is not None:
        out += ("#tree=" if r.tree else "#sha256=") + r.sha256
    return out


_TEXT = re.compile(r"jref:(?P<v>[0-9]+):(?P<node>[^:/@#]+):(?P<kind>[a-z_]+):(?P<id>[^@/#]+)"
                   r"(?:@(?P<rev>[0-9]+))?(?:/(?P<path>[^#]*))?(?:#(?P<h>sha256|tree)=(?P<hex>[0-9a-f]{64}))?")


def parse(text: str) -> WorkspaceRef:
    m = _TEXT.fullmatch(str(text or ""))
    if m is None:
        raise RefError("not a reference (jref:1:<node>:<kind>:<id>...)")
    if m["v"] != VERSION:
        raise RefError(f"reference version {m['v']} is not one this JARVIS reads")
    try:
        kind = RefKind(m["kind"])
    except ValueError:
        raise RefError(f"no such kind of reference: {m['kind']}") from None
    path = unquote(m["path"]) if m["path"] is not None else None
    parsed = WorkspaceRef(m["node"], kind, m["id"], int(m["rev"]) if m["rev"] else None, path,
                          m["hex"], None, m["h"] == "tree")
    if format(parsed) != text:
        raise RefError("a reference has one spelling; that is not it")
    return parsed


# ── hashes ─────────────────────────────────────────────────────────────────

def canonical_json(value: Any, *, drop: frozenset = LOCAL_FIELDS) -> bytes:
    """Sorted keys, no spaces, UTF-8, with local bookkeeping removed at the
    top level: what records, manifests and knowledge nodes are hashed by."""
    if isinstance(value, dict):
        value = {k: v for k, v in value.items() if k not in drop}
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode("utf-8")


def record_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_hash(fd: int) -> tuple[str, int]:
    """(SHA-256, size) of an open file's bytes, read through `fd` from the
    start: the same descriptor that later packs it, so check and read are
    one open."""
    h, size, offset = hashlib.sha256(), 0, 0
    while True:
        chunk = os.pread(fd, 1 << 20, offset)
        if not chunk:
            return h.hexdigest(), size
        h.update(chunk)
        size += len(chunk)
        offset += len(chunk)


def tree_line(mode: str, digest: str, name: str) -> bytes:
    return f"{mode} {digest} {unicodedata.normalize('NFC', name)}\n".encode("utf-8")


def merkle(lines: list[tuple[bytes, bytes]]) -> str:
    """A tree's hash from (name as UTF-8, its line) pairs: sorted by name
    bytes, joined, hashed."""
    return hashlib.sha256(b"".join(line for _name, line in sorted(lines))).hexdigest()


# ── git, only where it really is (design 4.3) ─────────────────────────────

_git: Optional[bool] = None


def git_available() -> bool:
    """`xcode-select -p`, once per process: without the Command Line Tools
    `/usr/bin/git` is a stub that opens an install dialog, so no git command
    runs at all."""
    global _git
    if _git is None:
        try:
            _git = subprocess.run(["xcode-select", "-p"], capture_output=True,
                                  timeout=GIT_SECONDS).returncode == 0
        except (OSError, subprocess.SubprocessError):
            _git = False
    return _git


def _git_out(root: Path, *args: str) -> Optional[bytes]:
    if not git_available():
        return None
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=GIT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def git_identity(root: Path) -> Optional[dict]:
    """The repository a tree is in: its root commit (the oldest when there
    are several), HEAD, and whether the working tree differs from HEAD; None
    without git or outside a repository."""
    roots = _git_out(root, "rev-list", "--max-parents=0", "HEAD")
    head = _git_out(root, "rev-parse", "HEAD")
    if not roots or not head:
        return None
    status = _git_out(root, "status", "--porcelain", "--untracked-files=no")
    return {"root_commit": roots.decode().split()[-1], "head": head.decode().strip(),
            "dirty": bool(status and status.strip())}


def _git_lists(root: Path) -> dict:
    """What git says about a tree: ignored entries (folded to folders),
    submodules and tracked files the working tree no longer has, each as a
    set of paths relative to `root`; empty without git."""
    out = {"ignored": set(), "submodules": set(), "missing": set()}
    ignored = _git_out(root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory")
    if ignored is None:
        return out
    out["ignored"] = {p.decode("utf-8", "replace").rstrip("/") for p in ignored.split(b"\0") if p}
    staged = _git_out(root, "ls-files", "-z", "--stage") or b""
    for line in staged.split(b"\0"):
        if line.startswith(b"160000 "):
            out["submodules"].add(line.split(b"\t", 1)[1].decode("utf-8", "replace"))
    deleted = _git_out(root, "ls-files", "-z", "--deleted") or b""
    out["missing"] = {p.decode("utf-8", "replace") for p in deleted.split(b"\0") if p}
    return out


@dataclass
class Tree:
    sha256: str
    files: int
    left_out: list[dict] = field(default_factory=list)      # {path, reason}
    ignored: dict = field(default_factory=dict)              # {count, names, truncated}
    git: Optional[dict] = None

    def json(self) -> dict:
        return {"tree": self.sha256, "files": self.files, "left_out": self.left_out,
                "ignored": self.ignored, "git": self.git}


def tree_hash(root: Path, relative: Path, visible: Callable[[Path, bool], bool]) -> Tree:
    """The Merkle hash of `relative` inside `root`, every folder and file
    reached by descriptor without following a link (`folder_readers`).
    Left out, each with its reason: links, devices, submodules, what
    `visible` refuses, and tracked files the working tree no longer has.
    Git-ignored entries are summarised, not listed. Refused as `too_large`
    past TREE_ENTRIES_MAX entries or TREE_SECONDS."""
    from jarvis import folder_readers
    from jarvis.folder_contract import FolderError
    base = root / relative
    lists = _git_lists(base)
    left: list[dict] = [{"path": p, "reason": "missing"} for p in sorted(lists["missing"])]
    deadline = time.monotonic() + TREE_SECONDS
    seen = [0]

    def walk(rel: Path) -> tuple[str, int]:
        lines: list[tuple[bytes, bytes]] = []
        files = 0
        with folder_readers.opened_dir(root, relative / rel) as fd:
            with os.scandir(fd) as it:
                items = sorted(it, key=lambda e: e.name.encode("utf-8", "surrogateescape"))
            for item in items:
                seen[0] += 1
                if seen[0] > TREE_ENTRIES_MAX or time.monotonic() > deadline:
                    raise FolderError("too_large")
                here = rel / item.name
                key = here.as_posix()
                if item.name == ".git" or key in lists["ignored"]:
                    continue
                info = item.stat(follow_symlinks=False)
                is_dir = stat.S_ISDIR(info.st_mode)
                if key in lists["submodules"]:
                    left.append({"path": key, "reason": "special_file"})
                    continue
                if not visible(relative / here, is_dir):
                    left.append({"path": key, "reason": "refused"})
                    continue
                name = item.name.encode("utf-8", "surrogateescape")
                if is_dir:
                    digest, inner = walk(here)
                    lines.append((name, tree_line("d", digest, item.name)))
                    files += inner
                elif stat.S_ISREG(info.st_mode):
                    with folder_readers.opened_file(root, relative / here) as (ffd, _st):
                        digest, _size = file_hash(ffd)
                    mode = "x" if info.st_mode & 0o111 else "f"
                    lines.append((name, tree_line(mode, digest, item.name)))
                    files += 1
                else:
                    left.append({"path": key, "reason": "link" if stat.S_ISLNK(info.st_mode) else "special_file"})
        return merkle(lines), files

    digest, files = walk(Path())
    names = sorted(lists["ignored"])
    return Tree(digest, files, sorted(left, key=lambda x: x["path"]),
                {"count": len(names), "names": names[:IGNORED_NAMES_MAX],
                 "truncated": len(names) > IGNORED_NAMES_MAX},
                git_identity(base))


# ── resolving ──────────────────────────────────────────────────────────────

class Outcome(StrEnum):
    HERE = "here"                      # the same bytes (or record) are there
    CHANGED = "changed"                # other bytes at the path, or the record moved on
    MOVED = "moved"                    # the folder's location moved and the path is at the new place
    MISSING = "missing"
    REFUSED = "refused"                # with the folder contract's (or the store's) code
    FOREIGN_NODE = "foreign_node"      # another workspace's reference; nothing is read
    UNSUPPORTED = "unsupported"        # a kind this JARVIS does not resolve yet


@dataclass
class Resolved:
    outcome: Outcome
    code: Optional[str] = None         # a refusal's code
    sha256: Optional[str] = None       # what is there now
    size: Optional[int] = None
    revision: Optional[int] = None     # the location or record revision now
    detail: dict = field(default_factory=dict)

    def json(self) -> dict:
        return {"outcome": self.outcome.value, "code": self.code, "sha256": self.sha256,
                "size": self.size, "revision": self.revision, "detail": self.detail}


@dataclass
class Resolver:
    """What resolving needs from the rest of JARVIS, each a callable that
    may give None when that part is switched off."""
    folders: Callable[[], Any] = lambda: None
    projects: Callable[[], Any] = lambda: None


def _pinned(r: WorkspaceRef, digest: str, size: Optional[int], revision: Optional[int],
            moved: bool) -> Resolved:
    if r.sha256 is not None and r.sha256 != digest:
        return Resolved(Outcome.CHANGED, sha256=digest, size=size, revision=revision)
    return Resolved(Outcome.MOVED if moved else Outcome.HERE, sha256=digest, size=size, revision=revision)


def resolve(r: WorkspaceRef, via: Resolver) -> Resolved:
    """The one resolver. A folder reference goes through the folder
    service's own checks; every other kind through its store's reader."""
    if not is_self(r.node):
        return Resolved(Outcome.FOREIGN_NODE)
    handler = _RESOLVERS.get(r.kind)
    if handler is None:
        return Resolved(Outcome.UNSUPPORTED)
    return handler(r, via)


def _folder(r: WorkspaceRef, via: Resolver) -> Resolved:
    from jarvis.folder_contract import FolderError
    svc = via.folders()
    if svc is None:
        return Resolved(Outcome.REFUSED, code="disabled")
    try:
        if r.kind == RefKind.FOLDER_FILE:
            found = svc.file_hash(r.id, r.path or "")
            digest, size, revision = found["sha256"], found["size"], found["location_revision"]
        else:
            found = svc.tree_hash(r.id, r.path or "")
            digest, size, revision = found["tree"], None, found["location_revision"]
    except FolderError as e:
        return Resolved(Outcome.MISSING if e.code in ("missing", "not_found") else Outcome.REFUSED, code=e.code)
    moved = r.revision is not None and revision != r.revision
    return _pinned(r, digest, size, revision, moved)


def _note(r: WorkspaceRef, via: Resolver) -> Resolved:
    """A note: `<id>` is the notes folder (memory, projects, journal,
    topics) under brain_home, `path` the file in it; read without following
    a link."""
    from jarvis import folder_readers
    from jarvis.folder_contract import FolderError
    from jarvis.history_read import NOTE_DIRS
    if r.id not in NOTE_DIRS or not r.path:
        return Resolved(Outcome.REFUSED, code="invalid_request")
    try:
        with folder_readers.opened_file(data_paths.brain_home(), Path(r.id) / r.path) as (fd, _st):
            digest, size = file_hash(fd)
    except FolderError as e:
        return Resolved(Outcome.MISSING if e.code in ("missing", "not_found") else Outcome.REFUSED, code=e.code)
    return _pinned(r, digest, size, None, False)


def _topic(r: WorkspaceRef, via: Resolver) -> Resolved:
    return _note(WorkspaceRef(r.node, RefKind.NOTE, "topics", None, f"{r.id}.md", r.sha256), via)


def _record(read: Callable[[WorkspaceRef, Resolver], Optional[dict]]):
    """A record kind: there, its canonical hash and revision now; changed
    when it was pinned to other content."""
    def resolve_record(r: WorkspaceRef, via: Resolver) -> Resolved:
        try:
            found = read(r, via)
        except Exception:  # noqa: BLE001 - a store that refuses is a refusal, not a crash
            log.debug("a %s reference could not be read", r.kind, exc_info=True)
            return Resolved(Outcome.REFUSED, code="unreadable")
        if found is None:
            return Resolved(Outcome.MISSING)
        revision = found.get("revision") if isinstance(found.get("revision"), int) else None
        return _pinned(r, record_hash(found), None, revision, False)
    return resolve_record


def _get_project(r, via):
    svc = via.projects()
    p = svc.store.get(r.id) if svc is not None else None
    return p.json() if p is not None else None


def _get_work(r, via):
    from jarvis.project_store import ProjectError
    svc = via.projects()
    if svc is None:
        return None
    try:
        return svc.store.work(r.id).json()
    except ProjectError:
        return None


def _get_accomplishment(r, via):
    svc = via.projects()
    a = svc.store.accomplishment(r.id) if svc is not None else None
    return a.json() if a is not None else None



def _get_run(r, via):
    from jarvis import run_store
    return run_store.get_run(r.id)


def _get_workflow(r, via):
    from jarvis import workflow_store
    snap = workflow_store.snapshot(r.id)
    return {"id": snap.id, "name": snap.name, "state": snap.state} if snap else None


_RESOLVERS: dict[RefKind, Callable[[WorkspaceRef, Resolver], Resolved]] = {
    RefKind.FOLDER_FILE: _folder,
    RefKind.FOLDER_TREE: _folder,
    RefKind.NOTE: _note,
    RefKind.TOPIC: _topic,
    RefKind.PROJECT: _record(_get_project),
    RefKind.WORK: _record(_get_work),
    RefKind.ACCOMPLISHMENT: _record(_get_accomplishment),
    RefKind.RUN: _record(_get_run),
    RefKind.WORKFLOW: _record(_get_workflow),
}
