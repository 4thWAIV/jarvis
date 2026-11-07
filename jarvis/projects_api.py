"""The page's project routes, /api/project-records and /api/works. (The
older /api/projects lists the projects root and the dashboard's view; it is
left as it is, so no existing caller changes, as decided for the tool
names.) Thin: each runs
one ProjectService method (the same the brain's project tools run) off the
event loop and answers its JSON, or its refusal as `{"error": {"code",
"message"}}` with the code's status. A change needs the page's origin or the
tool token (web_auth) and is followed by a `projects` hint on /ws/sessions
(`on_change`), never before its commit.

  GET    /api/project-records                         his projects (?archived=1)
  POST   /api/project-records                         add {name, summary}
  GET    /api/project-records/candidates              proposals (?state=)
  POST   /api/project-records/candidates/refresh      offer what folders and legacy names suggest
  POST   /api/project-records/candidates/{id}         {action: accept|merge|ignore|restore, name, folders, home, into}
  GET    /api/project-records/{id}                    one project in full
  PATCH  /api/project-records/{id}                    {action: rename|summary|lifecycle|archive, ...}
  GET    /api/project-records/{id}/history            its events, newest first (?before=&limit=)
  POST   /api/project-records/{id}/folders/{folder}   {action: link|unlink|home|part|scope, part, index_scope}
  POST   /api/project-records/{id}/accomplishments    {action: record|propose|confirm|withdraw, ...}
  GET    /api/works                            pieces of work (?state=&project=)
  POST   /api/works                            start {title, outcome, projects, folder_id}
  GET    /api/works/{id}                       one piece of work
  PATCH  /api/works/{id}                       {action: rename|outcome|add_project|..., ...}
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from jarvis.project_service import ProjectService
from jarvis.project_store import ProjectError


def mount(app: FastAPI, service: Callable[[], Optional[ProjectService]],
          on_change: Callable[[], None] = lambda: None) -> None:

    async def run(work, changes: bool = False):
        svc = service()
        try:
            if svc is None:
                raise ProjectError("not_allowed", "project records are switched off with the folder catalog")
            result = await asyncio.to_thread(work, svc)
        except ProjectError as e:
            return JSONResponse(e.to_json(), status_code=e.status)
        if changes:
            on_change()
        return JSONResponse(result)

    async def body(request: Request, allowed: set[str]) -> Optional[dict]:
        try:
            b = await request.json()
        except ValueError:
            return None
        return b if isinstance(b, dict) and not set(b) - allowed else None

    def bad():
        return JSONResponse(ProjectError("invalid_request", "that request is not one these routes take").to_json(),
                            status_code=400)

    async def list_projects(archived: int = 0):
        return await run(lambda s: s.list_projects(include_archived=bool(archived)))

    async def add(request: Request):
        b = await body(request, {"name", "summary"})
        if b is None:
            return bad()
        return await run(lambda s: s.change_project("add", name=b.get("name"), summary=b.get("summary")),
                         changes=True)

    async def candidates(state: Optional[str] = "proposed"):
        return await run(lambda s: s.candidates(state or None))

    async def refresh():
        return await run(lambda s: {"proposed": s.refresh_candidates()}, changes=True)

    async def answer_candidate(candidate_id: str, request: Request):
        b = await body(request, {"action", "name", "folders", "home", "into"})
        if b is None:
            return bad()
        return await run(lambda s: s.change_candidate(candidate_id, str(b.get("action") or ""),
                                                      name=b.get("name"), folders=b.get("folders"),
                                                      home=b.get("home"), into=b.get("into")), changes=True)

    async def detail(project_id: str):
        return await run(lambda s: s.detail(project_id))

    async def change(project_id: str, request: Request):
        b = await body(request, {"action", "name", "summary", "lifecycle", "expected_revision"})
        if b is None:
            return bad()
        return await run(lambda s: s.change_project(str(b.get("action") or ""), project=project_id,
                                                    name=b.get("name"), summary=b.get("summary"),
                                                    lifecycle=b.get("lifecycle"),
                                                    expected_revision=b.get("expected_revision")), changes=True)

    async def history(project_id: str, before: Optional[int] = None, limit: int = 20):
        return await run(lambda s: s.history(project_id, before=before, limit=limit))

    async def folder(project_id: str, folder_id: str, request: Request):
        b = await body(request, {"action", "part", "index_scope"})
        if b is None:
            return bad()
        return await run(lambda s: s.change_project_folder(str(b.get("action") or ""), project=project_id,
                                                           folder_id=folder_id, part=b.get("part"),
                                                           index_scope=b.get("index_scope")), changes=True)

    async def accomplishments(project_id: str, request: Request):
        b = await body(request, {"action", "accomplishment", "title", "detail", "work", "evidence"})
        if b is None:
            return bad()
        return await run(lambda s: s.change_accomplishment(
            str(b.get("action") or ""), project=project_id, accomplishment=b.get("accomplishment"),
            title=b.get("title"), detail=b.get("detail") or "", work=b.get("work"),
            evidence=b.get("evidence") or []), changes=True)

    async def works(state: Optional[str] = "open", project: Optional[str] = None):
        return await run(lambda s: s.works(state or None, project))

    async def start_work(request: Request):
        b = await body(request, {"title", "outcome", "projects", "folder_id"})
        if b is None:
            return bad()
        return await run(lambda s: s.change_work("start", title=b.get("title"), outcome=b.get("outcome"),
                                                 projects=b.get("projects") or [], folder_id=b.get("folder_id")),
                         changes=True)

    async def work_detail(work_id: str):
        return await run(lambda s: s.work_detail(work_id))

    async def change_work(work_id: str, request: Request):
        b = await body(request, {"action", "title", "outcome", "project", "folder_id"})
        if b is None:
            return bad()
        return await run(lambda s: s.change_work(str(b.get("action") or ""), work=work_id, title=b.get("title"),
                                                 outcome=b.get("outcome"), project=b.get("project"),
                                                 folder_id=b.get("folder_id")), changes=True)

    routes = [
        ("/api/project-records", list_projects, "GET"), ("/api/project-records", add, "POST"),
        ("/api/project-records/candidates", candidates, "GET"),
        ("/api/project-records/candidates/refresh", refresh, "POST"),
        ("/api/project-records/candidates/{candidate_id}", answer_candidate, "POST"),
        ("/api/project-records/{project_id}", detail, "GET"), ("/api/project-records/{project_id}", change, "PATCH"),
        ("/api/project-records/{project_id}/history", history, "GET"),
        ("/api/project-records/{project_id}/folders/{folder_id}", folder, "POST"),
        ("/api/project-records/{project_id}/accomplishments", accomplishments, "POST"),
        ("/api/works", works, "GET"), ("/api/works", start_work, "POST"),
        ("/api/works/{work_id}", work_detail, "GET"), ("/api/works/{work_id}", change_work, "PATCH"),
    ]
    for path, fn, method in routes:
        app.add_api_route(path, fn, methods=[method])
