"""Ingest (phase 4): chunking, the adapters through the engine's routes, the
watermarks, and the pass scheduler."""
from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jarvis import data_paths
from jarvis import knowledge_client as kc
from jarvis import knowledge_ingest as ki
from jarvis import knowledge_runtime as kr
from jarvis.knowledge.engine import Engine


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)


@pytest.fixture
def engine(tmp_path):
    from jarvis import run_store
    run_store.init_db()
    e = Engine.open(tmp_path / "engine")
    yield e
    e.close()


def caller(engine):
    client = kc.KnowledgeClient(kc.InProcessTransport(engine))

    def call(method, path, body):
        try:
            return client.call(method, path, body)
        except kc.KnowledgeRefused as e:
            if e.status == 404:
                raise LookupError(path)
            raise
    return call


def keys_matching(engine, words):
    return [engine.docs.key_of(o) for o, _, _ in engine.lexical.search(words, 50)]


def texts_matching(engine, words):
    return [engine.docs.by_ordinal(o)["text"] for o, _, _ in engine.lexical.search(words, 50)]


def only(*adapters):
    return [a() if isinstance(a, type) else a for a in adapters]


# --- chunking -----------------------------------------------------------------

def test_markdown_is_chunked_by_heading_within_the_cap_with_overlap():
    para = "This is one sentence about the bridge engine. " * 30            # ~1,400 characters
    text = f"# Top\nIntro line.\n\n## Plan\n{para}\n\n## Risks\nShort.\n"
    chunks = ki.chunk_text(text)
    assert [c.heading_path for c in chunks][:1] == [["Top"]]
    assert all(len(c.text) <= ki.CHUNK_MAX for c in chunks)
    plan = [c for c in chunks if c.heading_path == ["Top", "Plan"]]
    assert len(plan) >= 2
    assert plan[1].text.startswith("This is one sentence")     # the last sentence carried over
    assert chunks[-1].heading_path == ["Top", "Risks"] and "Short." in chunks[-1].text
    for c in chunks:
        assert c.text.split()[-1] in text[c.span[0]:c.span[1]]


def test_a_line_longer_than_the_cap_is_cut():
    chunks = ki.chunk_text("x" * 2500, markdown=False)
    assert all(len(c.text) <= ki.CHUNK_MAX for c in chunks) and len(chunks) == 3


# --- notes ---------------------------------------------------------------------

def _note(name, text):
    path = data_paths.brain_home() / "memory" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_notes_are_read_once_changed_ones_again_and_gone_ones_forgotten(engine):
    call = caller(engine)
    _note("denice.md", "# Denice\nThe successor direction, planned.\n")
    _note("orbs.md", "# Orbs\nThe brain orbs sit in the centre.\n")
    first = ki.run_pass(call, adapters=only(ki.Notes))
    assert first.documents == 2 and not first.errors
    assert keys_matching(engine, "successor")
    again = ki.run_pass(call, adapters=only(ki.Notes))
    assert again.documents == 0                                # nothing moved
    path = _note("denice.md", "# Denice\nNow about parris island.\n")
    os.utime(path, (time.time() + 5, time.time() + 5))
    ki.run_pass(call, adapters=only(ki.Notes))
    assert keys_matching(engine, "successor") == [] and keys_matching(engine, "parris")
    (data_paths.brain_home() / "memory" / "orbs.md").unlink()
    gone = ki.run_pass(call, adapters=only(ki.Notes))
    assert gone.forgotten == 1 and keys_matching(engine, "orbs centre") == []


def test_a_linked_note_is_never_read(engine, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("private words outside")
    link = data_paths.brain_home() / "memory" / "link.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    ki.run_pass(caller(engine), adapters=only(ki.Notes))
    assert keys_matching(engine, "private words outside") == []


# --- the conversation log ----------------------------------------------------------

def test_the_conversation_is_followed_by_id_with_trust_by_kind(engine):
    from jarvis import conversation_log as cl
    cl.append(cl.ConversationItem.new(cl.ItemKind.HEARD, "what about the zebra roadmap"))
    cl.append(cl.ConversationItem.new(cl.ItemKind.JARVIS, "the zebra roadmap has four stages"))
    cl.append(cl.ConversationItem.new(cl.ItemKind.FROM_AGENT, "zebra agent words",
                                      agent_session_id="s1", agent_name="hammer"))
    call = caller(engine)
    ki.run_pass(call, adapters=only(ki.Conversation))
    trust = {engine.docs.by_ordinal(o)["text"]: engine.docs.by_ordinal(o)["_trust"]
             for o, _, _ in engine.lexical.search("zebra", 50)}
    assert trust == {"what about the zebra roadmap": "own",
                     "the zebra roadmap has four stages": "untrusted",
                     "zebra agent words": "untrusted"}
    mark = engine.watermarks()["conversation"]["after"]
    cl.append(cl.ConversationItem.new(cl.ItemKind.TYPED, "zebra typed later"))
    ki.run_pass(call, adapters=only(ki.Conversation))
    assert len(engine.lexical.search("zebra", 50)) == 4
    assert engine.watermarks()["conversation"]["after"] > mark


# --- runs and workflows -----------------------------------------------------------

def test_a_finished_run_is_read_its_prompt_own_its_result_not(engine):
    from jarvis import run_store
    rid = run_store.create_run("build the quokka checker", "jarvis", "/tmp/x", "brain")
    ki.run_pass(caller(engine), adapters=only(ki.Runs))
    assert keys_matching(engine, "quokka") == []              # still running: not read
    run_store.update_run(rid, status="succeeded", result_text="the quokka checker is built")
    ki.run_pass(caller(engine), adapters=only(ki.Runs))
    trust = {engine.docs.by_ordinal(o)["text"]: engine.docs.by_ordinal(o)["_trust"]
             for o, _, _ in engine.lexical.search("quokka", 50)}
    assert trust == {"build the quokka checker": "own", "the quokka checker is built": "untrusted"}
    run_store.update_run(rid, result_text="rewritten: the wombat checker")
    ki.run_pass(caller(engine), adapters=only(ki.Runs))
    assert "the quokka checker is built" not in texts_matching(engine, "quokka")
    assert keys_matching(engine, "wombat")


def test_workflow_events_are_followed_by_seq(engine):
    from jarvis import workflow_store as ws
    from jarvis.workflow_store import WorkflowEvent
    wid = ws.start("Narwhal migration", "jarvis", ["one"])
    ws.record(WorkflowEvent(wid, "note", None, "The narwhal phase is half done.", {}))
    ki.run_pass(caller(engine), adapters=only(ki.Workflows))
    hits = engine.lexical.search("narwhal", 50)
    assert hits and all(engine.docs.by_ordinal(o)["_trust"] == "untrusted" for o, _, _ in hits)
    before = len(hits)
    ki.run_pass(caller(engine), adapters=only(ki.Workflows))
    assert len(engine.lexical.search("narwhal", 50)) == before


# --- registered folders ------------------------------------------------------------

def test_folder_documents_are_read_through_the_folder_service_by_the_rule(engine, tmp_path):
    from jarvis import folder_service, folder_store
    root = tmp_path / "proj"
    (root / "docs" / "deep" / "deeper").mkdir(parents=True)
    (root / "src" / "a" / "b").mkdir(parents=True)
    (root / "README.md").write_text("# Proj\nThe ocelot project readme.\n")
    (root / "docs" / "deep" / "deeper" / "plan.md").write_text("The ocelot plan in docs.\n")
    (root / "src" / "a" / "b" / "far.md").write_text("ocelot too deep to read\n")
    (root / "src" / "code.py").write_text("ocelot = 'code is not read'\n")
    (root / ".env").write_text("OCELOT_KEY=secret\n")
    service = folder_service.FolderService(folder_store.FolderStore(), data_paths.node_id)
    service.register(request_key="k1", path=str(root))
    found = ki.run_pass(caller(engine), adapters=only(ki.Folders(service)))
    assert not found.errors, found.errors
    texts = texts_matching(engine, "ocelot")
    assert sorted(texts) == ["# Proj\nThe ocelot project readme.", "The ocelot plan in docs."]


# --- the pass and its watermarks -----------------------------------------------------

def test_a_spent_budget_leaves_more_and_the_next_pass_goes_on(engine):
    for i in range(5):
        _note(f"n{i}.md", f"# N{i}\nbudget word{i}\n")
    call = caller(engine)
    clock = iter([0.0] + [0.0] * 3 + [100.0] * 1000)
    tight = ki.Budget(seconds=1, clock=lambda: next(clock))
    first = ki.run_pass(call, budget=tight, adapters=only(ki.Notes))
    assert first.more and 0 < first.documents < 5
    rest = ki.run_pass(call, adapters=only(ki.Notes))
    assert first.documents + rest.documents == 5 and not rest.more


def test_watermarks_are_kept_with_the_documents_across_a_restart(tmp_path):
    from jarvis import run_store
    run_store.init_db()
    from jarvis import conversation_log as cl
    cl.append(cl.ConversationItem.new(cl.ItemKind.HEARD, "remember the kestrel"))
    e = Engine.open(tmp_path / "e")
    ki.run_pass(caller(e), adapters=only(ki.Conversation))
    mark = e.watermarks()
    e.close()
    e = Engine.open(tmp_path / "e")
    try:
        assert e.watermarks() == mark and mark["conversation"]["after"] >= 1
        assert ki.run_pass(caller(e), adapters=only(ki.Conversation)).chunks == 0
    finally:
        e.close()


def test_one_failing_source_leaves_the_rest(engine):
    class Broken(ki.Adapter):
        name = "broken"

        def run(self, writer, mark, budget):
            raise RuntimeError("no")
    _note("x.md", "# X\nheron\n")
    stats = ki.run_pass(caller(engine), adapters=[Broken(), ki.Notes()])
    assert stats.errors and keys_matching(engine, "heron")


# --- the scheduler ----------------------------------------------------------------

class FakeProc:
    def __init__(self, out=b""):
        self.pid = 999999
        self.returncode = None
        self.signals = []
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        self.stdout = io.BytesIO(out)

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.returncode = -9

    def wait(self):
        return self.returncode


class Sup:
    def __init__(self, pid):
        self._pid = pid

    def pid(self):
        return self._pid


def test_the_scheduler_runs_when_free_stops_and_continues_with_the_pace(monkeypatch):
    from jarvis.knowledge import address as ea
    ea.record(ea.EngineAddress("http", "127.0.0.1", 4100, 777, "b"))
    procs = []
    clock = [0.0]

    def spawn():
        procs.append(FakeProc(json.dumps({"documents": 3, "chunks": 9, "more": False, "errors": []}).encode()))
        return procs[-1]
    s = kr.IngestScheduler(Sup(777), spawn=spawn, clock=lambda: clock[0], enabled=lambda: True)
    s("hold")
    assert procs == []                                   # never starts while held
    s("free")
    assert len(procs) == 1
    sent = json.loads(procs[0].stdin.getvalue())
    assert sent["port"] == 4100 and sent["token"] == data_paths.ensure_knowledge_token()
    s("hold")
    s("hold")
    s("free")
    assert procs[0].signals == [signal.SIGSTOP, signal.SIGCONT]
    procs[0].returncode = 0
    s("free")                                            # reaped; not due again yet
    assert len(procs) == 1 and s.health()["documents"] == 3 and s.health()["error"] is None
    clock[0] += kr.INGEST_EVERY_SEC
    s("free")
    assert len(procs) == 2


def test_the_scheduler_never_hands_the_token_to_an_engine_not_its_own():
    from jarvis.knowledge import address as ea
    ea.record(ea.EngineAddress("http", "127.0.0.1", 4100, 555, "b"))
    procs = []
    s = kr.IngestScheduler(Sup(777), spawn=lambda: procs.append(FakeProc()) or procs[-1],
                           enabled=lambda: True)
    s("free")
    assert procs == []


def test_a_pass_that_left_more_runs_again_soon():
    from jarvis.knowledge import address as ea
    ea.record(ea.EngineAddress("http", "127.0.0.1", 4100, 777, "b"))
    clock = [0.0]
    procs = []

    def spawn():
        procs.append(FakeProc(json.dumps({"more": True, "errors": []}).encode()))
        return procs[-1]
    s = kr.IngestScheduler(Sup(777), spawn=spawn, clock=lambda: clock[0], enabled=lambda: True)
    s("free")
    procs[0].returncode = 0
    s("free")
    clock[0] += kr.INGEST_AGAIN_SEC
    s("free")
    assert len(procs) == 2


def test_a_leftover_pass_is_ended_only_when_it_is_an_ingest_pass(tmp_path):
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        kr.ingest_pid_path().write_text(str(sleeper.pid))
        kr.end_leftover_ingest()
        assert sleeper.poll() is None                    # not an ingest pass: left alone
        assert not kr.ingest_pid_path().exists()
    finally:
        sleeper.kill()
        sleeper.wait()


# --- the real child against the real engine ------------------------------------------

@pytest.mark.knowledge_engine
def test_a_real_pass_in_its_child_against_the_real_engine():
    from jarvis import run_store
    from jarvis.knowledge import address as ea
    run_store.init_db()
    _note("lynx.md", "# Lynx\nThe lynx note for the child pass.\n")
    repo = Path(__file__).resolve().parents[1]
    engine = subprocess.Popen([sys.executable, "-m", "jarvis.knowledge", "serve"],
                              stdin=subprocess.PIPE, cwd=repo)
    try:
        end = time.monotonic() + 20
        while (ea.running() is None or ea.running().pid != engine.pid) and time.monotonic() < end:
            time.sleep(0.05)
        found = ea.running()
        child = subprocess.run([sys.executable, "-m", "jarvis.knowledge_ingest"], cwd=repo,
                               input=json.dumps({"port": found.port,
                                                 "token": data_paths.ensure_knowledge_token()}) + "\n",
                               capture_output=True, text=True, timeout=60)
        stats = json.loads(child.stdout.strip().splitlines()[-1])
        assert stats["documents"] >= 1, child.stderr[-2000:]
        client = kc.KnowledgeClient(kc.HTTPTransport(lambda: engine.pid))
        hits = client.call("POST", "/v1/search", {"text": "lynx"})["hits"]
        assert hits and hits[0]["trust"] == "own"
    finally:
        engine.stdin.close()
        engine.wait(timeout=20)
