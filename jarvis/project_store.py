"""Projects, pieces of work and accomplishments: records of truth in JARVIS's
existing database (`data_paths.db_path()`), under their own schema version
(`project_schema`), migrated forward with `run_store.migrate` (design
section 5).

  projects                  an area of work with accomplishments: a stable
                            ID, its owner, its name and spoken key, the
                            notes file it joins, a summary, a lifecycle
                            (active, paused, done, archived) and a revision
  project_aliases           the names it was known by before it had an ID
                            (a run's project string, a memory slug), one
                            project per (owner, source, key)
  project_folders           which registered folders it holds, each with a
                            part (source, docs, data, output, reference), at
                            most one home, and what the indexer may read
  project_candidates        projects JARVIS inferred and has not been told to
                            keep, with their evidence and a fingerprint, so an
                            ignored one stays ignored until it changes
  works, work_projects,     a piece of work spanning one or several projects
  work_folders              (exactly one primary) and the independent folders
                            it uses directly
  accomplishments,          what was achieved, proposed then confirmed or
  accomplishment_evidence   withdrawn; only confirmed ones count anywhere
  project_bindings          something decided: an agent, workflow or package
                            placed in a project or work, and how
  project_events            append-only history of every committed change:
                            the page's history and a change feed by `seq`

They are operational records: runs, agents and the page join to them, and
they work when the knowledge engine is down. They are not knowledge edges.

This module is the only writer. Every change is one transaction, moves the
record's revision inside it and writes its `project_events` row in it, so a
hint (sent by the caller after it returns) never runs ahead of a commit.
Nothing inferred becomes a record without his yes (I9): candidates are
proposals until accepted.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

from jarvis import data_paths, run_store, workspace_ref
from jarvis.agent_directory import spoken_key

SCHEMA_VERSION = 1
LIFECYCLES = ("active", "paused", "done", "archived")
PARTS = ("source", "docs", "data", "output", "reference")
INDEX_SCOPES = ("docs", "text", "off")
ALIAS_SOURCES = ("spoken", "session_project", "run_project", "workflow_project",
                 "message_project", "memory_slug", "projects_root")
ACTORS = ("page", "voice", "agent", "migration", "package")
WORK_STATES = ("open", "done", "dropped")
ACCOMPLISHMENT_STATES = ("proposed", "confirmed", "withdrawn")
PROPOSED_BY = ("page", "voice", "run_finish", "workflow_finish", "merge", "package_result", "import")
NAME_MAX = 80
SUMMARY_MAX = 1200
TITLE_MAX = 160

_MIGRATIONS: dict[int, str] = {
    1: """
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    spoken_key    TEXT NOT NULL,
    notes_slug    TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    lifecycle     TEXT NOT NULL CHECK (lifecycle IN ('active', 'paused', 'done', 'archived')),
    revision      INTEGER NOT NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    archived_at   REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS projects_by_spoken_key
    ON projects (owner_id, spoken_key) WHERE lifecycle <> 'archived';
CREATE TABLE IF NOT EXISTS project_aliases (
    owner_id    TEXT NOT NULL,
    source      TEXT NOT NULL CHECK (source IN (
                    'spoken', 'session_project', 'run_project', 'workflow_project',
                    'message_project', 'memory_slug', 'projects_root')),
    alias_key   TEXT NOT NULL,
    project_id  TEXT NOT NULL REFERENCES projects(id),
    added_at    REAL NOT NULL,
    PRIMARY KEY (owner_id, source, alias_key)
);
CREATE INDEX IF NOT EXISTS project_aliases_by_project ON project_aliases (project_id);
CREATE TABLE IF NOT EXISTS project_folders (
    project_id  TEXT NOT NULL REFERENCES projects(id),
    folder_id   TEXT NOT NULL,
    part        TEXT NOT NULL CHECK (part IN ('source', 'docs', 'data', 'output', 'reference')),
    is_home     INTEGER NOT NULL DEFAULT 0,
    index_scope TEXT NOT NULL DEFAULT 'docs' CHECK (index_scope IN ('docs', 'text', 'off')),
    revision    INTEGER NOT NULL,
    added_at    REAL NOT NULL,
    removed_at  REAL,
    PRIMARY KEY (project_id, folder_id, added_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS project_folders_live
    ON project_folders (project_id, folder_id) WHERE removed_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS project_folders_one_home
    ON project_folders (project_id) WHERE is_home = 1 AND removed_at IS NULL;
CREATE INDEX IF NOT EXISTS project_folders_by_folder
    ON project_folders (folder_id) WHERE removed_at IS NULL;
CREATE TABLE IF NOT EXISTS project_candidates (
    id             TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL,
    proposed_name  TEXT NOT NULL,
    evidence       TEXT NOT NULL,
    fingerprint    TEXT NOT NULL,
    state          TEXT NOT NULL CHECK (state IN ('proposed', 'ignored', 'accepted')),
    project_id     TEXT REFERENCES projects(id),
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS project_candidates_by_key ON project_candidates (owner_id, proposed_name);
CREATE TABLE IF NOT EXISTS works (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    title       TEXT NOT NULL,
    outcome     TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL CHECK (state IN ('open', 'done', 'dropped')),
    origin      TEXT NOT NULL CHECK (origin IN ('local', 'received')),
    revision    INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    closed_at   REAL
);
CREATE TABLE IF NOT EXISTS work_projects (
    work_id     TEXT NOT NULL REFERENCES works(id),
    project_id  TEXT NOT NULL REFERENCES projects(id),
    role        TEXT NOT NULL CHECK (role IN ('primary', 'involved')),
    added_at    REAL NOT NULL,
    PRIMARY KEY (work_id, project_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS work_projects_one_primary
    ON work_projects (work_id) WHERE role = 'primary';
CREATE TABLE IF NOT EXISTS work_folders (
    work_id    TEXT NOT NULL REFERENCES works(id),
    folder_id  TEXT NOT NULL,
    added_at   REAL NOT NULL,
    PRIMARY KEY (work_id, folder_id)
);
CREATE TABLE IF NOT EXISTS accomplishments (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id),
    work_id       TEXT REFERENCES works(id),
    title         TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL CHECK (state IN ('proposed', 'confirmed', 'withdrawn')),
    proposed_by   TEXT NOT NULL CHECK (proposed_by IN (
                      'page', 'voice', 'run_finish', 'workflow_finish', 'merge', 'package_result', 'import')),
    achieved_at   REAL NOT NULL,
    confirmed_at  REAL,
    revision      INTEGER NOT NULL,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS accomplishments_by_project ON accomplishments (project_id, state, achieved_at);
CREATE TABLE IF NOT EXISTS accomplishment_evidence (
    accomplishment_id  TEXT NOT NULL REFERENCES accomplishments(id),
    ref                TEXT NOT NULL,
    PRIMARY KEY (accomplishment_id, ref)
);
CREATE TABLE IF NOT EXISTS project_bindings (
    subject     TEXT NOT NULL,
    project_id  TEXT REFERENCES projects(id),
    work_id     TEXT REFERENCES works(id),
    folder_id   TEXT,
    how         TEXT NOT NULL CHECK (how IN ('started_here', 'owner', 'adopted')),
    bound_at    REAL NOT NULL,
    unbound_at  REAL,
    CHECK (project_id IS NOT NULL OR work_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS project_bindings_live
    ON project_bindings (subject, IFNULL(project_id, ''), IFNULL(work_id, '')) WHERE unbound_at IS NULL;
CREATE TABLE IF NOT EXISTS project_events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL NOT NULL,
    subject   TEXT NOT NULL,
    action    TEXT NOT NULL,
    revision  INTEGER NOT NULL,
    actor     TEXT NOT NULL CHECK (actor IN ('page', 'voice', 'agent', 'migration', 'package')),
    detail    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS project_events_by_subject ON project_events (subject, seq);
""",
}

# HTTP status of each refusal's code, for the routes.
STATUS = {"not_found": 404, "name_taken": 409, "stale_revision": 409, "invalid_request": 400,
          "already_answered": 409, "not_allowed": 400}


class ProjectError(ValueError):
    """A change the records refuse, by code, with words to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def status(self) -> int:
        return STATUS.get(self.code, 400)

    def to_json(self) -> dict:
        return {"error": {"code": self.code, "message": str(self)}}


def subject(kind: str, record_id: str) -> str:
    """The reference a record is named by in events and bindings
    (`workspace_ref.format`, design 4.2)."""
    return workspace_ref.format(workspace_ref.ref(kind, record_id))


@dataclass
class Project:
    id: str
    owner_id: str
    node_id: str
    display_name: str
    spoken_key: str
    notes_slug: str
    summary: str
    lifecycle: str
    revision: int
    created_at: float
    updated_at: float
    archived_at: Optional[float]

    def json(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ProjectFolder:
    project_id: str
    folder_id: str
    part: str
    is_home: bool
    index_scope: str
    revision: int
    added_at: float

    def json(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Candidate:
    id: str
    owner_id: str
    proposed_name: str
    evidence: dict
    fingerprint: str
    state: str
    project_id: Optional[str]
    created_at: float
    updated_at: float

    def json(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Work:
    id: str
    owner_id: str
    title: str
    outcome: str
    state: str
    origin: str
    revision: int
    created_at: float
    updated_at: float
    closed_at: Optional[float]
    projects: list[dict] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)

    def json(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Accomplishment:
    id: str
    project_id: str
    work_id: Optional[str]
    title: str
    detail: str
    state: str
    proposed_by: str
    achieved_at: float
    confirmed_at: Optional[float]
    revision: int
    created_at: float
    evidence: list[str] = field(default_factory=list)

    def json(self) -> dict:
        return dict(self.__dict__)


def fingerprint(evidence: dict) -> str:
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _name(text: str, what: str = "a project's name", limit: int = NAME_MAX) -> str:
    text = " ".join(str(text or "").split())
    if not text or len(text) > limit:
        raise ProjectError("invalid_request", f"{what} is 1 to {limit} characters")
    if not spoken_key(text):
        raise ProjectError("invalid_request", f"{what} needs a letter or a number")
    return text


class ProjectStore:
    """The records over SQLite. `path` is for a store given its own file
    (tests); JARVIS's is `data_paths.db_path()`."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else data_paths.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as conn:
            run_store.migrate(conn, "project_schema", _MIGRATIONS, "project records")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(run_store._connect(self.path)) as conn:
            with conn:
                yield conn

    def schema_version(self) -> int:
        with self._tx() as conn:
            return conn.execute("SELECT version FROM project_schema").fetchone()[0]

    # -- events ------------------------------------------------------------
    @staticmethod
    def _event(conn, kind: str, record_id: str, action: str, revision: int, actor: str,
               detail: Optional[dict] = None) -> None:
        if actor not in ACTORS:
            raise ProjectError("invalid_request", f"actor is one of {', '.join(ACTORS)}")
        conn.execute("INSERT INTO project_events (at, subject, action, revision, actor, detail) "
                     "VALUES (?,?,?,?,?,?)",
                     (time.time(), subject(kind, record_id), action, revision, actor,
                      json.dumps(detail or {}, sort_keys=True)))

    def events(self, *, record: Optional[tuple[str, str]] = None, after: int = 0,
               before: Optional[int] = None, limit: int = 50, newest_first: bool = False) -> list[dict]:
        sql, args = "SELECT * FROM project_events WHERE seq > ?", [after]
        if record is not None:
            sql += " AND subject = ?"
            args.append(subject(*record))
        if before is not None:
            sql += " AND seq < ?"
            args.append(before)
        sql += f" ORDER BY seq {'DESC' if newest_first else 'ASC'} LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        with self._tx() as conn:
            return [{**dict(r), "detail": json.loads(r["detail"])} for r in conn.execute(sql, args)]

    # -- projects ------------------------------------------------------------
    @staticmethod
    def _project(row) -> Project:
        return Project(**{k: row[k] for k in row.keys()})

    def get(self, project_id: str) -> Optional[Project]:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._project(row) if row else None

    def require(self, project_id: str) -> Project:
        found = self.get(str(project_id or ""))
        if found is None:
            raise ProjectError("not_found", "there is no such project")
        return found

    def projects(self, owners: Iterable[str], *, include_archived: bool = False) -> list[Project]:
        owners = list(owners)
        sql = f"SELECT * FROM projects WHERE owner_id IN ({','.join('?' * len(owners))})"
        if not include_archived:
            sql += " AND lifecycle <> 'archived'"
        with self._tx() as conn:
            rows = conn.execute(sql + " ORDER BY display_name COLLATE NOCASE", owners).fetchall()
        return [self._project(r) for r in rows]

    def find_by_key(self, owners: Iterable[str], key: str) -> list[Project]:
        owners = list(owners)
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT * FROM projects WHERE spoken_key = ? AND lifecycle <> 'archived' "
                f"AND owner_id IN ({','.join('?' * len(owners))})", [spoken_key(key), *owners]).fetchall()
        return [self._project(r) for r in rows]

    def project_for_legacy(self, owners: Iterable[str], source: str, key: str) -> Optional[Project]:
        """The project a legacy string names, through its aliases, then by
        its spoken key."""
        owners = list(owners)
        with self._tx() as conn:
            row = conn.execute(
                f"SELECT p.* FROM project_aliases a JOIN projects p ON p.id = a.project_id "
                f"WHERE a.source = ? AND a.alias_key = ? AND a.owner_id IN ({','.join('?' * len(owners))}) "
                f"AND p.lifecycle <> 'archived'", [source, spoken_key(key), *owners]).fetchone()
        if row:
            return self._project(row)
        found = self.find_by_key(owners, key)
        return found[0] if len(found) == 1 else None

    def create(self, owner: str, name: str, *, summary: str = "", actor: str = "page",
               notes_slug: Optional[str] = None, conn=None) -> Project:
        from jarvis import jarvis_memory
        name = _name(name)
        if len(summary) > SUMMARY_MAX:
            raise ProjectError("invalid_request", f"a summary is at most {SUMMARY_MAX} characters")
        now = time.time()
        pid = str(uuid.uuid4())
        row = (pid, owner, data_paths.node_id(), name, spoken_key(name),
               notes_slug or jarvis_memory.slugify(name), summary, "active", 1, now, now, None)

        def write(c):
            try:
                c.execute("INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
            except sqlite3.IntegrityError:
                raise ProjectError("name_taken", "there is already a project by that name") from None
            self._event(c, "project", pid, "created", 1, actor, {"name": name})
        if conn is not None:
            write(conn)
        else:
            with self._tx() as c:
                write(c)
        return Project(*row)

    def change(self, project_id: str, *, expected_revision: Optional[int] = None,
               name: Optional[str] = None, summary: Optional[str] = None,
               lifecycle: Optional[str] = None, actor: str = "page") -> Project:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                raise ProjectError("not_found", "there is no such project")
            p = self._project(row)
            if expected_revision is not None and expected_revision != p.revision:
                raise ProjectError("stale_revision", "the project changed meanwhile; read it again")
            changes: dict = {}
            if name is not None:
                changes["display_name"] = _name(name)
                changes["spoken_key"] = spoken_key(changes["display_name"])
            if summary is not None:
                if len(summary) > SUMMARY_MAX:
                    raise ProjectError("invalid_request", f"a summary is at most {SUMMARY_MAX} characters")
                changes["summary"] = summary
            if lifecycle is not None:
                if lifecycle not in LIFECYCLES:
                    raise ProjectError("invalid_request", f"lifecycle is one of {', '.join(LIFECYCLES)}")
                changes["lifecycle"] = lifecycle
                changes["archived_at"] = time.time() if lifecycle == "archived" else None
            if not changes:
                return p
            changes["revision"] = p.revision + 1
            changes["updated_at"] = time.time()
            sets = ", ".join(f"{k} = ?" for k in changes)
            try:
                conn.execute(f"UPDATE projects SET {sets} WHERE id = ?", [*changes.values(), project_id])
            except sqlite3.IntegrityError:
                raise ProjectError("name_taken", "there is already a project by that name") from None
            action = ("renamed" if name is not None else "lifecycle_changed" if lifecycle is not None
                      else "summary_changed")
            self._event(conn, "project", project_id, action, changes["revision"], actor,
                        {k: v for k, v in changes.items() if k in ("display_name", "lifecycle")})
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._project(row)

    def _bump(self, conn, project_id: str) -> int:
        conn.execute("UPDATE projects SET revision = revision + 1, updated_at = ? WHERE id = ?",
                     (time.time(), project_id))
        return conn.execute("SELECT revision FROM projects WHERE id = ?", (project_id,)).fetchone()[0]

    # -- aliases -------------------------------------------------------------------
    def add_alias(self, owner: str, source: str, key: str, project_id: str, *, conn=None) -> None:
        if source not in ALIAS_SOURCES:
            raise ProjectError("invalid_request", f"an alias source is one of {', '.join(ALIAS_SOURCES)}")
        args = (owner, source, spoken_key(key), project_id, time.time())

        def write(c):
            c.execute("INSERT OR IGNORE INTO project_aliases VALUES (?,?,?,?,?)", args)
        if conn is not None:
            write(conn)
        else:
            with self._tx() as c:
                write(c)

    def aliases(self, project_id: str) -> list[dict]:
        with self._tx() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT source, alias_key FROM project_aliases WHERE project_id = ? ORDER BY source, alias_key",
                (project_id,))]

    # -- folders ------------------------------------------------------------------
    @staticmethod
    def _pf(row) -> ProjectFolder:
        return ProjectFolder(row["project_id"], row["folder_id"], row["part"], bool(row["is_home"]),
                             row["index_scope"], row["revision"], row["added_at"])

    def folders_of(self, project_id: str) -> list[ProjectFolder]:
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM project_folders WHERE project_id = ? AND removed_at IS NULL "
                                "ORDER BY is_home DESC, added_at", (project_id,)).fetchall()
        return [self._pf(r) for r in rows]

    def projects_of_folder(self, folder_id: str) -> list[Project]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT p.* FROM project_folders f JOIN projects p ON p.id = f.project_id "
                "WHERE f.folder_id = ? AND f.removed_at IS NULL AND p.lifecycle <> 'archived'",
                (folder_id,)).fetchall()
        return [self._project(r) for r in rows]

    def linked_folder_ids(self) -> set[str]:
        with self._tx() as conn:
            return {r[0] for r in conn.execute(
                "SELECT DISTINCT folder_id FROM project_folders WHERE removed_at IS NULL")}

    def link_folder(self, project_id: str, folder_id: str, *, part: str = "source", home: bool = False,
                    index_scope: str = "docs", actor: str = "page", conn=None) -> None:
        if part not in PARTS:
            raise ProjectError("invalid_request", f"a part is one of {', '.join(PARTS)}")
        if index_scope not in INDEX_SCOPES:
            raise ProjectError("invalid_request", f"the index scope is one of {', '.join(INDEX_SCOPES)}")

        def write(c):
            if c.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ProjectError("not_found", "there is no such project")
            live = c.execute("SELECT * FROM project_folders WHERE project_id = ? AND folder_id = ? "
                             "AND removed_at IS NULL", (project_id, folder_id)).fetchone()
            if live is not None:
                raise ProjectError("already_answered", "that folder is already in the project")
            if home:
                c.execute("UPDATE project_folders SET is_home = 0 WHERE project_id = ? AND removed_at IS NULL",
                          (project_id,))
            rev = self._bump(c, project_id)
            c.execute("INSERT INTO project_folders VALUES (?,?,?,?,?,?,?,NULL)",
                      (project_id, folder_id, part, 1 if home else 0, index_scope, 1, time.time()))
            self._event(c, "project", project_id, "folder_linked", rev, actor,
                        {"folder_id": folder_id, "part": part, "home": home})
        if conn is not None:
            write(conn)
        else:
            with self._tx() as c:
                write(c)

    def change_folder(self, project_id: str, folder_id: str, *, unlink: bool = False,
                      home: Optional[bool] = None, part: Optional[str] = None,
                      index_scope: Optional[str] = None, actor: str = "page") -> None:
        with self._tx() as c:
            live = c.execute("SELECT * FROM project_folders WHERE project_id = ? AND folder_id = ? "
                             "AND removed_at IS NULL", (project_id, folder_id)).fetchone()
            if live is None:
                raise ProjectError("not_found", "that folder is not in the project")
            key = (project_id, folder_id, live["added_at"])
            if unlink:
                c.execute("UPDATE project_folders SET removed_at = ? WHERE project_id = ? AND folder_id = ? "
                          "AND added_at = ?", (time.time(), *key))
                action, detail = "folder_unlinked", {"folder_id": folder_id}
            else:
                sets, args, detail = [], [], {"folder_id": folder_id}
                if home is not None:
                    if home:
                        c.execute("UPDATE project_folders SET is_home = 0 WHERE project_id = ? "
                                  "AND removed_at IS NULL", (project_id,))
                    sets.append("is_home = ?")
                    args.append(1 if home else 0)
                    detail["home"] = home
                if part is not None:
                    if part not in PARTS:
                        raise ProjectError("invalid_request", f"a part is one of {', '.join(PARTS)}")
                    sets.append("part = ?")
                    args.append(part)
                    detail["part"] = part
                if index_scope is not None:
                    if index_scope not in INDEX_SCOPES:
                        raise ProjectError("invalid_request",
                                           f"the index scope is one of {', '.join(INDEX_SCOPES)}")
                    sets.append("index_scope = ?")
                    args.append(index_scope)
                    detail["index_scope"] = index_scope
                if not sets:
                    return
                sets.append("revision = revision + 1")
                c.execute(f"UPDATE project_folders SET {', '.join(sets)} WHERE project_id = ? AND folder_id = ? "
                          "AND added_at = ?", [*args, *key])
                action = "folder_changed"
            rev = self._bump(c, project_id)
            self._event(c, "project", project_id, action, rev, actor, detail)

    # -- candidates ----------------------------------------------------------------
    @staticmethod
    def _candidate(row) -> Candidate:
        return Candidate(row["id"], row["owner_id"], row["proposed_name"], json.loads(row["evidence"]),
                         row["fingerprint"], row["state"], row["project_id"], row["created_at"], row["updated_at"])

    def offer_candidate(self, owner: str, name: str, evidence: dict) -> Optional[Candidate]:
        """Record what JARVIS inferred, as a proposal. A candidate he ignored
        stays ignored until its evidence changes; one he accepted stays."""
        fp = fingerprint(evidence)
        key = spoken_key(name)
        now = time.time()
        with self._tx() as conn:
            rows = [self._candidate(r) for r in conn.execute(
                "SELECT * FROM project_candidates WHERE owner_id = ?", (owner,))]
            held = next((c for c in rows if spoken_key(c.proposed_name) == key), None)
            if held is None:
                cid = str(uuid.uuid4())
                conn.execute("INSERT INTO project_candidates VALUES (?,?,?,?,?,?,NULL,?,?)",
                             (cid, owner, name, json.dumps(evidence, sort_keys=True), fp, "proposed", now, now))
                self._event(conn, "candidate", cid, "proposed", 1, "migration", {"name": name})
                return self._candidate(conn.execute("SELECT * FROM project_candidates WHERE id = ?",
                                                    (cid,)).fetchone())
            if held.state == "accepted" or held.fingerprint == fp:
                return held
            state = "proposed"           # evidence changed: offered again, even if ignored
            conn.execute("UPDATE project_candidates SET evidence = ?, fingerprint = ?, state = ?, updated_at = ? "
                         "WHERE id = ?", (json.dumps(evidence, sort_keys=True), fp, state, now, held.id))
            self._event(conn, "candidate", held.id, "evidence_changed", 1, "migration", {})
            return self._candidate(conn.execute("SELECT * FROM project_candidates WHERE id = ?",
                                                (held.id,)).fetchone())

    def candidates(self, owners: Iterable[str], state: Optional[str] = "proposed") -> list[Candidate]:
        owners = list(owners)
        sql = f"SELECT * FROM project_candidates WHERE owner_id IN ({','.join('?' * len(owners))})"
        args: list = list(owners)
        if state:
            sql += " AND state = ?"
            args.append(state)
        with self._tx() as conn:
            return [self._candidate(r) for r in conn.execute(sql + " ORDER BY proposed_name", args)]

    def candidate(self, candidate_id: str) -> Candidate:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM project_candidates WHERE id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise ProjectError("not_found", "there is no such candidate")
        return self._candidate(row)

    def answer_candidate(self, candidate_id: str, action: str, *, name: Optional[str] = None,
                         folders: Optional[list[str]] = None, home: Optional[str] = None,
                         into: Optional[str] = None, actor: str = "page") -> dict:
        """accept (a new project with the chosen folders, home and the legacy
        aliases, in one transaction), merge (into an existing project),
        ignore or restore."""
        cand = self.candidate(candidate_id)
        if action == "restore":
            if cand.state != "ignored":
                raise ProjectError("already_answered", "only an ignored candidate is restored")
            with self._tx() as conn:
                conn.execute("UPDATE project_candidates SET state = 'proposed', updated_at = ? WHERE id = ?",
                             (time.time(), candidate_id))
                self._event(conn, "candidate", candidate_id, "restored", 1, actor, {})
            return {"done": "restore", "candidate": candidate_id}
        if cand.state != "proposed":
            raise ProjectError("already_answered", "that candidate has already been answered")
        if action == "ignore":
            with self._tx() as conn:
                conn.execute("UPDATE project_candidates SET state = 'ignored', updated_at = ? WHERE id = ?",
                             (time.time(), candidate_id))
                self._event(conn, "candidate", candidate_id, "ignored", 1, actor, {})
            return {"done": "ignore", "candidate": candidate_id}
        if action not in ("accept", "merge"):
            raise ProjectError("invalid_request", "the action is accept, merge, ignore or restore")
        offered = [f["folder_id"] for f in cand.evidence.get("folders") or []]
        chosen = offered if folders is None else [f for f in folders if f in offered]
        if home is not None and home not in chosen:
            raise ProjectError("invalid_request", "the home folder must be one of the project's folders")
        with self._tx() as conn:
            if action == "accept":
                project = self.create(cand.owner_id, name or cand.proposed_name, actor=actor, conn=conn)
                pid = project.id
            else:
                row = conn.execute("SELECT * FROM projects WHERE id = ?", (into,)).fetchone()
                if row is None:
                    raise ProjectError("not_found", "there is no such project to merge into")
                pid = row["id"]
            live = {r[0] for r in conn.execute("SELECT folder_id FROM project_folders WHERE project_id = ? "
                                               "AND removed_at IS NULL", (pid,))}
            home_id = home or (chosen[0] if chosen and action == "accept" else None)
            for fid in chosen:
                if fid not in live:
                    part = next((f.get("part") for f in cand.evidence.get("folders") or []
                                 if f["folder_id"] == fid), None) or "source"
                    self.link_folder(pid, fid, part=part if part in PARTS else "source",
                                     home=(fid == home_id), actor=actor, conn=conn)
            for legacy in cand.evidence.get("legacy") or []:
                if legacy.get("source") in ALIAS_SOURCES:
                    self.add_alias(cand.owner_id, legacy["source"], legacy["key"], pid, conn=conn)
            conn.execute("UPDATE project_candidates SET state = 'accepted', project_id = ?, updated_at = ? "
                         "WHERE id = ?", (pid, time.time(), candidate_id))
            self._event(conn, "candidate", candidate_id, "accepted", 1, actor, {"project_id": pid})
        return {"done": action, "candidate": candidate_id, "project_id": pid}

    # -- works ---------------------------------------------------------------------
    def _work(self, conn, row) -> Work:
        w = Work(**{k: row[k] for k in row.keys()})
        w.projects = [dict(r) for r in conn.execute(
            "SELECT project_id, role FROM work_projects WHERE work_id = ? ORDER BY role DESC, added_at", (w.id,))]
        w.folders = [r[0] for r in conn.execute("SELECT folder_id FROM work_folders WHERE work_id = ?", (w.id,))]
        return w

    def work(self, work_id: str) -> Work:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
            if row is None:
                raise ProjectError("not_found", "there is no such piece of work")
            return self._work(conn, row)

    def works(self, owners: Iterable[str], *, state: Optional[str] = "open",
              project_id: Optional[str] = None) -> list[Work]:
        owners = list(owners)
        sql = f"SELECT * FROM works WHERE owner_id IN ({','.join('?' * len(owners))})"
        args: list = list(owners)
        if state:
            sql += " AND state = ?"
            args.append(state)
        if project_id:
            sql += " AND id IN (SELECT work_id FROM work_projects WHERE project_id = ?)"
            args.append(project_id)
        with self._tx() as conn:
            return [self._work(conn, r) for r in conn.execute(sql + " ORDER BY updated_at DESC", args)]

    def start_work(self, owner: str, title: str, *, projects: list[str], outcome: str = "",
                   folders: Iterable[str] = (), actor: str = "page") -> Work:
        title = _name(title, "a piece of work's title", TITLE_MAX)
        if not projects:
            raise ProjectError("invalid_request", "a piece of work belongs to at least one project")
        wid, now = str(uuid.uuid4()), time.time()
        with self._tx() as conn:
            for pid in projects:
                if conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone() is None:
                    raise ProjectError("not_found", "there is no such project")
            conn.execute("INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                         (wid, owner, title, outcome, "open", "local", 1, now, now))
            for i, pid in enumerate(dict.fromkeys(projects)):
                conn.execute("INSERT INTO work_projects VALUES (?,?,?,?)",
                             (wid, pid, "primary" if i == 0 else "involved", now))
            for fid in dict.fromkeys(folders):
                conn.execute("INSERT INTO work_folders VALUES (?,?,?)", (wid, fid, now))
            self._event(conn, "work", wid, "started", 1, actor, {"title": title, "projects": list(projects)})
            return self._work(conn, conn.execute("SELECT * FROM works WHERE id = ?", (wid,)).fetchone())

    def change_work(self, work_id: str, *, title: Optional[str] = None, outcome: Optional[str] = None,
                    add_project: Optional[str] = None, remove_project: Optional[str] = None,
                    add_folder: Optional[str] = None, remove_folder: Optional[str] = None,
                    state: Optional[str] = None, actor: str = "page") -> Work:
        now = time.time()
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
            if row is None:
                raise ProjectError("not_found", "there is no such piece of work")
            sets, args, detail = [], [], {}
            if title is not None:
                sets.append("title = ?")
                args.append(_name(title, "a piece of work's title", TITLE_MAX))
                detail["title"] = args[-1]
            if outcome is not None:
                sets.append("outcome = ?")
                args.append(outcome)
            if state is not None:
                if state not in WORK_STATES:
                    raise ProjectError("invalid_request", f"a work's state is one of {', '.join(WORK_STATES)}")
                sets += ["state = ?", "closed_at = ?"]
                args += [state, now if state != "open" else None]
                detail["state"] = state
            if add_project:
                if conn.execute("SELECT 1 FROM projects WHERE id = ?", (add_project,)).fetchone() is None:
                    raise ProjectError("not_found", "there is no such project")
                conn.execute("INSERT OR IGNORE INTO work_projects VALUES (?,?,?,?)",
                             (work_id, add_project, "involved", now))
                detail["added_project"] = add_project
            if remove_project:
                primary = conn.execute("SELECT project_id FROM work_projects WHERE work_id = ? AND role = 'primary'",
                                       (work_id,)).fetchone()
                if primary and primary[0] == remove_project:
                    raise ProjectError("not_allowed", "the primary project stays; close the work instead")
                conn.execute("DELETE FROM work_projects WHERE work_id = ? AND project_id = ?",
                             (work_id, remove_project))
                detail["removed_project"] = remove_project
            if add_folder:
                conn.execute("INSERT OR IGNORE INTO work_folders VALUES (?,?,?)", (work_id, add_folder, now))
                detail["added_folder"] = add_folder
            if remove_folder:
                conn.execute("DELETE FROM work_folders WHERE work_id = ? AND folder_id = ?", (work_id, remove_folder))
                detail["removed_folder"] = remove_folder
            sets += ["revision = revision + 1", "updated_at = ?"]
            args.append(now)
            conn.execute(f"UPDATE works SET {', '.join(sets)} WHERE id = ?", [*args, work_id])
            rev = conn.execute("SELECT revision FROM works WHERE id = ?", (work_id,)).fetchone()[0]
            self._event(conn, "work", work_id, "changed", rev, actor, detail)
            return self._work(conn, conn.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone())

    # -- accomplishments --------------------------------------------------------------
    def _acc(self, conn, row) -> Accomplishment:
        a = Accomplishment(**{k: row[k] for k in row.keys()})
        a.evidence = [r[0] for r in conn.execute(
            "SELECT ref FROM accomplishment_evidence WHERE accomplishment_id = ? ORDER BY ref", (a.id,))]
        return a

    def accomplishments(self, project_id: str, *, states: Iterable[str] = ("confirmed",),
                        limit: int = 50) -> list[Accomplishment]:
        states = list(states)
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT * FROM accomplishments WHERE project_id = ? AND state IN ({','.join('?' * len(states))}) "
                "ORDER BY achieved_at DESC LIMIT ?", [project_id, *states, limit]).fetchall()
            return [self._acc(conn, r) for r in rows]

    def propose_accomplishment(self, project_id: str, title: str, *, detail: str = "",
                               proposed_by: str = "page", work_id: Optional[str] = None,
                               evidence: Iterable[str] = (), achieved_at: Optional[float] = None,
                               confirm: bool = False, actor: str = "page") -> Accomplishment:
        title = _name(title, "an accomplishment", TITLE_MAX)
        if proposed_by not in PROPOSED_BY:
            raise ProjectError("invalid_request", f"proposed_by is one of {', '.join(PROPOSED_BY)}")
        aid, now = str(uuid.uuid4()), time.time()
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ProjectError("not_found", "there is no such project")
            state = "confirmed" if confirm else "proposed"
            conn.execute("INSERT INTO accomplishments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (aid, project_id, work_id, title, detail, state, proposed_by,
                          achieved_at or now, now if confirm else None, 1, now))
            for ref in dict.fromkeys(evidence):
                conn.execute("INSERT INTO accomplishment_evidence VALUES (?,?)", (aid, str(ref)))
            self._event(conn, "accomplishment", aid, "recorded" if confirm else "proposed", 1, actor,
                        {"project_id": project_id, "title": title})
            return self._acc(conn, conn.execute("SELECT * FROM accomplishments WHERE id = ?", (aid,)).fetchone())

    def accomplishment(self, accomplishment_id: str) -> Optional[Accomplishment]:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM accomplishments WHERE id = ?", (accomplishment_id,)).fetchone()
            return self._acc(conn, row) if row is not None else None

    def answer_accomplishment(self, accomplishment_id: str, action: str, *, actor: str = "page") -> Accomplishment:
        if action not in ("confirm", "withdraw"):
            raise ProjectError("invalid_request", "the action is confirm or withdraw")
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM accomplishments WHERE id = ?", (accomplishment_id,)).fetchone()
            if row is None:
                raise ProjectError("not_found", "there is no such accomplishment")
            if row["state"] == ("confirmed" if action == "confirm" else "withdrawn"):
                raise ProjectError("already_answered", "that accomplishment is already answered that way")
            state = "confirmed" if action == "confirm" else "withdrawn"
            conn.execute("UPDATE accomplishments SET state = ?, confirmed_at = ?, revision = revision + 1 "
                         "WHERE id = ?", (state, time.time() if action == "confirm" else row["confirmed_at"],
                                          accomplishment_id))
            self._event(conn, "accomplishment", accomplishment_id, f"{state}", row["revision"] + 1, actor,
                        {"project_id": row["project_id"]})
            return self._acc(conn, conn.execute("SELECT * FROM accomplishments WHERE id = ?",
                                                (accomplishment_id,)).fetchone())

    # -- bindings --------------------------------------------------------------------
    def bind(self, subject_ref: str, *, project_id: Optional[str] = None, work_id: Optional[str] = None,
             folder_id: Optional[str] = None, how: str = "started_here", actor: str = "voice") -> None:
        if how not in ("started_here", "owner", "adopted"):
            raise ProjectError("invalid_request", "how is started_here, owner or adopted")
        with self._tx() as conn:
            conn.execute("INSERT OR IGNORE INTO project_bindings VALUES (?,?,?,?,?,?,NULL)",
                         (subject_ref, project_id, work_id, folder_id, how, time.time()))
            if project_id:
                rev = self._bump(conn, project_id)
                self._event(conn, "project", project_id, "bound", rev, actor, {"subject": subject_ref, "how": how})

    def bound_to(self, *, work_id: Optional[str] = None, project_id: Optional[str] = None) -> list[dict]:
        """What is bound to a work or a project now, newest first."""
        column, value = ("work_id", work_id) if work_id else ("project_id", project_id)
        with self._tx() as conn:
            rows = conn.execute(f"SELECT * FROM project_bindings WHERE {column} = ? AND unbound_at IS NULL "
                                "ORDER BY bound_at DESC", (value,)).fetchall()
        return [dict(r) for r in rows]

    def binding(self, subject_ref: str) -> Optional[dict]:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM project_bindings WHERE subject = ? AND unbound_at IS NULL "
                               "ORDER BY bound_at DESC", (subject_ref,)).fetchone()
        return dict(row) if row else None
