"""Codex as a session provider (codex_sessions.py), beside Claude Code behind
one session model (session_provider.py).

A stand-in Codex home (its thread table and transcripts) and stand-in
processes: which running session is which thread, what each is doing, its
last reply, a message sent through `codex queue`, and the server routing a
read, a send and a keystroke to the session's own provider."""

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

import codex_sessions as cs
import session_steer
import session_watch as sw

NOW = 2_000_000_000.0


def _home(tmp_path, threads):
    home = tmp_path / "codex"
    home.mkdir()
    con = sqlite3.connect(home / "state_5.sqlite")
    con.execute("create table threads (id text, rollout_path text, cwd text, title text, "
                "git_branch text, created_at_ms integer, updated_at_ms integer, archived integer)")
    for t in threads:
        rollout = home / f"rollout-{t['id']}.jsonl"
        rollout.write_text("\n".join(json.dumps(x) for x in t.get("lines", [])))
        con.execute("insert into threads values (?,?,?,?,?,?,?,?)",
                    (t["id"], str(rollout), t["cwd"], t.get("title", "a title"), "main",
                     int(t.get("created", NOW - 3600) * 1000), int(t.get("updated", NOW - 60) * 1000),
                     int(t.get("archived", 0))))
    con.commit()
    con.close()
    return home


def _say(role, text, at="2033-05-18T03:33:20Z"):
    kind = "input_text" if role == "user" else "output_text"
    return {"timestamp": at, "type": "response_item",
            "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}}


def _event(kind, at="2033-05-18T03:33:20Z"):
    return {"timestamp": at, "type": "event_msg", "payload": {"type": kind}}


def _tool(name):
    return {"type": "response_item", "payload": {"type": "function_call", "name": name}}


T1 = "01a0ca40-0ae3-7452-893a-87da26c01453"
T2 = "01a0cbd5-3ef1-78e0-9bc7-a4b8d931c8f6"
T3 = "01a0d640-8412-7b63-8983-0754b565ecaa"


def _procs(*procs):
    return lambda: list(procs)


def test_running_sessions_are_matched_to_their_threads_and_described(tmp_path):
    home = _home(tmp_path, [
        {"id": T1, "cwd": "/w/agent-guard", "title": "Approval authority\nmore",
         "lines": [_say("user", "<environment_context>x</environment_context>"),
                   _say("user", "fix the gate"), _event("task_started"), _tool("exec"),
                   _say("assistant", "Done, the gate is fixed."), _event("task_complete")]},
        {"id": T2, "cwd": "/w/proteus", "created": NOW - 30,
         "lines": [_say("user", "build it"), _event("task_started"), _tool("exec")]},
        {"id": T3, "cwd": "/w/proteus", "created": NOW - 7200, "updated": NOW - 7000,
         "lines": [_say("user", "old"), _say("assistant", "Shall I go on?"), _event("task_complete")]},
    ])
    provider = cs.CodexSessions(home=home, processes=_procs(
        cs.CodexProcess(11, NOW - 600, ["/x/codex", "resume", T1], "/w/agent-guard"),
        cs.CodexProcess(12, NOW - 500, ["/x/codex", "resume", T1, "--no-alt-screen"], "/w/agent-guard"),
        cs.CodexProcess(13, NOW - 40, ["/x/codex"], "/w/proteus"),            # a new session
        cs.CodexProcess(14, NOW - 9000, ["/x/codex", "resume", T3], "/w/proteus"),
    ))
    by = {s.session_id: s for s in provider.discover(NOW)}
    assert set(by) == {T1, T2, T3}
    one = by[T1]
    assert (one.provider, one.project, one.state, one.title) == ("openai", "agent-guard", "idle", "Approval authority")
    assert sorted(one.pids) == [11, 12], "two terminals on one thread are one conversation"
    assert (one.last_prompt, one.last_text, one.recent_tools) == ("fix the gate", "Done, the gate is fixed.", ["exec"])
    assert one.steerable is True
    assert by[T2].state == "working", "the new session is matched by folder and start time"
    assert by[T3].state == "needs_you", "idle on a question needs him, as for Claude"


def test_a_session_with_no_transcript_yet_is_fresh_and_an_archived_thread_is_not_listed(tmp_path):
    home = _home(tmp_path, [{"id": T1, "cwd": "/w/a", "lines": []},
                            {"id": T2, "cwd": "/w/b", "archived": 1}])
    provider = cs.CodexSessions(home=home, processes=_procs(
        cs.CodexProcess(1, NOW - 10, ["/x/codex", "resume", T1], "/w/a"),
        cs.CodexProcess(2, NOW - 10, ["/x/codex", "resume", T2], "/w/b")))
    states = {s.session_id: s.state for s in provider.discover(NOW)}
    assert states == {T1: "fresh"}


def test_the_last_reply_is_the_last_thing_the_session_said(tmp_path):
    home = _home(tmp_path, [{"id": T1, "cwd": "/w/a", "lines": [
        _say("assistant", "first"), _say("user", "and?"), _say("assistant", "The whole answer."),
        _event("task_complete")]}])
    provider = cs.CodexSessions(home=home, processes=_procs())
    session = sw.SessionState(session_id=T1, cwd="/w/a", project="a", state="idle", provider="openai")
    assert provider.last_reply(session) == "The whole answer."


def test_a_message_goes_through_codex_s_own_queue(tmp_path):
    log = tmp_path / "queue.log"
    fake = tmp_path / "fake_codex_queue.py"
    fake.write_text("import json, os, sys\n"
                    f"open({str(log)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], "
                    "'home': os.environ.get('CODEX_HOME')}) + '\\n')\n")
    home = _home(tmp_path, [{"id": T1, "cwd": "/w/a"}])
    provider = cs.CodexSessions(home=home, processes=_procs(), codex=f"{sys.executable} {fake}")
    session = sw.SessionState(session_id=T1, cwd="/w/a", project="a", state="idle", provider="openai")
    assert provider.send(session, "  [From JARVIS] use Postgres  ") == session_steer.SENT
    sent = json.loads(log.read_text())
    assert sent == {"argv": ["queue", "--thread", T1, "--message", "[From JARVIS] use Postgres"],
                    "home": str(home)}
    assert provider.send(session, "   ") == session_steer.REFUSED


def test_both_providers_merge_into_one_list_with_names_across_them(tmp_path):
    class Claude:
        name, can_answer_dialogs = "anthropic", True

        def discover(self, now):
            return [sw.SessionState(session_id="c1", cwd="/w/proteus", project="proteus",
                                    state="working", steerable=True, since=now - 5, started=now - 100)]

    home = _home(tmp_path, [{"id": T2, "cwd": "/w/proteus",
                             "lines": [_say("user", "x"), _say("assistant", "y"), _event("task_complete")]}])
    codex = cs.CodexSessions(home=home, processes=_procs(
        cs.CodexProcess(5, NOW - 50, ["/x/codex", "resume", T2], "/w/proteus")))
    snap = sw.build_snapshot(now=NOW, providers=[Claude(), codex])
    by = {s.session_id: s for s in snap.sessions}
    assert {by["c1"].provider, by[T2].provider} == {"anthropic", "openai"}
    assert by["c1"].voice_name != by[T2].voice_name, "two sessions in one project get two names"


def test_one_provider_that_cannot_be_read_does_not_hide_the_others(tmp_path):
    class Broken:
        name, can_answer_dialogs = "openai", False

        def discover(self, now):
            raise sqlite3.OperationalError("locked")

    class Claude:
        name, can_answer_dialogs = "anthropic", True

        def discover(self, now):
            return [sw.SessionState(session_id="c1", cwd="/w/a", project="a", state="idle")]

    snap = sw.build_snapshot(now=NOW, providers=[Broken(), Claude()])
    assert [s.session_id for s in snap.sessions] == ["c1"]


def test_ps_elapsed_time_is_read_in_every_shape():
    assert cs._etime_seconds("05:09") == 309
    assert cs._etime_seconds("01:02:03") == 3723
    assert cs._etime_seconds("3-00:00:10") == 3 * 86400 + 10


# --- the server routes to the session's own provider ------------------------

@pytest.fixture
def server(monkeypatch):
    import run_store
    import server as server_module
    run_store.init_db()                     # the audit trail every action writes to
    return server_module


class _Provider:
    def __init__(self, name, dialogs):
        self.name, self.can_answer_dialogs = name, dialogs
        self.sent, self.read = [], []

    def discover(self, now):
        return []

    def last_reply(self, session):
        self.read.append(session.session_id)
        return f"reply from {self.name}"

    def send(self, session, text, interrupt=False):
        self.sent.append((session.session_id, text))
        return session_steer.SENT


class _Watcher:
    def __init__(self, providers):
        self.providers = providers
        self.roots = None


def test_a_codex_session_s_reply_is_read_by_codex(server, monkeypatch):
    codex, claude = _Provider("openai", False), _Provider("anthropic", True)
    monkeypatch.setattr(server, "session_watcher", _Watcher([claude, codex]))
    session = sw.SessionState(session_id=T1, cwd="/w/a", project="a", state="idle", provider="openai")
    assert server._session_reply(session) == "reply from openai"
    assert codex.read == [T1] and claude.read == []


def test_a_message_to_a_codex_session_is_sent_by_codex(server, monkeypatch):
    codex, claude = _Provider("openai", False), _Provider("anthropic", True)
    monkeypatch.setattr(server, "session_watcher", _Watcher([claude, codex]))
    item = server._StagedSteer(session_id=T1, voice_name="proteus", project="proteus",
                               prompt="use Postgres", socket_path=None, provider="openai")
    assert server._deliver(item, "use Postgres") == session_steer.SENT
    assert codex.sent == [(T1, "use Postgres")] and claude.sent == []


@pytest.mark.asyncio
async def test_a_keystroke_into_a_codex_session_is_refused_with_the_reason(server, monkeypatch):
    codex = _Provider("openai", False)
    monkeypatch.setattr(server, "session_watcher", _Watcher([_Provider("anthropic", True), codex]))
    session = sw.SessionState(session_id=T1, cwd="/w/a", project="proteus", state="needs_you",
                              voice_name="proteus", provider="openai")
    monkeypatch.setattr(server, "_resolve_or_explain", lambda n: (session, None, None))
    out = await server.tool_answer_dialog({"name": "proteus", "key": "1"})
    assert "Codex session" in out and "own window" in out


def test_the_brain_s_session_list_says_which_sessions_are_codex(server):
    codex = sw.SessionState(session_id=T1, cwd="/w/a", project="proteus", state="idle",
                            voice_name="proteus", provider="openai", steerable=True, since=NOW - 60)
    claude = sw.SessionState(session_id="c1", cwd="/w/b", project="hammer", state="idle",
                             voice_name="hammer", steerable=True, since=NOW - 60)
    import agent_directory
    lines = {a.name: server._agent_line(a, s, NOW)
             for a, s in zip([agent_directory.Agent(s.session_id, s.voice_name, s.project,
                                                    s.provider, s.state, False, 0)
                              for s in (codex, claude)], (codex, claude))}
    assert lines["proteus"].endswith("a Codex session.")
    assert "Codex" not in lines["hammer"]
