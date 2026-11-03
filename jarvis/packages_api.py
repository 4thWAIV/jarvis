"""The page's work-package routes, /api/packages: the packages unpacked here,
a page package's own files for its frame, what that frame saves, opening
it in the centre area, opening a received one, and telling its story.
Thin: each calls work_packages.py; opening a received zip calls the same
`open_received` the brain's `open_package` does, opening its page the same
`open_page` the brain's `open_package_page` does, and telling the same story
teller (story_teller.py) the brain's `tell_package_story` does.

They sit behind the web boundary like every other route (web_auth.py): a
POST needs the page's own origin or the tool token, and no access-key
category covers them. A page package's frame has no origin (its files are
served with `sandbox allow-scripts`, web_auth.PACKAGE_PAGE_HEADERS), so it
reaches none of these itself: it asks the page, by postMessage, and the
page (PackageView.tsx) makes the request.

  GET  /api/packages                        the packages unpacked here
  POST /api/packages/received               {"message_id", "attachment_id"}:
                                            the Queue's Open package, a
                                            received zip unpacked here
  GET  /api/packages/{id}/files/{path}      one file of a page package
  POST /api/packages/{id}/results?name=     save the body as results/<name>
                                            (a page package's, at most
                                            work_packages.RESULT_MAX_BYTES)
  POST /api/packages/{id}/page              open it in every page's centre
  POST /api/packages/{id}/story             {"action": "tell", "slide"?} or
                                            {"action": "stop"}: its story
  GET  /api/packages/story.js               the helper a story page loads
                                            (package_story.js, one copy),
                                            which points and highlights in
                                            its frame
"""
from __future__ import annotations

import asyncio
import mimetypes
import uuid
from typing import Awaitable, Callable, Optional

from fastapi import Body, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from jarvis import data_paths, package_story, work_packages
from jarvis.web_auth import PACKAGE_PAGE_MARK, PACKAGE_PAGE_PREFIX
from jarvis.work_packages import Package, PackageError

RECEIVED_PATH = "/api/packages/received"
_TEXT_TYPES = ("text/", "application/javascript", "application/json", "image/svg+xml")


def describe(package: Package) -> dict:
    """A package as the page shows it; `story` is its story's outline, or
    None (no story, or one that can no longer be told)."""
    m = package.manifest
    try:
        story = package.story()
    except PackageError:
        story = None
    return {"id": package.id, "name": m.name, "version": m.version, "purpose": m.purpose,
            "runs": m.runs, "entry": m.entry, "source": package.source,
            "sources": list(package.sources),
            "story": story.outline() if story is not None else None}


HELPER_FILE = data_paths.PACKAGE_DIR / "package_story.js"


def media_type(relative: str) -> str:
    guessed = mimetypes.guess_type(relative)[0] or "application/octet-stream"
    if guessed == "application/javascript":
        guessed = "text/javascript"
    return f"{guessed}; charset=utf-8" if guessed.startswith(_TEXT_TYPES) else guessed


def _refused(e: PackageError, status: int = 404) -> JSONResponse:
    return JSONResponse({"error": str(e)}, status_code=status)


async def _body(request: Request, limit: int) -> bytes:
    """The request body, refused past `limit` bytes without reading on."""
    out = bytearray()
    async for chunk in request.stream():
        out.extend(chunk)
        if len(out) > limit:
            raise PackageError(f"That result is over {limit} bytes, the most that may be "
                               f"saved.")
    return bytes(out)


def mount(app: FastAPI,
          open_page: Callable[[str], Awaitable[tuple[Package | None, str]]],
          story: Callable[[str, str, Optional[int]], Awaitable[tuple[bool, str]]],
          open_received: Callable[[uuid.UUID, uuid.UUID],
                                  Awaitable[tuple[Package | None, bool, str]]]) -> None:
    """`open_page(reference)` opens a page package in every page's centre
    area: (the package, "") or (None, why not). `story(action, reference,
    slide)` tells or stops its story: (done, what was done or why not).
    `open_received(message_id, attachment_id)` unpacks a received zip:
    (the package, unpacked now, "") or (None, False, why not)."""

    async def list_packages():
        packages = await asyncio.to_thread(work_packages.every)
        return {"packages": [describe(p) for p in packages]}

    async def page_file(package_id: str, path: str):
        try:
            package = await asyncio.to_thread(work_packages.find, package_id,
                                              work_packages.PAGE)
            data = await asyncio.to_thread(work_packages.page_file, package, path)
        except PackageError as e:
            return _refused(e)
        return Response(data, media_type=media_type(path),
                        headers={"Cache-Control": "no-store"})

    async def save_result(package_id: str, name: str, request: Request):
        try:
            package = await asyncio.to_thread(work_packages.find, package_id,
                                              work_packages.PAGE)
        except PackageError as e:
            return _refused(e)
        try:
            work_packages.result_name(name)
            data = await _body(request, work_packages.RESULT_MAX_BYTES)
            saved = await asyncio.to_thread(work_packages.save_result, package, name, data)
        except PackageError as e:
            return _refused(e, 400)
        return {"saved": saved.relative_to(package.folder).as_posix(), "bytes": len(data)}

    async def open_package_page(package_id: str):
        package, problem = await open_page(package_id)
        if package is None:
            return JSONResponse({"error": problem}, status_code=404)
        return {"package": describe(package)}

    async def open_received_package(body: dict = Body(default_factory=dict)):
        try:
            message_id = uuid.UUID(str(body.get("message_id") or ""))
            attachment_id = uuid.UUID(str(body.get("attachment_id") or ""))
        except ValueError:
            return JSONResponse({"error": "Which file? Give its message and attachment ids."},
                                status_code=400)
        package, fresh, problem = await open_received(message_id, attachment_id)
        if package is None:
            return JSONResponse({"error": problem}, status_code=409)
        return {"package": describe(package), "unpacked": fresh}

    async def story_helper():
        return Response(await asyncio.to_thread(HELPER_FILE.read_bytes),
                        media_type=media_type(HELPER_FILE.name),
                        headers={"Cache-Control": "no-store"})

    async def tell_story(package_id: str, body: dict = Body(default_factory=dict)):
        slide = body.get("slide")
        if slide is not None and (not isinstance(slide, int) or isinstance(slide, bool)):
            return JSONResponse({"error": "slide is a number, from 1."}, status_code=400)
        done, said = await story(str(body.get("action") or ""), package_id, slide)
        if not done:
            return JSONResponse({"error": said}, status_code=409)
        return {"said": said}

    app.add_api_route("/api/packages", list_packages, methods=["GET"])
    app.add_api_route(RECEIVED_PATH, open_received_package, methods=["POST"])
    # Before the {package_id} routes, so story.js is never taken for an id.
    app.add_api_route(package_story.HELPER_PATH, story_helper, methods=["GET"])
    app.add_api_route(PACKAGE_PAGE_PREFIX + "{package_id}" + PACKAGE_PAGE_MARK + "{path:path}",
                      page_file, methods=["GET"])
    app.add_api_route(PACKAGE_PAGE_PREFIX + "{package_id}/results", save_result,
                      methods=["POST"])
    app.add_api_route(PACKAGE_PAGE_PREFIX + "{package_id}/page", open_package_page,
                      methods=["POST"])
    app.add_api_route(PACKAGE_PAGE_PREFIX + "{package_id}/story", tell_story,
                      methods=["POST"])
