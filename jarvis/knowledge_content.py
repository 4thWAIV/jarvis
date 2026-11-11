"""Content: the files in his folders, and in JARVIS's own, indexed where
they are (UDS round 2; docs/planning/knowledge-engine/plan-uds.md, "Content
stays on disk; the engine indexes it").

The engine never holds a file's bytes. For every file under a watched
folder it holds one item at the file's path: `/folders/<folder id>/<rel>`
for a registered folder, `/jarvis/<area>/<rel>` for JARVIS's own (`ROOTS`).
The item's data is its path entry (the file as it is: `file`, `size`,
`mtime`, `inode`, `sha256`) and what the reader found (title, format,
media type, pages, headings, notes, `state`); its content is the extracted
text and its sections, with the file's reference (`jref`). Vectors follow
in the engine.

Which files: a registered folder read under the FILES reader, on this Mac
and available, under its projects' index scope (`scope_of`: the widest of
its project links; `off` on every link keeps it out; a folder in no
project is `docs`), every entry through the folder catalog's rule
(`repo_read.sensitive_reason`), git-ignored paths and `SKIP_DIRS` passed
over, and JARVIS's own data folder never read from inside a registered
one. A file whose name has no suffix is not indexed: its path would name
a folder.

How changes are found, cheapest first, none ever lost:
- the watcher (`fs_watch.Watcher`, one FSEvents stream over every root)
  starts from the last event ID handled, kept in the engine's watermarks
  (`WATERMARK`) only once its batch is recorded, so FSEvents replays what
  changed while JARVIS was away; an event that says changes were dropped
  or coalesced scans that subtree;
- the backup scan (`fs_watch.scan`, stat only) compares every root with
  what the engine holds: at start, after the Mac wakes, every six hours
  (`fs_watch.ScanClock`), and over a root when it is added.

Two lanes: finding a change and writing its path entry (`state:
pending`) is cheap and never waits; reading, extracting and sectioning
(`Indexer.extract`) is the background lane, and stops while the pace
holds (`hold` and `free` on stdin from `knowledge_runtime`). A file whose
bytes did not change (same SHA-256) is not read again. What is pending is
on record, so a restart picks it up (`Indexer.pending`).

It runs as one long-lived child, `python -m jarvis.knowledge_content`,
started and paced by `knowledge_runtime.ContentScheduler`, at background
priority, never in the server process: reading and extracting is
CPU-bound Python that would hold the GIL against the voice path. It writes
its state for Diagnostics to `data_paths.data_dir()/knowledge-content.json`.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from jarvis import content_sections, data_paths, folder_readers, fs_watch, workspace_ref
from jarvis.knowledge import paths as item_paths
from jarvis.knowledge.uds import Caller, EngineUDS, Stream, Transaction

log = logging.getLogger("jarvis.knowledge_content")

WRITER = Caller("tim")                  # JARVIS indexes his files for him, with his rights
WATERMARK = "content"
ROOTS_EVERY_SEC = 60.0
STATE_EVERY_SEC = 5.0
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache",
                       ".pytest_cache", ".ruff_cache", ".next", ".cache"})
SKIP_FRESH_SEC = 60.0
HEADINGS_KEPT = 50
NOTES_KEPT = 10
WRITE_BATCH = 200


def state_path() -> Path:
    return data_paths.data_dir() / "knowledge-content.json"


@dataclass(frozen=True)
class Root:
    prefix: str                  # the item path the root's files go under
    path: str                    # the folder on disk, canonical
    scope: str                   # docs or text
    trust: str                   # own (his) or untrusted (an agent's or another person's)
    folder_id: Optional[str] = None

    @property
    def area(self) -> str:
        return self.folder_id or self.prefix.rsplit("/", 1)[-1]


def scope_of(scopes: Iterable[str]) -> str:
    """A folder's index scope from its project links: the widest; off only
    when every link says off; docs when it is in no project."""
    found = set(scopes)
    if not found:
        return "docs"
    if "text" in found:
        return "text"
    if "docs" in found:
        return "docs"
    return "off"


def jarvis_roots() -> list[Root]:
    """JARVIS's own folders: the answers to agents, the packages, the
    files people sent once downloaded, and the runs' output streams. Needs boards live in the project
    folders and come with them. The brain's notes stay with the legacy
    ingest until the linker, topics and context pack read items (round 3),
    so they are never counted twice."""
    found = [Root("/jarvis/answers", str(data_paths.answers_dir()), "docs", "own"),
             Root("/jarvis/packages", str(data_paths.packages_dir()), "docs", "untrusted"),
             Root("/jarvis/received", str(data_paths.messaging_downloads_dir()), "docs", "untrusted"),
             # Each run's output stream (run_store.py): JSON lines, the agent's words.
             Root("/jarvis/runs", str(data_paths.runs_dir()), "text", "untrusted")]
    return [Root(r.prefix, os.path.realpath(r.path), r.scope, r.trust) for r in found if os.path.isdir(r.path)]


def folder_roots(service=None, scopes: Optional[dict[str, list[str]]] = None) -> list[Root]:
    """Every registered folder JARVIS may read whole (the FILES reader), on
    this Mac and available, with its index scope."""
    if service is None:
        from jarvis import folder_service, folder_store
        service = folder_service.FolderService(folder_store.FolderStore(), data_paths.node_id)
    if scopes is None:
        from jarvis import project_store
        scopes = project_store.ProjectStore().index_scopes()
    out = []
    cursor = None
    while True:
        listing = service.list_folders(cursor=cursor)
        for f in listing["folders"]:
            access = f.get("access") or {}
            loc = f.get("location") or {}
            if access.get("reader") != "files" or not loc.get("local") or loc.get("availability") != "available":
                continue
            scope = scope_of(scopes.get(f["id"], ()))
            if scope == "off":
                continue
            out.append(Root(f"/folders/{f['id']}", os.path.realpath(loc["canonical_path"]), scope, "own", f["id"]))
        cursor = listing.get("next_cursor")
        if not cursor:
            return out


def all_roots() -> list[Root]:
    return jarvis_roots() + folder_roots()


def item_path(root: Root, rel: str) -> Optional[str]:
    """The item path of a file, or None when no item path can name it (a
    name with no suffix, too long a name, a character a path cannot hold)."""
    path = root.prefix + "/" + unicodedata.normalize("NFC", rel)
    if item_paths.problem(path) is not None or item_paths.type_of(path) == item_paths.FOLDER:
        return None
    return path


def skipper(root: Root) -> Callable[[str, bool], bool]:
    """What the walk passes over in one root: the folder catalog's rule,
    git-ignored paths, `SKIP_DIRS`, JARVIS's own data inside a registered
    folder, files out of scope or with no item path."""
    from jarvis import repo_read
    ignored = fs_watch.git_ignored(root.path)
    data = os.path.realpath(data_paths.data_dir())
    inside = os.path.relpath(data, root.path) if root.folder_id and \
        (data + "/").startswith(root.path.rstrip("/") + "/") else None

    def skip(rel: str, is_dir: bool) -> bool:
        name = rel.rsplit("/", 1)[-1]
        if is_dir and name in SKIP_DIRS:
            return True
        if inside is not None and (rel == inside or rel.startswith(inside + "/")):
            return True
        if repo_read.sensitive_reason(Path(rel), named_dotfiles=root.folder_id is not None) is not None:
            return True
        if ignored(rel, is_dir):
            return True
        if not is_dir and (not content_sections.in_scope(name, root.scope) or item_path(root, rel) is None):
            return True
        return False
    return skip


def wanted(root: Root, rel: str, is_dir: bool = False,
           skip: Optional[Callable[[str, bool], bool]] = None) -> bool:
    """Whether a path inside the root is indexed: none of its folders and
    not itself passed over."""
    skip = skip or skipper(root)
    parts = rel.split("/")
    return not any(skip("/".join(parts[:i]), True) for i in range(1, len(parts))) and not skip(rel, is_dir)


def read_file(root: Root, rel: str) -> tuple[bytes, os.stat_result]:
    """The file's bytes and its stat, through the folder catalog's reader
    (component by component, no link followed); at most FILE_MAX_BYTES."""
    with folder_readers.opened_file(Path(root.path), Path(rel)) as (fd, info):
        if info.st_size > content_sections.FILE_MAX_BYTES:
            return b"", info
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > content_sections.FILE_MAX_BYTES:
                break
        return b"".join(chunks), info


def _stat_fields(stat: fs_watch.Stat) -> dict:
    return {"size": stat.size, "mtime": stat.mtime_ns / 1e9, "mtime_ns": stat.mtime_ns, "inode": stat.inode}


def _known_stat(data: dict) -> Optional[fs_watch.Stat]:
    try:
        return fs_watch.Stat(int(data["size"]), int(data["mtime_ns"]), int(data["inode"]))
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class Counters:
    noted: int = 0
    indexed: int = 0
    unchanged: int = 0
    unreadable: int = 0
    removed: int = 0
    scans: int = 0
    last_scan_at: Optional[float] = None
    last_scan_reason: Optional[str] = None
    last_scan_sec: Optional[float] = None
    index_sec: float = 0.0
    errors: list = field(default_factory=list)

    def error(self, words: str) -> None:
        self.errors = (self.errors + [words])[-5:]


class Indexer:
    """The work, without threads: what is on disk against what the engine
    holds, one root at a time."""

    def __init__(self, uds: EngineUDS, counters: Optional[Counters] = None) -> None:
        self.uds = uds
        self.counters = counters or Counters()
        self._skips: dict[str, tuple[float, Callable[[str, bool], bool]]] = {}

    def skip_of(self, root: Root, fresh: bool = False) -> Callable[[str, bool], bool]:
        """The root's skip rule, made again (git asked again) at most every
        `SKIP_FRESH_SEC`, or at once for a scan."""
        held = self._skips.get(root.prefix)
        if fresh or held is None or time.monotonic() - held[0] > SKIP_FRESH_SEC:
            held = (time.monotonic(), skipper(root))
            self._skips[root.prefix] = held
        return held[1]

    # -- what the engine holds --------------------------------------------------
    def known(self, root: Root, under: str = "") -> dict[str, dict]:
        """Every item under the root (or a subtree of it), by its file's
        relative path: its data."""
        prefix = root.prefix + ("/" + unicodedata.normalize("NFC", under) if under else "")
        out = {}
        for item in self.uds.scan(WRITER, prefix, streams={Stream.DATA}, limit=1000):
            rel = (item.data or {}).get("file")
            if isinstance(rel, str):
                out[rel] = {**item.data, "_path": item.path, "_version": item.version}
        return out

    def pending(self, root: Root) -> list[str]:
        return [i.data["file"] for i in self.uds.scan(WRITER, root.prefix, where=[["state", "=", "pending"]],
                                                       streams={Stream.DATA}, limit=1000)
                if isinstance((i.data or {}).get("file"), str)]

    # -- the cheap lane: path entries -------------------------------------------
    def note(self, root: Root, rel: str, stat: fs_watch.Stat, held: Optional[dict] = None,
             tx: Optional[Transaction] = None) -> bool:
        """The file as it is now, recorded at once as its path entry (`state:
        pending` until it is read again). False when nothing changed."""
        path = item_path(root, rel)
        if path is None:
            return False
        if held is not None and _known_stat(held) == stat and held.get("state") != "pending":
            return False
        own = tx is None
        tx = tx if tx is not None else Transaction()
        if held is None:
            tx.put(path, {"file": rel, "source": root.area, "trust": root.trust, "state": "pending",
                          **_stat_fields(stat)}, content=None)
        else:
            tx.merge(path, {**_stat_fields(stat), "state": "pending"})
        if own:
            self.uds.transaction(WRITER, tx, change="file changed", summary=rel[:200])
        self.counters.noted += 1
        return True

    def remove(self, root: Root, rels: Iterable[str]) -> int:
        paths = [p for p in (item_path(root, r) for r in rels) if p]
        for i in range(0, len(paths), WRITE_BATCH):
            tx = Transaction()
            for p in paths[i:i + WRITE_BATCH]:
                tx.delete(p)
            self.uds.transaction(WRITER, tx, change="file gone")
        self.counters.removed += len(paths)
        return len(paths)

    def scan(self, root: Root, under: str = "", budget_sec: Optional[float] = None,
             reason: str = "scan") -> list[str]:
        """The backup scan of a root (or a subtree): stat only. New and
        changed files get their path entries now; files gone (or now passed
        over) leave the engine. The files to read are returned."""
        started = time.monotonic()
        held = self.known(root, under)
        if under and not wanted(root, under, is_dir=True, skip=self.skip_of(root, fresh=True)):
            self.remove(root, list(held))          # a subtree passed over holds nothing
            return []
        known = {rel: s for rel, s in ((rel, _known_stat(d)) for rel, d in held.items()) if s is not None}
        found = fs_watch.scan(root.path, known, under=under, skip=self.skip_of(root, fresh=True),
                              budget_sec=budget_sec)
        todo: list[str] = []
        changed = list(found.added) + list(found.changed)
        for i in range(0, len(changed), WRITE_BATCH):
            tx = Transaction()
            for rel in changed[i:i + WRITE_BATCH]:
                stat = fs_watch.stat_of(os.path.join(root.path, rel))
                if stat is not None and self.note(root, rel, stat, held.get(rel), tx):
                    todo.append(rel)
            if tx.writes:
                self.uds.transaction(WRITER, tx, change="files changed", summary=f"{root.prefix} {reason}")
        if found.complete:
            gone = list(found.removed) + [rel for rel in held if rel not in known]
            self.remove(root, gone)
        todo += [rel for rel, d in held.items() if d.get("state") == "pending" and rel not in todo
                 and rel not in found.removed]
        c = self.counters
        c.scans += 1
        c.last_scan_at, c.last_scan_reason = time.time(), reason
        c.last_scan_sec = round(time.monotonic() - started, 3)
        return todo

    def changed(self, root: Root, rel: str) -> Optional[str]:
        """One path an event named: its path entry now. The relative path
        to read, or None (gone, passed over, unchanged)."""
        full = os.path.join(root.path, rel)
        path = item_path(root, rel)
        stat = fs_watch.stat_of(full)
        if stat is None or path is None or not wanted(root, rel, skip=self.skip_of(root)):
            if path is not None and self.uds.get(WRITER, path) is not None:
                self.remove(root, [rel])
            return None
        item = self.uds.get(WRITER, path, {Stream.DATA})
        held = None if item is None else {**(item.data or {}), "_version": item.version}
        return rel if self.note(root, rel, stat, held) or (held or {}).get("state") == "pending" else None

    # -- the background lane: reading ------------------------------------------------
    def extract(self, root: Root, rel: str) -> str:
        """Read one file and write its data and content in one write: the
        text and sections its reader gives, or why it has none. A file whose
        bytes have not changed is only marked indexed again."""
        path = item_path(root, rel)
        if path is None:
            return "skipped"
        started = time.monotonic()
        item = self.uds.get(WRITER, path, {Stream.DATA})
        try:
            data, info = read_file(root, rel)
        except (folder_readers.FolderError, OSError):
            if item is not None:
                self.remove(root, [rel])
            return "gone"
        import hashlib
        stat = fs_watch.Stat(info.st_size, info.st_mtime_ns, info.st_ino)
        sha = hashlib.sha256(data).hexdigest()
        held = (item.data or {}) if item is not None else {}
        base = {"file": rel, "source": root.area, "trust": root.trust, **_stat_fields(stat), "sha256": sha}
        expect = item.version if item is not None else 0
        tx = Transaction()
        if held.get("sha256") == sha and ("format" in held or "reason" in held):
            tx.merge(path, {**_stat_fields(stat), "state": "unreadable" if held.get("reason") else "indexed"},
                     expect_version=expect)
            outcome = "unchanged"
        elif info.st_size > content_sections.FILE_MAX_BYTES:
            tx.put(path, {**base, "state": "unreadable", "reason": "too_large"}, content=None,
                   expect_version=expect)
            outcome = "unreadable"
        else:
            found = content_sections.extract(data, rel.rsplit("/", 1)[-1], root.scope)
            if isinstance(found, content_sections.Unreadable):
                tx.put(path, {**base, "state": "unreadable", "reason": found.reason}, content=None,
                       expect_version=expect)
                outcome = "unreadable"
            else:
                fields = {**base, "state": "indexed", "title": found.title, "format": found.format,
                          "media_type": found.media_type, "headings": list(found.headings[:HEADINGS_KEPT]),
                          "notes": list(found.notes[:NOTES_KEPT])}
                if found.pages is not None:
                    fields["pages"] = found.pages
                content = {"text": found.text, "title": found.title, "format": found.format,
                           "media_type": found.media_type, "ref": self.ref(root, rel),
                           "sections": [content_sections.section_json(s) for s in found.sections]}
                tx.put(path, fields, content=content, expect_version=expect)
                outcome = "indexed"
        try:
            self.uds.transaction(WRITER, tx, change="file read", summary=rel[:200])
        except Exception as e:  # noqa: BLE001 - a newer change came meanwhile: it is read again
            from jarvis.knowledge.uds import Conflict
            if isinstance(e, Conflict):
                return "raced"
            raise
        c = self.counters
        setattr(c, outcome, getattr(c, outcome) + 1)
        c.index_sec += time.monotonic() - started
        return outcome

    @staticmethod
    def ref(root: Root, rel: str) -> str:
        if root.folder_id:
            try:
                return workspace_ref.format(workspace_ref.ref("folder_file", root.folder_id, rel))
            except workspace_ref.RefError:
                pass
        return "file://" + os.path.join(root.path, rel)

    def forget_root(self, prefix: str) -> int:
        """Every item under a root that is no longer watched (a folder
        unregistered, or its scope turned off)."""
        paths = [i.path for i in self.uds.scan(WRITER, prefix, streams={Stream.ENTRY}, limit=1000)]
        for i in range(0, len(paths), WRITE_BATCH):
            tx = Transaction()
            for p in paths[i:i + WRITE_BATCH]:
                tx.delete(p)
            self.uds.transaction(WRITER, tx, change="folder left", summary=prefix)
        self.counters.removed += len(paths)
        return len(paths)


class ContentService:
    """The watcher, the backup scans and the reading lane over every root."""

    def __init__(self, uds: EngineUDS, *, roots: Callable[[], list[Root]] = all_roots,
                 marks: Optional["Marks"] = None, latency: float = 0.3,
                 clock: Optional[fs_watch.ScanClock] = None, watch: bool = True) -> None:
        self.uds = uds
        self.counters = Counters()
        self.indexer = Indexer(uds, self.counters)
        self.roots_of = roots
        self.marks = marks or Marks(uds)
        self.latency = latency
        self.clock = clock or fs_watch.ScanClock()
        self.watch = watch and fs_watch.available()
        self.roots: list[Root] = []
        self.watcher: Optional[fs_watch.Watcher] = None
        self.events: queue.Queue = queue.Queue()
        self.todo: dict[tuple[str, str], Root] = {}
        self._todo_cond = threading.Condition()
        self.paused = False
        self.stopping = threading.Event()
        self.handled_event_id: Optional[int] = None
        self._roots_at = 0.0
        self._threads: list[threading.Thread] = []
        self.watched_since: Optional[float] = None

    # -- the two lanes ------------------------------------------------------------
    def set_paused(self, paused: bool) -> None:
        with self._todo_cond:
            self.paused = paused
            self._todo_cond.notify_all()

    def _want(self, root: Root, rels: Iterable[str]) -> None:
        with self._todo_cond:
            for rel in rels:
                self.todo[(root.prefix, rel)] = root
            self._todo_cond.notify_all()

    def start(self) -> None:
        self._threads = [threading.Thread(target=self._main, name="content-main", daemon=True),
                         threading.Thread(target=self._reader, name="content-read", daemon=True)]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self.stopping.set()
        with self._todo_cond:
            self._todo_cond.notify_all()
        for t in self._threads:
            t.join(timeout=10)
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None

    def _reader(self) -> None:
        from jarvis.knowledge import lanes
        lanes.background()
        while not self.stopping.is_set():
            with self._todo_cond:
                while not self.stopping.is_set() and (self.paused or not self.todo):
                    self._todo_cond.wait(1.0)
                if self.stopping.is_set():
                    return
                key = next(iter(self.todo))
                root = self.todo.pop(key)
            try:
                self.indexer.extract(root, key[1])
            except Exception as e:  # noqa: BLE001 - one file never stops the rest
                log.warning("reading %s/%s failed: %s", root.prefix, key[1], e)
                self.counters.error(f"{root.prefix}/{key[1]}: {type(e).__name__}")

    def _main(self) -> None:
        try:
            self.refresh_roots(start=True)
        except Exception as e:  # noqa: BLE001
            log.warning("content roots: %s", e)
            self.counters.error(f"roots: {e}")
        while not self.stopping.is_set():
            try:
                self.step()
            except Exception as e:  # noqa: BLE001 - logged, tried again next time
                log.warning("content: %s", e)
                self.counters.error(f"{type(e).__name__}: {e}")
                self.stopping.wait(1.0)

    def step(self, wait: float = 0.5) -> None:
        """One turn of the main lane: the events that came, a backup scan
        when one is due, the roots again now and then."""
        try:
            batch = [self.events.get(timeout=wait)]
        except queue.Empty:
            batch = []
        while True:
            try:
                batch.append(self.events.get_nowait())
            except queue.Empty:
                break
        if batch:
            self.handle(batch)
        reason = self.clock.due()
        if reason is not None:
            self.backup_scan(reason)
        if time.monotonic() - self._roots_at >= ROOTS_EVERY_SEC:
            self.refresh_roots()

    # -- roots ------------------------------------------------------------------------
    def refresh_roots(self, start: bool = False) -> None:
        self._roots_at = time.monotonic()
        new = self.roots_of()
        old = {r.prefix: r for r in self.roots}
        now = {r.prefix: r for r in new}
        added = [r for p, r in now.items() if p not in old or old[p] != r]
        for prefix in old:
            if prefix not in now and not self._only_unavailable(prefix):
                self.indexer.forget_root(prefix)
        self.roots = new
        if start:
            mark = self.marks.read()
            since = mark.get("event_id") if mark.get("roots") == sorted(now) else None
            if self.watch:
                self.watcher = fs_watch.Watcher([r.path for r in new], self._on_events, since=since,
                                                latency=self.latency)
                self.watcher.start()
                self.watched_since = time.time()
            for r in new:
                self._want(r, self.indexer.pending(r))
            self._save_mark()
            return
        if added or len(now) != len(old):
            if self.watcher is not None:
                self.watcher.restart([r.path for r in new])
            for r in added:
                self._want(r, self.indexer.scan(r, reason="added"))
            self._save_mark()

    def _only_unavailable(self, prefix: str) -> bool:
        """A registered folder that is only unavailable now (a drive away)
        keeps its items; one unregistered or turned off loses them."""
        if not prefix.startswith("/folders/"):
            return False
        folder_id = prefix.rsplit("/", 1)[-1]
        try:
            from jarvis import folder_service, folder_store
            service = folder_service.FolderService(folder_store.FolderStore(), data_paths.node_id)
            f = service.detail(folder_id)
        except Exception:  # noqa: BLE001 - gone from the catalog
            return False
        folder = f.get("folder") or f
        return folder.get("lifecycle") == "active" and \
            (folder.get("location") or {}).get("availability") != "available"

    # -- events -------------------------------------------------------------------------
    def _on_events(self, events: list) -> None:
        for e in events:
            self.events.put(e)

    def handle(self, events: list) -> None:
        """A batch of FSEvents: each path named gets its path entry now; a
        subtree whose changes were dropped is scanned; then the batch's
        last event ID is kept, so a restart replays from there."""
        roots = {r.path: r for r in self.roots}
        for root_path, under in fs_watch.scan_requests(events, list(roots)):
            root = roots.get(root_path)
            if root is not None:
                self._want(root, self.indexer.scan(root, under=under, reason="dropped events"))
        seen: set[tuple[str, str]] = set()
        for e in events:
            if e.must_scan or e.root_changed or e.history_done:
                continue
            found = fs_watch.locate(e.path, list(roots))
            if found is None:
                continue
            root = roots[found[0]]
            rel = found[1]
            if not rel or (root.prefix, rel) in seen:
                continue
            seen.add((root.prefix, rel))
            if e.is_dir:
                if e.renamed or e.removed or e.created:
                    self._want(root, self.indexer.scan(root, under=rel, reason="folder moved"))
                continue
            todo = self.indexer.changed(root, rel)
            if todo is not None:
                self._want(root, [todo])
        ids = [e.event_id for e in events if e.event_id]
        if ids:
            self.handled_event_id = max(ids + [self.handled_event_id or 0])
            self._save_mark()

    def backup_scan(self, reason: str) -> None:
        for root in list(self.roots):
            if self.stopping.is_set():
                return
            try:
                self._want(root, self.indexer.scan(root, reason=reason))
            except Exception as e:  # noqa: BLE001
                log.warning("backup scan of %s: %s", root.prefix, e)
                self.counters.error(f"scan {root.prefix}: {type(e).__name__}")

    def _save_mark(self) -> None:
        event_id = self.handled_event_id
        if event_id is None and self.watcher is not None:
            event_id = self.watcher.last_event_id
        if event_id is not None:
            self.marks.write({"event_id": int(event_id), "roots": sorted(r.prefix for r in self.roots)})

    def idle(self) -> bool:
        return self.events.empty() and not self.todo

    def health(self) -> dict:
        c = self.counters
        return {"roots": len(self.roots), "watching": self.watcher is not None, "paused": self.paused,
                "waiting": len(self.todo), "noted": c.noted, "indexed": c.indexed, "unchanged": c.unchanged,
                "unreadable": c.unreadable, "removed": c.removed, "scans": c.scans,
                "last_scan_at": c.last_scan_at, "last_scan_reason": c.last_scan_reason,
                "last_scan_sec": c.last_scan_sec,
                "index_ms_per_file": round(1000 * c.index_sec / max(c.indexed + c.unreadable + c.unchanged, 1), 1),
                "event_id": self.handled_event_id, "errors": list(c.errors), "at": time.time()}


class Marks:
    """The watcher's position, kept in the engine's watermarks."""

    def __init__(self, uds: EngineUDS) -> None:
        self.uds = uds

    def read(self) -> dict:
        try:
            status, found = self.uds.transport.request("GET", "/v1/watermarks", None, self.uds.deadline)
        except Exception:  # noqa: BLE001
            return {}
        return ((found or {}).get("watermarks") or {}).get(WATERMARK) or {} if status == 200 else {}

    def write(self, mark: dict) -> None:
        self.uds.transport.request("POST", "/v1/txn", {"watermarks": {WATERMARK: mark}}, self.uds.deadline)


def _write_state(service: ContentService) -> None:
    target = state_path()
    partial = target.with_name(target.name + ".partial")
    try:
        partial.write_text(json.dumps(service.health()))
        os.replace(partial, target)
    except OSError:
        pass


def main() -> int:
    """The child: {pid, token} (the engine's) on the first line of stdin, then `hold`,
    `free` and `scan` lines from the server; ends when stdin closes."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        os.setpriority(getattr(os, "PRIO_DARWIN_PROCESS", os.PRIO_PROCESS), 0,
                       getattr(os, "PRIO_DARWIN_BG", 19))
    except (OSError, AttributeError):
        pass
    given = json.loads(sys.stdin.readline())
    from jarvis.knowledge_client import HTTPTransport
    pid, token = int(given["pid"]), str(given["token"])
    uds = EngineUDS(HTTPTransport(lambda: pid, lambda: token), deadline=30.0)
    service = ContentService(uds)
    service.start()

    def report() -> None:
        while not service.stopping.wait(STATE_EVERY_SEC):
            _write_state(service)
    threading.Thread(target=report, name="content-state", daemon=True).start()
    for line in sys.stdin:
        word = line.strip()
        if word in ("hold", "free"):
            service.set_paused(word == "hold")
        elif word == "scan":
            service.clock = fs_watch.ScanClock()
    service.stop()
    _write_state(service)
    return 0


if __name__ == "__main__":
    sys.exit(main())
