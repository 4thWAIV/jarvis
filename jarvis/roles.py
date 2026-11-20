"""
roles.py — each role of a package's making: its tier, its effort, its
engines.json entry, how long it lives, and what it is released to.

Every value here is the design's (sessions.yaml role_tiers_v1, effort_v1
and launch_v1, participants.md, workflow.yaml swarm tools and
tim_knowledge), approved by Tim on 2026-11-18 and 2026-11-19. A role is
launched the way the main brains are (no more setup than theirs, his word
2026-11-19): on JARVIS's own settings on both brains, locked down and
released to exactly its `Release` (his word, 2026-11-20), its own tool
server holding only its tools, never a sending command (message,
work_report, package_start), and no role a shell.

Imported by the role's tool server (role_mcp.py, a child process), so it
imports nothing heavier than agent_rights.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jarvis import agent_rights, role_lookups

ONE_SHOT = "one_shot"           # one item in, one answer out, nothing kept
LONG_LIVED = "long_lived"       # keeps its session and is resumed with it

# The research tools: the research right's (agent_rights.TOOLS), the
# brain's own past conversations among them (workflow.yaml research
# tools_to_add_to_research_right).
RESEARCH_TOOLS: tuple[str, ...] = tuple(agent_rights.TOOLS["research"])

# The package's evidence search (workflow.yaml gather scoped_search).
EVIDENCE_SEARCH: tuple[str, ...] = ("knowledge_search",)

# What retrieval may search to find what a reference names: JARVIS's read
# tools for Work items, packages, messages, workflows and runs (sessions.yaml
# RetrievalSession chat_role).
RETRIEVAL_TOOLS: tuple[str, ...] = (
    "work_status", "work_detail", "package_inbox", "list_packages", "messages_to_him",
    "workflow_status", "run_status")

# And GitHub, read only, for pull requests, CI runs, commits, issues and
# branches: each a fixed `gh` lookup on its own tool server, never a shell
# (Tim, 2026-11-20; role_lookups.py).
RETRIEVAL_LOOKUPS: tuple[str, ...] = role_lookups.NAMES


@dataclass(frozen=True)
class Release:
    """What a role is released to from the lockdown, the one definition
    both brains read (Tim, 2026-11-20: every role on either brain starts
    locked down, no tools, and is released for its run to exactly what it
    needs; "THAT lockdown and release shoudl apply to Claude as well"; no
    role is exclusive to one brain). Claude reads it in
    `claude_provider.role_command`, Codex in `codex_provider.role_release`.
    The folders it reads are its run's (`Launch.reads`, beside its own run
    folder)."""
    builtins: tuple[str, ...] = ()  # the brain's own tools, by Claude's --tools names
    tools: tuple[str, ...] = ()     # its tools, on its own tool server
    writes: bool = False            # writes in its run folder (its prep and script)

    @property
    def file_tools(self) -> tuple[str, ...]:
        """Its tools that read or write files in its folders."""
        return tuple(t for t in self.builtins if t in FILE_TOOLS)


LOCKDOWN = Release()                # what every role starts from

# The built-in tools that read or write files. No role has a shell on either
# brain (Tim, 2026-11-20).
FILE_TOOLS = ("Read", "Write", "Edit", "Grep", "Glob")


@dataclass(frozen=True)
class Role:
    name: str
    tier: int                       # role_tiers_v1
    effort: str                     # effort_v1
    kind: str                       # its engines.json entry (engine_choice.KINDS)
    lifetime: str                   # ONE_SHOT or LONG_LIVED
    release: Release = LOCKDOWN     # what it is released to for its run

    @property
    def tools(self) -> tuple[str, ...]:
        return self.release.tools


_READS = ("Read", "Grep", "Glob")

ROLES: dict[str, Role] = {r.name: r for r in (
    # The swarm (workflow.yaml swarm; participants.md sections 6 to 8).
    Role("author", 1, "high", "package_author", LONG_LIVED,
         Release(builtins=("Read", "Write", "Edit", "Grep", "Glob"), tools=RESEARCH_TOOLS,
                 writes=True)),
    Role("editor", 1, "medium", "editor", LONG_LIVED,
         Release(builtins=_READS, tools=EVIDENCE_SEARCH)),
    Role("decision_ready_judge", 2, "medium", "judges", LONG_LIVED,
         Release(builtins=_READS, tools=RESEARCH_TOOLS)),
    Role("background_judge", 2, "medium", "judges", LONG_LIVED,
         Release(builtins=_READS, tools=RESEARCH_TOOLS)),
    # Given only what Tim knows and had access to, in the prompt, and no
    # tools (Tim, 2026-11-20).
    Role("cares_judge", 2, "medium", "judges", LONG_LIVED),
    Role("order_judge", 2, "medium", "judges", LONG_LIVED),
    Role("theory_of_mind_reader", 1, "high", "theory_of_mind_reader", LONG_LIVED),
    # The reading steps (participants.md sections 1 to 4).
    Role("retrieval", 3, "low", "retrieval", ONE_SHOT,
         Release(tools=RETRIEVAL_TOOLS + RETRIEVAL_LOOKUPS)),
    Role("marking", 3, "low", "marking", ONE_SHOT),
    Role("classification", 3, "low", "classification", ONE_SHOT),
    Role("extraction", 4, "low", "extraction", ONE_SHOT),
)}


@dataclass(frozen=True)
class Launch:
    """How one role session is started, beside its model and effort
    (BrainConfig): the brain's transport reads it (claude_provider,
    codex_provider)."""
    role: Role
    mcp_config: Optional[Path] = None       # its own tool server, when it has tools
    reads: tuple[Path, ...] = ()            # folders it may read beside its own
    answer_format: Optional[dict] = None    # a one-shot's schema
    session_id: Optional[str] = None        # a new long-lived Claude session's id


JUDGES = ("cares_judge", "decision_ready_judge", "background_judge", "order_judge")


def role(name: str) -> Role:
    found = ROLES.get(name)
    if found is None:
        raise KeyError(f"no role called {name!r}; the roles are {', '.join(ROLES)}")
    return found


# The name a role's tool server goes by, apart from the brain's own
# "jarvis" (which a Codex role switches off in JARVIS's Codex home).
SERVER_NAME = "jarvis_role"


def tool_name(tool: str) -> str:
    """A role tool as Claude Code and the brain's checks spell it."""
    return f"mcp__{SERVER_NAME}__{tool}"


def allows(name: Optional[str], tool: str) -> bool:
    """A role's tool server may call this tool for it."""
    found = ROLES.get(name or "")
    return found is not None and tool in found.release.tools
