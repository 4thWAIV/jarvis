#!/usr/bin/env python3
"""One role's tools: a stdio MCP server a role session runs (roles.py).

Built on the brain's own server (jarvis_mcp.py) and offering only the
role's tools (`roles.Role.tools`), never a sending command. A GitHub lookup
of retrieval's (`role_lookups.py`) runs here, one fixed `gh` call; every
other call goes over JARVIS's own tool channel (`POST /internal/tool`, the tool token: the
role acts as JARVIS) naming the role, and the server answers it only for a
tool that role has, as a call that is not his turn: no acting tool, no
taint on the brain's turn (server.internal_tool).

    python -m jarvis.role_mcp --role <role>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import jarvis_mcp, role_lookups, roles  # noqa: E402

ROLE = ""


def tool_specs(role: str) -> list[dict]:
    """The brain's own specs of exactly this role's tools."""
    wanted = set(roles.role(role).tools)
    return ([s for s in jarvis_mcp.TOOL_SPECS if s["name"] in wanted]
            + [l.spec for name, l in role_lookups.LOOKUPS.items() if name in wanted])


def call(tool: str, arguments: dict) -> tuple[bool, str, dict | None]:
    if not roles.allows(ROLE, tool):
        return False, f"The {ROLE} role has no tool called {tool!r}.", None
    if tool in role_lookups.LOOKUPS:
        ok, text = role_lookups.call(tool, arguments)
        return ok, text, None
    return jarvis_mcp._forward(tool, arguments, role=ROLE)


def handle(msg: dict) -> dict | None:
    return jarvis_mcp.handle(msg, tools=tool_specs(ROLE), call=call,
                             server_name=roles.SERVER_NAME)


def entry(role: str) -> dict:
    """This server for one role, as an MCP configuration names it: the
    brain's own server's way in (its URL and token file), and this role.
    The server's address is the one it recorded, else this process's own
    (the server itself)."""
    from jarvis import data_paths, server_address
    running = server_address.running()
    url = running.url if running else server_address.own().url
    return {"command": sys.executable,
            "args": ["-P", "-m", "jarvis.role_mcp", "--role", role],
            "env": {"PYTHONPATH": str(data_paths.PACKAGE_DIR.parent),
                    "JARVIS_TOOL_URL": f"{url}/internal/tool",
                    "JARVIS_TOOL_TOKEN_FILE": str(data_paths.tool_token_path())}}


def main(argv: list[str] | None = None) -> None:
    global ROLE
    parser = argparse.ArgumentParser(prog="python -m jarvis.role_mcp")
    parser.add_argument("--role", required=True, choices=sorted(roles.ROLES))
    ROLE = parser.parse_args(argv).role
    jarvis_mcp.main(handle)


if __name__ == "__main__":
    main()
