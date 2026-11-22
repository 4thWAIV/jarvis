"""The package request routes (package_request.py, package_jobs.py). Thin:
each calls the server's one function for it.

  POST /api/packages/requests?work=<item id>|general=1
        {"handoff": {...}, "engine"?, "project"?, "workflow"?, "source"?,
         "documents"?: [[document, revision], ...]}: an onboarded agent asks
        for a package, signed with its own key (agent_rights `packages`;
        `work` in the query so the right's scope sees it, the one Work item
        the package goes to; the handoff's own `work` is only what it is
        about). Checked at once:
        a bad request is refused (400, `problems`) and nothing is recorded.
        Taken, JARVIS records who asked from the signature and the job with
        its run, starts the run itself and answers {job, package, run,
        state}. Each request lands at most once (report_keys.once).
  GET  /api/packages/requests/{job}
        the job's record, to the agent that asked for it
  POST /api/packages/requests/{job}/answer
        {"answers": {question id: words}}: its answers to the questions the
        job asked it; the run goes on. Its answer to its check of the
        finished package (`check`: approve, or what is wrong) delivers it,
        or starts a new run with its words
  POST /api/packages/requests/{job}/rerun
        the asking agent asks for its package again after this run ended in
        Error or failed, with no new answers: the next run reuses every
        step whose inputs are unchanged (step_cache.py);
        answers the new run's job. Refused (409) while a later run of the
        package is still going, or when this run did neither
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from jarvis import messages_api, package_jobs, package_request, reporters, report_keys, web_auth

ROUTE = package_request.ROUTE
ONLY_AN_AGENT = ("A package is asked for by an onboarded agent, its request signed with its own "
                 "key (make_package --start, or the package_start tool).")


@dataclass(frozen=True)
class Calls:
    """The server's functions (each blocking, but `answer`, a coroutine:
    an approved check delivers the package on the loop)."""
    accept: Callable[[str, str, bool, dict], dict]
    status: Callable[[str, str], dict]
    answer: Callable[[str, str, dict], Awaitable[dict]]
    rerun: Callable[[str, str], dict]


def _refused(why: str, status: int, problems=None) -> JSONResponse:
    return JSONResponse({"error": why, "problems": list(problems or [why])}, status_code=status)


def _session(request: Request) -> str:
    principal = web_auth.principal(request.scope) or ""
    return principal[len(reporters.AGENT_PREFIX):] \
        if principal.startswith(reporters.AGENT_PREFIX) else ""


def mount(app: FastAPI, calls: Calls) -> None:

    async def ask(request: Request, work: str = "", general: str = ""):
        session = _session(request)
        if not session:
            return _refused(ONLY_AN_AGENT, 403)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return _refused("A package request is a JSON object: handoff, and what goes "
                            "beside it.", 400)
        try:
            return await asyncio.to_thread(calls.accept, session, work.strip(),
                                           general.strip().lower() in messages_api.GENERAL_YES,
                                           body)
        except package_request.Refused as e:
            return _refused("Not taken: " + " ".join(e.problems), 400, e.problems)

    async def one(job: str, request: Request):
        session = _session(request)
        if not session:
            return _refused(ONLY_AN_AGENT, 403)
        try:
            return await asyncio.to_thread(calls.status, session, job)
        except package_jobs.JobError as e:
            return _refused(str(e), 404 if "no package job" in str(e) else 403)

    async def answer(job: str, request: Request):
        session = _session(request)
        if not session:
            return _refused(ONLY_AN_AGENT, 403)
        try:
            body = await request.json()
        except ValueError:
            body = None
        answers = body.get("answers") if isinstance(body, dict) else None
        try:
            return await calls.answer(session, job, answers)
        except package_jobs.JobError as e:
            return _refused(str(e), 404 if "no package job" in str(e) else 409)

    async def rerun(job: str, request: Request):
        session = _session(request)
        if not session:
            return _refused(ONLY_AN_AGENT, 403)
        try:
            return await asyncio.to_thread(calls.rerun, session, job)
        except package_jobs.JobError as e:
            return _refused(str(e), 404 if "no package job" in str(e) else 409)

    app.add_api_route(ROUTE, report_keys.once(ask), methods=["POST"])
    app.add_api_route(ROUTE + "/{job}/rerun", rerun, methods=["POST"])
    app.add_api_route(ROUTE + "/{job}", one, methods=["GET"])
    app.add_api_route(ROUTE + "/{job}/answer", answer, methods=["POST"])
