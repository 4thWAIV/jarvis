"""Ingest once records and notes are items (UDS round 3): a pass makes no
chunks, retires what earlier ingest made once, hands the engine his index
choices and runs the linker; records are found by their own words, as they
are, within a bound; the pass scheduler; the real children."""
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
from jarvis.knowledge.uds import Caller, EngineUDS, Query, Transaction

TIM = Caller("tim")


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)


@pytest.fixture
def engine(tmp_path):
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


def uds_of(engine) -> EngineUDS:
    return EngineUDS(kc.InProcessTransport(engine))


def index_brain(engine) -> None:
    """What the content child does with the brain's notes: every note under
    the brain root noted and read into its item."""
    from jarvis import knowledge_content as kcont
    root = next(r for r in kcont.jarvis_roots() if r.prefix == kcont.BRAIN)
    ix = kcont.Indexer(uds_of(engine))
    for rel in ix.scan(root):
        ix.extract(root, rel)


def only(*adapters):
    return [a() if isinstance(a, type) else a for a in adapters]


def found_by_words(engine, words, caller_=TIM):
    return [h.path for h in uds_of(engine).search(caller_, Query(words=words, limit=50))]


# --- a pass ---------------------------------------------------------------------

def test_a_pass_makes_no_chunks_and_retires_earlier_ones_once(engine):
    engine.transaction({"puts": [{"_key": "doc:old", "_kind": "document", "_label": "old",
                                  "_source": "jref:1:n:note:memory/old.md"},
                                 {"_key": "chunk:old", "_kind": "chunk", "text": "an old chunk of zebra",
                                  "document": "doc:old"}],
                        "watermarks": {"conversation": {"after": 9, "schema": 2}}})
    first = ki.run_pass(caller(engine), adapters=[], link=False)
    assert not first.errors and first.forgotten == 2 and first.chunks == 0
    assert engine.docs.find(kinds_=["chunk", "document"], limit=10) == []
    marks = engine.watermarks()
    assert marks[ki.RETIRED]["schema"] == ki.SCHEMA and marks["conversation"] == {"retired": True}
    again = ki.run_pass(caller(engine), adapters=[], link=False)
    assert again.forgotten == 0 and not again.errors


def test_the_retirement_is_kept_across_a_restart(tmp_path):
    e = Engine.open(tmp_path / "e")
    ki.run_pass(caller(e), adapters=[], link=False)
    mark = e.watermarks()
    e.close()
    e = Engine.open(tmp_path / "e")
    try:
        assert e.watermarks() == mark
        e.transaction({"puts": [{"_key": "chunk:late", "_kind": "chunk", "text": "late"}]})
        assert ki.run_pass(caller(e), adapters=[], link=False).forgotten == 0
    finally:
        e.close()


def test_a_pass_hands_his_index_choices_and_runs_the_linker(engine):
    from jarvis import knowledge_links as kl
    kl.create_topic("Denice", status="planned", pinned=True)
    kl.choose_index("document|language,media_type", "pin")
    note = data_paths.brain_home() / "memory" / "orbs.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Orbs\nThe orbs sit around Denice.\n\n## More\nDenice again.\n")
    index_brain(engine)
    stats = ki.run_pass(caller(engine))
    assert not stats.errors and stats.linked == 1
    assert engine.advisor.pinned == {"document|language,media_type"}
    assert engine.linker.last["topics"] == 1


def test_one_failing_source_leaves_the_rest(engine):
    class Broken(ki.Adapter):
        name = "broken"

        def run(self, call, mark, budget):
            raise RuntimeError("no")

    class Fine(ki.Adapter):
        name = "fine"
        ran = False

        def run(self, call, mark, budget):
            Fine.ran = True
            return False
    stats = ki.run_pass(caller(engine), adapters=[Broken(), Fine()])
    assert stats.errors == ["broken: RuntimeError: no"] and Fine.ran


def test_a_spent_budget_leaves_more(engine):
    class Slow(ki.Adapter):
        name = "slow"

        def run(self, call, mark, budget):
            budget.spend(10)
            return False
    clock = iter([0.0] + [100.0] * 100)
    tight = ki.Budget(seconds=1, clock=lambda: next(clock))
    assert ki.run_pass(caller(engine), budget=tight, adapters=[Slow()], link=False).more


# --- records are found by their own words ------------------------------------------

def test_records_are_found_by_their_own_words_as_they_are(engine):
    uds = uds_of(engine)
    tx = Transaction()
    tx.put("/conversation/2026-11-10/1.turn", {"kind": "heard", "text": "what about the zebra roadmap"})
    tx.put("/agents/s1/messages/2.message", {"kind": "from_agent", "text": "zebra agent words"})
    tx.put("/runs/r1.run", {"prompt": "build the quokka checker", "status": "succeeded",
                            "result_text": "the start " + "filler words here " * 20000 + "wombat at the end"})
    uds.transaction(TIM, tx)
    assert sorted(found_by_words(engine, "zebra")) == ["/agents/s1/messages/2.message",
                                                     "/conversation/2026-11-10/1.turn"]
    assert found_by_words(engine, "quokka") == ["/runs/r1.run"]
    assert found_by_words(engine, "wombat") == []          # past the record words' bound
    hits = uds.search(TIM, Query(phrase="zebra agent words"))
    assert [h.path for h in hits] == ["/agents/s1/messages/2.message"]
    uds.transaction(TIM, Transaction().merge("/conversation/2026-11-10/1.turn", {"text": "about giraffes"}))
    assert found_by_words(engine, "zebra") == ["/agents/s1/messages/2.message"]
    assert found_by_words(engine, "giraffes") == ["/conversation/2026-11-10/1.turn"]


def test_a_hidden_field_finds_nothing_for_whom_it_is_hidden(engine):
    uds = uds_of(engine)
    uds.put(TIM, "/people/p1/messages/1.message", data={"text": "public okapi words",
                                                       "secret": "private narwhal words"})
    uds.set_rights(TIM, "/people", [{"person": "ana", "data": True, "content": True, "write": False,
                                     "hidden": ["secret"]}])
    assert found_by_words(engine, "narwhal") == ["/people/p1/messages/1.message"]
    assert found_by_words(engine, "narwhal", Caller("ana")) == []
    assert found_by_words(engine, "okapi", Caller("ana")) == ["/people/p1/messages/1.message"]
    assert uds.search(Caller("ana"), Query(phrase="private narwhal")) == []


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

    def wait(self, timeout=None):
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

def _engine_process():
    from jarvis.knowledge import address as ea
    repo = Path(__file__).resolve().parents[1]
    engine = subprocess.Popen([sys.executable, "-m", "jarvis.knowledge", "serve"],
                              stdin=subprocess.PIPE, cwd=repo)
    end = time.monotonic() + 20
    while (ea.running() is None or ea.running().pid != engine.pid) and time.monotonic() < end:
        time.sleep(0.05)
    return engine, ea.running(), repo


@pytest.mark.knowledge_engine
def test_a_real_pass_in_its_child_against_the_real_engine():
    engine, found, repo = _engine_process()
    try:
        uds = EngineUDS(kc.HTTPTransport(lambda: engine.pid))
        uds.put(TIM, "/conversation/2026-11-10/7.turn", data={"kind": "heard",
                                                              "text": "the lynx line for the child pass"})
        child = subprocess.run([sys.executable, "-m", "jarvis.knowledge_ingest"], cwd=repo,
                               input=json.dumps({"port": found.port,
                                                 "token": data_paths.ensure_knowledge_token()}) + "\n",
                               capture_output=True, text=True, timeout=60)
        stats = json.loads(child.stdout.strip().splitlines()[-1])
        assert not stats["errors"] and stats["chunks"] == 0, child.stderr[-2000:]
        assert [h.path for h in uds.search(TIM, Query(words="lynx"))] == ["/conversation/2026-11-10/7.turn"]
    finally:
        engine.stdin.close()
        engine.wait(timeout=20)


@pytest.mark.knowledge_engine
def test_the_content_child_indexes_jarvis_s_own_files_against_the_real_engine():
    from jarvis import run_store
    from jarvis.knowledge.uds import Caller, EngineUDS, Stream
    run_store.init_db()
    note = data_paths.answers_dir() / "jarvis" / "lynx.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Lynx\nThe lynx note for the content child.\n")
    memo = data_paths.brain_home() / "memory" / "lynx.md"
    memo.parent.mkdir(parents=True, exist_ok=True)
    memo.write_text("# Lynx memory\nWhat the brain keeps about the lynx.\n")
    (data_paths.brain_home() / "CLAUDE.md").write_text("# Persona\nNot a note.\n")
    engine, found, repo = _engine_process()
    child = subprocess.Popen([sys.executable, "-m", "jarvis.knowledge_content"], cwd=repo,
                             stdin=subprocess.PIPE, text=True)
    try:
        child.stdin.write(json.dumps({"pid": engine.pid, "token": data_paths.ensure_knowledge_token()}) + "\n")
        child.stdin.flush()
        uds = EngineUDS(kc.HTTPTransport(lambda: engine.pid))
        end = time.monotonic() + 90
        item = None
        while time.monotonic() < end:
            item = uds.get(Caller("tim"), "/jarvis/answers/jarvis/lynx.md", {Stream.DATA, Stream.SECTIONS})
            if item is not None and item.data.get("state") == "indexed":
                break
            time.sleep(0.1)
        assert item is not None and item.data["state"] == "indexed", item
        assert item.sections[0].heading == "Lynx" and item.data["trust"] == "own"
        while time.monotonic() < end:
            item = uds.get(Caller("tim"), "/jarvis/brain/memory/lynx.md", {Stream.DATA, Stream.CONTENT})
            if item is not None and item.data.get("state") == "indexed":
                break
            time.sleep(0.1)
        assert item is not None and item.data["state"] == "indexed", item
        assert item.content["ref"].endswith(":note:memory/lynx.md")
        assert uds.get(Caller("tim"), "/jarvis/brain/CLAUDE.md") is None     # the brain's home is not a note
    finally:
        child.stdin.close()
        child.wait(timeout=30)
        engine.stdin.close()
        engine.wait(timeout=20)


# --- the content child's scheduler ------------------------------------------------

def test_the_content_child_is_handed_its_engine_and_told_the_pace(monkeypatch):
    from jarvis.knowledge import address as ea
    ea.record(ea.EngineAddress("http", "127.0.0.1", 4100, 777, "b"))
    procs = []
    clock = [0.0]

    def spawn():
        procs.append(FakeProc())
        return procs[-1]
    sup = Sup(777)
    s = kr.ContentScheduler(sup, spawn=spawn, clock=lambda: clock[0], enabled=lambda: True)
    s("hold")
    assert len(procs) == 1 and procs[0].signals == []          # never stopped whole: told
    lines = procs[0].stdin.getvalue().decode().splitlines()
    assert json.loads(lines[0])["pid"] == 777 and lines[1:] == ["hold"]
    s("hold")
    s("free")
    assert procs[0].stdin.getvalue().decode().splitlines()[1:] == ["hold", "free"]
    procs[0].returncode = 1                                   # it ended: started again after a pause
    s("free")
    assert len(procs) == 1 and s.proc is None
    clock[0] += kr.CONTENT_RESTART_SEC
    s("free")
    assert len(procs) == 2
    sup._pid = 778                                            # a new engine: a new child for it
    ea.record(ea.EngineAddress("http", "127.0.0.1", 4101, 778, "c"))
    s("free")
    assert len(procs) == 3 and s.engine_pid == 778
    s.stop()
    assert s.proc is None and not kr.content_pid_path().exists()
