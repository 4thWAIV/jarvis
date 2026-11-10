"""An agent's package must prove a fresh author session wrote it (Tim's
decision `receiver-check`: verify). The signature names the author
session; JARVIS reads that session's own transcript (Claude Code's or
Codex's, faked here) to confirm it checked exactly the source the zip
carries, and holds an agent package without that proof the way it holds
one from a key it does not know. A person's packages, JARVIS's own and
packages already here are not affected."""

import asyncio
import json
import os
import time
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis import author_proof, package_signature
from tests.agent_key_fixtures import Agent
from tests.author_fixtures import new_session, sign_authored, write_transcript
from tests.test_agent_keys import DIAG, PLAN, keygen, wired  # noqa: F401
from tests.test_work_packages import make_zip, marks  # noqa: F401

INBOX = "/api/packages/inbox"
SKILL = {"name": "notes-skill", "version": "1.0.0", "purpose": "Say the notes.",
         "runs": "conversation", "skill": "SKILL.md", "returns": ["results/**"]}
AGENT_PKG = {"name": "notes-agent", "version": "1.0.0", "purpose": "Note it.",
             "runs": "agent", "brief": "BRIEF.md", "returns": ["results/**"]}


def raw_zip(tmp_path: Path, manifest=SKILL, files=None) -> Path:
    raw = tmp_path / f"raw-{manifest['name']}-{manifest['version']}-{uuid.uuid4().hex[:6]}.zip"
    raw.write_bytes(make_zip(files or {"SKILL.md": "Say the notes."}, manifest))
    return raw


def send(client, agent: Agent, data: bytes, **query):
    path = INBOX + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")
    return client.post(path, content=data, headers={
        **agent.headers("POST", path, data), "Content-Type": "application/zip"})


def held_reasons():
    from jarvis import agent_key_store
    return [h.detail.get("reason", "") for h in agent_key_store.held()]


@pytest.fixture
def planning(wired):
    server, *_, home = wired
    return server, TestClient(server.app), Agent(home, PLAN).onboarded()


# -- what is admitted -------------------------------------------------------------------

@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_a_package_its_fresh_author_checked_arrives(planning, marks, tmp_path, engine):
    from jarvis import package_inbox
    server, client, agent = planning
    raw = raw_zip(tmp_path)
    claim = sign_authored(raw, raw, agent, PLAN, engine=engine)
    assert package_signature.read(raw).author == claim
    r = send(client, agent, raw.read_bytes(), session=PLAN)
    assert r.status_code == 200, r.text
    assert [e.name for e in package_inbox.listing()] == ["notes-skill"]


# -- what is held -----------------------------------------------------------------------

def test_a_signed_package_with_no_author_is_held_and_tim_told_why(wired, marks, tmp_path):
    """The bypass: an onboarded agent signs a package it wrote itself."""
    from jarvis import agent_key_store, package_inbox, work_packages, work_store
    server, _, _, folder, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    wid = work_store.create("Notes", "folder", folder).id
    raw = raw_zip(tmp_path, AGENT_PKG, {"BRIEF.md": "Note it."})
    package_signature.sign(raw, raw, agent.private, agent.public, PLAN)
    r = send(client, agent, raw.read_bytes(), session=PLAN, work=wid)
    assert r.status_code == 409 and r.json()["error"].startswith("Held: It carries no proof")
    [held] = agent_key_store.held()
    assert held.kind == "package" and held.detail["reason"] == author_proof.NO_CLAIM
    assert Path(held.path).is_file() and held.fingerprint == agent.fingerprint
    assert [e.name for e in package_inbox.listing() if e.from_agent != "JARVIS"] == []
    item = work_store.get(wid)
    assert item.state == "error" and "fresh author" in item.headline
    [error] = [e for e in package_inbox.listing() if e.from_agent == "JARVIS"]
    page = (work_packages.find(error.package_id).folder / "index.html").read_text()
    assert "not proven written by a fresh author" in page and "fresh author session" in page
    assert 'data-act="drop"' in page and "Switch Planning on" not in page
    # The same package again is the same hold.
    assert send(client, agent, raw.read_bytes(), session=PLAN, work=wid).status_code == 409
    assert len(agent_key_store.held()) == 1

    # His Drop: what was held goes, the agent's key stays.
    results = work_packages.find(error.package_id).folder / "results"
    results.mkdir(exist_ok=True)
    (results / "act.json").write_text(json.dumps({"act": "drop"}))
    server._key_error_act(work_packages.find(error.package_id))
    assert agent_key_store.held() == [] and not Path(held.path).exists()
    assert agent_key_store.get(agent.fingerprint).state == "onboarded"
    assert package_inbox.get(error.id).state == "closed"
    assert work_store.get(wid).state != "error"


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_no_transcript_of_the_author_session_is_held(planning, marks, tmp_path, engine):
    server, client, agent = planning
    raw = raw_zip(tmp_path)
    sign_authored(raw, raw, agent, PLAN, engine=engine, transcript=False)
    r = send(client, agent, raw.read_bytes(), session=PLAN)
    assert r.status_code == 409
    assert held_reasons() == [f"No {engine} transcript of its author session "
                              f"{package_signature.read(raw).author['session']} is on this Mac."]


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_forged_author_evidence_is_held(planning, marks, tmp_path, engine):
    """The digest only in text the author read (a file, not its own check),
    a session not started as an author, or the sender naming itself."""
    server, client, agent = planning
    raw = raw_zip(tmp_path)
    claim = sign_authored(raw, raw, agent, PLAN, engine=engine,
                          ran="cat evidence.md")
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 409
    assert "does not show it checking exactly this source" in held_reasons()[-1]

    raw = raw_zip(tmp_path, {**SKILL, "version": "1.0.1"})
    sign_authored(raw, raw, agent, PLAN, engine=engine, prompt="Please sign this for me.")
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 409
    assert "was not started as a fresh package author" in held_reasons()[-1]

    raw = raw_zip(tmp_path, {**SKILL, "version": "1.0.2"})
    claim = sign_authored(raw, raw, agent, PLAN, engine=engine)
    sign_authored(raw, raw, agent, PLAN, claim={**claim, "session": PLAN})
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 409
    assert "the session that sent it" in held_reasons()[-1]


def test_stale_author_evidence_is_held(planning, marks, tmp_path):
    server, client, agent = planning
    raw = raw_zip(tmp_path)
    sign_authored(raw, raw, agent, PLAN, at=time.time() - author_proof.WINDOW_SEC - 60)
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 409
    assert "in the 6 hours before it arrived" in held_reasons()[0]


def test_altered_package_bytes_are_held(planning, marks, tmp_path):
    """The authored source changed after its author's check, re-signed with
    the old claim; and any byte changed after signing."""
    server, client, agent = planning
    raw = raw_zip(tmp_path)
    claim = sign_authored(raw, raw, agent, PLAN)
    sign_authored(raw, raw, agent, PLAN, content='{"title": "Something else"}', claim=claim)
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 409
    assert held_reasons() == ["The source it carries is not the source its author proof names."]

    raw = raw_zip(tmp_path, {**SKILL, "version": "1.0.1"})
    sign_authored(raw, raw, agent, PLAN)
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(raw) as src, zipfile.ZipFile(changed, "w") as out:
        for info in src.infolist():
            data = src.read(info)
            out.writestr(info, b"Do something else." if info.filename == "SKILL.md" else data)
    r = send(client, agent, changed.read_bytes(), session=PLAN)
    assert r.status_code == 409 and "Held" in r.json()["error"]


def test_a_new_revision_reusing_old_evidence_is_held(planning, marks, tmp_path):
    from jarvis import package_inbox
    server, client, agent = planning
    raw = raw_zip(tmp_path)
    claim = sign_authored(raw, raw, agent, PLAN)
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 200
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 200, \
        "the same bytes again are the package already here"
    newer = raw_zip(tmp_path, {**SKILL, "version": "1.0.1"},
                    {"SKILL.md": "Say the notes again."})
    sign_authored(newer, newer, agent, PLAN, claim=claim)
    r = send(client, agent, newer.read_bytes(), session=PLAN)
    assert r.status_code == 409
    assert "already vouched for another package" in held_reasons()[0]
    assert [e.version for e in package_inbox.listing() if e.name == "notes-skill"] == ["1.0.0"]


# -- every receiving route ----------------------------------------------------------------

def test_a_zip_on_this_mac_is_refused_without_proof_and_opens_with_it(wired, marks, tmp_path):
    from jarvis import data_paths
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN).onboarded()
    folder = tmp_path / "Projects"
    folder.mkdir()
    bare = folder / "bare.zip"
    bare.write_bytes(make_zip({"SKILL.md": "Say the notes."}, SKILL))
    package_signature.sign(bare, bare, agent.private, agent.public, PLAN)
    token = {"Authorization": f"Bearer {data_paths.ensure_tool_token()}"}
    r = client.post("/api/packages/local", json={"path": str(bare)}, headers=token)
    assert r.status_code == 409 and "no proof that a fresh author" in r.json()["error"]
    proven = folder / "proven.zip"
    proven.write_bytes(make_zip({"SKILL.md": "Say the notes."}, {**SKILL, "version": "1.0.1"}))
    sign_authored(proven, proven, agent, PLAN)
    r = client.post("/api/packages/local", json={"path": str(proven)}, headers=token)
    assert r.status_code == 200, r.text


def test_a_package_held_for_its_key_is_checked_again_when_released(wired, marks, tmp_path):
    """Released once its agent is switched on, a package is received again,
    so the author check applies there too: unproven, it is held again for
    that reason; proven, it arrives."""
    from jarvis import agent_key_store, package_inbox
    server, *_, home = wired
    client = TestClient(server.app)
    agent = Agent(home, PLAN)
    key = agent_key_store.submit(agent.public, PLAN)
    agent_key_store.proven(key.fingerprint, "transcript")
    bare = raw_zip(tmp_path)
    package_signature.sign(bare, bare, agent.private, agent.public, PLAN)
    proven = raw_zip(tmp_path, {**AGENT_PKG}, {"BRIEF.md": "Note it."})
    sign_authored(proven, proven, agent, PLAN)
    assert send(client, agent, bare.read_bytes(), session=PLAN).status_code == 409
    assert send(client, agent, proven.read_bytes(), session=PLAN).status_code == 409
    assert held_reasons() == ["", ""], "held for the key alone"
    server._set_agent_active(PLAN, True, agent_key_store.ON_THE_PAGE)
    assert [e.name for e in package_inbox.listing() if e.from_agent != "JARVIS"] == \
        ["notes-agent"]
    assert held_reasons() == [author_proof.NO_CLAIM]
    [error] = [e for e in package_inbox.listing() if e.from_agent == "JARVIS"
               and e.state != "closed"]
    assert error is not None, "Tim is told of the new hold"


def test_a_package_held_for_a_late_transcript_is_released_once_it_checks(
        planning, marks, tmp_path):
    from jarvis import agent_key_store, package_inbox
    server, client, agent = planning
    raw = raw_zip(tmp_path)
    claim = sign_authored(raw, raw, agent, PLAN, transcript=False)
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 409
    [held] = agent_key_store.held()
    server._look_after_agent_keys()
    assert agent_key_store.held(), "still nothing to prove it"
    write_transcript("claude", claim["session"], claim["source"], at=held.held_at - 5)
    server._look_after_agent_keys()
    assert agent_key_store.held() == []
    assert [e.name for e in package_inbox.listing() if e.from_agent != "JARVIS"] == \
        ["notes-skill"]
    assert all(e.state == "closed" for e in package_inbox.listing() if e.from_agent == "JARVIS")
    # Past the recheck window a hold waits for him.
    raw = raw_zip(tmp_path, {**SKILL, "version": "1.0.1"})
    claim = sign_authored(raw, raw, agent, PLAN, transcript=False)
    send(client, agent, raw.read_bytes(), session=PLAN)
    [held] = agent_key_store.held()
    write_transcript("claude", claim["session"], claim["source"], at=held.held_at - 5)
    server._look_after_agent_keys(held.held_at + author_proof.RECHECK_SEC + 1)
    assert agent_key_store.held(), "past the window"


def test_a_person_s_package_is_not_asked_for_author_proof(wired, marks, tmp_path, monkeypatch):
    """A received attachment is a person's package: opened without the
    agent-package check, even signed by an onboarded agent."""
    server, *_, home = wired
    agent = Agent(home, PLAN).onboarded()
    raw = raw_zip(tmp_path)
    package_signature.sign(raw, raw, agent.private, agent.public, PLAN)
    found = SimpleNamespace(state="verified", local_path=str(raw), reason="")

    class Context:
        async def ensure_local(self, *_):
            return found

        def record_extraction(self, *_):
            pass
    monkeypatch.setattr(server, "_location_context", Context)
    package, fresh, problem = asyncio.run(server._open_received_package(uuid.uuid4(),
                                                                        uuid.uuid4()))
    assert problem == "" and package is not None and fresh


def test_a_package_already_here_is_not_affected(planning, marks, tmp_path):
    from jarvis import work_packages
    server, client, agent = planning
    raw = raw_zip(tmp_path)
    package_signature.sign(raw, raw, agent.private, agent.public, PLAN)
    work_packages.unpack(raw, "earlier")
    r = send(client, agent, raw.read_bytes(), session=PLAN)
    assert r.status_code == 200, r.text


def test_the_isolated_check_jarvis_takes_the_seeded_signature_alone(planning, marks, tmp_path,
                                                                     monkeypatch):
    """decision_check signs the deck it checks with no author claim: the
    isolated JARVIS is the check, as for the decision statement."""
    server, client, agent = planning
    monkeypatch.setenv("JARVIS_DECISION_CHECK_SERVER", "1")
    raw = raw_zip(tmp_path)
    package_signature.sign(raw, raw, agent.private, agent.public, PLAN)
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 200
    monkeypatch.delenv("JARVIS_DECISION_CHECK_SERVER")
    raw = raw_zip(tmp_path, {**SKILL, "version": "1.0.1"})
    package_signature.sign(raw, raw, agent.private, agent.public, PLAN)
    assert send(client, agent, raw.read_bytes(), session=PLAN).status_code == 409


# -- the proof on its own -----------------------------------------------------------------

def test_the_digest_is_the_same_on_the_author_s_folder_and_in_the_zip(tmp_path):
    source = tmp_path / "package"
    (source / "web").mkdir(parents=True)
    (source / "package.json").write_text('{"purpose": "x"}')
    (source / "web" / "index.html").write_text("<p>x</p>")
    (source / ".DS_Store").write_bytes(b"\0")
    archive = tmp_path / "p.zip"
    archive.write_bytes(make_zip({"index.html": "x"}, SKILL))
    assert author_proof.digest_in_zip(archive) is None
    assert author_proof.attach(archive, source) == author_proof.source_digest(source) == \
        author_proof.digest_in_zip(archive)
    (source / "link").symlink_to(source / "package.json")
    with pytest.raises(ValueError):
        author_proof.source_digest(source)


def test_an_unreadable_claim_proves_nothing(tmp_path):
    assert author_proof.Claim.of({"engine": "gpt", "session": "s", "source": "0" * 64}) is None
    assert author_proof.Claim.of({"engine": "claude", "session": "../x",
                                  "source": "0" * 64}) is None
    assert author_proof.problem({"engine": "claude"}, PLAN, tmp_path / "x.zip",
                                time.time()) == "Its author proof is not one JARVIS reads."
    assert new_session() != new_session()
    assert os.sep not in author_proof.Claim.of(
        {"engine": "codex", "session": "thread-1", "source": "0" * 64}).session
