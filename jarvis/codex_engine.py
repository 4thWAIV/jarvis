"""codex_engine.py — Codex agents as runs, on the Codex app server already
running on this Mac.

The run pipeline's second engine (run_executor.RunEngine). A run is a thread
on the app server JARVIS already watches (`codex_runtime`), reached on its
Unix WebSocket and spoken to with the same JSON-RPC as the Codex brain
(`codex_provider.RpcCalls`):

  start   `thread/start` in the run's folder, on its model, never asking and
          with no sandbox (`approvalPolicy: never`, `danger-full-access`), as
          a Claude run skips permissions; then `turn/start` with the prompt
  retry   `thread/fork` of the run it picks up from, then `turn/start`
  events  each item and turn notification for its thread, as the same run
          events a Claude run writes (`neutral`), so the page, the outcome
          check and the dashboard read both alike
  steer   `turn/steer` into the turn under way
  cancel  `turn/interrupt`
  end     `turn/completed`; an error; the wall-clock or idle bound; or the
          connection lost for longer than `LOST_BOUND_SEC`

It runs in the user's own Codex home (his login, his rules, his tools), as a
Claude run runs on his Claude login. On connecting it reads the app server's
version and refuses to start a run on one this was not checked against
(`TESTED_VERSIONS`), saying so. If the connection drops it connects again
and resumes each running thread (`thread/resume`); a turn that ended while
it was away is read back (`thread/turns/list`), and one it cannot get back
within the bound ends the run failed, never stuck.

The executor owns the rest (run_executor.RunExecutor): the row written first,
the permit, the terminal-state rule and every write before its notice.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from jarvis import (brains_config, codex_provider, codex_runtime, codex_sessions, session_steer,
                    usage_store, worker_activity)

log = logging.getLogger("jarvis.codex_engine")

NAME = "codex"
PROVIDER = codex_provider.SERVICE

# The app server releases whose generated protocol schema (`codex app-server
# generate-json-schema`) this client was checked against: every request it
# sends and every field it reads is in both. Any other is refused until it is
# checked.
TESTED_VERSIONS = frozenset({"0.154.0", "0.167.1"})

# Same as a Claude run (run_executor's --dangerously-skip-permissions): a run
# is unattended, so it never asks, and it has no sandbox (his answer,
# "codex-freedom: same-as-claude").
APPROVAL_POLICY = "never"
SANDBOX = "danger-full-access"

# How long one request to the app server may take.
CALL_TIMEOUT_SEC = 30.0
# How long a running run may be without the app server before it is failed.
# The app server restarting (it updates itself) takes seconds; two minutes is
# room for that and still frees the permit the same afternoon.
LOST_BOUND_SEC = 120.0
# How often a lost connection is tried again while a run waits on it.
RECONNECT_EVERY_SEC = 2.0
# How often a run's driver wakes to look at its bounds when nothing arrives.
POLL_SEC = 1.0

_VERSION = re.compile(r"/(\d+\.\d+\.\d+)")

# Notifications about a thread, besides its items: what a run ends on.
_ENDS = ("turn/completed", "thread/closed")


class Untested(Exception):
    """The app server is a version this was not checked against."""


def version_of(initialized: dict) -> Optional[str]:
    """The app server's release, from `initialize`'s `userAgent`
    ("codex_cli_rs/0.167.1 (Mac OS ...) ...")."""
    found = _VERSION.search(str(initialized.get("userAgent") or ""))
    return found.group(1) if found else None


def check_version(initialized: dict) -> str:
    version = version_of(initialized)
    if version not in TESTED_VERSIONS:
        raise Untested(
            f"the Codex app server is {version or 'of an unknown version'}, which JARVIS "
            f"has not been checked against (it has been with "
            f"{', '.join(sorted(TESTED_VERSIONS))}); no Codex run is started on it")
    return version


# ── its items as run events ───────────────────────────────────────────────
#
# The run events every reader already knows are Claude's stream-json:
# `system`/init, `assistant` turns of text and tool_use blocks, and `result`.
# A Codex item is said in the same words, so `stream_parser.assess_outcome`
# (did it change anything, did it stop to ask), `summarize_assistant` and the
# dashboard read a Codex run exactly as a Claude one. Its tools are named as
# Claude names the same act, so the read-only set is the same set.

def _assistant(*blocks: dict) -> dict:
    return {"type": "assistant", "message": {"content": list(blocks)}}


def _tool(name: str, **inputs) -> dict:
    return _assistant({"type": "tool_use", "name": name, "input": inputs})


def neutral(item: dict) -> Optional[dict]:
    """A completed Codex item as a run event, or None for one nobody reads
    (its reasoning, its plan, the prompt echoed back)."""
    kind = item.get("type")
    if kind == "agentMessage":
        text = str(item.get("text") or "")
        return _assistant({"type": "text", "text": text}) if text else None
    if kind == "commandExecution":
        return _tool("Bash", command=str(item.get("command") or ""))
    if kind == "fileChange":
        paths = [str(c.get("path") or "") for c in item.get("changes") or [] if isinstance(c, dict)]
        return _tool("Edit", file_path=", ".join(p for p in paths if p))
    if kind == "mcpToolCall":
        return _tool(codex_provider.tool_name(str(item.get("server") or ""),
                                              str(item.get("tool") or "")))
    if kind == "dynamicToolCall":
        return _tool(str(item.get("tool") or "tool"))
    if kind == "webSearch":
        return _tool("WebSearch", query=str(item.get("query") or ""))
    if kind == "imageView":
        return _tool("Read", file_path=str(item.get("path") or ""))
    return None


def _usage(token_usage: dict) -> dict:
    """The thread's running totals as the run's token columns."""
    total = (token_usage or {}).get("total") or {}

    def n(key):
        value = total.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    return {"input_tokens": n("inputTokens"), "output_tokens": n("outputTokens"),
            "cache_read_tokens": n("cachedInputTokens"),
            "cache_creation_tokens": n("cacheWriteInputTokens")}


def _error_words(error) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("additionalDetails") or "")
    return str(error or "")


# ── one connection to the app server ──────────────────────────────────────

class AppServer:
    """One connection to the running app server's socket: JSON-RPC calls
    (`codex_provider.RpcCalls`) and every other message handed on."""

    def __init__(self, path: Path, on_message: Callable[[dict], None],
                 on_lost: Callable[[], None]):
        self.path = path
        self._on_message = on_message
        self._on_lost = on_lost
        self._calls = codex_provider.RpcCalls()
        self._ws = None
        self._reader: Optional[asyncio.Task] = None
        self.version: Optional[str] = None

    async def open(self) -> str:
        from websockets.asyncio.client import unix_connect
        self._ws = await asyncio.wait_for(
            unix_connect(str(self.path), uri="ws://localhost", max_size=16 * 1024 * 1024,
                         close_timeout=1), CALL_TIMEOUT_SEC)
        self._reader = asyncio.create_task(self._read())
        try:
            initialized = await self.call("initialize", {
                "clientInfo": {"name": "jarvis_runs", "title": "JARVIS runs", "version": "1"},
                "capabilities": {"experimentalApi": True, "requestAttestation": False}})
            self.version = check_version(initialized)
            await self.send({"method": "initialized"})
        except BaseException:
            await self.close()
            raise
        return self.version

    async def send(self, msg: dict) -> None:
        await self._ws.send(json.dumps(msg))

    async def call(self, method: str, params: dict) -> dict:
        return await self._calls.call(self.send, method, params, CALL_TIMEOUT_SEC)

    @property
    def alive(self) -> bool:
        return self._reader is not None and not self._reader.done()

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict) or self._calls.resolve(msg):
                    continue
                try:
                    self._on_message(msg)
                except Exception:
                    log.warning("codex engine: a message was not handled", exc_info=True)
        except Exception as e:                       # the connection went
            log.warning("codex engine: the app server connection ended (%s)", type(e).__name__)
        finally:
            self._calls.end("the Codex app server connection ended")
            self._on_lost()

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()


# ── a run on it ───────────────────────────────────────────────────────────

@dataclass
class _Run:
    run_id: str
    thread_id: Optional[str] = None
    turn_id: Optional[str] = None
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    heard_at: float = field(default_factory=time.monotonic)
    lost_at: Optional[float] = None
    activity: dict = field(default_factory=dict)


class CodexEngine:
    """Runs as threads on the running Codex app server (module docstring)."""

    name = NAME
    provider = PROVIDER

    def __init__(self, ex, home: Callable[[], Path] = codex_sessions.codex_home, *,
                 lost_bound_sec: float = LOST_BOUND_SEC,
                 reconnect_every_sec: float = RECONNECT_EVERY_SEC,
                 poll_sec: float = POLL_SEC):
        self._ex = ex
        self._home = home
        self._lost_bound = lost_bound_sec
        self._reconnect_every = reconnect_every_sec
        self._poll = poll_sec
        self._server: Optional[AppServer] = None
        self._connecting = asyncio.Lock()
        self._runs: dict[str, _Run] = {}
        self._threads: dict[str, str] = {}          # thread id -> run id
        self._reconnector: Optional[asyncio.Task] = None
        self._closed = False

    def resolve_model(self, model: str | None) -> str:
        """Explicit, else the Codex brain's model in Settings, else Codex's
        default: never left to the app server's own default."""
        return model or brains_config.model_for(PROVIDER) or codex_provider.DEFAULT_MODEL

    # -- the connection ---------------------------------------------------

    async def _connection(self) -> AppServer:
        async with self._connecting:
            if self._server is not None and self._server.alive:
                return self._server
            server = AppServer(codex_runtime.control_socket(self._home()),
                               self._on_message, self._on_lost)
            await server.open()
            self._server = server
            log.info("codex engine: connected to the app server %s", server.version)
            return server

    def _on_lost(self) -> None:
        if self._closed:
            return
        now = time.monotonic()
        for run in self._runs.values():
            if run.lost_at is None:
                run.lost_at = now
        if self._runs and (self._reconnector is None or self._reconnector.done()):
            self._reconnector = asyncio.get_running_loop().create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Connect again and pick each running thread back up, until every
        run has its connection back or has ended."""
        while any(run.lost_at is not None for run in self._runs.values()):
            await asyncio.sleep(self._reconnect_every)
            try:
                server = await self._connection()
            except Untested as e:
                # A different app server came back: its runs cannot go on.
                for run in list(self._runs.values()):
                    run.inbox.put_nowait(("_failed", {"error": str(e)}))
                return
            except Exception as e:
                log.info("codex engine: the app server is not back yet (%s)", type(e).__name__)
                continue
            for run in list(self._runs.values()):
                if run.lost_at is not None and run.thread_id:
                    await self._resume(server, run)

    async def _resume(self, server: AppServer, run: _Run) -> None:
        try:
            await server.call("thread/resume", {"threadId": run.thread_id, "excludeTurns": True})
            latest = await server.call("thread/turns/list", {"threadId": run.thread_id, "limit": 1})
        except Exception as e:
            log.info("codex engine: run %s not resumed yet (%s)", run.run_id, e)
            return
        run.lost_at = None
        run.heard_at = time.monotonic()
        turns = latest.get("data") or []
        turn = turns[0] if turns else None
        if turn is None or turn.get("id") != run.turn_id:
            # Its turn is gone from the thread: lost with the old app server.
            run.inbox.put_nowait(("_failed", {"error": "its turn was lost when the Codex app "
                                                       "server restarted"}))
        elif turn.get("status") != "inProgress":
            # It ended while JARVIS was away: settled as if heard.
            run.inbox.put_nowait(("turn/completed", {"threadId": run.thread_id, "turn": turn}))
        log.info("codex engine: run %s resumed on thread %s", run.run_id, run.thread_id)

    def _on_message(self, msg: dict) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "account/rateLimits/updated":
            _record_limits(params.get("rateLimits") or {})
            return
        run_id = self._threads.get(str(params.get("threadId") or ""))
        run = self._runs.get(run_id) if run_id else None
        if "id" in msg:
            # A request to answer. Only ever for a thread JARVIS started
            # (another client's thread is that client's to answer); its
            # runs never ask, so one that does anyway is declined.
            if run is not None:
                asyncio.get_running_loop().create_task(
                    self._answer(msg["id"]))
            return
        if run is None:
            return
        run.heard_at = time.monotonic()
        found = codex_runtime.event_evidence(msg, time.time(), run.activity)
        if found:
            _sid, run.activity = found
            worker_activity.record_runtime(PROVIDER, run.thread_id, run.activity)
        run.inbox.put_nowait((method, params))

    async def _answer(self, request_id) -> None:
        try:
            await self._server.send({"id": request_id, "result": {"decision": "decline"}})
        except Exception:
            log.debug("codex engine: an answer was not sent", exc_info=True)

    # -- a run ------------------------------------------------------------

    async def drive(self, run_id: str, prompt: str, project_path: str,
                    resume_from: str | None, timeout_sec: float,
                    model: str | None = None) -> None:
        ex = self._ex
        failed = ex._store.RunStatus.FAILED
        run = self._runs[run_id] = _Run(run_id)
        try:
            server = await self._connection()
        except Untested as e:
            await ex._finish(run_id, failed, error=str(e))
            return
        except Exception as e:
            await ex._finish(run_id, failed,
                             error=f"could not reach the Codex app server: {e or type(e).__name__}")
            return
        if run_id in ex._cancelling:
            await ex._finish(run_id, ex._store.RunStatus.CANCELLED)
            return
        settings = {"cwd": project_path or None, "model": model,
                    "approvalPolicy": APPROVAL_POLICY, "sandbox": SANDBOX}
        try:
            if resume_from:
                original = await asyncio.to_thread(ex._store.get_run, resume_from)
                thread = (original or {}).get("engine_session")
                if not thread:
                    await ex._finish(run_id, failed, error="the run it picks up from has no "
                                                           "Codex thread to fork")
                    return
                started = await server.call("thread/fork", {**settings, "threadId": thread,
                                                            "excludeTurns": True})
            else:
                started = await server.call("thread/start", settings)
            run.thread_id = str((started.get("thread") or {}).get("id") or "")
            if not run.thread_id:
                raise RuntimeError("the app server started no thread")
            self._threads[run.thread_id] = run_id
            on_model = str(started.get("model") or model or "")
            # The thread is the run's session: on record before anything
            # about it is announced.
            row = await asyncio.to_thread(ex._start_write, run_id, None,
                                          engine_session=run.thread_id, model=on_model)
            ex._publish({"type": "run_started", "run": row})
            turn = await server.call("turn/start", {
                "threadId": run.thread_id, "input": [{"type": "text", "text": prompt}]})
            run.turn_id = str((turn.get("turn") or {}).get("id") or "") or None
        except RuntimeError as e:                      # the app server refused
            await ex._finish(run_id, failed, error=f"the Codex app server refused the run: {e}")
            return
        except asyncio.TimeoutError:
            await ex._finish(run_id, failed, error="the Codex app server did not answer in time")
            return
        await self._follow(run, timeout_sec, on_model, project_path)

    async def _follow(self, run: _Run, timeout_sec: float, model: str, cwd: str) -> None:
        """The run's events, to its end: the turn completing, an error, or a
        bound."""
        ex = self._ex
        status = ex._store.RunStatus
        run_id = run.run_id
        events = await self._events(run_id)
        events.add("system", {"type": "system", "subtype": "init", "model": model,
                              "cwd": cwd, "session_id": run.thread_id},
                   json.dumps({"type": "system", "subtype": "init", "model": model,
                               "cwd": cwd, "session_id": run.thread_id}))
        deadline = time.monotonic() + timeout_sec
        said = ""             # its last message: the run's result
        error = ""            # the last error it reported that it will not retry
        outcome = None
        try:
            while outcome is None:
                try:
                    method, params = await asyncio.wait_for(run.inbox.get(), self._poll)
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if now >= deadline:
                        await self._interrupt(run)
                        outcome = (status.TIMED_OUT, {"error": f"exceeded timeout of {timeout_sec}s"})
                    elif run.lost_at is not None and now - run.lost_at >= self._lost_bound:
                        outcome = (status.FAILED, {"error": (
                            f"lost the Codex app server for {self._lost_bound:.0f}s and could "
                            f"not pick the run back up")})
                    elif run.lost_at is None and now - run.heard_at >= ex._idle_sec:
                        await self._interrupt(run)
                        outcome = (status.TIMED_OUT, {"error": (
                            f"codex produced nothing for {ex._idle_sec}s and was still "
                            f"running; interrupted")})
                    continue
                if method == "item/completed":
                    event = neutral(params.get("item") or {})
                    if event is not None:
                        events.add("assistant", event, json.dumps(event))
                        if (params.get("item") or {}).get("type") == "agentMessage":
                            said = str(params["item"].get("text") or "") or said
                elif method == "thread/tokenUsage/updated":
                    await events.flush()
                    await asyncio.to_thread(ex._store.update_run, run_id,
                                            **_usage(params.get("tokenUsage") or {}))
                    await ex._publish_run_updated(run_id)
                elif method == "error":
                    if not params.get("willRetry"):
                        error = _error_words(params.get("error")) or error
                elif method == "_failed":
                    outcome = (status.FAILED, {"error": params.get("error") or "failed"})
                elif method in _ENDS:
                    turn = params.get("turn") or {}
                    if method == "turn/completed" and run.turn_id and turn.get("id") not in (None, run.turn_id):
                        continue                       # an earlier turn on a forked thread
                    outcome = await self._ended(run, method, turn, said, error, events)
                if events.due():
                    await events.flush()
        finally:
            await events.flush()
        if run_id in ex._cancelling:
            # However it came to an end, he asked for it to stop.
            outcome = (status.CANCELLED, {})
        await ex._finish(run_id, outcome[0], **outcome[1])
        await self._read_limits()

    async def _read_limits(self) -> None:
        """The plan's limits once a run's turn is over, so OpenAI's usage
        (and its token stress) is as of its last work."""
        server = self._server
        if server is None or not server.alive:
            return
        try:
            _record_limits((await server.call("account/rateLimits/read", None))
                           .get("rateLimits") or {})
        except Exception as e:
            log.info("codex engine: the plan's limits were not read (%s)", e)

    async def _ended(self, run: _Run, method: str, turn: dict, said: str, error: str,
                     events) -> tuple:
        ex = self._ex
        status = ex._store.RunStatus
        state = turn.get("status") if method == "turn/completed" else "closed"
        failed = state != "completed"
        why = (_error_words(turn.get("error")) or error
               or {"interrupted": "the turn was interrupted",
                   "closed": "the app server closed the thread"}.get(state)
               or f"the turn ended {state}")
        result = {"type": "result", "subtype": "success" if not failed else "error",
                  "is_error": failed, "result": said, "num_turns": 1}
        events.add("result", result, json.dumps(result))
        await events.flush()
        await asyncio.to_thread(ex._store.update_run, run.run_id, result_text=said[:20000],
                                num_turns=1, is_error=int(failed))
        await ex._publish_run_updated(run.run_id)
        if failed:
            return (status.FAILED, {"error": why})
        return (status.SUCCEEDED, {})

    async def _events(self, run_id: str):
        from jarvis.run_executor import RunEvents
        return await RunEvents.open(self._ex, run_id)

    async def _interrupt(self, run: _Run) -> None:
        if self._server is None or not run.thread_id or not run.turn_id:
            return
        try:
            await self._server.call("turn/interrupt", {"threadId": run.thread_id,
                                                       "turnId": run.turn_id})
        except Exception as e:
            log.info("codex engine: run %s not interrupted (%s)", run.run_id, e)

    async def stop(self, run_id: str) -> dict | None:
        """Interrupt the turn of a run being cancelled and end its driver;
        None when nothing of it is going here."""
        run = self._runs.get(run_id)
        if run is None:
            return None
        await self._interrupt(run)
        run.inbox.put_nowait(("_failed", {"error": "cancelled"}))
        return {}

    async def steer(self, run_id: str, text: str) -> str:
        """`turn/steer`: the words join the turn under way."""
        run = self._runs.get(run_id)
        if not text or not text.strip():
            return session_steer.REFUSED
        if run is None or not run.thread_id or not run.turn_id:
            return session_steer.failed("its turn has not started")
        if self._server is None or not self._server.alive:
            return session_steer.failed("the Codex app server is not connected")
        try:
            await self._server.call("turn/steer", {
                "threadId": run.thread_id, "expectedTurnId": run.turn_id,
                "input": [{"type": "text", "text": text.strip()}]})
        except (RuntimeError, asyncio.TimeoutError) as e:
            return session_steer.failed(f"turn/steer was refused: {e}")
        return session_steer.SENT

    async def release(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run is not None:
            await self._interrupt(run)

    def forget(self, run_id: str) -> None:
        run = self._runs.pop(run_id, None)
        if run is not None and run.thread_id:
            self._threads.pop(run.thread_id, None)
            worker_activity.forget_managed(PROVIDER, run.thread_id)

    async def close(self) -> None:
        """At shutdown: the connection closed and not tried again. Its runs
        are failed by the server's next start (run_store.sweep_stale_runs)."""
        self._closed = True
        if self._reconnector is not None:
            self._reconnector.cancel()
        if self._server is not None:
            await self._server.close()


# ── its plan's limits ────────────────────────────────────────────────────

def _record_limits(limits: dict) -> None:
    """A rate-limit reading from the app server, kept under OpenAI's usage
    as the Codex brain keeps its own (usage_store)."""
    if not limits:
        return
    report, plan = codex_provider.usage_report(limits)
    try:
        usage_store.record(report, provider={"service": PROVIDER, "subscription": plan})
    except Exception as e:
        log.warning("codex engine: could not record usage (%s)", e)

