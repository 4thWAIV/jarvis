"""The agent directory: the one way JARVIS lists and finds agents.

An agent is a conversation running on this machine (Claude Code or Codex),
active or inactive, by the name it goes by: the official name the user gave
it (run_store.rename_agent), else the one the session watcher derived. Every
tool that lists agents or takes an agent's name comes here, and nowhere else
matches names, so a name that works once works everywhere.

Names are matched as they are SPOKEN: `spoken_key` lowercases, folds
accents (`spoken_words.fold`, since the recogniser may or may not write them) and turns
hyphens, underscores, dots and commas into spaces, so "Tokens for Cash",
"tokens-for-cash" and "tokens_for_cash" are one name. Ambiguity is never
resolved by guessing: `find` returns every candidate and the caller asks.

The server connects the directory to its live sources once, at import
(`connect`); the defaults read the agents table and the needs boards.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Callable

from jarvis import data_paths
from jarvis import run_store
from jarvis import session_needs
from jarvis import session_watch
from jarvis import spoken_words


@dataclass(frozen=True)
class Agent:
    session_id: str
    name: str             # official name if set, else the derived one
    project: str
    provider: str | None
    state: str            # working, idle, needs_you, ...
    active: bool
    needs: int            # needs-board items
    # Its subagents working now (session_watch.read_subagents); a floor when
    # `subagents_capped`.
    subagents_working: int = 0
    subagents_capped: bool = False


# Words a person says around a name that carry no identity of their own.
# "the newer one" distinguishes; "the", "one", "session" do not.
_FILLER = frozenset({
    "the", "a", "an", "one", "ones", "session", "sessions", "conversation",
    "conversations", "project", "in", "on", "at", "my", "please", "that",
    "this", "it", "s", "lets", "let", "go", "with", "use", "about", "agent",
    "agents",
})

# What the user says to mean the agent just talked about.
_THAT_ONE = frozenset({"that one", "that", "it", "the same one", "that session",
                       "that agent"})


def spoken_key(text: str) -> str:
    """A name as it is said: lowercase, accents folded ("José" is "jose");
    hyphens, underscores, dots and commas become spaces; apostrophes go;
    spaces collapsed."""
    text = spoken_words.fold(text).lower().replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[-_.,]+", " ", text).split())


def _words(text: str) -> set[str]:
    """A name's words as said, accents folded."""
    return set(spoken_words.words(spoken_key(text)))


def _needs_by_project() -> dict[str, int]:
    """Each project's needs-board item count, by lowercased project name,
    its running workflows' open decisions included."""
    from jarvis import workflow_store
    return {b.project.lower(): b.items for b in
            workflow_store.with_decisions(session_needs.read_boards(data_paths.needs_dir()))}


def _agent_rows() -> list[dict]:
    """The agents table: the user's activations, in the list's order. A
    store not yet made (before init_db) has no choices in it yet; finding an
    agent by name never depends on it."""
    try:
        return run_store.list_agents()
    except sqlite3.OperationalError:
        return []


_snapshot: Callable[[], session_watch.Snapshot] = session_watch.Snapshot
_rows: Callable[[], list[dict]] = _agent_rows
_needs: Callable[[], dict[str, int]] = _needs_by_project


def connect(snapshot: Callable[[], session_watch.Snapshot],
            rows: Callable[[], list[dict]] = _agent_rows,
            needs: Callable[[], dict[str, int]] = _needs_by_project) -> None:
    """Where the directory reads from: the sessions running now (with
    official names applied), the agents table (activation and order), and
    the needs boards."""
    global _snapshot, _rows, _needs
    _snapshot, _rows, _needs = snapshot, rows, needs


def _entries() -> list[tuple[Agent, session_watch.SessionState | None]]:
    """Every agent with the conversation it is. The agents table first, in
    its order (busy first, then most recently messaged), filled in from the
    running conversation; then any running conversation the table has not
    caught up with yet (it syncs about once a second)."""
    sessions = {s.session_id: s for s in _snapshot().sessions
                if s.state != session_watch.GONE}
    needs = _needs()
    out = []
    for row in _rows():
        s = sessions.pop(row["session_id"], None)
        project = s.project if s else ""
        out.append((Agent(session_id=row["session_id"], name=row["name"], project=project,
                          provider=s.provider if s else None,
                          state=s.state if s else row["state"], active=bool(row["active"]),
                          needs=needs.get(project.lower(), 0) if project else 0,
                          **_subagent_counts(s)), s))
    for s in sessions.values():
        out.append((Agent(session_id=s.session_id, name=session_watch.agent_name(s),
                          project=s.project, provider=s.provider, state=s.state,
                          active=False, needs=needs.get(s.project.lower(), 0),
                          **_subagent_counts(s)), s))
    return out


def _subagent_counts(s: session_watch.SessionState | None) -> dict:
    if s is None:
        return {}
    return {"subagents_working": s.agents_active, "subagents_capped": s.agents_capped}


def agents() -> list[Agent]:
    """Every running agent, active and inactive."""
    return [a for a, _ in _entries()]


def _prefer_real(matches: list[tuple[Agent, session_watch.SessionState]]):
    """A `fresh` conversation (never prompted) is noise beside a real one;
    only when every match is fresh are they all kept."""
    real = [m for m in matches if m[0].state != session_watch.FRESH]
    return real if real else matches


def find(reference: str, last_mentioned: str | None = None) -> list[Agent]:
    """Every candidate for what the user said, by spoken_key: exact name,
    session id, exact project, all words in name and project, then
    substring either way. Several means ask; none means it is not running."""
    return [a for a, _ in _find(reference, last_mentioned)]


def _find(reference: str, last_mentioned: str | None):
    ref = spoken_key(reference)
    entries = _entries()
    if not ref:
        return []
    if ref in _THAT_ONE:
        return [e for e in entries if e[0].session_id == last_mentioned]

    exact = [e for e in entries if spoken_key(e[0].name) == ref]
    if exact:
        return exact
    sid = [e for e in entries if e[0].session_id == (reference or "").strip()]
    if sid:
        return sid
    roster = [e for e in entries
              if e[1] is not None and e[1].roster_name and spoken_key(e[1].roster_name) == ref]
    if roster:
        return roster

    # Exact project before anything looser: "hammer" is not "hammer-private".
    exact_project = [e for e in entries if e[0].project and spoken_key(e[0].project) == ref]
    if exact_project:
        return _prefer_real(exact_project)

    # Every distinguishing word the user said is in the name or project, so
    # an answer to "which one?" ("the newer one in webapp-fresh") narrows.
    ref_words = {w for w in _words(ref) if w not in _FILLER}
    if ref_words:
        hits = [e for e in entries if ref_words <= _words(f"{e[0].name} {e[0].project}")]
        # A project said whole ("the hammer one") beats one that merely
        # shares a word with it (hammer-private).
        whole = [e for e in hits
                 if e[0].project and f" {spoken_key(e[0].project)} " in f" {ref} "]
        hits = whole or hits
        if hits:
            narrowed = _prefer_real(hits)
            if len(narrowed) < len(entries):
                return narrowed

    # Substring, both directions: "the chitauri one" contains "chitauri",
    # and "hammer" is contained in "the newer hammer".
    loose = [e for e in entries
             if (e[0].name and (spoken_key(e[0].name) in ref or ref in spoken_key(e[0].name)))
             or (e[0].project and spoken_key(e[0].project) in ref)]
    return _prefer_real(loose)


def find_sessions(reference: str, last_mentioned: str | None = None
                  ) -> list[session_watch.SessionState]:
    """`find`, as the conversations themselves, for the tools that act on
    one. An agent whose conversation has just ended has none to act on."""
    return [s for _, s in _find(reference, last_mentioned) if s is not None]


def same_name(name: str, other: str) -> bool:
    """Whether two names are the same name said aloud."""
    return spoken_key(name) == spoken_key(other)
