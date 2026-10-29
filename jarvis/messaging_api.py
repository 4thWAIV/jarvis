"""The page's human-messaging routes, /api/team/messages (the table is in
`messaging_ports`). They sit behind the existing web boundary like every
other route; the page never sees the team server's credential, and every
call to the team server goes through delivery's transport.

What is local stays local: drafts, review and the queue's last known state
are served from the store whatever the team server is doing, and a work
command made while it cannot be reached is kept and sent later. Approving
is not here: the review opens the capture the owner accepts (the existing
`POST /api/capture/{n}/send`, or the read-back heard in full), and only that
acceptance, through the approval guard, puts an envelope in the outbox.
Reading a message is the one way a read receipt is made: the page calls
`.../read` once the body was displayed.

A refusal is the team server's error shape with a code from `wire.ERRORS`
or `ports.LOCAL_ERRORS`. A hint (`on_change`, the `messages` frame) follows
every committed change.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.messaging_client import Delivery, Pairing

PREFIX = "/api/team/messages"


class Refused(Exception):
    """A route's refusal: a code, the current record as `latest` where the
    code has one, and any extra fields the page words it with."""

    def __init__(self, code: str, latest: Optional[BaseModel | dict] = None, **extra):
        super().__init__(code)
        self.code, self.latest, self.extra = code, latest, extra

    def response(self) -> JSONResponse:
        spec = (ports.LOCAL_ERRORS.get(self.code) or wire.ERRORS.get(self.code)
                or wire.error_spec(self.code, 502))
        body: dict = {"code": self.code, "message": spec.shown, **self.extra}
        if self.latest is not None:
            body["latest"] = (self.latest.model_dump(mode="json")
                              if isinstance(self.latest, BaseModel) else self.latest)
        return JSONResponse({"error": body}, status_code=spec.status)


def refused_by(e: Exception) -> Refused:
    """The page's refusal for what a service raised."""
    if isinstance(e, Refused):
        return e
    if isinstance(e, wire.ContractError):
        return Refused(e.code, e.latest)
    if isinstance(e, ports.SendRefused):
        return Refused("send_refused", state=e.state)
    if isinstance(e, ports.NotApproved):
        return Refused("not_approved")
    if isinstance(e, ports.SnapshotMissing):
        return Refused("snapshot_missing", attachment_id=str(e.attachment_id))
    if isinstance(e, ports.TransportError):
        return Refused(e.code, e.latest)
    if isinstance(e, ValidationError):
        return Refused("invalid_request")
    raise e


class _Ids:
    def new_id(self) -> UUID:
        return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalMessaging:
    """The routes' work. `captures` is server.py's capture box (the review's
    capture), `snapshots` the files stream's private copies, `guard` the
    approval stream's one guard; each is injected."""

    def __init__(self, *, store, delivery: Delivery, guard: ports.ApprovalGuard,
                 captures: Optional[ports.ReviewCaptures] = None,
                 snapshots: Optional[ports.AttachmentSnapshots] = None,
                 ids: Optional[ports.IdSource] = None, clock: ports.Clock = _utcnow,
                 files_routes: Optional[Callable[[FastAPI], None]] = None):
        self.store, self.delivery, self.guard = store, delivery, guard
        self.captures, self.snapshots = captures, snapshots
        self.ids, self.clock, self.files_routes = ids or _Ids(), clock, files_routes
        self._roster: dict[tuple[str, int], wire.RecipientList] = {}

    # -- whose records ------------------------------------------------------------------

    def pairing(self) -> Pairing:
        if not self.delivery.enabled():
            raise Refused("messaging_unavailable", availability="switched_off")
        pairing = self.delivery.pairing()
        if pairing is None:
            raise Refused("messaging_unavailable", availability="not_paired")
        return pairing

    async def remote(self) -> Pairing:
        """A pairing whose team server offers messaging, for a call to it."""
        pairing = self.pairing()
        availability = await self.delivery.availability()
        if availability != "available":
            raise Refused("messaging_unavailable", availability=availability)
        return pairing

    # -- files on the page's shapes ---------------------------------------------------------

    def _files(self, ns, message_id: UUID) -> list[ports.LocalFile]:
        return self.snapshots.files(*ns, message_id) if self.snapshots else []

    def _draft_view(self, ns, draft: ports.Draft) -> ports.Draft:
        files = {f.attachment_id: f for f in self._files(ns, draft.message_id)}
        if not files:
            return draft
        return draft.model_copy(update={"files": [
            files.get(a.attachment_id, ports.LocalFile(attachment_id=a.attachment_id,
                                                       state="missing"))
            for a in draft.attachments]})

    def _item_view(self, ns, item: ports.LocalItem) -> ports.LocalItem:
        if self.snapshots is None:
            return item
        messages = [m.model_copy(update={"files": self._files(ns, m.message.message_id)})
                    for m in item.messages]
        drafts = [self._draft_view(ns, d) for d in item.drafts]
        editable = [d for d in drafts if d.send.state in ("draft", "awaiting_approval")]
        return item.model_copy(update={
            "messages": messages, "drafts": drafts,
            "dismiss_blockers": ports.dismiss_blockers(
                unsent_text=any(d.text or d.attachments for d in editable),
                send_states=[d.send.state for d in drafts],
                uploads_pending=any(ports.uploads_pending(d.files) for d in drafts))})

    def _attachments(self, ns, message_id: UUID, content_revision: int,
                     attachment_ids: list[UUID]) -> list[wire.AttachmentRef]:
        """The chosen files, in the chosen order, from their private copies."""
        if not attachment_ids:
            return []
        if self.snapshots is None:
            raise ports.SnapshotMissing(attachment_ids[0])
        refs = {r.attachment_id: r for r in self.snapshots.verified_attachment_manifest(
            *ns, message_id, content_revision)}
        for attachment_id in attachment_ids:
            if attachment_id not in refs:
                raise ports.SnapshotMissing(attachment_id)
        return [refs[a] for a in attachment_ids]

    # -- reads ------------------------------------------------------------------------------

    async def snapshot(self) -> ports.QueueSnapshot:
        availability = await self.delivery.availability()
        pairing = self.delivery.pairing() if self.delivery.enabled() else None
        if pairing is None:
            return ports.QueueSnapshot(availability=availability, items=[])
        snap = self.store.snapshot(*pairing.ns, availability=availability,
                                   stale=self.delivery.stale or availability != "available")
        return snap.model_copy(update={
            "items": [self._item_view(pairing.ns, i) for i in snap.items],
            "unsent": [self._draft_view(pairing.ns, d) for d in snap.unsent]})

    def history(self, continuation: Optional[str], limit: int) -> ports.HistoryPage:
        ns = self.pairing().ns
        page = self.store.history(*ns, continuation, limit)
        return page.model_copy(update={"items": [self._item_view(ns, i) for i in page.items]})

    def item(self, thread_id: UUID) -> ports.LocalItem:
        ns = self.pairing().ns
        item = self.store.item(*ns, thread_id)
        if item is None:
            raise Refused("message_not_found")
        return self._item_view(ns, item)

    async def recipients(self) -> wire.RecipientList:
        pairing = await self.remote()
        roster = await self.delivery.transport(pairing).recipients()
        self._roster[pairing.ns] = roster
        return roster

    async def _label(self, pairing: Pairing, github_id: int) -> str:
        """How the read-back names the recipient: the roster's name, else
        the login. Read once per pairing and again if they are not on it."""
        roster = self._roster.get(pairing.ns)
        if roster is None or all(m.github_id != github_id for m in roster.members):
            roster = await self.recipients()
        member = next((m for m in roster.members if m.github_id == github_id), None)
        if member is None:
            raise Refused("recipient_not_allowed")
        return member.name or member.login

    # -- work state ------------------------------------------------------------------------

    async def work_command(self, thread_id: UUID,
                           request: wire.WorkCommandRequest) -> ports.WorkCommandResult:
        pairing = self.pairing()
        item = self.item(thread_id)
        if isinstance(request.change, wire.Dismiss) and item.dismiss_blockers:
            raise Refused("send_refused", dismiss_blockers=list(item.dismiss_blockers))
        if await self.delivery.availability() != "available":
            return self._keep_command(pairing, item, request)
        try:
            state = await self.delivery.transport(pairing).change_work_state(thread_id, request)
        except ports.TransportError as e:
            if e.code == "revision_conflict" and e.latest:
                latest = self.store.save_work_state(*pairing.ns,
                                                    wire.WorkState.model_validate(e.latest))
                self.delivery.changed()
                raise Refused("revision_conflict", latest) from None
            if (e.code == "transport" or e.retryable) and not e.session_ended:
                return self._keep_command(pairing, item, request)
            raise
        stored = self.store.save_work_state(*pairing.ns, state)
        self.store.settle_command(*pairing.ns, thread_id)
        self.delivery.changed()
        return ports.WorkCommandResult(work_state=stored)

    def _keep_command(self, pairing: Pairing, item: ports.LocalItem,
                      request: wire.WorkCommandRequest) -> ports.WorkCommandResult:
        self.store.queue_command(*pairing.ns, item.work_state.thread_id, request)
        self.delivery.changed()
        return ports.WorkCommandResult(work_state=item.work_state, pending=True)

    # -- drafts ------------------------------------------------------------------------------

    def _editable_on(self, ns, thread_id: UUID) -> Optional[ports.Draft]:
        item = self.store.item(*ns, thread_id)
        return next((d for d in (item.drafts if item else [])
                     if d.send.state in ("draft", "awaiting_approval")), None)

    def create_draft(self, save: ports.DraftSave) -> ports.Draft:
        ns = self.pairing().ns
        if save.expected_draft_revision is not None:
            raise Refused("invalid_request")
        message_id = self.ids.new_id()
        if save.thread_id is None:
            if save.in_reply_to_message_id is not None:
                raise Refused("invalid_request")
            thread_id = message_id
        else:
            parent = (self.store.message(*ns, save.in_reply_to_message_id)
                      if save.in_reply_to_message_id else None)
            if parent is None or parent.message.thread_id != save.thread_id:
                raise Refused("invalid_request")
            existing = self._editable_on(ns, save.thread_id)
            if existing is not None:
                raise Refused("revision_conflict", self._draft_view(ns, existing))
            thread_id = save.thread_id
        send = ports.LocalSend(message_id=message_id, content_revision=1, state="draft")
        draft = ports.Draft(
            message_id=message_id, thread_id=thread_id,
            in_reply_to_message_id=save.in_reply_to_message_id,
            related_message_ids=list(save.related_message_ids),
            recipient_github_id=save.recipient_github_id, draft_revision=1,
            attachments=self._attachments(ns, message_id, 1, list(save.attachment_ids)),
            send=send, **self._normalized(save))
        created = self.store.create_draft(*ns, draft)
        self.delivery.changed()
        return self._draft_view(ns, created)

    @staticmethod
    def _normalized(save: ports.DraftSave) -> dict:
        """Subject and text as they will be reviewed and sent; anything the
        wire would refuse is refused now, never silently dropped."""
        subject, text = wire.normalize_subject(save.subject), wire.normalize_text(save.text)
        if wire.subject_problem(subject) or wire.text_problem(text):
            raise Refused("invalid_request")
        return {"subject": subject, "text": text}

    def save_draft(self, message_id: UUID, save: ports.DraftSave) -> ports.Draft:
        ns = self.pairing().ns
        current = self.store.draft(*ns, message_id)
        if current is None:
            raise Refused("draft_not_found")
        if (save.expected_draft_revision is None
                or save.thread_id not in (None, current.thread_id)
                or save.in_reply_to_message_id != current.in_reply_to_message_id):
            raise Refused("invalid_request")
        if current.draft_revision != save.expected_draft_revision:
            raise Refused("revision_conflict", self._draft_view(ns, current))
        fields = {**self._normalized(save), "recipient_github_id": save.recipient_github_id,
                  "related_message_ids": list(save.related_message_ids)}
        attachment_ids = list(save.attachment_ids)
        if all(getattr(current, k) == v for k, v in fields.items()) and \
                [a.attachment_id for a in current.attachments] == attachment_ids:
            return self._draft_view(ns, current)          # nothing changed: nothing revised
        send = ports.revise(current)
        attachments = self._attachments(ns, message_id, send.content_revision, attachment_ids)
        if send.content_revision != current.send.content_revision:
            self._close_capture(current)
        updated = current.model_copy(update={
            **fields, "attachments": attachments, "send": send,
            "capture_id": current.capture_id
            if send.content_revision == current.send.content_revision else None})
        saved = self.store.save_draft(*ns, ports.Draft.model_validate(updated.model_dump()),
                                      save.expected_draft_revision)
        self.delivery.changed()
        return self._draft_view(ns, saved)

    def discard_draft(self, message_id: UUID, expected_draft_revision: int) -> None:
        ns = self.pairing().ns
        current = self.store.draft(*ns, message_id)
        if current is None:
            raise Refused("draft_not_found")
        self.store.discard_draft(*ns, message_id, expected_draft_revision)
        self._close_capture(current)
        self.delivery.changed()

    def _close_capture(self, draft: ports.Draft) -> None:
        if draft.capture_id is not None and self.captures is not None:
            self.captures.close(draft.capture_id)

    def _action_draft(self, ns, message_id: UUID, action: ports.DraftAction) -> ports.Draft:
        draft = self.store.draft(*ns, message_id)
        if draft is None:
            raise Refused("draft_not_found")
        if draft.draft_revision != action.expected_draft_revision:
            raise Refused("revision_conflict", self._draft_view(ns, draft))
        return draft

    async def review(self, message_id: UUID, action: ports.DraftAction) -> ports.Draft:
        """Freeze the revision for review and open the capture the owner
        accepts. Approves nothing; a repeat is a no-op."""
        pairing = self.pairing()
        ns = pairing.ns
        draft = self._action_draft(ns, message_id, action)
        if draft.send.state == "awaiting_approval" and draft.capture_id is not None:
            return self._draft_view(ns, draft)
        label = await self._label(pairing, draft.recipient_github_id)
        reviewed = self.guard.request_review(*ns, message_id, action.expected_draft_revision)
        if self.captures is not None:
            capture_id = self.captures.open_for_review(
                reviewed, ports.human_readback(reviewed, label), label)
            reviewed = self.store.set_capture(*ns, message_id, capture_id)
        self.delivery.changed()
        return self._draft_view(ns, reviewed)

    def cancel(self, message_id: UUID, action: ports.DraftAction) -> ports.Draft:
        """Stop a revision before any attempt is out. After that there is
        nothing to cancel: it settles first, and acceptance is final."""
        ns = self.pairing().ns
        draft = self._action_draft(ns, message_id, action)
        state = ports.next_send_state(draft.send.state, "cancel")
        if state != draft.send.state and not self.store.transition_send(
                *ns, draft.send, draft.send.model_copy(update={"state": state,
                                                               "next_attempt_at": None})):
            raise Refused("send_refused", self.store.draft(*ns, message_id))
        self._close_capture(draft)
        cancelled = self.store.set_capture(*ns, message_id, None)
        self.delivery.changed()
        return self._draft_view(ns, cancelled)

    def retry(self, message_id: UUID, action: ports.DraftAction) -> ports.Draft:
        """The same approved revision again, under its idempotency key."""
        ns = self.pairing().ns
        draft = self._action_draft(ns, message_id, action)
        send = self.store.send(*ns, message_id)
        if send is None or send.state not in ("failed", "accept_unknown"):
            raise Refused("send_refused", state=send.state if send else None)
        self.guard.require_approved_revision(*ns, message_id, send.content_revision)
        if not self.store.transition_send(*ns, send, send.model_copy(update={
                "attempts": 0, "next_attempt_at": self.clock()})):
            raise Refused("send_refused", self.store.draft(*ns, message_id))
        self.delivery.wake()
        self.delivery.changed()
        return self._draft_view(ns, self.store.draft(*ns, message_id) or draft)

    # -- reading -------------------------------------------------------------------------------

    def read(self, message_id: UUID) -> ports.LocalMessage:
        """The owner had this body displayed: its read receipt is owed (a
        message the owner received; the sender's own read is no receipt)."""
        pairing = self.pairing()
        local = self.store.message(*pairing.ns, message_id)
        if local is None:
            raise Refused("message_not_found")
        if local.message.recipient_github_id == pairing.owner_github_id \
                and local.receipts.read_at is None:
            self.store.owe_receipt(*pairing.ns, message_id, "read")
            self.delivery.wake()
        return local.model_copy(update={"files": self._files(pairing.ns, message_id)})


def mount(app: FastAPI, messaging: LocalMessaging) -> None:
    """The page's messaging routes, and the files stream's beside them."""

    @app.exception_handler(Refused)
    async def on_refused(request: Request, exc: Refused):
        return exc.response()

    async def run(work):
        try:
            result = work()
            if hasattr(result, "__await__"):
                result = await result
        except Exception as e:
            return refused_by(e).response()
        return result

    def body(model: type[BaseModel], raw: dict):
        try:
            return model.model_validate(raw)
        except ValidationError:
            raise Refused("invalid_request") from None

    async def json_of(request: Request) -> dict:
        try:
            raw = await request.json()
        except ValueError:
            raise Refused("invalid_request") from None
        if not isinstance(raw, dict):
            raise Refused("invalid_request")
        return raw

    @app.get(PREFIX)
    async def queue_snapshot():
        return await run(messaging.snapshot)

    @app.get(PREFIX + "/history")
    async def queue_history(continuation: Optional[str] = None,
                            limit: int = ports.HISTORY_PAGE_DEFAULT):
        return await run(lambda: messaging.history(continuation, limit))

    @app.get(PREFIX + "/recipients")
    async def queue_recipients():
        return await run(messaging.recipients)

    @app.get(PREFIX + "/threads/{thread_id}")
    async def queue_item(thread_id: UUID):
        return await run(lambda: messaging.item(thread_id))

    @app.put(PREFIX + "/threads/{thread_id}/work-state")
    async def queue_work(thread_id: UUID, request: Request):
        raw = await json_of(request)
        return await run(lambda: messaging.work_command(
            thread_id, body(wire.WorkCommandRequest, raw)))

    @app.post(PREFIX + "/drafts", status_code=201)
    async def draft_create(request: Request):
        raw = await json_of(request)
        return await run(lambda: messaging.create_draft(body(ports.DraftSave, raw)))

    @app.put(PREFIX + "/drafts/{message_id}")
    async def draft_save(message_id: UUID, request: Request):
        raw = await json_of(request)
        return await run(lambda: messaging.save_draft(message_id, body(ports.DraftSave, raw)))

    @app.delete(PREFIX + "/drafts/{message_id}", status_code=204)
    async def draft_discard(message_id: UUID, expected_draft_revision: int):
        result = await run(lambda: messaging.discard_draft(message_id, expected_draft_revision))
        return result if isinstance(result, JSONResponse) else Response(status_code=204)

    for action in ("review", "cancel", "retry"):
        def make(action: str):
            async def draft_action(message_id: UUID, request: Request):
                raw = await json_of(request)
                return await run(lambda: getattr(messaging, action)(
                    message_id, body(ports.DraftAction, raw)))
            draft_action.__name__ = f"draft_{action}"
            return draft_action
        app.add_api_route(f"{PREFIX}/drafts/{{message_id}}/{action}", make(action),
                          methods=["POST"])

    if messaging.files_routes is not None:
        messaging.files_routes(app)

    @app.post(PREFIX + "/{message_id}/read")
    async def message_read(message_id: UUID):
        return await run(lambda: messaging.read(message_id))
