"""The page's work-package routes, /api/packages: the packages unpacked here,
a page package's own files for its frame, what that frame saves, and
opening it in the centre area. Thin: each calls work_packages.py, and
opening calls the same `open_page` the brain's `open_package_page` does.

They sit behind the web boundary like every other route (web_auth.py): a
POST needs the page's own origin or the tool token, and no access-key
category covers them. A page package's frame has no origin (its files are
served with `sandbox allow-scripts`, web_auth.PACKAGE_PAGE_HEADERS), so it
reaches none of these itself: it asks the page, by postMessage, and the
page (PackageView.tsx) makes the request.

  GET  /api/packages                        the packages unpacked here
  GET  /api/packages/{id}/files/{path}      one file of a page package
  POST /api/packages/{id}/results?name=     save the body as results/<name>
                                            (a page package's, at most
                                            work_packages.RESULT_MAX_BYTES)
  POST /api/packages/{id}/page              open it in every page's centre
"""
from __future__ import annotations

import asyncio
import mimetypes
from typing import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from jarvis import work_packages
from jarvis.web_auth import PACKAGE_PAGE_MARK, PACKAGE_PAGE_PREFIX
from jarvis.work_packages import Package, PackageError

_TEXT_TYPES = ("text/", "application/javascript", "application/json", "image/svg+xml")


def describe(package: Package) -> dict:
    """A package as the page shows it."""
    m = package.manifest
    return {"id": package.id, "name": m.name, "version": m.version, "purpose": m.purpose,
            "runs": m.runs, "entry": m.entry, "source": package.source}


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
          open_page: Callable[[str], Awaitable[tuple[Package | None, str]]]) -> None:
    """`open_page(reference)` opens a page package in every page's centre
    area: (the package, "") or (None, why not)."""

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

    app.add_api_route("/api/packages", list_packages, methods=["GET"])
    app.add_api_route(PACKAGE_PAGE_PREFIX + "{package_id}" + PACKAGE_PAGE_MARK + "{path:path}",
                      page_file, methods=["GET"])
    app.add_api_route(PACKAGE_PAGE_PREFIX + "{package_id}/results", save_result,
                      methods=["POST"])
    app.add_api_route(PACKAGE_PAGE_PREFIX + "{package_id}/page", open_package_page,
                      methods=["POST"])
