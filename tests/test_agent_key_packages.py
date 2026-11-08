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


def signed_zip(tmp_path: Path, agent: Agent, manifest=SKILL, statements=(), worker="",
               files=None) -> bytes:
    raw = tmp_path / f"raw-{manifest['name']}-{manifest['version']}.zip"
    raw.write_bytes(make_zip(files or {"SKILL.md": "Say the notes."}, manifest))
    package_signature.sign(raw, raw, agent.private, agent.public, agent.session_id, worker,
                           statements)
    return raw.read_bytes()


def send(client, agent: Agent, data: bytes, **query):
    path = INBOX + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")
    return client.post(path, content=data, headers={
        **agent.headers("POST", path, data), "Content-Type": "application/zip"})


def _start_work(client, agent, folder):
    r = agent.post(client, "/api/work", {"title": "Keys", "source": {"kind": "folder",
                                                                      "path": folder}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_signed_package_is_its_onboarded_agent_s_by_its_signature(wired, marks, tmp_path):
    server, *_ , home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    data = signed_zip(tmp_path, agent, worker="logo build agent")
    signed = package_signature.read(tmp_path / "raw-notes-skill-1.0.0.zip")
    assert signed.fingerprint == agent.fingerprint and signed.checks(agent.public)
    r = send(client, agent, data, session=PLAN)
    assert r.status_code == 200, r.text
    row = r.json()["inbox"]
    assert row["from_session"] == PLAN and row["sender_evidence"] == "subagent:logo build agent"
    assert row["sender"] == "logo build agent (Planning)"


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
    monkeypatch.setattr(server, "_message_sender", lambda dropped: None)
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
