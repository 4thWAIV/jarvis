"""The local messaging store (SQLite under the test's own data dir): the
contract's store rules, checked against the contract's MemoryStore too so
the two cannot drift, then what only a durable store can show: every
transition survives closing and reopening, pages and their continuation are
one transaction, namespaces never mix, and the schema migrates once."""
from __future__ import annotations

import asyncio
import sqlite3
from uuid import UUID

import pytest

from jarvis import data_paths
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.messaging_store import SCHEMA_VERSION, SqliteMessagingStore
from tests.fixtures import messaging_fakes as fakes

SERVER, OTHER_SERVER = "server-1", "server-2"
TIM, MATT = 863470, 1001
NS = (SERVER, TIM)


def sqlite_store():
    return SqliteMessagingStore()


@pytest.fixture(params=["memory", "sqlite"])
def store(request):
    return fakes.MemoryStore() if request.param == "memory" else sqlite_store()


@pytest.fixture
def serve():
    return fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=fakes.FixedClock())


def send(serve, sender, node, to, text, reply_to=None):
    t = serve.pair(sender, UUID(int=node))
    mid = serve.ids.new_id()
    content = wire.MessageContent(
        message_id=mid, thread_id=reply_to.thread_id if reply_to else mid,
        in_reply_to_message_id=reply_to.message_id if reply_to else None,
        recipient_github_id=to, content_revision=1, text=text)
    req = wire.SendRequest(**content.model_dump(), source_node_id=UUID(int=node),
                           idempotency_key=serve.ids.new_id(),
                           content_digest=wire.content_digest(content, server_id=SERVER,
                                                              sender_github_id=sender))
    return asyncio.run(t.send(req))


def page(*entries, continuation=None):
    return wire.SyncPage(epoch="e1", entries=list(entries), continuation=continuation,
                         scan_complete=continuation is None, server_time=fakes.T0)


def entry(serve, owner, m):
    return wire.SyncEntry(message=m, receipts=serve.receipts[m.message_id],
                          work_state=serve.work[(owner, m.thread_id)])


def new_draft(ids, to=MATT, text="Friday?"):
    mid = ids.new_id()
    return ports.Draft(message_id=mid, thread_id=mid, recipient_github_id=to, draft_revision=1,
                       text=text, send=ports.LocalSend(message_id=mid, content_revision=1,
                                                       state="draft"))


# -- the contract's rules, on both stores ------------------------------------------

def test_an_older_entry_never_rolls_back_and_an_arrival_is_new_once(store, serve):
    root = send(serve, MATT, 2, TIM, "One.").message
    old = entry(serve, TIM, root)
    reply = send(serve, MATT, 2, TIM, "Two.", reply_to=root).message
    new = entry(serve, TIM, reply)
    node = UUID(int=1)
    assert store.store_page(*NS, node, page(new)) == [reply.message_id]
    assert store.store_page(*NS, node, page(old)) == [root.message_id]
    stored = store.item(*NS, root.thread_id)
    assert stored.work_state == new.work_state
    assert [m.message.message_id for m in stored.messages] == [root.message_id, reply.message_id]
    assert store.store_page(*NS, node, page(new)) == []
    assert {o.message_id for o in store.receipts_owed(*NS)} == {root.message_id,
                                                                reply.message_id}
    assert set(store.announcements_due(*NS)) == {root.message_id, reply.message_id}


def test_an_accepted_send_is_one_record_and_to_oneself_is_also_incoming_once(store, serve):
    ids, clock = fakes.SeqIds(start=900), fakes.FixedClock()
    guard = fakes.TableGuard(store, ids=ids, clock=clock)
    draft = store.create_draft(*NS, new_draft(ids, to=TIM, text="Note to self."))
    reviewed = guard.request_review(*NS, draft.message_id, 1)
    approved = guard.approve(*NS, draft.message_id, 1, reviewed.send.content_digest,
                             UUID(int=1), "gui")
    assert [s.state for s in store.unresolved_sends(*NS)] == ["approved"]
    result = asyncio.run(serve.pair(TIM, UUID(int=1)).send(approved.request))
    assert store.record_accepted(*NS, result) is True
    assert store.record_accepted(*NS, result) is False
    assert store.draft(*NS, draft.message_id).send.state == "accepted"
    assert store.unresolved_sends(*NS) == []
    local = store.message(*NS, result.message.message_id)
    assert (local.direction, local.self_message) == ("outgoing", True)
    assert [o.kind for o in store.receipts_owed(*NS)] == ["received"]
    snap = store.snapshot(*NS, availability="available", stale=False)
    assert len(snap.items) == 1 and snap.unsent == [], "one item; an accepted draft is no draft"


def test_receipts_owed_are_settled_including_not_found(store, serve):
    m = send(serve, MATT, 2, TIM, "Hello.").message
    store.store_page(*NS, UUID(int=1), page(entry(serve, TIM, m)))
    store.owe_receipt(*NS, m.message_id, "read")
    gone = UUID(int=77)
    store.owe_receipt(*NS, gone, "read")
    owed = store.receipts_owed(*NS)
    result = asyncio.run(serve.pair(TIM, UUID(int=1)).receipts(wire.ReceiptBatch(
        node_id=UUID(int=1), receipts=[wire.ReceiptItem(message_id=o.message_id, kind=o.kind)
                                       for o in owed])))
    assert result.not_found == [gone]
    store.settle_receipts(*NS, owed, result)
    assert store.receipts_owed(*NS) == []
    facts = store.message(*NS, m.message_id).receipts
    assert facts.received_at and facts.read_at


def test_a_stale_draft_save_is_a_conflict_carrying_the_latest(store):
    ids = fakes.SeqIds()
    draft = store.create_draft(*NS, new_draft(ids))
    agent = store.save_draft(*NS, draft.model_copy(update={"text": "Agent's words."}), 1)
    with pytest.raises(wire.ContractError) as e:
        store.save_draft(*NS, draft.model_copy(update={"text": "Page's words."}), 1)
    assert e.value.code == "revision_conflict" and e.value.latest == agent


def test_the_queue_pages_dismissed_items_and_lists_unsent_conversations(store, serve):
    t = serve.pair(TIM, UUID(int=1))
    for n in range(3):
        m = send(serve, MATT, 2, TIM, f"m{n}").message
        store.store_page(*NS, UUID(int=1), page(entry(serve, TIM, m)))
    first = sorted(serve.work.values(), key=wire.item_order_key)[0]
    store.save_work_state(*NS, asyncio.run(t.change_work_state(
        first.thread_id, wire.WorkCommandRequest(change=wire.Dismiss(
            expected_revision=first.revision)))))
    unsent = store.create_draft(*NS, new_draft(fakes.SeqIds(start=500)))
    snap = store.snapshot(*NS, availability="available", stale=False)
    assert (len(snap.items), snap.history_count, snap.unsent) == (2, 1, [unsent])
    assert [i.work_state.arrival_at for i in snap.items] == sorted(
        (i.work_state.arrival_at for i in snap.items), reverse=True)
    assert [i.work_state.thread_id for i in store.history(*NS, None, 1).items] == \
        [first.thread_id]


def test_namespaces_never_mix(store, serve):
    m = send(serve, MATT, 2, TIM, "For Tim on server 1.").message
    store.store_page(*NS, UUID(int=1), page(entry(serve, TIM, m)))
    store.create_draft(*NS, new_draft(fakes.SeqIds()))
    for other in ((OTHER_SERVER, TIM), (SERVER, MATT)):
        assert store.message(*other, m.message_id) is None
        assert store.receipts_owed(*other) == [] and store.announcements_due(*other) == []
        snap = store.snapshot(*other, availability="available", stale=False)
        assert snap.items == [] and snap.unsent == []


# -- what only the durable store shows ------------------------------------------------

def test_every_transition_survives_closing_and_reopening(serve):
    ids, clock = fakes.SeqIds(start=900), fakes.FixedClock()
    store = sqlite_store()
    guard = fakes.TableGuard(store, ids=ids, clock=clock)
    draft = store.create_draft(*NS, new_draft(ids))
    assert sqlite_store().draft(*NS, draft.message_id) == draft
    reviewed = guard.request_review(*NS, draft.message_id, 1)
    assert sqlite_store().draft(*NS, draft.message_id).send.state == "awaiting_approval"
    approved = guard.approve(*NS, draft.message_id, 1, reviewed.send.content_digest,
                             UUID(int=1), "voice")
    again = sqlite_store()
    assert again.approved(*NS, draft.message_id, 1) == approved
    assert [s.state for s in again.unresolved_sends(*NS)] == ["approved"]
    going = again.unresolved_sends(*NS)[0]
    assert again.transition_send(*NS, going, going.model_copy(update={
        "state": "submitting", "attempts": 1}))
    assert [s.state for s in sqlite_store().submitting(*NS)] == ["submitting"]
    result = asyncio.run(serve.pair(TIM, UUID(int=1)).send(approved.request))
    sqlite_store().record_accepted(*NS, result)
    reopened = sqlite_store()
    assert reopened.draft(*NS, draft.message_id).send.state == "accepted"
    assert reopened.item(*NS, draft.thread_id).work_state == result.work_state
    reopened.queue_command(*NS, draft.thread_id, wire.WorkCommandRequest(
        change=wire.OpenItem(expected_revision=1)))
    assert len(sqlite_store().pending_commands(*NS)) == 1
    m = send(serve, MATT, 2, TIM, "Incoming.").message
    reopened.store_page(*NS, UUID(int=1), page(entry(serve, TIM, m), continuation="c1"))
    last = sqlite_store()
    assert last.sync_position(*NS, UUID(int=1)) == "c1"
    assert [o.message_id for o in last.receipts_owed(*NS)] == [m.message_id]
    assert last.announcements_due(*NS) == [m.message_id]


def test_a_page_and_its_continuation_are_one_transaction(serve, monkeypatch):
    store = sqlite_store()
    a = send(serve, MATT, 2, TIM, "a").message
    b = send(serve, MATT, 2, TIM, "b").message
    real = store._first_incoming
    calls = []

    def crash_on_second(conn, *args):
        calls.append(args)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk gone")
        return real(conn, *args)

    monkeypatch.setattr(store, "_first_incoming", crash_on_second)
    with pytest.raises(sqlite3.OperationalError):
        store.store_page(*NS, UUID(int=1), page(entry(serve, TIM, a), entry(serve, TIM, b),
                                                continuation="c1"))
    monkeypatch.undo()
    assert store.message(*NS, a.message_id) is None, "nothing of the page was kept"
    assert store.sync_position(*NS, UUID(int=1)) is None and store.receipts_owed(*NS) == []
    assert store.store_page(*NS, UUID(int=1), page(entry(serve, TIM, a), entry(
        serve, TIM, b))) == [a.message_id, b.message_id]


def test_a_rescan_of_what_is_here_changes_nothing(serve):
    store = sqlite_store()
    m = send(serve, MATT, 2, TIM, "Once.").message
    assert store.store_page_changes(*NS, UUID(int=1), page(entry(serve, TIM, m)))[1] is True
    assert store.store_page_changes(*NS, UUID(int=1), page(entry(serve, TIM, m))) == ([], False)


def test_an_edit_never_replaces_a_send_that_is_out():
    ids = fakes.SeqIds()
    store = sqlite_store()
    draft = store.create_draft(*NS, new_draft(ids))
    out = draft.send.model_copy(update={"state": "submitting"})
    store.set_send(*NS, out)
    edited = store.draft(*NS, draft.message_id).model_copy(update={
        "text": "Changed.", "send": ports.LocalSend(message_id=draft.message_id,
                                                    content_revision=2, state="draft")})
    with pytest.raises(ports.SendRefused):
        store.save_draft(*NS, edited, 1)
    with pytest.raises(ports.SendRefused):
        store.discard_draft(*NS, draft.message_id, 1)
    assert store.send(*NS, draft.message_id) == out
    assert not store.transition_send(*NS, draft.send, draft.send.model_copy(
        update={"state": "cancelled"})), "a compare-and-set on a send that moved fails"


def test_an_edit_after_approval_replaces_the_approved_send():
    ids = fakes.SeqIds()
    store = sqlite_store()
    draft = store.create_draft(*NS, new_draft(ids))
    store.set_send(*NS, draft.send.model_copy(update={"state": "approved"}))
    current = store.draft(*NS, draft.message_id)
    edited = current.model_copy(update={"text": "Changed.", "send": ports.revise(current)})
    store.save_draft(*NS, edited, 1)
    assert store.unresolved_sends(*NS) == [], "the approved revision can no longer go out"
    assert store.draft(*NS, draft.message_id).send.content_revision == 2


def test_the_schema_is_versioned_and_migrates_once(tmp_path):
    path = tmp_path / "jarvis.db"
    SqliteMessagingStore(path)
    SqliteMessagingStore(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*), MAX(version) FROM human_messaging_schema"
                            ).fetchone() == (1, SCHEMA_VERSION)
        conn.execute("UPDATE human_messaging_schema SET version = ?", (SCHEMA_VERSION + 1,))
    with pytest.raises(RuntimeError):
        SqliteMessagingStore(path)


def test_the_store_lives_in_the_data_dir_under_test():
    assert SqliteMessagingStore().path == data_paths.db_path()
    assert "data-dir" in str(data_paths.db_path())
