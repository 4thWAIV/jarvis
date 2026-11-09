"""A coordinator's subagents on the Work board (work_store `agent:<session>/sub:<agent id>`).

A piece may name one of a session's subagents by the agent id the Agent
tool gave its dispatcher, which is the id that names its transcript
(`subagents/agent-<id>.jsonl`). Its own files are live evidence: while the
session watcher sees it written within AGENT_ACTIVE_WITHIN_SEC the piece
reads Working (it glows, no activity_unknown); finished or gone quiet, the
piece says so. The piece shows the subagent's name (its description) and
what it is doing now (its last tool or line, walled for the brain)."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from jarvis import session_watch, work_store, worker_inventory
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (the fixture)
from tests.test_work_states import _key, _piece, _report

SESSION, AGENT = "a5b4be76-7b3d-4a0c-9220-817d38b9e5f3", "aeb4b37bbf9ed2571"
WHO = {"kind": "agent", "ref": f"{SESSION}/sub:{AGENT}"}


@pytest.fixture
def item(wired, tmp_path):
    folder = tmp_path / "release"
    folder.mkdir()
    return work_store.create("Release", "folder", str(folder))


@pytest.fixture
def subagent(wired, monkeypatch):
    """The coordinator's session in the snapshot, with one subagent whose
    state and last line each test sets."""
    agent = {"state": session_watch.AGENT_WORKING, "written": time.time()}
    entries = [session_watch.TranscriptEntry(0, None, "agent", "Merging PR 77 into main."),
               session_watch.TranscriptEntry(1, None, "tool", "gh pr merge 77", tool="Bash")]

    def snapshot():
        sub = session_watch.Subagent(agent_id=AGENT, description="Release agent",
                                     agent_type="general-purpose", background=True, depth=1,
                                     state=agent["state"], started=None,
                                     written=agent["written"], worker_activity={"state": "model_work" if agent["state"] == "working" else "unknown", "observed_at": agent["written"]})
        session = session_watch.SessionState(session_id=SESSION, provider="anthropic", project="test", subagents=[sub],
                                  cwd="/Users/someone/code/jarvis",
                                  # Only the watcher's view of it: the agent directory
                                  # (who reported, by key) passes it over.
                                  state=session_watch.IDLE)
        return session_watch.Snapshot([session],time.time())

    provider = SimpleNamespace(subagent_transcript=lambda s, aid, before, limit:
                               entries if aid == AGENT else None, roots=[wired.tmp / "no-root"])
    monkeypatch.setattr(wired.server, "_snapshot_or_empty", snapshot)
    monkeypatch.setattr(wired.server, "_worker_inventory", lambda: worker_inventory.build(snapshot(), now=time.time()))
    monkeypatch.setattr(wired.server, "_session_provider_named", lambda name: provider)
    work_store.look_up_live_with(wired.server._subagent_now)
    return agent


@pytest.mark.parametrize("ref,ok", [
    (f"{SESSION}/sub:{AGENT}", True),
    (f"{SESSION}/sub:bad id", False),
    (f"{SESSION}/sub:", False),
    (f"/sub:{AGENT}", False),
])
def test_a_subagent_is_named_by_its_session_and_agent_id(ref, ok):
    if ok:
        assert work_store.subagent_of(work_store.who("agent", ref)) == (SESSION, AGENT)
    else:
        with pytest.raises(work_store.WorkRefused, match="sub:<agent id>|names its id"):
            work_store.who("agent", ref)
    with pytest.raises(work_store.WorkRefused):
        work_store.who("run", f"run-1/sub:{AGENT}")


def test_a_subagent_jarvis_cannot_find_is_refused(wired, item, subagent):
    r = _piece(wired, item.id, "pr77", auth=_key(), ok=False, state="working",
               who={"kind": "agent", "ref": f"{SESSION}/sub:aunknown0000000"})
    assert "JARVIS has no agent" in r["error"]


def test_model_request_is_visible_but_does_not_prove_assignment_generation(wired, item, subagent):
    _piece(wired, item.id, "pr77", auth=_key(), state="working", who=WHO)
    got = wired.client.get(f"/api/work/{item.id}").json()["item"]
    [piece] = got["pieces"]
    assert piece["state"] == "activity_unknown"
    assert piece["live"]["activity"]["model_work"]
    assert not piece["live"]["activity"]["generating"]
    assert piece["live"]["activity"]["orb_state"] == "thinking"
    assert piece["live"]["snapshot_id"]
    assert got["state"] == "activity_unknown"


@pytest.mark.parametrize("state,reason", [
    (session_watch.AGENT_FINISHED, "Its subagent has finished."),
    (session_watch.AGENT_STALE, "Current token generation for this assignment is not confirmed."),
])
def test_finished_or_gone_quiet_the_piece_says_so(wired, item, subagent, state, reason):
    subagent["state"] = state
    _piece(wired, item.id, "pr77", auth=_key(), state="working", who=WHO)
    [piece] = wired.client.get(f"/api/work/{item.id}").json()["item"]["pieces"]
    assert (piece["state"], piece["activity_reason"], piece["live"]["state"]) == (
        "activity_unknown", reason, state)


def test_a_session_s_own_working_claim_is_still_unconfirmed(wired, item, subagent, monkeypatch):
    """The session itself (between turns) is no evidence; only a subagent is."""
    monkeypatch.setattr(wired.server.agent_directory, "names_by_session",
                        lambda: {SESSION: "coordinator"})
    _piece(wired, item.id, "plan", auth=_key(), state="working",
           who={"kind": "agent", "ref": SESSION})
    [piece] = wired.client.get(f"/api/work/{item.id}").json()["item"]["pieces"]
    assert piece["state"] == "activity_unknown" and piece["live"]["state"] == "finished"


def test_the_agent_id_is_the_one_that_names_its_transcript(tmp_path):
    """read_subagents takes each id from `agent-<id>.jsonl`, the name Claude
    Code gives the transcript of the agent the Agent tool returned that id for."""
    cwd = "/Users/someone/code/jarvis"
    folder = tmp_path / "projects" / session_watch.encode_cwd(cwd) / SESSION / "subagents"
    folder.mkdir(parents=True)
    (folder / f"agent-{AGENT}.jsonl").write_text(
        '{"type":"user","agentId":"%s","sessionId":"%s","isSidechain":true}\n' % (AGENT, SESSION))
    (folder / f"agent-{AGENT}.meta.json").write_text('{"description":"Release agent"}')
    agents, _ = session_watch.read_subagents(tmp_path, cwd, SESSION, time.time())
    assert [(a.agent_id, a.description, a.state) for a in agents] == [
        (AGENT, "Release agent", session_watch.AGENT_WORKING)]


# -- found by id whatever the cap, and waiting on its own background work ------------

CWD = "/Users/someone/code/jarvis"


def _write_agent(root, agent_id, lines, meta=None):
    folder = root / "projects" / session_watch.encode_cwd(CWD) / SESSION / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    import json
    (folder / f"agent-{agent_id}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    (folder / f"agent-{agent_id}.meta.json").write_text(json.dumps(
        {"description": f"Agent {agent_id}", **(meta or {})}))


def _now_iso(offset=0.0):
    import datetime
    return datetime.datetime.fromtimestamp(time.time() + offset, datetime.timezone.utc).isoformat()


FINISHED = {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
                                             "content": [{"type": "text", "text": "Waiting for CI."}]}}


def _started(task, monitor=True, offset=0.0):
    words = (f"Monitor started (task {task}, expires in 30m)" if monitor
             else f"Command running in background with ID: {task}. Output is being written")
    return {"type": "user", "timestamp": _now_iso(offset),
            "message": {"role": "user", "content": [{"type": "tool_result",
                                                     "tool_use_id": "t", "content": words}]}}


def _notice(task, status=True):
    body = f"<task-notification>\n<task-id>{task}</task-id>\n" + (
        "<status>completed</status>\n" if status else "<event>runner: SUCCESS</event>\n") + \
        "</task-notification>"
    return {"type": "user", "message": {"role": "user", "content": body}}


@pytest.fixture
def real_session(wired, monkeypatch, tmp_path):
    """The coordinator's session in the snapshot as the watcher reads it,
    its subagents in real files under a config root of its own: the
    snapshot's list is read with the cap, as the live one is."""
    root = tmp_path / "claude"

    def snapshot():
        agents, _capped = session_watch.read_subagents(root, CWD, SESSION, time.time(), limit=None)
        session = session_watch.SessionState(session_id=SESSION, provider="anthropic", project="test", subagents=agents,
                                  cwd=CWD,
                                  # Only the watcher's view of it: the agent directory
                                  # (who reported, by key) passes it over.
                                  state=session_watch.IDLE)
        return session_watch.Snapshot([session],time.time())

    provider = SimpleNamespace(roots=[root], subagent_transcript=lambda *a: [])
    monkeypatch.setattr(wired.server, "_snapshot_or_empty", snapshot)
    monkeypatch.setattr(wired.server, "_worker_inventory", lambda: worker_inventory.build(snapshot(), now=time.time()))
    monkeypatch.setattr(wired.server, "_session_provider_named", lambda name: provider)
    work_store.look_up_live_with(wired.server._subagent_now)
    return root


def test_an_agent_named_by_id_is_found_whatever_the_cap(wired, real_session):
    for n in range(session_watch.MAX_AGENT_FILES + 5):
        _write_agent(real_session, f"a{n:04d}", [FINISHED])
    _write_agent(real_session, "zlate0000000001", [{"type": "user", "message": {"content": "go"}}])
    listed, capped = session_watch.read_subagents(real_session, CWD, SESSION, time.time())
    assert capped and all(a.agent_id != "zlate0000000001" for a in listed)
    live = wired.server._subagent_now(SESSION, "zlate0000000001")
    assert (live.name, live.state) == ("Agent zlate0000000001", "stale")


def test_ended_its_turn_but_waiting_on_its_monitor_says_so(wired, real_session, item):
    _write_agent(real_session, "arelease0000001", [_started("bmon1"), _notice("bmon1", False),
                                                     FINISHED])
    _piece(wired, item.id, "pr77", auth=_key(), state="working",
           who={"kind": "agent", "ref": f"{SESSION}/sub:arelease0000001"})
    [piece] = wired.client.get(f"/api/work/{item.id}").json()["item"]["pieces"]
    assert (piece["live"]["state"], piece["activity_reason"]) == (
        "waiting", "Its subagent is waiting on its own background work.")


@pytest.mark.parametrize("lines", [
    [_started("bmon1"), _notice("bmon1", True), FINISHED],              # it ended
    [_started("bcmd1", monitor=False),
     {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t",
                                                "content": "Successfully stopped task: bcmd1"}]}},
     FINISHED],                                                          # it was stopped
    [_started("bmon1", offset=-(session_watch.MONITOR_MAX_SEC + 60)), FINISHED],  # expired
    [{"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t",
                                               "content": "grep: running in background with ID: bquoted"}]}},
     FINISHED],                                                          # only quoted
])
def test_with_no_background_work_left_it_has_finished(wired, real_session, lines):
    _write_agent(real_session, "arelease0000002", lines)
    assert wired.server._subagent_now(SESSION, "arelease0000002").state == "finished"


# -- the item says what the subagent on it is doing ---------------------------------

def test_a_subagent_waiting_on_background_work_is_the_item_s_line_never_stalled(
        wired, real_session, item, monkeypatch):
    """work-06181b8c5de3: pr77's agent waiting on its CI monitor."""
    _write_agent(real_session, "arelease0000003", [_started("bmon1"), FINISHED],
                 meta={"description": "Release agent"})
    import json
    folder = real_session / "projects" / session_watch.encode_cwd(CWD) / SESSION / "subagents"
    (folder / "agent-arelease0000003.meta.json").write_text(json.dumps({"description":
                                                                        "Release agent"}))
    _piece(wired, item.id, "pr77", auth=_key(), state="working",
           who={"kind": "agent", "ref": f"{SESSION}/sub:arelease0000003"})
    later = time.time() + work_store.QUIET_AFTER_SEC + 60
    monkeypatch.setattr(work_store.time, "time", lambda: later)
    got = wired.client.get(f"/api/work/{item.id}").json()["item"]
    assert (got["stalled"], got["live"]["state"], got["live"]["name"]) == (
        False, "waiting", "Release agent")
    assert got["activity_reason"] == "Waiting on Release agent's background work."
    listed = wired.client.get("/api/work").json()["items"][0]
    assert (listed["stalled"], listed["activity_reason"]) == (
        False, "Waiting on Release agent's background work.")
    assert "Waiting on Release agent's background work." in _call(wired, "work_status")["text"]


def test_a_request_phase_does_not_change_work_business_state(wired, item, subagent):
    _piece(wired, item.id, "pr77", auth=_key(), state="working", who=WHO)
    got = wired.client.get(f"/api/work/{item.id}").json()["item"]
    assert got["state"] == "activity_unknown"
    assert got["pieces"][0]["live"]["activity"]["orb_state"] == "thinking"
