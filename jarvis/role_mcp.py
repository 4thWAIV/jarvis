#!/usr/bin/env python3
"""One role's tools: a stdio MCP server a role session runs (roles.py).

Built on the brain's own server (jarvis_mcp.py) and offering only the
role's tools (`roles.Role.tools`), never a sending command. A GitHub lookup
of retrieval's (`role_lookups.py`) runs here, one fixed `gh` call, and so
do a file tool (`role_files.py`), read only inside the folders given with
`--read` (the run folder first), and the author's batch search
(`role_search.py`, several searches at once on the engine); every
other call goes over JARVIS's own tool channel (`POST /internal/tool`, the tool token: the
role acts as JARVIS) naming the role, and the server answers it only for a
tool that role has, as a call that is not his turn: no acting tool, no
taint on the brain's turn (server.internal_tool). With `--log`, every call
is appended to the run's search log as it is answered (research_log.py:
workflow.yaml research log_searches).

    python -m jarvis.role_mcp --role <role> [--log <run folder>/research-log.jsonl]
                              [--read <folder> ...] [--show <name> ...]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import (jarvis_mcp, research_log, role_files, role_lookups, role_search,  # noqa: E402
                    roles)

ROLE = ""
LOG: Path | None = None
FILES = role_files.Files([])


def tool_specs(role: str) -> list[dict]:
    """The brain's own specs of exactly this role's tools."""
    wanted = set(roles.role(role).tools)
    return ([s for s in jarvis_mcp.TOOL_SPECS if s["name"] in wanted]
            + [l.spec for name, l in role_lookups.LOOKUPS.items() if name in wanted]
            + [s for s in role_files.SPECS if s["name"] in wanted]
            + [s for s in role_search.SPECS if s["name"] in wanted])


def _answer(tool: str, arguments: dict) -> tuple[bool, str, dict | None]:
    if not roles.allows(ROLE, tool):
        return False, f"The {ROLE} role has no tool called {tool!r}.", None
    if tool in role_lookups.LOOKUPS:
        ok, text = role_lookups.call(tool, arguments)
        return ok, text, None
    if tool in role_files.NAMES:
        ok, text = role_files.call(FILES, tool, arguments)
        return ok, text, None
    if tool in role_search.NAMES:
        ok, text = role_search.call(arguments)
        return ok, text, None
    return jarvis_mcp._forward(tool, arguments, role=ROLE)


def call(tool: str, arguments: dict) -> tuple[bool, str, dict | None]:
    began = time.monotonic()
    ok, text, image = _answer(tool, arguments)
    if LOG is not None:
        research_log.record(LOG, ROLE, tool, arguments, ok, text, seconds=time.monotonic() - began)
    return ok, text, image


def handle(msg: dict) -> dict | None:
    return jarvis_mcp.handle(msg, tools=tool_specs(ROLE), call=call,
                             server_name=roles.SERVER_NAME)


def entry(role: str, log: Path | None = None, folders: tuple[Path, ...] = (),
          shown: tuple[str, ...] | None = None) -> dict:
    """This server for one role, as an MCP configuration names it: the
    brain's own server's way in (its URL and token file), this role, the
    run's search log it appends to (`log`), the folders its file tools
    read (`folders`, the run folder first) and the names it sees in the run
    folder (`shown`; None, everything there). The server's address is the
    one it recorded, else this process's own (the server itself)."""
    from jarvis import data_paths, server_address
    running = server_address.running()
    url = running.url if running else server_address.own().url
    return {"command": sys.executable,
            "args": ["-P", "-m", "jarvis.role_mcp", "--role", role,
                     *(["--log", str(log)] if log is not None else []),
                     *[arg for folder in folders for arg in ("--read", str(folder))],
                     *([arg for name in shown for arg in ("--show", name)] or ["--show-none"]
                       if shown is not None else [])],
            "env": {"PYTHONPATH": str(data_paths.PACKAGE_DIR.parent),
                    "JARVIS_TOOL_URL": f"{url}/internal/tool",
                    "JARVIS_TOOL_TOKEN_FILE": str(data_paths.tool_token_path())}}


def main(argv: list[str] | None = None) -> None:
    global ROLE, LOG
    parser = argparse.ArgumentParser(prog="python -m jarvis.role_mcp")
    parser.add_argument("--role", required=True, choices=sorted(roles.ROLES))
    parser.add_argument("--log", type=Path)
    parser.add_argument("--read", type=Path, action="append", default=[])
    parser.add_argument("--show", action="append", default=None)
    parser.add_argument("--show-none", action="store_true")
    args = parser.parse_args(argv)
    ROLE, LOG = args.role, args.log
    shown = () if args.show_none else (tuple(args.show) if args.show is not None else None)
    files = role_files.Files(args.read, shown)
    FILES.folders, FILES.shown = files.folders, files.shown
    jarvis_mcp.main(handle)


if __name__ == "__main__":
    main()
