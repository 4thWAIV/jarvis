"""The attachment routes, their service, the file settings and the recovery
worker (capability `messaging.v1`, contract in messaging_models).

    POST /api/v1/attachments                  AttachmentCreate -> AttachmentStatus
    PUT  /api/v1/attachments/{id}/bytes       (octet-stream)   -> AttachmentStatus
    POST /api/v1/attachments/{id}/finalize                     -> AttachmentStatus
    GET  /api/v1/attachments/{id}                              -> AttachmentStatus
    GET  /api/v1/messages/{message_id}/attachments/{id}/content   (the bytes)

Staging is its owner's alone: every staging route is scoped to the session's
member, and someone else's record answers exactly as no record
(`attachment_not_found`). The recipient never sees staging. A download is
authorized only by an accepted message the viewer may see whose manifest
names the file (the message repository decides visibility; a team
administrator is no participant). The bytes go out as an inert download:
octet-stream whatever the sender said, `attachment` disposition with the
manifest's safe name, nosniff, and the app's sandboxing CSP.

The upload route is the one route whose body may exceed the app's 16 KiB
limit (`is_upload` names it exactly, for that middleware); it counts the
bytes it reads itself, so a missing or false Content-Length changes
nothing, and each owner has at most `upload_streams_max` uploads at once,
each aborted after `upload_idle_timeout_sec` without a byte.

Every dependency is injected: the storage, the message repository (only
its viewer-scoped `get_visible`), the settings, the clock and the faults
(`NO_FAULTS` in production). Mounting (`router`) takes the app's own
`device` dependency, so only a paired JARVIS's session reaches any of it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AsyncIterator, Awaitable, Callable, Mapping, Optional, Protocol
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from teamserver import clock as clock_mod
from teamserver.errors import Problem
from teamserver.messaging_models import (ERRORS, NO_FAULTS, ORPHAN_PIN_AFTER_SEC,
                                         RECOVERY_LEASE_SEC, AcceptedMessage, AttachmentCreate,
                                         AttachmentRef, AttachmentStatus, ContractError, Faults)
from teamserver.messaging_ports import ATTACHMENT_SETTINGS, AttachmentStorage, StagingRecord
from teamserver.sessions import Principal

log = logging.getLogger("teamserver.attachments")

UPLOAD_PATH = re.compile(r"^/api/v1/attachments/[0-9a-fA-F-]{36}/bytes$")
# How long an attempt nobody chose is kept before the sweep deletes it: far
# past the idle timeout, so only a dead upload is ever swept.
ATTEMPT_SWEEP_AFTER_SEC = 3600
RECOVERY_INTERVAL_SEC = 60
RECOVERY_BATCH = 100
# The download's own headers. The app adds nosniff and its CSP to every
# answer; `sandbox` here keeps even a browser that renders it from running it.
DOWNLOAD_CSP = "sandbox; default-src 'none'"


def is_upload(method: str, path: str) -> bool:
    """The request the app's 16 KiB body gate lets through: exactly an
    attachment's byte upload, which counts its own bytes."""
    return method == "PUT" and bool(UPLOAD_PATH.match(path))


# -- settings ----------------------------------------------------------------------

@dataclass(frozen=True)
class AttachmentSettings:
    """The deployment's file settings, each the contract's default unless
    its TEAM_SERVER_* variable sets it lower (never higher, never zero)."""
    file_max_bytes: int
    package_max_bytes: int
    staging_quota_bytes: int
    staging_expiry_sec: int
    upload_streams_max: int
    upload_idle_timeout_sec: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "AttachmentSettings":
        """Raises ValueError naming every setting that is not a whole number
        from 1 to its default."""
        values, problems = {}, []
        for name, default in ATTACHMENT_SETTINGS.items():
            field = name.removeprefix("TEAM_SERVER_").lower()
            raw = env.get(name, "").strip()
            if not raw:
                values[field] = default
            elif raw.isdigit() and 1 <= int(raw) <= default:
                values[field] = int(raw)
            else:
                problems.append(f"{name} must be a whole number from 1 to {default}")
        if problems:
            raise ValueError("; ".join(problems))
        return cls(**values)


DEFAULT_SETTINGS = AttachmentSettings.from_env({})


def problem(e: ContractError) -> Problem:
    spec = ERRORS[e.code]
    return Problem(spec.status, e.code, spec.shown)


def status_of(record: StagingRecord) -> AttachmentStatus:
    return AttachmentStatus(
        attachment_id=record.attachment_id, message_id=record.message_id, state=record.state,
        size_bytes=record.size_bytes, sha256=record.sha256,
        received_bytes=record.received_bytes,
        expires_at=record.expires_at if record.state in ("uploading", "verified") else None)


class MessagesVisible(Protocol):
    """What the attachment service needs of the message repository: one
    accepted message, if this viewer may see it."""

    async def get_visible(self, server_id: str, viewer_github_id: int,
                          message_id: UUID) -> Optional[AcceptedMessage]: ...


async def _within(chunks: AsyncIterator[bytes], idle_sec: float) -> AsyncIterator[bytes]:
    """The request body, given up after `idle_sec` without a byte."""
    it = chunks.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(it.__anext__(), idle_sec)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            raise Problem(408, "upload_idle", "The upload stopped sending; send it again.") \
                from None
        if chunk:
            yield chunk


class AttachmentService:
    def __init__(self, storage: AttachmentStorage, messages: MessagesVisible, *,
                 settings: AttachmentSettings = DEFAULT_SETTINGS,
                 clock: Callable[[], datetime] = clock_mod.now, faults: Faults = NO_FAULTS):
        self.storage, self.messages, self.settings = storage, messages, settings
        self.clock, self.faults = clock, faults
        self._streams: dict[tuple[str, int], int] = {}

    async def _record(self, server_id: str, owner: int, attachment_id: UUID) -> StagingRecord:
        record = await self.storage.staging(server_id, owner, attachment_id)
        if record is None:
            raise ContractError("attachment_not_found")
        return record

    async def create(self, server_id: str, owner: int,
                     body: AttachmentCreate) -> AttachmentStatus:
        if body.size_bytes > self.settings.file_max_bytes:
            raise ContractError("too_large")
        return status_of(await self.storage.create_staging(
            server_id, owner, body.attachment_id, body.message_id, body.size_bytes,
            body.sha256, self.clock()))

    async def upload(self, server_id: str, owner: int, attachment_id: UUID,
                     chunks: AsyncIterator[bytes],
                     declared_length: Optional[int]) -> AttachmentStatus:
        """One attempt, from byte 0. A record that is no longer uploading
        is answered as it is, without reading the body."""
        record = await self._record(server_id, owner, attachment_id)
        if record.state != "uploading":
            return status_of(record)
        if declared_length is not None and declared_length > record.size_bytes:
            raise ContractError("too_large")
        slot = (server_id, owner)
        if self._streams.get(slot, 0) >= self.settings.upload_streams_max:
            raise Problem(429, "rate_limited", ERRORS["rate_limited"].shown, retry_after=1)
        self._streams[slot] = self._streams.get(slot, 0) + 1
        try:
            await self.storage.write_attempt(
                server_id, owner, attachment_id,
                _within(chunks, self.settings.upload_idle_timeout_sec), record.size_bytes)
        finally:
            self._streams[slot] -= 1
            if not self._streams[slot]:
                del self._streams[slot]
        return status_of(await self._record(server_id, owner, attachment_id))

    async def finalize(self, server_id: str, owner: int,
                       attachment_id: UUID) -> AttachmentStatus:
        record = await self._record(server_id, owner, attachment_id)
        if record.state == "expired":
            raise ContractError("attachment_not_found")
        if record.state == "uploading":
            attempt = await self.storage.latest_attempt(server_id, owner, attachment_id)
            if attempt is None:
                raise ContractError("attachment_not_ready")
            self.faults.check("server_before_attachment_finalize")
            record = await self.storage.finalize(server_id, owner, attachment_id, attempt,
                                                 self.clock())
            self.faults.check("server_after_attachment_finalize")
        return status_of(record)

    async def status(self, server_id: str, owner: int, attachment_id: UUID) -> AttachmentStatus:
        return status_of(await self._record(server_id, owner, attachment_id))

    async def download(self, server_id: str, viewer: int, message_id: UUID,
                       attachment_id: UUID) -> tuple[AttachmentRef, AsyncIterator[bytes]]:
        """The manifest entry and its bytes, for a viewer who may see an
        accepted message naming the file; anything else is
        `attachment_not_found`, whether the message, the file or the
        permission is what is missing."""
        message = await self.messages.get_visible(server_id, viewer, message_id)
        ref = next((a for a in (message.attachments if message else [])
                    if a.attachment_id == attachment_id), None)
        if ref is None:
            raise ContractError("attachment_not_found")
        return ref, self.storage.read(server_id, attachment_id)


# -- the routes ----------------------------------------------------------------------

def disposition(filename: str) -> str:
    """`attachment`, with an ASCII fallback and the exact UTF-8 name."""
    fallback = "".join(c if 32 <= ord(c) < 127 and c not in '"\\%' else "_" for c in filename)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def router(*, device: Callable[..., Awaitable[Principal]],
           service: Callable[[Request], AttachmentService]) -> APIRouter:
    """The routes, behind the app's `device` dependency. `service` gives the
    request's AttachmentService (the integrator's wiring decides whether
    messaging is switched on and builds it from the app's database)."""
    routes = APIRouter()

    def scope(p: Principal) -> tuple[str, int]:
        return p.team["server_id"], p.github_id

    async def answer(call: Awaitable):
        try:
            return await call
        except ContractError as e:
            raise problem(e) from None

    @routes.post("/api/v1/attachments", response_model=AttachmentStatus)
    async def create_attachment(request: Request, body: AttachmentCreate,
                                p: Principal = Depends(device)):
        return await answer(service(request).create(*scope(p), body))

    @routes.put("/api/v1/attachments/{attachment_id}/bytes", response_model=AttachmentStatus)
    async def upload_attachment(request: Request, attachment_id: UUID,
                                p: Principal = Depends(device)):
        length = request.headers.get("content-length")
        declared = int(length) if length and length.isdigit() else None
        return await answer(service(request).upload(*scope(p), attachment_id,
                                                    request.stream(), declared))

    @routes.post("/api/v1/attachments/{attachment_id}/finalize",
                 response_model=AttachmentStatus)
    async def finalize_attachment(request: Request, attachment_id: UUID,
                                  p: Principal = Depends(device)):
        return await answer(service(request).finalize(*scope(p), attachment_id))

    @routes.get("/api/v1/attachments/{attachment_id}", response_model=AttachmentStatus)
    async def attachment_status(request: Request, attachment_id: UUID,
                                p: Principal = Depends(device)):
        return await answer(service(request).status(*scope(p), attachment_id))

    @routes.get("/api/v1/messages/{message_id}/attachments/{attachment_id}/content",
                response_class=StreamingResponse)
    async def attachment_content(request: Request, message_id: UUID, attachment_id: UUID,
                                 p: Principal = Depends(device)):
        ref, chunks = await answer(service(request).download(*scope(p), message_id,
                                                             attachment_id))
        # The first chunk before the answer starts: a missing blob is an
        # error body, not a broken download.
        first = await answer(anext(chunks, b""))

        async def body():
            if first:
                yield first
            async for chunk in chunks:
                yield chunk

        return StreamingResponse(body(), media_type="application/octet-stream", headers={
            "Content-Disposition": disposition(ref.filename),
            "Content-Length": str(ref.size_bytes),
            "Content-Security-Policy": DOWNLOAD_CSP,
            "Cache-Control": "no-store"})

    return routes


# -- recovery and cleanup ---------------------------------------------------------------

@dataclass(frozen=True)
class RecoveryPass:
    published: int          # orphan-looking pins whose message was in fact accepted
    released: int           # pins no accepted message has, released after the hour
    deleted: int            # expired staging records, bytes and all
    kept: int               # expired records an accepted message names: never deleted
    swept: int              # attempts nobody chose


async def recover_once(storage: AttachmentStorage, messages: MessagesVisible, *,
                       worker_id: str, now: datetime,
                       batch: int = RECOVERY_BATCH) -> Optional[RecoveryPass]:
    """One pass under the fenced lease (None when another worker holds it).

    A pin older than ORPHAN_PIN_AFTER_SEC is finished when its message was
    accepted and names the file (marked published: the crash came after the
    insert), and released when no accepted message has its id. Expired
    staging records are deleted with every attempt's bytes, unless an
    accepted message names them, which only a bug could cause and which is
    then logged and kept: accepted data is never deleted. Then the attempts
    nobody chose are swept."""
    fence = await storage.claim_recovery(worker_id, now, RECOVERY_LEASE_SEC)
    if fence is None:
        return None
    published = released = deleted = kept = 0

    async def accepted_names(record: StagingRecord) -> bool:
        m = await messages.get_visible(record.server_id, record.owner_github_id,
                                       record.message_id)
        return m is not None and any(a.attachment_id == record.attachment_id
                                     for a in m.attachments)

    for record in await storage.stale_pins(
            fence, now - timedelta(seconds=ORPHAN_PIN_AFTER_SEC), batch):
        if await accepted_names(record):
            await storage.mark_published(record.server_id, record.attachment_id)
            published += 1
        elif await storage.release_pin(fence, record):
            released += 1
    for record in await storage.claim_expired(fence, now, batch):
        if await accepted_names(record):
            log.error("attachment %s expired although an accepted message names it; kept",
                      record.attachment_id)
            kept += 1
            continue
        await storage.delete(fence, record)
        deleted += 1
    swept = await storage.sweep_attempts(
        fence, now - timedelta(seconds=ATTEMPT_SWEEP_AFTER_SEC), batch)
    return RecoveryPass(published, released, deleted, kept, swept)


async def watch(storage: AttachmentStorage, messages: MessagesVisible, *, worker_id: str,
                clock: Callable[[], datetime] = clock_mod.now,
                interval: float = RECOVERY_INTERVAL_SEC) -> None:
    """The recovery worker: a pass every `interval`, for the app's lifespan.
    A failed pass is logged and tried again next time; a restart never
    releases anything by itself."""
    while True:
        try:
            done = await recover_once(storage, messages, worker_id=worker_id, now=clock())
            if done and any((done.published, done.released, done.deleted, done.kept,
                             done.swept)):
                log.info("attachment recovery: %s", done)
        except asyncio.CancelledError:
            raise
        except Exception as e:      # the next pass tries again
            log.warning("attachment recovery failed: %s", type(e).__name__)
        await asyncio.sleep(interval)
