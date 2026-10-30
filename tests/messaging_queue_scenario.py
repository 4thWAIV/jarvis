"""The Queue's test world: owner Tim with Matt, Ana (whose JARVIS is offline),
a member with a very long name, a former member whose JARVIS is no longer
listed, and Tim's own two JARVIS instances. Every message in it is really
sent through the contract's fake team server (`FakeServe`), synced into
Tim's local store (`MemoryStore`) and served as the page's local routes
would serve it, so every item, work state and receipt follows the
contract's own rules (`messaging_wire`): nothing here is drawn by hand.

`build()` makes the world; `QueueRoutes` answers the page's messaging
routes (jarvis/messaging_ports.py's route table) from it for Playwright,
and the existing capture send (`POST /api/capture/{n}/send`) for the
review it opened. It is a test adapter: the production routes are
`messaging_api.py`'s, over the real store and the real approval guard.

Ids and times are deterministic (`SeqIds`, `FixedClock`), so ordering is
the same on every run. No message content here is anyone's.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from tests.fixtures.messaging_fakes import FakeServe, FixedClock, MemoryStore, SeqIds, TableGuard, T0

SERVER = "00000000-0000-4000-8000-0000000000aa"
TIM, MATT, ANA, MAX, FORMER = 863470, 1001, 1002, 1003, 1004
TIM_I9 = UUID("00000000-0000-4000-8000-000000000065")
TIM_MINI = UUID("00000000-0000-4000-8000-000000000066")
MATT_MAC = UUID("00000000-0000-4000-8000-000000000067")
ANA_STUDIO = UUID("00000000-0000-4000-8000-000000000068")
MAX_NODE = UUID("00000000-0000-4000-8000-000000000069")
GONE_NODE = UUID("00000000-0000-4000-8000-00000000006a")

LONG_NAME = "Maximiliana Theodora Featherstonehaugh-Wolstenholme"
LONG_SUBJECT = ("A very long subject line that keeps going well past the width of any "
                "right-hand panel, so that it has to be cut and shown in full elsewhere")

ROSTER = {
    TIM: ("tistocks", "Tim"),
    MATT: ("mattr", "Matt"),
    ANA: ("ana-o", "Ana"),
    MAX: ("maxfw", LONG_NAME),
}

# The network's nodes: Ana's is offline, the former member's is gone.
NODES = [
    (TIM_I9, TIM, "tistocks", "Tim’s i9", "online"),
    (TIM_MINI, TIM, "tistocks", "Tim’s Mac mini", "online"),
    (MATT_MAC, MATT, "mattr", "Matt’s MacBook", "online"),
    (ANA_STUDIO, ANA, "ana-o", "Ana’s Studio", "offline"),
    (MAX_NODE, MAX, "maxfw", "Max’s JARVIS", "online"),
]


@dataclass
class World:
    clock: FixedClock
    ids: SeqIds
    serve: FakeServe
    store: MemoryStore
    guard: TableGuard
    transports: dict
    # Named messages and threads, for the tests to find.
    sent: dict = field(default_factory=dict)
    # The page's private file copies: attachment id -> (ref, bytes).
    files: dict = field(default_factory=dict)

    @property
    def tim(self):
        return self.transports[TIM_I9]

    def thread(self, name: str) -> str:
        return str(self.sent[name].thread_id)

    def message(self, name: str) -> str:
        return str(self.sent[name].message_id)

    async def send(self, name: str, node: UUID, recipient: int, subject: str, text: str, *,
                   reply_to: Optional[str] = None, related: tuple[str, ...] = (),
                   files: tuple[tuple[str, bytes], ...] = (), minutes: int = 7) -> wire.AcceptedMessage:
        """Send through the fake team server from `node`'s owner, exactly as
        a paired JARVIS would: staged files first, then the envelope."""
        transport = self.transports[node]
        message_id = self.ids.new_id()
        parent = self.sent[reply_to] if reply_to else None
        attachments = []
        for filename, data in files:
            ref = wire.AttachmentRef(attachment_id=self.ids.new_id(), filename=filename,
                                     media_type="application/pdf", size_bytes=len(data),
                                     sha256=hashlib.sha256(data).hexdigest())
            await transport.create_attachment(wire.AttachmentCreate(
                attachment_id=ref.attachment_id, message_id=message_id,
                size_bytes=ref.size_bytes, sha256=ref.sha256))
            await transport.upload_attachment(ref.attachment_id, _chunks(data))
            await transport.finalize_attachment(ref.attachment_id)
            attachments.append(ref)
        content = wire.MessageContent(
            message_id=message_id, thread_id=parent.thread_id if parent else message_id,
            in_reply_to_message_id=parent.message_id if parent else None,
            related_message_ids=[self.sent[r].message_id for r in related],
            recipient_github_id=recipient, content_revision=1,
            subject=wire.normalize_subject(subject), text=wire.normalize_text(text),
            attachments=attachments)
        sender = self.serve.nodes[node]
        request = wire.SendRequest(
            **content.model_dump(), source_node_id=node, idempotency_key=self.ids.new_id(),
            content_digest=wire.content_digest(content, server_id=SERVER, sender_github_id=sender))
        result = await transport.send(request)
        self.sent[name] = result.message
        self.clock.advance(minutes=minutes)
        return result.message

    async def receipt(self, node: UUID, name: str, kind: str) -> None:
        await self.transports[node].receipts(wire.ReceiptBatch(
            node_id=node, receipts=[wire.ReceiptItem(message_id=self.sent[name].message_id, kind=kind)]))

    async def command(self, name: str, change: dict) -> wire.WorkState:
        thread = self.sent[name].thread_id
        state = self.serve.work[(TIM, thread)]
        request = wire.WorkCommandRequest.model_validate(
            {"change": {**change, "expected_revision": state.revision}})
        return await self.tim.change_work_state(thread, request)

    async def sync(self) -> list[UUID]:
        """Tim's i9 syncs everything it may see, as delivery's loop would."""
        new, continuation = [], self.store.sync_position(SERVER, TIM, TIM_I9)
        while True:
            page = await self.tim.sync(TIM_I9, continuation, wire.SYNC_PAGE_MAX)
            new += self.store.store_page(SERVER, TIM, TIM_I9, page)
            continuation = page.continuation
            if page.scan_complete:
                return new

    def save_draft(self, name: str, thread: str, parent: str, recipient: int, text: str,
                   *, agent: bool = False) -> ports.Draft:
        message_id = self.ids.new_id()
        draft = ports.Draft(
            message_id=message_id, thread_id=self.sent[thread].thread_id,
            in_reply_to_message_id=self.sent[parent].message_id, recipient_github_id=recipient,
            draft_revision=1, subject=self.sent[thread].subject, text=wire.normalize_text(text),
            send=ports.LocalSend(message_id=message_id, content_revision=1, state="draft"),
            agent_drafted=agent)
        self.store.create_draft(SERVER, TIM, draft)
        return draft


async def _chunks(data: bytes):
    yield data


async def build() -> World:
    """Tim's queue, newest arrival last in the order sent:

      ana-numbers   Ana -> Tim, multiline, one file; opened and read; Tim's
                    reply is drafted and unsent (open, unfinished, Active)
      deploy        Tim -> Matt; Matt's JARVIS received it, unread (Matt's turn)
      budget        Tim -> Ana; read by Ana, unanswered (Ana's turn)
      release       Matt -> Tim, then Tim's accepted reply (You replied; Matt's turn)
      lunch         Matt -> Tim; opened, read and dismissed (history)
      note-self     Tim (mini) -> Tim, text; received by both of Tim's JARVIS
      self-file     Tim (i9) -> Tim, a file and no text (self, one node)
      former        a former member -> Tim, from a JARVIS no longer listed
      long          Max (very long name) -> Tim, long subject, multiline
      creds         Matt -> Tim, related to `deploy` (not a reply)
      newest        Matt -> Tim, unread (the newest arrival)
    """
    clock, ids = FixedClock(T0), SeqIds(start=0x100)
    serve = FakeServe(server_id=SERVER, members=set(ROSTER) | {FORMER}, clock=clock)
    transports = {node: serve.pair(owner, node) for node, owner, *_ in NODES}
    transports[GONE_NODE] = serve.pair(FORMER, GONE_NODE)
    store = MemoryStore()
    w = World(clock=clock, ids=ids, serve=serve, store=store,
              guard=TableGuard(store, ids=ids, clock=clock), transports=transports)

    await w.send("ana-numbers", ANA_STUDIO, TIM, "Quarterly numbers",
                 "Here are the numbers.\nRevenue is up.\n\nCan you check the second tab?",
                 files=(("q3-numbers.pdf", b"%PDF-1.4 numbers"),))
    await w.receipt(TIM_I9, "ana-numbers", "received")
    await w.receipt(TIM_I9, "ana-numbers", "read")
    await w.command("ana-numbers", {"command": "open"})
    await w.command("ana-numbers", {"command": "set_unread_reminder", "unread": False})

    await w.send("deploy", TIM_I9, MATT, "Deploy window", "Can we move the deploy to Friday?")
    await w.receipt(MATT_MAC, "deploy", "received")

    await w.send("budget", TIM_I9, ANA, "Budget review", "The budget draft is ready for you.")
    await w.receipt(ANA_STUDIO, "budget", "received")
    await w.receipt(ANA_STUDIO, "budget", "read")

    await w.send("release", MATT_MAC, TIM, "Release notes", "Notes for 2.1 are in the doc.")
    await w.receipt(TIM_I9, "release", "received")
    await w.receipt(TIM_I9, "release", "read")
    await w.command("release", {"command": "open"})
    await w.command("release", {"command": "set_unread_reminder", "unread": False})
    await w.send("release-reply", TIM_I9, MATT, "Release notes", "Looks good to me.",
                 reply_to="release")
    await w.receipt(MATT_MAC, "release-reply", "received")

    await w.send("lunch", MATT_MAC, TIM, "Lunch", "Lunch on Thursday?")
    await w.receipt(TIM_I9, "lunch", "received")
    await w.receipt(TIM_I9, "lunch", "read")
    await w.command("lunch", {"command": "open"})
    await w.command("lunch", {"command": "set_unread_reminder", "unread": False})
    await w.command("lunch", {"command": "dismiss"})

    await w.send("note-self", TIM_MINI, TIM, "Note to self", "Remember the deploy checklist.")
    await w.receipt(TIM_MINI, "note-self", "received")
    await w.receipt(TIM_I9, "note-self", "received")

    await w.send("self-file", TIM_I9, TIM, "", "", files=(("plan.pdf", b"%PDF-1.4 plan"),))

    await w.send("former", GONE_NODE, TIM, "Handover", "Everything is in the shared folder.")
    serve.members.discard(FORMER)                    # no longer on the roster

    await w.send("long", MAX_NODE, TIM, LONG_SUBJECT,
                 "First line of a long message.\nSecond line.\nThird line, with <b>tags</b> "
                 "that must show as text.")

    await w.send("creds", MATT_MAC, TIM, "Staging credentials",
                 "I rotated the staging credentials.", related=("deploy",))
    await w.send("newest", MATT_MAC, TIM, "Can you look at this?", "When you have a minute.")

    await w.sync()
    # Tim's unsent reply on Ana's item, saved before the page opened.
    w.save_draft("ana-reply", "ana-numbers", "ana-numbers", ANA, "Checking the second tab now.")
    return w


def network_envelope() -> dict:
    """GET /api/team/network: this JARVIS is Tim's i9."""
    from tests.test_voice_page_network import _envelope, _node
    nodes = []
    for node_id, owner, login, label, presence in NODES:
        node = _node(str(node_id), label, presence=presence)
        node.update(owner_github_id=owner, owner_login=login)
        nodes.append(node)
    envelope = _envelope(nodes=nodes)
    envelope["snapshot"]["you_node_id"] = str(TIM_I9)
    return envelope


def _error(code: str, status: Optional[int] = None, latest: Optional[dict] = None) -> tuple[int, dict]:
    spec = wire.ERRORS.get(code) or ports.LOCAL_ERRORS[code]
    body = {"error": {"code": code, "message": spec.shown}}
    if latest is not None:
        body["error"]["latest"] = latest
    return status or spec.status, body


class QueueRoutes:
    """Tim's JARVIS's messaging routes for the page under test, over the
    world. `reads` is every read receipt the page sent, in order; `calls`
    every request (method, path)."""

    def __init__(self, world: World, voice=None):
        self.w = world
        self.voice = voice            # the page's fake /ws/voice, for review captures
        self.reads: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.offline = False          # the team server cannot be reached
        self.fail_saves = False       # draft saves fail (JARVIS cannot write)
        self.down = False             # JARVIS's queue route fails
        self.availability = "available"
        self.captures: dict[int, UUID] = {}

    # -- the page's routes ----------------------------------------------------

    async def install(self, page) -> None:
        await page.route("**/api/team/messages**", self._route)
        await page.route("**/api/capture/*/send", self._capture_send)

    async def _route(self, route) -> None:
        req = route.request
        url = urlparse(req.url)
        path = url.path.removeprefix("/api/team/messages")
        self.calls.append((req.method, path))
        try:
            status, body = await self.answer(req.method, path, parse_qs(url.query),
                                             req.post_data_buffer, req.headers)
        except wire.ContractError as e:
            status, body = _error(e.code, latest=e.latest.model_dump(mode="json") if e.latest else None)
        except ports.TransportError as e:
            status, body = _error(e.code, e.status, e.latest)
        except ports.SendRefused:
            status, body = _error("send_refused")
        if body is None:
            await route.fulfill(status=status, body="")
        else:
            await route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    async def answer(self, method: str, path: str, query: dict, data: Optional[bytes],
                     headers: dict) -> tuple[int, Optional[dict]]:
        w, s = self.w, self.w.store
        parts = [unquote(p) for p in path.strip("/").split("/") if p]
        body = json.loads(data) if data and headers.get("content-type", "").startswith("application/json") else None
        dump = lambda m: m.model_dump(mode="json")  # noqa: E731
        if method == "GET" and not parts:
            if self.down:
                return _error("transport")
            return 200, dump(s.snapshot(SERVER, TIM, availability=self.availability, stale=self.offline))
        if method == "GET" and parts == ["history"]:
            limit = int(query.get("limit", [ports.HISTORY_PAGE_DEFAULT])[0])
            return 200, dump(s.history(SERVER, TIM, query.get("continuation", [None])[0], limit))
        if method == "GET" and parts == ["recipients"]:
            members = [wire.TeamMember(github_id=g, login=ROSTER[g][0], name=ROSTER[g][1])
                       for g in sorted(w.serve.members) if g in ROSTER]
            return 200, dump(wire.RecipientList(members=members))
        if parts[:1] == ["threads"]:
            thread = UUID(parts[1])
            if method == "GET":
                item = s.item(SERVER, TIM, thread)
                return (200, dump(item)) if item else _error("message_not_found")
            return await self._work(thread, wire.WorkCommandRequest.model_validate(body))
        if parts[:1] == ["drafts"]:
            return await self._drafts(method, parts[1:], query, body, data, headers)
        message_id = UUID(parts[0])
        if parts[1:] == ["read"]:
            self.reads.append(str(message_id))
            result = await w.tim.receipts(wire.ReceiptBatch(
                node_id=TIM_I9, receipts=[wire.ReceiptItem(message_id=message_id, kind="read")]))
            s.settle_receipts(SERVER, TIM, [ports.OwedReceipt(message_id=message_id, kind="read")], result)
            return 200, dump(s.message(SERVER, TIM, message_id))
        if parts[1] == "files":
            attachment = UUID(parts[2])
            if parts[3] == "download":
                return 200, dump(ports.LocalFile(attachment_id=attachment, state="verified",
                                                 bytes_done=1))
            return 204, None
        return _error("invalid_request")

    async def _work(self, thread: UUID, request: wire.WorkCommandRequest):
        s = self.w.store
        item = s.item(SERVER, TIM, thread)
        if item is None:
            return _error("message_not_found")
        if request.change.command == "dismiss" and item.dismiss_blockers:
            return _error("send_refused")
        if self.offline:
            s.queue_command(SERVER, TIM, thread, request)
            return 200, ports.WorkCommandResult(work_state=item.work_state, pending=True).model_dump(mode="json")
        state = await self.w.tim.change_work_state(thread, request)
        state = s.save_work_state(SERVER, TIM, state)
        return 200, ports.WorkCommandResult(work_state=state).model_dump(mode="json")

    async def _drafts(self, method, parts, query, body, data, headers):
        w, s = self.w, self.w.store
        if method == "POST" and not parts:
            save = ports.DraftSave.model_validate(body)
            if self.fail_saves:
                return _error("transport")
            message_id = w.ids.new_id()
            draft = self._draft(save, message_id=message_id, thread=save.thread_id or message_id,
                                revision=1, send=ports.LocalSend(message_id=message_id,
                                                                 content_revision=1, state="draft"))
            return 201, s.create_draft(SERVER, TIM, draft).model_dump(mode="json")
        message_id = UUID(parts[0])
        current = s.draft(SERVER, TIM, message_id)
        if current is None:
            return _error("draft_not_found")
        if method == "PUT":
            save = ports.DraftSave.model_validate(body)
            if self.fail_saves:
                return _error("transport")
            if save.expected_draft_revision != current.draft_revision:
                raise wire.ContractError("revision_conflict", latest=current)
            draft = self._draft(save, message_id=message_id, thread=current.thread_id,
                                revision=current.draft_revision, send=ports.revise(current))
            return 200, s.save_draft(SERVER, TIM, draft, current.draft_revision).model_dump(mode="json")
        if method == "DELETE":
            s.discard_draft(SERVER, TIM, message_id, int(query["expected_draft_revision"][0]))
            return 204, None
        action = parts[1]
        if action == "files":
            name = wire.normalize_filename(unquote(headers.get("x-filename", "")))
            ref = wire.AttachmentRef(attachment_id=w.ids.new_id(), filename=name,
                                     media_type=wire.normalize_media_type(headers.get("content-type")),
                                     size_bytes=len(data or b""),
                                     sha256=hashlib.sha256(data or b"").hexdigest())
            w.files[ref.attachment_id] = (ref, data or b"")
            return 200, ports.LocalFile(attachment_id=ref.attachment_id, state="staged").model_dump(mode="json")
        expected = ports.DraftAction.model_validate(body).expected_draft_revision
        if expected != current.draft_revision:
            raise wire.ContractError("revision_conflict", latest=current)
        if action == "review":
            draft = w.guard.request_review(SERVER, TIM, message_id, expected)
            capture = 100 + len(self.captures)
            self.captures[capture] = message_id
            label = "Me · Tim’s i9" if draft.recipient_github_id == TIM else ROSTER[draft.recipient_github_id][1]
            draft = draft.model_copy(update={"capture_id": capture})
            s.drafts[(SERVER, TIM, message_id)] = draft
            if self.voice:
                self.voice.send({"type": "capture", "id": capture, "state": "capturing",
                                 "text": ports.human_readback(draft, label), "to": label,
                                 "heard": [], "accepted": False})
            return 200, draft.model_dump(mode="json")
        event = {"cancel": "cancel", "retry": "retry"}[action]
        send = current.send.model_copy(update={"state": ports.next_send_state(current.send.state, event)})
        s.set_send(SERVER, TIM, send)
        return 200, s.draft(SERVER, TIM, message_id).model_dump(mode="json")

    def _draft(self, save: ports.DraftSave, *, message_id, thread, revision, send) -> ports.Draft:
        refs = [self.w.files[a][0] for a in save.attachment_ids]
        return ports.Draft(
            message_id=message_id, thread_id=thread, in_reply_to_message_id=save.in_reply_to_message_id,
            related_message_ids=save.related_message_ids, recipient_github_id=save.recipient_github_id,
            draft_revision=revision, subject=wire.normalize_subject(save.subject),
            text=wire.normalize_text(save.text), attachments=refs,
            files=[ports.LocalFile(attachment_id=r.attachment_id, state="staged") for r in refs],
            send=send)

    async def _capture_send(self, route) -> None:
        """The owner's Send on the review's capture: approved, handed over,
        accepted by the team server and stored, then the capture closes."""
        capture = int(route.request.url.rstrip("/").split("/")[-2])
        message_id = self.captures.get(capture)
        w, s = self.w, self.w.store
        draft = s.draft(SERVER, TIM, message_id) if message_id else None
        if draft is None:
            await route.fulfill(status=404, content_type="application/json", body='{"detail": "gone"}')
            return
        approved = w.guard.approve(SERVER, TIM, message_id, draft.send.content_revision,
                                   draft.send.content_digest, TIM_I9, "gui")
        for ref in approved.request.attachments:
            data = w.files[ref.attachment_id][1]
            await w.tim.create_attachment(wire.AttachmentCreate(
                attachment_id=ref.attachment_id, message_id=message_id,
                size_bytes=ref.size_bytes, sha256=ref.sha256))
            await w.tim.upload_attachment(ref.attachment_id, _chunks(data))
            await w.tim.finalize_attachment(ref.attachment_id)
        s.set_send(SERVER, TIM, s.sends[(SERVER, TIM, message_id)].model_copy(update={"state": "submitting"}))
        result = await w.tim.send(approved.request)
        s.record_accepted(SERVER, TIM, result)
        w.clock.advance(minutes=1)
        await w.sync()
        if self.voice:
            self.voice.send({"type": "capture", "id": capture, "state": None})
        await route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
