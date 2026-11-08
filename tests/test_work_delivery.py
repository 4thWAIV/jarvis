import importlib
from types import SimpleNamespace
import pytest

@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths, run_store, work_store, work_delivery
    importlib.reload(data_paths)
    importlib.reload(run_store)
    run_store.init_db()
    work_store._migrated.clear()
    folder = tmp_path / "project"; folder.mkdir()
    item = work_store.create("Rebuild", "folder", str(folder))
    work_store.record(work_store.WorkEvent(item.id, "piece", "Owner assigned", {
        "key": "build", "title": "Build", "who": {"kind": "agent", "ref": "owner"}, "state": "ready"}))
    session = SimpleNamespace(session_id="owner", provider="codex", voice_name="Planning", project="project", state="idle")
    snapshot = SimpleNamespace(by_id=lambda ref: session if ref == "owner" else None)
    return item, snapshot, run_store, work_store, work_delivery

def add(setup):
    item, _, _, store, _ = setup
    return store.record(store.WorkEvent(item.id, store.WORK_ADDED, "Additional requirement", {"words": "Continue with the new requirement", "where": "voice"}))

def test_atomic_obligation_queue_restart_and_actual_pickup(setup):
    item, snap, messages, _, delivery = setup
    add(setup)
    assert delivery.deliveries(item.id)[0]["status"] == "pending"
    delivery.poll(snap)
    first = delivery.deliveries(item.id)[0]
    assert first["status"] == "queued"
    mid = first["message_id"]
    assert messages.get_message(mid).status == "queued"
    importlib.reload(delivery)
    delivery.poll(snap)
    assert delivery.deliveries(item.id)[0]["message_id"] == mid
    assert len(messages.queued_messages()) == 1
    messages.update_message(mid, status="sent")
    delivery.poll(snap)
    assert delivery.deliveries(item.id)[0]["status"] == "sent"
    messages.update_message(mid, status="working", started_at=1)
    delivery.poll(snap)
    assert delivery.deliveries(item.id)[0]["status"] == "picked_up"

def test_unavailable_owner_is_visible_and_retried(setup):
    item, snap, messages, _, delivery = setup
    add(setup)
    delivery.poll(SimpleNamespace(by_id=lambda ref: None))
    assert delivery.deliveries(item.id)[0]["status"] == "blocked"
    assert not messages.queued_messages()
    delivery.poll(snap)
    assert delivery.deliveries(item.id)[0]["status"] == "queued"

def test_two_requirements_remain_separate_and_failed_send_is_not_pickup(setup):
    item, snap, messages, _, delivery = setup
    add(setup); add(setup)
    delivery.poll(snap)
    rows = delivery.deliveries(item.id)
    assert len(rows) == 2
    assert len({r["message_id"] for r in rows}) == 2
    messages.update_message(rows[0]["message_id"], status="failed", failure="provider unavailable")
    delivery.poll(snap)
    assert delivery.deliveries(item.id)[0]["status"] == "failed"
    assert delivery.deliveries(item.id)[0]["reason"] == "provider unavailable"

def test_refused_feedback_creates_no_obligation(setup):
    item, _, _, store, delivery = setup
    with pytest.raises(store.WorkRefused):
        store.record(store.WorkEvent(item.id, store.WORK_ADDED, "Missing words", {}))
    assert delivery.deliveries(item.id) == []


def test_idle_owner_receives_feedback_through_existing_provider(setup):
    from jarvis.session_watch import SessionWatcher
    item, snap, messages, _, delivery = setup
    session = snap.by_id('owner')
    session.between_turns = True
    sent = []
    provider = SimpleNamespace(name='codex', send=lambda s, text: sent.append((s.session_id, text)) or 'sent')
    add(setup)
    delivery.poll(snap)
    watcher = SessionWatcher(providers=[provider], messages=messages)
    watcher._publish = lambda *args, **kwargs: None
    watcher.send_queued(snap)
    assert sent[0][0] == 'owner'
    assert 'Continue with the new requirement' in sent[0][1]
    delivery.poll(snap)
    assert delivery.deliveries(item.id)[0]['status'] == 'sent'
    watcher.send_queued(snap)
    assert len(sent) == 1


def test_busy_owner_waits_without_losing_or_duplicating_requirement(setup):
    from jarvis.session_watch import SessionWatcher
    item, snap, messages, _, delivery = setup
    session = snap.by_id('owner')
    session.between_turns = False
    sent = []
    provider = SimpleNamespace(name='codex', send=lambda s, text: sent.append(text) or 'sent')
    add(setup)
    delivery.poll(snap)
    watcher = SessionWatcher(providers=[provider], messages=messages)
    watcher._publish = lambda *args, **kwargs: None
    watcher.send_queued(snap)
    assert not sent
    session.between_turns = True
    watcher.send_queued(snap)
    assert len(sent) == 1


def test_event_and_delivery_rollback_together(setup, monkeypatch):
    item, _, _, store, delivery = setup
    before = store.get(item.id).revision
    def broken(*args):
        raise RuntimeError('cannot persist notification')
    monkeypatch.setattr(delivery, 'schedule', broken)
    with pytest.raises(RuntimeError):
        add(setup)
    assert store.get(item.id).revision == before
    assert not [e for e in store.events(item.id) if e.kind == store.WORK_ADDED]
