"""The agent directory: one way to list and find agents. A name matches
however it is spelled aloud ("Tokens for Cash" is tokens-for-cash), active
and inactive agents are both found, and a name nobody goes by is answered
with every agent's name."""

import importlib

import pytest

import agent_directory
import session_watch as sw


def _session(sid, name, project, state=sw.IDLE):
    return sw.SessionState(session_id=sid, cwd=f"/p/{project}", project=project,
                           state=state, voice_name=name, steerable=True,
                           pids=[1], primary_pid=1)


SESSIONS = [_session("t", "tokens-for-cash", "tokens-for-cash"),
            _session("h", "hammer", "hammer", sw.WORKING),
            _session("g", "Agent Guard", "agent_guard")]


@pytest.fixture
def directory():
    rows = [{"session_id": "h", "name": "hammer", "active": True, "state": sw.WORKING},
            {"session_id": "t", "name": "tokens-for-cash", "active": False, "state": sw.IDLE}]
    agent_directory.connect(lambda: sw.Snapshot(sessions=SESSIONS),
                            rows=lambda: rows, needs=lambda: {"hammer": 2})
    yield
    importlib.reload(agent_directory)


@pytest.mark.parametrize("said", ["Tokens for Cash", "tokens-for-cash", "tokens_for_cash",
                                  "TOKENS FOR CASH", "tell tokens for cash"])
def test_a_name_matches_however_it_is_spelled(directory, said):
    assert [a.session_id for a in agent_directory.find(said)] == ["t"]


def test_spoken_key_turns_punctuation_into_spaces():
    assert agent_directory.spoken_key("  Tokens-for_Cash.v2, ") == "tokens for cash v2"
    assert agent_directory.same_name("Agent Guard", "agent-guard")


def test_every_agent_is_listed_active_and_inactive_in_the_table_s_order(directory):
    got = [(a.name, a.active, a.project, a.needs) for a in agent_directory.agents()]
    assert got == [("hammer", True, "hammer", 2),
                   ("tokens-for-cash", False, "tokens-for-cash", 0),
                   ("Agent Guard", False, "agent_guard", 0)], \
        "one the table has not caught up with yet is still listed, last"


def test_an_inactive_agent_is_found_like_an_active_one(directory):
    assert [a.name for a in agent_directory.find("agent guard")] == ["Agent Guard"]


def test_that_one_is_the_agent_last_talked_about(directory):
    assert [a.name for a in agent_directory.find("that one", last_mentioned="h")] == ["hammer"]


def test_nothing_is_guessed(directory):
    assert agent_directory.find("anvil") == []
    assert agent_directory.find("") == []


# --- through the tools --------------------------------------------------------

@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import server as server_module
    importlib.reload(server_module)
    server_module.run_store.init_db()
    monkeypatch.setattr(server_module, "_snapshot_or_empty",
                        lambda: sw.Snapshot(sessions=SESSIONS))
    agent_directory.connect(lambda: sw.Snapshot(sessions=SESSIONS))
    yield server_module
    importlib.reload(agent_directory)


def test_steering_by_the_spoken_name_finds_the_hyphenated_agent(server):
    session, problem, _ = server._resolve_or_explain("Tokens for Cash")
    assert problem is None and session.session_id == "t"


def test_a_name_nobody_goes_by_is_answered_with_every_agent_s_name(server):
    _, problem, reason = server._resolve_or_explain("anvil")
    assert reason == "unresolved"
    assert problem == ("No agent is called anvil. The agents running are:\n"
                       "tokens-for-cash\nhammer\nAgent Guard")


@pytest.mark.asyncio
async def test_a_capture_is_addressed_by_the_agent_s_own_name(server, monkeypatch):
    async def _quiet(msg):
        pass
    monkeypatch.setattr(server, "_voice_emit", _quiet)
    server.tool_capture({"text": "Ship it.", "to": "Tokens for Cash"})
    assert server._captures[1]["to"] == "tokens-for-cash"


def test_a_capture_for_nobody_is_refused_while_he_is_still_there(server):
    out = server.tool_capture({"text": "Ship it.", "to": "anvil"})
    assert out.startswith("No agent is called anvil.") and "Nothing was captured" in out
    assert server._captures == {}


def test_a_rename_to_a_name_that_sounds_like_another_agent_s_is_refused(server):
    server.run_store.sync_agents({s.session_id: (s.voice_name, s.state) for s in SESSIONS})
    assert server._rename_agent("h", "Tokens For Cash") == (None, "duplicate")


def test_the_one_listing_names_every_agent(server):
    out = server.tool_list_agents({})
    assert out.splitlines()[0] == "Three agents:"
    assert "tokens-for-cash: inactive, idle, in tokens-for-cash." in out
    assert server.tool_list_agents({"filter": "active"}) == "No agent is active, sir."
