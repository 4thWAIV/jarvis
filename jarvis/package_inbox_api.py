"""The package inbox's routes (package_inbox.py). Thin: each calls the
server's one function for it, which the brain's tool calls too.

  POST /api/packages/inbox?project=&session=&workflow=&work=
                                     the zip's bytes: a package for Tim's
                                     inbox, checked, unpacked and recorded,
                                     never shown. The `packages` access
                                     key's only route (make_package sends it)
  GET  /api/packages/inbox           the open packages, newest first, and how
                                     many are closed (?closed=1 lists those)
  GET  /api/packages/inbox/{id}      one, with its questions and answers
  POST /api/packages/inbox/{id}/open his Open: shown in the centre (a page
                                     package), on his click only
  POST /api/packages/inbox/{id}/answer
                                     {"decision", "option", "words"?}: his
                                     answer, recorded by the package's own
                                     scripts/decide.py
  POST /api/packages/inbox/{id}/close
                                     dismiss it: set aside, nothing sent back
  POST /api/packages/{package_id}/signoff
                                     his Done, on the package's view and its
                                     Queue row: signed off, for any package
                                     unpacked here (in the inbox or not); his
                                     answers go back (the workflow's
                                     decisions, and a message to the sending
                                     session that goes only on his yes), its
                                     Work item learns it, and it is closed

Every route but the first needs the page's own origin or the tool token
(web_auth.py); Open needs his click (packages_api.his_click), as every way
of showing a package does. Each change is written before its hint.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from jarvis import messaging_wire, package_inbox, packages_api, work_packages
from jarvis.package_inbox import Entry, Sender
from jarvis.work_packages import Package, PackageError

INBOX_PATH = "/api/packages/inbox"
SIGN_OFF_PATH = "/api/packages/{package_id}/signoff"
# The most a package zip may be: a received file's limit.
MAX_BYTES = messaging_wire.FILE_MAX_BYTES

# (the row, "") or (None, why not)
Outcome = tuple[Optional[Entry], str]


@dataclass(frozen=True)
class Calls:
    """The server's functions, shared with the brain's tools."""
    receive: Callable[[bytes, Sender, str, str], Awaitable[tuple[Optional[
        package_inbox.Arrival], Optional[Package], str]]]
    open: Callable[[str, str], Awaitable[tuple[Optional[Entry], Optional[Package], str]]]
    answer: Callable[[str, str, str, str, str], Awaitable[Outcome]]
    sign_off: Callable[[str, str], Awaitable[Outcome]]
    close: Callable[[str, str], Awaitable[Outcome]]


def entry_json(entry: Entry) -> dict:
    """A row as the page shows it: every field, and who sent it in words."""
    return {**entry.public(), "sender": entry.sender_name(), "waiting": entry.waiting}


def detail(entry: Entry) -> dict:
    """One row in full: the row, its package as the page describes it, and
    each question its deck asks with what is recorded (none for a package
    that asks nothing, or one no longer here)."""
    try:
        package = work_packages.find(entry.package_id)
    except PackageError:
        return {"inbox": entry_json(entry), "package": None, "questions": []}
    return {"inbox": entry_json(entry), "package": packages_api.describe(package),
            "questions": [q.public() for q in package_inbox.questions(package)]}


async def _body(request: Request) -> bytes:
    out = bytearray()
    async for chunk in request.stream():
        out.extend(chunk)
        if len(out) > MAX_BYTES:
            raise ValueError(f"The zip is over {MAX_BYTES} bytes, the most a package may be.")
    return bytes(out)


def _refused(problem: str, status: int = 409) -> JSONResponse:
    return JSONResponse({"error": problem}, status_code=status)


def mount(app: FastAPI, calls: Calls) -> None:

    async def receive(request: Request, project: str = "", session: str = "",
                      workflow: str = "", work: str = ""):
        try:
            data = await _body(request)
        except ValueError as e:
            return _refused(str(e), 413)
        if not data:
            return _refused("There is no zip in the request.", 400)
        arrival, package, problem = await calls.receive(
            data, Sender(project=project, session=session), workflow, work)
        if arrival is None:
            return _refused(problem)
        return {"inbox": entry_json(arrival.entry), "arrived": arrival.fresh,
                "superseded": [e.id for e in arrival.superseded],
                "package": packages_api.describe(package)}

    async def listing(closed: int = 0):
        def read():
            rows = package_inbox.listing(closed=bool(closed))
            return {"packages": [entry_json(e) for e in rows],
                    "closed_count": package_inbox.closed_count()}
        return await asyncio.to_thread(read)

    async def one(inbox_id: str):
        entry = await asyncio.to_thread(package_inbox.get, inbox_id)
        if entry is None:
            return _refused("There is no package by that id in the inbox.", 404)
        return await asyncio.to_thread(detail, entry)

    async def open_one(inbox_id: str, request: Request):
        if not packages_api.his_click(request):
            return _refused(packages_api.NOT_HIS_ACT, 403)
        entry, package, problem = await calls.open(inbox_id, package_inbox.ON_THE_PAGE)
        if entry is None:
            return _refused(problem)
        return {"inbox": entry_json(entry), "package": packages_api.describe(package),
                "problem": problem or None}

    async def answer(inbox_id: str, body: dict = Body(default_factory=dict)):
        entry, problem = await calls.answer(inbox_id, str(body.get("decision") or ""),
                                            str(body.get("option") or ""),
                                            str(body.get("words") or ""),
                                            package_inbox.ON_THE_PAGE)
        if entry is None:
            return _refused(problem)
        return {"inbox": entry_json(entry), "said": problem}

    async def sign_off(package_id: str):
        entry, said = await calls.sign_off(package_id, package_inbox.ON_THE_PAGE)
        if entry is None:
            return _refused(said)
        return {"inbox": entry_json(entry), "said": said}

    async def close(inbox_id: str):
        entry, problem = await calls.close(inbox_id, package_inbox.ON_THE_PAGE)
        if entry is None:
            return _refused(problem)
        return {"inbox": entry_json(entry)}

    app.add_api_route(INBOX_PATH, receive, methods=["POST"])
    app.add_api_route(INBOX_PATH, listing, methods=["GET"])
    app.add_api_route(INBOX_PATH + "/{inbox_id}", one, methods=["GET"])
    app.add_api_route(INBOX_PATH + "/{inbox_id}/open", open_one, methods=["POST"])
    app.add_api_route(INBOX_PATH + "/{inbox_id}/answer", answer, methods=["POST"])
    app.add_api_route(SIGN_OFF_PATH, sign_off, methods=["POST"])
    app.add_api_route(INBOX_PATH + "/{inbox_id}/close", close, methods=["POST"])
