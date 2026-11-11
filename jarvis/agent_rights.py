"""What an agent may reach: the rights, defined once, and the one check.

A right is a named set of (method, path pattern) routes: the seven that
were the access keys' categories, unchanged. A grant is one row of
`agent_rights` (agent_key_store.py's schema): a principal (an onboarded
agent, `agent:<session id>`, or a work-item key, `workkey:<fingerprint>`),
a right and a scope. The scope is `*` for every target, or `work:<id>` for
one Work item; `project:<name>` comes later. `allows` is the only place a
signed request's rights are judged (web_auth's BY_AGENT_KEY). The rights
were the access keys' categories, which admit nothing any more.

The voice socket, settings, brains, restart and /internal/tool are in no
right: those stay with the browser and the tool token.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

# What a WebSocket handshake is called in a right's routes.
WEBSOCKET = "WS"
ANY_SCOPE = "*"
WORK_SCOPE = "work:"


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
        (("POST", r"/api/workflows"), ("POST", r"/api/workflows/[^/]+/events"))),
    "work": Right(
        "Track work items and report who is on each piece to the Work tab, and send Tim "
        "messages about them (or about nothing in particular).",
        (("POST", r"/api/work"), ("POST", r"/api/work/[^/]+/events"),
         ("POST", r"/api/messages"),
         ("GET", r"/api/work/answers"), ("POST", r"/api/agent-keys/work-keys"),
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
}

# The rights a newly onboarded agent is given, each with scope `*`: Tim's
# answer to the design's Q2 ("plus-watch"), what agents use today (`work`,
# `workflows`, and `packages`, which make_package reached through the tool
# token) and `watch`. Changing the default is a change to this one line.
DEFAULT_RIGHTS: tuple[str, ...] = ("work", "workflows", "packages", "watch")

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
    if scope == ANY_SCOPE:
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
