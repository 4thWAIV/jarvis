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


# -- the switch and availability ------------------------------------------------------

@pytest.mark.parametrize("value, on", [("", False), ("0", False), ("no", False), ("1", True),
                                       ("TRUE", True), (" on ", True), ("yes", True)])
def test_messaging_is_off_unless_switched_on(value, on):
    assert ports.messaging_enabled({ports.ENABLE_FLAG: value}) is on
    assert ports.messaging_enabled({}) is False


def test_availability_fails_closed_in_order():
    ok = [wire.CAPABILITY]
    assert ports.availability(enabled=False, paired=True, capabilities=ok) == "switched_off"
    assert ports.availability(enabled=True, paired=False, capabilities=ok) == "not_paired"
    assert ports.availability(enabled=True, paired=True,
                              capabilities=["github-login", "network"]) == "unsupported"
    assert ports.availability(enabled=True, paired=True, capabilities=ok) == "available"
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


def test_a_transfer_event_id_cannot_be_an_agent_message_number():
    good = dict(event_id="human:" + str(fakes.SeqIds().new_id()), message_id=UUID(int=1),
                thread_id=UUID(int=1), direction="incoming", source_node_id=UUID(int=2),
                sender_github_id=MATT, recipient_github_id=TIM, occurred_at=fakes.T0,
                replay=False)
    ports.TransferEvent(**good)
    for bad in ("7", "agent:7", "human:7"):
        with pytest.raises(ValidationError):
            ports.TransferEvent(**{**good, "event_id": bad})


def test_transport_errors_retry_only_what_the_table_says():
    assert ports.TransportError("transport").retryable
    assert ports.TransportError("rate_limited").retryable
    assert not ports.TransportError("idempotency_conflict").retryable
    assert not ports.TransportError("digest_mismatch").retryable


# -- the test adapters satisfy the interfaces ------------------------------------------

def test_every_adapter_satisfies_its_interface():
    serve = fakes.FakeServe(server_id=SERVER, members={TIM}, clock=fakes.FixedClock())
    assert isinstance(serve.pair(TIM, UUID(int=1)), ports.ServeTransport)
    assert isinstance(fakes.MemoryStore(), ports.LocalMessagingStore)
    assert isinstance(fakes.TableGuard(), ports.ApprovalGuard)
    assert isinstance(fakes.StaticSnapshots(), ports.AttachmentSnapshots)
    assert isinstance(fakes.RecordingPresentation(), ports.PresentationDispatcher)
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
            page = asyncio.run(self.t.sync(continuation, limit))
            entries += page.entries
            if page.scan_complete:
                return entries
            continuation = page.continuation


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
    facts = asyncio.run(tim.t.receipt(m.message_id, wire.ReceiptRequest(kind="received",
                                                                        node_id=tim.node)))
    assert facts.received_at == clock() and facts.read_at is None
    assert serve.work[(TIM, m.thread_id)] == item, "a receipt never moves the item"


def test_self_two_nodes_converge_on_one_item_and_each_node_acknowledges(team):
    serve, clock, _ = team
    laptop, desk = Owner(serve, TIM, 1), Owner(serve, TIM, 2)
    m = laptop.send(TIM, "Note to self.")
    first = asyncio.run(laptop.t.receipt(m.message_id, wire.ReceiptRequest(
        kind="received", node_id=laptop.node)))
    clock.advance(minutes=3)                   # the desk was offline
    (entry,) = desk.scan()
    second = asyncio.run(desk.t.receipt(m.message_id, wire.ReceiptRequest(
        kind="received", node_id=desk.node)))
    assert second.received_at == first.received_at, "the first receipt time never moves"
    assert [n.node_id for n in second.nodes] == [laptop.node, desk.node]
    again = asyncio.run(desk.t.receipt(m.message_id, wire.ReceiptRequest(
        kind="received", node_id=desk.node)))
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
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(eve.t.receipt(m.message_id, wire.ReceiptRequest(kind="read",
                                                                     node_id=eve.node)))
    assert e.value.code == "message_not_found"
    with pytest.raises(ports.TransportError) as e:
        eve.send(MATT, "Me too.", related=[m.message_id])
    assert e.value.code == "relation_not_allowed", "a link never grants access"
    with pytest.raises(ports.TransportError) as e:
        eve.send(TIM, "Hijack.", reply_to=m)
    assert e.value.code == "relation_not_allowed"
    with pytest.raises(ports.TransportError) as e:
        asyncio.run(tim.t.receipt(m.message_id, wire.ReceiptRequest(kind="read",
                                                                     node_id=tim.node)))
    assert e.value.code == "message_not_found", "the sender's own read is no receipt"
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
    read = asyncio.run(matt.t.receipt(m.message_id, wire.ReceiptRequest(kind="read",
                                                                        node_id=matt.node)))
    assert read.read_at == clock() and read.received_at is None
    clock.advance(minutes=1)
    both = asyncio.run(matt.t.receipt(m.message_id, wire.ReceiptRequest(kind="received",
                                                                        node_id=matt.node)))
    assert both.read_at == read.read_at and both.received_at == clock()


def test_late_publication_behind_the_cursor_is_found_by_the_next_scan(team):
    serve, clock, _ = team
    tim, matt = Owner(serve, TIM, 1), Owner(serve, MATT, 2)
    for n in range(3):
        clock.advance(seconds=1)
        tim.send(MATT, f"m{n}")
    page = asyncio.run(matt.t.sync(None, 2))
    early = clock() - timedelta(seconds=10)
    late_clock = fakes.FixedClock(early)
    serve.clock = late_clock
    late = tim.send(MATT, "accepted earlier, published later")
    serve.clock = clock
    rest = asyncio.run(matt.t.sync(page.continuation, 2))
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
