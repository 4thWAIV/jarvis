"""The page's bundle routes, each calling bundle_inspection (and the
transport lane's services) exactly as the brain's bundle tools do. Mounted
beside messaging_files' routes, behind the web boundary; reads are GETs,
the rest need the page's origin or the tool token like every change.

    POST {A}/bundle                                   -> Classification  (download if needed)
    GET  {A}/bundle                                   -> Classification
    GET  {A}/bundle/members?folder&continuation&limit -> MemberPage
    GET  {A}/bundle/members/{member_id}/text?continuation&max_chars -> TextPage
    GET  {A}/bundle/members/{member_id}/preview?continuation        -> Preview
    GET  {A}/bundle/members/{member_id}/pages/{page}?max_edge       -> the page's image
    GET  {A}/bundle/members/{member_id}/html/{path}   -> a static preview's file
    POST {A}/bundle/members/{member_id}/show          -> 204 (the bundle_view frame)
    POST {A}/repair                                   -> Draft
    GET  /api/team/messages/drafts/{message_id}/readiness  -> PreflightResult
    PUT  /api/team/messages/drafts/{message_id}/task       -> Draft
    PUT  /api/team/messages/drafts/{message_id}/cited      -> Draft
    POST /api/team/messages/drafts/{message_id}/companions -> Draft
    GET  /api/team/messages/threads/{thread_id}/review     -> ReviewState
    PUT  /api/team/messages/threads/{thread_id}/review/scope -> ReviewState

`{A}` is bundle_contract.ATTACHMENT_PREFIX. A refusal is
`{"error": {"code", "message", "reason"}}` with bundle_contract.REFUSAL_STATUS
(an inspection's), or the messaging routes' own refusal (a file that is not
here, a draft at another revision). The html route answers with web_auth's
bundle-preview headers: a sandbox without scripts, nothing from outside the
bundle.

Each route's work is one of the `Calls` below, and the brain's bundle tools
(server.py) make the same call and get the same JSON text (`answer`), so
the page and the brain can never be told different things.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Optional, Union
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from jarvis import bundle_preflight, bundle_tasks
from jarvis import messaging_api
from jarvis.bundle_contract import (ATTACHMENT_PREFIX, BUNDLE_VIEW_FRAME, MEMBER_PAGE_DEFAULT,
                                    MEMBER_PAGE_MAX, REFUSAL_STATUS, TEXT_PAGE_DEFAULT_CHARS,
                                    TEXT_PAGE_MAX_CHARS, InspectionRefused, RenderedPage,
                                    RenderOptions, RENDER_EDGE_DEFAULT)
from jarvis.bundle_inspection import AttachmentKey, BundleInspection, Owner
from jarvis.web_auth import BUNDLE_PREVIEW_MARK

MESSAGES = messaging_api.PREFIX
BUNDLE = ATTACHMENT_PREFIX + "/bundle"
MEMBER = BUNDLE + "/members/{member_id}"


@dataclass(frozen=True)
class BundleContext:
    """What a bundle call works with: the inspection service and whose
    messaging it is (the owner signed in now)."""
    inspection: BundleInspection
    owner: Owner


Context = Callable[[], Optional[BundleContext]]
# Hand a bundle_view frame to every open page: True when one took it.
Show = Callable[[dict], Awaitable[bool]]


# -- one answer, the page's and the brain's ----------------------------------------------

def _json(value: Union[BaseModel, dict]) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def refusal(e: Exception) -> tuple[int, dict]:
    """The status and body of a refused call: an inspection's own code, or
    the messaging routes' refusal for what the files or a draft raised."""
    if isinstance(e, InspectionRefused):
        return REFUSAL_STATUS.get(e.code, 422), {
            "error": {"code": e.code, "message": e.shown, "reason": e.reason}}
    refused = messaging_api.refused_by(e)
    return refused.spec().status, json.loads(refused.response().body)


@dataclass(frozen=True)
class Answer:
    """A call's outcome as the route sends it: its status and JSON text."""
    status: int
    text: str

    @property
    def ok(self) -> bool:
        return self.status < 400

    def body(self) -> dict:
        return json.loads(self.text)


async def answer(work: Awaitable[Union[BaseModel, dict]]) -> Answer:
    try:
        return Answer(200, _json(await work))
    except Exception as e:
        status, body = refusal(e)
        return Answer(status, _json(body))


def _unavailable() -> InspectionRefused:
    return InspectionRefused("messaging_unavailable",
                             messaging_api.Refused("messaging_unavailable").shown())


def _invalid() -> messaging_api.Refused:
    return messaging_api.Refused("invalid_request")


class Calls:
    """Every bundle route's work, as the brain's tools call it too. Each
    method takes the route's own arguments and gives its model; the sync
    service methods run off the event loop."""

    def __init__(self, context: Context, show: Optional[Show] = None):
        self._context, self._show = context, show

    def ctx(self) -> BundleContext:
        found = self._context()
        if found is None:
            raise _unavailable()
        return found

    async def _off_loop(self, method: str, key: AttachmentKey, *args):
        c = self.ctx()
        return await asyncio.to_thread(getattr(c.inspection, method), c.owner, key, *args)

    # inspection

    async def inspect(self, key: AttachmentKey):
        """The owner's instruction to look: downloaded and verified once,
        then classified."""
        c = self.ctx()
        return await c.inspection.open(c.owner, key)

    async def classify(self, key: AttachmentKey):
        return await self._off_loop("classify", key)

    async def members(self, key: AttachmentKey, folder: str = "",
                      continuation: Optional[str] = None,
                      limit: int = MEMBER_PAGE_DEFAULT):
        if not 1 <= limit <= MEMBER_PAGE_MAX:
            raise _invalid()
        return await self._off_loop("list_members", key, folder or "", continuation or None,
                                    limit)

    async def text(self, key: AttachmentKey, member_id: str,
                   continuation: Optional[str] = None,
                   max_chars: int = TEXT_PAGE_DEFAULT_CHARS):
        if not 1 <= max_chars <= TEXT_PAGE_MAX_CHARS:
            raise _invalid()
        return await self._off_loop("read_member", key, member_id, continuation or None,
                                    max_chars)

    async def preview(self, key: AttachmentKey, member_id: str,
                      continuation: Optional[str] = None):
        return await self._off_loop("preview_member", key, member_id, continuation or None)

    async def page(self, key: AttachmentKey, member_id: str, page: int,
                   max_edge: int = RENDER_EDGE_DEFAULT) -> RenderedPage:
        try:
            options = RenderOptions(max_edge=max_edge)
        except ValidationError:
            raise _invalid() from None
        return await self._off_loop("render_page", key, member_id, page, options)

    async def html(self, key: AttachmentKey, member_id: str,
                   relative: str) -> tuple[bytes, str]:
        return await self._off_loop("html_asset", key, member_id, relative)

    async def show(self, key: AttachmentKey, member_id: str) -> bool:
        """Open a member in the centre of every JARVIS page: the member is
        checked first (it must be here and readable as a preview), then the
        frame goes. True when a page took it. The frame names ids only."""
        await self.preview(key, member_id)
        if self._show is None:
            return False
        return await self._show({"type": BUNDLE_VIEW_FRAME, "message_id": str(key.message_id),
                                 "attachment_id": str(key.attachment_id),
                                 "member_id": member_id})

    # the transport lane's services, for whose messaging this is

    async def _owned(self, fn, *args):
        c = self.ctx()
        return await asyncio.to_thread(fn, c.owner.server_id, c.owner.owner_github_id, *args)

    async def readiness(self, message_id: UUID):
        return await self._owned(bundle_preflight.preflight, message_id)

    async def set_task(self, message_id: UUID, expected_draft_revision: int,
                       task: Optional[dict]):
        return await self._owned(bundle_tasks.set_task, message_id, expected_draft_revision,
                                 task)

    async def set_cited(self, message_id: UUID, expected_draft_revision: int,
                        cited: list[dict]):
        return await self._owned(bundle_tasks.set_cited, message_id, expected_draft_revision,
                                 cited)

    async def add_companions(self, message_id: UUID, expected_draft_revision: int,
                             attachment_id: UUID, kinds: list[str]):
        return await self._owned(bundle_tasks.add_companions, message_id,
                                 expected_draft_revision, attachment_id, kinds)

    async def repair(self, key: AttachmentKey, mode: str):
        return await self._owned(bundle_tasks.prepare_repair, key.message_id,
                                 key.attachment_id, mode)

    async def review(self, thread_id: UUID):
        return await self._owned(bundle_tasks.review_state, thread_id)

    async def set_scope(self, thread_id: UUID, expected_revision: int, scope: str):
        return await self._owned(bundle_tasks.set_scope, thread_id, expected_revision, scope)


# -- the routes --------------------------------------------------------------------------

class TaskBody(BaseModel):
    expected_draft_revision: int
    task: Optional[dict] = None


class CitedRef(BaseModel):
    attachment_id: UUID
    member_id: str


class CitedBody(BaseModel):
    expected_draft_revision: int
    cited: list[CitedRef]


class CompanionsBody(BaseModel):
    expected_draft_revision: int
    attachment_id: UUID
    kinds: list[bundle_tasks.CompanionKind]


class RepairBody(BaseModel):
    mode: bundle_tasks.RepairMode


class ScopeBody(BaseModel):
    expected_revision: int
    scope: Literal["full", "partial"]


def _sent(got: Answer) -> Response:
    return Response(got.text, status_code=got.status, media_type="application/json")


async def _bytes_or_refusal(work, respond) -> Response:
    try:
        return respond(await work)
    except Exception as e:
        status, body = refusal(e)
        return Response(_json(body), status_code=status, media_type="application/json")


def router(context: Context, show: Optional[Show] = None) -> APIRouter:
    """The routes above; `context` gives the inspection service and whose
    messaging it is, or None while messaging is unavailable; `show` hands a
    frame to every open page."""
    calls = Calls(context, show)
    routes = APIRouter()

    def key(message_id: UUID, attachment_id: UUID) -> AttachmentKey:
        return AttachmentKey(message_id, attachment_id)

    # The draft's and the thread's routes first: nothing below may capture
    # them as a message id.
    @routes.get(MESSAGES + "/drafts/{message_id}/readiness")
    async def readiness(message_id: UUID):
        return _sent(await answer(calls.readiness(message_id)))

    @routes.put(MESSAGES + "/drafts/{message_id}/task")
    async def set_task(message_id: UUID, body: TaskBody):
        return _sent(await answer(calls.set_task(message_id, body.expected_draft_revision,
                                                 body.task)))

    @routes.put(MESSAGES + "/drafts/{message_id}/cited")
    async def set_cited(message_id: UUID, body: CitedBody):
        return _sent(await answer(calls.set_cited(
            message_id, body.expected_draft_revision,
            [c.model_dump(mode="json") for c in body.cited])))

    @routes.post(MESSAGES + "/drafts/{message_id}/companions")
    async def add_companions(message_id: UUID, body: CompanionsBody):
        return _sent(await answer(calls.add_companions(
            message_id, body.expected_draft_revision, body.attachment_id, list(body.kinds))))

    @routes.get(MESSAGES + "/threads/{thread_id}/review")
    async def review(thread_id: UUID):
        return _sent(await answer(calls.review(thread_id)))

    @routes.put(MESSAGES + "/threads/{thread_id}/review/scope")
    async def set_scope(thread_id: UUID, body: ScopeBody):
        return _sent(await answer(calls.set_scope(thread_id, body.expected_revision,
                                                  body.scope)))

    @routes.post(BUNDLE)
    async def inspect(message_id: UUID, attachment_id: UUID):
        return _sent(await answer(calls.inspect(key(message_id, attachment_id))))

    @routes.get(BUNDLE)
    async def classify(message_id: UUID, attachment_id: UUID):
        return _sent(await answer(calls.classify(key(message_id, attachment_id))))

    @routes.get(BUNDLE + "/members")
    async def members(message_id: UUID, attachment_id: UUID, folder: str = "",
                      continuation: Optional[str] = None, limit: int = MEMBER_PAGE_DEFAULT):
        return _sent(await answer(calls.members(key(message_id, attachment_id), folder,
                                                continuation, limit)))

    @routes.get(MEMBER + "/text")
    async def text(message_id: UUID, attachment_id: UUID, member_id: str,
                   continuation: Optional[str] = None,
                   max_chars: int = TEXT_PAGE_DEFAULT_CHARS):
        return _sent(await answer(calls.text(key(message_id, attachment_id), member_id,
                                             continuation, max_chars)))

    @routes.get(MEMBER + "/preview")
    async def preview(message_id: UUID, attachment_id: UUID, member_id: str,
                      continuation: Optional[str] = None):
        return _sent(await answer(calls.preview(key(message_id, attachment_id), member_id,
                                                continuation)))

    @routes.get(MEMBER + "/pages/{page}")
    async def page(message_id: UUID, attachment_id: UUID, member_id: str, page: int,
                   max_edge: int = RENDER_EDGE_DEFAULT):
        return await _bytes_or_refusal(
            calls.page(key(message_id, attachment_id), member_id, page, max_edge),
            lambda got: Response(got.data, media_type=got.info.media_type,
                                 headers={"Cache-Control": "private, max-age=3600"}))

    @routes.get(MEMBER + BUNDLE_PREVIEW_MARK + "{path:path}")
    async def html(message_id: UUID, attachment_id: UUID, member_id: str, path: str):
        return await _bytes_or_refusal(
            calls.html(key(message_id, attachment_id), member_id, path),
            lambda got: Response(got[0], media_type=got[1],
                                 headers={"Cache-Control": "no-store"}))

    @routes.post(MEMBER + "/show", status_code=204)
    async def show_member(message_id: UUID, attachment_id: UUID, member_id: str):
        got = await answer(_nothing(calls.show(key(message_id, attachment_id), member_id)))
        return Response(status_code=204) if got.ok else _sent(got)

    @routes.post(ATTACHMENT_PREFIX + "/repair")
    async def repair(message_id: UUID, attachment_id: UUID, body: RepairBody):
        return _sent(await answer(calls.repair(key(message_id, attachment_id), body.mode)))

    return routes


async def _nothing(work: Awaitable) -> dict:
    """A call whose route answers with no body."""
    await work
    return {}


def mount(app, context: Context, show: Optional[Show] = None) -> None:
    """The routes themselves on the app, as messaging_files' are (an
    included router would be one opaque entry among the app's routes)."""
    app.router.routes.extend(router(context, show).routes)
