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
import hashlib
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
                    package_signature, records, work_store)
from jarvis import decision_package as dp
from tests import architecture_author

pytestmark = pytest.mark.ssh_keygen

ROOT = Path(__file__).resolve().parent.parent
SESSION = "sess-architect-e2e"
# JARVIS_ARCHITECTURE_REAL_AUTHOR=claude (or 1) or codex runs it once with that
# real fresh author (this Mac's claude or codex and its login) in place of the
# stand-in, through the factory's own path for that engine.
_REAL = os.getenv("JARVIS_ARCHITECTURE_REAL_AUTHOR", "")
ENGINE = "codex" if _REAL == "codex" else "claude"
REAL_AUTHOR = _REAL in ("1", "claude", "codex")
# What the author is asked to write besides its explanation: a walk-through
# with one question for the reader (the stand-in copies this one).
STORY = {"title": "The architecture inbox", "questions": [
    {"id": "hold-after-sign-off", "question": "Should the inbox keep a package after sign-off?",
     "options": [{"id": "keep", "name": "Keep it", "says": "It stays readable in History."},
                 {"id": "drop", "name": "Drop it", "says": "It goes once signed off."}],
     "recommended": "keep", "reason": "A signed-off package is still evidence."}],
    "slides": [{"title": "What it holds",
                "beats": [{"say": "The inbox holds each architecture package until it is signed off."}]},
               {"title": "After sign-off", "waits": "hold-after-sign-off",
                "beats": [{"say": "Should it keep a package after sign-off?"}]}]}

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
    codex_home = {"JARVIS_CODEX_SESSIONS_HOME": str(tmp_path / "codex-home")}
    if REAL_AUTHOR:
        # The real author, on this Mac's own login: its transcript is in
        # ~/.claude or ~/.codex, where both JARVIS processes look; the
        # agent's key is in ~/.claude, under a session of this run's own,
        # removed after.
        session = f"sess-architect-e2e-{uuid.uuid4().hex[:8]}"
        home, extra = Path.home() / ".claude", {}
        env.pop("CLAUDE_CONFIG_DIR", None)
        if ENGINE == "codex":
            # The real Codex author's rollout is in this Mac's own Codex home,
            # which the suite otherwise points at a folder of each test's own.
            codex_home = {"JARVIS_CODEX_SESSIONS_HOME": str(Path.home() / ".codex")}
            env.update(codex_home)
    else:
        session, home = SESSION, tmp_path / "agent-home"
        extra = {"JARVIS_CLAUDE_CONFIG_DIRS": str(transcripts)}
        story = tmp_path / "authored-story.json"
        story.write_text(json.dumps(STORY))
        env.update({"CLAUDE_CONFIG_DIR": str(home), "AUTHOR_TEST_CLAUDE_ROOT": str(transcripts),
                    "AUTHOR_TEST_STORY": str(story),
                    "JARVIS_CLAUDE_PATH": str(architecture_author.install(
                        tmp_path / "bin", f"author-{uuid.uuid4().hex[:12]}")),
                    **extra})
    keys = agent_keys.key_folder(session, home)
    private, public = agent_keys.make_pair(keys, f"jarvis-agent {session}")
    made = {}

    def before(data: Path) -> None:
        # Its records (the Work item, the agent's key) are written into its
        # engine's folder before its server and engine start there.
        with decision_check._data_dir(data), records.opened_at(data_paths.knowledge_dir()):
            made["work"] = work_store.create("Architecture inbox", "folder", str(tmp_path))
        decision_check.seed(data, public, session)

    jarvis = decision_check.Isolated(folder, "en", page, before, is_the_check=False, extra_env={
        **extra, **codex_home})
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
        "questions": ["What holds an architecture package?",
                      "Walk Tim through the page in a story, and ask him in it, as a declared "
                      "question, whether the inbox should keep a package after sign-off."],
        "evidence": [str(source)]}))
    out = tmp_path / "out"
    made = agent(env, "jarvis.make_package", "--document", checked["document_id"],
                 "--revision", checked["revision"], "--handoff", str(handoff),
                 "--engine", ENGINE, "--session", session, "--work", work.id,
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
    assert frame["package"]["purpose"] == "Explain the proposed architecture inbox", \
        "the package states the handoff's purpose"

    # The author's story, composed once with the vote, asks its questions in
    # the head; answers chosen here (this JARVIS only, never Tim's) return by
    # the existing path, with nothing edited, closed or consented for him.
    author_story = next((out / "author").glob("*/package/story.json"), None)
    assert author_story is not None, "the handoff asked the author for a walk-through"
    answered, captures = {}, ""
    if author_story is not None:
        folder = jarvis.package_folder(record["package"])
        canonical = json.loads((folder / "canonical.json").read_text())
        assert canonical["understanding"]["story_sha256"] == hashlib.sha256(
            author_story.read_bytes()).hexdigest()
        with decision_check._data_dir(data):
            from jarvis import work_packages
            package = work_packages.find(record["package"])
            told = package.story()
            vote = package_inbox.page_vote(package)
        assert [s.waits for s in told.slides].count("understood") == 1
        assert told.slides[-1].waits == "understood", "the standard vote, once, last"
        assert vote["question"] == "understood"
        for asked in [q for q in vote["asks"] if q["applies"]][:1]:
            ok, said = jarvis.tool("answer_package_decision", package=record["inbox"],
                                   decision=asked["id"], option=asked["options"][0]["id"],
                                   words="Isolated test answer.")
            assert ok and said.startswith("Recorded."), said
            answered[asked["id"]] = asked["options"][0]["id"]
        ok, said = jarvis.tool("sign_off_package", package=record["inbox"])
        assert ok, said
        import urllib.request
        with urllib.request.urlopen(urllib.request.Request(
                f"{jarvis.base}/api/work/{work.id}",
                headers={"Authorization": f"Bearer {jarvis.token}"}), timeout=30) as response:
            item = json.loads(response.read())
        item = item.get("item", item)
        with decision_check._data_dir(data):
            entry = package_inbox.get(record["inbox"])
        assert item["state"] != "done" and not item.get("closed_at"), \
            "his review is not taken for done, and the Work item stays open"
        assert entry.state == package_inbox.CLOSED
        # The answers stay in the package's own record, keyed by question,
        # the vote unanswered: signed off early, nothing is taken as his.
        recorded = json.loads((folder / "results" / "decisions.json").read_text())
        assert {k: recorded[k]["answer"] for k in answered} == answered
        assert "understood" not in recorded
        ok, captures = jarvis.tool("list_captures")
        assert ok, captures
        if entry.capture_id:
            # A running sender: the answers wait as a capture, read back and
            # sent only on his yes (the in-process tests check its words).
            mine = next(line for line in captures.splitlines()
                        if line.startswith(f"Capture {entry.capture_id} "))
            for key, option in answered.items():
                assert f"{key}: {option}" in mine, mine
            assert "understood: not answered" in mine
            assert "approved by him" not in mine and "staged to send" not in mine, mine
        else:
            # The sending session is not running here: nothing is sent.
            assert "not running" in said and captures.startswith("No capture boxes"), said
        regrounded = agent(env, "jarvis.language_document", "--as", session, "ground",
                           "--json", json.dumps({"package_id": record["package"]}))
        assert json.loads(regrounded.stdout)["result"]["revision"] == checked["revision"], \
            "no architecture edit: the canonical revision is unchanged"

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

    # The evidence, kept where a review asks for it (JARVIS_LANGUAGE_REVIEW_DIR):
    # the handoff, the author's own folder (receipt, source, its check's
    # output), the signed zip and what was observed.
    keep = Path(os.getenv("JARVIS_LANGUAGE_REVIEW_DIR") or tmp_path) / f"isolated-{ENGINE}"
    keep.mkdir(parents=True, exist_ok=True)
    shutil.copytree(out / "author", keep / "author", dirs_exist_ok=True)
    shutil.copy(handoff, keep / "handoff.json")
    shutil.copy(record["zip"], keep / Path(record["zip"]).name)
    receipt = json.loads(next((out / "author").glob("*/author-receipt.json")).read_text())
    with zipfile.ZipFile(record["zip"]) as z:
        claim = json.loads(z.read(next(n for n in z.namelist()
                                       if n.endswith(package_signature.FILE))))["author"]
    (keep / "evidence.json").write_text(json.dumps({
        "engine": ENGINE, "real_author": REAL_AUTHOR,
        "delivery": record, "grounded": {k: result[k] for k in (
            "document_id", "revision", "source_revision")},
        "opened": frame["package"]["id"], "purpose": frame["package"]["purpose"],
        "understanding": result["understanding"], "author": receipt, "author_claim": claim,
        "admission": {"inbox": record["inbox"], "from_session": entry.from_session,
                      "unproven_claim": entry.unproven_claim, "held_after": "none"},
        "answered_in_isolation": answered, "captures_after_sign_off": captures,
        "held_forgery": held.what}, indent=2))
