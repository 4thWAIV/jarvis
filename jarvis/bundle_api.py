"""The page's bundle routes, each calling bundle_inspection (and the
transport lane's services) exactly as the brain's bundle tools do. Mounted
beside messaging_files' routes, behind the web boundary; reads are GETs,
the rest need the page's origin or the tool token like every change.

    POST {A}/bundle                                   -> Classification  (download if needed)
    GET  {A}/bundle                                   -> Classification
    GET  {A}/bundle/members?folder&continuation&limit -> MemberPage
    GET  {A}/bundle/members/{member_id}/text?continuation&max_chars -> TextPage
    GET  {A}/bundle/members/{member_id}/preview?continuation        -> Preview
    GET  {A}/bundle/members/{member_id}/pages/{page}?max_edge       -> image/png
    GET  {A}/bundle/members/{member_id}/html/{path}   -> a static preview's file
    POST {A}/bundle/members/{member_id}/show          -> 204 (the bundle_view frame)
    POST {A}/repair                                   -> Draft
    GET  /api/team/messages/drafts/{message_id}/readiness  -> PreflightResult
    PUT  /api/team/messages/drafts/{message_id}/task       -> Draft
    POST /api/team/messages/drafts/{message_id}/companions -> Draft
    GET  /api/team/messages/threads/{thread_id}/review     -> ReviewState
    PUT  /api/team/messages/threads/{thread_id}/review/scope -> ReviewState

`{A}` is bundle_contract.ATTACHMENT_PREFIX. A refusal is
`{"error": {"code", "message", "reason"}}` with bundle_contract.REFUSAL_STATUS.
The html route answers with web_auth's bundle-preview headers: a sandbox
without scripts, nothing from outside the bundle.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter

from jarvis.bundle_contract import ATTACHMENT_PREFIX

BUNDLE = ATTACHMENT_PREFIX + "/bundle"


def router(context: Callable[[], Optional[object]]) -> APIRouter:
    """The routes above; `context` gives the inspection service and whose
    messaging it is, or None while messaging is unavailable."""
    raise NotImplementedError("bindings lane")
