"""
roles.py — each role of a package's making: its tier, its effort, its
engines.json entry, how long it lives, and exactly the tools it has.

Every value here is the design's (sessions.yaml role_tiers_v1, effort_v1
and launch_v1, participants.md, workflow.yaml swarm tools and
tim_knowledge), approved by Tim on 2026-11-18 and 2026-11-19. A role is
launched the way the main brains are (no more setup than theirs, his word
2026-11-19): on JARVIS's own settings on both brains, with its own tool
server holding only its tools, never a sending command (message,
work_report, package_start).

Imported by the role's tool server (role_mcp.py, a child process), so it
imports nothing heavier than agent_rights.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jarvis import agent_rights

ONE_SHOT = "one_shot"           # one item in, one answer out, nothing kept
LONG_LIVED = "long_lived"       # keeps its session and is resumed with it

# The research tools: the research right's (agent_rights.TOOLS) with the
# brain's own past conversations (workflow.yaml research
# tools_to_add_to_research_right).
RESEARCH_TOOLS: tuple[str, ...] = tuple(agent_rights.TOOLS["research"]) + (
    "search_history", "read_history")

# The package's evidence search (workflow.yaml gather scoped_search).
EVIDENCE_SEARCH: tuple[str, ...] = ("knowledge_search",)

# What retrieval may search to find what a reference names: JARVIS's read
# tools for Work items, packages, messages, workflows and runs (sessions.yaml
# RetrievalSession chat_role).
RETRIEVAL_TOOLS: tuple[str, ...] = (
    "work_status", "work_detail", "package_inbox", "list_packages", "messages_to_him",
    "workflow_status", "run_status")

# And gh, read only, for pull requests, CI runs, commits, issues and
# branches: the commands Claude's Bash may run (participants.md section 1).
RETRIEVAL_COMMANDS: tuple[str, ...] = (
    "gh pr list *", "gh pr view *", "gh run list *", "gh run view *",
    "gh issue list *", "gh issue view *", "gh search prs *", "gh search issues *",
    "gh search commits *", "git ls-remote *")


@dataclass(frozen=True)
class Role:
    name: str
    tier: int                       # role_tiers_v1
    effort: str                     # effort_v1
    kind: str                       # its engines.json entry (engine_choice.KINDS)
    lifetime: str                   # ONE_SHOT or LONG_LIVED
    builtins: tuple[str, ...] = ()  # Claude Code's own tools (--tools)
    tools: tuple[str, ...] = ()     # JARVIS's tools, on its own tool server
    commands: tuple[str, ...] = ()  # the shell commands Claude's Bash may run
    writes: bool = False            # writes in its run folder (its prep and script)


_READS = ("Read", "Grep", "Glob")

ROLES: dict[str, Role] = {r.name: r for r in (
    # The swarm (workflow.yaml swarm; participants.md sections 6 to 8).
    Role("author", 1, "high", "package_author", LONG_LIVED,
         builtins=("Read", "Write", "Edit", "Grep", "Glob"), tools=RESEARCH_TOOLS, writes=True),
    Role("editor", 1, "medium", "editor", LONG_LIVED, builtins=_READS, tools=EVIDENCE_SEARCH),
    Role("decision_ready_judge", 2, "medium", "judges", LONG_LIVED,
         builtins=_READS, tools=RESEARCH_TOOLS),
    Role("background_judge", 2, "medium", "judges", LONG_LIVED,
         builtins=_READS, tools=RESEARCH_TOOLS),
    # sessions.yaml gives these two "only the package evidence, filtered to
    # what Tim knows and had access to"; their approved prompt says "You are
    # given only what Tim knows and what he had access to ... and you have
    # no research tools". Which of the two is meant (that evidence given in
    # the prompt, or a search of it filtered by knowledge state) is his to
    # say: until he does they have no tool, and their material comes in the
    # prompt as the approved words say (sessions.share).
    Role("cares_judge", 2, "medium", "judges", LONG_LIVED),
    Role("order_judge", 2, "medium", "judges", LONG_LIVED),
    Role("theory_of_mind_reader", 1, "high", "theory_of_mind_reader", LONG_LIVED),
    # The reading steps (participants.md sections 1 to 4).
    Role("retrieval", 3, "low", "retrieval", ONE_SHOT, builtins=("Bash",),
         tools=RETRIEVAL_TOOLS, commands=RETRIEVAL_COMMANDS),
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


def codex_refusal(found: Role) -> Optional[str]:
    """Why this role is not started on Codex, or None. Codex roles run with
    the code-mode host off (Tim's "Awesome", 2026-11-19: "exec is listed but
    refuses to run, so a Codex session cannot read files"), and Codex calls
    MCP tools only through that host (codex_provider.KEEP_FEATURES,
    measured), so a role whose approved tools are its job (launch_v1
    tools_per_swarm_role, RetrievalSession) would run without them. That
    conflict between the two approved entries is his to settle; until then
    such a role is refused on Codex, never run without its tools."""
    if not (found.tools or found.commands):
        return None
    return (f"the {found.name} role cannot run on Codex: Codex roles run with the code-mode "
            f"host off, which leaves no way to call its tools "
            f"({', '.join(found.tools + found.commands)}); this waits on Tim's decision")


def allows(name: Optional[str], tool: str) -> bool:
    """A role's tool server may call this tool for it."""
    found = ROLES.get(name or "")
    return found is not None and tool in found.tools
