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

Measured against Codex 0.154.0 (`TESTED_VERSION`) on this Mac. See
.agents/in-progress/openai-brain.html for the findings this rests on.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import data_paths
import preflight
import usage_store
from brain_provider import (Account, EventSink, ExitSink, SessionInfo, SignIn, TextDelta,
                            ToolStarted, TurnEnded, UsageReading, UsageSeen, UsageUnavailable)

if TYPE_CHECKING:
    from brain import BrainConfig

log = logging.getLogger("jarvis.brain")

SERVICE = "openai"
TESTED_VERSION = "0.154.0"
DEFAULT_MODEL = "gpt-5.6-terra"

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

# Never inherited by the Codex child: with one of these set, Codex would bill
# an API key instead of the ChatGPT plan. The plan is the whole point.
SCRUBBED_ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID",
                     "OPENAI_ORGANIZATION", "OPENAI_PROJECT", "CODEX_API_KEY")

# JARVIS's own MCP server; its results are already walled where they carry
# somebody else's words (see claude_provider.untrusted_tool_source).
OWN_SERVER = "jarvis"
WEB_SEARCH_TOOL = "web_search"

WINDOW_NAMES = {300: "five_hour", 10080: "seven_day"}


def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV_KEYS}
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
        if not isinstance(spec, dict) or not spec.get("command"):
            continue
        table = f"mcp_servers.{_toml_key(name)}"
        lines += ["", f"[{table}]", f"command = {_toml_value(spec['command'])}"]
        if spec.get("args"):
            lines.append(f"args = {_toml_value(list(spec['args']))}")
        lines.append('default_tools_approval_mode = "approve"')
        if spec.get("env"):
            lines += ["", f"[{table}.env]"]
            lines += [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in sorted(spec["env"].items())]
    return "\n".join(lines) + "\n"


def write_config(mcp_config: Optional[Path]) -> Path:
    """Write the Codex home's config.toml, 0600: it holds a copy of every
    `env` block of the user's connections, as mcp.json does."""
    servers = {}
    if mcp_config:
        try:
            servers = json.loads(Path(mcp_config).read_text()).get("mcpServers") or {}
        except (OSError, ValueError) as e:
            log.warning(f"codex: could not read {mcp_config}: {e}")
    path = data_paths.codex_home() / "config.toml"
    path.write_text(config_toml(servers))
    try:
        path.chmod(0o600)
    except OSError as e:                             # pragma: no cover
        log.warning("could not tighten config.toml's permissions: %s", e)
    return path


def features_to_disable(listing: str) -> list[str]:
    """From `codex features list`: every feature that is on and not kept."""
    off = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1] == "true" and parts[0] not in KEEP_FEATURES:
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


# ── the session ───────────────────────────────────────────────────────────

class CodexSession:
    """One `codex app-server` process with one thread: one conversation."""

    def __init__(self, config: "BrainConfig", on_event: EventSink, on_exit: ExitSink):
        self.config = config
        self._on_event = on_event
        self._on_exit = on_exit
        self.codex = getattr(config, "codex_path", None) or shutil.which("codex") or "codex"
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = 0
        self.thread_id: Optional[str] = None
        self._info: Optional[SessionInfo] = None
        self._last_usage: dict = {}
        self.disabled: list[str] = []

    @property
    def alive(self) -> bool:
        proc = self.proc
        return proc is not None and proc.returncode is None and proc.stdin is not None

    def _cmd(self, *args: str) -> list[str]:
        return shlex.split(self.codex) + list(args)

    async def open(self, instructions: str) -> None:
        home = data_paths.codex_home()
        write_config(self.config.mcp_config)
        listing = await asyncio.create_subprocess_exec(
            *self._cmd("features", "list"), env=child_env(), cwd=str(self.config.home),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await listing.communicate()
        self.disabled = features_to_disable(out.decode(errors="replace"))
        argv = self._cmd("app-server") + [a for f in self.disabled for a in ("--disable", f)]
        self.proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(self.config.home), env=child_env(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read_stdout(self.proc))
        self._stderr_task = asyncio.create_task(self._drain_stderr(self.proc))
        try:
            await self._call("initialize", {"clientInfo": {"name": "jarvis", "version": "1"}},
                             timeout=self.config.warmup_timeout)
            self._notify("initialized")
            started = await self._call("thread/start", {
                "model": self.config.model, "cwd": str(self.config.home),
                "approvalPolicy": "never", "sandbox": "read-only",
                "developerInstructions": instructions,
            }, timeout=self.config.warmup_timeout)
            thread = started.get("thread") or {}
            self.thread_id = thread.get("id")
            status = await self._call("mcpServerStatus/list", {}, timeout=self.config.warmup_timeout)
        except (asyncio.TimeoutError, RuntimeError, OSError) as e:
            self.kill()
            raise OSError(f"codex app-server did not start a thread: {e}") from e
        servers, tools, by_server = [], [WEB_SEARCH_TOOL], {}
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
        log.info("codex: thread %s in %s, %d features switched off",
                 self.thread_id, home, len(self.disabled))

    async def send(self, text: str) -> None:
        if not self.alive or not self.thread_id:
            raise BrokenPipeError("the codex app-server is not running")
        if self._info is not None:
            info, self._info = self._info, None
            self._emit(info)
        try:
            await self._call("turn/start", {
                "threadId": self.thread_id, "effort": self.config.effort,
                "input": [{"type": "text", "text": text}],
            }, timeout=self.config.turn_timeout)
        except RuntimeError as e:          # refused: the turn never started
            self._emit(TurnEnded(0, 0, error=str(e)))

    def kill(self) -> None:
        if self.proc is None:
            return
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

    # ── JSON-RPC ─────────────────────────────────────────────────────────
    def _write(self, msg: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise BrokenPipeError("the codex app-server is not running")
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())

    def _notify(self, method: str, params: Optional[dict] = None) -> None:
        self._write({"method": method, "params": params})

    async def _call(self, method: str, params: dict, timeout: float) -> dict:
        self._ids += 1
        rid = self._ids
        future = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        self._write({"method": method, "id": rid, "params": params})
        await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(rid, None)

    def _emit(self, event) -> None:
        try:
            self._on_event(self, event)
        except Exception as e:  # a listener must never kill the reader
            log.warning(f"codex: event listener failed: {e}")

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
                try:
                    msg = json.loads(raw.decode(errors="replace"))
                except ValueError:
                    continue
                try:
                    self._handle(msg)
                except Exception as e:  # one malformed message must not kill the reader
                    log.warning(f"codex: bad message ignored: {e}")
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("the codex app-server ended"))
            code = await proc.wait()
            await self._on_exit(self, code)

    def _handle(self, msg: dict) -> None:
        method = msg.get("method")
        if method is None:                                  # a response
            future = self._pending.get(msg.get("id"))
            if future is not None and not future.done():
                if "error" in msg:
                    err = msg.get("error") or {}
                    future.set_exception(RuntimeError(str(err.get("message") or err)))
                else:
                    future.set_result(msg.get("result") or {})
            return
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
        elif method == "thread/tokenUsage/updated":
            self._last_usage = ((params.get("tokenUsage") or {}).get("last") or {})
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            error = None
            if turn.get("status") not in (None, "completed"):
                err = turn.get("error") or {}
                error = str(err.get("message") or err or f"codex turn {turn.get('status')}")
                log.error(f"codex: turn failed: {error[:300]}")
            u, self._last_usage = self._last_usage, {}
            self._emit(TurnEnded(context_tokens=int(u.get("inputTokens") or 0),
                                 output_tokens=int(u.get("outputTokens") or 0), error=error))
        elif method == "account/rateLimits/updated":
            report, plan = usage_report(params.get("rateLimits") or {})
            try:
                usage_store.record(report, provider={"service": SERVICE, "subscription": plan})
            except Exception as e:
                log.warning(f"codex: could not record usage ({e})")
            blocked = report["status"] == "rejected"
            self._emit(UsageSeen(blocked=blocked, resets_at=report.get("resetsAt"),
                                 window=report.get("rateLimitType"),
                                 summary={"status": report["status"],
                                          "utilization": report.get("utilization"),
                                          "windows": report["unifiedWindows"]},
                                 detail=report))
        elif method == "error":
            log.warning(f"codex: {json.dumps(params)[:300]}")

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


# ── usage, history and checks ─────────────────────────────────────────────

def _codex_binary(path: Optional[str] = None) -> list[str]:
    return shlex.split(path or shutil.which("codex") or "codex")


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
    """`codex` is on PATH, ideally at the version this was measured against."""
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
    if shown != TESTED_VERSION:
        return preflight.Check(
            name="codex_cli", status=preflight.STATUS_WARN,
            message=f"codex {shown} is not the {TESTED_VERSION} JARVIS was measured against.",
            remedy=(f"It may work; if the brain misbehaves, install {TESTED_VERSION}: "
                    f"npm install -g @openai/codex@{TESTED_VERSION}"))
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


class CodexProvider:
    name = SERVICE
    title = "OpenAI (Codex)"
    default_model = DEFAULT_MODEL

    def __init__(self, codex_path: Optional[str] = None):
        self._codex = codex_path or os.getenv("JARVIS_CODEX_PATH") or None
        self._signing_in: dict[str, AppServer] = {}

    def models(self) -> list[str]:
        try:
            with AppServer(self._codex) as server:
                found = server.call("model/list", {}).get("data") or []
        except UsageUnavailable:
            return [DEFAULT_MODEL]
        names = [str(m.get("id") or m.get("model")) for m in found if isinstance(m, dict)]
        return [n for n in names if n and n != "None"] or [DEFAULT_MODEL]

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
            return Account(True, str(acct.get("planType") or ""), "Signed in with ChatGPT.")
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
