"""Three things Tim found in one channel (Work item work-b93db25f6aac, piece
one-channel-fixes):

  1. A message read to him to its end is read, by the page's Read and by
     voice alike: read_aloud waits as long as the longest message takes,
     and an agent's answer the brain reads to him (read_message_reply) is
     read once its turn's reply has been heard, not only its update seen.
     His mark (read or done) sees the message's update too, so the update
     and the message say the same.
  2. and 3. The Inbox lists only the new messages that belong to no item;
     read and answered ones are its Read section (`?read=1`, the brain's
     messages_to_him `read`); done ones are in neither.

Nothing here reads a real data/.
"""

import asyncio
from types import SimpleNamespace

from tests.attention_fixtures import attention  # noqa: F401  (a fixture)
from tests.test_message_read_reply import GUARD, ORIGIN, server  # noqa: F401  (a fixture)


def _answer(server, monkeypatch, attention, reply="Done: all tests pass."):
    """His message to Agent Guard, answered, on record as the watcher read
    it, its update pending: (the run_store message id, its message to him)."""
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
    return mid, cl.message_for("agent_message", str(mid), "answered")


def _general(server, monkeypatch, says):
    """A message Agent Guard sent him with its key, about no item; new."""
    monkeypatch.setattr(server, "_send_to_session_clients", lambda m: None)
    return server._record_message(f"agent:{GUARD}", GUARD, "Agent Guard", "", says)


# -- 1. read once heard ------------------------------------------------------------------

def _on_his_turn(server, monkeypatch):
    reads = []
    monkeypatch.setattr(server, "_turn_reads", reads)
    monkeypatch.setattr(server, "brain_instance", SimpleNamespace(current_origin="user"))
    return reads


def test_an_answer_the_brain_reads_to_him_is_read_once_its_reply_is_heard(server, monkeypatch,
                                                                         attention):
    mid, message = _answer(server, monkeypatch, attention)
    reads = _on_his_turn(server, monkeypatch)
    server.tool_read_message_reply({"message": mid})
    assert (server._MESSAGE_READ, str(message.id), None) in reads
    cl = server.conversation_log
    assert cl.get(message.id).state == "new", "the tool call alone is not hearing it"
    server._seen_once_heard(reads, SimpleNamespace(chunks=["Guard says done."]), True)
    assert cl.get(message.id).state == "read"
    assert attention.get("agent_message", str(mid)).state == "seen", "the update agrees"


def test_an_answer_whose_reply_was_cut_off_stays_new(server, monkeypatch, attention):
    mid, message = _answer(server, monkeypatch, attention)
    reads = _on_his_turn(server, monkeypatch)
    server.tool_read_message_reply({"message": mid})
    server._seen_once_heard(reads, SimpleNamespace(chunks=["Guard says"]), False)
    assert server.conversation_log.get(message.id).state == "new"
    assert attention.get("agent_message", str(mid)).state == "pending"


def test_an_answer_read_with_no_pending_update_is_still_read(server, monkeypatch, attention):
    """His update already seen (Mark all seen), the message is still new:
    hearing it read makes it read."""
    mid, message = _answer(server, monkeypatch, attention)
    attention.mark_seen(None)
    reads = _on_his_turn(server, monkeypatch)
    server.tool_read_message_reply({"message": mid})
    assert reads == [(server._MESSAGE_READ, str(message.id), None)]
    server._seen_once_heard(reads, SimpleNamespace(chunks=["x"]), True)
    assert server.conversation_log.get(message.id).state == "read"


def test_read_aloud_waits_as_long_as_the_longest_message_takes(server, monkeypatch, attention):
    """An 8,000-character message takes about nine minutes to say: five
    minutes gave up on it while he was still hearing it, so it stayed new."""
    from fastapi.testclient import TestClient
    waited = []

    async def wait_for(utt, timeout=60.0):
        waited.append(timeout)
        return True
    monkeypatch.setattr(server.speech, "wait_for", wait_for)
    _, message = _answer(server, monkeypatch, attention, reply="word " * 1600)
    r = TestClient(server.app).post(f"/api/messages/{message.id}/read", headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert waited == [server.READ_ALOUD_TIMEOUT] and server.READ_ALOUD_TIMEOUT >= 3600
    assert server.conversation_log.get(message.id).state == "read"


def test_the_brain_s_read_aloud_marks_it_read_once_heard(server, monkeypatch, attention):
    _, message = _answer(server, monkeypatch, attention)

    async def read():
        said = await server.tool_read_aloud({"message": message.id})
        for _ in range(50):
            if server.conversation_log.get(message.id).state == "read":
                break
            await asyncio.sleep(0.02)
        return said
    assert asyncio.run(read()).startswith("Reading message")
    assert server.conversation_log.get(message.id).state == "read"


def test_his_mark_sees_the_message_s_update_too(server, monkeypatch, attention):
    from fastapi.testclient import TestClient
    mid, message = _answer(server, monkeypatch, attention)
    r = TestClient(server.app).post(f"/api/messages/{message.id}/mark", json={"state": "read"},
                                    headers=ORIGIN)
    assert r.status_code == 200 and r.json()["message"]["state"] == "read"
    assert attention.get("agent_message", str(mid)).state == "seen"


# -- 2 and 3. the Inbox: new in view, read in Read, done gone ------------------------------

def test_the_inbox_lists_new_ones_and_read_holds_read_and_answered_not_done(server, monkeypatch,
                                                                            attention):
    from fastapi.testclient import TestClient
    cl = server.conversation_log
    new = _general(server, monkeypatch, "Still new.")
    read = _general(server, monkeypatch, "Read already.")
    answered = _general(server, monkeypatch, "Answered.")
    done = _general(server, monkeypatch, "Done with.")
    cl.mark(read.id, cl.MessageState.READ)
    cl.mark(answered.id, cl.MessageState.ANSWERED)
    cl.mark(done.id, cl.MessageState.DONE)
    client = TestClient(server.app)
    inbox = client.get("/api/messages/general").json()
    assert [m["id"] for m in inbox["messages"]] == [new.id], "only the new ones in view"
    assert (inbox["new"], inbox["read"]) == (1, 2)
    shelf = client.get("/api/messages/general?read=1").json()
    assert [m["id"] for m in shelf["messages"]] == [answered.id, read.id], "newest first"
    assert done.id not in [m["id"] for m in inbox["messages"] + shelf["messages"]]


def test_the_brain_reads_the_same_lists(server, monkeypatch, attention):
    import json
    cl = server.conversation_log
    new = _general(server, monkeypatch, "Still new.")
    read = _general(server, monkeypatch, "Read already.")
    cl.mark(read.id, cl.MessageState.READ)
    both = server._messages_read({})
    assert [m["id"] for m in both["general"]["messages"]] == [new.id]
    assert both["general"]["read"] == 1
    assert [m["id"] for m in server._messages_read({"read": True})["messages"]] == [read.id]
    said = asyncio.run(server.tool_messages_to_him({"read": True}))
    assert "Read already." in said and "Still new." not in said
    assert json.loads(json.dumps(server._messages_read({"general": True})))["messages"][0]["id"] \
        == new.id


def test_a_work_item_s_line_counts_only_its_new_messages(server, monkeypatch, attention):
    from jarvis import messages_api
    cl = server.conversation_log
    m = cl.inbound(cl.ItemKind.MESSAGE_IN, cl.Counterpart.AGENT, GUARD, "Agent Guard", "On it.",
                   "agent_signature", source_kind=cl.Source.SIGNED, source_id="w1",
                   sender=f"agent:{GUARD}", sender_name="Agent Guard", about="work-1")
    [line] = messages_api.work_list()["items"]
    assert line["new"] == 1
    cl.mark(m.id, cl.MessageState.READ)
    [line] = messages_api.work_list()["items"]
    assert line["new"] == 0 and line["first_new"] is None, "the Inbox draws no line for it"
