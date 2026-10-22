"""The brain is provider-neutral: `Brain` drives whatever `BrainProvider` it
is given through `brain_provider`'s events, and nothing in it is Claude's.

The proof is a stand-in provider that is not Claude in any way (no process,
no stream-json): the brain warms it up, runs turns on it, learns what it read
from outside JARVIS, honours its limits, gives up on a failure it calls
fatal, and restarts it when it ends. The Claude provider's own behaviour is
pinned by test_brain.py, which drives it through a fake `claude`.
"""

import asyncio
from typing import Optional

import pytest

import brain
import brain_provider
import claude_provider
from brain_provider import SessionInfo, TextDelta, ToolStarted, TurnEnded, UsageSeen


class StandInSession:
    """A conversation with an imaginary model: each message is answered from
    the script the provider was given."""

    def __init__(self, provider, on_event, on_exit):
        self.provider, self._on_event, self._on_exit = provider, on_event, on_exit
        self.alive = False
        self.instructions = None

    async def open(self, instructions: str) -> None:
        if self.provider.cannot_start:
            raise OSError("no such model")
        self.instructions = instructions
        self.alive = True
        self.provider.sessions.append(self)

    async def send(self, text: str) -> None:
        if not self.alive:
            raise BrokenPipeError("ended")
        events = self.provider.answer(text)
        asyncio.get_running_loop().call_soon(self._play, events)

    def _play(self, events) -> None:
        for ev in events:
            self._on_event(self, ev)

    def kill(self) -> None:
        if self.alive:
            self.alive = False
            asyncio.get_running_loop().create_task(self._on_exit(self, -9))

    async def close(self, timeout: float) -> None:
        if self.alive:
            self.alive = False
            await self._on_exit(self, 0)


class StandInProvider:
    name = "stand-in"

    def __init__(self):
        self.sessions: list[StandInSession] = []
        self.cannot_start = False
        self.warmup_error: Optional[str] = None
        self.script: list = []

    def answer(self, text: str) -> list:
        if text == brain.WARMUP_TEXT:
            return [SessionInfo("conv-1", "imaginary-1", [{"name": "jarvis", "status": "connected"}],
                                ["jarvis/capture", "look_it_up"], {"jarvis": ["capture"]}),
                    TurnEnded(1000, 1, error=self.warmup_error)]
        return list(self.script)

    def session(self, config, on_event, on_exit):
        return StandInSession(self, on_event, on_exit)

    def usage(self):
        raise NotImplementedError

    def history(self):
        raise NotImplementedError

    def preflight(self):
        return ((), ())

    def failure_kind(self, error):
        return "auth" if error and "login" in error else None

    def reads_foreign(self, tool):
        return "the internet" if tool == "look_it_up" else None


def _brain(tmp_path, provider, **kw):
    cfg = brain.BrainConfig(home=tmp_path / "home", warmup_timeout=2.0, turn_timeout=2.0,
                            max_restarts=3, **kw)
    return brain.Brain(cfg, provider)


@pytest.mark.asyncio
async def test_the_brain_runs_turns_on_a_provider_that_is_not_claude(tmp_path):
    provider = StandInProvider()
    b = _brain(tmp_path, provider)
    try:
        assert await b.start() and b.ready
        assert b.model_in_use == "imaginary-1" and b.connected_servers == ["jarvis"]
        assert b.tools_from("jarvis") == ["capture"], "the provider says whose tools are whose"
        assert "brain generation 1" in provider.sessions[0].instructions
        assert b.baseline_tokens == 1000

        provider.script = [TextDelta("Hello, "), ToolStarted("look_it_up"),
                           TextDelta("sir."), TurnEnded(1600, 12)]
        seen = []
        result = await b.turn("hi", on_delta=seen.append)
        assert (result.stop_reason, result.text, result.tools) == ("result", "Hello, sir.", ["look_it_up"])
        assert seen == ["Hello, ", "sir."] and b.conversation_tokens == 600
        assert b.generation_untrusted_source == "the internet", "the provider says what reads foreign text"
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_a_provider_s_limit_blocks_turns_until_it_resets(tmp_path):
    provider = StandInProvider()
    b = _brain(tmp_path, provider)
    states = []
    b.on_state(lambda state, info: states.append(state))
    try:
        await b.start()
        provider.script = [UsageSeen(blocked=True, resets_at=None, window="weekly",
                                     summary={"status": "rejected"}, detail={"status": "rejected"}),
                           TurnEnded(1000, 0)]
        await b.turn("anything")
        await asyncio.sleep(0)
        assert "rate_limited" in states and b.rate_limit["resetsAt"] > 0
        assert (await b.turn("again")).stop_reason == "rate_limited"
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_a_failure_the_provider_calls_fatal_is_not_retried(tmp_path):
    provider = StandInProvider()
    provider.warmup_error = "your login has expired"
    b = _brain(tmp_path, provider)
    try:
        assert not await b.start()
        assert b.failed and b.failure_reason == "auth"
        assert len(provider.sessions) == 1, "no restart can heal it"
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_a_session_that_ends_is_replaced(tmp_path):
    provider = StandInProvider()
    b = _brain(tmp_path, provider)
    try:
        await b.start()
        provider.sessions[0].kill()
        for _ in range(100):
            if b.ready and len(provider.sessions) == 2:
                break
            await asyncio.sleep(0.05)
        assert b.ready and len(provider.sessions) == 2 and b.generation == 2
    finally:
        await b.stop()


@pytest.mark.asyncio
async def test_a_provider_that_cannot_start_fails_cleanly(tmp_path):
    provider = StandInProvider()
    provider.cannot_start = True
    b = _brain(tmp_path, provider)
    assert not await b.start() and b.failed and not b.ready


def test_the_provider_is_chosen_by_name_and_claude_is_the_default(tmp_path, monkeypatch):
    assert isinstance(brain_provider.provider(), claude_provider.ClaudeProvider)
    assert isinstance(brain.Brain(brain.BrainConfig(home=tmp_path)).provider,
                      claude_provider.ClaudeProvider)
    monkeypatch.setenv("JARVIS_BRAIN_PROVIDER", "anthropic")
    assert brain.BrainConfig.from_env(tmp_path).provider == "anthropic"
    with pytest.raises(ValueError):
        brain_provider.provider("nobody")


def test_the_claude_provider_supplies_every_component():
    p = claude_provider.ClaudeProvider()
    assert p.name == "anthropic"
    assert p.failure_kind("Failed to authenticate: OAuth session expired") == "auth"
    assert p.failure_kind("overloaded") is None
    assert p.reads_foreign("WebSearch") == "a web page"
    assert p.reads_foreign("mcp__jarvis__capture") is None
    assert p.history().suffix == ".jsonl"
    async_checks, sync_checks = p.preflight()
    assert [c.__name__ for c in async_checks] == ["_check_claude_cli", "_check_claude_login"]
    assert [c.__name__ for c in sync_checks] == ["_check_anthropic_key_leftover_sync"]


def test_nothing_in_brain_py_s_code_is_claude_s():
    """The line that keeps the seam where it is: brain.py's code (its comments
    may still tell Claude's history) names no Claude command, event, flag,
    tool or naming rule."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(brain.__file__).read_text())
    for node in ast.walk(tree):                       # drop the docstrings
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            body.pop(0)
    code = ast.unparse(tree)
    for claude_only in ("stream-json", "--append-system-prompt", "rate_limit_event",
                        "content_block_delta", "WebSearch", "create_subprocess_exec",
                        "claude_provider", "claude_env", "mcp__", "tool_use"):
        assert claude_only not in code, claude_only
