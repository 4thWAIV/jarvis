"""An agent has one name, and every surface says it.

Live, 2026-11-06: Tim sent two messages to the agent called Denice and they
were recorded, read back and shown as "the older denice"; its reply came back
from "the denice that's working"; an ended agent was "the jarvis that hasn't
started". Those were labels `session_watch._assign_voice_names` made up to
tell two conversations in one folder apart, and they were the field
(`voice_name`) that every read-back, record, tool result and lookup used.

The rule now: the agents table's `name` is the agent's one name (set once
when it is first seen, its plain derived name; changed only by his rename,
for good). The session snapshot goes by it, so the voice name, the agents
list, the read-back, the messages row, the conversation record, the
attention item and the reported reply all say it. Two agents of one name
are kept apart by session id, and a name that fits both is asked about.
"""

import asyncio
import re
from pathlib import Path

import pytest

from jarvis import session_steer
from jarvis import session_watch as sw
from tests.attention_fixtures import attention  # noqa: F401  (a fixture)
from tests.fixtures.captures import captured

DENICE = "98880f86-b367-4325-99c7-79e5eb22df1f"
DESIGNER = "01a1af11-4760-7c10-9fd8-c52d47fbbaa3"
HAMMER_A = "aaaaaaaa-0000-0000-0000-000000000001"
HAMMER_B = "bbbbbbbb-0000-0000-0000-000000000002"
ORIGIN = {"Origin": "http://localhost:5173"}

# Every label the old naming chain could produce, as words.
MADE_UP = re.compile(r"\b(newer|older|newest|second|third)\b|that's|that hasn't|that needs"
                     r"|in an unclear state|, the .* one\b|number \d")


class _Watcher:
    def __init__(self, sessions):
        self.snapshot = sw.Snapshot(sessions=sessions, taken_at=1.0)
        self.polled = True


def _session(sid, cwd, state="idle", given="", started=1_000.0):
    s = sw.SessionState(session_id=sid, cwd=cwd, project=sw.project_name(cwd), state=state,
                        given_name=given, steerable=True, socket_path="/x.sock",
                        pids=[1], primary_pid=1, started=started, since=started,
                        between_turns=True)
    return s


class _Speech:
    def __init__(self):
        self.said = []

    async def say(self, text, *a, **k):
        self.said.append(text)

    async def open_cancel_window(self, *a, **k):
        return False


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import server as server_module
    importlib.reload(server_module)
    server_module.run_store.init_db()
    monkeypatch.setattr(server_module, "_inbound_accepted", lambda: True)
    monkeypatch.setattr(server_module, "_notes_for_brain", [])
    monkeypatch.setattr(server_module, "speech", _Speech())
    return server_module


def _live(server, monkeypatch, sessions):
    """These sessions running, the agents list synced to them the way the
    server's loop does it, and the name cache cleared."""
    monkeypatch.setattr(server, "session_watcher", _Watcher(sessions))
    server._forget_agent_names()
    server._sync_agents()
    server._forget_agent_names()


@pytest.fixture
def denice_and_designer(server, monkeypatch):
    """Live: two conversations in the denice folder. One was named Denice in
    Claude Code; the other he renamed Designer in JARVIS. Designer was seen
    first under the plain derived name "denice"."""
    sessions = [_session(DENICE, "/Users/t/code/denice", given="Denice", started=1_000.0),
                _session(DESIGNER, "/Users/t/code/denice", state="working", started=2_000.0)]
    _live(server, monkeypatch, sessions)
    row, problem = server._rename_agent(DESIGNER, "Designer")
    assert problem is None and row["name"] == "Designer"
    return server


# --- the directory: a name finds exactly its agent ------------------------------

def test_each_name_finds_exactly_its_own_session(denice_and_designer):
    from jarvis import agent_directory
    assert [a.session_id for a in agent_directory.find("Denice")] == [DENICE]
    assert [a.session_id for a in agent_directory.find("denice")] == [DENICE]
    assert [a.session_id for a in agent_directory.find("Designer")] == [DESIGNER]
    assert {a.name for a in agent_directory.agents()} == {"Denice", "Designer"}


def test_every_surface_reads_the_one_name(denice_and_designer, monkeypatch):
    """The agents table, the snapshot's voice name, the agents route, the
    sessions route and the brain's agents list all say the same name."""
    server = denice_and_designer
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    names = server.run_store.agent_names()
    snap = {s.session_id: s.voice_name for s in server._snapshot_or_empty().sessions}
    route = {a["session_id"]: a["name"] for a in client.get("/api/agents").json()["agents"]}
    assert names == snap == route == {DENICE: "Denice", DESIGNER: "Designer"}
    sessions = {s["session_id"]: s["voice_name"]
                for s in client.get("/api/sessions").json()["sessions"]}
    assert sessions == {DENICE: "Denice", DESIGNER: "Designer"}
    listed = server.tool_list_agents({})
    assert "\nDenice: " in listed and "\nDesigner: " in listed
    assert not MADE_UP.search(listed), listed


# --- one message to each, through every record ----------------------------------

async def _send(server, monkeypatch, name, text):
    """Capture it to `name`, read back and approved, staged and sent: the
    session it went to and what each record says."""
    delivered = []

    def deliver(item, body):
        delivered.append(item.session_id)
        return session_steer.SENT

    monkeypatch.setattr(server, "_deliver", deliver)
    monkeypatch.setattr(server, "_spawn", lambda coro: coro.close())
    out = server.tool_capture({"text": text, "to": name})
    assert "holds it" in out, out
    number = max(server._captures)
    frame = server._capture_message(server._captures[number], "capturing")
    args = captured(server, {"name": name, "prompt": text})
    await server.tool_steer_session(args)
    await server._perform_staged_steers()
    return delivered, frame


@pytest.mark.asyncio
@pytest.mark.parametrize("name, sid", [("Denice", DENICE), ("Designer", DESIGNER)])
async def test_a_message_goes_to_its_agent_and_every_record_names_it(
        denice_and_designer, monkeypatch, attention, name, sid):
    server = denice_and_designer
    monkeypatch.setattr(server, "attention_record", attention)

    delivered, frame = await _send(server, monkeypatch, name, "Rename the remote.")

    assert delivered == [sid], "the message reached that agent's session and no other"
    assert frame["to"] == name, "the capture box's label"
    (m,) = server.run_store.list_messages()
    assert (m.session_id, m.agent_name) == (sid, name), "the messages row"
    items = server.conversation_log.page()
    named = {(str(i.kind), i.agent_session_id, i.agent_name) for i in items if i.agent_name}
    assert ("capture", sid, name) in named and ("to_agent", sid, name) in named
    assert all(n == name for _k, _s, n in named), named

    # The watcher reports the answer with the session as IT names it (the
    # plain derived name), and the server puts the one name on it.
    spawned = []
    monkeypatch.setattr(server, "_spawn", spawned.append)
    server._on_session_event({"kind": "answered", "at": 5.0,
                              "message": {"id": m.id, "session_id": sid, "body": "x",
                                          "reply": "Done."},
                              "session": {"session_id": sid, "voice_name": "denice",
                                          "project": "denice"}})
    for coro in spawned:
        await coro
    assert {i.untrusted.get("agent") for i in attention.items()} == {name}, "the attention item"
    server.run_store.update_message(m.id, status="answered", reply="Done.", answered_at=6.0)
    assert server.tool_read_message_reply({"message": m.id}).startswith(name), \
        "the reported reply names the agent that answered"
    assert f"to {name}," in server.tool_list_messages({})
    said = server.speech.said
    assert not any(MADE_UP.search(s) for s in said), said


# --- two agents of one name -----------------------------------------------------

@pytest.fixture
def two_hammers(server, monkeypatch):
    monkeypatch.setattr(server, "_spawn", lambda coro: coro.close())
    _live(server, monkeypatch, [
        _session(HAMMER_A, "/Users/t/Desktop/hammer", started=1_000.0),
        _session(HAMMER_B, "/Users/t/code/hammer", state="working", started=2_000.0)])
    return server


def test_two_agents_of_one_name_are_neither_relabelled(two_hammers):
    server = two_hammers
    assert server.run_store.agent_names() == {HAMMER_A: "hammer", HAMMER_B: "hammer"}
    assert [s.voice_name for s in server._snapshot_or_empty().sessions] == ["hammer", "hammer"]


def test_a_name_that_fits_two_is_asked_about_with_a_fact_beside_the_name(two_hammers):
    server = two_hammers
    out = server.tool_capture({"text": "Ship it.", "to": "hammer"})
    assert "Which one?" in out and "Nothing was captured" in out
    assert out.count("\nhammer (") == 2, out
    assert HAMMER_A in out and HAMMER_B in out, "the brain can pick one by its id"
    assert "in Desktop" in out and "in code" in out, "the folders differ, so they are said"
    assert not MADE_UP.search(out), out
    assert not server._captures, "nothing was captured to either"

    session, problem, reason = server._resolve_or_explain("hammer")
    assert session is None and reason == "ambiguous" and "Which one?" in problem
    assert "Which one?" in server.tool_set_agent_active({"name": "hammer", "active": True})


def test_the_session_id_picks_one_of_two_of_one_name(two_hammers):
    server = two_hammers
    out = server.tool_capture({"text": "Ship it.", "to": HAMMER_B})
    assert "holds it" in out
    capture = server._captures[max(server._captures)]
    assert (capture["to"], capture["to_session"]) == ("hammer", HAMMER_B)


def test_send_it_to_a_name_two_agents_share_approves_neither(two_hammers):
    server = two_hammers
    server.tool_capture({"text": "Ship it.", "to": HAMMER_A})
    capture = server._captures[max(server._captures)]
    assert server._names_recipient("hammer", capture) is False
    assert server._names_recipient(HAMMER_A, capture) is True


# --- a rename is the name, for good ---------------------------------------------

def test_a_rename_is_kept_across_a_new_claude_code_name_a_resume_and_a_restart(
        denice_and_designer, monkeypatch):
    server = denice_and_designer
    import importlib
    renamed = [_session(DENICE, "/Users/t/code/denice", given="Denice"),
               _session(DESIGNER, "/Users/t/code/denice", given="A new Claude Code title")]
    _live(server, monkeypatch, renamed)
    _live(server, monkeypatch, [renamed[0]])                 # Designer stopped
    _live(server, monkeypatch, renamed)                      # and resumed
    importlib.reload(server)                                 # JARVIS restarted
    server.run_store.init_db()
    _live(server, monkeypatch, renamed)

    assert server.run_store.agent_names() == {DENICE: "Denice", DESIGNER: "Designer"}
    assert {s.session_id: s.voice_name for s in server._snapshot_or_empty().sessions} == \
        {DENICE: "Denice", DESIGNER: "Designer"}


def test_records_written_under_another_label_are_read_back_under_the_name(
        denice_and_designer):
    """Rows 1143 and 1144 of the live record say "the older denice". Read
    back, they name the agent by its one name."""
    server = denice_and_designer
    store = server.run_store
    mid = store.create_message(DENICE, "anthropic", "the older denice", "denice",
                               server.STEER_SIGNATURE + "Rename the remote.")
    store.update_message(mid, status="answered", reply="Renamed.", answered_at=6.0)
    server.conversation_log.append(server.conversation_log.ConversationItem.new(
        "answer", "Renamed.", agent_session_id=DENICE,
        agent_name="the denice that's working", message_id=mid))

    assert "to Denice," in server.tool_list_messages({})
    assert server.tool_read_message_reply({"message": mid}).startswith("Denice")
    body = server._conversation_json(server.conversation_log.page())
    assert [i["agent_name"] for i in body["items"]] == ["Denice"]
    assert "Answer from Denice" in server.tool_conversation_history({})


def test_a_made_up_label_stored_on_an_agent_is_replaced_once_by_its_plain_name(
        server, monkeypatch):
    """The live ended agent "the jarvis that hasn't started": seen running
    again, it is "jarvis"."""
    server.run_store.sync_agents({"bf": ("the jarvis that hasn't started", "fresh")})
    server.run_store.sync_agents({})
    _live(server, monkeypatch, [_session("bf", "/Users/t/code/jarvis", state="fresh")])
    assert server.run_store.agent_names() == {"bf": "jarvis"}


# --- no code can make the labels again -------------------------------------------

ROOT = Path(__file__).resolve().parent.parent

# How the labels were made: f-strings joining a base name to a made-up
# distinguisher. Any of these in the source means a screen or a voice path
# can say one again.
LABEL_MAKERS = [
    r'f"the (newer|older|newest) \{',
    r'f"the \{\w+\} \{',
    r'\{base\}',
    r'"that\'s working"', r'"that hasn\'t started"', r'"that needs you"',
    r'_name_by_(topic|state|age)', r'_STATE_PHRASES',
]


def test_no_source_makes_an_agent_label():
    files = list((ROOT / "jarvis").rglob("*.py")) + [
        f for f in (ROOT / "frontend" / "src").rglob("*")
        if f.suffix in (".ts", ".tsx") and "node_modules" not in f.parts]
    found = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for pattern in LABEL_MAKERS:
            for m in re.finditer(pattern, text):
                line = text.count("\n", 0, m.start()) + 1
                # run_store's pattern that recognises old labels in stored
                # rows names them in order to replace them.
                if f.name == "run_store.py" and "_MADE_UP_NAME" in text[
                        max(0, m.start() - 600):m.start()]:
                    continue
                found.append(f"{f.relative_to(ROOT)}:{line}: {m.group(0)}")
    assert not found, found


def test_the_page_shows_the_server_s_name_and_derives_none():
    """The page's one reader of an agent's name returns the stored field."""
    source = (ROOT / "frontend" / "src" / "agentName.ts").read_text()
    assert "return agent.name;" in source
