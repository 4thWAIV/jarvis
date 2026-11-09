"""Agent keys, phase 4: a package carries its sender's signature, and a
message file or needs board its `.sig`; anything signed by a key JARVIS
does not know as an onboarded agent's is held, its Work item goes to
Error, and JARVIS makes Tim an error package offering his two acts."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis import agent_keys, package_signature
from tests.agent_key_fixtures import Agent
from tests.test_agent_keys import DIAG, ORIGIN, PLAN, keygen, wired  # noqa: F401
from tests.test_work_packages import make_zip, marks  # noqa: F401

INBOX = "/api/packages/inbox"
SKILL = {"name": "notes-skill", "version": "1.0.0", "purpose": "Say the notes.",
         "runs": "conversation", "skill": "SKILL.md", "returns": ["results/**"]}


def signed_zip(tmp_path: Path, agent: Agent, manifest=SKILL, statements=(),
               files=None) -> bytes:
    raw = tmp_path / f"raw-{manifest['name']}-{manifest['version']}.zip"
    raw.write_bytes(make_zip(files or {"SKILL.md": "Say the notes."}, manifest))
    package_signature.sign(raw, raw, agent.private, agent.public, agent.session_id, statements)
    return raw.read_bytes()


def _running(monkeypatch, server, tmp_path, session=PLAN):
    """`session` running in tmp/plan, its transcripts under tmp/claude."""
    from types import SimpleNamespace
    s = SimpleNamespace(session_id=session, cwd=str(tmp_path / "plan"), project="plan",
                        provider="anthropic", voice_name="Planning", state="idle",
                        agents_active=0, agents_capped=False)
    monkeypatch.setattr(server, "_snapshot_or_empty", lambda: SimpleNamespace(sessions=[s]))
    monkeypatch.setattr(server.session_watch, "config_roots", lambda: [tmp_path / "claude"])
    return s


def subagent_ran(tmp_path, session, description, command):
    """A Claude subagent of `session` (its own file, named by its meta
    description) running `command`."""
    from jarvis import session_watch
    d = (tmp_path / "claude" / "projects" / session_watch.encode_cwd(str(tmp_path / "plan"))
         / session / "subagents")
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent-a1b2c3.jsonl").write_text(json.dumps({"type": "assistant", "message": {
        "content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]}})
        + "\n")
    (d / "agent-a1b2c3.meta.json").write_text(json.dumps({"description": description}))


def send(client, agent: Agent, data: bytes, **query):
    path = INBOX + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")
    return client.post(path, content=data, headers={
        **agent.headers("POST", path, data), "Content-Type": "application/zip"})


def _start_work(client, agent, folder):
    r = agent.post(client, "/api/work", {"title": "Keys", "source": {"kind": "folder",
                                                                      "path": folder}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_signed_package_is_its_onboarded_agent_s_by_its_signature(wired, marks, tmp_path,
                                                                    monkeypatch):
    server, *_ , home = wired
    client = TestClient(server.app)
    _running(monkeypatch, server, tmp_path)
    agent = Agent(home, PLAN).onboarded()
    data = signed_zip(tmp_path, agent)
    signed = package_signature.read(tmp_path / "raw-notes-skill-1.0.0.zip")
    assert signed.fingerprint == agent.fingerprint and signed.checks(agent.public)
    r = send(client, agent, data, session=PLAN, source="notes")
    assert r.status_code == 200, r.text
    row = r.json()["inbox"]
    assert (row["from_session"], row["sender_evidence"], row["sender"]) == (
        PLAN, "agent_signature", "Planning"), "no subagent proven: the agent alone"


def test_a_subagent_is_named_with_its_parent_only_on_its_own_file_s_proof(wired, marks,
                                                                         tmp_path, monkeypatch):
    """The parent's signature proves the parent; the subagent is named only
    when its own file shows it running make_package on that source."""
    server, *_ , home = wired
    client = TestClient(server.app)
    _running(monkeypatch, server, tmp_path)
    agent = Agent(home, PLAN).onboarded()
    subagent_ran(tmp_path, PLAN, "Feynman",
                 f"python -m jarvis.make_package notes/ --session {PLAN}")
    r = send(client, agent, signed_zip(tmp_path, agent), session=PLAN, source="notes/")
    row = r.json()["inbox"]
    assert (row["sender"], row["sender_evidence"]) == ("Feynman (Planning)", "subagent:Feynman")
    other = {**SKILL, "version": "1.0.1"}
    r = send(client, agent, signed_zip(tmp_path, agent, other), session=PLAN,
             source="elsewhere/")
    assert r.json()["inbox"]["sender"] == "Planning", "its file shows another source"


def test_a_package_changed_after_it_was_signed_is_held(wired, marks, tmp_path):
    import zipfile
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    signed_zip(tmp_path, agent)
    raw = tmp_path / "raw-notes-skill-1.0.0.zip"
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(raw) as src, zipfile.ZipFile(changed, "w") as out:
        for info in src.infolist():
            data = src.read(info)
            out.writestr(info, b"Do something else." if info.filename == "SKILL.md" else data)
    r = send(client, agent, changed.read_bytes(), session=PLAN)
    assert r.status_code == 409 and "Held" in r.json()["error"]


def test_a_key_not_onboarded_is_held_with_its_work_item_in_error_until_he_switches_it_on(
        wired, marks, tmp_path):
    from jarvis import agent_key_store, package_inbox, work_store
    server, hints, store, folder, home = wired
    client = TestClient(server.app)
    reporter = Agent(home, DIAG).onboarded()
    wid = _start_work(client, reporter, folder)
    agent = Agent(home, PLAN)
    key = agent_key_store.submit(agent.public, PLAN)
    agent_key_store.proven(key.fingerprint, "transcript")

    r = send(client, agent, signed_zip(tmp_path, agent), session=PLAN, work=wid)
    assert r.status_code == 409 and "Held" in r.json()["error"]
    [held] = agent_key_store.held()
    assert held.kind == "package" and "notes skill" in held.what and held.work_id == wid
    assert Path(held.path).is_file()
    item = work_store.get(wid)
    assert item.state == "error" and "Planning" in item.headline
    # The error package, from JARVIS, unshown in his inbox.
    rows = package_inbox.listing()
    [error] = [e for e in rows if e.from_agent == "JARVIS"]
    assert error.name.startswith("key-error-") and error.work_id == wid
    from jarvis import work_packages
    page = (work_packages.find(error.package_id).folder / "index.html").read_text()
    assert agent.fingerprint in page and PLAN in page and "notes skill" in page
    assert "Switch Planning on" in page and "Refuse and revoke" in page
    assert store.get("package", error.id) is not None

    # The same package again is the same hold, and no second error package.
    assert send(client, agent, signed_zip(tmp_path, agent), session=PLAN,
                work=wid).status_code == 409
    assert len(agent_key_store.held()) == 1
    assert len([e for e in package_inbox.listing() if e.from_agent == "JARVIS"]) == 1

    # His act in the error package: switch it on, as his click.
    result = work_packages.find(error.package_id).folder / "results"
    result.mkdir(exist_ok=True)
    (result / "act.json").write_text(json.dumps({"act": "onboard"}))
    server._key_error_act(work_packages.find(error.package_id))
    assert agent_key_store.for_session(PLAN).state == "onboarded"
    assert agent_key_store.for_session(PLAN).onboarded_by == "page"
    assert agent_key_store.held() == []
    arrived = [e for e in package_inbox.listing() if e.name == "notes-skill"]
    assert arrived and arrived[0].from_session == PLAN and arrived[0].work_id == wid
    assert package_inbox.get(error.id).state == "closed"
    assert work_store.get(wid).state != "error"
    assert not Path(held.path).exists()


def test_refusing_revokes_the_key_and_drops_what_was_held(wired, marks, tmp_path):
    from jarvis import agent_key_store, package_inbox, work_packages
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN)
    key = agent_key_store.submit(agent.public, PLAN)
    agent_key_store.proven(key.fingerprint, "transcript")
    send(client, agent, signed_zip(tmp_path, agent), session=PLAN)
    [held] = agent_key_store.held()
    [error] = [e for e in package_inbox.listing() if e.from_agent == "JARVIS"]
    folder = work_packages.find(error.package_id).folder
    (folder / "results").mkdir(exist_ok=True)
    (folder / "results" / "act.json").write_text(json.dumps({"act": "refuse"}))
    server._key_error_act(work_packages.find(error.package_id))
    assert agent_key_store.get(key.fingerprint).state == "revoked"
    assert agent_key_store.held() == [] and not Path(held.path).exists()
    assert package_inbox.get(error.id).state == "closed"
    # A package that saves an act.json but is not one of JARVIS's own does nothing.
    assert agent_key_store.error_subject("someone-else") is None


def test_a_report_from_a_key_not_onboarded_is_refused_held_and_told(wired, tmp_path):
    from jarvis import agent_key_store, package_inbox
    server, *_, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN)
    key = agent_key_store.submit(agent.public, PLAN)
    agent_key_store.proven(key.fingerprint, "transcript")
    r = agent.post(client, "/api/work", {"title": "Speech migration",
                                         "source": {"kind": "folder", "path": folder}})
    assert r.status_code == 403
    [held] = agent_key_store.held()
    assert held.kind == "request" and "Speech migration" in held.what
    assert [e for e in package_inbox.listing() if e.from_agent == "JARVIS"]
    stranger = Agent(home / "x", "sess-stranger")
    assert stranger.post(client, "/api/work", {"title": "x"}).status_code == 403
    assert len(agent_key_store.held()) == 1, "a key JARVIS does not hold proves nothing"


def test_a_decision_package_needs_its_signed_check_statement(wired, marks, tmp_path):
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    deck = {**SKILL, "name": "a-deck"}
    files = {"SKILL.md": "x", "deck-data.js": "window.DECK = {}", "scripts/decide.py": "x"}
    r = send(client, agent, signed_zip(tmp_path, agent, deck, files=files), session=PLAN)
    assert r.status_code == 409 and "not passed its check" in r.json()["error"]
    ok = signed_zip(tmp_path, agent, {**deck, "version": "1.0.1"}, files=files,
                    statements=(package_signature.DECISION_CHECKED,))
    assert send(client, agent, ok, session=PLAN).status_code == 200


def _message(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text)
    old = time.time() - 60
    import os
    os.utime(path, (old, old))
    return path


def test_a_signed_message_is_its_agent_s_and_one_from_a_waiting_key_waits_for_him(
        wired, monkeypatch):
    from jarvis import agent_key, agent_key_store, data_paths, run_store
    server, *_, home = wired
    folder = data_paths.session_messages_dir() / "jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    planner = Agent(home, PLAN).onboarded()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    path = _message(folder, "done.md", "# Phase one is in\n\nAll green.\n")
    assert agent_key.main(["sign", str(path), "--session", PLAN]) == 0
    [taken] = server._take_session_messages()
    assert (taken.agent_name, taken.sender_session) == ("Planning", PLAN)
    assert taken.sender_evidence == "agent_signature"
    assert not path.exists() and not agent_key.sig_path(path).exists()

    diag = Agent(home / "d", DIAG)
    k = agent_key_store.submit(diag.public, DIAG)
    agent_key_store.proven(k.fingerprint, "transcript")
    path = _message(folder, "later.md", "# Tests are flaky\n\nLook.\n")
    agent_key.sig_path(path).write_text(agent_keys.sign(
        diag.private, agent_keys.MESSAGE, agent_key.message_text(path, path.read_bytes())))
    assert server._take_session_messages() == []
    assert (folder / "later.held").exists()
    [held] = agent_key_store.held()
    assert held.kind == "message" and "Tests are flaky" in held.what
    server._set_agent_active(DIAG, True, "voice")
    assert (folder / "later.md").exists(), "released: put back to be taken"
    import os
    os.utime(folder / "later.md", (time.time() - 60, time.time() - 60))
    [taken] = server._take_session_messages()
    assert taken.sender_session == DIAG
    assert run_store.get_session_message(taken.id).sender_evidence == "agent_signature"


def test_a_message_changed_after_it_was_signed_is_unsigned_not_forged(wired, monkeypatch):
    from jarvis import agent_key, data_paths
    server, *_, home = wired
    Agent(home, PLAN).onboarded()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    folder = data_paths.session_messages_dir() / "jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    path = _message(folder, "x.md", "# One\n\nTwo.\n")
    agent_key.main(["sign", str(path), "--session", PLAN])
    path.write_text("# One\n\nThree.\n")
    signer, key, status, _ = server._file_signer(path)
    assert (signer, status) == (None, "unsigned")


# -- phase 5: work-item keys --------------------------------------------------------------

AGENT_PKG = {"name": "build-agent", "version": "1.0.0", "purpose": "Build it.", "runs": "agent",
             "brief": "BRIEF.md", "returns": ["results/**"]}


class WorkKeyAgent(Agent):
    """A work-item key as the package carries it, signing as an agent does."""

    def __init__(self, private, public):
        self.private, self.public = private, public
        self.fingerprint = agent_keys.fingerprint(public)
        self.session_id = ""


def test_a_work_key_in_an_agent_package_reports_on_its_item_alone_and_ends_with_it(
        wired, marks, tmp_path):
    from jarvis import agent_key_store, make_package, work_packages, work_store
    server, *_, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    wid = _start_work(client, agent, folder)
    other = _start_work(client, agent, folder)
    raw = tmp_path / "agent.zip"
    raw.write_bytes(make_zip({"BRIEF.md": "Build it."}, AGENT_PKG))
    grant = make_package.add_work_key(raw, wid)
    make_package.sign(raw, (agent.private, agent.public), PLAN, (), grant)
    r = send(client, agent, raw.read_bytes(), session=PLAN, work=wid)
    assert r.status_code == 200, r.text
    package = work_packages.find(r.json()["package"]["id"])
    private = package.folder / ".jarvis" / "work-key"
    assert private.is_file() and (private.stat().st_mode & 0o777) == 0o600
    found = agent_keys.work_key(package.folder)
    assert found is not None and found[1] == grant["public_key"]
    [key] = agent_key_store.work_keys(wid)
    assert key.issued_by == f"agent:{PLAN}" and key.expires_at > time.time() + 29 * 86400
    worker = WorkKeyAgent(*found)

    r = worker.post(client, f"/api/work/{wid}/events", {"kind": "update", "summary": "Built."})
    assert r.status_code == 200, r.text
    events = client.get(f"/api/work/{wid}").json()
    assert events["events"][-1]["reporter_name"] == "Planning", "it reports for its issuer"
    assert events["item"]["work_keys"][0]["issued_by"] == "Planning"
    assert worker.post(client, f"/api/work/{other}/events",
                       {"kind": "update", "summary": "x"}).status_code == 403
    assert worker.post(client, "/api/work", {"title": "x"}).status_code == 403
    assert worker.post(client, "/api/agent-keys/work-keys",
                       {"work": wid, "public_key": agent.public}).status_code == 403

    r = agent.post(client, f"/api/agent-keys/work-keys/{key.fingerprint}/renew")
    assert r.status_code == 200
    assert worker.post(client, f"/api/agent-keys/work-keys/{key.fingerprint}/renew",
                       ).status_code == 403
    work_store.record(work_store.WorkEvent(wid, work_store.CLOSED, "Done with it.", {}))
    server._work_recorded(work_store.get(wid), work_store.CLOSED, None)
    assert agent_key_store.get(key.fingerprint).state == "revoked"
    assert worker.post(client, f"/api/work/{wid}/events",
                       {"kind": "update", "summary": "x"}).status_code == 403


def test_an_agent_grants_a_work_key_into_a_folder(wired, monkeypatch, capsys, tmp_path):
    from jarvis import agent_key, agent_key_store, workflow_report
    server, *_, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    wid = _start_work(client, agent, folder)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))

    def send_bytes(self, method, url, data, content_type):
        headers = {"Content-Type": content_type}
        if self.signer:
            headers["Authorization"] = agent_keys.request_header(
                self.signer[0], self.signer[1], method, url, data or b"")
        r = client.request(method, url, content=data, headers=headers)
        return workflow_report._Response(r.status_code, r.content,
                                         r.headers.get("content-type", ""))
    monkeypatch.setattr(workflow_report._Client, "send_bytes", send_bytes)
    monkeypatch.setattr(agent_key, "base_url", lambda: "http://testserver")
    place = tmp_path / "place"
    place.mkdir()
    assert agent_key.main(["grant", "--work", wid, "--into", str(place),
                           "--session", PLAN]) == 0
    assert (place / ".jarvis" / "work-key").is_file()
    assert [k.work_id for k in agent_key_store.work_keys()] == [wid]
    with pytest.raises(SystemExit, match="no open work item"):
        agent_key.main(["grant", "--work", "work-none", "--into", str(tmp_path / "p2"),
                        "--session", PLAN])
