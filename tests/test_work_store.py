"""The Work tracker's record (work_store.py): an item on a zip or folder that
is on this Mac, its pieces and who is on each, its latest update and open
decisions, every change one event in one transaction."""

import importlib
import os
import time

import pytest


@pytest.fixture
def ws(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import work_store
    importlib.reload(work_store)
    return work_store


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "speech"
    path.mkdir()
    return str(path)


def _ev(ws, item, kind, summary, **detail):
    return ws.record(ws.WorkEvent(item.id, kind, summary, detail))


def test_an_item_starts_on_a_folder_or_zip_that_is_there(ws, folder, tmp_path):
    item = ws.create("Speech migration", "folder", folder)
    assert (item.state, item.source, item.source_path) == ("planned", "present", folder)
    assert item.headline == "Speech migration is tracked." and item.revision == 1
    package = tmp_path / "bundle.zip"
    package.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    assert ws.create("Bundle", "zip", str(package)).source == "present"


@pytest.mark.parametrize("kind,path,why", [
    ("folder", "relative/path", "full path"),
    ("folder", "/no/such/folder", "There is no folder"),
    ("zip", "{folder}", "is not a zip"),
    ("tarball", "/tmp", "a zip or a folder"),
])
def test_a_source_that_is_not_there_or_not_its_kind_is_refused(ws, folder, kind, path, why):
    with pytest.raises(ws.WorkRefused, match=why):
        ws.create("X", kind, path.format(folder=folder))


def test_a_source_that_goes_away_reads_missing_and_is_never_opened(ws, tmp_path):
    path = tmp_path / "gone"
    path.mkdir()
    item = ws.create("Gone", "folder", str(path))
    os.rmdir(path)
    assert ws.get(item.id).source == "missing"


def test_pieces_say_exactly_who_is_on_them(ws, folder):
    item = ws.create("Speech migration", "folder", folder)
    _ev(ws, item, "piece", "Engine wiring started.", key="engine", title="Wire the engine",
        who={"kind": "agent", "ref": "s-1"}, state="working")
    item = _ev(ws, item, "piece", "Tim reviews it.", key="review", who={"kind": "tim"})
    assert [(p.key, p.who.kind, p.who.ref, p.state) for p in item.pieces] == [
        ("engine", "agent", "s-1", "working"), ("review", "tim", None, "planned")]
    item = _ev(ws, item, "piece", "Handed to a run.", key="engine", who={"kind": "run", "ref": "r-9"})
    assert item.pieces[0].who == ws.Who("run", "r-9") and item.pieces[0].title == "Wire the engine"
    with pytest.raises(ws.WorkRefused, match="says who"):
        _ev(ws, item, "piece", "Nobody.", key="nobody")
    with pytest.raises(ws.WorkRefused, match="names its id"):
        _ev(ws, item, "piece", "No id.", key="x", who={"kind": "workflow"})


def test_an_open_decision_leads_the_update_and_waits_on_tim(ws, folder):
    item = ws.create("Speech migration", "folder", folder)
    item = _ev(ws, item, "update", "Engine wired.", state="working")
    assert (item.state, item.headline) == ("working", "Engine wired.")
    item = _ev(ws, item, "decision_needed", "Which engine is the default?", key="engine",
               options=["Kroko", "Apple"], recommendation="Kroko")
    assert item.state == "waiting_on_tim" and item.reported_state == "working"
    assert item.headline == "Which engine is the default?"
    item = _ev(ws, item, "update", "Tests running.")
    assert item.headline == "Which engine is the default?", "the decision keeps priority"
    item = _ev(ws, item, "decision_made", "He chose.", key="engine", answer="Kroko", where="voice")
    assert (item.state, item.decisions, item.headline) == ("working", [], "He chose.")
    with pytest.raises(ws.WorkRefused, match="No decision engine is open"):
        _ev(ws, item, "decision_made", "Again.", key="engine", answer="Apple")
    answered = ws.events(item.id, kinds={"decision_made"})
    assert answered[-1].detail == {"key": "engine", "answer": "Kroko", "where": "voice"}
    with pytest.raises(ws.WorkRefused, match="already answered; ask with a new key"):
        _ev(ws, item, "decision_needed", "Which voice?", key="engine", options=["Fish"])
    assert ws.get(item.id).decisions == [], "an answered key is never re-opened"


def test_the_latest_events_are_the_newest_however_long_the_log(ws, folder):
    item = ws.create("Speech", "folder", folder)
    for n in range(250):
        _ev(ws, item, "update", f"Step {n}.")
    newest = ws.latest(item.id, 3)
    assert [e.summary for e in newest] == ["Step 247.", "Step 248.", "Step 249."]
    assert newest[0].seq < newest[-1].seq


def test_a_working_item_with_nothing_reported_reads_quiet(ws, folder, monkeypatch):
    item = _ev(ws, ws.create("Speech", "folder", folder), "update", "Go.", state="working")
    assert not item.quiet
    real = time.time
    monkeypatch.setattr(ws.time, "time", lambda: real() + ws.QUIET_AFTER_SEC + 1)
    assert ws.get(item.id).quiet


def test_the_list_puts_done_last_and_finds_by_title(ws, folder):
    a = ws.create("Speech migration", "folder", folder)
    b = ws.create("Rebrand", "folder", folder)
    _ev(ws, b, "update", "Shipped.", state="done")
    assert [i.title for i in ws.listing()] == ["Speech migration", "Rebrand"]
    assert [i.id for i in ws.find("speech-migration")] == [a.id]
    assert [i.id for i in ws.find(b.id)] == [b.id]
    assert ws.find("nothing like it") == []


def test_every_change_is_one_event_and_a_bad_one_changes_nothing(ws, folder):
    item = ws.create("Speech", "folder", folder)
    with pytest.raises(ws.WorkRefused, match="one of"):
        _ev(ws, item, "update", "Odd.", state="sideways")
    with pytest.raises(ws.WorkRefused, match="Kinds are"):
        _ev(ws, item, "phase_started", "No.")
    assert ws.get(item.id).revision == 1
    assert [e.kind for e in ws.events(item.id)] == ["created"]


def test_the_schema_is_its_own_and_versioned(ws, folder):
    ws.create("Speech", "folder", folder)
    from contextlib import closing

    from jarvis import run_store
    with closing(run_store._connect()) as conn:
        assert conn.execute("SELECT version FROM work_schema").fetchone()[0] == max(ws._MIGRATIONS)
