"""The human-messaging contract, from JARVIS's side.

The wire rules themselves are tested where they are defined
(`team_server/tests/test_messaging_contract.py`); JARVIS runs them from an
exact copy. Here: the copy, the fixtures and the page's types are current;
the fixtures mean what they say through the copy; the local rules (the
switch, availability, the send states of one approved revision, the
dismissal guard, the presentation event); every test adapter satisfies its
interface; and the named transport scenarios run end to end against the
fake team server, with the contract's own rules deciding.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from tests.fixtures import messaging_fakes as fakes

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "messaging_contract.json").read_text())
TIM, MATT, OUTSIDER = 863470, 1001, 6666
SERVER = FIXTURES["server_id"]


def _generator():
    path = ROOT / "scripts" / "generate_messaging_contract.py"
    spec = importlib.util.spec_from_file_location("generate_messaging_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- generated outputs ----------------------------------------------------------

def test_the_copy_fixtures_and_page_types_are_current():
    assert _generator().stale() == [], "run scripts/generate_messaging_contract.py"


def test_the_copy_is_the_team_servers_source_exactly():
    gen = _generator()
    assert (ROOT / "jarvis" / "messaging_wire.py").read_text() == gen.mirror_text()


def test_fixtures_and_types_name_the_same_revision():
    types = (ROOT / "frontend" / "src" / "messagingContract.ts").read_text()
    assert FIXTURES["interface_revision"] == wire.INTERFACE_REVISION
    assert f'INTERFACE_REVISION = "{wire.INTERFACE_REVISION}"' in types
    for code in wire.ERRORS:
        assert f'"{code}"' in types


# -- the fixtures, through JARVIS's copy -------------------------------------------

@pytest.mark.parametrize("model, case", [(m, c) for m, cases in FIXTURES["valid"].items()
                                         for c in cases])
def test_every_valid_fixture_parses_and_round_trips(model, case):
    value = FIXTURES["valid"][model][case]
    assert getattr(wire, model).model_validate(value).model_dump(mode="json") == value


@pytest.mark.parametrize("model, case", [(m, c) for m, cases in FIXTURES["local_valid"].items()
                                         for c in cases])
def test_every_page_shape_example_parses_and_round_trips(model, case):
    value = FIXTURES["local_valid"][model][case]
    assert getattr(ports, model).model_validate(value).model_dump(mode="json") == value


def test_the_page_examples_cover_self_send_on_one_node_and_two():
    items = FIXTURES["local_valid"]["LocalItem"]
    one = items["self_one_node"]["messages"][0]
    two = items["self_two_nodes"]["messages"][0]
    assert (one["direction"], one["self_message"]) == ("outgoing", True), "served once"
    assert len(one["receipts"]["nodes"]) == 1 and len(two["receipts"]["nodes"]) == 2
    assert items["self_one_node"]["work_state"]["action_owner_github_id"] == TIM


@pytest.mark.parametrize("case", FIXTURES["invalid"], ids=lambda c: f"{c['model']}-{c['case']}")
def test_every_invalid_fixture_is_refused(case):
    with pytest.raises(ValidationError):
        getattr(wire, case["model"]).model_validate(case["value"])


@pytest.mark.parametrize("vector", FIXTURES["digest_vectors"], ids=lambda v: v["case"])
def test_jarvis_computes_the_digest_the_team_server_checks(vector):
    content = wire.MessageContent.model_validate(vector["content"])
    canonical = wire.canonical_bytes(wire.digest_input(
        content, server_id=vector["server_id"], sender_github_id=vector["sender_github_id"]))
    assert canonical.decode() == vector["canonical"]
    assert wire.content_digest(content, server_id=vector["server_id"],
                               sender_github_id=vector["sender_github_id"]) == vector["digest"]


# -- availability ----------------------------------------------------------------------

def test_availability_fails_closed_in_order():
    ok = [wire.CAPABILITY]
    assert ports.availability(paired=False, capabilities=ok) == "not_paired"
    assert ports.availability(paired=True,
                              capabilities=["github-login", "network"]) == "unsupported"
    assert ports.availability(paired=True, capabilities=ok) == "available"
    assert set(ports.AVAILABILITY_SHOWN) == set(ports.Availability.__args__)


# -- one approved revision's send states -----------------------------------------------

def _walk(*events, start="draft"):
    state = start
    for e in events:
        state = ports.next_send_state(state, e)
    return state


def test_the_normal_path_is_review_approve_submit_accepted():
    assert _walk("edit", "review", "approve", "submit", "accepted") == "accepted"


def test_a_lost_answer_is_unknown_and_only_the_same_send_retries_it():
    assert _walk("review", "approve", "submit", "answer_lost") == "accept_unknown"
    assert _walk("review", "approve", "submit", "answer_lost", "retry", "accepted") == "accepted"
    for event in ("edit", "cancel", "approve", "review"):
        with pytest.raises(ports.SendRefused):
            _walk(event, start="accept_unknown")


@pytest.mark.parametrize("state", ["draft", "awaiting_approval", "superseded", "cancelled"])
def test_nothing_unapproved_can_be_submitted(state):
    with pytest.raises(ports.SendRefused):
        ports.next_send_state(state, "submit")


def test_approval_needs_a_review_and_an_edit_supersedes_it():
    with pytest.raises(ports.SendRefused):
        ports.next_send_state("draft", "approve")
    assert _walk("review", "edit") == "superseded"
    assert _walk("review", "approve", "edit") == "superseded"
    assert _walk("review", "approve", "approve") == "approved", "a second click changes nothing"
    assert _walk("review", "review") == "awaiting_approval"
    assert _walk("review", "approve", "submit", "submit") == "submitting"


def _draft(state="draft", revision=1, **kw):
    mid = UUID(int=50)
    base = dict(message_id=mid, thread_id=mid, recipient_github_id=MATT, draft_revision=1,
                text="Friday?", send=ports.LocalSend(message_id=mid, content_revision=revision,
                                                     state=state))
    return ports.Draft(**{**base, **kw})


@pytest.mark.parametrize("state", ["awaiting_approval", "approved", "failed", "cancelled",
                                   "superseded"])
def test_an_edit_after_review_continues_on_the_next_content_revision(state):
    send = ports.revise(_draft(state, revision=3))
    assert (send.content_revision, send.state, send.content_digest) == (4, "draft", None)


def test_an_edit_of_a_draft_keeps_its_revision_and_one_in_flight_is_refused():
    draft = _draft("draft", revision=2)
    assert ports.revise(draft) == draft.send
    for state in ("submitting", "accept_unknown", "accepted"):
        with pytest.raises(ports.SendRefused):
            ports.revise(_draft(state))


def test_the_read_back_names_everything_approved():
    pdf = wire.AttachmentRef(attachment_id=UUID(int=31), filename="plan.pdf",
                             media_type="application/pdf", size_bytes=48213, sha256="a" * 64)
    draft = _draft(subject="Deploy", attachments=[pdf])
    said = ports.human_readback(draft, "Matt")
    assert said == "To Matt.\nSubject: Deploy.\nFriday?\nFiles: plan.pdf, 47.1 kilobytes."
    for changed in (ports.human_readback(draft, "Me"),
                    ports.human_readback(draft.model_copy(update={"subject": "Deploy!"}), "Matt"),
                    ports.human_readback(draft.model_copy(update={"attachments": [
                        pdf.model_copy(update={"size_bytes": 9})]}), "Matt")):
        assert changed != said, "a change the owner would approve undoes the acceptance"
    assert ports.spoken_size(1) == "1 byte" and ports.spoken_size(3 << 20) == "3 megabytes"


def test_after_acceptance_nothing_recalls_it():
    for event in ("edit", "cancel", "retry", "submit", "review"):
        with pytest.raises(ports.SendRefused):
            ports.next_send_state("accepted", event)
    assert ports.next_send_state("accepted", "accepted") == "accepted"


def test_a_refused_send_stays_visible_and_retries_the_same_revision():
    assert _walk("review", "approve", "submit", "refused") == "failed"
    assert "failed" in ports.SEND_UNRESOLVED
    assert _walk("retry", start="failed") == "submitting"


def test_every_state_and_event_is_decided():
    for state in ports.SendState.__args__:
        for event in ports.SendEvent.__args__:
            try:
                assert ports.next_send_state(state, event) in ports.SendState.__args__
            except ports.SendRefused:
                pass


def test_dismissal_is_blocked_by_unsent_work_only():
    assert ports.dismiss_blockers(unsent_text=False, send_states=["accepted", "cancelled"],
                                  uploads_pending=False) == []
    assert ports.dismiss_blockers(unsent_text=True, send_states=["accept_unknown"],
                                  uploads_pending=True) == ["unsent_text", "upload_pending",
                                                            "send_unresolved"]


def test_a_transfer_event_id_is_the_same_on_every_node_and_differs_by_direction():
    m = UUID(int=77)
    assert ports.transfer_event_id(m, "incoming") == ports.transfer_event_id(m, "incoming")
    assert ports.transfer_event_id(m, "incoming") != ports.transfer_event_id(m, "outgoing")
    assert ports.TransferEvent(
        event_id=ports.transfer_event_id(m, "outgoing"), message_id=m, thread_id=m,
        direction="outgoing", owner_github_id=TIM, source_node_id=UUID(int=2),
        sender_github_id=TIM, recipient_github_id=TIM, occurred_at=fakes.T0, replay=False)


def test_a_transfer_event_id_cannot_be_an_agent_message_number():
    good = dict(event_id="human:" + str(fakes.SeqIds().new_id()), message_id=UUID(int=1),
                thread_id=UUID(int=1), direction="incoming", owner_github_id=TIM,
                source_node_id=UUID(int=2),
                sender_github_id=MATT, recipient_github_id=TIM, occurred_at=fakes.T0,
                replay=False)
    ports.TransferEvent(**good)
    for bad in ("7", "agent:7", "human:7"):
        with pytest.raises(ValidationError):
            ports.TransferEvent(**{**good, "event_id": bad})


def test_transport_errors_retry_only_what_the_table_says():
    assert ports.TransportError("transport").retryable
    assert ports.TransportError("rate_limited").retryable
    assert ports.TransportError("not_ready").retryable, "a starting server is not a refusal"
    assert not ports.TransportError("idempotency_conflict").retryable
    assert not ports.TransportError("digest_mismatch").retryable


def test_an_unknown_code_is_judged_by_its_status():
    assert ports.TransportError("something_new", status=502).retryable
    assert not ports.TransportError("something_new", status=400).retryable
    ended = ports.TransportError("something_new", status=401)
    assert ended.session_ended and not ended.retryable
    for code in ("session_missing", "session_expired", "session_revoked", "session_invalid"):
        assert ports.TransportError(code).session_ended
    assert not ports.TransportError("membership_suspended").session_ended


def test_the_page_can_word_every_local_code():
    types = (ROOT / "frontend" / "src" / "messagingContract.ts").read_text()
    for code in [*ports.LOCAL_ERRORS, *ports.DISMISS_BLOCKERS_SHOWN]:
        assert f"{code}:" in types
    assert set(ports.DISMISS_BLOCKERS_SHOWN) == set(ports.DismissBlocker.__args__)
    assert not set(ports.LOCAL_ERRORS) & set(wire.ERRORS)


# -- the test adapters satisfy the interfaces ------------------------------------------

def test_every_adapter_satisfies_its_interface():
    serve = fakes.FakeServe(server_id=SERVER, members={TIM}, clock=fakes.FixedClock())
    assert isinstance(serve.pair(TIM, UUID(int=1)), ports.ServeTransport)
    store = fakes.MemoryStore()
    assert isinstance(store, ports.LocalMessagingStore)
    assert isinstance(fakes.TableGuard(store, ids=fakes.SeqIds(), clock=fakes.FixedClock()),
                      ports.ApprovalGuard)
    assert isinstance(fakes.StaticSnapshots(), ports.AttachmentSnapshots)
    assert isinstance(fakes.RecordingPresentation(serve.pair(TIM, UUID(int=1))),
                      ports.PresentationDispatcher)
    assert isinstance(fakes.SeqIds(), ports.IdSource)
    assert isinstance(fakes.SeqRandom(), ports.RandomSource)


# -- named transport scenarios against the fake team server ------------------------------

class Owner:
    """One paired node of one owner, composing sends the way the approval
    path will: normalized content, digest over the session's identity, a
    key reserved at approval."""

    def __init__(self, serve, github_id, node):
        self.github_id, self.node = github_id, UUID(int=node)
        self.t = serve.pair(github_id, self.node)
        self.ids = fakes.SeqIds(start=node * 1000)

    def request(self, to, text, *, reply_to=None, related=(), revision=1, message_id=None,
                key=None):
        mid = message_id or self.ids.new_id()
        content = wire.MessageContent(
            message_id=mid, thread_id=reply_to.thread_id if reply_to else mid,
            in_reply_to_message_id=reply_to.message_id if reply_to else None,
            related_message_ids=list(related), recipient_github_id=to,
            content_revision=revision, text=wire.normalize_text(text))
        return wire.SendRequest(**content.model_dump(), source_node_id=self.node,
                                idempotency_key=key or self.ids.new_id(),
                                content_digest=wire.content_digest(
                                    content, server_id=SERVER, sender_github_id=self.github_id))

    def send(self, *a, **k):
        return asyncio.run(self.t.send(self.request(*a, **k))).message

    def scan(self, limit=wire.SYNC_PAGE_DEFAULT):
        entries, continuation = [], None
        while True:
            page = asyncio.run(self.t.sync(self.node, continuation, limit))
            entries += page.entries
            if page.scan_complete:
                return entries
            continuation = page.continuation

    def receipt(self, message_id, kind):
        """One receipt, as a batch of one: the facts, or None if not found."""
        result = asyncio.run(self.t.receipts(wire.ReceiptBatch(
            node_id=self.node, receipts=[wire.ReceiptItem(message_id=message_id, kind=kind)])))
        return result.facts[0] if result.facts else None


@pytest.fixture
def team():
    clock = fakes.FixedClock()
    faults = wire.ArmedFaults()
    serve = fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=clock, faults=faults)
    return serve, clock, faults


def test_self_one_node_is_one_item_your_turn_and_unread_until_opened(team):
    serve, clock, _ = team
    tim = Owner(serve, TIM, 1)
    m = tim.send(TIM, "Remember the deploy.")

    (entry,) = tim.scan()
    item = entry.work_state
    assert len(serve.work) == 1, "one item, not an outgoing and an incoming one"
    assert (item.placement, item.action_owner_github_id, item.unread_reminder) == \
        ("queued", TIM, True)
    assert entry.receipts.received_at is None and entry.receipts.read_at is None, \
        "acceptance is not a receipt"
    clock.advance(seconds=5)
    facts = tim.receipt(m.message_id, "received")
    assert facts.received_at == clock() and facts.read_at is None
    assert serve.work[(TIM, m.thread_id)] == item, "a receipt never moves the item"


def test_self_two_nodes_converge_on_one_item_and_each_node_acknowledges(team):
    serve, clock, _ = team
    laptop, desk = Owner(serve, TIM, 1), Owner(serve, TIM, 2)
    m = laptop.send(TIM, "Note to self.")
    first = laptop.receipt(m.message_id, "received")
    clock.advance(minutes=3)                   # the desk was offline
    (entry,) = desk.scan()
    second = desk.receipt(m.message_id, "received")
    assert second.received_at == first.received_at, "the first receipt time never moves"
    assert [n.node_id for n in second.nodes] == [laptop.node, desk.node]
    again = desk.receipt(m.message_id, "received")
    assert again.revision == second.revision, "a repeated receipt changes nothing"
    assert entry.work_state == laptop.scan()[0].work_state


def test_two_owner_isolation(team):
    serve, _, _ = team
    serve.members.add(OUTSIDER)
    tim, matt, eve = Owner(serve, TIM, 1), Owner(serve, MATT, 2), Owner(serve, OUTSIDER, 3)
    m = tim.send(MATT, "Private.")
    assert eve.scan() == []
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(eve.t.get(m.message_id))
    assert e.value.code == "message_not_found"
    assert eve.receipt(m.message_id, "read") is None, "not found, and nothing disclosed"
    with pytest.raises(ports.TransportError) as e:
        eve.send(MATT, "Me too.", related=[m.message_id])
    assert e.value.code == "relation_not_allowed", "a link never grants access"
    with pytest.raises(ports.TransportError) as e:
        eve.send(TIM, "Hijack.", reply_to=m)
    assert e.value.code == "relation_not_allowed"
    assert tim.receipt(m.message_id, "read") is None, "the sender's own read is no receipt"
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(eve.t.sync(tim.node, None, 5))
    assert e.value.code == "source_node_not_owned", "a scan is only ever of one's own node"
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(matt.t.send(tim.request(MATT, "Forged node.")))
    assert e.value.code == "source_node_not_owned"


def test_lost_accept_response_retries_to_one_message(team):
    serve, _, faults = team
    tim = Owner(serve, TIM, 1)
    req = tim.request(MATT, "Once only.")
    faults.arm("server_after_commit_before_response")
    with pytest.raises(wire.InjectedFault) as e:
        asyncio.run(tim.t.send(req))
    assert e.value.point == "server_after_commit_before_response"
    result = asyncio.run(tim.t.send(req))
    assert result.duplicate and len(serve.messages) == 1
    assert result.message.message_id == req.message_id


def test_same_key_with_changed_content_is_a_conflict(team):
    serve, _, _ = team
    tim = Owner(serve, TIM, 1)
    first = tim.request(MATT, "Friday.")
    asyncio.run(tim.t.send(first))
    changed = tim.request(MATT, "Saturday.", message_id=first.message_id,
                          key=first.idempotency_key, revision=2)
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(tim.t.send(changed))
    assert e.value.code == "idempotency_conflict"
    forged = first.model_copy(update={"text": "Saturday."})
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(tim.t.send(forged))
    assert e.value.code == "digest_mismatch", "the bytes approved are the bytes accepted"


def test_reordered_receipts_keep_both_first_facts(team):
    serve, clock, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    m = tim.send(MATT, "Read me.")
    clock.advance(minutes=1)
    read = matt.receipt(m.message_id, "read")
    assert read.read_at == clock() and read.received_at is None
    clock.advance(minutes=1)
    both = matt.receipt(m.message_id, "received")
    assert both.read_at == read.read_at and both.received_at == clock()


def test_late_publication_behind_the_cursor_is_found_by_the_next_scan(team):
    serve, clock, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    for n in range(3):
        clock.advance(seconds=1)
        tim.send(MATT, f"m{n}")
    page = asyncio.run(matt.t.sync(matt.node, None, 2))
    early = clock() - timedelta(seconds=10)
    late_clock = fakes.FixedClock(early)
    serve.clock = late_clock
    late = tim.send(MATT, "accepted earlier, published later")
    serve.clock = clock
    rest = asyncio.run(matt.t.sync(matt.node, page.continuation, 2))
    seen = {e.message.message_id for e in page.entries + rest.entries}
    assert late.message_id not in seen
    assert late.message_id in {e.message.message_id for e in matt.scan()}


def test_a_reply_returns_the_turn_and_an_outgoing_reply_gives_it_away(team):
    serve, clock, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    root = tim.send(MATT, "Can we move the deploy?")
    assert serve.work[(TIM, root.thread_id)].action_owner_github_id == MATT
    assert serve.work[(MATT, root.thread_id)].action_owner_github_id == MATT
    clock.advance(minutes=1)
    reply = matt.send(TIM, "Friday works.", reply_to=root)
    tims = serve.work[(TIM, root.thread_id)]
    assert reply.thread_id == root.thread_id and len(serve.work) == 2, "one item each"
    assert (tims.action_owner_github_id, tims.unread_reminder) == (TIM, True)
    assert serve.work[(MATT, root.thread_id)].action_owner_github_id == TIM


def test_a_stale_work_state_change_is_refused_with_the_latest(team):
    serve, _, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    m = tim.send(MATT, "Hello.")
    item = serve.work[(MATT, m.thread_id)]
    change = wire.WorkCommandRequest(change=wire.OpenItem(expected_revision=item.revision))
    opened = asyncio.run(matt.t.change_work_state(m.thread_id, change))
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(matt.t.change_work_state(m.thread_id, wire.WorkCommandRequest(
            change=wire.Dismiss(expected_revision=item.revision))))
    assert e.value.code == "revision_conflict"
    assert e.value.latest["revision"] == opened.revision


def test_a_continuation_belongs_to_one_node(team):
    serve, _, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    desk = serve.pair(MATT, UUID(int=3))
    for n in range(3):
        tim.send(MATT, f"m{n}")
    page = asyncio.run(matt.t.sync(matt.node, None, 1))
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(desk.sync(UUID(int=3), page.continuation, 1))
    assert e.value.code == "sync_restart", "one node's scan never advances another's"


def test_the_send_result_carries_the_senders_item_at_the_servers_revision(team):
    serve, _, _ = team
    tim = Owner(serve, TIM, 1)
    req = tim.request(MATT, "Hello.")
    result = asyncio.run(tim.t.send(req))
    assert result.work_state == serve.work[(TIM, result.message.message_id)]
    assert (result.work_state.placement, result.receipts.received_at) == ("active", None)
    again = asyncio.run(tim.t.send(req))
    assert again.duplicate and (again.message, again.work_state) == (result.message,
                                                                     result.work_state)


def test_a_message_id_someone_else_used_is_a_conflict_that_discloses_nothing(team):
    serve, _, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    m = tim.send(MATT, "Mine.")
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(matt.t.send(matt.request(TIM, "Mine too.", message_id=m.message_id)))
    assert e.value.code == "idempotency_conflict" and e.value.latest is None


def test_projections_are_derived_once_even_when_a_retry_repairs_them(team):
    serve, _, faults = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    root = tim.send(MATT, "Root.")
    req = matt.request(TIM, "Reply.", reply_to=root)
    faults.arm("server_after_insert_before_projection")
    with pytest.raises(wire.InjectedFault):
        asyncio.run(matt.t.send(req))
    assert serve.work[(TIM, root.thread_id)].action_owner_github_id == MATT, "not yet derived"
    result = asyncio.run(matt.t.send(req))
    item = serve.work[(TIM, root.thread_id)]
    assert result.duplicate and item.action_owner_github_id == TIM
    asyncio.run(matt.t.send(req))
    assert serve.work[(TIM, root.thread_id)] == item, "applied once, however often repaired"


# -- the local store keeps the higher revision ------------------------------------

NS = (SERVER, TIM)


def _entry(serve, owner, m):
    return wire.SyncEntry(message=m, receipts=serve.receipts[m.message_id],
                          work_state=serve.work[(owner, m.thread_id)])


def test_an_older_entry_never_rolls_back_a_newer_work_state(team):
    serve, _, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    store = fakes.MemoryStore()
    root = matt.send(TIM, "One.")
    old = _entry(serve, TIM, root)
    reply = matt.send(TIM, "Two.", reply_to=root)
    new = _entry(serve, TIM, reply)
    page = lambda *entries: wire.SyncPage(epoch="e1", entries=list(entries), scan_complete=True,
                                          server_time=fakes.T0)
    assert store.store_page(*NS, tim.node, page(new)) == [reply.message_id]
    assert store.store_page(*NS, tim.node, page(old)) == [root.message_id]
    stored = store.item(*NS, root.thread_id)
    assert stored.work_state == new.work_state, "kept per thread, the higher revision"
    assert [m.message.message_id for m in stored.messages] == [root.message_id, reply.message_id]
    assert store.store_page(*NS, tim.node, page(new)) == [], "first time only"
    assert {o.message_id for o in store.receipts_owed(*NS)} == {root.message_id,
                                                                reply.message_id}


def test_a_page_and_its_continuation_are_stored_together_and_own_sends_are_not_new(team):
    serve, _, _ = team
    laptop, desk = Owner(serve, TIM, 1), Owner(serve, TIM, 2)
    store = fakes.MemoryStore()
    sent = laptop.send(MATT, "From the laptop.")
    page = asyncio.run(desk.t.sync(desk.node, None, 1))
    assert store.store_page(*NS, desk.node, page) == [], "sent from another node: stored, not new"
    assert store.message(*NS, sent.message_id).direction == "outgoing"
    assert store.sync_position(*NS, desk.node) is None, "a complete scan restarts"


def test_receipts_owed_are_settled_by_the_batch_answer(team):
    serve, _, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    store = fakes.MemoryStore()
    m = matt.send(TIM, "Hello.")
    store.store_page(*NS, tim.node, wire.SyncPage(epoch="e1", entries=[_entry(serve, TIM, m)],
                                                  scan_complete=True, server_time=fakes.T0))
    store.owe_receipt(*NS, m.message_id, "read")
    owed = store.receipts_owed(*NS)
    assert {(o.message_id, o.kind) for o in owed} == {(m.message_id, "received"),
                                                      (m.message_id, "read")}
    result = asyncio.run(tim.t.receipts(wire.ReceiptBatch(node_id=tim.node, receipts=[
        wire.ReceiptItem(message_id=o.message_id, kind=o.kind) for o in owed])))
    store.settle_receipts(*NS, owed, result)
    assert store.receipts_owed(*NS) == []
    facts = store.message(*NS, m.message_id).receipts
    assert facts.received_at and facts.read_at


# -- drafts, review, approval and the guarded send ---------------------------------

def _new_draft(ids, to=MATT, text="Friday?"):
    mid = ids.new_id()
    return ports.Draft(message_id=mid, thread_id=mid, recipient_github_id=to, draft_revision=1,
                       text=wire.normalize_text(text),
                       send=ports.LocalSend(message_id=mid, content_revision=1, state="draft"))


def test_a_stale_draft_save_is_a_conflict_carrying_the_latest():
    store, ids = fakes.MemoryStore(), fakes.SeqIds()
    draft = store.create_draft(*NS, _new_draft(ids))
    agent = store.save_draft(*NS, draft.model_copy(update={"text": "Agent's words."}), 1)
    with pytest.raises(wire.ContractError) as e:
        store.save_draft(*NS, draft.model_copy(update={"text": "Page's words."}), 1)
    assert e.value.code == "revision_conflict" and e.value.latest == agent
    assert store.draft(*NS, draft.message_id).text == "Agent's words."


def test_a_reviewed_draft_is_approved_once_and_sent_through_the_guard(team):
    serve, clock, _ = team
    tim = Owner(serve, TIM, 1)
    store, ids = fakes.MemoryStore(), fakes.SeqIds(start=900)
    guard = fakes.TableGuard(store, ids=ids, clock=clock)
    draft = store.create_draft(*NS, _new_draft(ids))
    with pytest.raises(ports.NotApproved):
        guard.require_approved_revision(*NS, draft.message_id, 1)
    reviewed = guard.request_review(*NS, draft.message_id, 1)
    assert reviewed.send.state == "awaiting_approval"
    assert store.unresolved_sends(*NS) == [], "nothing is in the outbox before the yes"
    approved = guard.approve(*NS, draft.message_id, 1, reviewed.send.content_digest, tim.node,
                             "gui")
    assert guard.approve(*NS, draft.message_id, 1, reviewed.send.content_digest, tim.node,
                         "gui") == approved, "a repeated yes changes nothing"
    with pytest.raises(ports.NotApproved):
        guard.require_approved_revision(SERVER, MATT, draft.message_id, 1)
    released = guard.require_approved_revision(*NS, draft.message_id, 1)
    result = asyncio.run(tim.t.send(released.request))
    assert store.record_accepted(*NS, result) is False, "to Matt: nothing incoming here"
    assert store.draft(*NS, draft.message_id).send.state == "accepted"
    assert store.item(*NS, draft.thread_id).work_state.action_owner_github_id == MATT


def test_a_stale_digest_cannot_be_approved():
    store, ids, clock = fakes.MemoryStore(), fakes.SeqIds(), fakes.FixedClock()
    guard = fakes.TableGuard(store, ids=ids, clock=clock)
    draft = store.create_draft(*NS, _new_draft(ids))
    guard.request_review(*NS, draft.message_id, 1)
    with pytest.raises(ports.NotApproved):
        guard.approve(*NS, draft.message_id, 1, "sha256:" + "0" * 64, UUID(int=1), "voice")


def test_a_message_to_oneself_is_stored_as_incoming_by_the_node_that_sent_it(team):
    serve, clock, _ = team
    tim = Owner(serve, TIM, 1)
    store = fakes.MemoryStore()
    result = asyncio.run(tim.t.send(tim.request(TIM, "Note to self.")))
    assert store.record_accepted(*NS, result) is True, "the first incoming cue is legitimate"
    assert store.record_accepted(*NS, result) is False
    assert [o.kind for o in store.receipts_owed(*NS)] == ["received"]
    assert store.announcements_due(*NS) == [result.message.message_id]
    page = asyncio.run(tim.t.sync(tim.node, None, 5))
    assert store.store_page(*NS, tim.node, page) == [], "no second arrival from the scan"
    local = store.message(*NS, result.message.message_id)
    assert (local.direction, local.self_message) == ("outgoing", True)


def test_the_queue_shows_live_work_and_unsent_conversations_and_pages_dismissed_ones(team):
    serve, _, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    store, ids = fakes.MemoryStore(), fakes.SeqIds(start=500)
    for n in range(3):
        m = matt.send(TIM, f"m{n}")
        store.store_page(*NS, tim.node, wire.SyncPage(
            epoch="e", entries=[_entry(serve, TIM, m)], scan_complete=True, server_time=fakes.T0))
    first = next(iter(store.work.values()))
    store.save_work_state(*NS, asyncio.run(tim.t.change_work_state(
        first.thread_id, wire.WorkCommandRequest(change=wire.Dismiss(
            expected_revision=first.revision)))))
    unsent = store.create_draft(*NS, _new_draft(ids))
    snap = store.snapshot(*NS, availability="available", stale=False)
    assert first.thread_id not in {i.work_state.thread_id for i in snap.items}
    assert (len(snap.items), snap.history_count, snap.unsent) == (2, 1, [unsent])
    assert (snap.server_id, snap.owner_github_id) == NS
    page = store.history(*NS, None, 1)
    assert [i.work_state.thread_id for i in page.items] == [first.thread_id]
    assert page.continuation is None


def test_unsent_text_and_an_unresolved_send_block_dismissal():
    store, ids = fakes.MemoryStore(), fakes.SeqIds()
    draft = _new_draft(ids)
    m = wire.AcceptedMessage(**ports.draft_content(draft).model_dump(),
                             server_id=SERVER, sender_github_id=MATT, source_node_id=UUID(int=2),
                             content_digest="sha256:" + "0" * 64, idempotency_key=UUID(int=3),
                             accepted_at=fakes.T0)
    m = m.model_copy(update={"recipient_github_id": TIM})
    store.save_work_state(*NS, wire.new_work_state(server_id=SERVER, owner_github_id=TIM,
                                                   message=m))
    reply_id = ids.new_id()
    store.create_draft(*NS, draft.model_copy(update={
        "message_id": reply_id, "in_reply_to_message_id": m.message_id,
        "send": draft.send.model_copy(update={"message_id": reply_id, "state": "failed"})}))
    assert store.item(*NS, m.thread_id).dismiss_blockers == ["send_unresolved"]


# -- the announcement lease --------------------------------------------------------

def _arrival(serve, clock):
    tim_laptop, tim_desk, matt = (Owner(serve, TIM, 1), Owner(serve, TIM, 2),
                                  Owner(serve, MATT, 3))
    m = matt.send(TIM, "Lunch?")
    event = ports.TransferEvent(
        event_id=ports.transfer_event_id(m.message_id, "incoming"), message_id=m.message_id,
        thread_id=m.thread_id, direction="incoming", owner_github_id=TIM,
        source_node_id=matt.node, sender_github_id=MATT, recipient_github_id=TIM,
        occurred_at=clock(), replay=False)
    return (fakes.RecordingPresentation(tim_laptop.t), fakes.RecordingPresentation(tim_desk.t),
            fakes.RecordingPresentation(matt.t), event)


def test_one_of_the_owners_nodes_announces_and_a_crash_may_repeat_it(team):
    serve, clock, _ = team
    laptop, desk, _, event = _arrival(serve, clock)
    token = asyncio.run(laptop.reserve_announcement(event))
    assert token is not None
    assert asyncio.run(desk.reserve_announcement(event)) is None, "held by the laptop"
    assert asyncio.run(laptop.reserve_announcement(event)) == token, "a repeat by its holder"
    asyncio.run(laptop.attempted(event, token))
    clock.advance(seconds=fakes.LEASE_SEC + 1)          # the laptop died mid-sentence
    again = asyncio.run(desk.reserve_announcement(event))
    assert again is not None and again != token, "no exactly-once claim: it may be said twice"
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(laptop.complete_announcement(event, token))
    assert e.value.code == "announcement_claimed" and e.value.latest["lease_token"] is None
    asyncio.run(desk.complete_announcement(event, again))
    clock.advance(hours=1)
    assert asyncio.run(laptop.reserve_announcement(event)) is None, "done is done"


def test_only_the_recipient_holds_an_announcement_lease(team):
    serve, clock, _ = team
    _, _, sender, event = _arrival(serve, clock)
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(sender.reserve_announcement(event))
    assert e.value.code == "message_not_found"


# -- files ---------------------------------------------------------------------------

async def _chunks(data: bytes):
    yield data


def _staged(owner, message_id, data, attachment_id=None):
    import hashlib
    ref = wire.AttachmentRef(attachment_id=attachment_id or owner.ids.new_id(),
                             filename="plan.pdf", media_type="application/pdf",
                             size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    asyncio.run(owner.t.create_attachment(wire.AttachmentCreate(
        attachment_id=ref.attachment_id, message_id=message_id, size_bytes=ref.size_bytes,
        sha256=ref.sha256)))
    return ref


def _with_files(owner, to, refs, message_id):
    content = wire.MessageContent(message_id=message_id, thread_id=message_id,
                                  recipient_github_id=to, content_revision=1, attachments=refs)
    return wire.SendRequest(**content.model_dump(), source_node_id=owner.node,
                            idempotency_key=owner.ids.new_id(),
                            content_digest=wire.content_digest(content, server_id=SERVER,
                                                               sender_github_id=owner.github_id))


def test_a_file_must_be_uploaded_and_verified_before_its_message_is_accepted(team):
    serve, _, _ = team
    tim, matt, eve = Owner(serve, TIM, 1), Owner(serve, MATT, 2), Owner(serve, OUTSIDER, 3)
    serve.members.add(OUTSIDER)
    mid = tim.ids.new_id()
    ref = _staged(tim, mid, b"the plan")
    req = _with_files(tim, MATT, [ref], mid)
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(tim.t.send(req))
    assert e.value.code == "attachment_not_ready"
    asyncio.run(tim.t.upload_attachment(ref.attachment_id, _chunks(b"the plan")))
    assert asyncio.run(tim.t.finalize_attachment(ref.attachment_id)).state == "verified"
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(eve.t.attachment_status(ref.attachment_id))
    assert e.value.code == "attachment_not_found", "staging is the owner's alone"
    m = asyncio.run(tim.t.send(req)).message
    assert serve.staging[ref.attachment_id]["state"] == "published"

    async def fetch(owner):
        return b"".join([c async for c in owner.t.download_attachment(m.message_id,
                                                                       ref.attachment_id)])
    assert asyncio.run(fetch(matt)) == b"the plan"
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(fetch(eve))
    assert e.value.code == "attachment_not_found", "an id alone is no permission"


def test_bytes_that_are_not_the_approved_ones_never_verify(team):
    serve, _, _ = team
    tim = Owner(serve, TIM, 1)
    ref = _staged(tim, tim.ids.new_id(), b"approved bytes")
    asyncio.run(tim.t.upload_attachment(ref.attachment_id, _chunks(b"other bytes!!!")))
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(tim.t.finalize_attachment(ref.attachment_id))
    assert e.value.code == "attachment_mismatch"


def test_a_crash_after_pinning_leaves_no_message_and_the_retry_resumes(team):
    serve, _, faults = team
    tim = Owner(serve, TIM, 1)
    mid = tim.ids.new_id()
    ref = _staged(tim, mid, b"x")
    asyncio.run(tim.t.upload_attachment(ref.attachment_id, _chunks(b"x")))
    asyncio.run(tim.t.finalize_attachment(ref.attachment_id))
    req = _with_files(tim, MATT, [ref], mid)
    faults.arm("server_after_pin_before_insert")
    with pytest.raises(wire.InjectedFault):
        asyncio.run(tim.t.send(req))
    assert serve.messages == {} and serve.staging[ref.attachment_id]["state"] == "pinned"
    assert asyncio.run(tim.t.send(req)).duplicate is False
    assert len(serve.messages) == 1


def test_the_recipient_list_is_the_roster_and_includes_me(team):
    serve, _, _ = team
    tim = Owner(serve, TIM, 1)
    ids = [m.github_id for m in asyncio.run(tim.t.recipients()).members]
    assert TIM in ids and MATT in ids
