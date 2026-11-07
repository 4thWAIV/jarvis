"""The one service over projects, pieces of work and accomplishments (design
section 5.5): the page's /api/projects and /api/works routes (projects_api.py)
and the brain's project tools call these same methods, so page and voice do
the same thing.

The owner in force is the signed-in member, else `local`; reads show his
rows and the `local` rows nobody has claimed (design 5.6). Each method
returns JSON-ready dicts; a refusal is a `ProjectError` with a code. Folders
are named by the folder catalog's IDs and shown with the catalog's own
facts (name, availability), read through the folder service.

Candidates are proposals (I9): `refresh_candidates` offers what the
folders and legacy project strings suggest, and nothing becomes a project
until he accepts one (or makes one himself).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from jarvis import project_seed, run_store
from jarvis.agent_directory import spoken_key
from jarvis.project_store import ProjectError, ProjectStore

RUNS_SHOWN = 10


# The confirmed accomplishments a context pack names per project (design 8.1).
BRIEF_DONE = 5


class ProjectService:
    def __init__(self, store: ProjectStore, folders: Callable[[], Any],
                 owner: Callable[[], Optional[str]] = lambda: None,
                 live_sessions: Callable[[], Iterable] = lambda: ()) -> None:
        self.store = store
        self._folders = folders
        self._owner = owner
        self.live_sessions = live_sessions
        self.answered: Callable[[str], None] = lambda candidate_id: None

    # -- who ----------------------------------------------------------------------
    def owner(self) -> str:
        return self._owner() or "local"

    def owners(self) -> list[str]:
        return list(dict.fromkeys([self.owner(), "local"]))

    def folders(self):
        found = self._folders()
        if found is None:
            raise ProjectError("not_allowed", "the folder catalog is switched off")
        return found

    # -- reading -------------------------------------------------------------------
    def _folder_facts(self, folder_id: str) -> dict:
        svc = self._folders()
        f = svc.store.get(folder_id) if svc is not None else None
        if f is None:
            return {"folder_id": folder_id, "name": None, "availability": "missing"}
        return {"folder_id": f.id, "name": f.display_name, "role": f.role,
                "availability": f.location.availability, "path": f.location.canonical_path,
                "local": f.location.node_id == svc.node_id()}

    def _summary_row(self, p) -> dict:
        folders = self.store.folders_of(p.id)
        home = next((f for f in folders if f.is_home), None)
        return {**p.json(), "folders": len(folders),
                "home": self._folder_facts(home.folder_id) if home else None,
                "open_works": len(self.store.works(self.owners(), state="open", project_id=p.id))}

    def list_projects(self, include_archived: bool = False) -> dict:
        return {"projects": [self._summary_row(p) for p in
                             self.store.projects(self.owners(), include_archived=include_archived)],
                "owner": self.owner()}

    def find(self, name: str) -> list[dict]:
        """By spoken key, then by any alias."""
        found = self.store.find_by_key(self.owners(), name)
        if not found:
            for source in ("spoken", "run_project", "session_project", "workflow_project",
                           "message_project", "memory_slug", "projects_root"):
                p = self.store.project_for_legacy(self.owners(), source, name)
                if p is not None:
                    found = [p]
                    break
        return [self._summary_row(p) for p in found]

    def resolve(self, reference: str):
        """A project by ID, else by name or alias; refused when none or more
        than one."""
        p = self.store.get(str(reference or ""))
        if p is not None:
            return p
        found = self.find(str(reference or ""))
        if len(found) == 1:
            return self.store.require(found[0]["id"])
        if not found:
            raise ProjectError("not_found", "no project goes by that name")
        raise ProjectError("invalid_request", "more than one project goes by that name; give its ID")

    def agents_in(self, folder_paths: list[str]) -> list[dict]:
        out = []
        for s in self.live_sessions() or ():
            cwd = getattr(s, "cwd", None) if not isinstance(s, dict) else s.get("cwd")
            name = getattr(s, "name", None) if not isinstance(s, dict) else s.get("name")
            sid = getattr(s, "session_id", None) if not isinstance(s, dict) else s.get("session_id")
            if not cwd:
                continue
            here = Path(cwd)
            if any(here == Path(p) or Path(p) in here.parents for p in folder_paths if p):
                out.append({"session_id": sid, "name": name})
        return out

    def projects_for_path(self, path: str) -> list[dict]:
        """His projects holding a path on this Mac: the registered folder
        holding it (`folder_for_cwd`), then the projects that folder is in.
        Worked out when asked, never stored."""
        svc = self._folders()
        folder = svc.folder_for_cwd(path) if svc is not None and path else None
        if folder is None:
            return []
        owners = set(self.owners())
        return [{"id": p.id, "name": p.display_name} for p in self.store.projects_of_folder(folder.id)
                if p.owner_id in owners]

    def project_holding(self, path: str) -> Optional[str]:
        """The one project holding a path, for a run started there by
        folder or legacy name; None when none or several hold it (several
        are never guessed between)."""
        found = self.projects_for_path(path)
        return found[0]["id"] if len(found) == 1 else None

    def brief(self, *, project: Optional[str] = None, work: Optional[str] = None) -> dict:
        """The records a context pack is made from (design 8.1, tier 0):
        the work's title and outcome, and each of its projects (primary
        first), or the one project: name, summary, notes slug, aliases,
        folders by name and part (no paths) and the last confirmed
        accomplishments. Proposed ones never count."""
        w = self.store.work(work) if work else None
        if w is not None:
            ids = [x["project_id"] for x in sorted(w.projects, key=lambda x: x["role"] != "primary")]
        elif project:
            ids = [self.resolve(project).id]
        else:
            ids = []
        out = []
        for pid in ids:
            p = self.store.require(pid)
            out.append({
                "id": p.id, "name": p.display_name, "summary": p.summary, "notes_slug": p.notes_slug,
                "aliases": sorted({a["alias_key"] for a in self.store.aliases(p.id)}),
                "folders": [{"name": self._folder_facts(f.folder_id)["name"], "part": f.part,
                             "home": f.is_home} for f in self.store.folders_of(p.id)],
                "accomplishments": [a.title for a in self.store.accomplishments(p.id, limit=BRIEF_DONE)]})
        return {"work": {"id": w.id, "title": w.title, "outcome": w.outcome} if w else None, "projects": out}

    def detail(self, reference: str) -> dict:
        p = self.resolve(reference)
        folders = [{**self._folder_facts(f.folder_id), "part": f.part, "is_home": f.is_home,
                    "index_scope": f.index_scope} for f in self.store.folders_of(p.id)]
        works = [w.json() for w in self.store.works(self.owners(), state="open", project_id=p.id)]
        accomplishments = [a.json() for a in self.store.accomplishments(
            p.id, states=("confirmed", "proposed"), limit=20)]
        runs = [{"id": r["id"], "status": r["status"], "created_at": r["created_at"]}
                for r in self._runs(p)]
        from jarvis import context_service
        topics = [{"slug": t.slug, "name": t.name, "status": t.status}
                  for t in context_service.touched_topics([p.display_name, p.summary])]
        return {"project": p.json(), "folders": folders, "works": works,
                "accomplishments": accomplishments, "runs": runs, "topics": topics,
                "aliases": self.store.aliases(p.id),
                "agents": self.agents_in([f.get("path") for f in folders])}

    def _runs(self, p) -> list[dict]:
        """Its runs: by the run's own project_id, and by the legacy name its
        aliases or spoken key match."""
        keys = {p.spoken_key} | {a["alias_key"] for a in self.store.aliases(p.id) if a["source"] == "run_project"}
        out = []
        for r in run_store.list_runs(limit=200):
            if r.get("project_id") == p.id or spoken_key(r.get("project_name") or "") in keys:
                out.append(r)
            if len(out) >= RUNS_SHOWN:
                break
        return out

    def history(self, reference: str, before: Optional[int] = None, limit: int = 20) -> dict:
        p = self.resolve(reference)
        events = self.store.events(record=("project", p.id), before=before, limit=limit, newest_first=True)
        return {"project_id": p.id, "events": events,
                "before": events[-1]["seq"] if len(events) == limit else None}

    def candidates(self, state: Optional[str] = "proposed") -> dict:
        return {"candidates": [c.json() for c in self.store.candidates(self.owners(), state)]}

    def refresh_candidates(self) -> list[dict]:
        """Offer what the folders and legacy strings suggest; the ones now
        open come back."""
        return [c.json() for c in project_seed.propose(self.store, self.folders(), self.owner(), self.owners())]

    def works(self, state: Optional[str] = "open", project: Optional[str] = None) -> dict:
        pid = self.resolve(project).id if project else None
        return {"works": [w.json() for w in self.store.works(self.owners(), state=state, project_id=pid)]}

    def work_detail(self, work_id: str) -> dict:
        w = self.store.work(work_id)
        return {"work": w.json(),
                "projects": [{**self.store.require(x["project_id"]).json(), "role": x["role"]} for x in w.projects],
                "folders": [self._folder_facts(f) for f in w.folders]}

    # -- changing ------------------------------------------------------------------
    def change_project(self, action: str, *, project: Optional[str] = None, name: Optional[str] = None,
                       summary: Optional[str] = None, lifecycle: Optional[str] = None,
                       expected_revision: Optional[int] = None, actor: str = "page") -> dict:
        if action == "add":
            return {"done": "add", "project": self.store.create(self.owner(), name or "", summary=summary or "",
                                                                actor=actor).json()}
        p = self.resolve(project or "")
        if action == "rename":
            changed = self.store.change(p.id, name=name, expected_revision=expected_revision, actor=actor)
        elif action == "summary":
            changed = self.store.change(p.id, summary=summary or "", expected_revision=expected_revision, actor=actor)
        elif action == "lifecycle":
            changed = self.store.change(p.id, lifecycle=lifecycle, expected_revision=expected_revision, actor=actor)
        elif action == "archive":
            changed = self.store.change(p.id, lifecycle="archived", expected_revision=expected_revision, actor=actor)
        else:
            raise ProjectError("invalid_request", "the action is add, rename, summary, lifecycle or archive")
        return {"done": action, "project": changed.json()}

    def change_project_folder(self, action: str, *, project: str, folder_id: str, part: Optional[str] = None,
                              index_scope: Optional[str] = None, actor: str = "page") -> dict:
        p = self.resolve(project)
        if action == "link":
            svc = self.folders()
            f = svc.store.get(folder_id)
            if f is None or f.lifecycle != "active":
                raise ProjectError("not_found", "there is no such registered folder")
            if f.scope.reader_kind != "files" and (part or "source") != "reference":
                raise ProjectError("not_allowed", "a history or notes folder joins a project only as a reference")
            self.store.link_folder(p.id, folder_id, part=part or "source",
                                   home=not self.store.folders_of(p.id), index_scope=index_scope or "docs",
                                   actor=actor)
        elif action == "unlink":
            self.store.change_folder(p.id, folder_id, unlink=True, actor=actor)
        elif action == "home":
            self.store.change_folder(p.id, folder_id, home=True, actor=actor)
        elif action == "part":
            self.store.change_folder(p.id, folder_id, part=part, actor=actor)
        elif action == "scope":
            self.store.change_folder(p.id, folder_id, index_scope=index_scope, actor=actor)
        else:
            raise ProjectError("invalid_request", "the action is link, unlink, home, part or scope")
        return {"done": action, "project_id": p.id, "folder_id": folder_id}

    def change_candidate(self, candidate_id: str, action: str, *, name: Optional[str] = None,
                         folders: Optional[list[str]] = None, home: Optional[str] = None,
                         into: Optional[str] = None, actor: str = "page") -> dict:
        target = self.resolve(into).id if into else None
        done = self.store.answer_candidate(candidate_id, action, name=name, folders=folders, home=home,
                                           into=target, actor=actor)
        if action != "restore":
            self.answered(candidate_id)
        return done

    def change_work(self, action: str, *, work: Optional[str] = None, title: Optional[str] = None,
                    outcome: Optional[str] = None, projects: Optional[list[str]] = None,
                    project: Optional[str] = None, folder_id: Optional[str] = None, actor: str = "page") -> dict:
        if action == "start":
            ids = [self.resolve(x).id for x in projects or []]
            w = self.store.start_work(self.owner(), title or "", projects=ids, outcome=outcome or "",
                                      folders=[folder_id] if folder_id else [], actor=actor)
            return {"done": "start", "work": w.json()}
        if not work:
            raise ProjectError("invalid_request", "which piece of work?")
        kw: dict = {}
        if action == "rename":
            kw["title"] = title
        elif action == "outcome":
            kw["outcome"] = outcome or ""
        elif action == "add_project":
            kw["add_project"] = self.resolve(project or "").id
        elif action == "remove_project":
            kw["remove_project"] = self.resolve(project or "").id
        elif action == "add_folder":
            kw["add_folder"] = folder_id
        elif action == "remove_folder":
            kw["remove_folder"] = folder_id
        elif action in ("close", "drop", "reopen"):
            kw["state"] = {"close": "done", "drop": "dropped", "reopen": "open"}[action]
        else:
            raise ProjectError("invalid_request", "the action is start, rename, outcome, add_project, "
                                                  "remove_project, add_folder, remove_folder, close, drop or reopen")
        return {"done": action, "work": self.store.change_work(work, actor=actor, **kw).json()}

    def change_accomplishment(self, action: str, *, project: Optional[str] = None,
                              accomplishment: Optional[str] = None, title: Optional[str] = None,
                              detail: str = "", work: Optional[str] = None,
                              evidence: Iterable[str] = (), actor: str = "page") -> dict:
        if action in ("record", "propose"):
            p = self.resolve(project or "")
            a = self.store.propose_accomplishment(p.id, title or "", detail=detail,
                                                  proposed_by="voice" if actor == "voice" else "page",
                                                  work_id=work, evidence=evidence,
                                                  confirm=action == "record", actor=actor)
        elif action in ("confirm", "withdraw"):
            a = self.store.answer_accomplishment(str(accomplishment or ""), action, actor=actor)
        else:
            raise ProjectError("invalid_request", "the action is record, propose, confirm or withdraw")
        return {"done": action, "accomplishment": a.json()}

    # -- where work may run -------------------------------------------------------------
    def run_location(self, *, project: Optional[str] = None, work: Optional[str] = None,
                     folder_id: Optional[str] = None) -> tuple[dict, Any, Path]:
        """Where a run for this project or work may start, under the folder
        service's checks: the named folder when it belongs to the work's
        projects or folders, else the project's home folder. Returns the
        project row (or None), the folder and its root."""
        svc = self.folders()
        p = self.resolve(project) if project else None
        allowed: Optional[set[str]] = None
        if work:
            w = self.store.work(work)
            allowed = set(w.folders)
            for x in w.projects:
                allowed |= {f.folder_id for f in self.store.folders_of(x["project_id"])}
            if p is None:
                primary = next((x for x in w.projects if x["role"] == "primary"), None)
                p = self.store.require(primary["project_id"]) if primary else None
        elif p is not None:
            allowed = {f.folder_id for f in self.store.folders_of(p.id)}
        if folder_id is None:
            if p is None:
                raise ProjectError("invalid_request", "a project, a work or a folder is needed")
            home = next((f for f in self.store.folders_of(p.id) if f.is_home), None)
            if home is None:
                raise ProjectError("not_found", "that project has no home folder to start in")
            folder_id = home.folder_id
        elif allowed is not None and folder_id not in allowed:
            raise ProjectError("not_allowed", "that folder is not one of the work's projects or folders")
        f, root = svc.work_location(folder_id)
        return (p.json() if p else None), f, root
