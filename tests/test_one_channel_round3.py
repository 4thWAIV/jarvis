"""One channel, round 3: agents move to the one route, the old paths go,
and the voice reads and acts on messages in the Inbox's order.

docs/planning/one-channel/messages-by-work-item.md, "How a message gets to
its Work item" and "What goes away": a package is always a message
(make_package posts it to /api/messages); a decision is only ever a
package (work_report.py's decision and withdraw, and a workflow's
decision_needed, refuse saying so); a workflow's blocker is a message on
its Work item; "What's new?", "read it", "open it" and "reply" act on the
message he is on through the same functions as the page's buttons."""

import asyncio
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis import conversation_log as cl
from jarvis.message import ASK_WITH_A_PACKAGE
from tests.agent_key_fixtures import Agent
from tests.test_agent_key_packages import _start_work, send, signed_zip
from tests.test_agent_keys import DIAG, ORIGIN, PLAN, keygen, wired  # noqa: F401
from tests.test_work_packages import marks  # noqa: F401


def _say(client, agent, says, work=""):
    r = agent.post(client, "/api/messages" + (f"?work={work}" if work else ""), {"says": says})
    assert r.status_code == 200, r.text
    return r.json()["message"]


# -- make_package: a package is always a message ------------------------------------------

def test_make_package_delivers_a_message_carrying_the_package(monkeypatch, tmp_path):
    from jarvis import decision_package as dp
    archive = tmp_path / "p.zip"
    archive.write_bytes(b"PK zip bytes")
    sent = {}

    def post(url, token, body, timeout=60, signer=None):
        sent.update(url=url, body=body, signer=signer)
        return {"message": {"id": 7}, "inbox": {"id": "inbox-1"}, "arrived": True,
                "package": {"id": "p-1.0.0"}}
    monkeypatch.setattr(dp, "post", post)
    out = dp.deliver(archive, "https://x.test", "Decision package: Ports", project="jarvis",
                     workflow="wf-1", work="work-1", source="h.json", signer=("k", "pub"))
    assert sent["url"] == ("https://x.test/api/messages?work=work-1&project=jarvis&workflow=wf-1"
                           "&source=h.json")
    assert sent["body"] == {"says": "Decision package: Ports",
                            "package": base64.b64encode(b"PK zip bytes").decode()}
    assert sent["signer"] == ("k", "pub"), "signed by the agent's own key"
    assert (out.message["id"], out.inbox["id"], out.unpacked) == (7, "inbox-1", True)
    assert dp.says_of("  One  channel ", "decision package") == \
        "Decision package: One channel"


def test_a_package_sent_as_a_message_is_on_its_item_carrying_it(wired, marks, tmp_path):
    from jarvis import package_inbox
    server, *_, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    wid = _start_work(client, agent, folder)
    r = send(client, agent, signed_zip(tmp_path, agent), says="The notes, for review.",
             project="plan")
    assert r.status_code == 200, r.text
    body = r.json()
    entry = package_inbox.get(body["inbox"]["id"])
    assert body["message"]["carries"] == entry.id and body["arrived"] is True
    assert body["package"]["id"] == entry.package_id
    assert body["message"]["says"] == "The notes, for review."
    assert body["message"]["about"] is None and not entry.work_id, "no --work: his Inbox"
    piece = agent.post(client, f"/api/work/{wid}/events", {
        "kind": "piece", "summary": "On it.",
        "detail": {"key": "b", "title": "B", "who": {"kind": "agent", "ref": PLAN},
                   "state": "ready"}})
    assert piece.status_code == 200, piece.text
    refused = send(client, agent, signed_zip(tmp_path, agent, files={"SKILL.md": "Other."}))
    assert refused.status_code == 409 and wid in refused.json()["error"], \
        "an agent with a piece on an open item names it"


# -- a decision is only ever a package -------------------------------------------------------

def test_work_report_decision_and_withdraw_refuse_naming_the_new_way(monkeypatch, capsys):
    from jarvis import work_report
    monkeypatch.setattr(work_report, "_client", lambda *a: pytest.fail("nothing is sent"))
    for argv in (["decision", "work-1", "db", "Which database?", "--option", "a"],
                 ["withdraw", "work-1", "db", "Asked too soon."]):
        assert work_report.main(["work_report.py", *argv]) == 2
        err = capsys.readouterr().err
        assert err.strip() == ASK_WITH_A_PACKAGE
    assert "make_package" in ASK_WITH_A_PACKAGE and "--work <item id>" in ASK_WITH_A_PACKAGE


def test_the_work_route_refuses_a_decision_from_any_reporter(wired):
    from jarvis import work_store
    server, *_, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    wid = _start_work(client, agent, folder)
    for kind in ("decision_needed", "decision_withdrawn"):
        r = agent.post(client, f"/api/work/{wid}/events", {
            "kind": kind, "summary": "Which database?", "detail": {"key": "db"}})
        assert r.status_code == 409 and r.json()["error"] == ASK_WITH_A_PACKAGE, r.text
    assert work_store.get(wid).decisions == [] and cl.work_threads() == []


# -- workflows ----------------------------------------------------------------------------------

def _workflow(client, agent, work=""):
    r = agent.post(client, "/api/workflows", {"name": "Speech build", "project": "jarvis",
                                              "phases": ["plan"], "work": work})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_workflow_is_for_a_work_item_and_asks_no_decision(wired):
    from jarvis import workflow_store
    server, *_, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    wid = _start_work(client, agent, folder)
    wf = _workflow(client, agent, wid)
    assert workflow_store.snapshot(wf).work == wid
    r = agent.post(client, "/api/workflows", {"name": "x", "project": "y", "work": "work-nope"})
    assert r.status_code == 409 and "work-nope" in r.json()["error"]
    r = agent.post(client, f"/api/workflows/{wf}/events", {
        "kind": "decision_needed", "summary": "Which engine?", "detail": {"key": "engine"}})
    assert r.status_code == 409 and r.json()["error"] == ASK_WITH_A_PACKAGE
    assert workflow_store.snapshot(wf).open_decisions == []


def test_a_workflow_s_blocker_is_a_message_on_its_item_from_its_reporter(wired):
    from jarvis import workflow_store
    server, hints, store, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    wid = _start_work(client, agent, folder)
    wf = _workflow(client, agent, wid)
    r = agent.post(client, f"/api/workflows/{wf}/events", {
        "kind": "blocker_raised", "summary": "The test machine is down.",
        "detail": {"key": "machine"}})
    assert r.status_code == 200, r.text
    assert workflow_store.snapshot(wf).open_blockers == ["machine"], "still its state"
    [m] = cl.work_thread(wid)
    assert (m.text, m.sender, m.counterpart_ref, m.state, m.about) == (
        "Speech build: blocked: The test machine is down.", f"agent:{PLAN}", PLAN, "new", wid)
    assert store.get("workflow", wf) is None, "a message, not a workflow update"
    agent.post(client, f"/api/workflows/{wf}/events", {"kind": "phase_started",
                                                       "summary": "Plan.", "item": "plan"})
    assert store.get("workflow", wf).event == "phase_started", "progress stays an update"
    other = _workflow(client, Agent(home, DIAG).onboarded())
    Agent(home, DIAG).post(client, f"/api/workflows/{other}/events", {
        "kind": "blocker_raised", "summary": "No key.", "detail": {"key": "k"}})
    [general] = cl.general()
    assert general.about is None and general.counterpart_ref == DIAG, "no item: his Inbox"


def test_the_reporter_starts_a_workflow_for_an_item(monkeypatch):
    from jarvis import workflow_report
    posted = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, path, json):
            posted.append((path, json))
            return workflow_report._Response(200, b'{"id": "wf-1"}', "application/json")
    monkeypatch.setattr(workflow_report, "_client", lambda *a: Client())
    assert workflow_report.main(["workflow_report.py", "start", "Build", "--project", "j",
                                 "--work", "work-9"]) == 0
    assert [(path.partition("?")[0], body) for path, body in posted] == [
        ("/api/workflows", {"name": "Build", "project": "j", "phases": [], "work": "work-9"})]


# -- what went away ------------------------------------------------------------------------------

def test_the_old_paths_are_gone():
    from pathlib import Path
    from jarvis import agent_keys, data_paths, jarvis_mcp, server, subsystem_diagnostics as sd
    for module in ("session_needs.py", "session_messages.py"):
        assert not (Path(data_paths.PACKAGE_DIR) / module).exists(), module
    for name in ("needs_dir", "session_messages_dir"):
        assert not hasattr(data_paths, name), name
    for name in ("_watch_needs_board", "_watch_session_messages", "_take_session_messages",
                 "_file_signer", "_proven_boards", "_needs_by_session"):
        assert not hasattr(server, name), name
    tools = {t["name"] for t in jarvis_mcp.TOOL_SPECS}
    assert not tools & {"session_needs", "session_messages", "read_session_message"}
    assert not hasattr(agent_keys, "MESSAGE"), "nothing signs a file any more"
    assert not hasattr(sd, "NEEDS_LOOP") and not hasattr(sd, "SESSION_MESSAGES_LOOP")
    from jarvis import workflow_store
    assert not hasattr(workflow_store, "with_decisions")


def test_agent_key_has_no_sign_command(keygen, capsys):
    from jarvis import agent_key
    with pytest.raises(SystemExit):
        agent_key.main(["sign", "x.md", "--session", PLAN])
    assert "invalid choice" in capsys.readouterr().err


def test_read_file_reads_nothing_of_jarvis_s_data_now(tmp_path, monkeypatch):
    from jarvis import repo_read
    project = tmp_path / "jarvis"
    data = project / "data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(data))
    (data / "needs").mkdir(parents=True)
    (data / "needs" / "jarvis.md").write_text("# jarvis\n- old: an old board.")
    (data / "tool-token").write_text("secret")
    for closed in ("data/needs/jarvis.md", "data/tool-token"):
        with pytest.raises(repo_read.Refused):
            repo_read.resolve_within(project, closed)


def test_communications_judge_the_watcher_and_the_messages_it_sends_only():
    from jarvis import subsystem_diagnostics as sd
    now = 1000.0
    sub = sd.communications(None, [], now, now - 600)
    assert [c.id for c in sub.checks] == ["watcher", "outgoing", "queued"]


# -- an agent's box counts its new messages ----------------------------------------------------

def test_an_agent_s_box_counts_its_new_messages(wired):
    server, hints, *_, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    m = _say(client, agent, "One.")
    _say(client, agent, "Two.")
    assert server._new_by_agent() == {PLAN: 2}
    assert {"type": "agents"} in hints, "the box is told to read it again"
    cl.mark(m["id"], cl.MessageState.READ)
    assert server._new_by_agent() == {PLAN: 1}
    [planning] = [a for a in server.agent_directory.agents() if a.session_id == PLAN]
    assert planning.needs == 1


# -- voice: what's new, read it, open it, reply ------------------------------------------------

class _Speech:
    def __init__(self):
        self.said = []

    async def say(self, text, priority=None, immediate=None, label=None):
        self.said.append(text)
        return SimpleNamespace(chunks=[text], text=text)

    async def wait_for(self, utt, timeout=60.0):
        return True


def test_whats_new_reads_each_item_s_new_messages_then_the_general_ones(wired, monkeypatch):
    server, *_, folder, home = wired
    client = TestClient(server.app)
    plan, diag = Agent(home, PLAN).onboarded(), Agent(home, DIAG).onboarded()
    first_item = _start_work(client, plan, folder)
    second_item = _start_work(client, diag, folder)
    general_old = _say(client, diag, "General, older.")
    a1 = _say(client, plan, "A one.", first_item)
    b1 = _say(client, diag, "B one.", second_item)
    a2 = _say(client, plan, "A two.", first_item)
    general_new = _say(client, plan, "General, newer.")
    cl.mark(b1["id"], cl.MessageState.READ)
    b2 = _say(client, diag, "B two.", second_item)
    order = [m["id"] for m in server.messages_api.whats_new()]
    assert order == [b2["id"], a1["id"], a2["id"], general_new["id"], general_old["id"]], \
        "the item with the newest message first, its new ones oldest first; then general"
    assert server.messages_api.whats_new()[0]["about_title"] == "Keys"
    out = asyncio.run(server.tool_messages_to_him({"new": True}))
    assert '"new":[' in out and "B two." in out and out.count("</") == 1, "walled"
    assert server._message_on == b2["id"], "he is on the first"

    monkeypatch.setattr(server, "speech", _Speech())
    said = asyncio.run(server.tool_read_aloud({}))
    assert said.startswith(f"Message {b2['id']},") and "B two." in said
    assert server.speech.said == [], "the brain reads it in its reply"
    said = asyncio.run(server.tool_read_aloud({"next": True}))
    assert said.startswith(f"Message {a1['id']},") and server._message_on == a1["id"]
    said = asyncio.run(server.tool_read_aloud({}))
    assert said.startswith(f"Message {a1['id']},"), "\"read it\" is the one he is on"


def test_read_it_with_no_message_on_says_how_to_start(wired):
    server, *_ = wired
    assert "messages_to_him" in asyncio.run(server.tool_read_aloud({}))
    assert "Nothing was captured" in asyncio.run(server.tool_reply({}))
    assert "messages_to_him" in asyncio.run(server.tool_open_inbox_package({}))


def test_reply_acts_on_the_message_he_is_on(wired):
    server, *_, folder, home = wired
    client = TestClient(server.app)
    m = _say(client, Agent(home, PLAN).onboarded(), "Done.")
    asyncio.run(server.tool_messages_to_him({"new": True}))
    said = asyncio.run(server.tool_reply({"text": "Thanks."}))
    newest = max(server._captures.values(), key=lambda c: c["id"])
    assert (newest["to_session"], newest["text"], newest["reply_to"]) == (PLAN, "Thanks.", m["id"])
    assert "read_capture" in said


def test_open_it_opens_the_package_the_message_carries_and_tells_its_story(
        wired, marks, tmp_path, monkeypatch):
    server, *_, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    plain = _say(client, agent, "No package here.")
    r = send(client, agent, signed_zip(tmp_path, agent), says="Notes.")
    carried = r.json()["message"]
    opened, told = [], []

    async def open_one(reference, how):
        opened.append((reference, how))
        package = SimpleNamespace(id="p-1", manifest=SimpleNamespace(
            runs="page", story="story.json", understanding=None,
            as_json=lambda: "{}"))
        return SimpleNamespace(id=reference), package, ""

    async def story(action, reference, slide=None, by="on the page"):
        told.append((action, reference, by))
        return True, "Telling it."
    monkeypatch.setattr(server, "_open_inbox_package", open_one)
    monkeypatch.setattr(server, "_package_story", story)
    monkeypatch.setattr(server.package_inbox, "questions", lambda p: [])
    server._on_message(plain["id"])
    assert "carries no package" in asyncio.run(server.tool_open_inbox_package({}))
    said = asyncio.run(server.tool_open_inbox_package({"message": carried["id"]}))
    assert opened == [(carried["carries"], server.package_inbox.BY_VOICE)]
    assert told == [("tell", "p-1", "by voice")], "its story told, as Tell the story does"
    assert "waits for his answer" in said and server._message_on == carried["id"]
