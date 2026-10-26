"""
session_provider.py — what a provider of agent sessions is.

JARVIS watches the coding sessions running on this machine: lists them,
says what each is doing, reads their replies and sends them messages. Who
runs a session (Claude Code, OpenAI's Codex) changes only how that is done,
so each is a provider behind this interface, the way the brains are
(brain_provider.py). `session_watch.build_snapshot` merges what every
provider discovers; the server sends and reads through the session's own
provider.

Claude Code is `session_watch.ClaudeSessions`; Codex is
`codex_sessions.CodexSessions`. See
.agents/in-progress/session-providers-design.md for how each capability
maps, and Codex's limits.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Optional, Protocol

if TYPE_CHECKING:
    from jarvis.run_store import Message
    from jarvis.session_watch import SessionState, TranscriptEntry


@dataclass(frozen=True)
class Tracking:
    """Where one message stands in its session's transcript. Each time is
    the transcript's own, None until it has happened."""
    delivered_at: Optional[float] = None    # it is in the transcript
    started_at: Optional[float] = None      # the turn that took it began
    answered_at: Optional[float] = None     # that turn ended
    reply: Optional[str] = None             # that turn's final answer, as said


class SessionProvider(Protocol):
    name: str                       # "anthropic", "openai"; also picks the logo
    can_answer_dialogs: bool        # can it press a key in the session's own prompt

    def discover(self, now: float) -> list["SessionState"]:
        """Its live conversations, each a SessionState carrying this
        provider's name, its project, state, title, last prompt, last text
        and recent tools. Voice names and the primary mark are given by the
        watcher, across every provider together."""
        ...

    def last_reply(self, session: "SessionState") -> Optional[str]: ...

    def transcript(self, session: "SessionState", before: Optional[int] = None,
                   limit: int = 200) -> list["TranscriptEntry"]:
        """The session's transcript as entries (session_watch.TranscriptEntry):
        the last `limit` before byte `before`, the end when None, oldest
        first. Only reads."""
        ...

    can_interrupt: bool             # can a message stop the turn under way

    def send(self, session: "SessionState", text: str, interrupt: bool = False) -> str:
        """Deliver one message; with `interrupt`, stopping the turn under way
        where the provider can (`can_interrupt`). One of session_steer's
        outcomes."""
        ...

    def track(self, session: "SessionState", message: "Message") -> Tracking:
        """Where this message stands in the session's transcript: whether it
        has arrived, whether the turn answering it has begun and ended, and
        that turn's final answer. Found by the text after JARVIS's signature
        (`message.body`), in lines written no earlier than it was staged.
        Only reads; never raises for a transcript it cannot read (that is an
        empty Tracking)."""
        ...


def for_session(session: "SessionState",
                providers: Iterable[SessionProvider]) -> Optional[SessionProvider]:
    """The provider a session came from."""
    return next((p for p in providers if p.name == session.provider), None)
