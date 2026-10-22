"""
brain_provider.py — what a brain provider is, and which ones there are.

JARVIS has one brain (`brain.Brain`): one turn at a time, every turn reaching
a result, the untrusted-content tracking, the context budget and rotation
with the user's yes, and the crash-restart budget. None of that depends on
who runs the model. A provider is the part that does: it supplies a session
with the model, a usage reader, a history reader and setup checks, and it
turns its own wire format into the events below. `Brain` never sees the
wire, and nothing else in JARVIS knows which provider is running.

Claude Code is one provider (`claude_provider.py`, "anthropic") and OpenAI's
Codex the other (`codex_provider.py`, "openai"). `JARVIS_BRAIN_PROVIDER`
picks one, "anthropic" by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Protocol, Union

if TYPE_CHECKING:
    from brain import BrainConfig


# ── what a session reports, in the brain's own terms ──────────────────────

@dataclass(frozen=True)
class SessionInfo:
    """What the session actually started with, reported once it knows."""
    conversation_id: Optional[str]
    model: Optional[str]
    servers: list[dict] = field(default_factory=list)   # {"name", "status"} per MCP server
    tools: list[str] = field(default_factory=list)      # every tool it can call
    # Each MCP server's own tools, by their bare names (the provider knows
    # how it spells a server's tools; the brain does not).
    server_tools: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolStarted:
    name: str                       # as the provider names it


@dataclass(frozen=True)
class TurnEnded:
    context_tokens: int             # how big the window is now
    output_tokens: int
    error: Optional[str] = None     # the provider's wording, when the turn failed


@dataclass(frozen=True)
class UsageSeen:
    """The provider reported where its limits stand."""
    blocked: bool                   # it is refusing turns until `resets_at`
    resets_at: Optional[float]
    window: Optional[str]           # which limit, in the provider's words
    summary: dict                   # {"status", "utilization", "windows"}, for usage_status
    detail: dict                    # the provider's own report, kept with a block


ProviderEvent = Union[SessionInfo, TextDelta, ToolStarted, TurnEnded, UsageSeen]

EventSink = Callable[["ProviderSession", ProviderEvent], None]
ExitSink = Callable[["ProviderSession", Optional[int]], Awaitable[None]]


class ProviderSession(Protocol):
    """One live conversation with the model. Events go to the `EventSink` it
    was made with, from the moment it opens; when it ends, for any reason,
    it calls its `ExitSink` once."""

    @property
    def alive(self) -> bool: ...

    async def open(self, instructions: str) -> None:
        """Start it with these standing instructions (the launch prompt).
        Raises OSError when it cannot start at all."""
        ...

    async def send(self, text: str) -> None:
        """One user message. Raises OSError when it can no longer be sent."""
        ...

    def kill(self) -> None: ...

    async def close(self, timeout: float) -> None:
        """End it gracefully, killing it if it has not gone within `timeout`."""
        ...


# ── usage and history ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class UsageReading:
    """The account's limits, read directly rather than off a reply."""
    service: str                    # "anthropic", "openai"
    subscription: str               # "max", "pro"
    report: dict                    # in the shape usage_store.record takes


class UsageSource(Protocol):
    def read(self) -> UsageReading:
        """Blocking; raises UsageUnavailable when there is no reading."""
        ...


class UsageUnavailable(Exception):
    """No reading could be had; the reason is safe to log."""


class HistorySource(Protocol):
    """Where the brain's own earlier conversations are kept, for
    search_history and read_history."""

    suffix: str                     # a conversation file's suffix, e.g. ".jsonl"

    def folders(self) -> list[Path]: ...

    def message(self, record: dict) -> Optional[tuple[str, str]]:
        """(role, text) for one record of a conversation file, or None when
        it is not something the user or the brain said."""
        ...


# ── its account, for the Settings view's Brains section ───────────────────

@dataclass(frozen=True)
class Account:
    signed_in: bool
    plan: str                       # "max", "pro"; "" when unknown
    detail: str                     # said on the card, e.g. "Not signed in"


@dataclass(frozen=True)
class SignIn:
    url: str                        # the page opens it in a new tab
    sign_in_id: str


# ── the provider ──────────────────────────────────────────────────────────

class BrainProvider(Protocol):
    name: str                       # "anthropic" or "openai"; also picks the orb's logo
    title: str                      # "Claude", "OpenAI (Codex)"
    default_model: str              # when neither .env nor brains.json names one

    def models(self) -> list[str]: ...

    async def account(self) -> Account: ...

    async def start_sign_in(self) -> Optional[SignIn]:
        """Begin signing in from the page; None when the provider signs in
        some other way (Claude Code signs in through `claude` itself)."""
        ...

    async def sign_in_finished(self, sign_in_id: str, timeout: float) -> bool: ...

    def session(self, config: "BrainConfig", on_event: EventSink,
                on_exit: ExitSink) -> ProviderSession: ...

    def usage(self) -> UsageSource: ...

    def history(self) -> HistorySource: ...

    def preflight(self) -> tuple[tuple, tuple]:
        """(async checks, sync checks) of its own, run with preflight's."""
        ...

    def failure_kind(self, error: Optional[str]) -> Optional[str]:
        """A short cause no restart can heal ("auth"), or None."""
        ...

    def reads_foreign(self, tool: str) -> Optional[str]:
        """What a tool of its own reads from outside JARVIS, said aloud ("a
        web page"), or None when it reads nothing foreign."""
        ...


DEFAULT_PROVIDER = "anthropic"


def provider(name: str = DEFAULT_PROVIDER) -> BrainProvider:
    """The provider by name. Imported here, not at the top, so a provider's
    module can use the types above."""
    if name == "anthropic":
        from claude_provider import ClaudeProvider
        return ClaudeProvider()
    if name == "openai":
        from codex_provider import CodexProvider
        return CodexProvider()
    raise ValueError(f"no brain provider called {name!r}")
