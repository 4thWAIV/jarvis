"""
claude_provider.py — Claude Code as JARVIS's brain provider.

One long-lived `claude -p` process on the user's Claude subscription, fed
over stdin as stream-json; no Anthropic API. Lean flags (no user hooks, no
user MCP servers, coding tools disallowed) keep a turn at ~13k context tokens
with sub-second first tokens once warm.

Everything here is Claude's own: the command line and its tool allowlist,
the stream-json events and how they become `brain_provider` events, what a
fatal failure looks like, which of its tools read foreign text, and where its
usage and its transcripts are. `brain.Brain` drives it without knowing any of
this.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from jarvis import anthropic_usage
from jarvis import claude_env
from jarvis import usage_store
from jarvis.brain_provider import (Account, EventSink, ExitSink, HistorySource, SessionInfo, SignIn,
                            TextDelta, ToolStarted, TurnEnded, UsageReading, UsageSeen, identity_text)

if TYPE_CHECKING:
    from jarvis.brain import BrainConfig

log = logging.getLogger("jarvis.brain")

# The brain runs on the user's Claude subscription — never on an API key. Any of
# these in the server's environment (they come from .env for Fish/other features)
# would make the CLI authenticate with the key instead of the login.
#
# Re-exported from claude_env, which is where the scrub now lives: the run
# pipeline needs exactly the same one, and the first copy of this rule was
# fixed here and nowhere else for a whole milestone.
SCRUBBED_ENV_PREFIXES = claude_env.SCRUBBED_ENV_PREFIXES
SCRUBBED_ENV_KEYS = claude_env.SCRUBBED_ENV_KEYS

# ALLOWLIST (`--tools`), not a denylist: anything a future CLI adds is off by
# default. The MCP tools are namespaced `mcp__<server>__<tool>`.
# `ListAgents` is deliberately absent: it cannot see sessions that bound no
# inbox socket (4 of 17 on the dev machine), and `list_agents` reads the
# roster directly.
ALLOWED_TOOLS = [
    "mcp__jarvis__list_agents",
    "mcp__jarvis__workflow_status",
    "mcp__jarvis__conversation_history",
    "mcp__jarvis__restore_capture",
    "mcp__jarvis__session_detail",
    "mcp__jarvis__steer_session",
    "mcp__jarvis__answer_dialog",
    "mcp__jarvis__spawn_run",
    "mcp__jarvis__start_build",
    "mcp__jarvis__build_status",
    "mcp__jarvis__review_document",
    "mcp__jarvis__approve_document",
    "mcp__jarvis__run_command",
    "mcp__jarvis__create_project",
    "mcp__jarvis__run_status",
    "mcp__jarvis__cancel_run",
    "mcp__jarvis__list_projects",
    "mcp__jarvis__open_in_browser",
    "mcp__jarvis__open_in_terminal",
    "mcp__jarvis__read_page",
    "mcp__jarvis__look_at_page",
    "mcp__jarvis__what_is_on_screen",
    "mcp__jarvis__look_at_screen",
    "mcp__jarvis__github_repo",
    "mcp__jarvis__usage_status",
    "mcp__jarvis__connections",
    "mcp__jarvis__enable_session_inbox",
    "mcp__jarvis__repo_overview",
    "mcp__jarvis__search_repo",
    "mcp__jarvis__read_file",
    "mcp__jarvis__search_history",
    "mcp__jarvis__read_history",
    "mcp__jarvis__open_in_editor",
    "mcp__jarvis__remember",
    "mcp__jarvis__recall",
    "mcp__jarvis__project_note",
    "mcp__jarvis__write_journal",
    # The folder catalog (server.py's folder tools): reads come back in an
    # untrusted block, and registering and removing are ACTING_TOOLS.
    "mcp__jarvis__list_folders",
    "mcp__jarvis__find_folder",
    "mcp__jarvis__browse_folder",
    "mcp__jarvis__read_folder_file",
    "mcp__jarvis__register_folder",
    "mcp__jarvis__unregister_folder",
    "mcp__jarvis__folder_detail",
    "mcp__jarvis__change_folder",
    "mcp__jarvis__folder_candidates",
    "mcp__jarvis__change_folder_candidate",
    # The rest of what the page shows and does (server.py): the team, its
    # network and its people, messages between people, diagnostics, a
    # workflow's decisions and a run's retry. Reads that carry other people's
    # words come back in an untrusted block; every change is an ACTING_TOOL.
    "mcp__jarvis__team_status",
    "mcp__jarvis__find_github_user",
    "mcp__jarvis__change_team_member",
    "mcp__jarvis__team_network",
    "mcp__jarvis__people",
    "mcp__jarvis__people_messages",
    "mcp__jarvis__read_people_thread",
    "mcp__jarvis__mark_people_message_read",
    "mcp__jarvis__set_people_thread",
    "mcp__jarvis__change_people_draft",
    # Work packages a teammate sends: opening one and running its steps are
    # ACTING_TOOLS; the results go back through draft_message's files.
    "mcp__jarvis__open_package",
    "mcp__jarvis__list_packages",
    "mcp__jarvis__run_package_command",
    "mcp__jarvis__package_results",
    "mcp__jarvis__diagnostics",
    "mcp__jarvis__answer_workflow_decision",
    "mcp__jarvis__dismiss_workflow",
    "mcp__jarvis__retry_run",
    # The CLI's own two, and the only non-JARVIS tools here. JARVIS could read
    # a page he was handed the address of and nothing else — "look it up" had
    # no answer at all. Both were verified inside this exact flag set
    # (`--tools`, `--strict-mcp-config`, subscription login): WebFetch answers
    # in ~9s, WebSearch in ~16s. Scraping a search engine instead returns an
    # anti-bot page in 0.3s, which is why there is no scraper.
    #
    # Their results are attacker-written text that lands in the context with
    # no `_wrap_untrusted` around it — the CLI puts it there, not JARVIS. See
    # WEB_CONTENT_TOOLS below and server.py's `_untrusted_content_refusal`.
    "WebSearch",
    "WebFetch",
    # Loads the repo's `skills/<name>/SKILL.md`, synced into the brain home.
    "Skill",
]

# JARVIS ships connected to nothing, and the user brings their own MCP servers
# by naming them in `<data>/jarvis/connections.json`. Their tools arrive as
# `mcp__<their-server>__<tool>`, which is on no list above — so the grant is
# computed per launch, from their file, and added to (never merged into)
# ALLOWED_TOOLS.
#
# It stays an ALLOWLIST. What is granted is exactly one `mcp__<server>` per
# server the user wrote down themselves; a server they did not declare is
# still refused, and so is every built-in a future CLI invents. Nothing here
# is ever expressed as "everything except".
#
# The grant names the SERVER, not its tools, and that is not laziness: JARVIS
# cannot know a server's tool names before it starts one, so enumerating them
# would mean either starting every server twice or guessing. Verified against
# `claude` 2.1.259: `--tools mcp__weather` admits `mcp__weather__forecast` and
# `mcp__weather__tide`.
#
# Also measured, and worth stating plainly because it decides how much this
# flag is actually load-bearing: that CLI does NOT filter MCP tools by
# `--tools` at all — with `--tools WebSearch` and a weather server in
# `--mcp-config`, both weather tools were still offered to the model. What
# really gates an MCP tool is whether its server is in `--mcp-config`
# (see server.py's `_write_mcp_config`) and, for JARVIS's own, the origin gate
# on `/internal/tool`. The grant is kept anyway: it costs nothing, it states
# the intent in the one place a reader will look, and if the CLI ever enforces
# `--tools` over MCP names again, a user's declared server keeps working
# instead of going silently dead.
def granted_tools(connections: list[str]) -> list[str]:
    """ALLOWED_TOOLS plus one whole-server grant per declared connection."""
    return ALLOWED_TOOLS + [f"mcp__{name}" for name in connections]

# Tools whose results put text from the open web into the brain's context. A
# turn that has used one may not also act unsupervised (server.py gates it);
# `_handle` sees them in the CLI's own tool_use events.
WEB_CONTENT_TOOLS = {"WebSearch", "WebFetch"}


def untrusted_tool_source(name: str) -> Optional[str]:
    """What a tool result came FROM, if it came from outside JARVIS — a short
    label to say out loud — or None if the turn stays clean.

    The user's own MCP servers are treated exactly like the open web, and the
    distinction that decides it is between the CODE and the CONTENT. They
    vouched for the code: they chose the server and gave it their token. They
    did not write what it returns — a Notion page somebody shared with them, a
    GitHub issue a stranger opened, a Slack message, a calendar invitation
    with a title anyone could set. That text lands in a brain holding
    `spawn_run`, `run_command`, `steer_session` and `start_build`, with no
    `_wrap_untrusted` around it, because the CLI puts it there and JARVIS
    never handles it — the same hole `WebFetch` has and for the same reason.
    So it goes through the same gate rather than a second one; see server.py's
    `_untrusted_content_refusal`.

    JARVIS's own `mcp__jarvis__*` results are exempt: where they carry
    somebody else's words they are already inside `<session-output>`, and
    gating them would shut the assistant down entirely.
    """
    if name in WEB_CONTENT_TOOLS:
        return "a web page"
    if name.startswith("mcp__") and not name.startswith("mcp__jarvis__"):
        parts = name.split("__")
        if len(parts) > 2 and parts[1]:
            return parts[1]
    return None

# Only an explicit rejection blocks turns. The CLI also sends courtesy statuses
# — "allowed_warning" means "you have passed a utilisation threshold", NOT that
# you are cut off. Treating one of those as a limit mutes JARVIS completely,
# so an unrecognised status fails OPEN: we try, and a real limit comes back as
# an error result we can speak.
BLOCKING_RATE_LIMIT_STATUSES = {"rejected", "blocked", "exceeded", "throttled"}

# A warm-up failure whose text matches this is PERMANENT: no restart heals an
# expired login, so retrying just burns the whole restart budget in seconds
# (as happened live: 3 restarts in 5s on an expired OAuth session). Matched
# case-insensitively on the stable fragments ("failed to authenticate",
# "oauth"), not the CLI's full sentence ("Failed to authenticate: OAuth
# session expired and could not be refreshed") -- so any future auth-shaped
# wording from the CLI is still caught. When the text doesn't match, the
# failure is treated as transient (the safe default): a wrongly-fatal call
# mutes a brain that would have recovered, which is worse than one extra
# restart on something that really was permanent.
_FATAL_AUTH_PATTERN = re.compile(r"failed to authenticate|oauth", re.IGNORECASE)


def _classify_fatal_failure(error_text: Optional[str]) -> Optional[str]:
    """A short machine-readable cause (e.g. "auth") for a warm-up failure's
    raw error text, or None if it should be treated as transient."""
    if error_text and _FATAL_AUTH_PATTERN.search(error_text):
        return "auth"
    return None

def context_tokens(usage: dict) -> int:
    """How big the window IS: the prompt as sent, which is the uncached
    part plus the part served from cache.

    Deliberately NOT `+ cache_creation_input_tokens`. Those are the cache
    being rebuilt out of the same prompt, not extra tokens in the window
    -- a turn that re-creates the cache reports the whole floor under
    both `cache_creation` and (next turn) `cache_read`. Summing all three
    counted a cache miss as the conversation doubling: live, a 60k
    budget rotated at ~30k of actual talk, and the user asked why his
    assistant compacted so often. The window is the same size either
    way; only the billing column changed.
    """
    return usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)


class ClaudeSession:
    """One `claude -p` process: one conversation. Its stdout is read from the
    moment it opens, and every event goes to `on_event` in the brain's terms;
    when the process ends, `on_exit` hears it once."""

    def __init__(self, config: "BrainConfig", on_event: EventSink, on_exit: ExitSink):
        self.config = config
        self._on_event = on_event
        self._on_exit = on_exit
        self.claude = config.claude_path or shutil.which("claude") or "claude"
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        # Text blocks of the turn in flight: an API error arrives as one.
        self._assistant_text: list[str] = []

    # ── what it runs ─────────────────────────────────────────────────────
    def command(self, instructions: str) -> list[str]:
        c = self.config
        cmd = shlex.split(self.claude) + [
            "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages",
            "--model", c.model, "--effort", c.effort, "--name", "jarvis",
            "--setting-sources", "project", "--strict-mcp-config",
            "--tools", ",".join(granted_tools(c.connections)),
            "--settings", json.dumps({"crossSessionInbound": "accept"}),
            "--dangerously-skip-permissions",
            "--append-system-prompt", instructions,
        ]
        if c.mcp_config:
            cmd += ["--mcp-config", str(c.mcp_config)]
        cmd += list(c.extra_args)
        return cmd

    @staticmethod
    def child_env() -> dict[str, str]:
        return claude_env.child_env()

    # ── lifecycle ────────────────────────────────────────────────────────
    @property
    def alive(self) -> bool:
        proc = self.proc
        return proc is not None and proc.returncode is None and proc.stdin is not None

    async def open(self, instructions: str) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.command(instructions), cwd=str(self.config.home), env=self.child_env(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Without this the brain's readers keep asyncio's 64 KiB line
            # limit, and the recovery below turns an oversized reply into a
            # SKIPPED one: JARVIS goes silent instead of answering. The
            # ceiling has to be raised here too, not only in run_executor.
            limit=claude_env.STREAM_LINE_LIMIT,
        )
        self._reader = asyncio.create_task(self._read_stdout(self.proc))
        self._stderr_task = asyncio.create_task(self._drain_stderr(self.proc))

    async def send(self, text: str) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise BrokenPipeError("the claude process is not running")
        self._assistant_text = []
        line = json.dumps({"type": "user", "message": {"role": "user", "content": text}})
        proc.stdin.write((line + "\n").encode())
        await proc.stdin.drain()

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
                log.warning("brain: did not exit after stdin close; killing")
                self.kill()
            except Exception as e:
                log.warning(f"brain: error while stopping: {e}")
                self.kill()
        for task in (self._reader, self._stderr_task):
            if task and not task.done():
                try:
                    await asyncio.wait_for(task, 2.0)
                except asyncio.TimeoutError:
                    task.cancel()
                except Exception as e:
                    log.warning(f"brain: reader task ended with {e}")

    # ── stdout protocol ──────────────────────────────────────────────────
    async def _read_stdout(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            while True:
                # A line bigger than even the raised claude_env.STREAM_LINE_
                # LIMIT makes readline() raise ValueError. Uncaught, that
                # used to kill this whole reader task — which is how the
                # brain went silent mid-conversation ("my language systems
                # are down"). Its bytes are already discarded by readline()
                # itself (that is what keeps the stream aligned on the next
                # '\n'), so the fix is to log and keep reading.
                try:
                    raw = await proc.stdout.readline()
                except ValueError as e:
                    log.warning(f"brain: skipping oversized stdout line ({e})")
                    continue
                if not raw:
                    break
                try:
                    # decode() FIRST, and with errors="replace".
                    #
                    # json.loads() accepts bytes, but it decodes them itself
                    # and a bad byte there raises UnicodeDecodeError — a
                    # ValueError, but NOT a JSONDecodeError, so it escaped
                    # this loop entirely, ran _on_exit, and left the brain
                    # with no reader while its process was still alive and
                    # still writing. The oversized-line recovery above can
                    # produce exactly that: readline()'s overrun path clears
                    # the buffer at an arbitrary byte offset, so the next
                    # line can begin mid-codepoint. run_executor.py has
                    # always decoded this way; this is the same treatment.
                    ev = json.loads(raw.decode(errors="replace"))
                except ValueError:
                    continue
                try:
                    self._handle(ev)
                except Exception as e:  # one malformed event must not kill the reader
                    log.warning(f"brain: bad event ignored: {e}")
        finally:
            code = await proc.wait()
            await self._on_exit(self, code)

    def _emit(self, event) -> None:
        try:
            self._on_event(self, event)
        except Exception as e:  # a listener must never kill the reader
            log.warning(f"brain: event listener failed: {e}")

    def _handle(self, ev: dict) -> None:
        """One stream-json event, in the brain's terms."""
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            servers = ev.get("mcp_servers")
            tools = [str(t) for t in ev.get("tools")] if isinstance(ev.get("tools"), list) else []
            # The CLI names an MCP server's tools `mcp__<server>__<tool>`.
            by_server: dict[str, list[str]] = {}
            for name in tools:
                parts = name.split("__", 2)
                if len(parts) == 3 and parts[0] == "mcp" and parts[1]:
                    by_server.setdefault(parts[1], []).append(parts[2])
            self._emit(SessionInfo(
                conversation_id=ev.get("session_id"), model=ev.get("model"),
                servers=[s for s in servers if isinstance(s, dict)] if isinstance(servers, list) else [],
                tools=tools, server_tools=by_server))
        elif kind == "stream_event":
            e = ev.get("event") or {}
            d = e.get("delta") or {}
            if e.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                text = d.get("text", "")
                if text:
                    self._emit(TextDelta(text))
        elif kind == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    self._emit(ToolStarted(str(block.get("name"))))
                elif block.get("type") == "text" and block.get("text"):
                    self._assistant_text.append(str(block["text"]))
        elif kind == "rate_limit_event":
            info = dict(ev.get("rate_limit_info") or {})
            status = str(info.get("status") or "")
            # This event is the only place the CLI tells JARVIS how much of
            # the subscription's windows is gone, and it arrives only while a
            # turn is in flight. Write it down before anything else happens
            # to it — but never let bookkeeping kill a turn.
            try:
                usage_store.record(info)
            except Exception as e:
                log.warning(f"brain: could not record usage ({e})")
            blocked = status in BLOCKING_RATE_LIMIT_STATUSES
            if not blocked:
                if status and not status.startswith("allowed"):
                    log.warning(f"brain: unrecognised rate-limit status {status!r}; treating as usable")
                elif status == "allowed_warning":
                    pct = info.get("utilization")
                    log.info(f"brain: usage warning — {info.get('rateLimitType')} window at "
                             f"{pct:.0%}" if isinstance(pct, float) else f"brain: usage warning ({status})")
            resets = info.get("resetsAt")
            self._emit(UsageSeen(
                blocked=blocked,
                resets_at=resets if isinstance(resets, (int, float)) else None,
                window=info.get("rateLimitType"),
                summary={"status": status, "utilization": info.get("utilization"),
                         "windows": info.get("unifiedWindows") or {}},
                detail=info))
        elif kind == "result":
            usage = ev.get("usage") or {}
            error = None
            if ev.get("is_error") or (ev.get("subtype") and ev.get("subtype") != "success"):
                # e.g. an API auth error: the CLI reports subtype "success" with
                # is_error true and puts the message in an assistant text block.
                error = (ev.get("result") if isinstance(ev.get("result"), str) and ev.get("result")
                         else " ".join(self._assistant_text) or f"claude reported {ev.get('subtype')}")
                log.error(f"brain: turn failed: {error[:300]}")
            self._emit(TurnEnded(context_tokens=context_tokens(usage),
                                 output_tokens=usage.get("output_tokens", 0), error=error))

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        try:
            while True:
                # Same overrun as stdout: an oversized line must not stop
                # this loop, because once nobody drains stderr the child's
                # next write to it blocks on a full, unread pipe forever —
                # the process goes silent without even exiting.
                try:
                    raw = await proc.stderr.readline()
                except ValueError as e:
                    log.warning(f"brain: skipping oversized stderr line ({e})")
                    continue
                if not raw:
                    return
                log.debug(f"brain stderr: {raw.decode(errors='replace').rstrip()}")
        except Exception as e:
            log.warning(f"brain: stderr reader stopped: {e}")


class ClaudeUsage:
    """The account's limits straight from Anthropic, between replies
    (anthropic_usage.py: the login held in memory, never refreshed here)."""

    def __init__(self, client: Optional[anthropic_usage.UsageClient] = None):
        self._client = client or anthropic_usage.UsageClient()

    def read(self) -> UsageReading:
        body = self._client.fetch()
        provider = self._client.provider()
        return UsageReading(service=provider["service"],
                            subscription=provider.get("subscription") or "",
                            report=anthropic_usage.as_rate_limit_info(body))


class ClaudeTranscripts:
    """The brain's own Claude Code transcripts: one `.jsonl` per generation,
    under `<claude config>/projects/<encoded brain home>/`."""

    suffix = ".jsonl"

    def folders(self) -> list[Path]:
        from jarvis import history_read
        return history_read.transcript_dirs()

    def message(self, record: dict) -> Optional[tuple[str, str]]:
        from jarvis import history_read
        return history_read.claude_message_text(record)


async def _signed_in_identity() -> tuple[Optional[str], Optional[str]]:
    """(email, organisation ID) from `claude auth status --json`, under the
    environment the brain runs with; (None, None) when it cannot be had.
    Read only for brain_accounts' keyed reference: what it printed is never
    logged."""
    from jarvis import preflight
    claude = shutil.which("claude")
    if not claude:
        return None, None
    rc, out, _ = await preflight._run_subprocess(claude, "auth", "status", "--json",
                                                 timeout=preflight.DEFAULT_CHECK_TIMEOUT,
                                                 env=claude_env.child_env())
    try:
        said = json.loads(out) if rc == 0 else None
    except ValueError:
        said = None
    if not isinstance(said, dict) or not said.get("loggedIn"):
        return None, None
    return identity_text(said.get("email")), identity_text(said.get("orgId"))


class ClaudeProvider:
    name = "anthropic"
    title = "Claude"
    default_model = "sonnet"

    def models(self) -> list[str]:
        return ["sonnet", "opus", "haiku"]

    async def account(self) -> Account:
        from jarvis import preflight
        check, (email, org_id) = await asyncio.gather(preflight._check_claude_login(),
                                                      _signed_in_identity())
        reading = usage_store.latest("anthropic") or {}
        plan = str((reading.get("provider") or {}).get("subscription") or "")
        return Account(signed_in=check.ok, plan=plan, detail=check.message,
                       email=email, org_id=org_id)

    async def start_sign_in(self) -> Optional[SignIn]:
        return None             # `claude` signs in for itself

    async def sign_in_finished(self, sign_in_id: str, timeout: float) -> bool:
        return False

    def session(self, config: "BrainConfig", on_event: EventSink, on_exit: ExitSink) -> ClaudeSession:
        return ClaudeSession(config, on_event, on_exit)

    def usage(self) -> ClaudeUsage:
        return ClaudeUsage()

    def history(self) -> HistorySource:
        return ClaudeTranscripts()

    def preflight(self) -> tuple[tuple, tuple]:
        from jarvis import preflight
        return ((preflight._check_claude_cli, preflight._check_claude_login),
                (preflight._check_anthropic_key_leftover_sync,))

    def failure_kind(self, error: Optional[str]) -> Optional[str]:
        return _classify_fatal_failure(error)

    def reads_foreign(self, tool: str) -> Optional[str]:
        return untrusted_tool_source(tool)
