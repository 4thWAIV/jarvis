"""The folder catalog's one durable record, in JARVIS's existing database
(`data_paths.db_path()`), under its own schema version (`folder_schema`),
migrated forward in order like human messaging's (`run_store.migrate`).

  folders                   one row per folder: a stable ID, its label and
                            role, active or removed, and a revision every
                            change of it or its location moves
  folder_locations          where a folder is: node, path as given, canonical
                            path, filesystem identity as evidence, and its
                            availability. One primary location per folder; a
                            reconnect retires the old row and adds one with
                            the next location revision
  folder_access_scopes      which reader may read a location, under which
                            policy version, and whether it is enabled.
                            Discovery never writes one
  folder_candidates         a path discovery found, by node and canonical
                            path: discovered, ignored or registered
  folder_observations       why: a provider session whose metadata names
                            that path as its cwd, or its history folder.
                            Kept after the session ends
  folder_location_events    every registration, reconnect, change of
                            availability, removal and reactivation, with the
                            location before and after
  folder_registrations      each registration request key, the request it
                            was for and the folder it made
  folder_discovery          each discovery source's progress
  folder_history_files      each history file discovery has read: -1 once
                            its cwd was found (never read again), else the
                            size it had (read again when that changes)

Every write is one transaction and the revision moves inside it. This module
reads no transcript and checks no permission: discovery hands it what it
observed (folder_discovery.py), the service decides what may be registered
and read (folder_service.py, folder_policy.py). It publishes nothing; the
caller hints after it returns, so a hint never runs ahead of a commit.

There is no project table and no project column. Projects come in a later
migration, referring to these folder IDs.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from jarvis import data_paths, run_store
from jarvis.folder_contract import (Availability, CandidateState, FolderError, Lifecycle,
                                    LIVE_WITHIN_SEC)

SCHEMA_VERSION = 1

# Each step takes the schema from the version before it to its own. Only ever
# appended to.
_MIGRATIONS: dict[int, str] = {
    1: """
        CREATE TABLE IF NOT EXISTS folders (
            id            TEXT PRIMARY KEY,
            owner_id      TEXT,
            display_name  TEXT NOT NULL,
            role          TEXT NOT NULL,
            lifecycle     TEXT NOT NULL,
            revision      INTEGER NOT NULL,
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL,
            removed_at    REAL
        );
        CREATE INDEX IF NOT EXISTS folders_by_lifecycle ON folders (lifecycle, display_name);

        CREATE TABLE IF NOT EXISTS folder_locations (
            id              TEXT PRIMARY KEY,
            folder_id       TEXT NOT NULL REFERENCES folders(id),
            node_id         TEXT NOT NULL,
            supplied_path   TEXT NOT NULL,
            canonical_path  TEXT NOT NULL,
            fs_identity     TEXT,
            revision        INTEGER NOT NULL,
            availability    TEXT NOT NULL,
            last_checked_at REAL,
            last_seen_at    REAL,
            error_code      TEXT,
            created_at      REAL NOT NULL,
            retired_at      REAL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS folder_locations_one_primary
            ON folder_locations (folder_id) WHERE retired_at IS NULL;
        CREATE INDEX IF NOT EXISTS folder_locations_by_path
            ON folder_locations (node_id, canonical_path) WHERE retired_at IS NULL;
        CREATE INDEX IF NOT EXISTS folder_locations_by_identity
            ON folder_locations (node_id, fs_identity) WHERE retired_at IS NULL;

        CREATE TABLE IF NOT EXISTS folder_access_scopes (
            id                TEXT PRIMARY KEY,
            location_id       TEXT NOT NULL REFERENCES folder_locations(id),
            reader_kind       TEXT NOT NULL,
            permitted_subtree TEXT NOT NULL DEFAULT '',
            policy_version    INTEGER NOT NULL,
            enabled           INTEGER NOT NULL,
            created_at        REAL NOT NULL,
            updated_at        REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS folder_access_scopes_by_location
            ON folder_access_scopes (location_id);

        CREATE TABLE IF NOT EXISTS folder_candidates (
            id                   TEXT PRIMARY KEY,
            node_id              TEXT NOT NULL,
            path_key             TEXT NOT NULL,
            proposed_role        TEXT NOT NULL,
            state                TEXT NOT NULL,
            fingerprint          TEXT NOT NULL DEFAULT '',
            ignored_fingerprint  TEXT,
            first_seen_at        REAL NOT NULL,
            last_observed_at     REAL NOT NULL,
            registered_folder_id TEXT REFERENCES folders(id),
            UNIQUE (node_id, path_key)
        );
        CREATE INDEX IF NOT EXISTS folder_candidates_by_state
            ON folder_candidates (state, last_observed_at DESC);

        CREATE TABLE IF NOT EXISTS folder_observations (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id              TEXT NOT NULL,
            provider             TEXT NOT NULL,
            profile_root         TEXT NOT NULL,
            native_session_id    TEXT NOT NULL,
            observed_cwd         TEXT NOT NULL,
            cwd_key              TEXT NOT NULL,
            history_dir          TEXT NOT NULL DEFAULT '',
            history_file         TEXT NOT NULL DEFAULT '',
            evidence             TEXT NOT NULL,
            associated           TEXT NOT NULL DEFAULT '{}',
            cwd_candidate_id     TEXT REFERENCES folder_candidates(id),
            history_candidate_id TEXT REFERENCES folder_candidates(id),
            first_observed_at    REAL NOT NULL,
            observed_at          REAL NOT NULL,
            last_live_at         REAL,
            UNIQUE (node_id, provider, profile_root, native_session_id)
        );
        CREATE INDEX IF NOT EXISTS folder_observations_by_cwd
            ON folder_observations (node_id, cwd_key);
        CREATE INDEX IF NOT EXISTS folder_observations_by_history
            ON folder_observations (node_id, history_dir);
        CREATE INDEX IF NOT EXISTS folder_observations_by_session
            ON folder_observations (provider, native_session_id);

        CREATE TABLE IF NOT EXISTS folder_location_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id   TEXT NOT NULL REFERENCES folders(id),
            location_id TEXT REFERENCES folder_locations(id),
            event       TEXT NOT NULL,
            prior       TEXT NOT NULL DEFAULT '{}',
            new         TEXT NOT NULL DEFAULT '{}',
            at          REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS folder_location_events_by_folder
            ON folder_location_events (folder_id, id DESC);

        CREATE TABLE IF NOT EXISTS folder_registrations (
            request_key TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            folder_id   TEXT NOT NULL REFERENCES folders(id),
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS folder_discovery (
            source      TEXT PRIMARY KEY,
            cursor      TEXT,
            listed      INTEGER NOT NULL DEFAULT 0,
            read        INTEGER NOT NULL DEFAULT 0,
            pending     INTEGER NOT NULL DEFAULT 0,
            complete    INTEGER NOT NULL DEFAULT 0,
            last_run_at REAL,
            last_ok_at  REAL,
            last_error  TEXT
        );

        CREATE TABLE IF NOT EXISTS folder_history_files (
            path    TEXT PRIMARY KEY,
            size    INTEGER NOT NULL,
            read_at REAL NOT NULL
        );
    """,
}


# ── records ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Location:
    id: str
    node_id: str
    supplied_path: str
    canonical_path: str
    fs_identity: Optional[str]
    revision: int
    availability: str
    last_checked_at: Optional[float]
    last_seen_at: Optional[float]
    error_code: Optional[str]


@dataclass(frozen=True)
class Scope:
    id: str
    reader_kind: str
    permitted_subtree: str
    policy_version: int
    enabled: bool


@dataclass(frozen=True)
class Folder:
    id: str
    owner_id: Optional[str]
    display_name: str
    role: str
    lifecycle: str
    revision: int
    created_at: float
    updated_at: float
    removed_at: Optional[float]
    location: Location
    scope: Scope


@dataclass(frozen=True)
class NewLocation:
    """A location the service has validated, for a registration or a
    reconnect."""
    node_id: str
    supplied_path: str
    canonical_path: str
    fs_identity: Optional[str]
    availability: str
    reader_kind: str
    permitted_subtree: str
    policy_version: int


@dataclass(frozen=True)
class Registered:
    folder: Folder
    outcome: str        # "created", "existing", "reactivated" or "replayed"


@dataclass(frozen=True)
class Candidate:
    id: str
    node_id: str
    path: str
    proposed_role: str
    state: str
    first_seen_at: float
    last_observed_at: float
    registered_folder_id: Optional[str]
    providers: list[str]
    sessions: int


@dataclass(frozen=True)
class Observation:
    """One provider session as discovery saw it (folder_discovery.Seen,
    with its paths already turned into keys)."""
    provider: str
    profile_root: str
    native_session_id: str
    observed_cwd: str
    cwd_key: str
    history_dir: str = ""
    history_file: str = ""
    evidence: str = "transcript"
    live: bool = False
    associated: dict = field(default_factory=dict)
    cwd_role: str = "data"


@dataclass(frozen=True)
class RelatedSession:
    provider: str
    profile_root: str
    native_session_id: str
    observed_cwd: str
    history_file: str
    evidence: str
    associated: dict
    first_observed_at: float
    observed_at: float
    live: bool
    agent_name: Optional[str]
    relation: str       # "cwd", "inside" or "history"


@dataclass(frozen=True)
class LocationEvent:
    id: int
    event: str
    prior: dict
    new: dict
    at: float


@dataclass(frozen=True)
class Progress:
    source: str
    cursor: Optional[str]
    listed: int
    read: int
    pending: int
    complete: bool
    last_run_at: Optional[float]
    last_ok_at: Optional[float]
    last_error: Optional[str]


def _location(row) -> Location:
    return Location(row["loc_id"], row["node_id"], row["supplied_path"], row["canonical_path"],
                    row["fs_identity"], row["loc_revision"], row["availability"],
                    row["last_checked_at"], row["last_seen_at"], row["error_code"])


def _folder(row) -> Folder:
    return Folder(row["id"], row["owner_id"], row["display_name"], row["role"], row["lifecycle"],
                  row["revision"], row["created_at"], row["updated_at"], row["removed_at"],
                  _location(row),
                  Scope(row["scope_id"], row["reader_kind"], row["permitted_subtree"],
                        row["policy_version"], bool(row["enabled"])))


_FOLDER_SELECT = """
    SELECT f.*, l.id AS loc_id, l.node_id, l.supplied_path, l.canonical_path, l.fs_identity,
           l.revision AS loc_revision, l.availability, l.last_checked_at, l.last_seen_at,
           l.error_code, s.id AS scope_id, s.reader_kind, s.permitted_subtree,
           s.policy_version, s.enabled
      FROM folders f
      JOIN folder_locations l ON l.folder_id = f.id AND l.retired_at IS NULL
      JOIN folder_access_scopes s ON s.location_id = l.id
"""


def _location_json(loc: Location) -> dict:
    """A location as the events record it: where, never anything read there."""
    return {"location_id": loc.id, "node_id": loc.node_id, "supplied_path": loc.supplied_path,
            "canonical_path": loc.canonical_path, "fs_identity": loc.fs_identity,
            "revision": loc.revision, "availability": loc.availability}


def _like(text: str) -> str:
    """`text` as a literal inside a LIKE pattern (escape character `\\`)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FolderStore:
    """The catalog over SQLite. `path` is for a store given its own file
    (tests); JARVIS's is `data_paths.db_path()`."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else data_paths.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as conn:
            run_store.migrate(conn, "folder_schema", _MIGRATIONS, "folder catalog")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction: committed when the block ends, rolled back if
        it raises."""
        with closing(run_store._connect(self.path)) as conn:
            with conn:
                yield conn

    def schema_version(self) -> int:
        with self._tx() as conn:
            return conn.execute("SELECT version FROM folder_schema").fetchone()[0]

    # ── reading folders ─────────────────────────────────────────────────

    def get(self, folder_id: str) -> Optional[Folder]:
        with self._tx() as conn:
            return self._get(conn, folder_id)

    @staticmethod
    def _get(conn, folder_id: str) -> Optional[Folder]:
        row = conn.execute(_FOLDER_SELECT + " WHERE f.id = ?", (folder_id,)).fetchone()
        return _folder(row) if row else None

    def list(self, *, lifecycle: Optional[str] = Lifecycle.ACTIVE, query: str = "",
             role: Optional[str] = None, availability: Optional[str] = None,
             after: Optional[tuple[str, str]] = None, limit: int = 100) -> list[Folder]:
        """Folders by name then ID, after `after` ((name, id) of the last one
        of the previous page). `query` matches the name or the path."""
        sql, args = _FOLDER_SELECT + " WHERE 1=1", []
        if lifecycle:
            sql += " AND f.lifecycle = ?"
            args.append(lifecycle)
        if query:
            sql += (" AND (f.display_name LIKE ? ESCAPE '\\' COLLATE NOCASE"
                    " OR l.canonical_path LIKE ? ESCAPE '\\' COLLATE NOCASE"
                    " OR l.supplied_path LIKE ? ESCAPE '\\' COLLATE NOCASE)")
            args += [f"%{_like(query)}%"] * 3
        if role:
            sql += " AND f.role = ?"
            args.append(role)
        if availability:
            sql += " AND l.availability = ?"
            args.append(availability)
        if after:
            sql += (" AND (f.display_name > ? COLLATE NOCASE"
                    " OR (f.display_name = ? COLLATE NOCASE AND f.id > ?))")
            args += [after[0], after[0], after[1]]
        sql += " ORDER BY f.display_name COLLATE NOCASE, f.id LIMIT ?"
        args.append(limit)
        with self._tx() as conn:
            return [_folder(r) for r in conn.execute(sql, args)]

    def find_location(self, node_id: str, canonical_path: str,
                      fs_identity: Optional[str]) -> Optional[Folder]:
        """The folder, active or removed, whose current location on `node_id`
        is this path or, failing that, this same directory by filesystem
        identity (a validated alias: another spelling of one directory)."""
        with self._tx() as conn:
            return self._find_location(conn, node_id, canonical_path, fs_identity)

    @classmethod
    def _find_location(cls, conn, node_id, canonical_path, fs_identity) -> Optional[Folder]:
        row = conn.execute(_FOLDER_SELECT + " WHERE l.node_id = ? AND l.canonical_path = ? "
                           "ORDER BY f.lifecycle = 'active' DESC LIMIT 1",
                           (node_id, canonical_path)).fetchone()
        if row is None and fs_identity:
            row = conn.execute(_FOLDER_SELECT + " WHERE l.node_id = ? AND l.fs_identity = ? "
                               "ORDER BY f.lifecycle = 'active' DESC LIMIT 1",
                               (node_id, fs_identity)).fetchone()
        return _folder(row) if row else None

    def located(self, node_id: str) -> list[Folder]:
        """Every active folder on `node_id`, for the availability pass."""
        with self._tx() as conn:
            return [_folder(r) for r in conn.execute(
                _FOLDER_SELECT + " WHERE f.lifecycle = 'active' AND l.node_id = ?", (node_id,))]

    def counts(self) -> dict[str, int]:
        """Active folders by availability."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT l.availability AS a, COUNT(*) AS n FROM folders f JOIN folder_locations l "
                "ON l.folder_id = f.id AND l.retired_at IS NULL WHERE f.lifecycle = 'active' "
                "GROUP BY l.availability").fetchall()
        return {r["a"]: r["n"] for r in rows}

    # ── registering ─────────────────────────────────────────────────────

    def register(self, *, request_key: str, fingerprint: str, owner_id: Optional[str],
                 display_name: str, role: str, location: NewLocation, reactivate: bool,
                 now: Optional[float] = None) -> Registered:
        """Register one validated location, in one transaction:

          - a request key seen before returns that registration's folder if
            the request was the same, and is refused if it was not;
          - a location an active folder already has (by path, or the same
            directory by identity) returns that folder: one identity;
          - a location a removed folder has is refused unless `reactivate`,
            which brings that folder and its ID back with its scope again;
          - otherwise a new folder, location and scope.
        """
        now = time.time() if now is None else now
        with self._tx() as conn:
            seen = conn.execute("SELECT * FROM folder_registrations WHERE request_key = ?",
                                (request_key,)).fetchone()
            if seen is not None:
                if seen["fingerprint"] != fingerprint:
                    raise FolderError("idempotency_conflict")
                return Registered(self._get(conn, seen["folder_id"]), "replayed")

            existing = self._find_location(conn, location.node_id, location.canonical_path,
                                           location.fs_identity)
            if existing is not None and existing.lifecycle == Lifecycle.ACTIVE:
                outcome, folder_id = "existing", existing.id
            elif existing is not None:
                if not reactivate:
                    raise FolderError("removed_exists", folder_id=existing.id)
                folder_id, outcome = existing.id, "reactivated"
                self._reactivate(conn, existing, location, now)
            else:
                folder_id, outcome = str(uuid.uuid4()), "created"
                conn.execute(
                    "INSERT INTO folders (id, owner_id, display_name, role, lifecycle, revision, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)",
                    (folder_id, owner_id, display_name, role, now, now))
                loc = self._insert_location(conn, folder_id, location, 1, now)
                self._event(conn, folder_id, loc.id, "registered", {}, _location_json(loc), now)
            conn.execute("INSERT INTO folder_registrations (request_key, fingerprint, folder_id, "
                         "created_at) VALUES (?, ?, ?, ?)",
                         (request_key, fingerprint, folder_id, now))
            self._link_candidate(conn, location.node_id, location.canonical_path, folder_id)
            return Registered(self._get(conn, folder_id), outcome)

    def _reactivate(self, conn, folder: Folder, location: NewLocation, now: float) -> None:
        """A removed folder brought back at a location validated again, with
        the policy's scope for it now."""
        conn.execute("UPDATE folders SET lifecycle = 'active', removed_at = NULL, "
                     "revision = revision + 1, updated_at = ? WHERE id = ?", (now, folder.id))
        conn.execute("UPDATE folder_locations SET availability = ?, last_checked_at = ?, "
                     "last_seen_at = ?, error_code = NULL WHERE id = ?",
                     (location.availability, now, now, folder.location.id))
        conn.execute("UPDATE folder_access_scopes SET reader_kind = ?, permitted_subtree = ?, "
                     "policy_version = ?, enabled = 1, updated_at = ? WHERE id = ?",
                     (location.reader_kind, location.permitted_subtree, location.policy_version,
                      now, folder.scope.id))
        self._event(conn, folder.id, folder.location.id, "reactivated",
                    {"lifecycle": Lifecycle.REMOVED.value},
                    {"lifecycle": Lifecycle.ACTIVE.value, "reader_kind": location.reader_kind},
                    now)

    def _insert_location(self, conn, folder_id: str, location: NewLocation, revision: int,
                         now: float) -> Location:
        loc_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO folder_locations (id, folder_id, node_id, supplied_path, canonical_path, "
            "fs_identity, revision, availability, last_checked_at, last_seen_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (loc_id, folder_id, location.node_id, location.supplied_path, location.canonical_path,
             location.fs_identity, revision, location.availability, now, now, now))
        conn.execute(
            "INSERT INTO folder_access_scopes (id, location_id, reader_kind, permitted_subtree, "
            "policy_version, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (str(uuid.uuid4()), loc_id, location.reader_kind, location.permitted_subtree,
             location.policy_version, now, now))
        return Location(loc_id, location.node_id, location.supplied_path, location.canonical_path,
                        location.fs_identity, revision, location.availability, now, now, None)

    @staticmethod
    def _event(conn, folder_id: str, location_id: Optional[str], event: str, prior: dict,
               new: dict, now: float) -> None:
        conn.execute("INSERT INTO folder_location_events (folder_id, location_id, event, prior, "
                     "new, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (folder_id, location_id, event, json.dumps(prior), json.dumps(new), now))

    @staticmethod
    def _link_candidate(conn, node_id: str, path_key: str, folder_id: str) -> None:
        conn.execute("UPDATE folder_candidates SET state = ?, registered_folder_id = ? "
                     "WHERE node_id = ? AND path_key = ?",
                     (CandidateState.REGISTERED.value, folder_id, node_id, path_key))

    # ── changing one ────────────────────────────────────────────────────

    def _current(self, conn, folder_id: str, expected_revision: Optional[int]) -> Folder:
        folder = self._get(conn, folder_id)
        if folder is None:
            raise FolderError("not_found")
        if expected_revision is not None and expected_revision != folder.revision:
            raise FolderError("stale_revision", revision=folder.revision)
        return folder

    def edit(self, folder_id: str, expected_revision: int, *, display_name: Optional[str] = None,
             role: Optional[str] = None, now: Optional[float] = None) -> Folder:
        """Its label and role, if it is still at `expected_revision`."""
        now = time.time() if now is None else now
        with self._tx() as conn:
            folder = self._current(conn, folder_id, expected_revision)
            if folder.lifecycle != Lifecycle.ACTIVE:
                raise FolderError("removed")
            conn.execute("UPDATE folders SET display_name = ?, role = ?, revision = revision + 1, "
                         "updated_at = ? WHERE id = ?",
                         (display_name if display_name is not None else folder.display_name,
                          role if role is not None else folder.role, now, folder_id))
            return self._get(conn, folder_id)

    def unregister(self, folder_id: str, expected_revision: Optional[int],
                   now: Optional[float] = None) -> Folder:
        """Removed from the catalog: kept, its scope disabled, its sessions'
        provenance kept. No file is touched."""
        now = time.time() if now is None else now
        with self._tx() as conn:
            folder = self._current(conn, folder_id, expected_revision)
            if folder.lifecycle == Lifecycle.REMOVED:
                return folder
            conn.execute("UPDATE folders SET lifecycle = 'removed', removed_at = ?, "
                         "revision = revision + 1, updated_at = ? WHERE id = ?",
                         (now, now, folder_id))
            conn.execute("UPDATE folder_access_scopes SET enabled = 0, updated_at = ? WHERE id = ?",
                         (now, folder.scope.id))
            conn.execute("UPDATE folder_candidates SET state = ? WHERE registered_folder_id = ? "
                         "AND state = ?", (CandidateState.DISCOVERED.value, folder_id,
                                           CandidateState.REGISTERED.value))
            self._event(conn, folder_id, folder.location.id, "unregistered",
                        {"lifecycle": Lifecycle.ACTIVE.value},
                        {"lifecycle": Lifecycle.REMOVED.value}, now)
            return self._get(conn, folder_id)

    def reconnect(self, folder_id: str, expected_revision: int, location: NewLocation,
                  now: Optional[float] = None) -> Folder:
        """The folder, same ID, at a new location the service validated and
        the caller confirmed: the old location retired, a new one with the
        next location revision (so a page of the old one is refused), and
        the policy's scope for the new one."""
        now = time.time() if now is None else now
        with self._tx() as conn:
            folder = self._current(conn, folder_id, expected_revision)
            if folder.lifecycle != Lifecycle.ACTIVE:
                raise FolderError("removed")
            other = self._find_location(conn, location.node_id, location.canonical_path,
                                        location.fs_identity)
            if other is not None and other.id != folder_id:
                raise FolderError("path_registered", folder_id=other.id)
            conn.execute("UPDATE folder_locations SET retired_at = ? WHERE id = ?",
                         (now, folder.location.id))
            conn.execute("UPDATE folder_access_scopes SET enabled = 0, updated_at = ? WHERE id = ?",
                         (now, folder.scope.id))
            loc = self._insert_location(conn, folder_id, location, folder.location.revision + 1, now)
            conn.execute("UPDATE folders SET revision = revision + 1, updated_at = ? WHERE id = ?",
                         (now, folder_id))
            self._event(conn, folder_id, loc.id, "reconnected", _location_json(folder.location),
                        _location_json(loc), now)
            self._link_candidate(conn, location.node_id, location.canonical_path, folder_id)
            return self._get(conn, folder_id)

    def record_availability(self, location_id: str, availability: str,
                            error_code: Optional[str], now: Optional[float] = None) -> bool:
        """A check of one location: always its time, its time seen when it
        was there, and an event when its availability moved. True if it
        moved. Not a metadata change: the folder's revision stays."""
        now = time.time() if now is None else now
        with self._tx() as conn:
            row = conn.execute("SELECT folder_id, availability FROM folder_locations WHERE id = ?",
                               (location_id,)).fetchone()
            if row is None:
                return False
            seen = availability == Availability.AVAILABLE
            conn.execute("UPDATE folder_locations SET availability = ?, error_code = ?, "
                         "last_checked_at = ?, last_seen_at = CASE WHEN ? THEN ? "
                         "ELSE last_seen_at END WHERE id = ?",
                         (availability, error_code, now, seen, now, location_id))
            moved = row["availability"] != availability
            if moved:
                self._event(conn, row["folder_id"], location_id, "availability",
                            {"availability": row["availability"]},
                            {"availability": availability, "error_code": error_code}, now)
            return moved

    def events(self, folder_id: str, limit: int = 20) -> list[LocationEvent]:
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM folder_location_events WHERE folder_id = ? "
                                "ORDER BY id DESC LIMIT ?", (folder_id, limit)).fetchall()
        return [LocationEvent(r["id"], r["event"], json.loads(r["prior"]), json.loads(r["new"]),
                              r["at"]) for r in rows]

    # ── discovery ───────────────────────────────────────────────────────

    def observe(self, node_id: str, seen: list[Observation], now: Optional[float] = None) -> int:
        """Record what discovery saw, in one transaction: each session's
        observation (made or brought up to date, never dropped), and a
        candidate for each path it names, its cwd and its history folder.
        An ignored candidate whose evidence changed is discovered again.
        Returns how many candidates are new or changed state."""
        now = time.time() if now is None else now
        changed = 0
        with self._tx() as conn:
            touched: set[str] = set()
            for o in seen:
                cwd_cand, new = self._candidate(conn, node_id, o.cwd_key, o.cwd_role, now)
                changed += new
                hist_cand = None
                if o.history_dir:
                    hist_cand, new = self._candidate(conn, node_id, o.history_dir, "history", now)
                    changed += new
                conn.execute(
                    "INSERT INTO folder_observations (node_id, provider, profile_root, "
                    "native_session_id, observed_cwd, cwd_key, history_dir, history_file, "
                    "evidence, associated, cwd_candidate_id, history_candidate_id, "
                    "first_observed_at, observed_at, last_live_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (node_id, provider, profile_root, native_session_id) DO UPDATE SET "
                    "observed_cwd = excluded.observed_cwd, cwd_key = excluded.cwd_key, "
                    "history_dir = CASE WHEN excluded.history_dir != '' THEN excluded.history_dir "
                    "  ELSE history_dir END, "
                    "history_file = CASE WHEN excluded.history_file != '' THEN excluded.history_file "
                    "  ELSE history_file END, "
                    # A record's evidence is kept over a sighting's: that
                    # it is live says nothing new about where it ran.
                    "evidence = CASE WHEN excluded.evidence IN ('live', 'roster') "
                    "  AND evidence NOT IN ('live', 'roster') THEN evidence "
                    "  ELSE excluded.evidence END, "
                    "associated = CASE WHEN excluded.associated != '{}' THEN excluded.associated "
                    "  ELSE associated END, "
                    "cwd_candidate_id = excluded.cwd_candidate_id, "
                    "history_candidate_id = COALESCE(excluded.history_candidate_id, "
                    "  history_candidate_id), "
                    "observed_at = excluded.observed_at, "
                    "last_live_at = COALESCE(excluded.last_live_at, last_live_at)",
                    (node_id, o.provider, o.profile_root, o.native_session_id, o.observed_cwd,
                     o.cwd_key, o.history_dir, o.history_file, o.evidence,
                     json.dumps(o.associated, sort_keys=True), cwd_cand, hist_cand, now, now,
                     now if o.live else None))
                touched.update(c for c in (cwd_cand, hist_cand) if c)
            for cand_id in touched:
                changed += self._refresh_fingerprint(conn, cand_id)
        return changed

    @staticmethod
    def _candidate(conn, node_id: str, path_key: str, role: str, now: float) -> tuple[str, int]:
        """(candidate ID, 1 if it is new) for a path, made if needed; a path
        an active folder already has is registered from the start."""
        row = conn.execute("SELECT id FROM folder_candidates WHERE node_id = ? AND path_key = ?",
                           (node_id, path_key)).fetchone()
        if row is not None:
            conn.execute("UPDATE folder_candidates SET last_observed_at = ? WHERE id = ?",
                         (now, row["id"]))
            return row["id"], 0
        folder = conn.execute(
            "SELECT f.id FROM folders f JOIN folder_locations l ON l.folder_id = f.id "
            "AND l.retired_at IS NULL WHERE f.lifecycle = 'active' AND l.node_id = ? "
            "AND l.canonical_path = ?", (node_id, path_key)).fetchone()
        cand_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO folder_candidates (id, node_id, path_key, proposed_role, state, "
            "first_seen_at, last_observed_at, registered_folder_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cand_id, node_id, path_key, role,
             (CandidateState.REGISTERED if folder else CandidateState.DISCOVERED).value,
             now, now, folder["id"] if folder else None))
        return cand_id, 1

    @staticmethod
    def _refresh_fingerprint(conn, cand_id: str) -> int:
        """A candidate's evidence in one string: which providers and kinds of
        evidence name it. An ignored one whose evidence is now different
        comes back as discovered. 1 if it did."""
        rows = conn.execute(
            "SELECT DISTINCT provider, evidence, cwd_candidate_id = ? AS as_cwd "
            "FROM folder_observations WHERE cwd_candidate_id = ? OR history_candidate_id = ?",
            (cand_id, cand_id, cand_id)).fetchall()
        fingerprint = "|".join(sorted(f"{r['provider']}:{'cwd' if r['as_cwd'] else 'history'}"
                                      for r in rows))
        row = conn.execute("SELECT state, ignored_fingerprint FROM folder_candidates WHERE id = ?",
                           (cand_id,)).fetchone()
        conn.execute("UPDATE folder_candidates SET fingerprint = ? WHERE id = ?",
                     (fingerprint, cand_id))
        if row["state"] == CandidateState.IGNORED and row["ignored_fingerprint"] != fingerprint:
            conn.execute("UPDATE folder_candidates SET state = ?, ignored_fingerprint = NULL "
                         "WHERE id = ?", (CandidateState.DISCOVERED.value, cand_id))
            return 1
        return 0

    _CANDIDATE_SELECT = """
        SELECT c.*,
               (SELECT GROUP_CONCAT(DISTINCT o.provider) FROM folder_observations o
                 WHERE o.cwd_candidate_id = c.id OR o.history_candidate_id = c.id) AS providers,
               (SELECT COUNT(*) FROM folder_observations o
                 WHERE o.cwd_candidate_id = c.id OR o.history_candidate_id = c.id) AS sessions
          FROM folder_candidates c
    """

    @staticmethod
    def _cand(row) -> Candidate:
        return Candidate(row["id"], row["node_id"], row["path_key"], row["proposed_role"],
                         row["state"], row["first_seen_at"], row["last_observed_at"],
                         row["registered_folder_id"],
                         sorted((row["providers"] or "").split(",")) if row["providers"] else [],
                         row["sessions"])

    def candidates(self, *, state: Optional[str] = CandidateState.DISCOVERED, query: str = "",
                   role: Optional[str] = None, after: Optional[tuple[float, str]] = None,
                   limit: int = 100) -> list[Candidate]:
        """Candidates, most recently observed first, after `after`
        ((last observed, ID) of the previous page's last)."""
        sql, args = self._CANDIDATE_SELECT + " WHERE 1=1", []
        if state:
            sql += " AND c.state = ?"
            args.append(state)
        if query:
            sql += " AND c.path_key LIKE ? ESCAPE '\\' COLLATE NOCASE"
            args.append(f"%{_like(query)}%")
        if role:
            sql += " AND c.proposed_role = ?"
            args.append(role)
        if after:
            sql += " AND (c.last_observed_at < ? OR (c.last_observed_at = ? AND c.id > ?))"
            args += [after[0], after[0], after[1]]
        sql += " ORDER BY c.last_observed_at DESC, c.id LIMIT ?"
        args.append(limit)
        with self._tx() as conn:
            return [self._cand(r) for r in conn.execute(sql, args)]

    def candidate(self, candidate_id: str) -> Optional[Candidate]:
        with self._tx() as conn:
            row = conn.execute(self._CANDIDATE_SELECT + " WHERE c.id = ?",
                               (candidate_id,)).fetchone()
        return self._cand(row) if row else None

    def set_candidate_state(self, candidate_id: str, ignored: bool) -> Candidate:
        """Ignore a discovered candidate (it stays ignored until its evidence
        changes), or take it back to discovered. A registered one is
        refused: unregister the folder instead."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM folder_candidates WHERE id = ?",
                               (candidate_id,)).fetchone()
            if row is None:
                raise FolderError("candidate_not_found")
            if row["state"] == CandidateState.REGISTERED:
                raise FolderError("unsupported")
            if ignored:
                conn.execute("UPDATE folder_candidates SET state = ?, ignored_fingerprint = ? "
                             "WHERE id = ?", (CandidateState.IGNORED.value, row["fingerprint"],
                                              candidate_id))
            else:
                conn.execute("UPDATE folder_candidates SET state = ?, ignored_fingerprint = NULL "
                             "WHERE id = ?", (CandidateState.DISCOVERED.value, candidate_id))
        return self.candidate(candidate_id)

    def related_sessions(self, node_id: str, canonical_path: str, now: Optional[float] = None,
                         limit: int = 50) -> list[RelatedSession]:
        """The sessions whose metadata named this path: as their cwd, a cwd
        inside it, or their history folder. Most recently observed first;
        each with the agent name JARVIS lists it by, where it has one."""
        now = time.time() if now is None else now
        inside = _like(canonical_path.rstrip("/")) + "/%"
        with self._tx() as conn:
            # The agents list's names (run_store), where that table is here.
            named = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                                 "AND name = 'agents'").fetchone() is not None
            agent = ("(SELECT a.name FROM agents a WHERE a.session_id = o.native_session_id)"
                     if named else "NULL")
            rows = conn.execute(
                f"SELECT o.*, {agent} AS agent_name, "
                "CASE WHEN o.cwd_key = ? THEN 'cwd' WHEN o.history_dir = ? THEN 'history' "
                "ELSE 'inside' END AS relation "
                "FROM folder_observations o "
                "WHERE o.node_id = ? AND (o.cwd_key = ? OR o.history_dir = ? "
                "OR o.cwd_key LIKE ? ESCAPE '\\') ORDER BY o.observed_at DESC LIMIT ?",
                (canonical_path, canonical_path, node_id, canonical_path, canonical_path,
                 inside, limit)).fetchall()
        return [RelatedSession(r["provider"], r["profile_root"], r["native_session_id"],
                               r["observed_cwd"], r["history_file"], r["evidence"],
                               json.loads(r["associated"] or "{}"), r["first_observed_at"],
                               r["observed_at"],
                               r["last_live_at"] is not None and now - r["last_live_at"]
                               < LIVE_WITHIN_SEC,
                               r["agent_name"], r["relation"]) for r in rows]

    def observed_cwds(self, node_id: str, history_dir: str) -> list[str]:
        """The cwds the sessions kept in one history folder recorded."""
        with self._tx() as conn:
            return [r["observed_cwd"] for r in conn.execute(
                "SELECT DISTINCT observed_cwd FROM folder_observations WHERE node_id = ? "
                "AND history_dir = ? ORDER BY observed_cwd", (node_id, history_dir))]

    # ── discovery's own progress ────────────────────────────────────────

    def history_files_read(self, paths: list[str]) -> dict[str, int]:
        """Of `paths`, those discovery has read, with the size each had."""
        out: dict[str, int] = {}
        with self._tx() as conn:
            for start in range(0, len(paths), 500):
                chunk = paths[start:start + 500]
                for r in conn.execute("SELECT path, size FROM folder_history_files WHERE path IN "
                                      f"({','.join('?' * len(chunk))})", chunk):
                    out[r["path"]] = r["size"]
        return out

    def mark_history_files(self, read: list[tuple[str, int]], now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._tx() as conn:
            conn.executemany("INSERT INTO folder_history_files (path, size, read_at) VALUES (?, ?, ?) "
                             "ON CONFLICT (path) DO UPDATE SET size = excluded.size, "
                             "read_at = excluded.read_at", [(p, s, now) for p, s in read])

    def record_progress(self, source: str, *, listed: int, read: int, pending: int,
                        complete: bool, error: Optional[str], cursor: Optional[str] = None,
                        now: Optional[float] = None) -> None:
        """One source's pass: how much it listed and read, how much is left,
        and where it got to (`cursor`, kept when a pass gives none)."""
        now = time.time() if now is None else now
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO folder_discovery (source, cursor, listed, read, pending, complete, "
                "last_run_at, last_ok_at, last_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (source) DO UPDATE SET "
                "cursor = COALESCE(excluded.cursor, cursor), listed = excluded.listed, "
                "read = excluded.read, pending = excluded.pending, complete = excluded.complete, "
                "last_run_at = excluded.last_run_at, "
                "last_ok_at = COALESCE(excluded.last_ok_at, last_ok_at), "
                "last_error = excluded.last_error",
                (source, cursor, listed, read, pending, int(complete), now,
                 None if error else now, error))

    def progress(self) -> list[Progress]:
        with self._tx() as conn:
            return [Progress(r["source"], r["cursor"], r["listed"], r["read"], r["pending"], bool(r["complete"]),
                             r["last_run_at"], r["last_ok_at"], r["last_error"])
                    for r in conn.execute("SELECT * FROM folder_discovery ORDER BY source")]
