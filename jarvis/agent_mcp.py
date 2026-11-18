#!/usr/bin/env python3
"""JARVIS's tools for an agent: a stdio MCP server a Claude Code or Codex
session runs (installed by `python -m jarvis.session_skill install`).

Built on the brain's own server (jarvis_mcp.py: hand-rolled JSON-RPC, no new
dependency), with two kinds of tool, every request signed with the agent's
own onboarded key (agent_keys.py), never the tool token:

- The brain's reading tools an agent's rights name (`agent_rights.TOOLS`,
  the `research` right): each call goes to `POST /api/agent-tool`, which
  answers it with the brain's own handler, capped and with other people's
  words walled as the brain gets them. Same names, same arguments.
- The agent commands as tools (`COMMANDS`): each calls the command's own
  `main` with the same arguments the command line takes (message.py,
  work_report.py, workflow_report.py, retrieve_attachment.py,
  language_document.py), so a tool and its command can never differ, and
  the package job's three functions (package_jobs.py).

The session it signs as is JARVIS_AGENT_SESSION, else the session Claude
Code says it runs in (CLAUDE_CODE_SESSION_ID), else a tool's `as_session`
argument (a Codex session gives it), else a work-item key in this folder or
above, as the commands find it. `agent_key` stays a command: it makes the
key these tools sign with.

    python -m jarvis.agent_mcp [--cert <cert.pem>]
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import importlib
import io
import json
import sys
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import agent_rights, env_names, jarvis_mcp  # noqa: E402
from jarvis.workflow_report import TimedOut, signed_client  # noqa: E402

SERVER_NAME = "jarvis"
# The certificate the commands trust (`--cert`), this install's.
CERT: Path | None = None

AS_SESSION = "as_session"
_AS_SESSION_SPEC = {
    "type": "string",
    "description": "Your own session id, only when JARVIS cannot tell it (a Codex session): "
                   "the request is signed with that session's key."}


def session_of(arguments: dict) -> str:
    """The session a call signs as: JARVIS_AGENT_SESSION, else Claude Code's
    own session, else the call's `as_session`, else "" (a work-item key in
    this folder or above, as the commands look for one)."""
    return (env_names.read("JARVIS_AGENT_SESSION").strip()
            or env_names.read("CLAUDE_CODE_SESSION_ID").strip()
            or str(arguments.get(AS_SESSION) or "").strip())


def _with_session(spec: dict) -> dict:
    """A tool's spec with the optional `as_session` argument."""
    spec = copy.deepcopy(spec)
    schema = spec.setdefault("inputSchema", {"type": "object"})
    schema.setdefault("properties", {})[AS_SESSION] = _AS_SESSION_SPEC
    return spec


# --- the brain's reading tools, through POST /api/agent-tool ---------------

def research_specs() -> list[dict]:
    """The brain's own specs of every tool the `research` right names."""
    wanted = set(agent_rights.TOOLS["research"])
    return [_with_session(s) for s in jarvis_mcp.TOOL_SPECS if s["name"] in wanted]


def _research(tool: str, arguments: dict) -> tuple[bool, str, dict | None]:
    """One brain tool, answered by JARVIS for this agent. Every failure is a
    sentence, never a traceback."""
    args = {k: v for k, v in arguments.items() if k != AS_SESSION}
    try:
        client = signed_client(CERT, session_of(arguments), "research")
    except SystemExit as no_key:
        return False, str(no_key), None
    client.timeout = jarvis_mcp.TIMEOUT_FOR.get(tool, jarvis_mcp.TIMEOUT_SEC)
    try:
        r = client.post(agent_rights.AGENT_TOOL_PATH, {"tool": tool, "arguments": args})
    except TimedOut:
        return False, "JARVIS did not answer in time.", None
    except (urllib.error.URLError, OSError):
        return False, "The JARVIS server is unreachable.", None
    try:
        payload = r.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return False, f"JARVIS sent something unreadable ({r.status_code}).", None
    if r.status_code != 200:
        return False, (f"JARVIS refused the call ({r.status_code}): "
                       f"{payload.get('error') or payload.get('detail') or r.text}"), None
    return (bool(payload.get("ok")), str(payload.get("text", "")),
            jarvis_mcp._image_block(payload.get("image")))


# --- the agent commands, as tools -----------------------------------------

@dataclass(frozen=True)
class Command:
    """One agent command as a tool: the module whose `main` it calls, the
    words before its arguments (a subcommand), its positional arguments in
    order, and its spec. Every other property is an option: `--<name>`
    (underscores as hyphens, or `flags`' name), a boolean a bare flag, an
    array repeated, an object given as JSON."""
    name: str
    module: str
    description: str
    properties: dict
    positional: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    words: tuple[str, ...] = ()
    flags: dict = field(default_factory=dict)
    # work_report.py and workflow_report.py take argv with the program first.
    program_first: bool = False

    def spec(self) -> dict:
        schema = {"type": "object", "properties": copy.deepcopy(self.properties)}
        if self.required:
            schema["required"] = list(self.required)
        return _with_session({"name": self.name, "description": self.description,
                              "inputSchema": schema})

    def argv(self, arguments: dict, session: str) -> list[str]:
        """The command line this call is, as the agent would type it."""
        argv = ["--cert", str(CERT)] if CERT else []
        if session:
            argv += ["--as", session]
        argv += list(self.words)
        for name in self.positional:
            argv.append(str(arguments.get(name, "")))
        for name, prop in self.properties.items():
            value = arguments.get(name)
            if name in self.positional or value is None or value == "" or value is False:
                continue
            flag = self.flags.get(name, "--" + name.replace("_", "-"))
            kind = prop.get("type")
            if kind == "boolean":
                argv.append(flag)
            elif kind == "array":
                for each in (value if isinstance(value, list) else [value]):
                    argv += [flag, str(each)]
            elif kind == "object":
                argv += [flag, json.dumps(value)]
            else:
                argv += [flag, str(value)]
        return ([self.module] if self.program_first else []) + argv


def _run_main(module: str, argv: list[str]) -> tuple[bool, str]:
    """Call `module`'s main with `argv`, as its command line would: (exit 0,
    everything it printed). A refusal it raises is what it says."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = importlib.import_module(module).main(argv)
    except SystemExit as e:
        if isinstance(e.code, str):
            err.write(e.code)
            code = 1
        else:
            code = e.code or 0
    except TimedOut:
        return False, "JARVIS did not answer in time."
    except (urllib.error.URLError, OSError) as e:
        return False, f"The JARVIS server is unreachable ({e})."
    text = "\n".join(t.strip() for t in (out.getvalue(), err.getvalue()) if t.strip())
    return code == 0, text or ("Done." if code == 0 else f"It failed (exit {code}).")


_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}
_UNTIL = {**_STRS, "description": "What holds it: decision:<key>, work:<item>:<key>, "
                                  "workflow:<id>:<key>, package:<id or name>[:<decision>], "
                                  "or (a blocker only) words."}
_HELD_STATE = {"type": "string", "description": "planned, ready, working, verifying, "
                                                "waiting_on_tim or blocked (done is his)."}
_WORK = "jarvis.work_report"
_FLOW = "jarvis.workflow_report"

COMMANDS: tuple[Command, ...] = (
    Command("send_message", "jarvis.message",
            "Send the user a message about the Work item it belongs to (python -m "
            "jarvis.message). It lands on that item's Messages; with general, in his Inbox. "
            "A question or decision is never sent bare: carry it in a package. Prints the "
            "message's id.",
            {"says": {**_STR, "description": "What it says, short."},
             "work": {**_STR, "description": "The Work item it is about."},
             "general": {"type": "boolean", "description": "It is about no Work item."},
             "package": {**_STR, "description": "Absolute path of a package zip it carries."}},
            positional=("says",), required=("says",)),
    Command("work_start", _WORK,
            "Start tracking a Work item on a zip or folder (work_report.py start). Prints its id.",
            {"title": _STR, "zip": {**_STR, "description": "Absolute path of the zip."},
             "folder": {**_STR, "description": "Absolute path of the folder."},
             "state": _HELD_STATE, "area": _STR},
            positional=("title",), required=("title",), words=("start",), program_first=True),
    Command("work_update", _WORK,
            "Report on a Work item as a whole (work_report.py update).",
            {"id": _STR, "summary": _STR, "state": _HELD_STATE, "area": _STR, "until": _UNTIL,
             "cleared": {"type": "boolean", "description": "A blocker in words is cleared."}},
            positional=("id", "summary"), required=("id", "summary"), words=("update",),
            program_first=True),
    Command("work_piece", _WORK,
            "Report one piece of a Work item and who is on it (work_report.py piece). Who is "
            "agent:<session id>, agent:<session id>/sub:<agent id>, run:<id>, workflow:<id> "
            "or tim.",
            {"id": _STR, "key": _STR, "summary": _STR, "title": _STR, "who": _STR,
             "state": _HELD_STATE, "worker": _STR,
             "after": {**_STR, "description": "The pieces it waits on, key[,key]."},
             "evidence": {**_STRS, "description": "Commits, absolute paths or URLs."},
             "until": _UNTIL, "then": _STR, "cleared": {"type": "boolean"}},
            positional=("id", "key", "summary"), required=("id", "key", "summary"),
            words=("piece",), program_first=True),
    Command("work_end_wait", _WORK,
            "End a wait you put on a piece or item yourself (work_report.py end-wait).",
            {"id": _STR, "summary": _STR, "piece": _STR, "until": _UNTIL},
            positional=("id", "summary"), required=("id", "summary"), words=("end-wait",),
            program_first=True),
    Command("work_close", _WORK,
            "Close a Work item: to History for good (work_report.py close).",
            {"id": _STR, "summary": _STR},
            positional=("id", "summary"), required=("id",), words=("close",), program_first=True),
    Command("work_answer", _WORK,
            "The user's answer to one of a Work item's decisions, once given "
            "(work_report.py answer).",
            {"id": _STR, "key": _STR},
            positional=("id", "key"), required=("id", "key"), words=("answer",),
            program_first=True),
    Command("work_answers", _WORK,
            "Every answer the user gave to your decisions, since a time (work_report.py "
            "answers).",
            {"since": {**_STR, "description": "Epoch seconds or an ISO time."}},
            words=("answers",), program_first=True),
    Command("workflow_start", _FLOW,
            "Start a workflow on the workflow monitor, for one Work item "
            "(workflow_report.py start). Prints its id.",
            {"name": _STR, "project": _STR, "phase": _STRS, "work": _STR},
            positional=("name",), required=("name", "project"), words=("start",),
            program_first=True),
    Command("workflow_event", _FLOW,
            "Report one step of a workflow (workflow_report.py event): phase_started, "
            "task_started, task_done, wait_started, blocker_raised, ... (never a decision).",
            {"id": _STR, "kind": _STR, "summary": _STR, "item": _STR, "key": _STR,
             "done": {"type": "integer"}, "total": {"type": "integer"},
             "outcome": {**_STR, "description": "succeeded, failed, cancelled or skipped."},
             "phase": _STR, "title": _STR, "until": _STR,
             "session": {**_STR, "description": "A session attached to it (your own)."},
             "provider": _STR},
            positional=("id", "kind", "summary"), required=("id", "kind", "summary"),
            words=("event",), program_first=True),
    Command("workflow_answer", _FLOW,
            "The user's answer to one of a workflow's decisions, once given "
            "(workflow_report.py answer).",
            {"id": _STR, "key": _STR},
            positional=("id", "key"), required=("id", "key"), words=("answer",),
            program_first=True),
    Command("retrieve_attachment", "jarvis.retrieve_attachment",
            "Retrieve a file a person sent through JARVIS's verified downloader, optionally "
            "inspecting it without opening or running it (python -m "
            "jarvis.retrieve_attachment).",
            {"message": _STR, "attachment": _STR, "inspect": {"type": "boolean"}},
            required=("message", "attachment")),
    Command("language_document", "jarvis.language_document",
            "An architecture document's operations through JARVIS (python -m "
            "jarvis.language_document): capabilities, validate, retrieve, render, ground.",
            {"operation": {"type": "string",
                           "enum": ["capabilities", "validate", "retrieve", "render", "ground"]},
             "source": {**_STR, "description": "validate: absolute path of the YAML document."},
             "arguments": {"type": "object", "description": "The operation's arguments."}},
            positional=("operation",), required=("operation",), flags={"arguments": "--json"}),
)
_COMMANDS = {c.name: c for c in COMMANDS}


def _command(name: str, arguments: dict) -> tuple[bool, str, None]:
    command = _COMMANDS[name]
    ok, text = _run_main(command.module, command.argv(arguments, session_of(arguments)))
    return ok, text, None


# --- package jobs (package_jobs.py) ---------------------------------------

PACKAGE_SPECS: tuple[dict, ...] = (
    {"name": "package_start",
     "description": "Start a package for the user from your handoff (a JSON file): returns "
                    "the job's id at once; the package is researched, written, checked and "
                    "delivered by its own author. Ask package_status how it stands.",
     "inputSchema": {"type": "object", "properties": {
         "handoff": {**_STR, "description": "Absolute path of the handoff JSON."},
         "work": {**_STR, "description": "The Work item it is for."},
         "general": {"type": "boolean", "description": "It is for no Work item."}},
         "required": ["handoff"]}},
    {"name": "package_status",
     "description": "Where a package job stands: its state, and its author's questions "
                    "while it waits on you (answer them with package_answer).",
     "inputSchema": {"type": "object", "properties": {"job": _STR}, "required": ["job"]}},
    {"name": "package_answer",
     "description": "Answer a package job's waiting questions; its author goes on with them.",
     "inputSchema": {"type": "object", "properties": {
         "job": _STR,
         "answers": {"type": "object", "description": "Each question's id and your answer."}},
         "required": ["job", "answers"]}},
)


def _package(name: str, arguments: dict) -> tuple[bool, str, None]:
    from jarvis import package_jobs
    session = session_of(arguments)
    if session:
        # The job signs as this session (agent_keys.signer_for reads it).
        env_names.publish("JARVIS_AGENT_SESSION", session)
    try:
        if name == "package_start":
            result = package_jobs.package_start(str(arguments.get("handoff") or ""),
                                                work=arguments.get("work") or None,
                                                general=bool(arguments.get("general")),
                                                session=session or None)
        elif name == "package_status":
            result = package_jobs.package_status(str(arguments.get("job") or ""))
        else:
            answers = arguments.get("answers")
            if not isinstance(answers, dict):
                return False, "answers must be an object: each question's id and its answer.", None
            result = package_jobs.package_answer(str(arguments.get("job") or ""), answers,
                                                 session=session or None)
    except package_jobs.JobError as refused:
        return False, f"Refused: {refused}", None
    except SystemExit as refused:
        return False, str(refused), None
    except Exception as e:
        return False, f"That failed: {e}", None
    return True, json.dumps(result, ensure_ascii=False), None


# --- the server -----------------------------------------------------------

def tool_specs() -> list[dict]:
    """Every tool an agent is offered."""
    return research_specs() + [c.spec() for c in COMMANDS] + \
        [_with_session(s) for s in PACKAGE_SPECS]


_PACKAGE_TOOLS = {s["name"] for s in PACKAGE_SPECS}


def call(tool: str, arguments: dict) -> tuple[bool, str, dict | None]:
    """One tool call: a command's own main, a package job's function, or
    the brain's tool through JARVIS (which refuses one the agent's rights
    do not name)."""
    if tool in _COMMANDS:
        return _command(tool, arguments)
    if tool in _PACKAGE_TOOLS:
        return _package(tool, arguments)
    return _research(tool, arguments)


def handle(msg: dict) -> dict | None:
    return jarvis_mcp.handle(msg, tools=tool_specs(), call=call, server_name=SERVER_NAME)


def entry(session: str = "") -> dict:
    """This server as an MCP configuration names it (`command`, `args`,
    `env`), with this install's Python, checkout and certificate: what
    session_skill installs, and, given a `session`, what a process JARVIS
    starts for an agent (a package's author) is given to sign as it."""
    from jarvis import data_paths
    env = {"PYTHONPATH": str(data_paths.PACKAGE_DIR.parent)}
    if session:
        env["JARVIS_AGENT_SESSION"] = session
    return {"command": sys.executable,
            "args": ["-P", "-m", "jarvis.agent_mcp",
                     "--cert", str(data_paths.CONFIG_DIR / "cert.pem")],
            "env": env}


def main(argv: list[str] | None = None) -> None:
    global CERT
    parser = argparse.ArgumentParser(prog="python -m jarvis.agent_mcp")
    parser.add_argument("--cert", type=Path)
    CERT = parser.parse_args(argv).cert
    jarvis_mcp.main(handle)


if __name__ == "__main__":
    main()
