"""A message to a person through the server's own capture path: the review's
capture box, its Send button (over HTTP, through the web boundary), the
voice read-back and send_message, the staged-send drain, the cancel window
and Delete, with the real guard (`message_approvals.MessageApprovals`) over
the contract's in-memory store and team server. The same cases the agent
path is pinned on (`test_agent_transmission_pins.py`), for a person."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from jarvis import message_approvals
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from tests.fixtures import messaging_fakes as fakes

SERVER = "f0000000-0000-4000-8000-00000000000b"
TIM, MATT = 863470, 1001
NODE = UUID(int=0x7222)
NS = (SERVER, TIM)


class _Utterance:
    was_cancelled = False


class _Speech:
    def __init__(self, cancelled=False, heard=True):
        self.said, self.read, self.windows = [], [], 0
        self.cancelled, self.heard = cancelled, heard

    async def say(self, text, *a, **k):
        self.said.append(text)
        return _Utterance()

    def read_aloud(self, text, capture):
        self.read.append((capture, text))
        return _Utterance()

    async def wait_for(self, utt, timeout=60.0):
        return self.heard

    async def open_cancel_window(self, *a, **k):
        self.windows += 1
        return self.cancelled


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from jarvis import server as server_module
    importlib.reload(server_module)
    server_module.run_store.init_db()
    monkeypatch.setattr(server_module, "_notes_for_brain", [])

    async def quiet(msg):
        pass

    monkeypatch.setattr(server_module, "_voice_emit", quiet)
    return server_module


@pytest.fixture
def tim(server, monkeypatch):
    """Tim's JARVIS with messaging wired as startup will wire it."""

    class Tim:
        pass

    t = Tim()
    t.serve = fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=fakes.FixedClock())
    t.store, t.snapshots, t.ids = fakes.MemoryStore(), fakes.StaticSnapshots(), fakes.SeqIds(0x300)
    t.approvals = message_approvals.MessageApprovals(t.store, t.snapshots, ids=t.ids,
                                                     clock=t.serve.clock)
    t.who = message_approvals.Identity(SERVER, TIM, NODE)
    t.released, t.speech = [], _Speech()
    roster = [wire.TeamMember(github_id=TIM, login="tstockstill", name="Tim"),
              wire.TeamMember(github_id=MATT, login="mattk", name="Matt")]
    t.messaging = message_approvals.HumanMessaging(
        approvals=t.approvals, identity=lambda: t.who, availability=lambda: "available",
        recipients=lambda: roster, released=t.released.append)
    t.reviews = message_approvals.Reviews(t.approvals, server.review_captures, lambda: roster)
    monkeypatch.setattr(server, "messaging", SimpleNamespace(human=t.messaging))
    monkeypatch.setattr(server, "speech", t.speech)
    return t


def _reviewed(t, to=MATT, text="Friday?"):
    mid = t.ids.new_id()
    t.store.create_draft(*NS, ports.Draft(
        message_id=mid, thread_id=mid, recipient_github_id=to, draft_revision=1,
        text=wire.normalize_text(text),
        send=ports.LocalSend(message_id=mid, content_revision=1, state="draft")))
    draft = t.reviews.review(*NS, mid, 1)
    return draft, draft.capture_id


def _press_send(server, number):
    from fastapi.testclient import TestClient
    from jarvis import data_paths
    return TestClient(server.app).post(
        f"/api/capture/{number}/send",
        headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}"})


def _state(t, draft):
    return t.store.draft(*NS, draft.message_id).send.state


@pytest.mark.asyncio
async def test_a_review_opens_one_capture_bound_to_its_revision(server, tim):
    draft, n = _reviewed(tim)
    capture = server._captures[n]
    assert capture["text"] == "To Matt.\nFriday?" and capture["to"] == "Matt"
    assert capture["human"]["message_id"] == str(draft.message_id)
    assert capture["read"] is None, "opening the review approves nothing"
    assert tim.reviews.review(*NS, draft.message_id, 1).capture_id == n, "the same capture"
    assert _state(tim, draft) == "awaiting_approval"


@pytest.mark.asyncio
async def test_send_on_screen_during_a_turn_waits_then_approves_through_the_guard(
        server, tim, monkeypatch):
    draft, n = _reviewed(tim)
    monkeypatch.setattr(server, "_turn_utt", object())
    r = _press_send(server, n)
    assert r.status_code == 200 and r.json() == {"capture": n,
                                                 "message": str(draft.message_id)}
    assert _state(tim, draft) == "awaiting_approval", "nothing before the turn ends"
    assert f"as message {draft.message_id}" in server._with_notes_for_brain("")
    await server._perform_staged_steers()                 # what the turn's end does
    approved = tim.store.approved(*NS, draft.message_id, 1)
    assert approved.via == "gui" and _state(tim, draft) == "approved"
    assert tim.released == [approved] and n not in server._captures
    assert tim.speech.said == ["Approved, sir. It goes to Matt as soon as the team server "
                               "takes it."]


@pytest.mark.asyncio
async def test_an_agent_s_access_key_cannot_approve_a_person_s_message(server, tim):
    """A `captures` key opens the capture routes for an agent, but Accept and
    Send on a person's message are his yes alone: 403, and nothing staged,
    accepted or approved."""
    from fastapi.testclient import TestClient
    from jarvis import access_keys
    key = access_keys.create("some-agent", ["captures"])
    draft, n = _reviewed(tim)
    page = TestClient(server.app)
    for action in ("send", "accept"):
        r = page.post(f"/api/capture/{n}/{action}", headers={"Authorization": f"Bearer {key}"})
        assert (r.status_code, r.json()["detail"]) == (
            403, "Only he can approve a message to a person."), action
    await server._perform_staged_steers()
    assert server._captures[n]["read"] is None and not server._captures[n].get("staged")
    assert tim.store.approved(*NS, draft.message_id, 1) is None
    assert _state(tim, draft) == "awaiting_approval"


@pytest.mark.asyncio
async def test_voice_read_back_then_send_message_approves_the_heard_revision(server, tim):
    draft, n = _reviewed(tim)
    assert server.tool_send_message({"capture": n}).startswith(f"Capture {n} has not been read")
    assert (await server.tool_read_capture({"capture": n})).startswith("Read back in full")
    assert tim.speech.read == [(n, "To Matt.\nFriday?")]
    staged = server.tool_send_message({"capture": n})
    assert f"Capture {n} is staged for Matt" in staged and tim.speech.said == []
    await server._perform_staged_steers()
    assert tim.store.approved(*NS, draft.message_id, 1).via == "voice"


@pytest.mark.asyncio
async def test_a_review_capture_cannot_be_reworded_or_merged(server, tim):
    draft, n = _reviewed(tim)
    server.tool_capture({"text": "Other words."})
    other = max(server._captures)
    assert "draft_message" in server.tool_capture({"text": "Changed.", "capture": n})
    assert server._captures[n]["text"] == "To Matt.\nFriday?"
    assert "never merged" in server.tool_merge_captures({"captures": [n, other],
                                                         "text": "x"})
    assert "send_message" in server.tool_list_captures({})


@pytest.mark.asyncio
async def test_steer_session_refuses_a_person_s_message(server, tim, monkeypatch):
    from jarvis import session_watch as sw
    session = sw.SessionState(session_id="s-1", cwd="/p/g", project="g", state="idle",
                              voice_name="guard", steerable=True, socket_path="/x.sock",
                              pids=[1], primary_pid=1, between_turns=True)
    monkeypatch.setattr(server, "_resolve_or_explain", lambda n: (session, None, None))
    draft, n = _reviewed(tim)
    server._captures[n]["read"] = server._captures[n]["text"]
    out = await server.tool_steer_session({"name": "guard", "capture": n})
    assert "send it with send_message" in out and server._staged_steers == []
    assert server.run_store.list_messages() == [], "no agent message is formed"


@pytest.mark.asyncio
async def test_a_draft_edited_after_review_is_refused_at_send(server, tim):
    draft, n = _reviewed(tim)
    current = tim.store.draft(*NS, draft.message_id)
    tim.store.save_draft(*NS, current.model_copy(update={
        "text": "Friday at noon?", "send": ports.revise(current)}), current.draft_revision)
    r = _press_send(server, n)
    assert r.status_code == 409 and "changed since you reviewed it" in r.json()["detail"]
    assert server._staged_steers == [] and tim.store.unresolved_sends(*NS) == []


@pytest.mark.asyncio
async def test_stop_in_the_cancel_window_leaves_it_awaiting_approval(server, tim, monkeypatch):
    draft, n = _reviewed(tim)
    monkeypatch.setattr(server, "speech", _Speech(cancelled=True))
    monkeypatch.setattr(server, "_turn_utt", object())
    assert _press_send(server, n).status_code == 200
    await server._perform_staged_steers()
    assert server.speech.said == ["Cancelled, sir."]
    assert _state(tim, draft) == "awaiting_approval" and tim.store.unresolved_sends(*NS) == []
    assert n in server._captures and not server._captures[n].get("staged")


@pytest.mark.asyncio
async def test_delete_while_staged_cancels_it_unapproved(server, tim, monkeypatch):
    draft, n = _reviewed(tim)
    monkeypatch.setattr(server, "_turn_utt", object())
    assert _press_send(server, n).status_code == 200
    await server.api_capture_delete(n)
    await server._perform_staged_steers()
    assert _state(tim, draft) == "awaiting_approval" and tim.store.unresolved_sends(*NS) == []
    assert tim.released == [] and server.conversation_log.capture_events() == []


@pytest.mark.asyncio
async def test_unwired_messaging_refuses_a_person_s_capture(server, tim, monkeypatch):
    draft, n = _reviewed(tim)
    monkeypatch.setattr(server, "messaging", None)
    r = _press_send(server, n)
    assert r.status_code == 409 and "isn't available" in r.json()["detail"]
    assert server._staged_steers == []
    assert server.TOOL_HANDLERS["draft_message"]({"to": "Matt", "text": "hi"}) == \
        "Messaging isn't available on this JARVIS."


@pytest.mark.asyncio
async def test_a_message_to_oneself_is_approved_the_same_way(server, tim, monkeypatch):
    draft, n = _reviewed(tim, to=TIM, text="Remember the deploy.")
    assert server._captures[n]["text"] == "To Me.\nRemember the deploy."
    monkeypatch.setattr(server, "_turn_utt", object())
    assert _press_send(server, n).status_code == 200
    await server._perform_staged_steers()
    approved = tim.store.approved(*NS, draft.message_id, 1)
    assert approved.request.recipient_github_id == TIM
    guarded = message_approvals.GuardedTransport(tim.serve.pair(TIM, NODE), tim.approvals, *NS)
    send = tim.store.unresolved_sends(*NS)[0]
    tim.store.set_send(*NS, send.model_copy(update={"state": "submitting"}))
    result = await guarded.send(approved.request)
    assert result.message.recipient_github_id == TIM and not result.duplicate


def test_the_brain_s_message_tools_are_registered_and_gated(server):
    from jarvis import jarvis_mcp
    names = {"send_message", "draft_message", "request_message_review"}
    assert names <= set(server.TOOL_HANDLERS) and names <= server.ACTING_TOOLS
    assert names <= {t["name"] for t in jarvis_mcp.TOOL_SPECS}
    assert names <= set(server.TAINT_EXEMPT_TOOLS)
    assert "send_message" in server.READ_BACK_TOOLS
    assert not [t for t in server.TOOL_HANDLERS if "approv" in t and "document" not in t]


@pytest.mark.asyncio
async def test_a_human_send_that_raises_never_stops_the_drain(server, tim, monkeypatch):
    first, n1 = _reviewed(tim)
    second, n2 = _reviewed(tim, text="Saturday?")

    class Broken(_Speech):
        async def say(self, text, *a, **k):
            raise RuntimeError("no mouth")

    monkeypatch.setattr(server, "speech", Broken())
    monkeypatch.setattr(server, "_turn_utt", object())
    for n in (n1, n2):
        assert _press_send(server, n).status_code == 200
    await server._perform_staged_steers()
    assert tim.store.approved(*NS, first.message_id, 1) is not None
    assert tim.store.approved(*NS, second.message_id, 1) is not None
    assert server._staged_steers == []
