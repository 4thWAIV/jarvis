"""What an agent may reach: the rights, defined once, and the one check.

A right is a named set of (method, path pattern) routes: the seven that
were the access keys' categories, unchanged, and `research`. An onboarded
agent's rights come from ONE plain file, read on every check and never
copied onto the agent (`rights_file()`: the data folder's
`agent-rights.json`, else the shipped `agent_rights.json` beside this
module): the `standard` rights every onboarded agent has, and per-agent
exceptions under `agents`, by session id or name, each `add` and
`remove`. A rights list an agent was given before (its document's
`rights`, when onboarding still copied them) counts only where it differs
from what onboarding gave then (`LEGACY_STANDARD`): those differences are
its exceptions too. A work-item key's grants (`workkey:<fingerprint>`) are
its own document's, a right and a scope. The scope is `*` for every target, or `work:<id>` for
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

import json
import logging
import re
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.agent_rights")

# What a WebSocket handshake is called in a right's routes.
WEBSOCKET = "WS"
ANY_SCOPE = "*"
# An onboarded agent's principal (agent_key_store.agent_principal).
AGENT_PRINCIPAL = "agent:"
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

# The rights file shipped beside this module: the standard rights (Tim's
# answer to the design's Q2, "plus-watch", and `research`, the one package
# system's) and no exceptions. The data folder's copy, made from it when
# missing, is the one he edits.
SHIPPED_FILE = Path(__file__).resolve().parent / "agent_rights.json"
RIGHTS_FILE_NAME = "agent-rights.json"
# What onboarding copied onto every agent before the rights file: a stored
# list equal to it is no exception, any other differs from it by its
# exceptions.
LEGACY_STANDARD: tuple[str, ...] = ("work", "workflows", "packages", "watch")
# Who an onboarded agent's rights are recorded as given by.
FROM_FILE = "rights file"

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


@dataclass(frozen=True)
class RightsFile:
    standard: tuple[str, ...]
    agents: dict            # session id or name (folded) -> {"add": [...], "remove": [...]}


def rights_file() -> Path:
    from jarvis import data_paths
    return data_paths.data_dir() / RIGHTS_FILE_NAME


def _parse(text: str) -> RightsFile:
    """The file's contents, or ValueError saying what is wrong with it."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("it is not a JSON object")
    standard = data.get("standard")
    agents = data.get("agents", {})
    if not isinstance(standard, list) or not isinstance(agents, dict):
        raise ValueError("`standard` must be a list and `agents` an object")

    def names(value, where: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not all(isinstance(r, str) for r in value):
            raise ValueError(f"{where} must be a list of rights")
        unknown = [r for r in value if r not in RIGHTS]
        if unknown:
            raise ValueError(f"{where} names no right {', '.join(unknown)}")
        return tuple(value)
    out = {}
    for who, change in agents.items():
        if not isinstance(change, dict) or set(change) - {"add", "remove"}:
            raise ValueError(f"agent {who!r} takes only `add` and `remove`")
        out[str(who).strip().casefold()] = {
            "add": names(change.get("add", []), f"{who}'s add"),
            "remove": names(change.get("remove", []), f"{who}'s remove")}
    return RightsFile(names(standard, "standard"), out)


_SHIPPED: Optional[RightsFile] = None
_cache: tuple = (None, None)          # (the file's stat stamp, what it said)
_lock = threading.Lock()


def _shipped() -> RightsFile:
    global _SHIPPED
    if _SHIPPED is None:
        _SHIPPED = _parse(SHIPPED_FILE.read_text(encoding="utf-8"))
    return _SHIPPED


def current() -> RightsFile:
    """What the rights file says now: read again whenever it changed (its
    stat), the shipped default while it is missing (a copy made for him to
    edit) or cannot be read, which is logged."""
    global _cache
    path = rights_file()
    try:
        st = path.stat()
    except FileNotFoundError:
        try:
            path.write_text(SHIPPED_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            log.warning("agent rights: could not write %s; using the shipped default", path)
        return _shipped()
    stamp = (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
    with _lock:
        if _cache[0] == stamp:
            return _cache[1]
    try:
        found = _parse(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("agent rights: %s cannot be used (%s); using the shipped default "
                    "until it is fixed", path, e)
        found = _shipped()
    with _lock:
        _cache = (stamp, found)
    return found


def _agent_name(session_id: str) -> str:
    from jarvis import agent_record
    doc = agent_record.CACHE.get(agent_record.agent_path(session_id)) or {}
    return str((doc.get("agent") or {}).get("name") or "")


def agent_rights_of(session_id: str, stored: tuple[str, ...] = ()) -> tuple[str, ...]:
    """An onboarded agent's rights: the file's standard ones, with the
    exceptions the file names for its session id or its name, and the
    differences of a list it was given before (`stored`) from
    LEGACY_STANDARD. Sorted."""
    rights = current()
    have = set(rights.standard)
    stored_set = set(stored)
    if stored_set and stored_set != set(LEGACY_STANDARD):
        have |= stored_set - set(LEGACY_STANDARD)
        have -= set(LEGACY_STANDARD) - stored_set
    for who in (session_id, _agent_name(session_id)):
        change = rights.agents.get(who.strip().casefold()) if who else None
        if change:
            have = (have | set(change["add"])) - set(change["remove"])
    return tuple(sorted(have))


def granted(principal: str) -> list:
    """Every grant `principal` holds now (agent_key_store.Grant): an
    onboarded agent's from the rights file (scope `*`; an agent with no
    onboarded key holds none), a work-item key's from its own document."""
    from jarvis import agent_key_store
    held = agent_key_store.grants(principal)
    if not principal.startswith(AGENT_PRINCIPAL):
        return held
    session = principal[len(AGENT_PRINCIPAL):]
    if not agent_key_store.is_onboarded(session):
        return []
    stored = tuple(g.right for g in held if g.scope == ANY_SCOPE)
    return [agent_key_store.Grant(principal, r, ANY_SCOPE, 0.0, FROM_FILE)
            for r in agent_rights_of(session, stored)]


def allows(principal: str, method: str, path: str, query: str = "") -> bool:
    """Whether `principal`'s grants cover this request: one of its rights
    covers the method and path, within that grant's scope. Never raises:
    an unreadable record admits nothing."""
    try:
        held = granted(principal)
    except Exception:
        log.warning("agent rights: %s's rights could not be read", principal, exc_info=True)
        return False
    method = method.upper()
    return any(r.right in RIGHTS and RIGHTS[r.right].covers(method, path)
               and in_scope(r.scope, method, path, query) for r in held)


def tools_of(principal: str) -> frozenset[str]:
    """Every brain tool `principal`'s grants reach through AGENT_TOOL_PATH:
    only grants for every target (a tool reads across items, so a work-item
    key's scope admits none). Never raises: an unreadable record reaches
    nothing."""
    try:
        held = granted(principal)
    except Exception:
        log.warning("agent rights: %s's rights could not be read", principal, exc_info=True)
        return frozenset()
    return frozenset(tool for r in held if r.scope == ANY_SCOPE
                     for tool in TOOLS.get(r.right, ()))


def allows_tool(principal: str, tool: str) -> bool:
    """Whether `principal` may call the brain tool `tool` (AGENT_TOOL_PATH)."""
    return tool in tools_of(principal)
