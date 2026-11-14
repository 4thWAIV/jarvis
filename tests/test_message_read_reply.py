"""Read and Reply on every message to him, whatever brought it.

One channel (docs/planning/one-channel/messages-by-work-item.md): every
message to him is one record (conversation_log), and the page's Read and
Reply and the brain's read_aloud and reply act on it by its id, through the
same functions: an agent's own message (the signed route, `_record_message`),
an agent's answer, a decision, a package, a person's message. Session
message files and needs boards are gone (round 3).

His Read is a turn of his ("Read me message 42."), never words spoken past
the brain (docs/planning/brain-reads/design.md): the brain reads the message
with read_aloud and reads it to him in its reply, so it knows what he
heard; the message is read, and its update seen, only once that reply has
been heard in full. Nothing here reads a real data/.
"""

import asyncio
from types import SimpleNamespace

import pytest

from tests.attention_fixtures import attention, recorded  # noqa: F401  (a fixture)


class _Speech:
    """Records what is said; `heard` is whether each utterance is heard in full."""

    def __init__(self):
        self.said = []
        self.heard = True

    async def say(self, text, priority=None, immediate=None, label=None):
        self.said.append(text)
        return SimpleNamespace(chunks=[text], text=text)

    async def wait_for(self, utt, timeout=60.0):
        return self.heard


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import server as server_module
    importlib.reload(server_module)
    server_module.run_store.init_db()
    monkeypatch.setattr(server_module, "_notes_for_brain", [])
    sp = _Speech()
    monkeypatch.setattr(server_module, "speech", sp)
    server_module.said = sp.said
    sessions = [SimpleNamespace(project="guard", voice_name="Agent Guard", session_id=GUARD,
                                cwd=str(tmp_path / "guard"), provider="anthropic"),
                SimpleNamespace(project="guard", voice_name="Designer", session_id=DESIGNER,
                                cwd=str(tmp_path / "guard"), provider="anthropic")]
    monkeypatch.setattr(server_module, "_snapshot_or_empty",
                        lambda: SimpleNamespace(sessions=sessions))
    monkeypatch.setattr(server_module.agent_directory, "names_by_session",
                        lambda: {GUARD: "Agent Guard", DESIGNER: "Designer"})
    monkeypatch.setattr(server_module.session_watch, "config_roots",
                        lambda: [tmp_path / "claude"])
    return server_module


GUARD, DESIGNER = "sess-guard-0001", "sess-designer-01"


def _taken(server, monkeypatch, attention):
    """A message Agent Guard sent with its own key (the signed route's one
    writer), its update recorded; the message."""
    monkeypatch.setattr(server, "attention_record", attention)
    monkeypatch.setattr(server, "_send_to_session_clients", lambda m: None)
    server.run_store.init_db()
    server.run_store.sync_agents({GUARD: ("Agent Guard", "idle")})
    return server._record_message(f"agent:{GUARD}", GUARD, "Agent Guard", "",
                                  "Status\n\nAll good.")


ORIGIN = {"Origin": "http://localhost:5173"}


def _message_of(client, kind: str, source) -> int:
    """The message id the Updates entry for (kind, source) carries: what its
    Read and Reply post to."""
    [entry] = [i for i in client.get("/api/attention").json()["items"]
               if i["kind"] == kind and i["source_id"] == str(source)]
    assert entry["message"] is not None, entry
    return entry["message"]


def _asked(server, monkeypatch) -> list:
    """The turns of his that reach the brain, by their words: what his Read
    asks for. The brain's turn itself is not run."""
    asked = []

    def handle(text, heard=None):
        asked.append(text)

        async def nothing():
            return None
        return nothing()
    monkeypatch.setattr(server, "_handle_utterance", handle)
    return asked


def _read_on_his_turn(server, monkeypatch, item_id, heard=True) -> str:
    """The brain's read_aloud on a turn he drove, then that turn's reply
    heard in full (`heard`) or cut short: what the tool gave the brain."""
    reads = []
    monkeypatch.setattr(server, "_turn_reads", reads)
    monkeypatch.setattr(server, "brain_instance", SimpleNamespace(current_origin="user"))
    out = asyncio.run(server.tool_read_aloud({"message": item_id}))
    server._seen_once_heard(reads, SimpleNamespace(chunks=["Agent Guard writes: ..."]), heard)
    return out


def test_read_asks_the_brain_for_that_one_message_and_speaks_nothing(server, monkeypatch,
                                                                     attention):
    from fastapi.testclient import TestClient
    m = _taken(server, monkeypatch, attention)
    asked = _asked(server, monkeypatch)
    client = TestClient(server.app)
    r = client.post(f"/api/messages/{m.id}/read", headers=ORIGIN)
    assert r.status_code == 200 and r.json() == {"id": m.id, "asking": True}, r.text
    assert asked == [f"Read me message {m.id}."], "one turn of his, naming the exact message"
    assert server.said == [], "nothing is spoken past the brain"
    [turn] = [i for i in server.conversation_log.page() if i.kind == "typed"]
    assert (turn.text, turn.detail["via"], turn.detail["message"]) == (
        f"Read me message {m.id}.", "read", m.id), "his request, on record with the id"
    assert server._message_on == m.id
    assert server.conversation_log.get(m.id).state == "new", "asking is not hearing it"
    (entry,) = attention.items(("pending", "seen", "resolved"))
    assert entry.state == "pending"
    assert client.post("/api/messages/999/read", headers=ORIGIN).status_code == 404
    assert client.post(f"/api/messages/{m.id}/read").status_code == 403, "his click only"
    assert asked == [f"Read me message {m.id}."]


def test_read_with_no_voice_is_refused_and_asks_nothing(server, monkeypatch, attention):
    from fastapi.testclient import TestClient
    m = _taken(server, monkeypatch, attention)
    asked = _asked(server, monkeypatch)
    monkeypatch.setattr(server, "speech", None)
    r = TestClient(server.app).post(f"/api/messages/{m.id}/read", headers=ORIGIN)
    assert r.status_code == 409 and asked == []


def test_read_is_asked_in_the_language_in_force(server, monkeypatch, attention, spanish):
    from fastapi.testclient import TestClient
    m = _taken(server, monkeypatch, attention)
    asked = _asked(server, monkeypatch)
    TestClient(server.app).post(f"/api/messages/{m.id}/read", headers=ORIGIN)
    assert asked == [f"Léeme el mensaje {m.id}."]


def test_the_brain_is_given_that_message_to_read_and_it_is_read_once_heard(server, monkeypatch,
                                                                         attention):
    m = _taken(server, monkeypatch, attention)
    out = _read_on_his_turn(server, monkeypatch, m.id)
    head, _, block = out.partition("\n")
    assert head.startswith(f"Message {m.id}, at {server.messages_api.local_time(m.at)} (his local "
                           f"time, today ") and "sum it up" in head
    assert "Agent Guard" not in head and "All good" not in head, "the sender's words are walled"
    assert block.startswith("<session-output") and 'untrusted="true"' in block
    assert "Agent Guard" in block and "Status\\n\\nAll good." in block
    assert server.said == [], "the brain reads it, in its own reply"
    assert server.conversation_log.get(m.id).state == "read", "read once heard in full"
    (entry,) = attention.items(("pending", "seen", "resolved"))
    assert entry.state == "seen"


def test_a_reply_cut_off_leaves_the_message_new_and_its_entry_pending(server, monkeypatch,
                                                                      attention):
    m = _taken(server, monkeypatch, attention)
    _read_on_his_turn(server, monkeypatch, m.id, heard=False)
    (entry,) = attention.items(("pending", "seen", "resolved"))
    assert entry.state == "pending"
    assert server.conversation_log.get(m.id).state == "new"


def test_the_route_and_the_tool_name_a_message_through_the_one_resolver(server):
    import inspect
    assert "_message_to_him" in inspect.getsource(server.api_read_message)
    assert "messages_api.resolve(" in inspect.getsource(server._message_to_him)
    assert "messages_api.resolve(" in inspect.getsource(server._message_meant)
    for tool in (server.tool_read_aloud, server.tool_reply, server.tool_mark_message):
        assert "_message_meant" in inspect.getsource(tool), tool.__name__
    assert "_message_meant" in inspect.getsource(server._carried_by)
    assert "speech." not in inspect.getsource(server.api_read_message)
    assert "speech." not in inspect.getsource(server.tool_read_aloud)


def test_reply_is_a_capture_to_its_sender_read_back_and_sent_on_his_yes(server, monkeypatch,
                                                                       attention):
    from fastapi.testclient import TestClient
    m = _taken(server, monkeypatch, attention)
    client = TestClient(server.app)
    item = m.id
    r = client.post(f"/api/messages/{item}/reply", headers=ORIGIN)
    assert r.status_code == 200, r.text
    capture = server._captures[r.json()["capture"]]
    assert (capture["to"], capture["to_session"], capture["text"]) == ("Agent Guard", GUARD, "")
    said = asyncio.run(server.tool_reply({"message": item, "text": "Thanks, ship it."}))
    newest = max(server._captures.values(), key=lambda c: c["id"])
    assert (newest["to_session"], newest["text"]) == (GUARD, "Thanks, ship it.")
    assert "read_capture" in said and "his yes" in said
    from jarvis import transmission
    assert not transmission.accepted(newest), "nothing goes until it is read back and he says yes"


def test_a_message_that_is_not_on_record_cannot_be_replied_to(server):
    """A message to him is replied to only through its record, which only a
    proven sender's message gets into (conversation_log.inbound)."""
    from fastapi.testclient import TestClient
    r = TestClient(server.app).post("/api/messages/999/reply", headers=ORIGIN)
    assert r.status_code == 404
    assert "Nothing was captured" in asyncio.run(server.tool_reply({"message": 999}))


def test_a_read_back_names_its_recipient_as_the_directory_names_it(server, monkeypatch):
    """Never "To that session." for a name with a space in it, and the
    agent's one name now, whatever it was addressed by."""
    capture = {"to": "agent guard", "to_session": GUARD}
    assert server._read_back_lead(capture) == "To Agent Guard."
    monkeypatch.setattr(server.agent_directory, "names_by_session", lambda: {})
    assert server._read_back_lead({"to": "Landorphan Taxes", "to_session": "x"}) == \
        "To Landorphan Taxes."


# --- an agent's answer to his message: the same Read, Reply and Seen ------------------

def _answered(server, monkeypatch, attention, reply="Done: all tests pass."):
    """His message to Agent Guard, answered, on record as the watcher read
    it, with its update pending; the id its Updates entry's Read takes."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(server, "attention_record", attention)
    store = server.run_store
    mid = store.create_message(GUARD, "anthropic", "Agent Guard", "guard", "Run the tests.",
                               status=store.MessageStatus.SENT)
    store.update_message(mid, status=store.MessageStatus.ANSWERED, reply=reply)
    cl = server.conversation_log
    server._record_inbound(cl.ItemKind.ANSWER, cl.Counterpart.AGENT, GUARD, "Agent Guard", reply,
                           "watcher", source_kind=cl.Source.MESSAGE, source_id=mid,
                           message_id=mid)
    attention.record("agent_message", str(mid), "answered", facts={"message_id": mid},
                     untrusted={"agent": "Agent Guard"})
    return _message_of(TestClient(server.app), "agent_message", mid)


def test_the_brain_reads_an_agent_s_answer_and_it_is_seen_once_heard(server, monkeypatch,
                                                                    attention):
    from fastapi.testclient import TestClient
    item = _answered(server, monkeypatch, attention)
    asked = _asked(server, monkeypatch)
    client = TestClient(server.app)
    assert client.post(f"/api/messages/{item}/read", headers=ORIGIN).status_code == 200
    assert asked == [f"Read me message {item}."]
    out = _read_on_his_turn(server, monkeypatch, item)
    assert "Done: all tests pass." in out.partition("\n")[2], "that answer only"
    (entry,) = attention.items(("pending", "seen", "resolved"))
    assert entry.state == "seen"
    assert client.post(f"/api/messages/{item}/read").status_code == 403, "his click only"


def test_an_answer_cut_off_stays_pending(server, monkeypatch, attention):
    item = _answered(server, monkeypatch, attention)
    _read_on_his_turn(server, monkeypatch, item, heard=False)
    (entry,) = attention.items(("pending", "seen", "resolved"))
    assert entry.state == "pending"


def test_reply_to_an_answer_is_a_capture_to_the_session_that_answered(server, monkeypatch,
                                                                      attention):
    from fastapi.testclient import TestClient
    from jarvis import transmission
    item = _answered(server, monkeypatch, attention)
    r = TestClient(server.app).post(f"/api/messages/{item}/reply", headers=ORIGIN)
    assert r.status_code == 200, r.text
    capture = server._captures[r.json()["capture"]]
    assert (capture["to_session"], server._read_back_lead(capture)) == (GUARD, "To Agent Guard.")
    said = asyncio.run(server.tool_reply({"message": item, "text": "Ship it."}))
    newest = max(server._captures.values(), key=lambda c: c["id"])
    assert (newest["to_session"], newest["text"]) == (GUARD, "Ship it.")
    assert "read_capture" in said and not transmission.accepted(newest)


def test_the_brain_finds_the_message_in_its_updates(server, monkeypatch, attention):
    """attention_updates names each message update's message, the id the
    brain's read_aloud and reply take, as the page's entry does."""
    item = _answered(server, monkeypatch, attention)
    out = asyncio.run(server.tool_attention_updates({}))
    assert f'"message":{item}' in out
    said = asyncio.run(server.tool_read_aloud({"message": item}))
    assert said.startswith(f"Message {item},") and "Done: all tests pass." in said
    assert server.said == []


# --- one channel, phase 3: the same Read and Reply for every channel's message -------

def _inbound(server, counterpart, ref, name, text, evidence, source, source_id, **links):
    cl = server.conversation_log
    server._record_inbound(cl.ItemKind.MESSAGE_IN, counterpart, ref, name, text, evidence,
                           source_kind=source, source_id=source_id, **links)
    kind, event, _ = cl.UPDATES[source]
    return cl.message_for(kind, str(source_id).split(":")[0], event)


def test_a_decision_is_read_aloud_and_replied_to_through_its_asking_key(server, monkeypatch,
                                                                       attention):
    from fastapi.testclient import TestClient
    from jarvis import reporters
    cl = server.conversation_log
    monkeypatch.setattr(server, "attention_record", attention)
    monkeypatch.setattr(reporters, "of_label", lambda label: reporters.Reporter(
        label, "Agent Guard", (GUARD,) if label == "guard" else ()))
    item = _inbound(server, cl.Counterpart.WORK, "work-1", "Speech",
                    "Decision needed on Speech: Which model?", "access_key",
                    cl.Source.WORK_DECISION, "work-1:model", detail={"reporter": "key:guard"})
    attention.record("work", "work-1", "decision_needed", untrusted={"work": "Speech"})
    client = TestClient(server.app)
    assert _message_of(client, "work", "work-1") == item.id
    out = _read_on_his_turn(server, monkeypatch, item.id)
    assert "Decision needed on Speech: Which model?" in out.partition("\n")[2]
    (entry,) = attention.items(("pending", "seen", "resolved"))
    assert entry.state == "seen"
    r = client.post(f"/api/messages/{item.id}/reply", headers=ORIGIN)
    capture = server._captures[r.json()["capture"]]
    assert capture["to_session"] == GUARD, "its proven asker, the key's agent"
    token = _inbound(server, cl.Counterpart.WORK, "work-2", "Other", "Decision needed: x",
                     "access_key", cl.Source.WORK_DECISION, "work-2:k",
                     detail={"reporter": "key:nobody"})
    r = client.post(f"/api/messages/{token.id}/reply", headers=ORIGIN)
    assert r.status_code == 409 and "no one to reply to" in r.json()["detail"]


def test_a_package_is_replied_to_through_its_proven_sender(server, monkeypatch):
    from fastapi.testclient import TestClient
    cl = server.conversation_log
    item = _inbound(server, cl.Counterpart.PACKAGE, "inbox-1", "Ports", "Package: Ports",
                    "subagent_transcript", cl.Source.PACKAGE, "inbox-1", subagent="Feynman",
                    detail={"agent": "Feynman (Agent Guard)", "session": GUARD})
    r = TestClient(server.app).post(f"/api/messages/{item.id}/reply", headers=ORIGIN)
    capture = server._captures[r.json()["capture"]]
    assert (capture["to_session"], server._read_back_lead(capture)) == (
        GUARD, "To Agent Guard, for Feynman.")


def test_a_person_s_message_is_read_aloud_and_replied_to_as_a_draft(server, monkeypatch,
                                                                   attention):
    from fastapi.testclient import TestClient
    cl = server.conversation_log
    monkeypatch.setattr(server, "attention_record", attention)
    mid = "00000000-0000-0000-0000-000000000042"
    item = _inbound(server, cl.Counterpart.PERSON, "42", "Ana", "Lunch\n\nAt noon?",
                    "team_server", cl.Source.PERSON_MESSAGE, mid)
    attention.record("arrival", mid, "arrived", untrusted={"sender": "Ana"})
    client = TestClient(server.app)
    out = _read_on_his_turn(server, monkeypatch, _message_of(client, "arrival", mid))
    assert "Ana" in out.partition("\n")[2] and "Lunch\\n\\nAt noon?" in out
    assert attention.get("arrival", mid).state == "seen"
    r = client.post(f"/api/messages/{item.id}/reply", headers=ORIGIN)
    assert r.status_code == 200 and r.json() == {"capture": None, "to": "Ana"}
    assert f"reply_to {mid}" in server._with_notes_for_brain("")
    said = asyncio.run(server.tool_reply({"message": item.id}))
    assert "draft_message" in said and "request_message_review" in said


def test_an_update_that_is_no_message_names_none(server, monkeypatch, attention):
    from fastapi.testclient import TestClient
    cl = server.conversation_log
    monkeypatch.setattr(server, "attention_record", attention)
    _inbound(server, cl.Counterpart.WORKFLOW, "wf-1", "Build", "Decision needed on Build: x",
             "access_key", cl.Source.WORKFLOW_DECISION, "wf-1:port")
    attention.record("workflow", "wf-1", "phase_started", untrusted={"workflow": "Build"})
    [entry] = TestClient(server.app).get("/api/attention").json()["items"]
    assert entry["message"] is None, "a new phase is no message"
