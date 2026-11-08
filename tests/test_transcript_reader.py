"""The Transcript tab's reader: a session's whole conversation as entries,
read back from the end a page at a time, for Claude Code and Codex alike."""

import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from jarvis import codex_sessions
from jarvis import session_steer
from jarvis import session_watch as sw
from tests.fixtures.roster import write_roster, write_transcript


def _write(path, objs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        for o in objs:
            fh.write(json.dumps(o) + "\n")


def _claude_turn(n):
    return [
        {"type": "user", "timestamp": "2026-10-25T00:00:00Z",
         "message": {"role": "user", "content": f"question {n}"}},
        {"type": "assistant", "timestamp": "2026-10-25T00:00:01Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": f"Looking at {n}."},
             {"type": "tool_use", "id": f"t{n}", "name": "Bash", "input": {"command": f"ls dir{n}"}}]}},
        {"type": "user", "timestamp": "2026-10-25T00:00:02Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": f"t{n}",
              "content": "\n".join(f"line {i}" for i in range(20))}]}},
        {"type": "assistant", "isSidechain": True,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "a subagent"}]}},
    ]


def test_a_claude_transcript_reads_as_prompts_replies_tools_and_results(tmp_path):
    path = tmp_path / "t.jsonl"
    peer = {"type": "user", "isMeta": True, "origin": {"kind": "peer"},
            "message": {"role": "user", "content": session_steer.STEER_SIGNATURE + "use Postgres"}}
    _write(path, _claude_turn(1) + [peer])
    calls = {}
    entries = sw.lines_before(path, None, 50, lambda o, b: sw._claude_entries(o, b, calls))
    assert [(e.role, e.tool) for e in entries] == [
        ("you", None), ("agent", None), ("tool", "Bash"), ("result", "Bash"), ("jarvis", None)]
    assert entries[2].text == "ls dir1"
    assert entries[3].text.splitlines() == [f"line {i}" for i in range(sw.RESULT_LINES)]
    assert "a subagent" not in [e.text for e in entries]


def test_earlier_pages_follow_on_with_nothing_lost_or_repeated(tmp_path):
    path = tmp_path / "t.jsonl"
    _write(path, [o for n in range(30) for o in _claude_turn(n)])
    pages, before = [], None
    while True:
        page = sw.lines_before(path, before, 7, sw._claude_entries, window=700)
        if not page:
            break
        pages.insert(0, page)
        before = page[0].offset
        if before == 0:
            break
    every = [e for page in pages for e in page]
    assert [e.text for e in every if e.role == "you"] == [f"question {n}" for n in range(30)]
    assert len(every) == 30 * 4


def test_a_codex_rollout_reads_the_same_way(tmp_path):
    path = tmp_path / "r.jsonl"
    item = lambda payload: {"type": "response_item", "timestamp": "2026-10-25T00:00:00Z",
                            "payload": payload}
    _write(path, [
        item({"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "rules"}]}),
        item({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "check the build"}]}),
        item({"type": "reasoning", "summary": []}),
        item({"type": "function_call", "name": "shell", "call_id": "c1",
              "arguments": json.dumps({"command": "make"})}),
        item({"type": "function_call_output", "call_id": "c1", "output": "ok\ndone"}),
        item({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Green."}]}),
    ])
    calls = {}
    entries = sw.lines_before(path, None, 50, lambda o, b: codex_sessions._codex_entries(o, b, calls))
    assert [(e.role, e.tool, e.text) for e in entries] == [
        ("you", None, "check the build"), ("tool", "shell", "make"),
        ("result", "shell", "ok\ndone"), ("agent", None, "Green.")]


def test_the_route_serves_a_session_s_page_and_its_cursor(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sw, "pid_alive", lambda pid: True)
    from jarvis import server
    importlib.reload(server)
    root = tmp_path / ".claude"
    write_roster(root, pid=os.getpid(), session_id="g", cwd="/p/guard", name="guard", status="idle")
    write_transcript(root, cwd="/p/guard", session_id="g", assistant_texts=["Hello."])
    watcher = sw.SessionWatcher(roots=[root], interval=999)
    watcher.poll_once()
    monkeypatch.setattr(server, "session_watcher", watcher)
    monkeypatch.setattr(server, "_session_provider_named",
                        lambda name: sw.ClaudeSessions([root]))
    client = TestClient(server.app)
    r = client.get("/api/sessions/g/transcript")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "guard" and body["earlier"] is None
    assert "Hello." in [e["text"] for e in body["entries"]]
    assert client.get("/api/sessions/nope/transcript").status_code == 404


def _bounded(fn, seconds=5.0):
    """fn's result, or a failure when it has not returned within `seconds`
    (the reader once looped forever on a line longer than its window)."""
    import threading
    out = []
    worker = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    worker.start()
    worker.join(seconds)
    assert out, f"lines_before did not return within {seconds}s"
    return out[0]


@pytest.mark.parametrize("long_at", ["last", "middle", "first"])
def test_a_line_longer_than_the_window_is_read_whole_and_the_read_ends(tmp_path, monkeypatch,
                                                                        long_at):
    path = tmp_path / "t.jsonl"
    short = [{"type": "assistant", "n": i} for i in range(4)]
    huge = {"type": "user", "n": "huge", "blob": "x" * 5000}
    objs = {"last": short + [huge], "middle": short[:2] + [huge] + short[2:],
            "first": [huge] + short}[long_at]
    _write(path, objs)
    reads = []
    real_open = open

    def counting_open(*args, **kwargs):
        fh = real_open(*args, **kwargs)
        real_read = fh.read

        def read(*a):
            data = real_read(*a)
            reads.append(len(data))
            return data
        fh.read = read
        return fh
    monkeypatch.setattr("builtins.open", counting_open)
    entries = _bounded(lambda: sw.lines_before(path, None, 50, lambda o, b: [b["n"]], window=500))
    assert [e for e in entries] == [o["n"] for o in objs]
    # Every line read once, give or take the windows widened to hold the
    # long one: never the same window again and again.
    assert sum(reads) < 4 * path.stat().st_size


def test_paging_back_across_a_line_longer_than_the_window_loses_nothing(tmp_path):
    path = tmp_path / "t.jsonl"
    objs = ([{"type": "assistant", "n": i} for i in range(10)]
            + [{"type": "user", "n": "huge", "blob": "y" * 3000}]
            + [{"type": "assistant", "n": i} for i in range(10, 20)])
    _write(path, objs)
    seen, before = [], None
    while True:
        page = _bounded(lambda: sw.lines_before(
            path, before, 3, lambda o, b: [sw.TranscriptEntry(o, None, "agent", str(b["n"]))],
            window=200))
        if not page:
            break
        seen = [e.text for e in page] + seen
        before = page[0].offset
        if before == 0:
            break
    assert seen == [str(o["n"]) for o in objs]
