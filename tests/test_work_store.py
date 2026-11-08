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
    run_store.init_db()
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


def _approve(ws, item):
    """His approval, the only way anything is done, of an item ready for
    his review (one with no pieces is reported so first)."""
    if not ws.get(item.id).pieces:
        ws.record(ws.WorkEvent(item.id, "update", "In for review.", {"state": "verifying"}))
    return ws.record(ws.WorkEvent(item.id, ws.APPROVED, "He approved it.", {"where": "page"}))


def _piece_done(ws, item, key):
    """A piece done, as only his approval makes one."""
    from jarvis import run_store
    with run_store._connect() as conn:
        conn.execute("UPDATE work_pieces SET state='done' WHERE item_id=? AND key=?", (item.id, key))
        conn.commit()
    return ws.get(item.id)


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
        ("engine", "agent", "s-1", "activity_unknown"), ("review", "tim", None, "planned")]
    item = _ev(ws, item, "piece", "Handed to a run.", key="engine", who={"kind": "run", "ref": "r-9"})
    assert item.pieces[0].who == ws.Who("run", "r-9") and item.pieces[0].title == "Wire the engine"
    with pytest.raises(ws.WorkRefused, match="says who"):
        _ev(ws, item, "piece", "Nobody.", key="nobody")
    with pytest.raises(ws.WorkRefused, match="names its id"):
        _ev(ws, item, "piece", "No id.", key="x", who={"kind": "workflow"})


def test_an_open_decision_leads_the_update_and_waits_on_tim(ws, folder):
    item = ws.create("Speech migration", "folder", folder)
    item = _ev(ws, item, "update", "Engine wired.", state="working")
    assert (item.state, item.headline) == ("activity_unknown", "Engine wired.")
    item = _ev(ws, item, "decision_needed", "Which engine is the default?", key="engine",
               options=["Kroko", "Apple"], recommendation="Kroko")
    assert item.state == "waiting_on_tim" and item.reported_state == "working"
    assert item.headline == "Which engine is the default?"
    item = _ev(ws, item, "update", "Tests running.")
    assert item.headline == "Which engine is the default?", "the decision keeps priority"
    item = _ev(ws, item, "decision_made", "He chose.", key="engine", answer="Kroko", where="voice")
    assert (item.state, item.decisions, item.headline) == ("activity_unknown", [], "He chose.")
    with pytest.raises(ws.WorkRefused, match="No decision engine is open"):
        _ev(ws, item, "decision_made", "Again.", key="engine", answer="Apple")
    answered = ws.events(item.id, kinds={"decision_made"})
    assert answered[-1].detail == {"key": "engine", "answer": "Kroko", "where": "voice"}
    with pytest.raises(ws.WorkRefused, match="already answered or closed; ask with a new key"):
        _ev(ws, item, "decision_needed", "Which voice?", key="engine", options=["Fish"])
    assert ws.get(item.id).decisions == [], "an answered key is never re-opened"


def test_the_latest_events_are_the_newest_however_long_the_log(ws, folder):
    item = ws.create("Speech", "folder", folder)
    for n in range(250):
        _ev(ws, item, "update", f"Step {n}.")
    newest = ws.latest(item.id, 3)
    assert [e.summary for e in newest] == ["Step 247.", "Step 248.", "Step 249."]
    assert newest[0].seq < newest[-1].seq


def test_a_claimed_working_item_never_reads_as_live_activity(ws, folder, monkeypatch):
    item = _ev(ws, ws.create("Speech", "folder", folder), "update", "Go.", state="working")
    assert not item.quiet
    real = time.time
    monkeypatch.setattr(ws.time, "time", lambda: real() + ws.QUIET_AFTER_SEC + 1)
    assert ws.get(item.id).state == "activity_unknown"
    assert not ws.get(item.id).quiet


def test_the_list_puts_done_last_and_finds_by_title(ws, folder):
    a = ws.create("Speech migration", "folder", folder)
    b = ws.create("Rebrand", "folder", folder)
    _approve(ws, b)
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


def test_verifying_sits_between_working_and_done_and_can_go_quiet(ws, folder, monkeypatch):
    assert ws.STATES.index("working") < ws.STATES.index("verifying") < ws.STATES.index("done")
    item = ws.create("Speech", "folder", folder)
    item = _ev(ws, item, "update", "Checking it.", state="verifying")
    _ev(ws, item, "piece", "Tests run.", key="tests", who={"kind": "tim"}, state="verifying")
    assert (ws.get(item.id).state, ws.get(item.id).pieces[0].state) == ("verifying", "verifying")
    real = time.time
    monkeypatch.setattr(ws.time, "time", lambda: real() + ws.QUIET_AFTER_SEC + 1)
    assert ws.get(item.id).quiet


def test_each_piece_keeps_its_latest_note_and_a_worker_label_beside_its_owner(ws, folder):
    item = ws.create("Rebrand", "folder", folder)
    _ev(ws, item, "piece", "Logo drafted.", key="logo", who={"kind": "agent", "ref": "s-1"},
        worker="logo build agent")
    item = _ev(ws, item, "piece", "Logo exported at three sizes.", key="logo")
    piece = item.pieces[0]
    assert (piece.note, piece.worker, piece.who) == (
        "Logo exported at three sizes.", "logo build agent", ws.Who("agent", "s-1"))
    assert piece.note_at is not None
    assert item.latest_update == "Logo exported at three sizes."
    item = _ev(ws, item, "piece", "Label taken off.", key="logo", worker="")
    assert item.pieces[0].worker == ""


def test_a_piece_waits_on_pieces_it_names_and_the_order_follows(ws, folder):
    item = ws.create("Rebrand", "folder", folder)
    tim = {"kind": "tim"}
    _ev(ws, item, "piece", "Ship it.", key="ship", who=tim)
    _ev(ws, item, "piece", "Design.", key="design", who=tim)
    _ev(ws, item, "piece", "Build.", key="build", who=tim, after=["design"])
    item = _ev(ws, item, "piece", "Ship after the build.", key="ship", after="build,design")
    assert [p.key for p in item.pieces] == ["design", "build", "ship"]
    assert {p.key: p.waits_for for p in item.pieces} == {
        "design": [], "build": ["design"], "ship": ["build", "design"]}
    item = _piece_done(ws, item, "design")
    assert {p.key: p.waits_for for p in item.pieces}["ship"] == ["build"]
    for after, why in ((["design", "ship"], "come after itself"),
                       (["nothing"], "no piece nothing"), (["Bad Key"], "lowercase")):
        with pytest.raises(ws.WorkRefused, match=why):
            _ev(ws, item, "piece", "Loop.", key="design", after=after)
    with pytest.raises(ws.WorkRefused, match="through the pieces it names"):
        _ev(ws, item, "piece", "Loop.", key="design", after=["ship"])
    assert ws.get(item.id).pieces[0].after == [], "a refused report changes nothing"
    item = _ev(ws, item, "piece", "No longer waits.", key="ship", after=[])
    assert [p.after for p in item.pieces if p.key == "ship"] == [[]]


def test_evidence_is_a_commit_a_path_or_a_url_kept_once_and_never_opened(ws, folder):
    item = ws.create("Rebrand", "folder", folder)
    item = _ev(ws, item, "piece", "Built.", key="build", who={"kind": "tim"},
               evidence=["ABC1234", "/tmp/out/logo.svg", "https://github.com/x/y/pull/7"])
    item = _ev(ws, item, "piece", "Again.", key="build", evidence=["abc1234", "/tmp/out/../out/logo.svg"])
    assert [(e.kind, e.value) for e in item.pieces[0].evidence] == [
        ("commit", "abc1234"), ("path", "/tmp/out/logo.svg"), ("url", "https://github.com/x/y/pull/7")]
    for bad in ("relative/file", "javascript:alert(1)", "https://a b", "line\nbreak"):
        with pytest.raises(ws.WorkRefused, match="commit, an absolute path or an http"):
            _ev(ws, item, "piece", "Bad.", key="build", evidence=[bad])


def test_items_group_by_area_else_by_source_folder(ws, folder, tmp_path):
    zipped = tmp_path / "bundles" / "logo.zip"
    zipped.parent.mkdir()
    zipped.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    a = ws.create("Speech", "folder", folder, area="Voice")
    b = ws.create("Logo", "zip", str(zipped))
    c = ws.create("Captions", "folder", folder)
    assert (b.grouped_by, b.group) == ("folder", str(zipped.parent))
    groups = ws.grouped(ws.listing())
    assert [(g.grouped_by, g.name, [i.id for i in g.items]) for g in groups] == [
        ("folder", folder, [c.id]), ("folder", str(zipped.parent), [b.id]), ("area", "Voice", [a.id])]
    c = _ev(ws, c, "update", "Moved to voice.", area="Voice")
    assert (c.grouped_by, c.group) == ("area", "Voice")
    c = _ev(ws, c, "update", "Back to its folder.", area="")
    assert (c.grouped_by, c.group) == ("folder", folder)


def test_blocked_and_waiting_pieces_are_counted_on_the_item(ws, folder):
    item = ws.create("Rebrand", "folder", folder)
    tim = {"kind": "tim"}
    _ev(ws, item, "decision_needed", "Which?", key="which")
    _ev(ws, item, "piece", "Stuck.", key="a", who=tim, state="blocked", until="CI is down")
    _ev(ws, item, "piece", "Asks him.", key="b", who=tim, state="waiting_on_tim",
        until="decision:which")
    item = _ev(ws, item, "piece", "Fine.", key="c", who=tim, state="working")
    assert (item.blocked_pieces, item.waiting_pieces) == (1, 1)


def test_a_first_version_record_gains_its_pieces_latest_notes(ws, monkeypatch, tmp_path):
    from contextlib import closing

    from jarvis import run_store
    with closing(run_store._connect()) as conn:
        run_store.migrate(conn, "work_schema", {1: ws._SCHEMA}, "work tracker")
        conn.execute("INSERT INTO work_items VALUES ('w1', 'T', 'folder', '/tmp', 'working', '', 1, 0, 0)")
        conn.execute("INSERT INTO work_pieces VALUES ('w1', 'a', 'A', 'tim', NULL, 'working', 0)")
        for at, said in ((5, "first"), (6, "second")):
            conn.execute("INSERT INTO work_events (item_id, kind, summary, detail, at) "
                         "VALUES ('w1', 'piece', ?, '{\"key\": \"a\"}', ?)", (said, at))
        conn.commit()
    piece = ws.get("w1").pieces[0]
    assert (piece.note, piece.note_at, piece.worker, piece.after, piece.evidence) == (
        "second", 6.0, "", [], [])


def test_blockers_list_decisions_then_the_item_then_pieces(ws, folder):
    item = ws.create("Rebrand", "folder", folder)
    tim = {"kind": "tim"}
    _ev(ws, item, "decision_needed", "Which logo?", key="logo", options=["A", "B"], package="logo-deck")
    _ev(ws, item, "piece", "Design.", key="design", who=tim, state="working")
    _ev(ws, item, "piece", "Asks him.", key="copy", who=tim, state="waiting_on_tim",
        until="decision:logo")
    _ev(ws, item, "piece", "Stuck on fonts.", key="fonts", who=tim, state="blocked",
        until="the font licence")
    item = _ev(ws, item, "piece", "After design.", key="build", who=tim, after=["design"])
    with pytest.raises(ws.WorkRefused, match="worked out from its pieces"):
        _ev(ws, item, "update", "CI is red.", state="blocked")
    assert [(b.kind, b.key) for b in item.blockers] == [
        ("decision", "logo"), ("piece_blocked", "fonts"),
        ("piece_waiting", "copy"), ("piece_after", "build")]
    assert item.blockers[1].text == "Stuck on fonts." and item.blockers[3].waits_for == ["design"]
    assert item.blockers[1].waits_for == ["the font licence"]
    assert item.blockers[2].waits_for == ["decision:logo"]
    assert item.decisions[0].package == "logo-deck"
    with pytest.raises(ws.WorkRefused, match="package is named"):
        _ev(ws, item, "decision_needed", "Bad?", key="bad", package="no spaces allowed")
    with pytest.raises(ws.WorkRefused, match="Done is Tim's to give"):
        _ev(ws, item, "update", "Done.", state="done")
    with pytest.raises(ws.WorkRefused, match="still open"):
        _approve(ws, item)


def test_answered_and_withdrawn_decisions_are_kept_newest_first(ws, folder):
    item = ws.create("Rebrand", "folder", folder)
    _ev(ws, item, "decision_needed", "Tone?", key="tone", asked_by="key:coordinator")
    _ev(ws, item, "decision_needed", "Colour?", key="colour", asked_by="key:coordinator")
    _ev(ws, item, "decision_made", "He chose.", key="tone", answer="dry", where="page")
    with pytest.raises(ws.WorkRefused, match="Only the agent that asked"):
        _ev(ws, item, "decision_withdrawn", "Not mine.", key="colour", by="key:someone-else")
    item = _ev(ws, item, "decision_withdrawn", "Asked before he saw the designs.", key="colour",
               by="key:coordinator")
    assert item.decisions == [] and item.state == "planned"
    assert [(a.key, a.where, a.answer) for a in item.answered] == [
        ("colour", "withdrawn", "Asked before he saw the designs."), ("tone", "page", "dry")]
    with pytest.raises(ws.WorkRefused, match="No decision colour is open"):
        _ev(ws, item, "decision_made", "Late.", key="colour", answer="blue")
    with pytest.raises(ws.WorkRefused, match="already answered or closed"):
        _ev(ws, item, "decision_needed", "Colour again?", key="colour")


def test_done_items_linger_then_move_to_history_and_closed_ones_at_once(ws, folder, monkeypatch):
    a = ws.create("Shipped", "folder", folder)
    b = ws.create("Dropped", "folder", folder)
    c = ws.create("Going", "folder", folder)
    _approve(ws, a)
    b = _ev(ws, b, "closed", "No longer wanted.")
    assert b.history and b.closed_at and b.blockers == []
    assert [i.id for i in ws.listing()] == [c.id, a.id]
    assert [i.id for i in ws.listing(history=True)] == [b.id]
    with pytest.raises(ws.WorkRefused, match="closed; it is kept in History"):
        _ev(ws, b, "update", "Back?")
    real = time.time
    monkeypatch.setattr(ws.time, "time", lambda: real() + ws.DONE_LINGER_SEC + 1)
    assert [i.id for i in ws.listing()] == [c.id]
    assert {i.id for i in ws.listing(history=True)} == {a.id, b.id}
    assert [i.id for i in ws.find("dropped")] == [b.id], "History is still found by name"


def test_a_working_piece_says_when_it_goes_quiet_from_its_latest_report(ws, folder):
    item = ws.create("Speech", "folder", folder)
    _ev(ws, item, "piece", "Started.", key="engine", who={"kind": "tim"}, state="working")
    item = _ev(ws, item, "piece", "Planned.", key="docs", who={"kind": "tim"})
    engine, docs = (next(p for p in ws.get(item.id).pieces if p.key == k) for k in ("engine", "docs"))
    assert engine.quiet_at is None
    assert engine.state == "activity_unknown"
    assert docs.quiet_at is None
    item = ws.get(item.id)
    assert item.state == "activity_unknown", "a piece claims work: a claim, never telemetry"
    item = _ev(ws, item, "update", "Going.", state="working")
    assert item.quiet_at is None
    assert item.state == "activity_unknown"
    _ev(ws, item, "piece", "In.", key="engine", state="verifying")
    _ev(ws, item, "piece", "In.", key="docs", state="verifying")
    item = _approve(ws, item)
    assert next(p for p in item.pieces if p.key == "engine").quiet_at is None


@pytest.mark.parametrize("owner", ["agent", "run", "workflow", "tim"])
def test_reporters_cannot_assert_generation_for_any_owner(ws, folder, monkeypatch, owner):
    now = [1000.0]
    monkeypatch.setattr(ws.time, "time", lambda: now[0])
    item = ws.create("Claim", "folder", folder, state="working")
    on = {"kind": owner, **({"ref": "shared-session"} if owner != "tim" else {})}
    item = _ev(ws, item, "piece", "Claim", key="claimed", who=on, state="working")
    for when in (1000.0, 1000.1, 1100.0, 2000.0):
        now[0] = when
        item = _ev(ws, item, "update", "Another heartbeat; still working", state="working")
        assert item.state == "activity_unknown"
        assert item.pieces[0].state == "activity_unknown"
        assert item.activity_reason and item.pieces[0].activity_reason
        assert item.quiet_at is None and item.pieces[0].quiet_at is None
        assert ws.listing()[0].state != "working"
        assert ws.find(item.id)[0].state != "working"
    # A restart cannot turn a persisted claim into evidence.
    importlib.reload(ws)
    assert ws.get(item.id).state == "activity_unknown"


def test_unrelated_session_activity_and_tools_cannot_promote_work(ws, folder):
    from jarvis import brain_activity
    one = ws.create("One", "folder", folder, state="working")
    two = ws.create("Two", "folder", folder, state="working")
    for item in (one, two):
        _ev(ws, item, "piece", "Assigned", key="task",
            who={"kind": "agent", "ref": "same-session"}, state="working")
    # Even fresh streaming is not assignment bound; tools do not imply tokens.
    for stage in ("started", "first_output", "streaming", "completed"):
        brain_activity.note("turn", stage, session_id="same-session", id="other-turn")
        for item in (one, two):
            assert ws.get(item.id).state == "activity_unknown"
    brain_activity.note("tool", "started", session_id="same-session", id="tool")
    assert all(i.state != "working" for i in ws.listing())


def test_legacy_database_claim_is_projected_and_history_is_preserved(ws, folder):
    item = ws.create("Legacy", "folder", folder)
    with ws._connect() as conn:
        conn.execute("UPDATE work_items SET state='working' WHERE id=?", (item.id,))
    loaded = ws.get(item.id)
    assert loaded.state == "activity_unknown"
    assert loaded.reported_state == "working"
    assert "working" not in ws.PUBLIC_STATES
