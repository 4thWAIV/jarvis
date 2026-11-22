"""No gap in a package run is unexplained (construction-drafts.md, the last
2026-11-22 entry, approved by Tim: "fix that and improve it").

Measured on package-9a8f7c72f87b run-1: the author's session reopened for
the outline at 11:13:00 and its 36 s turn ended at 11:28. The cause was the
Mac's idle sleep (pmset: "Entering Sleep state due to 'Idle Sleep'" at
11:13:00, a maintenance wake at 11:28:06): the run's process held nothing
awake. Now it holds the Mac awake (awake.held), every wait in a step is
timed and named with the seconds the Mac slept in it (run_report.wait),
the role file tools see only the package's working files in the run folder,
and nothing waits on background vectors of what the run just wrote."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from jarvis import (awake, package_evidence as pe, package_jobs as pj, role_files, role_mcp,
                    run_report, sessions)


# ── the Mac kept awake ───────────────────────────────────────────────────

class _Held:
    def __init__(self, args, **_):
        self.args, self.ended = args, False

    def poll(self):
        return None if not self.ended else 0

    def terminate(self):
        self.ended = True

    def wait(self, timeout=None):
        return 0


def test_a_run_holds_idle_sleep_off_for_its_process_and_lets_go(monkeypatch):
    started = []
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(awake.subprocess, "Popen",
                        lambda args, **kw: started.append(_Held(args, **kw)) or started[-1])
    with awake.held() as held:
        assert held
    args = started[0].args
    assert args[0] == "/usr/bin/caffeinate" and "-i" in args and "-s" in args
    assert "-d" not in args, "the display may sleep"
    assert "-t" not in args, "never a renewing assertion: its gaps let the Mac sleep"
    assert args[args.index("-w") + 1].isdigit(), "held only while this process lives"
    assert len(started) == 1, "one assertion for the whole run, no gap"
    assert started[0].ended


def test_no_caffeinate_holds_nothing_and_the_run_goes_on(monkeypatch):
    monkeypatch.setattr(awake.shutil, "which", lambda name: None)
    with awake.held() as held:
        assert not held


def test_the_run_s_process_runs_its_job_with_the_mac_kept_awake(monkeypatch, tmp_path):
    seen = []

    class Held:
        def __enter__(self):
            seen.append("held")
            return True

        def __exit__(self, *a):
            seen.append("let go")

    from jarvis import settings_service
    monkeypatch.setattr(settings_service, "load_once", lambda: None)
    monkeypatch.setattr(awake, "held", lambda *a, **k: Held())
    monkeypatch.setattr(pj, "get", lambda job: {"log": str(tmp_path / "job.log")})
    monkeypatch.setattr(pj, "run", lambda job: seen.append(f"run {job}") or 0)
    assert pj.main(["run", "job-1"]) == 0
    assert seen == ["held", "run job-1", "let go"]


def test_the_clock_tells_the_time_the_mac_slept(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(awake.time, "time", lambda: now[0])
    up = [50.0]
    monkeypatch.setattr(awake.time, "monotonic", lambda: up[0])
    clock = awake.Clock()
    now[0] += 940.0          # 15 min 40 s by the wall clock ...
    up[0] += 37.0            # ... of which the Mac was awake 37 s
    assert clock.seconds() == 940.0 and clock.slept() == 903.0
    now[0] += 1.0
    up[0] += 0.5
    assert awake.Clock().slept() == 0.0, "drift under SLEPT_MIN_SEC is no sleep"


# ── every wait timed and named ───────────────────────────────────────────

def _doc(tmp_path: Path, rounds=None) -> dict:
    return {"id": "job-1", "package": "package-1", "run": "run-1", "work": None,
            "folder": str(tmp_path), "rounds": rounds or []}


def test_each_wait_in_a_step_is_named_with_its_seconds_and_any_sleep(tmp_path, monkeypatch):
    with run_report.wait("indexing", "outside a step"):
        pass                                      # nothing kept outside a step
    now, up = [5000.0], [10.0]
    monkeypatch.setattr(awake.time, "time", lambda: now[0])
    monkeypatch.setattr(awake.time, "monotonic", lambda: up[0])
    monkeypatch.setattr(run_report.time, "time", lambda: now[0])
    doc = _doc(tmp_path, rounds=[{"kind": "questions", "asked_at": 100.0,
                                  "answered_at": 400.0}, {"kind": "check", "asked_at": 500.0,
                                                          "answered_at": None}])
    with run_report.step(doc, "swarm"):
        with run_report.wait("session_open", "tims_reader", engine="codex"):
            now[0] += 903.0
            up[0] += 1.0                          # the Mac slept through it
        now[0] += 10.0                            # program work nothing names
        up[0] += 10.0
    body = json.loads((tmp_path / run_report.TIMING).read_text())
    step = body["steps"][0]
    wait = step["waits"][0]
    assert (wait["wait"], wait["name"], wait["engine"]) == ("session_open", "tims_reader", "codex")
    assert wait["seconds"] == 903.0 and wait["asleep"] == 902.0
    assert step["asleep"] == 902.0, "the step says the Mac slept"
    assert step["unaccounted"] == 10.0, "only what no wait, call or search covers"
    assert step["wait_seconds"] == 903.0
    assert body["totals"]["asleep"] == 902.0 and body["totals"]["waits"] == 1
    assert body["sender"] == [
        {"round": 1, "kind": "questions", "asked_at": 100.0, "answered_at": 400.0,
         "seconds": 300.0},
        {"round": 2, "kind": "check", "asked_at": 500.0, "answered_at": None, "seconds": None}]


def test_what_runs_at_once_is_counted_once():
    spans = [(0.0, 10.0), (5.0, 12.0), (20.0, 25.0)]
    assert run_report.unaccounted(0.0, 30.0, spans) == 13.0
    assert run_report.unaccounted(0.0, 30.0, []) == 30.0
    assert run_report.unaccounted(10.0, 20.0, [(0.0, 50.0)]) == 0.0


def test_calls_and_searches_cover_their_own_time(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(run_report.time, "time", lambda: now[0])
    monkeypatch.setattr(awake.time, "time", lambda: now[0])
    with run_report.step(_doc(tmp_path), "mark"):
        now[0] += 40.0
        run_report.call({"role": "marking", "seconds": 30.0, "at": now[0]})
    step = json.loads((tmp_path / run_report.TIMING).read_text())["steps"][0]
    assert step["unaccounted"] == 10.0


def test_waiting_on_the_index_is_a_named_wait_and_words_only_needs_no_vector(tmp_path,
                                                                             monkeypatch):
    class Item:
        def __init__(self, state, complete):
            self.data = {"state": state}
            self.vectors = {"state": "on", "complete": complete}

    class Uds:
        items = {"/e/files/a.md": Item("indexed", False),
                 "/e/sender-answers/1.md": Item("indexed", False),
                 "/e/mark/marked.json": Item("pending", False)}

        def get(self, writer, path, streams):
            return self.items[path]
    waiting = pe.not_indexed(list(Uds.items), Uds(), vectors=False)
    assert waiting.paths == ["/e/mark/marked.json"], "words only: no vector is waited for"
    Uds.items["/e/mark/marked.json"] = Item("indexed", False)
    monkeypatch.setattr(pe, "index_now", lambda place, uds=None: list(Uds.items))
    with run_report.step(_doc(tmp_path), "swarm"):
        assert pe.wait_indexed(object(), "swarm", Uds(), vectors=False) == list(Uds.items)
    step = json.loads((tmp_path / run_report.TIMING).read_text())["steps"][0]
    assert [(w["wait"], w["name"]) for w in step["waits"]] == [
        ("indexing", "swarm: the evidence, words only")]


def test_a_step_s_own_writing_is_read_into_the_engine_at_once(tmp_path, monkeypatch):
    from jarvis import knowledge_content
    run = tmp_path / "work" / "w1" / "packages" / "p1" / "runs" / "run-1"
    run.mkdir(parents=True)
    (run / "research.md").write_text("Finding.")
    (run / "prep.md").write_text("Prep.")
    calls = []

    class Indexer:
        def __init__(self, uds):
            self.uds = self

        def get(self, writer, path, streams):
            return None

        def note(self, root, rel, stat, held=None):
            calls.append(("note", rel))
            return True

        def extract(self, root, rel):
            calls.append(("extract", rel))
            return "indexed"
    monkeypatch.setattr(knowledge_content, "Indexer", Indexer)
    monkeypatch.setattr(knowledge_content, "item_path", lambda root, rel: f"/work/{rel}")
    monkeypatch.setattr(knowledge_content, "wanted",
                        lambda root, rel: not rel.endswith(("timing.json", ".jarvis-role/x.json")))
    monkeypatch.setattr(pe, "_root_and_rel", lambda folder: (
        "root", str(Path(folder).relative_to(tmp_path / "work"))))
    (run / "timing.json").write_text("{}")
    read = pe.index_files([run / "research.md", run / "prep.md", run / "missing.md",
                           run / "timing.json"], uds=object())
    assert read == ["/work/w1/packages/p1/runs/run-1/research.md",
                    "/work/w1/packages/p1/runs/run-1/prep.md"]
    assert calls == [("note", "w1/packages/p1/runs/run-1/research.md"),
                     ("extract", "w1/packages/p1/runs/run-1/research.md"),
                     ("note", "w1/packages/p1/runs/run-1/prep.md"),
                     ("extract", "w1/packages/p1/runs/run-1/prep.md")], \
        "what the content child's rule passes over (knowledge_content.wanted) is not read"


def test_index_files_applies_the_content_child_s_own_rule(tmp_path, monkeypatch):
    from jarvis import knowledge_content
    run = tmp_path / "work" / "w1" / "packages" / "p1" / "runs" / "run-1"
    run.mkdir(parents=True)
    (run / "job.log").write_text("log")
    root = knowledge_content.Root("/work", str(tmp_path / "work"), "text", "untrusted", shares=True)
    monkeypatch.setattr(pe, "_root_and_rel", lambda folder: (
        root, str(Path(folder).relative_to(tmp_path / "work"))))

    class Indexer:
        def __init__(self, uds):
            self.uds = self

        def get(self, *a):
            raise AssertionError("a run's bookkeeping is never read in")
    monkeypatch.setattr(knowledge_content, "Indexer", Indexer)
    assert pe.index_files([run / "job.log"], uds=object()) == []


class _Vectors:
    def __init__(self, complete):
        self.data = {"state": "indexed"}
        self.vectors = {"state": "on", "complete": complete}


def test_a_reused_gather_waits_for_vectors_its_evidence_has_lost(tmp_path, monkeypatch):
    lost = {"/e/files/a.md": _Vectors(False), "/e/files/b.md": _Vectors(True)}

    class Uds:
        def get(self, writer, path, streams):
            return lost[path]
    looks = []

    def sleep(sec):
        looks.append(sec)
        lost["/e/files/a.md"] = _Vectors(True)       # the background build made it
    monkeypatch.setattr(pe, "SLEEP", sleep)
    with run_report.step(_doc(tmp_path), "gather"):
        assert pe.wait_lost_vectors(object(), "gather", list(lost), Uds()) == ["/e/files/a.md"]
    assert looks, "it waited, as a fresh gather does"
    step = json.loads((tmp_path / run_report.TIMING).read_text())["steps"][0]
    assert [(w["wait"], w["name"], w.get("items")) for w in step["waits"]] == [
        ("indexing", "gather: the evidence's meaning, lost since it was gathered", 1)]


def test_a_reused_gather_with_every_vector_names_no_wait(tmp_path):
    class Uds:
        def get(self, writer, path, streams):
            return _Vectors(True)
    with run_report.step(_doc(tmp_path), "gather"):
        assert pe.wait_lost_vectors(object(), "gather", ["/e/files/a.md"], Uds()) == []
    step = json.loads((tmp_path / run_report.TIMING).read_text())["steps"][0]
    assert not step.get("waits")


def test_lost_vectors_never_waited_for_past_the_bound_put_the_run_in_error(tmp_path, monkeypatch):
    class Uds:
        def get(self, writer, path, streams):
            return _Vectors(False)
    now = [0.0]
    monkeypatch.setattr(pe, "CLOCK", lambda: now[0])
    monkeypatch.setattr(pe, "SLEEP", lambda sec: now.__setitem__(0, now[0] + pe.INDEX_WAIT_SEC))
    with run_report.step(_doc(tmp_path), "gather"), pytest.raises(pj.Blocked):
        pe.wait_lost_vectors(object(), "gather", ["/e/files/a.md"], Uds())


def test_the_reused_gather_step_asks_for_lost_vectors(tmp_path, monkeypatch):
    from jarvis import package_gather, step_cache
    calls = []
    monkeypatch.setattr(pe, "place_of", lambda doc: "place")
    monkeypatch.setattr(pe, "gathered", lambda place: {"gathered": ["/e/files/a.md"],
                                                       "gather_key": "k"})
    monkeypatch.setattr(step_cache, "note", lambda *a, **k: None)
    monkeypatch.setattr(pe, "wait_indexed", lambda place, step, **kw: calls.append(("words", kw)))
    monkeypatch.setattr(pe, "wait_lost_vectors",
                        lambda place, step, items: calls.append(("lost", items)))
    package_gather.gather({})
    assert calls == [("words", {"vectors": False}), ("lost", ["/e/files/a.md"])]


# ── the role file tools: the package's files, never the run's bookkeeping ─

BOOKKEEPING = ("timing.json", "quality.json", "swarm.json", "run.job", "job.log",
               "research-log.jsonl", "research-used.json")
WORKING = ("research.md", "prep.md", "outline.yaml", "script.yaml", "research-searches.md",
           "deck")


def _run_folder(tmp_path: Path) -> tuple[Path, Path, role_files.Files]:
    run, evidence = tmp_path / "runs" / "run-1", tmp_path / "evidence"
    (run / "deck").mkdir(parents=True)
    (run / "deck" / "presentation.yaml").write_text("slides: the move\n")
    for name in WORKING[:-1]:
        (run / name).write_text(f"{name}: the move\n")
    for name in BOOKKEEPING:
        (run / name).write_text(f"{name}: the move\n")
    (evidence / "files").mkdir(parents=True)
    (evidence / "files" / "notes.md").write_text("The move waits on PR 180.\n")
    (evidence / "manifest.json").write_text("{}\n")
    return run, evidence, role_files.Files([run, evidence], WORKING)


def test_the_run_folder_shows_only_the_package_s_working_files(tmp_path):
    run, evidence, files = _run_folder(tmp_path)
    ok, text = role_files.call(files, "list_package_files", {})
    assert ok and sorted(e["name"] for e in json.loads(text)["entries"]) == sorted(WORKING)
    for name in WORKING[:-1]:
        assert role_files.call(files, "read_package_file", {"path": name})[0], name
    assert role_files.call(files, "read_package_file", {"path": "deck/presentation.yaml"})[0]
    for name in BOOKKEEPING:
        for path in (name, str(run / name), f"../run-1/{name}"):
            ok, text = role_files.call(files, "read_package_file", {"path": path})
            assert not ok and "may not be read" in text, path
    ok, text = role_files.call(files, "search_package_files", {"text": "the move"})
    found = json.loads(text)["lines"]
    assert found and not any(name in line for line in found for name in BOOKKEEPING)
    ok, text = role_files.call(files, "search_package_files",
                               {"text": "the move", "path": str(run.parent.parent)})
    assert not ok, "the folder above the run is none of the role's"


def test_the_evidence_is_read_whole_beside_the_working_files(tmp_path):
    run, evidence, files = _run_folder(tmp_path)
    assert role_files.call(files, "read_package_file", {"path": "../../evidence/files/notes.md"})[0]
    ok, text = role_files.call(files, "list_package_files", {"path": str(evidence)})
    assert {e["name"] for e in json.loads(text)["entries"]} == {"files", "manifest.json"}


def test_the_role_s_server_is_started_with_the_names_it_sees(tmp_path, monkeypatch):
    run, evidence = tmp_path / "run", tmp_path / "evidence"
    args = role_mcp.entry("editor", None, (run, evidence), WORKING)["args"]
    assert [args[i + 1] for i, a in enumerate(args) if a == "--show"] == list(WORKING)
    assert "--show" not in role_mcp.entry("editor", None, (run, evidence))["args"], \
        "no names given: everything in the run folder, as before"
    monkeypatch.setattr(role_mcp.jarvis_mcp, "main", lambda handle: None)
    monkeypatch.setattr(role_mcp, "FILES", role_files.Files([]))
    role_mcp.main(["--role", "editor", "--read", str(run), "--read", str(evidence),
                   *[a for n in WORKING for a in ("--show", n)]])
    assert role_mcp.FILES.shown == frozenset(WORKING)
    assert role_mcp.FILES.folders == [run, evidence]


def test_a_role_session_hands_its_server_the_names_it_sees(tmp_path):
    from jarvis import model_tiers
    pick = model_tiers.Pick("claude", "opus", 2, "test", ["opus"])
    chat = sessions.BrainChatSession("editor", "Edit.", tmp_path, pick=pick,
                                     reads=(tmp_path / "evidence",), shown=WORKING)
    body = json.loads(chat._mcp_config().read_text())
    args = body["mcpServers"]["jarvis_role"]["args"]
    assert [args[i + 1] for i, a in enumerate(args) if a == "--show"] == list(WORKING)


# ── the swarm's participants opened at once ──────────────────────────────

class _Opening:
    def __init__(self, name, log, gate, fails=False):
        self.name, self.log, self.gate, self.fails = name, log, gate, fails

    async def open(self):
        self.log.append(f"{self.name} opening")
        await self.gate.wait()
        if self.fails:
            raise sessions.SessionFailed(f"{self.name} could not open")
        self.log.append(f"{self.name} open")

    async def close(self):
        self.log.append(f"{self.name} closed")


@pytest.mark.asyncio
async def test_every_participant_opens_at_once():
    gate, log = asyncio.Event(), []
    people = {n: _Opening(n, log, gate) for n in ("author", "tims_reader", "editor")}
    swarm = sessions.BrainSwarmSession(people, [])
    opening = asyncio.ensure_future(swarm.open())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert log == ["author opening", "tims_reader opening", "editor opening"], \
        "none waits for another to open"
    gate.set()
    await opening
    assert sorted(log[3:]) == ["author open", "editor open", "tims_reader open"]


@pytest.mark.asyncio
async def test_one_that_cannot_open_is_raised_once_all_have_tried():
    gate, log = asyncio.Event(), []
    gate.set()
    people = {"author": _Opening("author", log, gate, fails=True),
              "editor": _Opening("editor", log, gate)}
    swarm = sessions.BrainSwarmSession(people, [])
    with pytest.raises(sessions.SessionFailed, match="author could not open"):
        await swarm.open()
    assert "editor open" in log, "the others still open, to be closed by close()"
    await swarm.close()
    assert {"author closed", "editor closed"} <= set(log)


@pytest.mark.asyncio
async def test_a_session_s_opening_is_a_named_wait(tmp_path, monkeypatch):
    from jarvis import model_tiers
    pick = model_tiers.Pick("codex", "gpt-x", 1, "test", ["gpt-x"])
    chat = sessions.BrainChatSession("tims_reader", "Read.", tmp_path, pick=pick)

    async def opened(last=""):
        time.sleep(0)
    monkeypatch.setattr(chat, "_open_next", opened)
    with run_report.step(_doc(tmp_path), "swarm"):
        await chat.open()
    step = json.loads((tmp_path / run_report.TIMING).read_text())["steps"][0]
    assert [(w["wait"], w["name"], w["engine"]) for w in step["waits"]] == [
        ("session_open", "tims_reader", "codex")]
