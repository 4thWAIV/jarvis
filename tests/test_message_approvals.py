"""The owner's approval of a human message and the one guarded send path
(`jarvis/message_approvals.py`), with the real guard, the real capture
adapters and the shared orchestration (`transmission`), over the contract's
in-memory store, file snapshots and team server
(`tests/fixtures/messaging_fakes.py`). No `TableGuard` here: the guard under
test is the production one.

The approval-negative cases of the build instructions, each by name:

    draft / awaiting approval     cannot enter the sender path
    changes                       text, subject, recipient, relations or
                                  file bytes invalidate the approval
    stale review, double clicks   cannot send a different or second message
    agent tools                   draft and request review, never approve
    self-send                     the same approval, through the team server
    lost answer, restart, retry   the approved bytes and key are kept
    cancellation                  before sending stops it; accepted stays so
    re-paired / revoked           another owner's JARVIS releases nothing;
                                  an ended session stops the sender
    logs                          ids and codes, never content
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from uuid import UUID

import pytest

from jarvis import message_approvals as approvals_module
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis import transmission
from jarvis.message_approvals import (GuardedTransport, HumanMessaging, HumanRecipient,
                                      Identity, MessageApprovals, MessagingTools, Reviews)
from tests.fixtures import messaging_fakes as fakes

SERVER = "f0000000-0000-4000-8000-00000000000a"
TIM, MATT, OUTSIDER = 863470, 1001, 6666
NODE = UUID(int=0x7111)
NS = (SERVER, TIM)
SECRET_TEXT = "The vault code is 4417 and nobody may know"
SECRET_SUBJECT = "Private: merger"
SECRET_FILE = "salaries-2026.xlsx"


# -- a JARVIS for Tim -------------------------------------------------------------------

class Speech:
    def __init__(self, cancelled=False):
        self.said, self.cancelled, self.windows = [], cancelled, 0

    async def say(self, text, *a, **k):
        self.said.append(text)

    async def open_cancel_window(self, seconds):
        self.windows += 1
        return self.cancelled


class Box:
    def __init__(self, captures):
        self.captures, self.events = captures, []

    def transmitting(self, capture_id):
        self.events.append(("transmitting", capture_id))

    def sent(self, capture_id):
        self.events.append(("sent", capture_id))
        self.captures.close(capture_id)

    def reopen(self, capture_id):
        self.events.append(("reopen", capture_id))


class Jarvis:
    """Tim's JARVIS: the real guard and adapters over the contract's store,
    snapshots and team server."""

    def __init__(self, serve, *, github_id=TIM, node=NODE, store=None, snapshots=None,
                 ids=None):
        self.serve, self.clock = serve, serve.clock
        self.who = Identity(SERVER, github_id, node)
        self.store = store or fakes.MemoryStore()
        self.snapshots = snapshots or fakes.StaticSnapshots()
        self.ids = ids or fakes.SeqIds(start=0x100)
        self.approvals = MessageApprovals(self.store, self.snapshots, ids=self.ids,
                                          clock=self.clock)
        self.raw = serve.pair(github_id, node)
        self.transport = GuardedTransport(self.raw, self.approvals, *self.who.ns)
        self.captures = fakes.ReviewCaptures()
        self.released: list = []
        self.presentation = fakes.RecordingPresentation(self.raw)
        self.available = "available"
        self.messaging = HumanMessaging(
            approvals=self.approvals, identity=lambda: self.who,
            availability=lambda: self.available,
            recipients=lambda: [wire.TeamMember(github_id=TIM, login="tstockstill", name="Tim"),
                                wire.TeamMember(github_id=MATT, login="mattk", name="Matt")],
            released=self.released.append, presentation=self.presentation)
        self.reviews = Reviews(self.approvals, self.captures, self.messaging.recipients)

    # drafts, as messaging_api would save them
    def draft(self, to=MATT, text="Friday?", subject="", files=(), reply_to=None):
        mid = self.ids.new_id()
        refs = [self.file(mid, 1, name, data) for name, data in files]
        draft = ports.Draft(
            message_id=mid, thread_id=reply_to.thread_id if reply_to else mid,
            in_reply_to_message_id=reply_to.message_id if reply_to else None,
            recipient_github_id=to, draft_revision=1, subject=wire.normalize_subject(subject),
            text=wire.normalize_text(text), attachments=refs,
            send=ports.LocalSend(message_id=mid, content_revision=1, state="draft"))
        return self.store.create_draft(*self.who.ns, draft)

    def file(self, mid, revision, name, data):
        ref = wire.AttachmentRef(attachment_id=self.ids.new_id(), filename=name,
                                 media_type="application/octet-stream", size_bytes=len(data),
                                 sha256=hashlib.sha256(data).hexdigest())
        self.snapshots.table.setdefault((mid, revision), []).append(ref)
        self.snapshots.bytes[ref.attachment_id] = data
        return ref

    def upload(self, request):
        """The files stream's `ensure_uploaded`, against the fake team server:
        each approved file staged, uploaded and verified before the submit."""
        async def go():
            for ref in request.attachments:
                await self.raw.create_attachment(wire.AttachmentCreate(
                    attachment_id=ref.attachment_id, message_id=request.message_id,
                    size_bytes=ref.size_bytes, sha256=ref.sha256))

                async def chunks(data=self.snapshots.bytes[ref.attachment_id]):
                    yield data
                await self.raw.upload_attachment(ref.attachment_id, chunks())
                await self.raw.finalize_attachment(ref.attachment_id)
        asyncio.run(go())

    def edit(self, draft, **update):
        """An edit as the draft route makes it: after review it continues on
        the next content revision (`revise`)."""
        send = ports.revise(draft)
        changed = draft.model_copy(update={**update, "send": send})
        if send.content_revision != draft.send.content_revision:
            self.snapshots.table[(draft.message_id, send.content_revision)] = \
                list(changed.attachments)
        return self.store.save_draft(*self.who.ns, changed, draft.draft_revision)

    def current(self, draft):
        return self.store.draft(*self.who.ns, draft.message_id)

    def review(self, draft):
        return self.reviews.review(*self.who.ns, draft.message_id, self.current(draft).draft_revision)

    def approve(self, draft, via="gui"):
        d = self.current(draft)
        return self.approvals.approve(*self.who.ns, d.message_id, d.send.content_revision,
                                      d.send.content_digest, self.who.node_id, via)

    def submitting(self, approved):
        """What delivery does before an attempt leaves: on record first."""
        req = approved.request
        send = next(s for s in self.store.unresolved_sends(*self.who.ns)
                    if s.message_id == req.message_id)
        self.store.set_send(*self.who.ns, send.model_copy(update={
            "state": ports.next_send_state(send.state, "submit"),
            "attempts": send.attempts + 1}))

    def send(self, request):
        return asyncio.run(self.transport.send(request))

    def submit(self, draft):
        """The whole way out for a reviewed and approved draft, as delivery
        will drive it: release, on record, through the guarded transport."""
        d = self.current(draft)
        approved = self.approvals.require_approved_revision(*self.who.ns, d.message_id,
                                                            d.send.content_revision)
        self.submitting(approved)
        self.upload(approved.request)
        result = self.send(approved.request)
        self.store.record_accepted(*self.who.ns, result)
        return result

    def capture(self, draft):
        return self.captures.open[self.current(draft).capture_id] \
            if self.current(draft).capture_id else None

    def stage_and_perform(self, capture_id, *, via="gui", speech=None, accept=True,
                          messaging="default"):
        capture = self.captures.open[capture_id]
        if accept:
            transmission.accept(capture)
        assert transmission.accepted(capture), "only an accepted capture is staged"
        staged = []
        recipient = HumanRecipient(capture, via,
                                   self.messaging if messaging == "default" else messaging)
        speech = Speech() if speech is None else speech
        record, problem = transmission.stage(recipient, capture,
                                             voice=speech if speech is not False else None,
                                             put=staged.append)
        box = Box(self.captures)
        for destination in staged:
            asyncio.run(transmission.perform(destination, speech=speech, cancel_window=2.0,
                                             captures=box))
        return record, problem, staged, speech


@pytest.fixture
def serve():
    return fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=fakes.FixedClock(),
                           faults=wire.ArmedFaults())


@pytest.fixture
def tim(serve):
    return Jarvis(serve)


def _reviewed(tim, **kw):
    draft = tim.draft(**kw)
    reviewed = tim.review(draft)
    return draft, reviewed


def _hand_built(tim, draft, key=None):
    """A request built from a draft without the guard's approval."""
    d = tim.current(draft)
    content = ports.draft_content(d)
    return wire.SendRequest(**content.model_dump(), source_node_id=tim.who.node_id,
                            idempotency_key=key or UUID(int=0xBAD),
                            content_digest=wire.content_digest(content, server_id=SERVER,
                                                               sender_github_id=TIM))


# -- the interfaces --------------------------------------------------------------------

def test_the_guard_and_the_guarded_transport_are_the_contract_s(tim):
    assert isinstance(tim.approvals, ports.ApprovalGuard)
    assert isinstance(tim.transport, ports.ServeTransport)
    assert isinstance(tim.captures, ports.ReviewCaptures)
    capture = {"id": 1, "text": "x", "to": "Matt",
               "human": {"github_id": MATT, "message_id": str(UUID(int=1)),
                         "content_revision": 1, "content_digest": "sha256:" + "0" * 64}}
    recipient = HumanRecipient(capture, "gui", tim.messaging)
    assert isinstance(recipient, transmission.Recipient)
    assert isinstance(approvals_module.HumanDestination(recipient, tim.who),
                      transmission.Destination)


# -- review ----------------------------------------------------------------------------

def test_review_freezes_the_revision_and_opens_its_capture_without_approving(tim):
    draft, reviewed = _reviewed(tim, subject="Deploy", text="Friday?")
    assert reviewed.send.state == "awaiting_approval"
    assert reviewed.send.content_digest == wire.content_digest(
        ports.draft_content(reviewed), server_id=SERVER, sender_github_id=TIM)
    capture = tim.captures.open[reviewed.capture_id]
    assert capture["text"] == "To Matt.\nSubject: Deploy.\nFriday?"
    assert capture["human"] == {"github_id": MATT, "message_id": str(draft.message_id),
                                "content_revision": 1,
                                "content_digest": reviewed.send.content_digest}
    assert tim.store.unresolved_sends(*NS) == [], "nothing is in the outbox before the yes"
    assert tim.store.approved(*NS, draft.message_id, 1) is None


def test_a_second_review_click_changes_nothing(tim):
    draft, first = _reviewed(tim)
    again = tim.review(draft)
    assert again == first and tim.captures.closed == []


def test_review_of_a_stale_draft_revision_is_a_conflict_with_the_latest(tim):
    draft = tim.draft()
    saved = tim.edit(draft, text="Friday at noon?")
    with pytest.raises(wire.ContractError) as e:
        tim.reviews.review(*NS, draft.message_id, 1)
    assert e.value.code == "revision_conflict" and e.value.latest == saved


def test_a_message_with_nothing_in_it_cannot_be_reviewed(tim):
    draft = tim.draft(text="")
    with pytest.raises(wire.ContractError) as e:
        tim.review(draft)
    assert e.value.code == "invalid_request"
    assert tim.current(draft).send.state == "draft"


def test_a_file_only_message_is_reviewed_with_its_files_named(tim):
    draft = tim.draft(text="", files=[("plan.pdf", b"%PDF" * 300)])
    reviewed = tim.review(draft)
    assert tim.captures.open[reviewed.capture_id]["text"] == \
        "To Matt.\nFiles: plan.pdf, 1.2 kilobytes."


def test_a_missing_private_copy_stops_the_review(tim):
    draft = tim.draft(files=[("a.bin", b"abc")])
    tim.snapshots.missing.add(draft.attachments[0].attachment_id)
    with pytest.raises(ports.SnapshotMissing):
        tim.review(draft)
    assert tim.current(draft).send.state == "draft"


# -- the negative cases ---------------------------------------------------------------

def test_a_draft_cannot_enter_the_sender_path(tim, serve):
    draft = tim.draft()
    with pytest.raises(ports.NotApproved):
        tim.approvals.require_approved_revision(*NS, draft.message_id, 1)
    with pytest.raises(ports.NotApproved):
        tim.send(_hand_built(tim, draft))
    with pytest.raises(ports.NotApproved):
        tim.approve(draft), "approving a draft never reviewed"
    assert serve.messages == {}


def test_a_message_awaiting_approval_cannot_enter_the_sender_path(tim, serve):
    draft, _ = _reviewed(tim)
    with pytest.raises(ports.NotApproved):
        tim.approvals.require_approved_revision(*NS, draft.message_id, 1)
    with pytest.raises(ports.NotApproved):
        tim.send(_hand_built(tim, draft))
    assert serve.messages == {}


def test_an_approved_send_leaves_only_once_its_attempt_is_on_record(tim, serve):
    draft, _ = _reviewed(tim)
    approved = tim.approve(draft)
    with pytest.raises(ports.NotApproved):
        tim.send(approved.request)
    assert serve.messages == {}, "an attempt not on record never leaves"
    tim.submitting(approved)
    result = tim.send(approved.request)
    assert result.message.message_id == draft.message_id and not result.duplicate


def test_only_the_stored_envelope_leaves_not_one_built_beside_it(tim, serve):
    draft, _ = _reviewed(tim)
    approved = tim.approve(draft)
    tim.submitting(approved)
    with pytest.raises(ports.NotApproved):
        tim.send(_hand_built(tim, draft, key=UUID(int=0x5EC0)))
    with pytest.raises(ports.NotApproved):
        tim.send(approved.request.model_copy(update={"source_node_id": UUID(int=9)}))
    assert serve.messages == {}


_CHANGES = {
    "text": dict(text="Friday at noon?"),
    "subject": dict(subject="Urgent"),
    "recipient": dict(recipient_github_id=TIM),
    "relations": dict(related_message_ids=[UUID(int=0x4E1)]),
}


@pytest.mark.parametrize("change", sorted(_CHANGES))
def test_a_change_after_review_invalidates_the_capture_s_approval(tim, serve, change):
    draft, reviewed = _reviewed(tim)
    binding = tim.captures.open[reviewed.capture_id]["human"]
    edited = tim.edit(tim.current(draft), **_CHANGES[change])
    assert edited.send.content_revision == 2 and edited.send.state == "draft"
    with pytest.raises(ports.NotApproved):
        tim.approvals.approve(*NS, draft.message_id, binding["content_revision"],
                              binding["content_digest"], NODE, "voice")
    assert tim.store.unresolved_sends(*NS) == [] and serve.messages == {}


@pytest.mark.parametrize("change", sorted(_CHANGES))
def test_a_change_saved_under_the_reviewed_revision_still_cannot_be_approved(tim, change):
    """Even a save that kept the reviewed content revision (no `revise`)
    cannot be approved: the guard digests what would go, not the record."""
    draft, reviewed = _reviewed(tim)
    tim.store.save_draft(*NS, tim.current(draft).model_copy(update=_CHANGES[change]),
                         tim.current(draft).draft_revision)
    with pytest.raises(ports.NotApproved):
        tim.approve(draft)


def test_changed_file_bytes_after_review_invalidate_the_approval(tim, serve):
    draft, _ = _reviewed(tim, files=[("plan.pdf", b"v1")])
    old = draft.attachments[0]
    tim.snapshots.table[(draft.message_id, 1)] = [old.model_copy(update={
        "sha256": hashlib.sha256(b"v2").hexdigest()})]
    with pytest.raises(ports.SnapshotMissing):
        tim.approve(draft)
    assert tim.store.approved(*NS, draft.message_id, 1) is None and serve.messages == {}


def test_changed_file_bytes_after_approval_stop_the_send(tim, serve):
    draft, _ = _reviewed(tim, files=[("plan.pdf", b"v1")])
    approved = tim.approve(draft)
    tim.submitting(approved)
    tim.snapshots.missing.add(draft.attachments[0].attachment_id)
    with pytest.raises(ports.SnapshotMissing):
        tim.approvals.require_approved_revision(*NS, draft.message_id, 1)
    with pytest.raises(ports.SnapshotMissing):
        tim.send(approved.request)
    assert serve.messages == {}


def test_a_stale_review_cannot_approve_the_later_edit(tim):
    draft, first = _reviewed(tim)
    stale = tim.captures.open[first.capture_id]["human"]
    tim.edit(tim.current(draft), text="Friday at noon?")
    second = tim.review(draft)
    assert second.send.content_revision == 2 and tim.captures.closed == [first.capture_id]
    with pytest.raises(ports.NotApproved):
        tim.approvals.approve(*NS, draft.message_id, stale["content_revision"],
                              stale["content_digest"], NODE, "gui")
    with pytest.raises(ports.NotApproved):
        tim.approvals.approve(*NS, draft.message_id, 2, stale["content_digest"], NODE, "gui")
    approved = tim.approve(draft)
    assert approved.request.content_revision == 2 and approved.request.text == "Friday at noon?"


def test_double_clicks_approve_once_and_send_one_message(tim, serve):
    draft, _ = _reviewed(tim)
    first, second = tim.approve(draft), tim.approve(draft, via="voice")
    assert first == second, "the same record, the same key, the first approval's way"
    tim.submitting(first)
    assert not tim.send(first.request).duplicate
    assert tim.send(first.request).duplicate
    assert len(serve.messages) == 1


def test_the_brain_s_tools_draft_and_request_review_but_never_approve(tim, serve):
    saved = []

    def save_draft(who, editing, body, agent_drafted):
        assert agent_drafted is True
        if editing is None:
            mid = tim.ids.new_id()
            draft = ports.Draft(
                message_id=mid, thread_id=body.thread_id or mid,
                in_reply_to_message_id=body.in_reply_to_message_id,
                recipient_github_id=body.recipient_github_id, draft_revision=1,
                subject=body.subject, text=body.text, agent_drafted=True,
                send=ports.LocalSend(message_id=mid, content_revision=1, state="draft"))
            saved.append(draft)
            return tim.store.create_draft(*who.ns, draft)
        current = tim.store.draft(*who.ns, editing)
        return tim.store.save_draft(*who.ns, current.model_copy(update={
            "text": body.text, "subject": body.subject, "send": ports.revise(current)}),
            body.expected_draft_revision)

    tools = MessagingTools(tim.messaging, tim.reviews, save_draft)
    out = asyncio.run(tools.draft_message({"to": "Matt", "subject": "Deploy",
                                           "text": "Friday?"}))
    (draft,) = saved
    assert out.startswith(f"Draft {draft.message_id} to Matt") and "Nothing is sent" in out
    out = tools.request_message_review({"message": str(draft.message_id), "draft_revision": 1})
    assert "Nothing is approved" in out and "send_message" in out
    assert tim.current(draft).send.state == "awaiting_approval"
    assert tim.store.approved(*NS, draft.message_id, 1) is None
    assert tim.store.unresolved_sends(*NS) == [] and serve.messages == {}
    assert not [n for n in dir(tools) if "approv" in n], "approving is never a tool"
    # A revision since the review sends the brain back, and revising reopens review.
    assert "changed since" in tools.request_message_review(
        {"message": str(draft.message_id), "draft_revision": 9})
    out = asyncio.run(tools.draft_message({"message": str(draft.message_id),
                                           "draft_revision": 1, "text": "Friday at noon?"}))
    assert "revision 2" in out
    assert (tim.current(draft).send.content_revision, tim.current(draft).send.state) == (2, "draft")


def test_the_draft_tool_names_the_team_when_it_does_not_know_the_person(tim):
    tools = MessagingTools(tim.messaging, tim.reviews, lambda *a: pytest.fail("saved"))
    assert "no one called Bob" in asyncio.run(tools.draft_message({"to": "Bob", "text": "hi"}))
    assert "Say who it is for" in asyncio.run(tools.draft_message({"text": "hi"}))
    tim.available = "not_paired"
    assert "isn't available" in asyncio.run(tools.draft_message({"to": "Matt", "text": "hi"}))


def test_a_message_to_oneself_needs_the_same_approval_and_goes_through_the_team_server(
        tim, serve):
    draft, reviewed = _reviewed(tim, to=TIM, text="Remember the deploy.")
    assert tim.captures.open[reviewed.capture_id]["text"].startswith("To Me.")
    with pytest.raises(ports.NotApproved):
        tim.send(_hand_built(tim, draft))
    assert serve.messages == {}
    record, problem, (destination,), speech = tim.stage_and_perform(reviewed.capture_id)
    assert problem is None and destination.status == "sent"
    result = tim.submit(draft)
    assert serve.messages[draft.message_id].recipient_github_id == TIM, "accepted by Serve"
    assert tim.store.message(*NS, draft.message_id).self_message
    assert tim.store.item(*NS, draft.thread_id).work_state.action_owner_github_id == TIM
    assert result.message.sender_github_id == TIM


def test_a_lost_answer_and_a_restart_retry_the_same_bytes_and_key(tim, serve):
    draft, _ = _reviewed(tim, files=[("plan.pdf", b"bytes")])
    approved = tim.approve(draft)
    tim.submitting(approved)
    tim.upload(approved.request)
    serve.faults.arm("server_after_commit_before_response")
    with pytest.raises(wire.InjectedFault):
        tim.send(approved.request)
    send = tim.store.unresolved_sends(*NS)[0]
    tim.store.set_send(*NS, send.model_copy(update={
        "state": ports.next_send_state(send.state, "answer_lost")}))
    # A restart: a new guard over the same store and copies.
    again = Jarvis(serve, store=tim.store, snapshots=tim.snapshots, ids=fakes.SeqIds(start=0x900))
    released = again.approvals.require_approved_revision(*NS, draft.message_id, 1)
    assert released == approved, "the approved bytes and key, unchanged"
    with pytest.raises(ports.NotApproved):
        again.send(released.request), "accept_unknown is retried only once on record"
    send = again.store.unresolved_sends(*NS)[0]
    again.store.set_send(*NS, send.model_copy(update={
        "state": ports.next_send_state(send.state, "retry")}))
    result = again.send(released.request)
    assert result.duplicate and len(serve.messages) == 1
    assert serve.messages[draft.message_id].idempotency_key == approved.request.idempotency_key


def test_another_owner_s_jarvis_releases_nothing_of_tim_s(tim, serve):
    draft, _ = _reviewed(tim)
    approved = tim.approve(draft)
    tim.submitting(approved)
    with pytest.raises(ports.NotApproved):
        tim.approvals.require_approved_revision(SERVER, MATT, draft.message_id, 1)
    with pytest.raises(ports.NotApproved):
        tim.approvals.require_approved_revision("another-server", TIM, draft.message_id, 1)
    repaired = GuardedTransport(serve.pair(MATT, UUID(int=0x22)), tim.approvals, SERVER, MATT)
    with pytest.raises(ports.NotApproved):
        asyncio.run(repaired.send(approved.request))
    assert serve.messages == {}


def test_a_session_the_team_server_ended_stops_the_sender_and_keeps_the_approval(tim):
    """Revoked pairing or a suspended member is the team server's existing
    authorization (`device`); its answers end the session or refuse for
    good, and the approved envelope stays for when it is restored."""
    draft, _ = _reviewed(tim)
    approved = tim.approve(draft)
    tim.submitting(approved)

    class Revoked:
        async def send(self, request):
            raise ports.TransportError("session_revoked", status=401)

    class Suspended:
        async def send(self, request):
            raise ports.TransportError("membership_suspended", status=403)

    for transport, ended in ((Revoked(), True), (Suspended(), False)):
        guarded = GuardedTransport(transport, tim.approvals, *NS)
        with pytest.raises(ports.TransportError) as e:
            asyncio.run(guarded.send(approved.request))
        assert e.value.session_ended is ended and not e.value.retryable
    assert tim.approvals.require_approved_revision(*NS, draft.message_id, 1) == approved


# -- the capture adapters through the shared orchestration -----------------------------

def test_accepted_on_screen_it_is_approved_at_the_hand_over_and_drawn_going(tim):
    draft, reviewed = _reviewed(tim)
    record, problem, (destination,), speech = tim.stage_and_perform(reviewed.capture_id)
    assert (record, problem) == (str(draft.message_id), None)
    assert destination.status == "sent" and speech.windows == 1
    approved = tim.store.approved(*NS, draft.message_id, 1)
    assert approved.via == "gui" and approved.request.source_node_id == NODE
    assert tim.current(draft).send.state == "approved"
    assert tim.released == [approved], "the sender is woken"
    (event,) = tim.presentation.presented
    assert (event.direction, event.message_id, event.replay) == ("outgoing", draft.message_id,
                                                                 False)
    assert event.event_id == ports.transfer_event_id(draft.message_id, "outgoing")
    assert speech.said == ["Approved, sir. It goes to Matt as soon as the team server takes it."]
    assert not any(w in speech.said[0].lower() for w in ("delivered", "received", "accepted"))
    assert reviewed.capture_id in tim.captures.closed, "sending closes its capture"


def test_heard_in_full_by_voice_it_is_approved_the_voice_way(tim):
    draft, reviewed = _reviewed(tim)
    capture = tim.captures.open[reviewed.capture_id]
    capture["read"] = capture["text"]                       # read_capture heard it
    tim.stage_and_perform(reviewed.capture_id, via="voice", accept=False)
    assert tim.store.approved(*NS, draft.message_id, 1).via == "voice"


def test_a_capture_whose_words_changed_is_refused_at_staging(tim):
    draft, reviewed = _reviewed(tim)
    capture = tim.captures.open[reviewed.capture_id]
    capture["text"] += " And bring the keys."
    record, problem, staged, _ = tim.stage_and_perform(reviewed.capture_id)
    assert record is None and "changed since you reviewed it" in problem and staged == []
    assert tim.store.approved(*NS, draft.message_id, 1) is None


def test_a_draft_edited_after_staging_is_not_approved_at_the_hand_over(tim):
    draft, reviewed = _reviewed(tim)
    capture = tim.captures.open[reviewed.capture_id]
    transmission.accept(capture)
    staged = []
    transmission.stage(HumanRecipient(capture, "gui", tim.messaging), capture,
                       voice=Speech(), put=staged.append)
    tim.edit(tim.current(draft), text="Changed in the window.")
    speech = Speech()
    asyncio.run(transmission.perform(staged[0], speech=speech, cancel_window=2.0,
                                     captures=Box(tim.captures)))
    assert staged[0].status == "failed" and tim.store.unresolved_sends(*NS) == []
    assert "haven't sent it" in speech.said[0] and tim.presentation.presented == []


def test_cancelled_in_the_window_it_stays_awaiting_approval_with_nothing_in_the_outbox(tim):
    draft, reviewed = _reviewed(tim)
    _, _, (destination,), speech = tim.stage_and_perform(reviewed.capture_id,
                                                         speech=Speech(cancelled=True))
    assert destination.status == "cancelled" and speech.said == ["Cancelled, sir."]
    assert tim.current(draft).send.state == "awaiting_approval"
    assert tim.store.unresolved_sends(*NS) == [] and tim.released == []
    assert reviewed.capture_id in tim.captures.open, "his to deal with again"


def test_with_no_voice_nothing_is_staged_unheard(tim):
    draft, reviewed = _reviewed(tim)
    record, problem, staged, _ = tim.stage_and_perform(reviewed.capture_id, speech=False)
    assert record is None and "unheard" in problem and staged == []
    assert tim.current(draft).send.state == "awaiting_approval"


@pytest.mark.parametrize("availability", ["not_started", "not_paired", "unsupported"])
def test_without_messaging_nothing_is_staged(tim, availability):
    draft, reviewed = _reviewed(tim)
    tim.available = availability
    record, problem, staged, _ = tim.stage_and_perform(reviewed.capture_id)
    assert record is None and "isn't available" in problem and staged == []
    _, problem, _, _ = tim.stage_and_perform(reviewed.capture_id, messaging=None)
    assert "isn't available" in problem


def test_a_human_destination_never_waits_a_turn_of_its_own(tim):
    draft, reviewed = _reviewed(tim)
    capture = tim.captures.open[reviewed.capture_id]
    recipient = HumanRecipient(capture, "gui", tim.messaging)
    assert recipient.refusal() is None
    _, destination = recipient.open(capture)
    assert destination.why_wait() is None and destination.priority is False


def test_a_missing_file_copy_at_the_hand_over_is_said_and_nothing_is_approved(tim):
    draft, reviewed = _reviewed(tim, files=[("a.bin", b"abc")])
    capture = tim.captures.open[reviewed.capture_id]
    transmission.accept(capture)
    staged = []
    transmission.stage(HumanRecipient(capture, "gui", tim.messaging), capture,
                       voice=Speech(), put=staged.append)
    tim.snapshots.missing.add(draft.attachments[0].attachment_id)
    speech = Speech()
    asyncio.run(transmission.perform(staged[0], speech=speech, cancel_window=2.0,
                                     captures=Box(tim.captures)))
    assert staged[0].status == "failed" and "Attach it again" in speech.said[0]
    assert tim.store.approved(*NS, draft.message_id, 1) is None


# -- logs -------------------------------------------------------------------------------

def test_logs_carry_ids_and_codes_never_content(tim, caplog):
    caplog.set_level(logging.DEBUG, logger="jarvis.message_approvals")
    draft, reviewed = _reviewed(tim, subject=SECRET_SUBJECT, text=SECRET_TEXT,
                                files=[(SECRET_FILE, b"payroll")])
    tim.stage_and_perform(reviewed.capture_id)
    tim.submit(draft)
    with pytest.raises(ports.NotApproved):
        tim.send(_hand_built(tim, draft, key=UUID(int=1)))
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert str(draft.message_id) in logged
    for secret in (SECRET_TEXT, SECRET_SUBJECT, SECRET_FILE, "4417", "payroll"):
        assert secret not in logged
