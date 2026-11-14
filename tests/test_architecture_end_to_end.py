"""The whole agent path, in a JARVIS of its own: an agent writes a small
architecture YAML, validates it with its signed command, sends it with
make_package --document, and the isolated JARVIS (decision_check.Isolated,
admitting exactly as the live one does: signature and author proof) takes
it into the inbox as a message on its Work item; it opens from that message
on a connected page, and ground by package id gives back the exact revision.

Every command runs as its own process, as an agent runs it; keys are made
and checked by the real ssh-keygen; the fresh author is the stand-in claude
of tests/architecture_author.py, which runs the real check command. Needs
the pinned kit asset (JARVIS_LANGUAGE_KIT_ASSET; the language-kit workflow).
"""
import json
import os
import subprocess
import sys
import threading
import time
import shutil
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis import (agent_keys, data_paths, decision_check, language_kit as lk, package_inbox,
                    package_signature, records, settings_service, work_store)
from jarvis import decision_package as dp
from tests import architecture_author

pytestmark = pytest.mark.ssh_keygen

ROOT = Path(__file__).resolve().parent.parent
SESSION = "sess-architect-e2e"
# JARVIS_ARCHITECTURE_REAL_AUTHOR=1 runs it once with the real Claude author
# (this Mac's claude and its login) in place of the stand-in.
REAL_AUTHOR = os.getenv("JARVIS_ARCHITECTURE_REAL_AUTHOR") == "1"

SOURCE = """scope: [review]
language: arch
import: [[metalang]]
title: A package inbox for architecture
declare:
- constant:
    id: ArchitectureInbox
    is: element
    value:
      form: record
      does: Holds each architecture package an agent sends until Tim signs it off.
      reach: internal
      exactness: loose
      groups: none
      members: none
      sits: {package: jarvis, assembly: none, namespace: none}
      relates: {implements: none, answers: none, holds: none}
"""


@pytest.fixture
def world(tmp_path):
    asset = os.getenv("JARVIS_LANGUAGE_KIT_ASSET")
    if not asset:
        pytest.skip("Supply the pinned external release asset for upstream integration tests")
    folder = tmp_path / "jarvis"
    data = folder / "data"
    with decision_check._data_dir(data):
        lk.activate(lk.stage(Path(asset), lk.Release(lk.PINNED_VERSION, lk.PINNED_COMMIT,
                                                     lk.PINNED_DIGEST, ("gate", "release"))))
    page = tmp_path / "page"
    page.mkdir()
    (page / "index.html").write_text("<!doctype html><title>JARVIS</title>")
    transcripts = tmp_path / "author-claude-root"
    env = {**os.environ, "PYTHONPATH": str(ROOT), "AGENT_CWD": str(tmp_path)}
    if REAL_AUTHOR:
        # The real Claude author, on this Mac's own login: its transcript is
        # in ~/.claude, where both JARVIS processes look, so the agent's key
        # is there too, under a session of this run's own, removed after.
        session = f"sess-architect-e2e-{uuid.uuid4().hex[:8]}"
        home, saved = Path.home() / ".claude", {}
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        session, home = SESSION, tmp_path / "agent-home"
        # Saved in the isolated JARVIS's Settings before it starts: its server
        # reads the author's transcripts there, and make_package's author runs
        # the stand-in Claude saved there.
        saved = {"claude_config_dirs": [str(transcripts)],
                 "claude_path": str(architecture_author.install(
                     tmp_path / "bin", f"author-{uuid.uuid4().hex[:12]}"))}
        env.update({"CLAUDE_CONFIG_DIR": str(home), "AUTHOR_TEST_CLAUDE_ROOT": str(transcripts)})
    keys = agent_keys.key_folder(session, home)
    private, public = agent_keys.make_pair(keys, f"jarvis-agent {session}")
    made = {}

    def before(data: Path) -> None:
        # Its records (the Work item, the agent's key) are written into its
        # engine's folder before its server and engine start there.
        with decision_check._data_dir(data), records.opened_at(data_paths.knowledge_dir()):
            made["work"] = work_store.create("Architecture inbox", "folder", str(tmp_path))
            for key, value in saved.items():
                settings_service.change(key, value)
        decision_check.seed(data, public, session)

    jarvis = decision_check.Isolated(folder, "en", page, before, is_the_check=False, settings={
        "codex_sessions_home": str(tmp_path / "codex-home")})
    work = made["work"]
    try:
        env.update({"JARVIS_URL": jarvis.base, "JARVIS_DATA_DIR": str(data)})
        yield SimpleNamespace(jarvis=jarvis, work=work, env=env, data=data, session=session,
                              key=(private, public))
    finally:
        jarvis.stop()
        if REAL_AUTHOR:
            shutil.rmtree(keys, ignore_errors=True)


def agent(env, *command) -> subprocess.CompletedProcess:
    """One agent command, as the talk-to-jarvis skill gives it."""
    return subprocess.run([sys.executable, "-P", "-m", *command], env=env, cwd=env["AGENT_CWD"],
                          capture_output=True, text=True, timeout=1800)


class Page:
    """A page connected to the isolated JARVIS's voice socket, keeping
    every frame it is sent."""

    def __init__(self, base: str):
        from websockets.sync.client import connect
        self.frames: list[dict] = []
        self.socket = connect(base.replace("http", "ws") + "/ws/voice", origin=base)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            for raw in self.socket:
                self.frames.append(json.loads(raw))
        except Exception:
            pass

    def close(self):
        self.socket.close()


def test_an_agent_s_architecture_document_reaches_tim_and_grounds(world, tmp_path):
    jarvis, work, env, data, session = world.jarvis, world.work, world.env, world.data, world.session
    private, public = world.key
    shown = agent(env, "jarvis.language_document", "--as", session, "capabilities")
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["result"]["languages"]["arch"]["enabled"]

    source = tmp_path / "architecture.yaml"
    source.write_text(SOURCE)
    validated = agent(env, "jarvis.language_document", "--as", session, "validate",
                      "--source", str(source), "--json", json.dumps({
                          "work_id": work.id, "scope_revision": "inbox-v1",
                          "author": session,
                          "coverage": {"constructs": ["forms", "relations"],
                                       "declarations": ["ArchitectureInbox"], "unresolved": [],
                                       "outside_language": ["No code correspondence claim."]}}))
    assert validated.returncode == 0, validated.stdout + validated.stderr
    checked = json.loads(validated.stdout)["result"]
    assert checked["check"]["ok"] and checked["metadata"]["reported_by"] == f"agent:{session}"

    handoff = tmp_path / "handoff.json"
    handoff.write_text(json.dumps({
        "purpose": "Explain the proposed architecture inbox", "audience": "Tim",
        "questions": ["What holds an architecture package?"],
        "evidence": [str(source)]}))
    out = tmp_path / "out"
    made = agent(env, "jarvis.make_package", "--document", checked["document_id"],
                 "--revision", checked["revision"], "--handoff", str(handoff),
                 "--engine", "claude", "--session", session, "--work", work.id,
                 "--project", "architecture-e2e", "--deliver-to", jarvis.base, "--out", str(out))
    assert made.returncode == 0, made.stdout + made.stderr
    record = json.loads((out / "delivery.json").read_text())

    # Admitted: in his inbox, its sender proven, its author proven (held
    # otherwise), as a message on the document's Work item.
    with decision_check._data_dir(data):
        entry = package_inbox.get(record["inbox"])
    assert entry is not None and entry.from_session == session
    assert not entry.unproven_claim
    with decision_check._data_dir(data):
        from jarvis import agent_key_store
        assert not agent_key_store.held(), "nothing was held for its key or its author"

    # Opened from the message carrying it, on a connected page.
    page = Page(jarvis.base)
    try:
        ok, said = jarvis.tool("open_inbox_package", message=record["message"])
        assert ok, said
        deadline = time.monotonic() + 30
        while not any(f.get("type") == "package_page" for f in page.frames):
            assert time.monotonic() < deadline, page.frames
            time.sleep(0.1)
    finally:
        page.close()
    shown = [f for f in page.frames if f.get("type") == "package_page"]
    assert {f["package"]["id"] for f in shown} == {record["package"]}
    frame = shown[0]

    # Grounded by package id alone: the exact revision and its explanation.
    grounded = agent(env, "jarvis.language_document", "--as", session, "ground",
                     "--json", json.dumps({"package_id": record["package"],
                                           "inbox_id": record["inbox"]}))
    assert grounded.returncode == 0, grounded.stdout + grounded.stderr
    result = json.loads(grounded.stdout)["result"]
    assert {k: result[k] for k in ("document_id", "revision", "source_revision")} == {
        k: checked[k] for k in ("document_id", "revision", "source_revision")}
    assert result["source"]["text"] == SOURCE
    assert result["understanding"]["origin"] == "fresh-author"
    assert "ArchitectureInbox" in result["understanding"]["text"]

    # The same package with an author proof naming a session that never
    # checked it is held, not admitted: this JARVIS checks the proof.
    with zipfile.ZipFile(record["zip"]) as z:
        signed = json.loads(z.read(next(n for n in z.namelist()
                                        if n.endswith(package_signature.FILE))))
    forged = tmp_path / "forged.zip"
    package_signature.sign(Path(record["zip"]), forged, private, public, session, (), None,
                           {**signed["author"], "session": "author-that-never-ran"})
    with pytest.raises(OSError):
        dp.deliver(forged, jarvis.base, "Architecture package: forged", work=work.id,
                   signer=(private, public))
    with decision_check._data_dir(data):
        from jarvis import agent_key_store
        [held] = agent_key_store.held()
    assert held.kind == "package" and held.session_id == session

    (tmp_path / "evidence.json").write_text(json.dumps({
        "delivery": record, "grounded": {k: result[k] for k in (
            "document_id", "revision", "source_revision")},
        "opened": frame["package"]["id"], "understanding": result["understanding"],
        "author": json.loads(next((out / "author").glob("*/author-receipt.json")).read_text()),
        "held_forgery": held.what}, indent=2))
