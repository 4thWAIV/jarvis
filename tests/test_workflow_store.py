"""The workflow monitor's record: every step is an event appended to one log,
and the workflow's status is updated in the same transaction, so what JARVIS
reports is always the latest. Going quiet is worked out when it is read."""

import importlib

import pytest

import session_needs
import workflow_store as ws
from workflow_store import WorkflowEvent


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import data_paths
    importlib.reload(data_paths)
    import run_store
    importlib.reload(run_store)
    importlib.reload(ws)
    run_store.init_db()
    return ws


def _ev(wid, kind, summary="Something happened.", item=None, **detail):
    return WorkflowEvent(wid, kind, item, summary, detail)


def test_a_workflow_starts_running_with_its_phases_pending():
    wid = ws.start("Speech migration", "jarvis", ["Mic recorder", "Deepgram engine"])
    w = ws.snapshot(wid)
    assert (w.name, w.project, w.state, w.phases_total, w.phases_done) == \
        ("Speech migration", "jarvis", "running", 2, 0)
    assert [(i.slug, i.state.value) for i in w.items] == \
        [("mic-recorder", "pending"), ("deepgram-engine", "pending")]
    assert [e.kind for e in ws.events(wid)] == ["workflow_started"]


def test_phases_and_tasks_move_on_and_are_counted():
    wid = ws.start("Build", "hammer", ["plan", "build"])
    ws.record(_ev(wid, "phase_started", item="plan"))
    ws.record(_ev(wid, "task_added", item="write-spec", title="Write the spec", total=4))
    ws.record(_ev(wid, "task_progress", item="write-spec", done=3))
    w = ws.record(_ev(wid, "task_finished", item="write-spec"))
    assert w.phase == "plan" and (w.tasks_done, w.tasks_total) == (1, 1)
    task = next(i for i in w.items if i.slug == "write-spec")
    assert (task.phase, task.title, task.done, task.total) == ("plan", "Write the spec", 3, 4)
    w = ws.record(_ev(wid, "phase_finished", item="plan"))
    assert (w.phases_done, w.last_summary) == (1, "Something happened.")


@pytest.mark.parametrize("opens, closes, state", [
    ("decision_needed", "decision_made", "needs_you"),
    ("blocker_raised", "blocker_cleared", "blocked"),
    ("wait_started", "wait_ended", "waiting"),
])
def test_an_open_item_sets_the_state_until_it_is_closed(opens, closes, state):
    wid = ws.start("Build", "hammer", [])
    w = ws.record(_ev(wid, opens, "Which database?", key="db", options=["a", "b"]))
    assert w.state == state and [o.key for o in w.open] == ["db"]
    w = ws.record(_ev(wid, closes, "Settled.", key="db"))
    assert w.state == "running" and w.open == []


def test_a_decision_outranks_a_blocker_and_a_wait():
    wid = ws.start("Build", "hammer", [])
    ws.record(_ev(wid, "wait_started", key="ci"))
    ws.record(_ev(wid, "blocker_raised", key="conflict"))
    w = ws.record(_ev(wid, "decision_needed", key="db"))
    assert w.state == "needs_you"
    assert (w.open_waits, w.open_blockers, w.open_decisions) == (["ci"], ["conflict"], ["db"])


def test_a_finished_workflow_takes_no_more_events():
    wid = ws.start("Build", "hammer", [])
    assert ws.record(_ev(wid, "workflow_finished", outcome="failed")).state == "failed"
    with pytest.raises(ws.WorkflowRefused):
        ws.record(_ev(wid, "note"))
    assert ws.active() == [] and [w.id for w in ws.recent()] == [wid]


def test_a_refused_event_changes_nothing():
    wid = ws.start("Build", "hammer", ["plan"])
    bad = [WorkflowEvent("nope", "note", None, "x", {}),
           _ev(wid, "phase_started", item="nonexistent"),
           _ev(wid, "task_progress", item="never-added"),
           _ev(wid, "decision_made", key="never-opened"),
           _ev(wid, "teleport"),
           _ev(wid, "note", summary="   "),
           _ev(wid, "note", blob="x" * 5000),
           _ev(wid, "phase_started", item="Not A Slug")]
    before = ws.snapshot(wid)
    for e in bad:
        with pytest.raises(ws.WorkflowRefused):
            ws.record(e)
    assert ws.snapshot(wid).last_event_at == before.last_event_at
    assert [e.kind for e in ws.events(wid)] == ["workflow_started"]


def test_going_quiet_is_read_never_stored(monkeypatch):
    wid = ws.start("Build", "hammer", [])
    real = ws.time.time
    monkeypatch.setattr(ws.time, "time", lambda: real() + ws.SILENT_AFTER_SEC + 1)
    assert ws.snapshot(wid).state == ws.SILENT
    monkeypatch.setattr(ws.time, "time", real)
    w = ws.record(_ev(wid, "note", "Still here."))
    assert w.state == "running"


def test_agents_are_attached_once():
    wid = ws.start("Build", "hammer", [])
    ws.record(_ev(wid, "agent_attached", session_id="s1"))
    w = ws.record(_ev(wid, "agent_attached", session_id="s1"))
    assert w.agents == ["s1"]


def test_the_log_reads_from_a_point_and_by_kind():
    wid = ws.start("Build", "hammer", [])
    for n in range(3):
        ws.record(_ev(wid, "note", f"Note {n}."))
    ws.record(_ev(wid, "decision_needed", key="db"))
    seqs = [e.seq for e in ws.events(wid)]
    assert [e.summary for e in ws.events(wid, after=seqs[1])] == \
        ["Note 1.", "Note 2.", "Something happened."]
    assert [e.kind for e in ws.events(wid, kinds={"decision_needed"})] == ["decision_needed"]


def test_a_workflow_is_found_by_its_name_as_said():
    wid = ws.start("Speech-migration", "jarvis", [])
    assert [w.id for w in ws.find("speech migration")] == [wid]
    assert [w.id for w in ws.find(wid)] == [wid]
    assert ws.find("rebrand") == []


def test_open_decisions_appear_on_the_needs_board_without_writing_it(tmp_path):
    wid = ws.start("Speech migration", "jarvis", [])
    ws.record(_ev(wid, "decision_needed", "Which engine?", key="engine"))
    own = session_needs.Board("jarvis", "# jarvis\n\n## Decisions\n- one: x", 0.0)
    boards = ws.with_decisions([own])
    assert boards[0].items == 2
    assert "Speech migration needs a decision (engine): Which engine?" in boards[0].text
    assert own.text.endswith("- one: x"), "the session's own board is never changed"
    assert ws.with_decisions([])[0].project == "jarvis"


def test_a_workflow_that_said_it_is_waiting_is_not_called_quiet(monkeypatch):
    wid = ws.start("Build", "hammer", [])
    ws.record(_ev(wid, "wait_started", "Running the test suite.", key="suite"))
    real = ws.time.time
    monkeypatch.setattr(ws.time, "time", lambda: real() + ws.SILENT_AFTER_SEC + 1)
    assert ws.snapshot(wid).state == "waiting"


def test_a_finished_workflow_lingers_then_is_dismissed_for_good(monkeypatch):
    wid = ws.start("Build", "hammer", [])
    w = ws.record(_ev(wid, "workflow_finished", "Done.", outcome="succeeded"))
    assert not w.dismissed and w.dismiss_at == w.finished_at + ws.LINGER_SEC
    real = ws.time.time
    monkeypatch.setattr(ws.time, "time", lambda: real() + ws.LINGER_SEC + 1)
    w = ws.snapshot(wid)
    assert w.dismissed and w.dismiss_at is None and w.state == "succeeded"


def test_he_can_dismiss_a_finished_workflow_at_once_and_only_once():
    wid = ws.start("Build", "hammer", [])
    with pytest.raises(ws.WorkflowRefused, match="Only a finished"):
        ws.record(_ev(wid, "workflow_dismissed", "Dismissed."))
    ws.record(_ev(wid, "workflow_finished", "Done.", outcome="failed"))
    w = ws.record(_ev(wid, "workflow_dismissed", "Dismissed."))
    assert w.dismissed and w.state == "failed"
    assert [e.kind for e in ws.events(wid)][-1] == "workflow_dismissed"
    with pytest.raises(ws.WorkflowRefused, match="already dismissed"):
        ws.record(_ev(wid, "workflow_dismissed", "Again."))

