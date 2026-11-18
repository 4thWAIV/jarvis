"""What an agent may reach: the rights, defined once, and the one check.

A right is a named set of (method, path pattern) routes: the seven that
were the access keys' categories, unchanged. A grant is one of the
`rights` of its holder's document (agent_key_store.py): a principal (an onboarded
agent, `agent:<session id>`, or a work-item key, `workkey:<fingerprint>`),
a right and a scope. The scope is `*` for every target, or `work:<id>` for
one Work item; `project:<name>` comes later. `allows` is the only place a
signed request's rights are judged (web_auth's BY_AGENT_KEY). The rights
were the access keys' categories, which admit nothing any more.

The voice socket, settings, brains, restart and /internal/tool are in no
right: those stay with the browser and the tool token. A right may also
name brain tools (`TOOLS`): `research` reaches the brain's own reading
tools through one route, `AGENT_TOOL_PATH`, each call judged by
`allows_tool` (agent_mcp.py is the agent's side).
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

# What a WebSocket handshake is called in a right's routes.
WEBSOCKET = "WS"
ANY_SCOPE = "*"
WORK_SCOPE = "work:"
# The one route an agent's MCP server reaches the brain's tools by
# (agent_mcp.py, server.api_agent_tool); which tools is `TOOLS`.
AGENT_TOOL_PATH = "/api/agent-tool"
# Whether one of the caller's own reports landed (report_keys.py): it reads
# only the caller's own keys, so every grant that may report may ask it.
OWN_REPORTS = r"/api/reports/[^/]+"


@dataclass(frozen=True)
class Right:
    description: str
    routes: tuple[tuple[str, str], ...]   # (method, full-match path pattern)

    def covers(self, method: str, path: str) -> bool:
        return any(m == method and re.fullmatch(p, path) for m, p in self.routes)


RIGHTS: dict[str, Right] = {
    "captures": Right(
        "Accept, delete, send, read aloud and restore the captures on the voice page.",
        (("POST", r"/api/capture/\d+/(accept|delete|send|read|restore)"),)),
    "agents": Right(
        "Rename agents and switch them active or inactive.",
        (("POST", r"/api/agents/[^/]+/(name|active)"),)),
    "runs": Right(
        "Start, cancel and retry runs, open projects, and watch the runs feed.",
        (("POST", r"/api/runs"), ("DELETE", r"/api/runs/[^/]+"),
         ("POST", r"/api/runs/[^/]+/retry"), ("POST", r"/api/projects/open"),
         (WEBSOCKET, r"/ws/runs"))),
    "workflows": Right(
        "Start workflows and report their steps to the Workflows view.",
        (("POST", r"/api/workflows"), ("POST", r"/api/workflows/[^/]+/events"),
         ("GET", OWN_REPORTS))),
    "work": Right(
        "Track work items and report who is on each piece to the Work tab, and send Tim "
        "messages about them (or about nothing in particular).",
        (("POST", r"/api/work"), ("POST", r"/api/work/[^/]+/events"),
         ("POST", r"/api/messages"),
         ("GET", r"/api/work/answers"), ("GET", OWN_REPORTS), ("POST", r"/api/agent-keys/work-keys"),
         ("POST", r"/api/agent-keys/work-keys/.+/renew"))),
    "packages": Right(
        "Send Tim packages for his package inbox, and write canonical language documents "
        "for them, and retrieve and inspect received attachments without opening or running them.",
        (("POST", r"/api/packages/inbox"), ("POST", r"/api/language-documents"),
         ("POST", r"/api/team/messages/[^/]+/files/[^/]+/(location|download)"),
         ("POST", r"/api/team/messages/[^/]+/attachments/[^/]+/bundle"))),
    "watch": Right(
        "Watch the sessions and specs feeds.",
        ((WEBSOCKET, r"/ws/sessions"), (WEBSOCKET, r"/ws/specs"))),
    "research": Right(
        "Read what JARVIS knows, for research: the brain's own reading tools (what the user said "
        "and decided, his messages, Work, packages, runs, workflows, the knowledge engine, "
        "projects and folders), other people's words walled as for the brain.",
        (("POST", AGENT_TOOL_PATH),)),
}

# The brain tools each right reaches through AGENT_TOOL_PATH: only reading
# tools (none is one of the server's ACTING_TOOLS, a test holds it), each
# answered by the brain's own handler. A right not named here reaches none.
TOOLS: dict[str, tuple[str, ...]] = {
    "research": (
        "conversation_history", "messages_to_him", "recall", "package_inbox", "list_packages",
        "package_results", "work_status", "work_detail", "work_context", "workflow_status",
        "run_status", "knowledge_search", "knowledge_status", "search_repo", "repo_overview",
        "project_notes", "project_history", "list_folders", "browse_folder",
        "read_folder_file", "list_agents", "read_message_reply", "read_session_reply"),
}

# The rights a newly onboarded agent is given, each with scope `*`: Tim's
# answer to the design's Q2 ("plus-watch"), what agents use today (`work`,
# `workflows`, and `packages`, which make_package reached through the tool
# token), `watch`, and `research` (the one package system: a package's
# author reads his record through the brain's reading tools). Changing the default is a change to this one line.
DEFAULT_RIGHTS: tuple[str, ...] = ("work", "workflows", "packages", "watch", "research")

# What a work-item key may reach, scoped to its one item: reports on it,
# answers to its decisions, packages sent for it and messages about it
# (`?work=` its own item). Never `POST /api/work`.
WORK_KEY_RIGHTS: tuple[str, ...] = ("work", "packages")


def _work_target(method: str, path: str, query: str) -> str:
    """The Work item a request is about, or "" when it names none."""
    m = re.fullmatch(r"/api/work/([^/]+)/events", path)
    if m:
        return urllib.parse.unquote(m.group(1))
    params = urllib.parse.parse_qs(query or "")
    if path in ("/api/packages/inbox", "/api/messages"):
        return (params.get("work") or [""])[0]
    if path == "/api/work/answers":
        return (params.get("item") or [""])[0]
    return ""


def in_scope(scope: str, method: str, path: str, query: str) -> bool:
    """Whether a grant's scope admits this request."""
    if scope == ANY_SCOPE or re.fullmatch(OWN_REPORTS, path):
        return True
    if scope.startswith(WORK_SCOPE):
        return bool(scope[len(WORK_SCOPE):]) and \
            _work_target(method, path, query) == scope[len(WORK_SCOPE):]
    return False


def allows(principal: str, method: str, path: str, query: str = "") -> bool:
    """Whether `principal`'s grants cover this request: one of its rights
    covers the method and path, within that grant's scope. Never raises:
    an unreadable record admits nothing."""
    from jarvis import agent_key_store
    try:
        held = agent_key_store.grants(principal)
    except Exception:
        return False
    method = method.upper()
    return any(r.right in RIGHTS and RIGHTS[r.right].covers(method, path)
               and in_scope(r.scope, method, path, query) for r in held)


def tools_of(principal: str) -> frozenset[str]:
    """Every brain tool `principal`'s grants reach through AGENT_TOOL_PATH:
    only grants for every target (a tool reads across items, so a work-item
    key's scope admits none). Never raises: an unreadable record reaches
    nothing."""
    from jarvis import agent_key_store
    try:
        held = agent_key_store.grants(principal)
    except Exception:
        return frozenset()
    return frozenset(tool for r in held if r.scope == ANY_SCOPE
                     for tool in TOOLS.get(r.right, ()))


def allows_tool(principal: str, tool: str) -> bool:
    """Whether `principal` may call the brain tool `tool` (AGENT_TOOL_PATH)."""
    return tool in tools_of(principal)
