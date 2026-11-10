"""Agent keys, phases 1 to 3: an agent makes its own key pair and submits
it; its own transcript proves which session did; switching the agent on is
Tim's act of onboarding it; from then on its signed requests are its own,
within its rights, and the access keys still work beside them."""

import importlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis import agent_keys
from tests.agent_key_fixtures import Agent, FakeKeygen

ORIGIN = {"Origin": "http://localhost:5173"}
PLAN = "sess-plan-0001"
DIAG = "sess-diag-0002"


@pytest.fixture
def keygen(monkeypatch, tmp_path):
    fake = FakeKeygen()
    monkeypatch.setattr(agent_keys, "RUNNER", fake)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    return fake


@pytest.fixture
def wired(monkeypatch, tmp_path, keygen):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import agent_key_store
    agent_key_store._migrated.clear()
    from jarvis import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    hints = []
    monkeypatch.setattr(server_module, "_send_to_session_clients", hints.append)
    from jarvis.attention_store import AttentionStore
    store = AttentionStore()
    monkeypatch.setattr(server_module, "attention_record", store)
    run_store.sync_agents({PLAN: ("Planning", "idle"), DIAG: ("Diagnostics", "idle")})
    folder = tmp_path / "work"
    folder.mkdir()
    return server_module, hints, store, str(folder), tmp_path / "claude-home"


# -- phase 1: the key ---------------------------------------------------------------------

def test_the_pair_is_made_once_private_and_signs_only_under_its_namespace(keygen, tmp_path):
    folder = agent_keys.key_folder(PLAN)
    assert folder == tmp_path / "claude-home" / "jarvis" / "agent-keys" / PLAN
    private, public = agent_keys.make_pair(folder, "c")
    assert stat.S_IMODE(os.stat(private).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    assert agent_keys.make_pair(folder, "c") == (private, public), "an existing pair is reused"
    assert sum("-t" in c for c in keygen.calls) == 1
    sig = agent_keys.sign(private, agent_keys.REQUEST, b"hello")
    assert agent_keys.signed_by(sig) == (public, agent_keys.REQUEST)
    assert agent_keys.verify(public, agent_keys.REQUEST, b"hello", sig)
    assert agent_keys.verify(public, agent_keys.REQUEST, b"hello", agent_keys.compact(sig))
    assert not agent_keys.verify(public, agent_keys.REQUEST, b"hellx", sig)
    assert not agent_keys.verify(public, agent_keys.PACKAGE, b"hello", sig), \
        "a signature made for a request never passes as a package's"
    assert agent_keys.session_key(PLAN) == (private, public)


def test_a_key_is_never_kept_through_a_link_or_readable_by_others(keygen, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    home = tmp_path / "claude-home" / "jarvis"
    home.mkdir(parents=True)
    (home / "agent-keys").symlink_to(elsewhere)
    with pytest.raises(agent_keys.AgentKeyError):
        agent_keys.make_pair(agent_keys.key_folder(PLAN), "c")
    (home / "agent-keys").unlink()
    private, _ = agent_keys.make_pair(agent_keys.key_folder(PLAN), "c")
    os.chmod(private, 0o644)
    with pytest.raises(agent_keys.AgentKeyError, match="0600"):
        agent_keys.sign(private, agent_keys.REQUEST, b"x")
    with pytest.raises(agent_keys.AgentKeyError):
        agent_keys.key_folder("../escape")


def test_no_folder_reader_ever_opens_a_key():
    from jarvis import repo_read
    for path in ("jarvis/agent-keys/s/id_ed25519.pub", "x/agent-keys/notes.md",
                 "pkg/.jarvis/work-key", "pkg/.jarvis/work-key.pub"):
        assert repo_read.sensitive_reason(Path(path), named_dotfiles=True), path


def test_a_signed_request_header_round_trips(keygen):
    private, public = agent_keys.make_pair(agent_keys.key_folder(PLAN), "c")
    header = agent_keys.request_header(private, public, "POST", "https://localhost:8340/a?b=1",
                                       b"{}", now=1000)
    fields = agent_keys.parse_header(header)
    assert fields["key"] == agent_keys.fingerprint(public) and fields["created"] == 1000
    assert fields["body"] == agent_keys.body_digest(b"{}")
    assert agent_keys.parse_header("Bearer x") is None
    assert agent_keys.parse_header('JARVIS-Signature key="a"') is None


@pytest.mark.ssh_keygen
def test_the_real_ssh_keygen_makes_signs_and_checks(tmp_path, monkeypatch):
    if not shutil.which("ssh-keygen"):
        pytest.skip("no ssh-keygen here")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    private, public = agent_keys.make_pair(agent_keys.key_folder(PLAN), "real")
    listed = subprocess.run(["ssh-keygen", "-lf", str(private) + ".pub"], capture_output=True,
                            text=True).stdout
    assert agent_keys.fingerprint(public) in listed, "the fingerprint is ssh-keygen's own"
    sig = agent_keys.sign(private, agent_keys.PACKAGE, b"a package")
    assert agent_keys.signed_by(sig) == (public, agent_keys.PACKAGE)
    assert agent_keys.verify(public, agent_keys.PACKAGE, b"a package", sig)
    assert not agent_keys.verify(public, agent_keys.PACKAGE, b"another", sig)
    assert not agent_keys.verify(public, agent_keys.REQUEST, b"a message", sig)


# -- phase 2: submit, prove, onboard -----------------------------------------------------

def _submit(client, agent, signature=None):
    from jarvis import agent_key
    sig = signature or agent_keys.compact(agent_keys.sign(
        agent.private, agent_keys.SUBMIT, agent_key.submit_text(agent.session_id, agent.public)))
    return client.post("/api/agent-keys", json={"session": agent.session_id,
                                                "public_key": agent.public, "signature": sig})


def _transcript_proof(monkeypatch, server, transcript: Path):
    """`_prove` over one transcript, as the session's own: checks it is
    asked for the claimed session alone and never a subagent's file."""
    from jarvis import session_message_sender
    asked = []

    def prove(what, shows, claimed=None, only_claimed=False, since=0.0, key=None,
              own_only=False):
        asked.append((claimed, only_claimed, own_only))
        return (session_message_sender.Sender(claimed, "Planning", "transcript")
                if shows(transcript) else None)
    monkeypatch.setattr(server, "_prove", prove)
    return asked


def test_a_submission_is_self_signed_proven_by_its_own_call_and_waits_for_tim(
        wired, monkeypatch, tmp_path):
    server, hints, store, _, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    asked = _transcript_proof(monkeypatch, server, transcript)

    other = Agent(home, DIAG)
    forged = agent_keys.compact(agent_keys.sign(other.private, agent_keys.SUBMIT, b"x"))
    assert _submit(client, agent, forged).status_code == 403, \
        "a submission must be signed by the key it submits"

    r = _submit(client, agent)
    assert r.status_code == 200 and r.json()["state"] == "pending"
    assert asked == [(PLAN, True, True)]

    # Words in the transcript that are not this session's own tool call
    # (a message it read, a tool's result) prove nothing.
    transcript.write_text("\n".join(json.dumps(line) for line in [
        {"type": "user", "message": {"content": agent.submit_line()["message"]["content"][0]
                                     ["input"]["command"]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content":
                                                  json.dumps(agent.submit_line())}]}},
    ]) + "\n")
    server._look_after_agent_keys()
    from jarvis import agent_key_store
    assert agent_key_store.for_session(PLAN).state == "pending"

    with transcript.open("a") as f:
        f.write(json.dumps(agent.submit_line()) + "\n")
    server._look_after_agent_keys()
    key = agent_key_store.for_session(PLAN)
    assert key.state == "waiting" and key.proven_by == "transcript"
    item = store.get("agent_key", key.fingerprint)
    assert item is not None and not item.audible, "a waiting key is counted, never spoken"
    row = next(a for a in client.get("/api/agents").json()["agents"] if a["session_id"] == PLAN)
    assert row["key"] == "waiting" and row["key_fingerprint"] == key.fingerprint


def test_an_unproven_submission_is_dropped_after_its_window_and_pending_is_capped(wired):
    from jarvis import agent_key_store
    _server, _, _, _, home = wired
    agent = Agent(home, PLAN)
    old = agent_key_store.submit(agent.public, PLAN,
                                 now=time.time() - agent_key_store.PROOF_WINDOW_SEC - 1)
    assert agent_key_store.to_prove() == []
    assert agent_key_store.drop_unproven() == [old.fingerprint]
    assert agent_key_store.get(old.fingerprint) is None
    for n in range(agent_key_store.PENDING_MAX + 3):
        a = Agent(home, f"many-{n:03d}")
        agent_key_store.submit(a.public, a.session_id, now=time.time() + n)
    pending = [k for k in agent_key_store.listing() if k.state == "pending"]
    assert len(pending) == agent_key_store.PENDING_MAX
    assert "many-000" not in {k.session_id for k in pending}, "the oldest go first"


def test_switching_the_agent_on_onboards_its_key_as_his_act(wired, monkeypatch):
    from jarvis import agent_key_store
    server, _, store, _, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN)
    key = agent_key_store.submit(agent.public, PLAN)
    agent_key_store.proven(key.fingerprint, "transcript")
    server._attend("agent_key", key.fingerprint, "waiting", audible=False)

    r = client.post(f"/api/agents/{PLAN}/active", json={"active": False}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["key"] is None, "switching off accepts nothing"
    before = time.time()
    r = client.post(f"/api/agents/{PLAN}/active", json={"active": True}, headers=ORIGIN)
    assert r.status_code == 200
    onboarded = r.json()["key"]
    assert onboarded["state"] == "onboarded" and onboarded["onboarded_by"] == "page"
    assert onboarded["onboarded_at"] >= before
    assert {g["right"] for g in onboarded["rights"]} == {"work", "workflows", "packages",
                                                         "watch"}
    assert store.get("agent_key", key.fingerprint).state == "resolved"
    assert [e["kind"] for e in agent_key_store.events(key.fingerprint)] == [
        "submitted", "proven", "onboarded"]

    # A newer key for the same session waits again, and his switch, by
    # voice this time, replaces the old one; the rights stay the agent's.
    newer = Agent(home / "second", PLAN)
    k2 = agent_key_store.submit(newer.public, PLAN)
    agent_key_store.proven(k2.fingerprint, "transcript")
    row = next(a for a in client.get("/api/agents").json()["agents"] if a["session_id"] == PLAN)
    assert row["active"] and row["key"] == "waiting"
    said = server.tool_set_agent_active({"name": "Planning", "active": True})
    assert "key is onboarded" in said
    assert agent_key_store.get(k2.fingerprint).onboarded_by == "voice"
    assert agent_key_store.get(key.fingerprint).state == "revoked"


def test_the_brain_reads_the_keys_and_revokes_one_as_his_revoke_does(wired):
    server, _, _, _, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    said = server.tool_agent_keys({})
    assert "Planning" in said and agent.fingerprint in said and "onboarded" in said
    assert "rights: packages, watch, work, workflows" in said
    assert "revoke_agent_key" in server.ACTING_TOOLS
    assert "Revoked" in server.tool_revoke_agent_key({"name": "Planning"})
    assert "already" in server.tool_revoke_agent_key({"fingerprint": agent.fingerprint})
    other = Agent(home, DIAG).onboarded()
    r = client.post(f"/api/agent-keys/{other.fingerprint}/revoke", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["key"]["revoked_by"] == "page"
    assert other.post(client, f"/api/agent-keys/{agent.fingerprint}/revoke").status_code == 403


# -- phase 3: signed requests and rights --------------------------------------------------

def _start(client, agent, folder):
    r = agent.post(client, "/api/work", {"title": "Keys", "source": {"kind": "folder",
                                                                      "path": folder}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_an_onboarded_agent_s_signed_reports_are_its_own(wired):
    server, _, _, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    wid = _start(client, agent, folder)
    r = agent.post(client, f"/api/work/{wid}/events",
                   {"kind": "update", "summary": "Halfway.", "detail": {"reporter": "key:x"}})
    assert r.status_code == 200, r.text
    found = client.get(f"/api/work/{wid}").json()
    assert {(e["reporter"], e["reporter_name"]) for e in found["events"]} == {
        (f"agent:{PLAN}", "Planning")}
    r = agent.post(client, "/api/workflows", {"name": "Build", "project": "jarvis"})
    assert r.status_code == 200, r.text
    r = agent.post(client, f"/api/workflows/{r.json()['id']}/events",
                   {"kind": "agent_attached", "summary": "Planning runs it.",
                    "detail": {"session_id": DIAG}})
    assert r.status_code == 409 and "not yours" in r.json()["error"]


def test_a_signature_out_of_its_time_replayed_tampered_or_unknown_is_refused(wired):
    server, _, _, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    body = json.dumps({"title": "Keys", "source": {"kind": "folder", "path": folder}}).encode()
    post = lambda headers, data=body: client.post(  # noqa: E731
        "/api/work", content=data, headers={**headers, "Content-Type": "application/json"})

    stale = agent.headers("POST", "/api/work", body, now=time.time() - 120)
    assert post(stale).status_code == 403
    once = agent.headers("POST", "/api/work", body)
    assert post(once).status_code == 200
    assert "already received" in post(once).json()["error"], "a nonce works once"
    tampered = agent.headers("POST", "/api/work", body)
    assert "body" in post(tampered, body.replace(b"Keys", b"Keyz")).json()["error"]
    other_host = agent.headers("POST", "/api/work", body, host="elsewhere:8340")
    assert post(other_host).status_code == 403, "the Host is signed"
    stranger = Agent(home / "x", "sess-stranger")
    assert "does not hold" in post(stranger.headers("POST", "/api/work", body)).json()["error"]


def test_a_key_not_yet_onboarded_or_revoked_admits_nothing(wired):
    from jarvis import agent_key_store
    server, _, _, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN)
    key = agent_key_store.submit(agent.public, PLAN)
    body = {"title": "Keys", "source": {"kind": "folder", "path": folder}}
    assert "not proven" in agent.post(client, "/api/work", body).json()["error"]
    agent_key_store.proven(key.fingerprint, "transcript")
    assert "waits for Tim" in agent.post(client, "/api/work", body).json()["error"]
    r = agent.get(client, "/api/agent-keys/mine")
    assert r.status_code == 200 and r.json()["state"] == "waiting", \
        "a key JARVIS holds may still ask where it stands"
    agent_key_store.onboard(PLAN, "page")
    assert agent.post(client, "/api/work", body).status_code == 200
    agent_key_store.revoke(key.fingerprint, "page", "test")
    assert "revoked" in agent.post(client, "/api/work", body).json()["error"]


def test_rights_bound_what_a_key_opens(wired):
    server, _, _, _, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    for path in ("/api/runs", "/api/restart", "/internal/tool", "/api/capture/1/accept",
                 f"/api/agents/{PLAN}/active"):
        r = agent.post(client, path, {})
        assert r.status_code == 403 and "rights" in r.json()["error"], path
    from jarvis import agent_rights
    assert agent_rights.allows(f"agent:{PLAN}", "WS", "/ws/sessions"), "watch is a default"
    assert not agent_rights.allows(f"agent:{PLAN}", "WS", "/ws/voice")
    assert not agent_rights.allows("agent:nobody", "POST", "/api/work")


def test_an_adopted_access_key_label_keeps_its_decisions_its_own(wired):
    """Planning asked decisions on its old access key, which admits nothing
    any more; onboarded, its own key reads his answers to them. (No key
    withdraws a decision any more: round 3.)"""
    from jarvis import agent_key_store, data_paths, work_store
    server, _, _, folder, home = wired
    client = TestClient(server.app)
    data_paths.access_keys_path().write_text(json.dumps({"keys": [
        {"label": "Planning", "categories": ["work"], "sha256": "x"}]}))
    item = work_store.create("Keys", "folder", folder, reporter="key:Planning")
    for key in ("colour", "size"):
        work_store.record(work_store.WorkEvent(item.id, "decision_needed", f"Which {key}?",
                                               {"key": key, "asked_by": "key:Planning"},
                                               reporter="key:Planning"))
    assert client.post(f"/api/work/{item.id}/decisions/size", json={"answer": "big"},
                       headers=ORIGIN).status_code == 200
    assert client.post("/api/work", json={}, headers={
        "Authorization": "Bearer jarvis_key_whatever"}).status_code == 403, \
        "an access key admits nothing"

    agent = Agent(home, PLAN)
    k = agent_key_store.submit(agent.public, PLAN)
    agent_key_store.proven(k.fingerprint, "transcript")
    server._set_agent_active(PLAN, True, "page")
    assert agent_key_store.get(k.fingerprint).label == "Planning"
    r = agent.get(client, "/api/work/answers")
    assert [a["key"] for a in r.json()["answers"]] == ["size"]


def test_the_report_commands_sign_with_the_session_s_key(wired, monkeypatch, capsys):
    """work_report.py and workflow_report.py sign with --as <session>'s
    key, through the real route."""
    server, _, _, folder, home = wired
    Agent(home, PLAN).onboarded()
    client = TestClient(server.app)
    from jarvis import work_report, workflow_report

    def send(self, method, url, data, content_type):
        headers = {"Content-Type": content_type}
        if self.signer:
            headers["Authorization"] = agent_keys.request_header(
                self.signer[0], self.signer[1], method, url, data or b"")
        r = client.request(method, url, content=data, headers=headers)
        return workflow_report._Response(r.status_code, r.content,
                                         r.headers.get("content-type", ""))
    monkeypatch.setattr(workflow_report._Client, "send_bytes", send)
    monkeypatch.setattr(workflow_report, "base_url", lambda: "http://testserver")
    assert work_report.main(["work_report.py", "--as", PLAN, "start", "Keys",
                             "--folder", folder]) == 0
    wid = capsys.readouterr().out.strip()
    assert client.get(f"/api/work/{wid}").json()["events"][0]["reporter"] == f"agent:{PLAN}"
    assert workflow_report.main(["workflow_report.py", "--as", PLAN, "start", "Build",
                                 "--project", "jarvis"]) == 0
    with pytest.raises(SystemExit, match="agent_key init"):
        work_report.main(["work_report.py", "--as", "sess-none", "start", "x", "--folder",
                          folder])


def test_the_agent_key_command_inits_and_submits(wired, monkeypatch, capsys, tmp_path):
    server, _, _, _, home = wired
    client = TestClient(server.app)
    from jarvis import agent_key, workflow_report
    transcript = tmp_path / "t.jsonl"
    _transcript_proof(monkeypatch, server, transcript)
    assert agent_key.main(["init", "--session", PLAN]) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "agent_key submit" in ln)
    fingerprint = line.split("--fingerprint ")[1]
    transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": line}}]}}) + "\n")

    def send(self, method, url, data, content_type):
        r = client.request(method, url, content=data, headers={"Content-Type": content_type})
        return workflow_report._Response(r.status_code, r.content,
                                         r.headers.get("content-type", ""))
    monkeypatch.setattr(workflow_report._Client, "send_bytes", send)
    monkeypatch.setattr(agent_key, "base_url", lambda: "http://testserver")
    with pytest.raises(SystemExit, match="not"):
        agent_key.main(["submit", "--session", PLAN, "--fingerprint", "SHA256:wrong"])
    assert agent_key.main(["submit", "--session", PLAN, "--fingerprint", fingerprint]) == 0
    from jarvis import agent_key_store
    assert agent_key_store.get(fingerprint).state == "waiting"
