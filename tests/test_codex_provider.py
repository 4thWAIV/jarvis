"""OpenAI's Codex as the brain provider (codex_provider.py), driven through
the real `Brain` against a stand-in `codex` (fixtures/fake_codex.py).

It runs in JARVIS's own Codex home, never the user's ~/.codex, with no API
key in its environment; every feature Codex has on is switched off except
the kept ones; the MCP servers and the persona come from JARVIS's own files;
and it reports turns, tools, usage and failures as the brain expects.
"""

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from jarvis import brain
from jarvis import brain_provider
from jarvis import codex_provider
from jarvis import data_paths
from jarvis import history_read
from jarvis import preflight
from jarvis import usage_store

FAKE = Path(__file__).parent / "fixtures" / "fake_codex.py"


@pytest.fixture
def codex_log(tmp_path, monkeypatch):
    path = tmp_path / "codex.log"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-never-reach-codex")

    def read():
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]
    return read


def _config(tmp_path, **kw):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {
        "jarvis": {"command": "/usr/bin/python3", "args": ["jarvis_mcp.py"],
                   "env": {"JARVIS_TOOL_URL": "https://127.0.0.1:8340/internal/tool"}},
        "notion": {"command": "notion-mcp", "env": {"NOTION_TOKEN": "secret \"quoted\""}}}}))
    cfg = brain.BrainConfig(home=home, provider="openai", model="gpt-5.6-terra",
                            codex_path=f"{sys.executable} {FAKE}", mcp_config=mcp,
                            warmup_timeout=10.0, turn_timeout=10.0, max_restarts=3, **kw)
    return cfg


@pytest.mark.asyncio
async def test_it_starts_in_jarvis_s_own_codex_home_with_no_api_key(tmp_path, codex_log):
    b = brain.Brain(_config(tmp_path))
    try:
        assert await b.start() and b.ready
        (start, thread) = [e for e in codex_log() if "argv" in e or "thread" in e][:2]
        assert start["codex_home"] == str(data_paths.codex_home())
        assert start["openai_key"] is False, "the plan pays, never an API key"
        assert thread["thread"]["approvalPolicy"] == "never"
        assert thread["thread"]["sandbox"] == "read-only"
        assert thread["thread"]["model"] == "gpt-5.6-terra"
        assert "brain generation 1" in thread["thread"]["developerInstructions"]
        assert b.connected_servers == ["jarvis"] and b.failed_servers == ["broken"]
        assert b.tools_from("jarvis") == ["capture", "list_agents"]
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_every_feature_that_is_on_is_switched_off_except_the_kept_ones(tmp_path, codex_log):
    b = brain.Brain(_config(tmp_path))
    try:
        await b.start()
        argv = next(e["argv"] for e in codex_log() if "argv" in e)
        off = [argv[i + 1] for i, a in enumerate(argv) if a == "--disable"]
        assert sorted(off) == ["computer_use", "shell_tool"], argv
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_the_config_holds_jarvis_s_servers_approved_and_the_persona_file(tmp_path, codex_log):
    b = brain.Brain(_config(tmp_path))
    try:
        await b.start()
    finally:
        await b.stop()
    path = data_paths.codex_home() / "config.toml"
    import tomllib
    cfg = tomllib.loads(path.read_text())
    assert cfg["include_apply_patch_tool"] is False
    assert cfg["project_doc_fallback_filenames"] == ["CLAUDE.md"]
    # Only the persona: the brain home is its own project root, so the
    # repository's developer CLAUDE.md above it is never read as instructions.
    assert cfg["project_root_markers"] == []
    assert cfg["project_doc_max_bytes"] >= 2 * 32768
    assert cfg["mcp_servers"]["jarvis"]["default_tools_approval_mode"] == "approve"
    assert cfg["mcp_servers"]["notion"]["env"]["NOTION_TOKEN"] == 'secret "quoted"'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, "it holds the user's tokens"


@pytest.mark.asyncio
async def test_a_turn_streams_its_words_and_counts_the_window(tmp_path, codex_log):
    b = brain.Brain(_config(tmp_path))
    try:
        await b.start()
        seen = []
        r = await b.turn("use capture", on_delta=seen.append)
        assert (r.stop_reason, r.text, r.tools) == ("result", "Hello, sir.", ["mcp__jarvis__capture"])
        assert seen == ["Hello, ", "sir."]
        assert b.baseline_tokens == 1000 and b.conversation_tokens == 500
        assert b.generation_untrusted_source is None, "JARVIS's own tools are not foreign"
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_a_web_search_marks_the_turn_as_having_read_the_web(tmp_path, codex_log):
    b = brain.Brain(_config(tmp_path))
    try:
        await b.start()
        r = await b.turn("use the web")
        assert r.tools == ["web_search"]
        assert b.generation_untrusted_source == "a web page"
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_a_limit_reached_blocks_turns_and_is_recorded_as_openai(tmp_path, codex_log):
    b = brain.Brain(_config(tmp_path))
    try:
        await b.start()
        await b.turn("hit the limit")
        assert b.rate_limit and b.rate_limit["resetsAt"] == 1_900_000_000
        assert (await b.turn("again")).stop_reason == "rate_limited"
        week = usage_store.week(service="openai")
        assert week["provider"] == {"service": "openai", "subscription": "pro"}
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_an_expired_login_is_fatal_and_not_retried(tmp_path, codex_log, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_FORCE", "AUTHERROR")
    b = brain.Brain(_config(tmp_path))
    try:
        assert not await b.start()
        assert b.failed and b.failure_reason == "auth"
        assert len([e for e in codex_log() if "argv" in e]) == 1
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_an_app_server_that_ends_is_replaced(tmp_path, codex_log):
    b = brain.Brain(_config(tmp_path))
    try:
        await b.start()
        assert (await b.turn("die")).stop_reason == "died"
        for _ in range(100):
            if b.ready and b.generation == 2:
                break
            await asyncio.sleep(0.05)
        assert b.ready and b.generation == 2
        assert (await b.turn("hello")).stop_reason == "result"
    finally:
        await b.stop()


def test_it_is_the_provider_named_openai_with_its_own_default_model(tmp_path, monkeypatch):
    p = brain_provider.provider("openai")
    assert isinstance(p, codex_provider.CodexProvider) and p.name == "openai"
    monkeypatch.setenv("JARVIS_BRAIN_PROVIDER", "openai")
    monkeypatch.delenv("JARVIS_BRAIN_MODEL", raising=False)
    cfg = brain.BrainConfig.from_env(tmp_path)
    assert (cfg.provider, cfg.model) == ("openai", codex_provider.DEFAULT_MODEL)


def test_its_failures_and_foreign_reads_are_its_own():
    p = codex_provider.CodexProvider()
    assert p.failure_kind("Not logged in. Please log in again.") == "auth"
    assert p.failure_kind("the model is overloaded") is None
    assert p.reads_foreign("web_search") == "a web page"
    assert p.reads_foreign("mcp__notion__search") == "notion"
    assert p.reads_foreign("mcp__jarvis__capture") is None


def test_usage_is_read_from_the_plan(monkeypatch):
    reading = codex_provider.CodexUsage(codex_path=f"{sys.executable} {FAKE}").read()
    assert (reading.service, reading.subscription) == ("openai", "pro")
    week = reading.report["unifiedWindows"]["seven_day"]
    assert week["utilization"] == 0.12 and reading.report["status"] == "allowed"


def test_its_transcripts_are_read_for_history_and_codex_s_own_context_is_not(monkeypatch):
    monkeypatch.setenv("JARVIS_BRAIN_PROVIDER", "openai")
    folder = data_paths.codex_home() / "sessions" / "2026" / "10" / "21"
    folder.mkdir(parents=True)
    lines = [
        {"timestamp": "2026-10-21T10:00:00Z", "type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
        {"timestamp": "2026-10-21T10:00:01Z", "type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "remember the purple walrus"}]}},
        {"timestamp": "2026-10-21T10:00:02Z", "type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Noted, sir."}]}},
        {"timestamp": "2026-10-21T10:00:03Z", "type": "event_msg", "payload": {"type": "token_count"}},
    ]
    (folder / "rollout-1.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    hits = history_read.search("purple walrus")
    assert any("purple walrus" in line for line in hits.lines), hits.lines
    said = [codex_provider.CodexTranscripts().message(x) for x in lines]
    assert said == [None, ("user", "remember the purple walrus"), ("assistant", "Noted, sir."), None]


@pytest.mark.asyncio
async def test_its_setup_checks_name_the_login_command(monkeypatch):
    async def run(*args, timeout, env=None):
        assert env["CODEX_HOME"] == str(data_paths.codex_home())
        return 0, "Not logged in", ""
    monkeypatch.setattr(codex_provider.shutil, "which", lambda name: "/usr/local/bin/codex")
    monkeypatch.setattr(preflight, "_run_subprocess", run)
    check = await codex_provider.check_codex_login()
    assert check.status == preflight.STATUS_FAIL
    assert f"CODEX_HOME={data_paths.codex_home()} codex login" in check.remedy

    async def ok(*args, timeout, env=None):
        return 0, "Logged in using ChatGPT", ""
    monkeypatch.setattr(preflight, "_run_subprocess", ok)
    assert (await codex_provider.check_codex_login()).ok


def test_features_to_disable_keeps_only_the_kept_ones():
    listing = "shell_tool stable true\ncode_mode_host stable true\nmemories stable false\n"
    assert codex_provider.features_to_disable(listing) == ["shell_tool"]


def _call(name, call_id):
    return {"type": "response_item", "payload": {"type": "custom_tool_call", "name": name,
                                                 "call_id": call_id, "input": "..."}}


def _answer(call_id, output):
    return {"type": "response_item", "payload": {"type": "custom_tool_call_output",
                                                 "call_id": call_id, "output": output}}


def test_a_code_mode_call_the_host_failed_is_found_in_the_transcript():
    # As measured on 29 October 2026, Codex 0.166's host against a 0.165 brain.
    broken = ("failed to read code-mode host message: failed to decode code-mode IPC frame: "
              "unknown field `result_buffered_for_ns`")
    names = {}
    objs = [_call("exec", "a"), _answer("a", [{"type": "input_text", "text": "Script completed"}]),
            _call("exec", "b"), _answer("b", "Script running with cell ID 4\nWall time 31.0 seconds"),
            _call("exec", "c"), _answer("c", "Script failed\nTypeError: x is undefined"),
            _call("exec", "d")]
    assert codex_provider.code_mode_failures(objs, names) == []
    # The answer to "d" is read on a later look than its call.
    assert codex_provider.code_mode_failures([_answer("d", broken)], names) == [("exec", broken)]
    assert names == {}


def test_tool_failures_inside_codex_reach_the_brain(tmp_path):
    seen = []
    session = codex_provider.CodexSession(_config(tmp_path), lambda s, ev: seen.append(ev), None)
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(json.dumps(_call("exec", "a")) + "\n"
                       + json.dumps(_answer("a", "failed to read code-mode host message")) + "\n")
    session._rollout = rollout
    session._handle({"method": "item/completed", "params": {"item": {
        "type": "mcpToolCall", "server": "jarvis", "tool": "list_agents", "status": "failed",
        "error": {"message": "connection closed"}}}})
    session._handle({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    failed = [ev for ev in seen if isinstance(ev, brain_provider.ToolFailed)]
    assert failed == [brain_provider.ToolFailed("mcp__jarvis__list_agents", "connection closed"),
                      brain_provider.ToolFailed("exec", "failed to read code-mode host message")]
    # Each transcript line is read once.
    session._handle({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    assert len([ev for ev in seen if isinstance(ev, brain_provider.ToolFailed)]) == 2


def test_a_tool_the_provider_failed_is_on_the_tool_channel_s_record(tmp_path, monkeypatch):
    from jarvis import subsystem_diagnostics as sd
    calls = sd.Outcomes()
    monkeypatch.setattr(sd, "tool_calls", calls)
    b = brain.Brain(_config(tmp_path))
    session = object()
    b._session = session
    b._on_event(session, brain_provider.ToolFailed("exec", "failed to read code-mode host message"))
    assert [(ok, detail) for _, ok, detail in calls.items] == [
        (False, "exec: failed to read code-mode host message")]
