"""
codex_provider.py — OpenAI's Codex as JARVIS's brain provider.

One long-lived `codex app-server` process on the user's ChatGPT plan, driven
over stdin and stdout with JSON-RPC: a thread is the conversation, and each
thing the user says is a turn on it. No OpenAI API key is ever used: the
environment is scrubbed of one, so the plan pays.

It runs in a Codex home of its own (`data_paths.codex_home()`), logged in
once with `CODEX_HOME=<that folder> codex login`. The user's own `~/.codex`
(their global AGENTS.md, plugins and MCP servers) never reaches the brain:
measured, a Codex started under it followed the user's own instructions and
reached his Playwright server's `browser_run_code_unsafe`, which can read
files. In its own home, the brain has JARVIS's MCP servers and nothing else.

Codex has no tool allowlist, only switches. So every feature that is on is
switched off except `KEEP_FEATURES`, read fresh from `codex features list`
at every start: a feature a future Codex turns on by default is off here
until someone decides otherwise. The file-editing tool is off by setting
(`include_apply_patch_tool = false`), and the sandbox is read-only. With all
that, the three tool names the model still sees (`exec`, `wait`,
`request_user_input`) run nothing: asked to read a file of random text, it
answered that it could not.

Measured against Codex 0.154.0 on this Mac; any release from `MIN_VERSION`
on is used, as Claude Code is from its minimum (preflight). See
.agents/in-progress/openai-brain.html for the findings this rests on.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from jarvis import settings_service
from jarvis import env_names
from jarvis import claude_env
from jarvis import data_paths
from jarvis import preflight
from jarvis import model_tiers
from jarvis import private_files
from jarvis import roles
from jarvis import usage_store
from jarvis.brain_provider import (Account, EventSink, ExitSink, SessionInfo, SignIn, TextDelta,
                            ToolFailed, ToolStarted, TurnEnded, UsageReading, UsageSeen, UsageUnavailable,
                            identity_text)

if TYPE_CHECKING:
    from jarvis.brain import BrainConfig

log = logging.getLogger("jarvis.brain")

SERVICE = "openai"
# The oldest Codex JARVIS runs on, the brain and agent runs alike: a newer
# release is used as it comes (Codex updates itself), never refused, as
# Claude Code is used from preflight.MIN_CLAUDE_VERSION on. Compared as a
# tuple of ints, never as a string.
MIN_VERSION = (0, 154, 0)
MIN_VERSION_STR = "0.154.0"

# The features left on. Everything else Codex has switched on is switched off
# at every start. `code_mode_host` is the host Codex calls MCP tools through:
# measured, without it no MCP tool can be called at all, and with it on and
# the shell tool off, `exec` still cannot read a file. The rest are plumbing
# (compression, compaction, retries, item ids, the thread store, steering a
# turn, image sizing), none of which is a tool.
KEEP_FEATURES = frozenset({
    "code_mode_host", "compaction_image_budget", "content_item_kinds",
    "enable_request_compression", "remote_compaction_v2", "unbounded_connection_retries",
    "item_ids", "sqlite", "steer", "resize_all_images",
})

# Package roles start in lockdown (approved by Tim 2026-11-19): the
# code-mode host is disabled, so `exec` is listed
# but refuses to run and a role session can neither read a file nor call a
# tool. Each role is then released for its run to exactly its
# `roles.Release` (Tim, 2026-11-20), the definition Claude reads too
# (`role_release`).
ROLE_KEEP_FEATURES = KEEP_FEATURES - {"code_mode_host"}

# The permission profile a writer runs under, measured on Codex 0.171.0:
# reads denied everywhere (`:root`, and the temp folders, which `:minimal`
# would otherwise leave readable) but the platform's own paths (`:minimal`)
# and its run folder; writes only in its run folder; no network. Measured
# with a shell: its home folder and ~/.zshrc refused, a write outside its
# run folder refused, curl could not resolve.
ROLE_PROFILE = "jarvis_role"
# Never inherited by the Codex child: with one of these set, Codex would bill
# an API key instead of the ChatGPT plan. The plan is the whole point.
SCRUBBED_ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID",
                     "OPENAI_ORGANIZATION", "OPENAI_PROJECT", "CODEX_API_KEY")

# JARVIS's own MCP server; its results are already walled where they carry
# somebody else's words (see claude_provider.untrusted_tool_source).
OWN_SERVER = "jarvis"
WEB_SEARCH_TOOL = "web_search"

WINDOW_NAMES = {300: "five_hour", 10080: "seven_day"}


def child_env(*, authoring: bool = False) -> dict[str, str]:
    env = env_names.child(drop=SCRUBBED_ENV_KEYS)
    if not authoring:
        env["CODEX_HOME"] = str(data_paths.codex_home())
    return env


def tool_name(server: str, tool: str) -> str:
    """An MCP tool's name, spelled as the brain's other provider spells it,
    so the server's checks on tool names hold for both."""
    return f"mcp__{server}__{tool}"


def untrusted_tool_source(name: str) -> Optional[str]:
    """What one of its tools read from outside JARVIS, said aloud, or None.
    The same rule as Claude's: the web, and any MCP server the user
    connected, are somebody else's words; JARVIS's own tools are not."""
    if name == WEB_SEARCH_TOOL:
        return "a web page"
    if name.startswith("mcp__") and not name.startswith(f"mcp__{OWN_SERVER}__"):
        parts = name.split("__")
        if len(parts) > 2 and parts[1]:
            return parts[1]
    return None


_FATAL_AUTH_PATTERN = re.compile(r"not logged in|log ?in again|unauthori[sz]ed|\b401\b|"
                                 r"refresh token|sign in", re.IGNORECASE)


def classify_failure(error: Optional[str]) -> Optional[str]:
    """"auth" for a failure no restart can heal (the ChatGPT login), else None."""
    if error and _FATAL_AUTH_PATTERN.search(error):
        return "auth"
    return None


# ── the configuration it starts with ──────────────────────────────────────

# The code-mode host answers every `exec` and `wait` with "Script completed",
# "Script running ..." or "Script failed" (every one of 3,890 such answers in
# the brain's transcripts, Codex 0.154 to 0.166). Any other answer is the host
# itself failing, and then no tool in the script ran at all, as on 29 October
# 2026 when every call came back "failed to read code-mode host message". It
# reaches only the transcript, never an app-server notification.
CODE_MODE_TOOLS = frozenset({"exec", "wait"})
_CODE_MODE_ANSWERS = ("Script ", "aborted by user")


def code_mode_failures(objs: list[dict], names: dict[str, str]) -> list[tuple[str, str]]:
    """(tool, error) for each code-mode call in these transcript lines that
    the host itself failed. `names` maps call ids to tool names and is kept
    up to date across calls, since a call and its answer can be read apart."""
    found = []
    for obj in objs:
        p = obj.get("payload") or {}
        kind = p.get("type")
        if kind in ("custom_tool_call", "function_call"):
            names[str(p.get("call_id"))] = str(p.get("name") or "")
        elif kind in ("custom_tool_call_output", "function_call_output"):
            name = names.pop(str(p.get("call_id")), "")
            out = p.get("output")
            if name not in CODE_MODE_TOOLS or not isinstance(out, str):
                continue
            if not out.startswith(_CODE_MODE_ANSWERS):
                found.append((name, " ".join(out.split())[:300]))
    return found


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value))           # a TOML basic string is JSON-escaped


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def config_toml(mcp_servers: dict) -> str:
    """The Codex home's config.toml: the settings above, and the MCP servers
    out of the brain's mcp.json, each approved as a whole server (the
    equivalent of Claude's whole-server grant)."""
    lines = [
        "# Generated by JARVIS on every start; edits here are lost.",
        "include_apply_patch_tool = false",
        # The persona is the brain home's CLAUDE.md; Codex reads it as its
        # project instructions under that name, so there is one persona file.
        'project_doc_fallback_filenames = ["CLAUDE.md"]',
        # And ONLY that file. Codex reads every project doc from the project
        # root down to its folder, the root found by `.git`, and the brain
        # home sits inside JARVIS's own repository by default: measured, the
        # brain got the repo's developer CLAUDE.md first and then its persona
        # cut off at the 32 KiB cap, model rules and all. No markers makes
        # the brain home its own root; the cap is raised for the persona.
        "project_root_markers = []",
        "project_doc_max_bytes = 131072",
        'web_search = "live"',
    ]
    for name, spec in sorted(mcp_servers.items()):
        if isinstance(spec, dict) and spec.get("command"):
            lines += mcp_server_toml(name, spec)
    return "\n".join(lines) + "\n"


def _servers_of(mcp_config: Optional[Path]) -> dict:
    """The `mcpServers` of an mcp.json, or none when it cannot be read."""
    if not mcp_config:
        return {}
    try:
        return json.loads(Path(mcp_config).read_text()).get("mcpServers") or {}
    except (OSError, ValueError) as e:
        log.warning(f"codex: could not read {mcp_config}: {e}")
        return {}


def config_overrides(mcp_servers: dict) -> list[str]:
    """The MCP servers as `-c` overrides of the user's own Codex home, for
    a package author (which runs on that home, never JARVIS's config.toml):
    each server's command, args and env, approved as a whole server."""
    argv: list[str] = []
    for name, spec in sorted(mcp_servers.items()):
        if not isinstance(spec, dict) or not spec.get("command"):
            continue
        table = f"mcp_servers.{_toml_key(name)}"
        values = {"command": spec["command"], "default_tools_approval_mode": "approve"}
        if spec.get("args"):
            values["args"] = list(spec["args"])
        for key, value in values.items():
            argv += ["-c", f"{table}.{key}={_toml_value(value)}"]
        for key, value in sorted((spec.get("env") or {}).items()):
            argv += ["-c", f"{table}.env.{_toml_key(key)}={_toml_value(value)}"]
    return argv


def mcp_server_toml(name: str, spec: dict) -> list[str]:
    """One MCP server (an mcp.json entry: command, args, env) as config.toml
    lines, approved as a whole server: the brain's, and the agent server
    session_skill installs in the user's own Codex home."""
    table = f"mcp_servers.{_toml_key(name)}"
    lines = ["", f"[{table}]", f"command = {_toml_value(spec['command'])}"]
    if spec.get("args"):
        lines.append(f"args = {_toml_value(list(spec['args']))}")
    lines.append('default_tools_approval_mode = "approve"')
    if spec.get("env"):
        lines += ["", f"[{table}.env]"]
        lines += [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in sorted(spec["env"].items())]
    return lines


def write_config(mcp_config: Optional[Path]) -> Path:
    """Write the Codex home's config.toml, 0600: it holds a copy of every
    `env` block of the user's connections, as mcp.json does."""
    path = data_paths.codex_home() / "config.toml"
    private_files.replace(path, config_toml(_servers_of(mcp_config)).encode("utf-8"))
    return path


def home_servers() -> list[str]:
    """Every MCP server JARVIS's Codex home's config.toml names (the brain's
    own and the user's connections, `write_config`), or none when it cannot
    be read."""
    try:
        text = (data_paths.codex_home() / "config.toml").read_text(encoding="utf-8")
        servers = tomllib.loads(text).get("mcp_servers") or {}
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        log.warning(f"codex: could not read the Codex home's config.toml: {e}")
        return []
    return sorted(str(name) for name in servers) if isinstance(servers, dict) else []


def servers_off(names: list[str]) -> list[str]:
    """Each named server switched off for one app server, as `-c` overrides
    (`mcp_servers.<name>.enabled=false`): the server is never started and
    offers no tool. Measured on Codex 0.171.0: the same switch in a
    thread's `config` does not take, the server already started by the app
    server and its tools offered to the thread (the exec probe found the
    brain's server offered to every role); config.toml is never changed,
    since the brain runs on it."""
    argv: list[str] = []
    for name in names:
        argv += ["-c", f"mcp_servers.{_toml_key(name)}.enabled=false"]
    return argv


def role_release(launch: "roles.Launch", run: Path) -> tuple[frozenset, dict, list[str]]:
    """A role on Codex: the lockdown, then exactly its `roles.Release`, as
    (the features kept on, the thread's config, the app server's `-c`
    overrides). Measured on Codex 0.171.0:

    - the lockdown, every role: `ROLE_KEEP_FEATURES`, subagents off
      (`agents.enabled`: Codex 0.171.0 offers spawn_agent and its
      companions whatever `multi_agent` says, and a long-lived thread's
      subagent runs), no project docs, web search or file-editing tool, the
      sandbox read-only;
    - no server of the home's: every MCP server JARVIS's Codex home names
      (the brain's own among them) switched off for its app server
      (`servers_off`), so a role is offered no tool server but its own;
    - its tools: the code-mode host on, its own tool server the only one
      added. With it on and the shell off, `exec` is to reach the
      configured servers' tools and nothing else of the Mac, which the exec
      probe (codex_role_probe.py) proves for every role with a real turn;
      a role that reads files reads them with its file tools on that
      server (role_files.py), as it does on Claude;
    - a writer: the file-editing tool, under `ROLE_PROFILE`, so a write is
      confined to its run folder and nothing reaches the network.

    The shell is `shell_tool` alone, and it stays off for every role (Tim,
    2026-11-20: never exec or a shell; Codex's code-mode exec only as the
    way its MCP tools are called). `unified_exec` cannot be switched off
    on Codex 0.171.0 (`--disable`, `-c features.unified_exec=false` and the
    config file all leave `codex features list` saying true) and does not
    decide the shell either: each model's catalog entry names its shell's
    kind (`shell_type: unified_exec` for every model listed). Measured in
    real role turns: a role with shell_tool off is offered no
    `exec_command` at all."""
    release = launch.role.release
    keep = ROLE_KEEP_FEATURES
    config: dict = {"project_doc_max_bytes": 0, "project_root_markers": [],
                    "web_search": "disabled", "agents": {"enabled": False},
                    "include_apply_patch_tool": release.writes}
    overrides = servers_off([n for n in home_servers() if n != roles.SERVER_NAME])
    if release.tools and launch.mcp_config is not None:
        keep = keep | {"code_mode_host"}
        overrides += config_overrides(
            {roles.SERVER_NAME: _servers_of(launch.mcp_config).get(roles.SERVER_NAME)})
    if release.writes:
        filesystem = {":root": "deny", ":minimal": "read",
                      ":slash_tmp": "deny", ":tmpdir": "deny", str(run): "write"}
        config["default_permissions"] = ROLE_PROFILE
        config["permissions"] = {ROLE_PROFILE: {"filesystem": filesystem,
                                                "network": {"enabled": False}}}
    return keep, config, overrides


def features_to_disable(listing: str, keep: frozenset = KEEP_FEATURES) -> list[str]:
    """From `codex features list`: every feature that is on and not kept."""
    off = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1] == "true" and parts[0] not in keep:
            off.append(parts[0])
    return off


def usage_report(limits: dict) -> tuple[dict, str]:
    """Codex's rate limits as usage_store records a reading, and the plan."""
    windows = {}
    for slot in ("primary", "secondary"):
        w = limits.get(slot)
        if not isinstance(w, dict) or not isinstance(w.get("usedPercent"), (int, float)):
            continue
        mins = w.get("windowDurationMins")
        key = WINDOW_NAMES.get(mins, f"{mins}_minutes")
        windows[key] = {"utilization": float(w["usedPercent"]) / 100.0,
                        "resetsAt": w.get("resetsAt"), "status": "allowed"}
    reached = limits.get("rateLimitReachedType")
    status = "rejected" if reached else "allowed"
    first = next(iter(windows.items()), (None, {}))
    report = {"status": status, "rateLimitType": first[0],
              "resetsAt": first[1].get("resetsAt"),
              "utilization": first[1].get("utilization"),
              "unifiedWindows": windows}
    return report, str(limits.get("planType") or "")


# ── JSON-RPC ──────────────────────────────────────────────────────────────

class RpcCalls:
    """The requests outstanding on one connection to a Codex app server,
    whatever carries it: the brain's own `codex app-server` over stdio
    (CodexSession) and the running app server's socket (codex_engine). A
    response settles its request; one with an error raises RuntimeError
    with the server's own message; the connection ending fails them all."""

    def __init__(self) -> None:
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = 0

    async def call(self, send, method: str, params, timeout: float) -> dict:
        """Send the request with `send` (an async callable taking the
        message) and wait at most `timeout` seconds for its result."""
        self._ids += 1
        rid = self._ids
        future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        try:
            await send({"method": method, "id": rid, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(rid, None)

    def resolve(self, msg: dict) -> bool:
        """True when `msg` is a response (it has no method), having settled
        the request it answers, if that is still waiting."""
        if msg.get("method") is not None:
            return False
        future = self._pending.get(msg.get("id"))
        if future is not None and not future.done():
            if "error" in msg:
                err = msg.get("error") or {}
                future.set_exception(RuntimeError(str(err.get("message") or err)))
            else:
                future.set_result(msg.get("result") or {})
        return True

    def end(self, why: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError(why))


# ── one app server process ────────────────────────────────────────────────

class _Connection:
    """One `codex app-server` process over stdio and its JSON-RPC: started
    and initialised, written to, read, closed. A response settles its
    request; every other message goes to `on_message`; each line read
    stamps `heard_at`; its end goes to `on_end(code)`."""

    def __init__(self, home: Path, warmup_timeout: float, on_message, on_end):
        self.home = home
        self.warmup_timeout = warmup_timeout
        self._on_message = on_message
        self._on_end = on_end
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._killed = False
        self._reader: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._calls = RpcCalls()
        self.heard_at = time.monotonic()

    @property
    def alive(self) -> bool:
        proc = self.proc
        # Killed is not alive, from the moment it is killed: the exit is
        # only seen once asyncio reaps the child, and until then a brain
        # that has given up on it would still read as running.
        return (proc is not None and not self._killed and proc.returncode is None
                and proc.stdin is not None)

    async def start(self, argv: list[str], env: dict) -> None:
        """The app server running and initialised."""
        self.proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(self.home), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read_stdout(self.proc))
        self._stderr_task = asyncio.create_task(self._drain_stderr(self.proc))
        try:
            await self.call("initialize", {"clientInfo": {"name": "jarvis", "version": "1"}},
                            timeout=self.warmup_timeout)
            self.notify("initialized")
        except (asyncio.TimeoutError, RuntimeError, OSError) as e:
            self.kill()
            raise OSError(f"codex app-server did not start: {e}") from e

    def write(self, msg: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise BrokenPipeError("the codex app-server is not running")
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        self.write({"method": method, "params": params})

    async def _send(self, msg: dict) -> None:
        self.write(msg)
        await self.proc.stdin.drain()

    async def call(self, method: str, params: dict, timeout: float) -> dict:
        return await self._calls.call(self._send, method, params, timeout)

    def kill(self) -> None:
        if self.proc is None:
            return
        self._killed = True
        try:
            self.proc.kill()
        except ProcessLookupError:
            pass

    async def close(self, timeout: float = 5.0) -> None:
        proc = self.proc
        if proc and proc.returncode is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), timeout)
            except asyncio.TimeoutError:
                self.kill()
            except Exception as e:
                log.warning(f"codex: error while stopping: {e}")
                self.kill()
        for task in (self._reader, self._stderr_task):
            if task and not task.done():
                try:
                    await asyncio.wait_for(task, 2.0)
                except asyncio.TimeoutError:
                    task.cancel()
                except Exception as e:
                    log.warning(f"codex: reader task ended with {e}")

    async def _read_stdout(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            while True:
                try:
                    raw = await proc.stdout.readline()
                except ValueError as e:
                    log.warning(f"codex: skipping oversized stdout line ({e})")
                    continue
                if not raw:
                    break
                self.heard_at = time.monotonic()
                try:
                    msg = json.loads(raw.decode(errors="replace"))
                except ValueError:
                    continue
                if self._calls.resolve(msg):                 # a response
                    continue
                try:
                    self._on_message(msg)
                except Exception as e:  # one malformed message must not kill the reader
                    log.warning(f"codex: bad message ignored: {e}")
        finally:
            self._calls.end("the codex app-server ended")
            code = await proc.wait()
            await self._on_end(code)

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        try:
            while True:
                try:
                    raw = await proc.stderr.readline()
                except ValueError:
                    continue
                if not raw:
                    return
                log.debug(f"codex stderr: {raw.decode(errors='replace').rstrip()}")
        except Exception as e:
            log.warning(f"codex: stderr reader stopped: {e}")


def _usage_seen(params: dict) -> UsageSeen:
    """An `account/rateLimits/updated` notification recorded as the openai
    brain's usage, and the event that says it."""
    report, plan = usage_report(params.get("rateLimits") or {})
    try:
        usage_store.record(report, provider={"service": SERVICE, "subscription": plan})
    except Exception as e:
        log.warning(f"codex: could not record usage ({e})")
    blocked = report["status"] == "rejected"
    return UsageSeen(blocked=blocked, resets_at=report.get("resetsAt"),
                     window=report.get("rateLimitType"),
                     summary={"status": report["status"],
                              "utilization": report.get("utilization"),
                              "windows": report["unifiedWindows"]},
                     detail=report)


# ── one app server per role for a step ─────────────────────────────────────

class RoleAppServer:
    """One `codex app-server` shared by every thread of one role in a step
    (the standard design, measured on package-870e45145b2d: 20 app servers
    started for 22 calls of the mark step). Started by the first of the
    role's sessions to open, with the role's lockdown (its features off,
    the home's servers off, its own tool server); each session of the role
    is a thread on it (`CodexSession`), and each thread's notifications go
    to its own session by the thread's id. Closed when the step ends
    (`role_servers`), never by a session's close, which only leaves it."""

    def __init__(self, home: Path, warmup_timeout: float):
        self.home = home
        self.warmup_timeout = warmup_timeout
        self.disabled: list[str] = []
        self.threads: dict[str, "CodexSession"] = {}
        self.starts = 0
        self._conn: Optional[_Connection] = None
        self._lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self._conn is not None and self._conn.alive

    @property
    def heard_at(self) -> float:
        return self._conn.heard_at if self._conn is not None else 0.0

    async def ensure(self, argv_of, env: dict) -> bool:
        """The app server running: started (True) with `argv_of()`'s
        argv when it is not, else left as it is (False)."""
        async with self._lock:
            if self.alive:
                return False
            argv, self.disabled = await argv_of()
            conn = _Connection(self.home, self.warmup_timeout, self._route, self._ended)
            await conn.start(argv, env)
            self._conn = conn
            self.starts += 1
            return True

    def attach(self, session: "CodexSession", old: Optional[str] = None) -> None:
        if old:
            self.threads.pop(old, None)
        if session.thread_id:
            self.threads[session.thread_id] = session

    def detach(self, session: "CodexSession") -> None:
        for tid in [t for t, s in self.threads.items() if s is session]:
            del self.threads[tid]

    def write(self, msg: dict) -> None:
        if self._conn is None:
            raise BrokenPipeError("the codex app-server is not running")
        self._conn.write(msg)

    async def call(self, method: str, params: dict, timeout: float) -> dict:
        if self._conn is None:
            raise BrokenPipeError("the codex app-server is not running")
        return await self._conn.call(method, params, timeout)

    def _route(self, msg: dict) -> None:
        """A message to its thread's session. A request to us is refused
        here (approvals are never); rate limits go to every thread, recorded
        once; another notification naming no thread goes to the one thread
        in a turn, else the one thread there is."""
        method = msg.get("method")
        if "id" in msg:
            self.write({"id": msg["id"], "result": {"decision": "decline"}})
            return
        params = msg.get("params") or {}
        if method == "account/rateLimits/updated":
            event = _usage_seen(params)
            for session in list(self.threads.values()):
                session._heard = time.monotonic()
                session._emit(event)
            return
        tid = params.get("threadId")
        session = self.threads.get(str(tid)) if tid else None
        if session is None and not tid:
            turning = [s for s in self.threads.values() if s._in_turn]
            candidates = turning or list(self.threads.values())
            session = candidates[0] if len(candidates) == 1 else None
        if session is None:
            log.debug("codex: %s for no thread here (%s)", method, tid)
            return
        session._handle(msg)

    async def _ended(self, code) -> None:
        threads, self.threads = list(self.threads.values()), {}
        for session in threads:
            await session._on_exit(session, code)

    async def close(self, timeout: float = 5.0) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close(timeout)


_ROLE_SERVERS: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "codex_role_servers", default=None)


@contextlib.asynccontextmanager
async def role_servers():
    """While it is open, every Codex role session opened in it shares one
    app server per role and lockdown (`RoleAppServer`); each is closed when
    it ends. Outside it, each role session has an app server of its own.
    Yields the servers, by role and lockdown."""
    held: dict = {}
    token = _ROLE_SERVERS.set(held)
    try:
        yield held
    finally:
        _ROLE_SERVERS.reset(token)
        for server in held.values():
            with contextlib.suppress(Exception):
                await server.close()


# ── the session ───────────────────────────────────────────────────────────

class CodexSession:
    """One thread: one conversation. On a `codex app-server` process of its
    own, or, for a role while `role_servers` is open, on its role's one
    shared app server (`RoleAppServer`), whose other threads are the same
    role's other sessions."""

    def __init__(self, config: "BrainConfig", on_event: EventSink, on_exit: ExitSink):
        self.config = config
        self._on_event = on_event
        self._on_exit = on_exit
        self.codex = getattr(config, "codex_path", None)
        self._conn: Optional[_Connection] = None        # its own app server
        self._server: Optional[RoleAppServer] = None    # its role's shared one
        self._attached = False
        # Whether opening it started an app server (one shared and already
        # running is not one), for the run's count of app server starts.
        self.started_server = False
        self._in_turn = False
        self.thread_id: Optional[str] = None
        self._rollout: Optional[Path] = None      # the thread's transcript
        self._rollout_read = 0                    # bytes of it already read
        self._call_names: dict[str, str] = {}
        self._info: Optional[SessionInfo] = None
        self._last_usage: dict = {}
        # The thread's running total, and what its last turn used (input,
        # cached input and output tokens, the total's change over the turn),
        # for a role's record of each turn.
        self._total_usage: dict = {}
        self._turn_from: dict = {}
        self.turn_usage: Optional[dict] = None
        self._reply: Optional[str] = None
        self.disabled: list[str] = []
        self._role_thread: Optional[dict] = None  # how a role's new thread is started
        self._heard = time.monotonic()

    @property
    def heard_at(self) -> float:
        """When it last heard from its brain (time.monotonic()): what a role
        session's idle bound is measured from (sessions.BrainChatSession).
        On its own app server every line counts; on a shared one, its own
        thread's."""
        if self._conn is not None:
            return max(self._heard, self._conn.heard_at)
        return self._heard

    @property
    def proc(self) -> Optional[asyncio.subprocess.Process]:
        return self._conn.proc if self._conn is not None else None

    @property
    def alive(self) -> bool:
        if self._server is not None:
            return self._attached and self._server.alive
        return self._conn is not None and self._conn.alive

    def _cmd(self, *args: str) -> list[str]:
        return _codex_binary(self.codex) + list(args)

    async def _features_off(self, env: dict, keep: frozenset) -> None:
        listing = await asyncio.create_subprocess_exec(
            *self._cmd("features", "list"), env=env, cwd=str(self.config.home),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await listing.communicate()
        self.disabled = features_to_disable(out.decode(errors="replace"), keep)

    async def open(self, instructions: str) -> None:
        if self.config.role is not None:
            return await self._open_role(instructions)
        home = self.config.home if self.config.authoring else data_paths.codex_home()
        env = child_env(authoring=self.config.authoring)
        if not self.config.authoring:
            write_config(self.config.mcp_config)
            await self._features_off(env, KEEP_FEATURES)
        argv = self._cmd("app-server") + [a for f in self.disabled for a in ("--disable", f)]
        if self.config.authoring:
            argv += config_overrides(_servers_of(self.config.mcp_config))
        await self._start(argv, env)
        try:
            settings = {
                "model": self.config.model, "cwd": str(self.config.home),
                "approvalPolicy": "never",
                "sandbox": "workspace-write" if self.config.authoring else "read-only",
                "developerInstructions": instructions,
                # Codex 0.171.0 offers spawn_agent and its companions to
                # every thread whatever the `multi_agent` feature says, and a
                # long-lived thread's subagent runs: switched off, as every
                # feature JARVIS does not keep is.
                "config": {"agents": {"enabled": False}},
            }
            if self.config.resume:
                # The same thread goes on (a package author answered by its
                # sender): its rollout, and so its transcript, is the same.
                started = await self._call("thread/resume", {"threadId": self.config.resume,
                                                             **settings},
                                           timeout=self.config.warmup_timeout)
            else:
                started = await self._call("thread/start", settings,
                                           timeout=self.config.warmup_timeout)
            status = await self._thread_started(started)
        except (asyncio.TimeoutError, RuntimeError, OSError) as e:
            self.kill()
            raise OSError(f"codex app-server did not start a thread: {e}") from e
        self._note_started(started, status, [WEB_SEARCH_TOOL])
        log.info("codex: thread %s in %s, %d features switched off",
                 self.thread_id, home, len(self.disabled))

    async def _start(self, argv: list[str], env: dict) -> None:
        """Its own app server running and initialised."""
        self._conn = _Connection(self.config.home, self.config.warmup_timeout, self._handle,
                                 self._ended)
        self.started_server = True
        await self._conn.start(argv, env)

    async def _ended(self, code) -> None:
        await self._on_exit(self, code)

    async def _thread_started(self, started: dict) -> dict:
        thread = started.get("thread") or {}
        old, self.thread_id = self.thread_id, thread.get("id")
        if self._server is not None:
            self._server.attach(self, old)
            self._attached = True
        self._rollout = Path(thread["path"]) if thread.get("path") else None
        return await self._call("mcpServerStatus/list", {}, timeout=self.config.warmup_timeout)

    def _note_started(self, started: dict, status: dict, tools: list[str]) -> None:
        servers, by_server = [], {}
        for s in (status or {}).get("data") or []:
            name = str(s.get("name") or "")
            if not name:
                continue
            servers.append({"name": name, "status": "connected" if s.get("tools") is not None else "failed"})
            for t in (s.get("tools") or {}):
                tools.append(tool_name(name, str(t)))
                by_server.setdefault(name, []).append(str(t))
        # Reported with the first turn: the brain takes events only from the
        # session it is serving, which it is not until open() returns.
        self._info = SessionInfo(conversation_id=self.thread_id,
                                 model=started.get("model") or self.config.model,
                                 servers=servers, tools=tools, server_tools=by_server)

    async def _open_role(self, instructions: str) -> None:
        """One role's thread (sessions.yaml launch_v1, participants.md
        section 0.3): JARVIS's own Codex home, never the user's (its
        AGENTS.md would reach the role), the role's prompt as the base
        instructions, no project docs, no web search, every server the
        home's config.toml names off (JARVIS's brain server among them),
        locked down and released to exactly its role's `Release`
        (`role_release`)."""
        launch = self.config.role
        role = launch.role
        keep, config, overrides = role_release(launch, self.config.home)
        env = child_env()

        async def argv_of() -> tuple[list[str], list[str]]:
            await self._features_off(env, keep)
            return (self._cmd("app-server")
                    + [a for f in self.disabled for a in ("--disable", f)] + overrides,
                    self.disabled)
        held = _ROLE_SERVERS.get()
        if held is None:
            await self._start((await argv_of())[0], env)
        else:
            # The role's one app server for the step: started by its first
            # session, each later one a thread on it.
            key = (role.name, str(self.config.home), tuple(self._cmd()), keep, tuple(overrides))
            server = held.get(key)
            if server is None:
                server = held[key] = RoleAppServer(self.config.home, self.config.warmup_timeout)
            self._server = server
            self.started_server = await server.ensure(argv_of, env)
            self.disabled = list(server.disabled)
        settings = {
            "model": self.config.model, "cwd": str(self.config.home),
            "approvalPolicy": "never",
            "baseInstructions": instructions, "developerInstructions": None,
            "personality": "none", "config": config,
        }
        if "default_permissions" not in config:
            settings["sandbox"] = "read-only"
        try:
            if self.config.resume:
                started = await self._call("thread/resume", {"threadId": self.config.resume,
                                                             **settings},
                                           timeout=self.config.warmup_timeout)
            else:
                self._role_thread = {**settings, "ephemeral": role.lifetime == roles.ONE_SHOT}
                started = await self._call("thread/start", self._role_thread,
                                           timeout=self.config.warmup_timeout)
            status = await self._thread_started(started)
        except (asyncio.TimeoutError, RuntimeError, OSError) as e:
            self.kill()
            raise OSError(f"codex app-server did not start the {role.name} thread: {e}") from e
        self._note_started(started, status, [])
        log.info("codex: %s thread %s, %d features switched off",
                 role.name, self.thread_id, len(self.disabled))

    async def new_thread(self) -> None:
        """A new conversation for a role on the same app server: a thread
        started as its first was, so the next item is asked with nothing of
        the last (Mark rebuilt for speed, Tim 2026-11-21: one session
        reused across a role's pages, not a new app server a call). Only a
        role's own new thread; raises OSError otherwise or when it fails."""
        if self._role_thread is None or not self.alive:
            raise OSError("only a running role's session starts a new thread")
        try:
            started = await self._call("thread/start", self._role_thread,
                                       timeout=self.config.warmup_timeout)
            status = await self._thread_started(started)
        except (asyncio.TimeoutError, RuntimeError) as e:
            raise OSError(f"codex app-server did not start a new thread: {e}") from e
        self._total_usage, self._turn_from, self._last_usage = {}, {}, {}
        self._rollout_read = 0
        self._note_started(started, status, [])

    async def send(self, text: str, answer_format: Optional[dict] = None) -> None:
        if not self.alive or not self.thread_id:
            raise BrokenPipeError("the codex app-server is not running")
        self._in_turn = True
        if self._info is not None:
            info, self._info = self._info, None
            self._emit(info)
        turn = {"threadId": self.thread_id, "effort": self.config.effort,
                "input": [{"type": "text", "text": text}]}
        launch = self.config.role
        schema = answer_format if answer_format is not None else (
            launch.answer_format if launch is not None else None)
        if schema is not None:
            turn["outputSchema"] = schema
        self._reply = None
        try:
            await self._call("turn/start", turn, timeout=self.config.turn_timeout)
        except RuntimeError as e:          # refused: the turn never started
            self._in_turn = False
            self._emit(TurnEnded(0, 0, error=str(e)))

    def _leave(self) -> None:
        """Off its role's shared app server, which goes on for the others."""
        if self._server is not None:
            self._server.detach(self)
        self._attached = False

    def kill(self) -> None:
        if self._server is not None:
            self._leave()
        elif self._conn is not None:
            self._conn.kill()

    async def close(self, timeout: float = 5.0) -> None:
        if self._server is not None:
            self._leave()
        elif self._conn is not None:
            await self._conn.close(timeout)

    # ── JSON-RPC ─────────────────────────────────────────────────────────
    def _write(self, msg: dict) -> None:
        if self._server is not None:
            self._server.write(msg)
        elif self._conn is not None:
            self._conn.write(msg)
        else:
            raise BrokenPipeError("the codex app-server is not running")

    async def _call(self, method: str, params: dict, timeout: float) -> dict:
        if self._server is not None:
            return await self._server.call(method, params, timeout)
        if self._conn is None:
            raise BrokenPipeError("the codex app-server is not running")
        return await self._conn.call(method, params, timeout)

    def _emit(self, event) -> None:
        try:
            self._on_event(self, event)
        except Exception as e:  # a listener must never kill the reader
            log.warning(f"codex: event listener failed: {e}")

    def _handle(self, msg: dict) -> None:
        """A message from the app server for this thread (a response has
        already settled its request)."""
        self._heard = time.monotonic()
        method = msg.get("method")
        if "id" in msg:                                     # a request to us
            # Approvals are set to never; anything that still asks is refused.
            self._write({"id": msg["id"], "result": {"decision": "decline"}})
            return
        params = msg.get("params") or {}
        if method == "item/agentMessage/delta":
            delta = params.get("delta") or ""
            if delta:
                self._emit(TextDelta(str(delta)))
        elif method == "item/started":
            item = params.get("item") or {}
            kind = item.get("type")
            if kind == "mcpToolCall":
                self._emit(ToolStarted(tool_name(str(item.get("server") or ""),
                                                 str(item.get("tool") or ""))))
            elif kind == "webSearch":
                self._emit(ToolStarted(WEB_SEARCH_TOOL))
            elif kind in ("commandExecution", "fileChange", "dynamicToolCall"):
                self._emit(ToolStarted(str(kind)))
        elif method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                self._reply = item["text"]          # the last one is the turn's answer
            if item.get("type") == "mcpToolCall" and item.get("status") == "failed":
                err = item.get("error") or {}
                self._emit(ToolFailed(tool_name(str(item.get("server") or ""),
                                                str(item.get("tool") or "")),
                                      str(err.get("message") if isinstance(err, dict) else err)
                                      or "the tool call failed"))
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") or {}
            self._last_usage = usage.get("last") or {}
            self._total_usage = usage.get("total") or self._total_usage
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            error = None
            if turn.get("status") not in (None, "completed"):
                err = turn.get("error") or {}
                error = str(err.get("message") or err or f"codex turn {turn.get('status')}")
                log.error(f"codex: turn failed: {error[:300]}")
            for name, failure in self._code_mode_failures():
                log.error(f"codex: {name} failed in the code-mode host: {failure}")
                self._emit(ToolFailed(name, failure))
            u, self._last_usage = self._last_usage, {}
            self.turn_usage = {k: int(self._total_usage.get(k) or 0)
                               - int(self._turn_from.get(k) or 0)
                               for k in ("inputTokens", "cachedInputTokens", "outputTokens")}
            self._turn_from = dict(self._total_usage)
            reply, self._reply = self._reply, None
            self._in_turn = False
            self._emit(TurnEnded(context_tokens=int(u.get("inputTokens") or 0),
                                 output_tokens=int(u.get("outputTokens") or 0), error=error,
                                 reply=None if error else reply))
        elif method == "account/rateLimits/updated":
            self._emit(_usage_seen(params))
        elif method == "error":
            log.warning(f"codex: {json.dumps(params)[:300]}")

    def _code_mode_failures(self) -> list[tuple[str, str]]:
        """Code-mode calls the host failed, from the transcript lines written
        since the last look. Never raises."""
        if self._rollout is None:
            return []
        try:
            with open(self._rollout, "rb") as fh:
                fh.seek(self._rollout_read)
                raw = fh.read()
        except OSError:
            return []
        end = raw.rfind(b"\n") + 1            # a line still being written waits
        self._rollout_read += end
        objs = []
        for line in raw[:end].splitlines():
            try:
                objs.append(json.loads(line))
            except ValueError:
                continue
        return code_mode_failures(objs, self._call_names)


# ── usage, history and checks ─────────────────────────────────────────────

def _codex_binary(path: Optional[str] = None) -> list[str]:
    """The argv `codex` begins with, for the brain and Settings alike.

    One found on PATH is a path, one element whatever it holds: shlex's
    POSIX rules took the backslashes out of the C:\\Users\\...\\npm\\codex.cmd
    shutil.which gives on Windows. A configured one (JARVIS_CODEX_PATH) may
    carry arguments: shell-quoted on macOS and Linux, as always, and on
    Windows split as claude_env.command_prefix splits one, an existing file
    named whole being the one element. Unlike claude's, a .cmd launcher is
    allowed: npm installs codex as one, and every argument here is a fixed
    word (the conversation goes over stdin)."""
    if not path:
        found = shutil.which("codex")
        return [found] if found else ["codex"]
    if sys.platform != "win32":
        return shlex.split(path)
    text = path.strip()
    whole = text[1:-1] if len(text) >= 2 and text[0] == text[-1] == '"' else text
    argv = [whole] if os.path.isfile(whole) else claude_env._windows_split(text)
    if not argv:
        raise FileNotFoundError("no codex executable is configured")
    return argv


class AppServer:
    """A short-lived `codex app-server` in JARVIS's Codex home, for one
    question or one sign-in: blocking, so callers run it in a thread."""

    def __init__(self, codex_path: Optional[str] = None, timeout: float = 20.0):
        try:
            self.proc = subprocess.Popen(_codex_binary(codex_path) + ["app-server"],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, env=child_env())
        except OSError as e:
            raise UsageUnavailable(f"codex could not start ({type(e).__name__})")
        self.timeout = timeout
        self._ids = 0
        self.call("initialize", {"clientInfo": {"name": "jarvis-settings", "version": "1"}})
        self._send({"method": "initialized", "params": None})

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _next(self, deadline: float) -> dict:
        if time.monotonic() > deadline:
            raise UsageUnavailable("codex did not answer in time")
        line = self.proc.stdout.readline()
        if not line:
            raise UsageUnavailable("codex ended before answering")
        try:
            return json.loads(line)
        except ValueError:
            return {}

    def call(self, method: str, params) -> dict:
        self._ids += 1
        rid = self._ids
        self._send({"method": method, "id": rid, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            msg = self._next(deadline)
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise UsageUnavailable(f"codex answered an error for {method}")
                return msg.get("result") or {}

    def wait_for(self, method: str, timeout: float) -> dict:
        """The params of the next notification called `method`."""
        deadline = time.monotonic() + timeout
        while True:
            msg = self._next(deadline)
            if msg.get("method") == method and "id" not in msg:
                return msg.get("params") or {}

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class CodexUsage:
    """The plan's limits, read from a short-lived app-server between turns."""

    def __init__(self, codex_path: Optional[str] = None, timeout: float = 20.0):
        self._codex = codex_path
        self._timeout = timeout

    def read(self) -> UsageReading:
        with AppServer(self._codex, self._timeout) as server:
            limits = server.call("account/rateLimits/read", None).get("rateLimits") or {}
        report, plan = usage_report(limits)
        return UsageReading(service=SERVICE, subscription=plan, report=report)


# Codex puts its own context into the conversation as user messages; they
# are not something the user said.
_INJECTED = re.compile(r"^\s*(<[a-z_]+>|# [A-Za-z.]+ instructions for )")


class CodexTranscripts:
    """The brain's own Codex transcripts: `sessions/YYYY/MM/DD/rollout-*.jsonl`
    in its Codex home."""

    suffix = ".jsonl"

    def folders(self) -> list[Path]:
        d = data_paths.codex_home() / "sessions"
        return [d] if d.is_dir() else []

    def message(self, record: dict) -> Optional[tuple[str, str]]:
        if not isinstance(record, dict) or record.get("type") != "response_item":
            return None
        payload = record.get("payload") or {}
        if payload.get("type") != "message" or payload.get("role") not in ("user", "assistant"):
            return None
        content = payload.get("content")
        if not isinstance(content, list):
            return None
        text = "\n".join(b["text"] for b in content if isinstance(b, dict)
                         and b.get("type") in ("input_text", "output_text")
                         and isinstance(b.get("text"), str)).strip()
        if not text or (payload["role"] == "user" and _INJECTED.match(text)):
            return None
        return payload["role"], text


async def check_codex_cli(timeout: float = preflight.DEFAULT_CHECK_TIMEOUT) -> preflight.Check:
    """`codex` is on PATH, at `MIN_VERSION` or newer."""
    codex = shutil.which("codex")
    if not codex:
        return preflight.Check(
            name="codex_cli", status=preflight.STATUS_FAIL, message="`codex` is not on PATH.",
            remedy="Install Codex: npm install -g @openai/codex")
    rc, out, err = await preflight._run_subprocess(codex, "--version", timeout=timeout)
    version = preflight._parse_version(out) or preflight._parse_version(err)
    if rc != 0 or version is None:
        return preflight.Check(
            name="codex_cli", status=preflight.STATUS_WARN,
            message=f"`codex --version` gave no version: {(err or out).strip()[:120]!r}",
            remedy="Run `codex --version` yourself to see what's wrong.")
    shown = ".".join(str(p) for p in version)
    if version < MIN_VERSION:
        return preflight.Check(
            name="codex_cli", status=preflight.STATUS_WARN,
            message=f"codex {shown} is older than the {MIN_VERSION_STR} JARVIS needs.",
            remedy="Update Codex: npm install -g @openai/codex")
    return preflight.Check(name="codex_cli", status=preflight.STATUS_OK,
                           message=f"codex {shown} on PATH.")


async def check_codex_login(timeout: float = preflight.DEFAULT_CHECK_TIMEOUT) -> preflight.Check:
    """JARVIS's own Codex home is logged in with ChatGPT, not an API key."""
    codex = shutil.which("codex")
    home = data_paths.codex_home()
    remedy = f"Log JARVIS's Codex in once, in a terminal: CODEX_HOME={home} codex login"
    if not codex:
        return preflight.Check(name="codex_login", status=preflight.STATUS_FAIL,
                               message="`codex` is not on PATH.", remedy=remedy)
    rc, out, err = await preflight._run_subprocess(codex, "login", "status",
                                                   timeout=timeout, env=child_env())
    said = (out + err).strip()
    if "chatgpt" in said.lower() and "logged in" in said.lower():
        return preflight.Check(name="codex_login", status=preflight.STATUS_OK,
                               message="JARVIS's Codex is logged in with ChatGPT.")
    if "api key" in said.lower():
        return preflight.Check(name="codex_login", status=preflight.STATUS_FAIL,
                               message="JARVIS's Codex is logged in with an API key, not ChatGPT.",
                               remedy=remedy)
    return preflight.Check(name="codex_login", status=preflight.STATUS_FAIL,
                           message="JARVIS's Codex is not logged in.", remedy=remedy)


def fallback_model(listed: list[str]) -> Optional[str]:
    """The model Codex runs on when none is chosen: the sweep's first for
    the default tier over the models listed now (model_tiers), never a
    fixed name; None when nothing listed fits, and Codex's own default
    is used."""
    found = model_tiers.sweep("codex", model_tiers.DEFAULT_TIER, listed)
    return found[0] if found else None


class CodexProvider:
    name = SERVICE
    title = "OpenAI (Codex)"

    def __init__(self, codex_path: Optional[str] = None):
        self._codex = codex_path or settings_service.value("codex_path") or None
        self._signing_in: dict[str, AppServer] = {}

    @property
    def default_model(self) -> Optional[str]:
        return fallback_model(self.models())

    def models(self) -> list[str]:
        """The app server's live model/list; none when it cannot be asked."""
        try:
            with AppServer(self._codex) as server:
                found = server.call("model/list", {}).get("data") or []
        except UsageUnavailable:
            return []
        names = [str(m.get("id") or m.get("model")) for m in found if isinstance(m, dict)]
        return [n for n in names if n and n != "None"]

    async def account(self) -> Account:
        def read() -> Account:
            try:
                with AppServer(self._codex) as server:
                    acct = server.call("account/read", {}).get("account")
            except UsageUnavailable as e:
                return Account(False, "", f"Codex could not be asked ({e}).")
            if not isinstance(acct, dict):
                return Account(False, "", "Not signed in.")
            if acct.get("type") != "chatgpt":
                return Account(False, "", "Signed in with an API key, not ChatGPT.")
            return Account(True, str(acct.get("planType") or ""), "Signed in with ChatGPT.",
                           email=identity_text(acct.get("email")))
        return await asyncio.to_thread(read)

    async def start_sign_in(self) -> Optional[SignIn]:
        """Codex's own ChatGPT sign-in, in JARVIS's Codex home. The app-server
        that started it stays up until it finishes: it serves the sign-in's
        local callback."""
        def start() -> SignIn:
            server = AppServer(self._codex)
            try:
                started = server.call("account/login/start", {"type": "chatgpt"})
            except Exception:
                server.close()
                raise
            sign_in = SignIn(url=str(started.get("authUrl") or ""),
                             sign_in_id=str(started.get("loginId") or ""))
            self._signing_in[sign_in.sign_in_id] = server
            return sign_in
        return await asyncio.to_thread(start)

    async def sign_in_finished(self, sign_in_id: str, timeout: float) -> bool:
        server = self._signing_in.pop(sign_in_id, None)
        if server is None:
            return False

        def wait() -> bool:
            try:
                done = server.wait_for("account/login/completed", timeout)
                return bool(done.get("success"))
            except UsageUnavailable:
                return False
            finally:
                server.close()
        return await asyncio.to_thread(wait)

    def session(self, config: "BrainConfig", on_event: EventSink, on_exit: ExitSink) -> CodexSession:
        return CodexSession(config, on_event, on_exit)

    def usage(self) -> CodexUsage:
        return CodexUsage(self._codex)

    def history(self) -> CodexTranscripts:
        return CodexTranscripts()

    def preflight(self) -> tuple[tuple, tuple]:
        return ((check_codex_cli, check_codex_login), ())

    def failure_kind(self, error: Optional[str]) -> Optional[str]:
        return classify_failure(error)

    def reads_foreign(self, tool: str) -> Optional[str]:
        return untrusted_tool_source(tool)
