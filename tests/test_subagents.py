"""Each session's subagents, one by one, and where the Workflows area shows them.

A subagent is what its own two files say of it: the transcript in
`<sessionId>/subagents/agent-<id>.jsonl` and the `agent-<id>.meta.json`
beside it. Finished is the transcript's last message being the assistant's,
ending its turn; otherwise it is working only while the file is being
written, and stale (not called either) once it has not been for a while.
These are another process's files: nothing here may raise on one that is
missing, half-written or gone, or read through a symlink.
"""

import importlib
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis import session_watch as sw
from tests.fixtures.roster import write_roster, write_transcript
from tests.fixtures.transcripts import encode, iso

NOW = 1_788_404_571.0


def _folder(root, cwd="/p/one", session_id="s") -> Path:
    d = Path(root) / "projects" / encode(cwd) / session_id / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _line(kind, *, stop=None, when=NOW - 60, text="working on it"):
    message = {"role": kind, "content": [{"type": "text", "text": text}] if kind == "assistant"
               else text}
    if stop:
        message["stop_reason"] = stop
    return {"type": kind, "isSidechain": True, "timestamp": iso(when), "message": message}


def _agent(root, agent_id, lines, *, written, meta=None, cwd="/p/one", session_id="s"):
    d = _folder(root, cwd, session_id)
    p = d / f"agent-{agent_id}.jsonl"
    p.write_text("".join(json.dumps(o) + "\n" for o in lines))
    os.utime(p, (written, written))
    if meta is not None:
        (d / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
    return p


META = {"agentType": "general-purpose", "description": "Write the release notes",
        "toolUseId": "toolu_1", "spawnDepth": 1, "requestShape": "background"}


@pytest.fixture(autouse=True)
def _fresh_cache():
    sw._AGENT_CACHE.clear()
    yield
    sw._AGENT_CACHE.clear()


def _read(root, **kw):
    return sw.read_subagents(root, "/p/one", "s", kw.get("now", NOW))


# ── the three states ────────────────────────────────────────────────────────

def test_a_transcript_ending_on_the_assistants_end_of_turn_is_finished(tmp_path):
    # Written seconds ago, and still finished: the file says it ended.
    _agent(tmp_path, "done", [_line("user", when=NOW - 300), _line("assistant", stop="end_turn")],
           written=NOW - 2, meta=META)

    (a,), capped = _read(tmp_path)

    assert a.state == sw.AGENT_FINISHED and capped is False


def test_a_transcript_written_lately_and_not_ended_is_working(tmp_path):
    _agent(tmp_path, "busy", [_line("user"), _line("assistant", stop="tool_use")],
           written=NOW - 5, meta=META)

    (a,), _ = _read(tmp_path)

    assert a.state == sw.AGENT_WORKING


def test_one_not_ended_and_not_written_for_a_while_is_stale_not_working(tmp_path):
    """No process to ask: past the window the file cannot say whether it is
    on a long tool call or was stopped, so it is called neither."""
    _agent(tmp_path, "quiet", [_line("user"), _line("assistant", stop="tool_use")],
           written=NOW - sw.AGENT_ACTIVE_WITHIN_SEC - 1, meta=META)

    (a,), _ = _read(tmp_path)

    assert a.state == sw.AGENT_STALE


def test_a_tool_result_after_the_end_of_turn_means_it_went_on(tmp_path):
    _agent(tmp_path, "resumed", [_line("assistant", stop="end_turn"), _line("user")],
           written=NOW - 5)

    (a,), _ = _read(tmp_path)

    assert a.state == sw.AGENT_WORKING


def test_a_later_write_is_read_again_and_not_served_from_the_cache(tmp_path):
    p = _agent(tmp_path, "x", [_line("user"), _line("assistant", stop="tool_use")],
               written=NOW - 5, meta=META)
    assert _read(tmp_path)[0][0].state == sw.AGENT_WORKING

    with open(p, "a") as fh:
        fh.write(json.dumps(_line("assistant", stop="end_turn")) + "\n")
    os.utime(p, (NOW - 1, NOW - 1))

    assert _read(tmp_path)[0][0].state == sw.AGENT_FINISHED


# ── what it is ──────────────────────────────────────────────────────────────

def test_its_description_type_and_shape_come_from_the_sidecar(tmp_path):
    _agent(tmp_path, "a1", [_line("user", when=NOW - 400), _line("assistant", stop="end_turn")],
           written=NOW - 100, meta={**META, "spawnDepth": 2})

    (a,), _ = _read(tmp_path)

    assert (a.agent_id, a.description, a.agent_type, a.background, a.depth) == (
        "a1", "Write the release notes", "general-purpose", True, 2)
    assert a.started == pytest.approx(NOW - 400)
    assert a.written == pytest.approx(NOW - 100)


def test_a_missing_or_broken_sidecar_leaves_the_fields_empty(tmp_path):
    _agent(tmp_path, "bare", [_line("user")], written=NOW - 5)
    p = _agent(tmp_path, "broken", [_line("user")], written=NOW - 5)
    p.with_name("agent-broken.meta.json").write_text('{"agentType": "Expl')    # caught mid-write

    agents, _ = _read(tmp_path)

    assert {a.agent_id for a in agents} == {"bare", "broken"}
    for a in agents:
        assert (a.description, a.agent_type, a.background, a.depth) == ("", "", False, 0)


def test_the_first_line_without_a_time_is_passed_over_for_the_start(tmp_path):
    """A fork's first line is a context reference with no timestamp."""
    _agent(tmp_path, "fork", [{"type": "fork-context-ref"}, _line("user", when=NOW - 70)],
           written=NOW - 5)

    (a,), _ = _read(tmp_path)

    assert a.started == pytest.approx(NOW - 70)


def test_working_ones_come_first_then_the_most_recently_written(tmp_path):
    _agent(tmp_path, "old", [_line("assistant", stop="end_turn")], written=NOW - 900)
    _agent(tmp_path, "new", [_line("assistant", stop="end_turn")], written=NOW - 30)
    _agent(tmp_path, "busy", [_line("user")], written=NOW - 10)

    agents, _ = _read(tmp_path)

    assert [a.agent_id for a in agents] == ["busy", "new", "old"]


# ── the folder belongs to another process ───────────────────────────────────

def test_the_cap_is_kept_and_said(tmp_path):
    for i in range(sw.MAX_AGENT_FILES + 5):
        _agent(tmp_path, f"n{i:04d}", [_line("user")], written=NOW - 4000)

    agents, capped = _read(tmp_path)

    assert len(agents) == sw.MAX_AGENT_FILES and capped is True


def test_the_same_file_under_two_roots_is_one_subagent(tmp_path):
    a, b = tmp_path / ".claude", tmp_path / ".claude-orcha"
    for root in (a, b):
        _agent(root, "same", [_line("user")], written=NOW - 5, meta=META)
    _agent(b, "only-b", [_line("user")], written=NOW - 5)

    agents, _ = sw.read_subagents([a, b], "/p/one", "s", NOW)

    assert sorted(x.agent_id for x in agents) == ["only-b", "same"]


def test_a_file_that_vanishes_between_listing_and_reading_is_left_out(tmp_path, monkeypatch):
    _agent(tmp_path, "here", [_line("user")], written=NOW - 5)
    real = Path.iterdir

    def listing_with_a_ghost(self):
        found = list(real(self))
        return iter(found + [self / "agent-ghost.jsonl"] if self.name == "subagents" else found)

    monkeypatch.setattr(Path, "iterdir", listing_with_a_ghost)

    agents, _ = _read(tmp_path)

    assert [a.agent_id for a in agents] == ["here"]


def test_a_symlink_is_never_followed_out_of_the_folder(tmp_path):
    outside = tmp_path / "secret.jsonl"
    outside.write_text(json.dumps(_line("assistant", stop="end_turn", text="private")) + "\n")
    d = _folder(tmp_path)
    (d / "agent-evil.jsonl").symlink_to(outside)
    p = _agent(tmp_path, "real", [_line("user")], written=NOW - 5)
    secret_meta = tmp_path / "secret.meta.json"
    secret_meta.write_text(json.dumps({"description": "read through a link"}))
    p.with_name("agent-real.meta.json").symlink_to(secret_meta)

    agents, _ = _read(tmp_path)

    assert [a.agent_id for a in agents] == ["real"]
    assert agents[0].description == ""
    assert sw.subagent_transcript_path([tmp_path], "/p/one", "s", "evil") is None


def test_no_folder_at_all_is_no_subagents(tmp_path):
    assert _read(tmp_path) == ([], False)


def test_an_id_from_a_request_cannot_leave_the_folder(tmp_path):
    _agent(tmp_path, "ok", [_line("user")], written=NOW - 5)
    for bad in ("../../etc/passwd", "a/b", "", "x" * 200, "ok.jsonl"):
        assert sw.subagent_transcript_path([tmp_path], "/p/one", "s", bad) is None
    assert sw.subagent_transcript_path([tmp_path], "/p/one", "s", "ok") is not None


def test_the_counts_are_made_from_the_same_reading(tmp_path):
    _agent(tmp_path, "w1", [_line("user")], written=NOW - 5)
    _agent(tmp_path, "w2", [_line("user")], written=NOW - 6)
    _agent(tmp_path, "f", [_line("assistant", stop="end_turn")], written=NOW - 3)

    assert sw.count_agents(tmp_path, "/p/one", "s", NOW) == (3, 2, False)


# ── on the snapshot ─────────────────────────────────────────────────────────

def _live_session(root, monkeypatch):
    monkeypatch.setattr(sw, "pid_alive", lambda pid: True)
    write_roster(root, pid=os.getpid(), session_id="s", cwd="/p/one", name="one", status="busy")
    write_transcript(root, cwd="/p/one", session_id="s", last_prompt="carry on")


def test_the_snapshot_carries_each_subagent(tmp_path, monkeypatch):
    _live_session(tmp_path, monkeypatch)
    _agent(tmp_path, "w", [_line("user", when=NOW - 50)], written=NOW - 5, meta=META)
    _agent(tmp_path, "f", [_line("assistant", stop="end_turn")], written=NOW - 500)

    s = sw.build_snapshot(roots=[tmp_path], now=NOW).by_id("s")
    body = sw.session_to_dict(s)

    assert (body["agents_seen"], body["agents_active"]) == (2, 1)
    assert body["subagents"] == [
        {"id": "w", "description": "Write the release notes", "type": "general-purpose",
         "background": True, "depth": 1, "state": "working",
         "started": pytest.approx(NOW - 50), "written": pytest.approx(NOW - 5)},
        {"id": "f", "description": "", "type": "", "background": False, "depth": 0,
         "state": "finished", "started": pytest.approx(NOW - 60), "written": pytest.approx(NOW - 500)},
    ]


def test_a_codex_session_has_no_subagents(tmp_path):
    s = sw.SessionState(session_id="c", cwd="/p", project="p", state=sw.IDLE, provider="openai")
    assert sw.session_to_dict(s)["subagents"] == []


def test_the_agents_list_says_how_many_are_working(tmp_path, monkeypatch):
    from jarvis import agent_directory
    _live_session(tmp_path, monkeypatch)
    _agent(tmp_path, "w1", [_line("user")], written=NOW - 5)
    _agent(tmp_path, "w2", [_line("user")], written=NOW - 5)
    _agent(tmp_path, "f", [_line("assistant", stop="end_turn")], written=NOW - 5)
    snap = sw.build_snapshot(roots=[tmp_path], now=NOW)
    agent_directory.connect(lambda: snap, rows=lambda: [], needs=lambda: {})
    try:
        (agent,) = agent_directory.agents()
    finally:
        agent_directory.connect(sw.Snapshot)

    assert (agent.subagents_working, agent.subagents_capped) == (2, False)


# ── the routes ──────────────────────────────────────────────────────────────

@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store, workflow_store
    importlib.reload(run_store)
    importlib.reload(workflow_store)
    from jarvis import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    root = tmp_path / ".claude"
    _live_session(root, monkeypatch)
    watcher = sw.SessionWatcher(roots=[root], interval=999)
    monkeypatch.setattr(server_module, "session_watcher", watcher)
    monkeypatch.setattr(server_module, "_session_provider_named",
                        lambda name: sw.ClaudeSessions([root]))
    return server_module, run_store, workflow_store, watcher, root


def _attach(workflow_store, name, session_id):
    wid = workflow_store.start(name, "one", ["Build"])
    workflow_store.record(workflow_store.WorkflowEvent(wid, "agent_attached", None, "Attached.",
                                                       {"session_id": session_id}))
    return wid


def test_a_subagent_of_no_workflow_gets_its_own_row_in_the_workflows_list(server):
    srv, _run_store, workflow_store, watcher, root = server
    now = time.time()
    _agent(root, "w", [_line("user")], written=now, meta=META)
    _agent(root, "just-done", [_line("assistant", stop="end_turn")], written=now - 30)
    _agent(root, "long-done", [_line("assistant", stop="end_turn")],
           written=now - workflow_store.LINGER_SEC - 60)
    watcher.poll_once()
    _attach(workflow_store, "Elsewhere", "someone-else")

    body = TestClient(srv.app).get("/api/workflows").json()

    rows = {r["id"]: r for r in body["subagents"]}
    assert set(rows) == {"w", "just-done"}, "one long finished has left, as a workflow does"
    w = rows["w"]
    assert (w["key"], w["session_id"], w["session_name"], w["description"], w["state"],
            w["dismiss_at"]) == ("agent:s/w", "s", "one", "Write the release notes", "working", None)
    assert rows["just-done"]["dismiss_at"] == pytest.approx(now - 30 + workflow_store.LINGER_SEC)
    # Shown, never recorded: the store holds only what sessions reported.
    assert [x["name"] for x in body["workflows"]] == ["Elsewhere"]


def test_a_subagent_of_an_attached_session_shows_inside_its_workflow(server):
    srv, _run_store, workflow_store, watcher, root = server
    wid = _attach(workflow_store, "Release", "s")
    now = time.time()
    _agent(root, "w", [_line("user")], written=now, meta=META)
    _agent(root, "before", [_line("assistant", stop="end_turn")], written=now - 3600)
    watcher.poll_once()
    client = TestClient(srv.app)

    assert client.get("/api/workflows").json()["subagents"] == []
    detail = client.get(f"/api/workflows/{wid}").json()
    assert [a["id"] for a in detail["subagents"]] == ["w"], "only what it dispatched while the workflow ran"
    transcript = client.get(f"/api/workflows/{wid}/transcript").json()
    assert [a["key"] for a in transcript["subagents"]] == ["agent:s/w"]


def test_once_its_workflow_finishes_a_sessions_subagents_stand_alone(server):
    srv, _run_store, workflow_store, watcher, root = server
    wid = _attach(workflow_store, "Release", "s")
    workflow_store.record(workflow_store.WorkflowEvent(wid, "workflow_finished", None, "Done.",
                                                       {"outcome": "succeeded"}))
    _agent(root, "w", [_line("user")], written=time.time(), meta=META)
    watcher.poll_once()

    body = TestClient(srv.app).get("/api/workflows").json()

    assert [a["id"] for a in body["subagents"]] == ["w"]


def test_a_subagents_transcript_is_served_by_the_sessions_route(server):
    srv, _run_store, _wf, watcher, root = server
    _agent(root, "w", [_line("user", text="go and look"),
                       _line("assistant", stop="end_turn", text="Found it.")],
           written=time.time(), meta=META)
    watcher.poll_once()
    client = TestClient(srv.app)

    r = client.get("/api/sessions/s/transcript", params={"subagent": "w"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["subagent"], body["name"], body["state"], body["session_name"]) == (
        "w", "Write the release notes", "finished", "one")
    assert [(e["role"], e["text"]) for e in body["entries"]] == [
        ("you", "go and look"), ("agent", "Found it.")]
    for bad in ("nope", "../w"):
        assert client.get("/api/sessions/s/transcript", params={"subagent": bad}).status_code == 404


def test_a_change_in_any_sessions_subagents_is_a_reason_to_tell_the_pages(server):
    srv, _run_store, _wf, watcher, root = server
    watcher.poll_once()
    srv._subagents_moved()
    assert srv._subagents_moved() is False

    _agent(root, "w", [_line("user")], written=time.time())
    watcher.poll_once()

    assert srv._subagents_moved() is True
    assert srv._subagents_moved() is False
