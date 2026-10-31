"""The page's Folders routes, /api/folders. Thin: each takes its request,
runs the folder service (folder_service.py) off the event loop and returns
its answer, or its refusal as `{"error": {"code", "message", ...}}` with
the status folder_contract.ERRORS gives the code.

They sit behind the web boundary like every other route (web_auth.py): a
change (POST, PATCH, DELETE) needs the page's own origin or the tool token.
No access-key category covers them, so no agent's key reaches them. A
change that was committed is followed by a `folders` hint on /ws/sessions
(`on_change`), never before it.

  GET    /api/folders                      the catalog (query, role,
                                           availability, lifecycle, cursor, limit)
  POST   /api/folders                      register (request_key, path,
                                           display_name, role, reader, reactivate)
  GET    /api/folders/resolve              what registering a path would do
  GET    /api/folders/candidates           what discovery found (state, query,
                                           role, cursor, limit)
  POST   /api/folders/candidates/{id}      ignore or restore one (action)
  GET    /api/folders/{id}                 one folder, its sessions and events
  PATCH  /api/folders/{id}                 label and role (expected_revision)
  DELETE /api/folders/{id}                 remove from the catalog; no file is touched
  POST   /api/folders/{id}/refresh         check it now, and discover again soon
  POST   /api/folders/{id}/reconnect       a new location (expected_revision,
                                           path, replace)
  GET    /api/folders/{id}/entries         one page of one directory
  GET    /api/folders/{id}/preview         one window of one file
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from jarvis.folder_contract import FolderError
from jarvis.folder_service import FolderService

PREFIX = "/api/folders"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterBody(_Body):
    request_key: str
    path: str
    display_name: Optional[str] = None
    role: Optional[str] = None
    reader: Optional[str] = None
    reactivate: bool = False


class EditBody(_Body):
    expected_revision: int
    display_name: Optional[str] = None
    role: Optional[str] = None


class ReconnectBody(_Body):
    expected_revision: int
    path: str
    replace: bool = False


class CandidateBody(_Body):
    action: str


def mount(app: FastAPI, service: Callable[[], Optional[FolderService]],
          on_change: Callable[[], None] = lambda: None) -> None:
    """`service` is the running folder service, or None while the catalog
    is switched off (JARVIS_FOLDERS=0): every route then says so, and the
    records stay as they are."""

    async def run(work: Callable[[FolderService], object], changes: bool = False,
                  status: Callable[[object], int] = lambda _r: 200):
        svc = service()
        try:
            if svc is None:
                raise FolderError("switched_off")
            result = await asyncio.to_thread(work, svc)
        except FolderError as e:
            return JSONResponse(e.to_json(), status_code=e.status)
        if changes:
            on_change()
        return JSONResponse(result, status_code=status(result))

    async def body(request: Request, model: type[BaseModel]):
        try:
            return model.model_validate(await request.json())
        except (ValueError, ValidationError):
            raise FolderError("invalid_request") from None

    async def with_body(request: Request, model: type[BaseModel], work, **kw):
        try:
            parsed = await body(request, model)
        except FolderError as e:
            return JSONResponse(e.to_json(), status_code=e.status)
        return await run(lambda svc: work(svc, parsed), **kw)

    async def list_folders(query: str = "", role: Optional[str] = None,
                           availability: Optional[str] = None, lifecycle: str = "active",
                           cursor: Optional[str] = None, limit: int = 100):
        return await run(lambda s: s.list_folders(query=query, role=role,
                                                  availability=availability,
                                                  lifecycle=lifecycle, cursor=cursor, limit=limit))

    async def register(request: Request):
        return await with_body(request, RegisterBody, lambda s, b: s.register(
            request_key=b.request_key, path=b.path, display_name=b.display_name, role=b.role,
            reader=b.reader, reactivate=b.reactivate), changes=True,
            status=lambda r: 201 if r["outcome"] == "created" else 200)

    async def resolve(path: str = ""):
        return await run(lambda s: s.resolve(path))

    async def candidates(state: str = "discovered", query: str = "", role: Optional[str] = None,
                         cursor: Optional[str] = None, limit: int = 100):
        return await run(lambda s: s.candidates(state=state, query=query, role=role,
                                                cursor=cursor, limit=limit))

    async def candidate_action(candidate_id: str, request: Request):
        return await with_body(request, CandidateBody,
                               lambda s, b: s.candidate_action(candidate_id, b.action),
                               changes=True)

    async def detail(folder_id: str):
        return await run(lambda s: s.detail(folder_id))

    async def edit(folder_id: str, request: Request):
        return await with_body(request, EditBody, lambda s, b: s.edit(
            folder_id, expected_revision=b.expected_revision, display_name=b.display_name,
            role=b.role), changes=True)

    async def unregister(folder_id: str, expected_revision: Optional[int] = None):
        return await run(lambda s: s.unregister(folder_id, expected_revision=expected_revision),
                         changes=True)

    async def refresh(folder_id: str):
        return await run(lambda s: s.refresh(folder_id), changes=True)

    async def reconnect(folder_id: str, request: Request):
        return await with_body(request, ReconnectBody, lambda s, b: s.reconnect(
            folder_id, expected_revision=b.expected_revision, path=b.path, replace=b.replace),
            changes=True)

    async def entries(folder_id: str, path: str = "", location_revision: Optional[str] = None,
                      cursor: Optional[str] = None):
        return await run(lambda s: s.entries(folder_id, path=path,
                                             location_revision=location_revision, cursor=cursor))

    async def preview(folder_id: str, path: str = "", location_revision: Optional[str] = None,
                      cursor: Optional[str] = None):
        return await run(lambda s: s.preview(folder_id, path=path,
                                             location_revision=location_revision, cursor=cursor))

    # The fixed paths before `{folder_id}`, which would otherwise take them.
    app.add_api_route(PREFIX, list_folders, methods=["GET"])
    app.add_api_route(PREFIX, register, methods=["POST"])
    app.add_api_route(PREFIX + "/resolve", resolve, methods=["GET"])
    app.add_api_route(PREFIX + "/candidates", candidates, methods=["GET"])
    app.add_api_route(PREFIX + "/candidates/{candidate_id}", candidate_action, methods=["POST"])
    app.add_api_route(PREFIX + "/{folder_id}", detail, methods=["GET"])
    app.add_api_route(PREFIX + "/{folder_id}", edit, methods=["PATCH"])
    app.add_api_route(PREFIX + "/{folder_id}", unregister, methods=["DELETE"])
    app.add_api_route(PREFIX + "/{folder_id}/refresh", refresh, methods=["POST"])
    app.add_api_route(PREFIX + "/{folder_id}/reconnect", reconnect, methods=["POST"])
    app.add_api_route(PREFIX + "/{folder_id}/entries", entries, methods=["GET"])
    app.add_api_route(PREFIX + "/{folder_id}/preview", preview, methods=["GET"])
