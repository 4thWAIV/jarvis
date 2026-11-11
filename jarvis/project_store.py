"""Projects, pieces of work and accomplishments: records of truth, as
documents in the engine that holds JARVIS's records (`records.py`; round 3
of docs/planning/knowledge-engine/plan-uds.md; design section 5).

  /projects/<project id>.project
                    an area of work with accomplishments: a stable ID, its
                    owner, its name and spoken key, the notes file it joins,
                    a summary, a lifecycle (active, paused, done, archived)
                    and a revision; the names it was known by before it had
                    an ID (`aliases`: a run's project string, a memory slug,
                    one project per (owner, source, key)); the registered
                    folders it holds (`folders`, each a reference to its
                    folder document, with a part, at most one home and what
                    the indexer may read); its accomplishments (proposed,
                    then confirmed or withdrawn; only confirmed ones count
                    anywhere) with their evidence; and what was placed in it
                    (`bindings`)
  /proposals/projects/<candidate id>.proposal
                    a project JARVIS inferred and has not been told to keep,
                    with its evidence and a fingerprint, so an ignored one
                    stays ignored until it changes
  /work/works/<work id>.work
                    a piece of work spanning one or several projects (exactly
                    one primary) and the independent folders it uses
                    directly; kept below /work but never directly in it, so
                    the Work tracker's own list never reads it (it is being
                    folded into Work)
  /settings/project-records.settings
                    the last event sequence number given

Every committed change is a version of the document it changes, and carries
its events (`events`: seq, at, subject, action, revision, actor, detail) in
that version: the page's history is the document's versions. A change is one
write (`records.atomic`) that moves the record's revision and takes the next
sequence number, so changes are one at a time and a hint (sent by the
caller after it returns) never runs ahead of a write. They are operational
records: runs, agents and the page join to them. They are not knowledge
edges.

This module is the only writer. Nothing inferred becomes a record without
his yes (I9): candidates are proposals until accepted.

`MAPPINGS` moves the tables this record replaced (`projects`,
`project_aliases`, `project_folders`, `project_candidates`, `works`,
`work_projects`, `work_folders`, `accomplishments`,
`accomplishment_evidence`, `project_bindings`, `project_events`) into these
documents and back, row for row (`knowledge_move`).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, TypeVar

from jarvis import data_paths, records, workspace_ref
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

PROJECTS = "/projects"
CANDIDATES = "/proposals/projects"
WORKS = "/work/works"
SEQUENCE = "/settings/project-records.settings"
AREA = "project-records"

# HTTP status of each refusal's code, for the routes.
STATUS = {"not_found": 404, "name_taken": 409, "stale_revision": 409, "invalid_request": 400,
          "already_answered": 409, "not_allowed": 400}

T = TypeVar("T")


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


def project_path(project_id: str) -> str:
    return f"{PROJECTS}/{records.segment(project_id)}.project"


def candidate_path(candidate_id: str) -> str:
    return f"{CANDIDATES}/{records.segment(candidate_id)}.proposal"


def work_path(work_id: str) -> str:
    return f"{WORKS}/{records.segment(work_id)}.work"


def _folder_ref(folder_id: str) -> str:
    from jarvis.folder_store import folder_path
    return folder_path(folder_id)


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


def _nocase(text: str) -> str:
    """SQLite's NOCASE: ASCII letters folded, nothing else."""
    return "".join(c.lower() if c.isascii() else c for c in str(text))


# -- documents to records --------------------------------------------------------

_PROJECT_FIELDS = ("id", "owner_id", "node_id", "display_name", "spoken_key", "notes_slug", "summary",
                   "lifecycle", "revision", "created_at", "updated_at", "archived_at")
_WORK_FIELDS = ("id", "owner_id", "title", "outcome", "state", "origin", "revision", "created_at",
                "updated_at", "closed_at")
_ACC_FIELDS = ("id", "work_id", "title", "detail", "state", "proposed_by", "achieved_at",
               "confirmed_at", "revision", "created_at")
_CAND_FIELDS = ("id", "owner_id", "proposed_name", "evidence", "fingerprint", "state", "project_id",
                "created_at", "updated_at")


def _project(d: dict) -> Project:
    return Project(*(d.get(k) for k in _PROJECT_FIELDS))


def _pf(project_id: str, f: dict) -> ProjectFolder:
    return ProjectFolder(project_id, f["folder_id"], f["part"], bool(f["is_home"]), f["index_scope"],
                         f["revision"], f["added_at"])


def _live_folders(d: dict) -> list[dict]:
    return [f for f in d.get("folders") or [] if f.get("removed_at") is None]


def _candidate(d: dict) -> Candidate:
    return Candidate(*(d.get(k) for k in _CAND_FIELDS))


def _work(d: dict) -> Work:
    w = Work(*(d.get(k) for k in _WORK_FIELDS))
    w.projects = [{"project_id": p["project_id"], "role": p["role"]} for p in sorted(
        d.get("projects") or [], key=lambda p: (p["role"] != "primary", p["added_at"]))]
    w.folders = [f["folder_id"] for f in d.get("folders") or []]
    return w


def _acc(project_id: str, a: dict) -> Accomplishment:
    out = Accomplishment(a["id"], project_id, *(a.get(k) for k in _ACC_FIELDS[1:]))
    out.evidence = sorted(e["ref"] for e in a.get("evidence") or [])
    return out


def _caller(actor: str):
    if actor in ("page", "voice"):
        return records.TIM
    if actor == "migration":
        return records.JARVIS
    return records.by(actor)


def _fresh(data: dict) -> dict:
    data["events"] = []
    return data


class _Change:
    """One change being gathered in a unit: the documents it touches, each
    with this version's events, and the next sequence numbers."""

    def __init__(self, unit: records.Unit) -> None:
        self.unit = unit
        self.docs: dict[str, dict] = {}
        self.dirty: list[str] = []
        self.sequence: Optional[dict] = None

    def get(self, path: str) -> Optional[dict]:
        if path not in self.docs:
            rec = self.unit.get(path)
            if rec is None:
                return None
            self.docs[path] = _fresh(rec.data)
        return self.docs[path]

    def scan(self, under: str, types: list[str]) -> list[dict]:
        out = []
        for rec in self.unit.scan(under, types=types):
            if rec.path not in self.docs:
                self.docs[rec.path] = _fresh(rec.data)
            out.append(self.docs[rec.path])
        return out

    def new(self, path: str, data: dict) -> dict:
        self.docs[path] = _fresh(data)
        self.touch(path)
        return self.docs[path]

    def touch(self, path: str) -> None:
        if path not in self.dirty:
            self.dirty.append(path)

    def event(self, path: str, kind: str, record_id: str, action: str, revision: int, actor: str,
              detail: Optional[dict] = None) -> None:
        if actor not in ACTORS:
            raise ProjectError("invalid_request", f"actor is one of {', '.join(ACTORS)}")
        if self.sequence is None:
            rec = self.unit.get(SEQUENCE)
            self.sequence = dict(rec.data) if rec is not None else {}
            self.sequence.setdefault("last_seq", 0)
            self.sequence.setdefault("orphans", {})
            self.sequence.update(area=AREA, events=[])
        self.sequence["last_seq"] += 1
        self.docs[path]["events"].append(
            {"seq": self.sequence["last_seq"], "at": time.time(), "subject": subject(kind, record_id),
             "action": action, "revision": revision, "actor": actor,
             "detail": json.loads(json.dumps(detail or {}, sort_keys=True))})
        self.touch(path)

    def flush(self) -> None:
        for path in self.dirty:
            data = self.docs[path]
            last = data["events"][-1] if data["events"] else None
            self.unit.put(path, data, change=last["action"] if last else "changed",
                          summary=str(data.get("display_name") or data.get("title")
                                      or data.get("proposed_name") or "")[:200] or None)
        if self.sequence is not None:
            self.unit.put(SEQUENCE, self.sequence)


class ProjectStore:
    """The records over the record engine. `path` is accepted for callers
    that gave a store its own SQLite file before; the records are the
    engine's (`records.uds`) whichever is given."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else None

    def schema_version(self) -> int:
        return SCHEMA_VERSION

    @staticmethod
    def _write(fn: Callable[[_Change], T], actor: str = "page") -> T:
        def run(unit: records.Unit) -> T:
            change = _Change(unit)
            result = fn(change)
            change.flush()
            return result
        return records.atomic(run, by=_caller(actor))

    @staticmethod
    def _run(conn: Optional[_Change], fn: Callable[[_Change], T], actor: str) -> T:
        """`fn` inside the change `conn` names (another of this store's
        changes), or as a change of its own."""
        if conn is not None:
            return fn(conn)
        return ProjectStore._write(fn, actor)

    # -- reading ---------------------------------------------------------------
    @staticmethod
    def _projects_data() -> list[dict]:
        return [r.data for r in records.scan(PROJECTS, types=["project"])]

    @staticmethod
    def _works_data() -> list[dict]:
        return [r.data for r in records.scan(WORKS, types=["work"])]

    # -- events ------------------------------------------------------------
    def _holder(self, kind: str, record_id: str) -> Optional[str]:
        if kind == "project":
            return project_path(record_id)
        if kind == "candidate":
            return candidate_path(record_id)
        if kind == "work":
            return work_path(record_id)
        if kind == "accomplishment":
            for d in self._projects_data():
                if any(a["id"] == record_id for a in d.get("accomplishments") or []):
                    return project_path(d["id"])
        return None

    def events(self, *, record: Optional[tuple[str, str]] = None, after: int = 0,
               before: Optional[int] = None, limit: int = 50, newest_first: bool = False) -> list[dict]:
        if record is not None:
            holder = self._holder(*record)
            paths = [holder] if holder else []
            wanted = subject(*record)
        else:
            paths = ([project_path(d["id"]) for d in self._projects_data()]
                     + [r.path for r in records.scan(CANDIDATES, types=["proposal"])]
                     + [work_path(d["id"]) for d in self._works_data()])
            wanted = None
        found: list[dict] = []
        for path in paths:
            for v in records.history(path, fields=["events"]):
                found += [e for e in (v.data or {}).get("events") or []
                          if wanted is None or e["subject"] == wanted]
        rec = records.get(SEQUENCE)
        for kept in ((rec.data.get("orphans") or {}).get("project_events") or []) if rec else []:
            e = _orphan_event(kept)
            if wanted is None or e["subject"] == wanted:
                found.append(e)
        found = [e for e in found if e["seq"] > after and (before is None or e["seq"] < before)]
        found.sort(key=lambda e: e["seq"], reverse=newest_first)
        return [dict(e, detail=dict(e.get("detail") or {})) for e in found[:max(1, min(int(limit), 500))]]

    # -- projects ------------------------------------------------------------
    def get(self, project_id: str) -> Optional[Project]:
        if not project_id:
            return None
        rec = records.get(project_path(project_id))
        return _project(rec.data) if rec is not None and rec.data.get("id") == project_id else None

    def require(self, project_id: str) -> Project:
        found = self.get(str(project_id or ""))
        if found is None:
            raise ProjectError("not_found", "there is no such project")
        return found

    def projects(self, owners: Iterable[str], *, include_archived: bool = False) -> list[Project]:
        owners = set(owners)
        found = [d for d in self._projects_data() if d["owner_id"] in owners
                 and (include_archived or d["lifecycle"] != "archived")]
        found.sort(key=lambda d: _nocase(d["display_name"]))
        return [_project(d) for d in found]

    def find_by_key(self, owners: Iterable[str], key: str) -> list[Project]:
        owners, key = set(owners), spoken_key(key)
        return [_project(d) for d in self._projects_data()
                if d["spoken_key"] == key and d["lifecycle"] != "archived" and d["owner_id"] in owners]

    def project_for_legacy(self, owners: Iterable[str], source: str, key: str) -> Optional[Project]:
        """The project a legacy string names, through its aliases, then by
        its spoken key."""
        owners, wanted = set(owners), spoken_key(key)
        for d in self._projects_data():
            if d["lifecycle"] == "archived":
                continue
            if any(a["source"] == source and a["alias_key"] == wanted and a["owner_id"] in owners
                   for a in d.get("aliases") or []):
                return _project(d)
        found = self.find_by_key(owners, key)
        return found[0] if len(found) == 1 else None

    @staticmethod
    def _taken(c: _Change, owner: str, key: str, but: Optional[str] = None) -> None:
        for d in c.scan(PROJECTS, ["project"]):
            if (d["id"] != but and d["owner_id"] == owner and d["spoken_key"] == key
                    and d["lifecycle"] != "archived"):
                raise ProjectError("name_taken", "there is already a project by that name")

    def create(self, owner: str, name: str, *, summary: str = "", actor: str = "page",
               notes_slug: Optional[str] = None, conn: Optional[_Change] = None) -> Project:
        from jarvis import jarvis_memory
        name = _name(name)
        if len(summary) > SUMMARY_MAX:
            raise ProjectError("invalid_request", f"a summary is at most {SUMMARY_MAX} characters")
        now = time.time()
        pid = str(uuid.uuid4())
        data = {"id": pid, "owner_id": owner, "node_id": data_paths.node_id(), "display_name": name,
                "spoken_key": spoken_key(name), "notes_slug": notes_slug or jarvis_memory.slugify(name),
                "summary": summary, "lifecycle": "active", "revision": 1, "created_at": now,
                "updated_at": now, "archived_at": None, "aliases": [], "folders": [],
                "accomplishments": [], "bindings": []}

        def write(c: _Change) -> Project:
            self._taken(c, owner, data["spoken_key"])
            c.new(project_path(pid), dict(data))
            c.event(project_path(pid), "project", pid, "created", 1, actor, {"name": name})
            return _project(data)
        return self._run(conn, write, actor)

    def change(self, project_id: str, *, expected_revision: Optional[int] = None,
               name: Optional[str] = None, summary: Optional[str] = None,
               lifecycle: Optional[str] = None, actor: str = "page") -> Project:
        def write(c: _Change) -> Project:
            d = c.get(project_path(project_id)) if project_id else None
            if d is None or d.get("id") != project_id:
                raise ProjectError("not_found", "there is no such project")
            p = _project(d)
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
            after = {**d, **changes}
            if after["lifecycle"] != "archived":
                self._taken(c, after["owner_id"], after["spoken_key"], but=project_id)
            d.update(changes)
            action = ("renamed" if name is not None else "lifecycle_changed" if lifecycle is not None
                      else "summary_changed")
            c.event(project_path(project_id), "project", project_id, action, changes["revision"], actor,
                    {k: v for k, v in changes.items() if k in ("display_name", "lifecycle")})
            return _project(d)
        return self._write(write, actor)

    @staticmethod
    def _bump(c: _Change, project_id: str) -> int:
        d = c.get(project_path(project_id))
        d["revision"] += 1
        d["updated_at"] = time.time()
        c.touch(project_path(project_id))
        return d["revision"]

    # -- aliases -------------------------------------------------------------------
    def add_alias(self, owner: str, source: str, key: str, project_id: str, *,
                  conn: Optional[_Change] = None) -> None:
        if source not in ALIAS_SOURCES:
            raise ProjectError("invalid_request", f"an alias source is one of {', '.join(ALIAS_SOURCES)}")
        alias = {"owner_id": owner, "source": source, "alias_key": spoken_key(key), "added_at": time.time()}

        def write(c: _Change) -> None:
            c.scan(PROJECTS, ["project"])
            held = [d for p, d in c.docs.items() if p.startswith(PROJECTS + "/")]
            for d in held:
                if any((a["owner_id"], a["source"], a["alias_key"])
                       == (owner, source, alias["alias_key"]) for a in d.get("aliases") or []):
                    return                      # one project per (owner, source, key): kept as it is
            d = c.get(project_path(project_id))
            if d is None:
                raise ProjectError("not_found", "there is no such project")
            d.setdefault("aliases", []).append(alias)
            c.touch(project_path(project_id))
        self._run(conn, write, "page")

    def aliases(self, project_id: str) -> list[dict]:
        rec = records.get(project_path(project_id)) if project_id else None
        found = (rec.data.get("aliases") or []) if rec is not None else []
        return sorted(({"source": a["source"], "alias_key": a["alias_key"]} for a in found),
                      key=lambda a: (a["source"], a["alias_key"]))

    # -- folders ------------------------------------------------------------------
    def folders_of(self, project_id: str) -> list[ProjectFolder]:
        rec = records.get(project_path(project_id)) if project_id else None
        if rec is None:
            return []
        live = sorted(_live_folders(rec.data), key=lambda f: (-int(f["is_home"]), f["added_at"]))
        return [_pf(project_id, f) for f in live]

    def index_scopes(self) -> dict[str, list[str]]:
        """Every registered folder linked to a project, with the index scope
        of each of its links (what the knowledge engine may read there)."""
        out: dict[str, list[str]] = {}
        for d in self._projects_data():
            for f in _live_folders(d):
                out.setdefault(f["folder_id"], []).append(f["index_scope"])
        return dict(sorted(out.items()))

    def projects_of_folder(self, folder_id: str) -> list[Project]:
        return [_project(d) for d in self._projects_data() if d["lifecycle"] != "archived"
                and any(f["folder_id"] == folder_id for f in _live_folders(d))]

    def linked_folder_ids(self) -> set[str]:
        return {f["folder_id"] for d in self._projects_data() for f in _live_folders(d)}

    def link_folder(self, project_id: str, folder_id: str, *, part: str = "source", home: bool = False,
                    index_scope: str = "docs", actor: str = "page", conn: Optional[_Change] = None) -> None:
        if part not in PARTS:
            raise ProjectError("invalid_request", f"a part is one of {', '.join(PARTS)}")
        if index_scope not in INDEX_SCOPES:
            raise ProjectError("invalid_request", f"the index scope is one of {', '.join(INDEX_SCOPES)}")

        def write(c: _Change) -> None:
            d = c.get(project_path(project_id)) if project_id else None
            if d is None or d.get("id") != project_id:
                raise ProjectError("not_found", "there is no such project")
            if any(f["folder_id"] == folder_id for f in _live_folders(d)):
                raise ProjectError("already_answered", "that folder is already in the project")
            if home:
                for f in _live_folders(d):
                    f["is_home"] = 0
            rev = self._bump(c, project_id)
            d.setdefault("folders", []).append(
                {"folder_id": folder_id, "folder": _folder_ref(folder_id), "part": part,
                 "is_home": 1 if home else 0, "index_scope": index_scope, "revision": 1,
                 "added_at": time.time(), "removed_at": None})
            c.event(project_path(project_id), "project", project_id, "folder_linked", rev, actor,
                    {"folder_id": folder_id, "part": part, "home": home})
        self._run(conn, write, actor)

    def change_folder(self, project_id: str, folder_id: str, *, unlink: bool = False,
                      home: Optional[bool] = None, part: Optional[str] = None,
                      index_scope: Optional[str] = None, actor: str = "page") -> None:
        def write(c: _Change) -> None:
            d = c.get(project_path(project_id)) if project_id else None
            live = next((f for f in _live_folders(d) if f["folder_id"] == folder_id), None) if d else None
            if live is None:
                raise ProjectError("not_found", "that folder is not in the project")
            if unlink:
                live["removed_at"] = time.time()
                action, detail = "folder_unlinked", {"folder_id": folder_id}
            else:
                changes, detail = {}, {"folder_id": folder_id}
                if home is not None:
                    changes["is_home"] = 1 if home else 0
                    detail["home"] = home
                if part is not None:
                    if part not in PARTS:
                        raise ProjectError("invalid_request", f"a part is one of {', '.join(PARTS)}")
                    changes["part"] = part
                    detail["part"] = part
                if index_scope is not None:
                    if index_scope not in INDEX_SCOPES:
                        raise ProjectError("invalid_request",
                                           f"the index scope is one of {', '.join(INDEX_SCOPES)}")
                    changes["index_scope"] = index_scope
                    detail["index_scope"] = index_scope
                if not changes:
                    return
                if home:
                    for f in _live_folders(d):
                        f["is_home"] = 0
                live.update(changes)
                live["revision"] += 1
                action = "folder_changed"
            rev = self._bump(c, project_id)
            c.event(project_path(project_id), "project", project_id, action, rev, actor, detail)
        self._write(write, actor)

    # -- candidates ----------------------------------------------------------------
    @staticmethod
    def _candidates_data() -> list[dict]:
        found = [r.data for r in records.scan(CANDIDATES, types=["proposal"])]
        return sorted(found, key=lambda d: (d.get("rowid") is None, d.get("rowid") or 0, d["created_at"]))

    def offer_candidate(self, owner: str, name: str, evidence: dict) -> Optional[Candidate]:
        """Record what JARVIS inferred, as a proposal. A candidate he ignored
        stays ignored until its evidence changes; one he accepted stays."""
        fp = fingerprint(evidence)
        key = spoken_key(name)
        evidence = json.loads(json.dumps(evidence, sort_keys=True))

        def write(c: _Change) -> Candidate:
            now = time.time()
            mine = [d for d in c.scan(CANDIDATES, ["proposal"]) if d["owner_id"] == owner]
            mine.sort(key=lambda d: (d.get("rowid") is None, d.get("rowid") or 0, d["created_at"]))
            held = next((d for d in mine if spoken_key(d["proposed_name"]) == key), None)
            if held is None:
                cid = str(uuid.uuid4())
                d = c.new(candidate_path(cid), {
                    "id": cid, "owner_id": owner, "proposed_name": name, "evidence": evidence,
                    "fingerprint": fp, "state": "proposed", "project_id": None,
                    "created_at": now, "updated_at": now})
                c.event(candidate_path(cid), "candidate", cid, "proposed", 1, "migration", {"name": name})
                return _candidate(d)
            if held["state"] == "accepted" or held["fingerprint"] == fp:
                return _candidate(held)
            held.update(evidence=evidence, fingerprint=fp, state="proposed", updated_at=now)
            c.event(candidate_path(held["id"]), "candidate", held["id"], "evidence_changed", 1,
                    "migration", {})
            return _candidate(held)
        return self._write(write, "migration")

    def candidates(self, owners: Iterable[str], state: Optional[str] = "proposed") -> list[Candidate]:
        owners = set(owners)
        found = [d for d in self._candidates_data()
                 if d["owner_id"] in owners and (not state or d["state"] == state)]
        found.sort(key=lambda d: d["proposed_name"])
        return [_candidate(d) for d in found]

    def candidate(self, candidate_id: str) -> Candidate:
        rec = records.get(candidate_path(candidate_id)) if candidate_id else None
        if rec is None or rec.data.get("id") != candidate_id:
            raise ProjectError("not_found", "there is no such candidate")
        return _candidate(rec.data)

    def answer_candidate(self, candidate_id: str, action: str, *, name: Optional[str] = None,
                         folders: Optional[list[str]] = None, home: Optional[str] = None,
                         into: Optional[str] = None, actor: str = "page") -> dict:
        """accept (a new project with the chosen folders, home and the legacy
        aliases, in one write), merge (into an existing project), ignore or
        restore."""
        def write(c: _Change) -> dict:
            path = candidate_path(candidate_id)
            d = c.get(path) if candidate_id else None
            if d is None or d.get("id") != candidate_id:
                raise ProjectError("not_found", "there is no such candidate")
            cand = _candidate(d)
            if action == "restore":
                if cand.state != "ignored":
                    raise ProjectError("already_answered", "only an ignored candidate is restored")
                d.update(state="proposed", updated_at=time.time())
                c.event(path, "candidate", candidate_id, "restored", 1, actor, {})
                return {"done": "restore", "candidate": candidate_id}
            if cand.state != "proposed":
                raise ProjectError("already_answered", "that candidate has already been answered")
            if action == "ignore":
                d.update(state="ignored", updated_at=time.time())
                c.event(path, "candidate", candidate_id, "ignored", 1, actor, {})
                return {"done": "ignore", "candidate": candidate_id}
            if action not in ("accept", "merge"):
                raise ProjectError("invalid_request", "the action is accept, merge, ignore or restore")
            offered = [f["folder_id"] for f in cand.evidence.get("folders") or []]
            chosen = offered if folders is None else [f for f in folders if f in offered]
            if home is not None and home not in chosen:
                raise ProjectError("invalid_request", "the home folder must be one of the project's folders")
            if action == "accept":
                pid = self.create(cand.owner_id, name or cand.proposed_name, actor=actor, conn=c).id
            else:
                target = c.get(project_path(into)) if into else None
                if target is None or target.get("id") != into:
                    raise ProjectError("not_found", "there is no such project to merge into")
                pid = target["id"]
            live = {f["folder_id"] for f in _live_folders(c.get(project_path(pid)))}
            home_id = home or (chosen[0] if chosen and action == "accept" else None)
            for fid in chosen:
                if fid not in live:
                    part = next((f.get("part") for f in cand.evidence.get("folders") or []
                                 if f["folder_id"] == fid), None) or "source"
                    self.link_folder(pid, fid, part=part if part in PARTS else "source",
                                     home=(fid == home_id), actor=actor, conn=c)
            for legacy in cand.evidence.get("legacy") or []:
                if legacy.get("source") in ALIAS_SOURCES:
                    self.add_alias(cand.owner_id, legacy["source"], legacy["key"], pid, conn=c)
            d.update(state="accepted", project_id=pid, updated_at=time.time())
            c.event(path, "candidate", candidate_id, "accepted", 1, actor, {"project_id": pid})
            return {"done": action, "candidate": candidate_id, "project_id": pid}
        return self._write(write, actor)

    # -- works ---------------------------------------------------------------------
    def work(self, work_id: str) -> Work:
        rec = records.get(work_path(work_id)) if work_id else None
        if rec is None or rec.data.get("id") != work_id:
            raise ProjectError("not_found", "there is no such piece of work")
        return _work(rec.data)

    def works(self, owners: Iterable[str], *, state: Optional[str] = "open",
              project_id: Optional[str] = None) -> list[Work]:
        owners = set(owners)
        found = [d for d in self._works_data() if d["owner_id"] in owners
                 and (not state or d["state"] == state)
                 and (not project_id or any(p["project_id"] == project_id for p in d.get("projects") or []))]
        found.sort(key=lambda d: d["updated_at"], reverse=True)
        return [_work(d) for d in found]

    @staticmethod
    def _need_project(c: _Change, project_id: str) -> None:
        d = c.get(project_path(project_id)) if project_id else None
        if d is None or d.get("id") != project_id:
            raise ProjectError("not_found", "there is no such project")

    def start_work(self, owner: str, title: str, *, projects: list[str], outcome: str = "",
                   folders: Iterable[str] = (), actor: str = "page") -> Work:
        title = _name(title, "a piece of work's title", TITLE_MAX)
        if not projects:
            raise ProjectError("invalid_request", "a piece of work belongs to at least one project")
        folders = list(folders)

        def write(c: _Change) -> Work:
            wid, now = str(uuid.uuid4()), time.time()
            for pid in projects:
                self._need_project(c, pid)
            d = c.new(work_path(wid), {
                "id": wid, "owner_id": owner, "title": title, "outcome": outcome, "state": "open",
                "origin": "local", "revision": 1, "created_at": now, "updated_at": now, "closed_at": None,
                "projects": [{"project_id": pid, "role": "primary" if i == 0 else "involved",
                              "added_at": now} for i, pid in enumerate(dict.fromkeys(projects))],
                "folders": [{"folder_id": fid, "folder": _folder_ref(fid), "added_at": now}
                            for fid in dict.fromkeys(folders)],
                "bindings": []})
            c.event(work_path(wid), "work", wid, "started", 1, actor,
                    {"title": title, "projects": list(projects)})
            return _work(d)
        return self._write(write, actor)

    def change_work(self, work_id: str, *, title: Optional[str] = None, outcome: Optional[str] = None,
                    add_project: Optional[str] = None, remove_project: Optional[str] = None,
                    add_folder: Optional[str] = None, remove_folder: Optional[str] = None,
                    state: Optional[str] = None, actor: str = "page") -> Work:
        def write(c: _Change) -> Work:
            now = time.time()
            d = c.get(work_path(work_id)) if work_id else None
            if d is None or d.get("id") != work_id:
                raise ProjectError("not_found", "there is no such piece of work")
            detail: dict = {}
            if title is not None:
                d["title"] = detail["title"] = _name(title, "a piece of work's title", TITLE_MAX)
            if outcome is not None:
                d["outcome"] = outcome
            if state is not None:
                if state not in WORK_STATES:
                    raise ProjectError("invalid_request", f"a work's state is one of {', '.join(WORK_STATES)}")
                d["state"], d["closed_at"] = state, now if state != "open" else None
                detail["state"] = state
            if add_project:
                self._need_project(c, add_project)
                if not any(p["project_id"] == add_project for p in d["projects"]):
                    d["projects"].append({"project_id": add_project, "role": "involved", "added_at": now})
                detail["added_project"] = add_project
            if remove_project:
                primary = next((p["project_id"] for p in d["projects"] if p["role"] == "primary"), None)
                if primary == remove_project:
                    raise ProjectError("not_allowed", "the primary project stays; close the work instead")
                d["projects"] = [p for p in d["projects"] if p["project_id"] != remove_project]
                detail["removed_project"] = remove_project
            if add_folder:
                if not any(f["folder_id"] == add_folder for f in d["folders"]):
                    d["folders"].append({"folder_id": add_folder, "folder": _folder_ref(add_folder),
                                         "added_at": now})
                detail["added_folder"] = add_folder
            if remove_folder:
                d["folders"] = [f for f in d["folders"] if f["folder_id"] != remove_folder]
                detail["removed_folder"] = remove_folder
            d["revision"] += 1
            d["updated_at"] = now
            c.event(work_path(work_id), "work", work_id, "changed", d["revision"], actor, detail)
            return _work(d)
        return self._write(write, actor)

    # -- accomplishments --------------------------------------------------------------
    def accomplishments(self, project_id: str, *, states: Iterable[str] = ("confirmed",),
                        limit: int = 50) -> list[Accomplishment]:
        states = set(states)
        rec = records.get(project_path(project_id)) if project_id else None
        found = [a for a in ((rec.data.get("accomplishments") or []) if rec else []) if a["state"] in states]
        found.sort(key=lambda a: a["achieved_at"], reverse=True)
        return [_acc(project_id, a) for a in found[:max(0, int(limit))]]

    def propose_accomplishment(self, project_id: str, title: str, *, detail: str = "",
                               proposed_by: str = "page", work_id: Optional[str] = None,
                               evidence: Iterable[str] = (), achieved_at: Optional[float] = None,
                               confirm: bool = False, actor: str = "page") -> Accomplishment:
        title = _name(title, "an accomplishment", TITLE_MAX)
        if proposed_by not in PROPOSED_BY:
            raise ProjectError("invalid_request", f"proposed_by is one of {', '.join(PROPOSED_BY)}")
        evidence = list(evidence)

        def write(c: _Change) -> Accomplishment:
            aid, now = str(uuid.uuid4()), time.time()
            self._need_project(c, project_id)
            if work_id:
                w = c.get(work_path(work_id))
                if w is None or w.get("id") != work_id:
                    raise ProjectError("not_found", "there is no such piece of work")
            d = c.get(project_path(project_id))
            state = "confirmed" if confirm else "proposed"
            a = {"id": aid, "work_id": work_id, "title": title, "detail": detail, "state": state,
                 "proposed_by": proposed_by, "achieved_at": achieved_at or now,
                 "confirmed_at": now if confirm else None, "revision": 1, "created_at": now,
                 "evidence": [{"ref": str(ref)} for ref in dict.fromkeys(str(r) for r in evidence)]}
            d.setdefault("accomplishments", []).append(a)
            c.event(project_path(project_id), "accomplishment", aid, "recorded" if confirm else "proposed",
                    1, actor, {"project_id": project_id, "title": title})
            return _acc(project_id, a)
        return self._write(write, actor)

    def accomplishment(self, accomplishment_id: str) -> Optional[Accomplishment]:
        for d in self._projects_data():
            for a in d.get("accomplishments") or []:
                if a["id"] == accomplishment_id:
                    return _acc(d["id"], a)
        return None

    def answer_accomplishment(self, accomplishment_id: str, action: str, *, actor: str = "page") -> Accomplishment:
        if action not in ("confirm", "withdraw"):
            raise ProjectError("invalid_request", "the action is confirm or withdraw")

        def write(c: _Change) -> Accomplishment:
            for d in c.scan(PROJECTS, ["project"]):
                a = next((a for a in d.get("accomplishments") or [] if a["id"] == accomplishment_id), None)
                if a is None:
                    continue
                state = "confirmed" if action == "confirm" else "withdrawn"
                if a["state"] == state:
                    raise ProjectError("already_answered", "that accomplishment is already answered that way")
                a.update(state=state, confirmed_at=time.time() if action == "confirm" else a["confirmed_at"],
                         revision=a["revision"] + 1)
                c.event(project_path(d["id"]), "accomplishment", accomplishment_id, state, a["revision"],
                        actor, {"project_id": d["id"]})
                return _acc(d["id"], a)
            raise ProjectError("not_found", "there is no such accomplishment")
        return self._write(write, actor)

    # -- bindings --------------------------------------------------------------------
    def bind(self, subject_ref: str, *, project_id: Optional[str] = None, work_id: Optional[str] = None,
             folder_id: Optional[str] = None, how: str = "started_here", actor: str = "voice") -> None:
        if how not in ("started_here", "owner", "adopted"):
            raise ProjectError("invalid_request", "how is started_here, owner or adopted")
        if not project_id and not work_id:
            raise ProjectError("invalid_request", "a binding names a project or a piece of work")

        def write(c: _Change) -> None:
            path = project_path(project_id) if project_id else work_path(work_id)
            d = c.get(path)
            if d is None:
                raise ProjectError("not_found", "there is no such project" if project_id
                                   else "there is no such piece of work")
            if work_id and project_id:
                w = c.get(work_path(work_id))
                if w is None:
                    raise ProjectError("not_found", "there is no such piece of work")
            live = any(b["subject"] == subject_ref and b.get("project_id") == project_id
                       and b.get("work_id") == work_id and b.get("unbound_at") is None
                       for b in d.get("bindings") or [])
            if not live:
                d.setdefault("bindings", []).append(
                    {"subject": subject_ref, "project_id": project_id, "work_id": work_id,
                     "folder_id": folder_id, "how": how, "bound_at": time.time(), "unbound_at": None})
                c.touch(path)
            if project_id:
                rev = self._bump(c, project_id)
                c.event(path, "project", project_id, "bound", rev, actor, {"subject": subject_ref, "how": how})
        self._write(write, actor)

    def binding(self, subject_ref: str) -> Optional[dict]:
        found = [b for d in self._projects_data() + self._works_data() for b in d.get("bindings") or []
                 if b["subject"] == subject_ref and b.get("unbound_at") is None]
        if not found:
            return None
        b = max(found, key=lambda b: b["bound_at"])
        return {k: b.get(k) for k in ("subject", "project_id", "work_id", "folder_id", "how", "bound_at",
                                      "unbound_at")}


# -- the move: these documents and the tables they replaced -------------------------

from jarvis import catalog_rows as _rows  # noqa: E402

TABLES = {
    "projects": ("id", "owner_id", "node_id", "display_name", "spoken_key", "notes_slug", "summary",
                 "lifecycle", "revision", "created_at", "updated_at", "archived_at"),
    "project_aliases": ("owner_id", "source", "alias_key", "project_id", "added_at"),
    "project_folders": ("project_id", "folder_id", "part", "is_home", "index_scope", "revision",
                        "added_at", "removed_at"),
    "project_candidates": ("id", "owner_id", "proposed_name", "evidence", "fingerprint", "state",
                           "project_id", "created_at", "updated_at"),
    "works": ("id", "owner_id", "title", "outcome", "state", "origin", "revision", "created_at",
              "updated_at", "closed_at"),
    "work_projects": ("work_id", "project_id", "role", "added_at"),
    "work_folders": ("work_id", "folder_id", "added_at"),
    "accomplishments": ("id", "project_id", "work_id", "title", "detail", "state", "proposed_by",
                        "achieved_at", "confirmed_at", "revision", "created_at"),
    "accomplishment_evidence": ("accomplishment_id", "ref"),
    "project_bindings": ("subject", "project_id", "work_id", "folder_id", "how", "bound_at", "unbound_at"),
    "project_events": ("seq", "at", "subject", "action", "revision", "actor", "detail"),
}
REAL = {"created_at", "updated_at", "archived_at", "added_at", "removed_at", "closed_at", "achieved_at",
        "confirmed_at", "bound_at", "unbound_at", "at"}
INTEGER = {"revision", "is_home", "seq"}
ALIASED = {"project_events"}            # INTEGER PRIMARY KEY: the seq is the rowid


def _with_rowid(name: str) -> tuple[str, ...]:
    return ("rowid",) + TABLES[name]


def _orphan_event(kept: dict) -> dict:
    """An event kept whole by the move (its record was not there), as
    `events` gives one."""
    from jarvis.knowledge_move import uncell
    cells = {c: uncell(v) for c, v in kept["cells"].items()}
    detail = _rows.json_or_text(cells.get("detail"))
    return {**cells, "detail": detail if isinstance(detail, dict) else {}}


def _sorted_json(text: str) -> Optional[dict]:
    """The JSON object `text` holds, when writing it again (sorted keys) gives
    `text` exactly; else None."""
    value = _rows.json_or_text(text)
    return value if isinstance(value, dict) and json.dumps(value, sort_keys=True) == text else None


class ProjectMapping:
    """The project records' tables to `project`, `proposal` and `work`
    documents and back; `project_events` become each record's versions. A
    row that cannot be part of a document as it is is kept whole in the
    sequence document."""

    name = "project"
    tables = tuple(TABLES)
    under = (PROJECTS, CANDIDATES, WORKS, "/settings")
    select = {"/settings": {"where": [["area", "=", AREA]]}}
    history = True

    def documents(self, conn):
        from jarvis.knowledge_move import Doc, Revision
        t = {name: _rows.read(conn, name, cols) for name, cols in TABLES.items()}
        orphans: dict[str, list] = {}

        def orphan(table: str, row: dict) -> None:
            orphans.setdefault(table, []).append(_rows.raw(row))

        projects = {r["id"]: {**_rows.plain(r, _with_rowid("projects")), "aliases": [], "folders": [],
                              "accomplishments": [], "bindings": [], "events": []} for r in t["projects"]}
        works = {r["id"]: {**_rows.plain(r, _with_rowid("works")), "projects": [], "folders": [],
                           "bindings": [], "events": []} for r in t["works"]}
        cands: dict[str, dict] = {}
        for r in t["project_candidates"]:
            evidence = _sorted_json(r["evidence"])
            if evidence is None:
                orphan("project_candidates", r)
                continue
            cands[r["id"]] = {**_rows.plain(r, _with_rowid("project_candidates")), "evidence": evidence,
                              "events": []}
        for r in t["project_aliases"]:
            if r["project_id"] in projects:
                projects[r["project_id"]]["aliases"].append(
                    _rows.plain(r, _with_rowid("project_aliases"), drop=("project_id",)))
            else:
                orphan("project_aliases", r)
        for r in t["project_folders"]:
            if r["project_id"] in projects:
                projects[r["project_id"]]["folders"].append(
                    {**_rows.plain(r, _with_rowid("project_folders"), drop=("project_id",)),
                     "folder": _folder_ref(r["folder_id"])})
            else:
                orphan("project_folders", r)
        accs: dict[str, dict] = {}
        owner_of: dict[str, str] = {}
        for r in t["accomplishments"]:
            if r["project_id"] in projects:
                a = {**_rows.plain(r, _with_rowid("accomplishments"), drop=("project_id",)), "evidence": []}
                projects[r["project_id"]]["accomplishments"].append(a)
                accs[r["id"]] = a
                owner_of[r["id"]] = r["project_id"]
            else:
                orphan("accomplishments", r)
        for r in t["accomplishment_evidence"]:
            if r["accomplishment_id"] in accs:
                accs[r["accomplishment_id"]]["evidence"].append(
                    _rows.plain(r, _with_rowid("accomplishment_evidence"), drop=("accomplishment_id",)))
            else:
                orphan("accomplishment_evidence", r)
        for r in t["work_projects"]:
            if r["work_id"] in works:
                works[r["work_id"]]["projects"].append(
                    _rows.plain(r, _with_rowid("work_projects"), drop=("work_id",)))
            else:
                orphan("work_projects", r)
        for r in t["work_folders"]:
            if r["work_id"] in works:
                works[r["work_id"]]["folders"].append(
                    {**_rows.plain(r, _with_rowid("work_folders"), drop=("work_id",)),
                     "folder": _folder_ref(r["folder_id"])})
            else:
                orphan("work_folders", r)
        for r in t["project_bindings"]:
            b = _rows.plain(r, _with_rowid("project_bindings"))
            if r["project_id"] is not None and r["project_id"] in projects:
                projects[r["project_id"]]["bindings"].append(b)
            elif r["project_id"] is None and r["work_id"] in works:
                works[r["work_id"]]["bindings"].append(b)
            else:
                orphan("project_bindings", r)
        events: dict[str, list] = {}
        for r in t["project_events"]:
            detail = _sorted_json(r["detail"])
            holder = None
            try:
                ref = workspace_ref.parse(r["subject"])
                kind, rid = ref.kind.value, ref.id
                holder = (project_path(rid) if kind == "project" and rid in projects
                          else candidate_path(rid) if kind == "candidate" and rid in cands
                          else work_path(rid) if kind == "work" and rid in works
                          else project_path(owner_of[rid]) if kind == "accomplishment" and rid in owner_of
                          else None)
            except workspace_ref.RefError:
                holder = None
            if holder is None or detail is None or workspace_ref.format(ref) != r["subject"]:
                orphan("project_events", r)
                continue
            events.setdefault(holder, []).append({**_rows.plain(r, TABLES["project_events"]), "detail": detail})
        docs = ([(project_path(k), v) for k, v in projects.items()]
                + [(candidate_path(k), v) for k, v in cands.items()]
                + [(work_path(k), v) for k, v in works.items()])
        for path, data in docs:
            history = tuple(Revision(dict(data, events=[e]), by=_caller(e["actor"]) if e["actor"] in ACTORS
                                     else records.JARVIS, change=e["action"])
                            for e in sorted(events.get(path, []), key=lambda e: e["seq"]))
            yield Doc(path, data, history=history)
        seq = _rows.sequences(conn)
        yield Doc(SEQUENCE, {"area": AREA, "events": [], "orphans": orphans,
                             "last_seq": max([r["seq"] for r in t["project_events"]]
                                             + [seq.get("project_events", 0)])})

    def rows(self, docs) -> dict:
        out = _rows.Tables(TABLES, REAL, INTEGER, ALIASED)
        for doc in docs:
            p, d = doc.path, doc.data
            if p == SEQUENCE:
                for table, kept in (d.get("orphans") or {}).items():
                    for one in kept:
                        out.add_raw(table, one)
                continue
            if p.startswith("/settings/"):
                continue
            if p.startswith(PROJECTS + "/"):
                out.add("projects", d)
                for a in d.get("aliases") or []:
                    out.add("project_aliases", {**a, "project_id": d["id"]})
                for f in d.get("folders") or []:
                    out.add("project_folders", {**f, "project_id": d["id"]})
                for a in d.get("accomplishments") or []:
                    out.add("accomplishments", {**a, "project_id": d["id"]})
                    for e in a.get("evidence") or []:
                        out.add("accomplishment_evidence", {**e, "accomplishment_id": a["id"]})
            elif p.startswith(CANDIDATES + "/"):
                out.add("project_candidates", {**d, "evidence": json.dumps(d.get("evidence") or {},
                                                                           sort_keys=True)})
            elif p.startswith(WORKS + "/"):
                out.add("works", d)
                for wp in d.get("projects") or []:
                    out.add("work_projects", {**wp, "work_id": d["id"]})
                for f in d.get("folders") or []:
                    out.add("work_folders", {**f, "work_id": d["id"]})
            else:
                continue
            for b in d.get("bindings") or []:
                out.add("project_bindings", b)
            for v in list(doc.history) + [doc]:
                for e in v.data.get("events") or []:
                    out.add("project_events", {**e, "detail": json.dumps(e.get("detail") or {}, sort_keys=True)})
        return out.rows()


MAPPINGS = [ProjectMapping()]
