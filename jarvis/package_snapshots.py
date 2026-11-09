"""Verified immutable package bytes and their durable inventory.

Only registered package identities reach this service. Paths are checked
internally and never supplied as a filesystem escape hatch by API callers.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import threading
import time
import uuid
from pathlib import Path

from jarvis import bundle_archive, bundle_readers, package_inbox, run_store, work_packages
from jarvis.bundle_cache import CacheBudgetError, DerivedCache, OwnerScope, ScopedCacheKey
from jarvis.bundle_contract import member_id
from jarvis.inspection_core import content_revision as revision
from jarvis.package_content_contract import (InspectionError, Member, PackageRef, SnapshotRef)

SCHEMA = """
CREATE TABLE IF NOT EXISTS package_content_snapshots (
 snapshot_id TEXT PRIMARY KEY, owner TEXT NOT NULL, package_id TEXT NOT NULL,
 inbox_id TEXT, delivery_sha256 TEXT, content_revision TEXT NOT NULL,
 results_revision TEXT NOT NULL, schema_version INTEGER NOT NULL,
 inventory_complete INTEGER NOT NULL, created_at REAL NOT NULL,
 manifest TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS package_content_snapshots_identity
 ON package_content_snapshots(owner, package_id, content_revision, results_revision);
CREATE TABLE IF NOT EXISTS package_content_members (
 snapshot_id TEXT NOT NULL, member_id TEXT NOT NULL, relative_path TEXT NOT NULL,
 part TEXT NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL,
 metadata TEXT NOT NULL, PRIMARY KEY(snapshot_id, member_id)
);
CREATE TABLE IF NOT EXISTS package_content_chunks (
 snapshot_id TEXT NOT NULL, member_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
 location TEXT NOT NULL, text TEXT NOT NULL, part TEXT NOT NULL,
 PRIMARY KEY(snapshot_id, member_id, ordinal)
);
CREATE TABLE IF NOT EXISTS package_content_index_progress (snapshot_id TEXT NOT NULL, member_id TEXT NOT NULL, cursor TEXT, ordinal INTEGER NOT NULL, PRIMARY KEY(snapshot_id, member_id));
CREATE VIRTUAL TABLE IF NOT EXISTS package_content_search USING fts5(
 snapshot_id UNINDEXED, member_id UNINDEXED, ordinal UNINDEXED, text,
 tokenize='trigram'
);
"""


def owner_key(owner: OwnerScope) -> str:
    from dataclasses import asdict
    return json.dumps({"kind": type(owner).__name__, **asdict(owner)}, sort_keys=True)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()



def describe_member(path, raw, source_revision, results_revision):
    found = bundle_readers.detect(path, raw[:bundle_readers.SNIFF_BYTES])
    reader = bundle_readers.reader_for(found.format)
    health = reader.health() if reader else None
    reason = ("nested_archive" if found.format in bundle_readers.ARCHIVES else
              health.reason if health and not health.available else
              "unknown_format" if found.format == "unknown" else
              "reader_unavailable" if not reader else None)
    readable = health is not None and health.available and reason is None
    return Member(member_id=member_id(results_revision if path.startswith("results/") else source_revision, path),
        relative_path=path, size=len(raw), sha256=digest(raw), detected_format=found.format,
        support="readable" if readable else "unsupported",
        operations=tuple(sorted(health.operations)) if readable else (),
        state="pending" if readable else "unreadable", reason=reason)


class SnapshotSource:
    def __init__(self, store, owner, snapshot, member):
        self.store, self.owner, self.snapshot, self.member = store, owner, snapshot, member
        self.path, self.size = member.relative_path, member.size
        self.member_id = member.member_id
        self.attachment_sha256 = member.sha256

    def _data(self):
        data = self.store.cache.get(self.store.cache_key(self.owner, self.member))
        if data is None or len(data) != self.size or digest(data) != self.member.sha256:
            self.store.reacquire(self.owner, self.snapshot, self.member)
            data = self.store.cache.get(self.store.cache_key(self.owner, self.member))
        if data is None or digest(data) != self.member.sha256:
            raise InspectionError("snapshot_unavailable", "The cited snapshot bytes are unavailable.")
        return data

    def head(self):
        return self._data()[:bundle_readers.SNIFF_BYTES]

    def read(self, limit=None):
        if limit is not None and self.size > limit:
            raise bundle_archive.ArchiveError("too-large", "The member exceeds the read limit.")
        return self._data()

    @contextlib.contextmanager
    def materialize(self):
        scratch = self.store.cache.scratch_scope(self.owner)
        path = scratch / f"member-{uuid.uuid4().hex}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(self._data())
            path.chmod(0o400)
            yield path
        finally:
            path.unlink(missing_ok=True)


class PackageSnapshots:
    def __init__(self, cache: DerivedCache, connect=None):
        self.cache = cache
        self.connect = connect or run_store._connect
        self._lock = threading.RLock()
        self._initialized: set[str] = set()

    @contextlib.contextmanager
    def db(self):
        with self._lock, contextlib.closing(self.connect()) as conn:
            database = str(conn.execute("PRAGMA database_list").fetchone()[2])
            if database not in self._initialized:
                run_store.migrate(conn, "package_content_schema", {1: SCHEMA}, "package content")
                conn.commit()
                self._initialized.add(database)
            with conn:
                yield conn

    @staticmethod
    def resolve(ref: PackageRef):
        if ref.inbox_id:
            entry = package_inbox.get(ref.inbox_id)
            if entry is None or entry.package_id != ref.package_id:
                raise InspectionError("package_not_found", "That exact package arrival is unavailable.")
        else:
            entry = None
        try:
            package = work_packages.find(ref.package_id)
        except work_packages.PackageError as e:
            raise InspectionError("package_not_found", str(e)) from None
        if package.id != ref.package_id:
            raise InspectionError("ambiguous_package", "Use the exact registered package ID.")
        return package, entry

    def _scan(self, package):
        """Directory metadata is a consistency hint; digests establish identity."""
        rows = []
        root = package.folder
        if root.is_symlink():
            raise InspectionError("unsafe_member", "The package directory is a symlink.")
        for folder, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(folder) / name
                st = path.lstat()
                relative = path.relative_to(root).as_posix()
                try:
                    checked = bundle_archive.normalised(relative)
                except bundle_archive.ArchiveError:
                    raise InspectionError("unsafe_member", "A package member has an unsafe path.") from None
                if stat.S_ISLNK(st.st_mode) or not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                    raise InspectionError("unsafe_member", "A package member is a link or special file.")
                if stat.S_ISREG(st.st_mode):
                    rows.append((checked, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino))
        budgets = bundle_archive.DEFAULT_BUDGETS
        if len(rows) > budgets.max_entries or any(r[1] > budgets.max_file_bytes for r in rows) \
                or sum(r[1] for r in rows) > min(budgets.max_total_bytes, self.cache.max_bytes):
            raise InspectionError("budget", "The package snapshot exceeds the available byte budget.")
        return sorted(rows)

    def _read_checked(self, package, rows):
        out = {}
        rootfd = os.open(package.folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name, size, *_ in rows:
                parts = name.split("/")
                fd = os.dup(rootfd)
                try:
                    for part in parts[:-1]:
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        os.close(fd)
                        fd = child
                    filefd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                    with os.fdopen(filefd, "rb") as source:
                        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                            raise InspectionError("unsafe_member", "A member changed into a special file.")
                        data = source.read(size + 1)
                    if len(data) != size:
                        raise InspectionError("source_changed", "The package changed during snapshot construction.", retryable=True)
                    out[name] = data
                finally:
                    os.close(fd)
        finally:
            os.close(rootfd)
        return out

    def capture(self, owner: OwnerScope, ref: PackageRef, expected_revision=None) -> SnapshotRef:
        package, entry = self.resolve(ref)
        with self._lock:
            for attempt in range(2):
                try:
                    before = self._scan(package)
                    data = self._read_checked(package, before)
                    after = self._scan(package)
                    # Read twice so timestamps alone never certify a snapshot.
                    second = self._read_checked(package, after)
                    if before != after or data != second:
                        raise InspectionError("source_changed", "The package changed during snapshot construction.", retryable=True)
                    break
                except (OSError, InspectionError) as error:
                    if isinstance(error, InspectionError) and error.failure.code != "source_changed":
                        raise
                    if attempt:
                        raise InspectionError("source_changed", "The package changed during both snapshot attempts.", retryable=True) from None
            manifest = work_packages.Manifest.parse(data[work_packages.MANIFEST])
            source = [(p, len(b), digest(b)) for p, b in data.items() if not p.startswith("results/")]
            results = [(p, len(b), digest(b)) for p, b in data.items() if p.startswith("results/")]
            content_rev, results_rev = revision(source), revision(results)
            if expected_revision and content_rev != expected_revision:
                raise InspectionError("revision_mismatch", "The package source revision changed.")
            sid = "snap-" + digest((owner_key(owner) + ref.model_dump_json() + content_rev + results_rev).encode())[:32]
            snap = SnapshotRef(snapshot_id=sid, package=ref,
                delivery_sha256=(entry.sha256 if entry else work_packages.sha256_of(package)) or None,
                content_revision=content_rev, results_revision=results_rev)
            members = [describe_member(path, raw, content_rev, results_rev)
                       for path, raw in sorted(data.items())]
            keys = [self.cache_key(owner, m) for m in members]
            lease = f"snapshot-build-{sid}"
            self.cache.lease(lease, keys)
            try:
                for member, key in zip(members, keys):
                    self.cache.put(key, data[member.relative_path])
                with self.db() as conn:
                    conn.execute("INSERT OR IGNORE INTO package_content_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, owner_key(owner), package.id, ref.inbox_id, snap.delivery_sha256,
                         content_rev, results_rev, 1, 1, time.time(), manifest.as_json()))
                    for member in members:
                        conn.execute("INSERT OR IGNORE INTO package_content_members VALUES (?,?,?,?,?,?,?)",
                            (sid, member.member_id, member.relative_path,
                             "results" if member.relative_path.startswith("results/") else "source",
                             member.sha256, member.size, member.model_dump_json()))
            except CacheBudgetError as e:
                raise InspectionError("budget", str(e)) from None
            finally:
                self.cache.release(lease)
            return snap

    def refresh_results(self, owner, snapshot):
        """Combine pinned source identity with a separately checked results revision."""
        package, _ = self.resolve(snapshot.package)
        with self._lock:
            for attempt in range(2):
                rows = [r for r in self._scan(package) if r[0].startswith("results/")]
                data = self._read_checked(package, rows)
                after = [r for r in self._scan(package) if r[0].startswith("results/")]
                if rows == after and data == self._read_checked(package, after):
                    break
                if attempt:
                    raise InspectionError("source_changed", "Results changed during both snapshot attempts.", retryable=True)
            result_rev = revision([(p, len(b), digest(b)) for p, b in data.items()])
            if result_rev == snapshot.results_revision:
                return snapshot
            sid = "snap-" + digest((owner_key(owner) + snapshot.package.model_dump_json() + snapshot.content_revision + result_rev).encode())[:32]
            updated = snapshot.model_copy(update={"snapshot_id": sid, "results_revision": result_rev})
            members = [describe_member(path, raw, snapshot.content_revision, result_rev) for path, raw in sorted(data.items())]
            lease = "results-build-" + sid
            self.cache.lease(lease, [self.cache_key(owner, m) for m in members])
            try:
                for member in members:
                    self.cache.put(self.cache_key(owner, member), data[member.relative_path])
                with self.db() as conn:
                    conn.execute("INSERT OR IGNORE INTO package_content_snapshots SELECT ?,owner,package_id,inbox_id,delivery_sha256,content_revision,?,schema_version,inventory_complete,?,manifest FROM package_content_snapshots WHERE snapshot_id=?",
                        (sid, result_rev, time.time(), snapshot.snapshot_id))
                    conn.execute("INSERT OR IGNORE INTO package_content_members SELECT ?,member_id,relative_path,part,digest,size,metadata FROM package_content_members WHERE snapshot_id=? AND part='source'", (sid, snapshot.snapshot_id))
                    for member in members:
                        conn.execute("INSERT OR IGNORE INTO package_content_members VALUES (?,?,?,?,?,?,?)", (sid, member.member_id, member.relative_path, "results", member.sha256, member.size, member.model_dump_json()))
            except CacheBudgetError as error:
                raise InspectionError("budget", str(error)) from None
            finally:
                self.cache.release(lease)
            return updated

    @staticmethod
    def cache_key(owner, member):
        return ScopedCacheKey(owner, member.sha256, member.member_id, "snapshot", 1, "source")

    def get(self, owner, snapshot_id):
        with self.db() as conn:
            row = conn.execute("SELECT * FROM package_content_snapshots WHERE snapshot_id=? AND owner=?",
                               (snapshot_id, owner_key(owner))).fetchone()
        if row is None:
            raise InspectionError("snapshot_not_found", "That snapshot does not belong to this owner.")
        return SnapshotRef(snapshot_id=row["snapshot_id"], package=PackageRef(
            package_id=row["package_id"], inbox_id=row["inbox_id"]), delivery_sha256=row["delivery_sha256"],
            content_revision=row["content_revision"], results_revision=row["results_revision"])

    def members(self, owner, snapshot):
        actual = self.get(owner, snapshot.snapshot_id)
        if actual != snapshot:
            raise InspectionError("revision_mismatch", "The supplied snapshot identity does not match its record.")
        with self.db() as conn:
            return [Member.model_validate_json(row[0]) for row in conn.execute(
                "SELECT metadata FROM package_content_members WHERE snapshot_id=? ORDER BY relative_path",
                (snapshot.snapshot_id,))]

    def source(self, owner, snapshot, mid):
        members = self.members(owner, snapshot)
        member = next((m for m in members if m.member_id == mid), None)
        if member is None:
            raise InspectionError("member_not_found", "That member does not belong to this snapshot.")
        return SnapshotSource(self, owner, snapshot, member)

    def reacquire(self, owner, snapshot, member):
        current = self.capture(owner, snapshot.package)
        matching = next((m for m in self.members(owner, current)
                         if m.relative_path == member.relative_path and m.sha256 == member.sha256 and m.size == member.size), None)
        if matching is None:
            raise InspectionError("snapshot_unavailable", "The old member bytes were evicted and its stored source changed.")
        raw = self.cache.get(self.cache_key(owner, matching))
        if raw is None or digest(raw) != member.sha256:
            raise InspectionError("snapshot_unavailable", "The exact old member bytes are unavailable.")
        self.cache.put(self.cache_key(owner, member), raw)

    def lease(self, owner, snapshot, mount_id, seconds=300):
        members = self.members(owner, snapshot)
        keys = [self.cache_key(owner, m) for m in members]
        self.cache.lease(mount_id, keys, seconds)
        try:
            for member in members:
                self.source(owner, snapshot, member.member_id)._data()
        except BaseException:
            self.cache.release(mount_id)
            raise
