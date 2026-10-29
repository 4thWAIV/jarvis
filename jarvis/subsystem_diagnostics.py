"""
subsystem_diagnostics.py — how each of JARVIS's major parts is doing, one
item per part, for the Diagnostics tab: above all, whether he can still talk
to the agents.

Each part has one probe, a plain function from what it reads to a
`Subsystem`: a state (`ok`, `degraded`, `down`, `idle` with nothing to judge,
`unknown` with no reading yet), one line saying why, and the checks that
decided it. Nothing is stored: every state is worked out when it is read,
the way the workflow monitor works out "quiet", from records that already
exist (the `messages` table, the workflow record, the voice record) and from
what the server holds in memory (the session watcher, the brain, and the
heartbeats and outcomes kept here).

  - Agent communications: the session watcher polling, the messages JARVIS
    sends reaching their sessions, the messages sessions drop being taken,
    and the needs board being read.
  - Voice input: voice_diagnostics.summary, judged, not repeated.
  - Brain, voice output (Fish Audio), the tool channel (the brain's MCP child
    to /internal/tool) and the workflow monitor.
  - The team server: team_client's own status, never worse than degraded,
    since JARVIS works without it.

The page judges its own connection. `gather` does the reads; the server's
route returns it, and the server's watch (`_watch_subsystems`) hints the
page and speaks when communications go down and come back.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from jarvis import (data_paths, run_store, session_messages, session_steer, voice_diagnostics,
                    workflow_store)
from jarvis.session_watch import DELIVERY_TIMEOUT_SEC, GONE

OK, DEGRADED, DOWN, IDLE, UNKNOWN = "ok", "degraded", "down", "idle", "unknown"
# Worst last: a subsystem is as bad as its worst check.
_RANK = {IDLE: 0, OK: 1, UNKNOWN: 2, DEGRADED: 3, DOWN: 4}

# A server this young has not had the chance to start its loops.
STARTUP_GRACE_SEC = 30.0

# The session watcher polls about every 3 s (a 1 s sleep and the poll).
WATCHER_LATE_SEC = 15.0
WATCHER_DOWN_SEC = 60.0
WATCHER_FAILURES_DOWN = 3

# How far back messages to sessions are judged, and how many are read.
MESSAGES_WINDOW_SEC = 3600.0
MESSAGES_READ = 50
# A message still `sent` this long after it went was never followed: the
# watcher that should have marked it undelivered did not get to it.
UNFOLLOWED_AFTER_SEC = DELIVERY_TIMEOUT_SEC + 30.0
# What a failed message's `failure` says when the channel lost it, as
# against JARVIS declining to send it (needs_a_human_hand, not_steerable,
# no_voice) or its session ending first.
LOST_FAILURES = frozenset({"not_live", "failed", "refused", "not delivered within a day"})
REACHED_STATUSES = frozenset({"delivered", "working", "answered"})
# Lost messages to this many different sessions in a row is the channel,
# not one session.
LOST_SESSIONS_DOWN = 2

# A message file a session dropped that has waited this long was not taken.
FILE_WAITING_SEC = 60.0

# Voice output: this many failed syntheses in a row is down; one this
# recent is degraded.
VOICE_FAILURES_DOWN = 3
VOICE_FAILURE_RECENT_SEC = 300.0
# Voice input: speech dropped this recently is "Dropping speech" (the page's
# own DROPPING_FOR_SEC).
VOICE_DROPPING_SEC = 60.0

# The brain's own MCP server, through which it reaches /internal/tool
# (server.RESERVED_SERVER_NAME).
TOOL_SERVER = "jarvis"

# The background loops' names, as the server's `_every` is given them.
NEEDS_LOOP = "needs board"
SESSION_MESSAGES_LOOP = "session messages"
WORKFLOW_LOOP = "workflow silence"


@dataclass(frozen=True)
class Check:
    id: str
    name: str
    state: str
    detail: str


@dataclass
class Subsystem:
    id: str
    name: str
    state: str
    summary: str
    checks: list[Check] = field(default_factory=list)
    messages: Optional[list[dict]] = None       # communications only


def worst(states) -> str:
    return max(states, key=_RANK.__getitem__, default=UNKNOWN)


def from_checks(id: str, name: str, checks: list[Check], fine: str, **extra) -> Subsystem:
    """A subsystem as bad as its worst check, saying which check and why
    when that is down or degraded, and `fine` otherwise."""
    state = worst(c.state for c in checks)
    bad = next((c for c in checks if c.state == state), None) if state in (DOWN, DEGRADED) else None
    summary = f"{bad.name}: {bad.detail}" if bad else "Starting" if state == UNKNOWN else fine
    return Subsystem(id, name, state, summary, checks, **extra)


def age(seconds: float) -> str:
    """A span in a few characters: 12 s, 3 min, 2 h."""
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.0f} h"


# ── what the server notes as it goes ─────────────────────────────────────

@dataclass
class Beat:
    """One background loop's heartbeat: each look that finished, or failed."""
    every: float
    started_at: float
    last_ok_at: Optional[float] = None
    failures: int = 0            # in a row
    error: Optional[str] = None

    def ok(self, now: Optional[float] = None) -> None:
        self.last_ok_at = time.time() if now is None else now
        self.failures, self.error = 0, None

    def failed(self, error: str) -> None:
        self.failures += 1
        self.error = error[:200]

    def down_after(self) -> float:
        return 4 * self.every + 30.0


beats: dict[str, Beat] = {}


def beat(name: str, every: float, now: Optional[float] = None) -> Beat:
    """A loop's heartbeat, new each time the loop starts."""
    beats[name] = Beat(every, time.time() if now is None else now)
    return beats[name]


class Outcomes:
    """The last few outcomes of something tried many times: (at, ok, detail)."""

    def __init__(self, keep: int = 20) -> None:
        self.items: deque = deque(maxlen=keep)

    def note(self, ok: bool, detail: str = "", now: Optional[float] = None) -> None:
        self.items.append((time.time() if now is None else now, ok, detail))


# Each Fish Audio synthesis, and each request on /internal/tool (refused for
# its token, or taken), and each tool call the brain's provider failed before
# it reached /internal/tool.
voice_output_calls = Outcomes()
tool_calls = Outcomes()


# ── the probes ───────────────────────────────────────────────────────────

def judge_loop(b: Optional[Beat], now: float) -> tuple[str, str]:
    if b is None:
        return UNKNOWN, "Not started"
    quiet = now - (b.last_ok_at or b.started_at)
    if quiet > b.down_after():
        return DOWN, f"Has not finished a look for {age(quiet)}"
    if b.failures:
        return DEGRADED, f"The last look failed: {b.error}"
    if b.last_ok_at is None:
        return UNKNOWN, "Starting"
    return OK, f"Last look {age(quiet)} ago"


def watcher_check(watcher, now: float, started_at: float) -> Check:
    """The session watcher: the one thing that sees the agents' sessions,
    sends queued messages and follows every message sent."""
    name = "Session watcher"
    young = now - started_at < STARTUP_GRACE_SEC
    if watcher is None or not watcher.running:
        if young:
            return Check("watcher", name, UNKNOWN, "Starting")
        return Check("watcher", name, DOWN, "Not running: no session is being watched")
    if watcher.poll_failures >= WATCHER_FAILURES_DOWN:
        return Check("watcher", name, DOWN,
                     f"{watcher.poll_failures} looks failed in a row: {watcher.last_poll_error}")
    if watcher.last_poll_ok_at is None:
        if young:
            return Check("watcher", name, UNKNOWN, "Starting")
        quiet = now - started_at
    else:
        quiet = now - watcher.last_poll_ok_at
    if quiet > WATCHER_DOWN_SEC:
        return Check("watcher", name, DOWN, f"Has not finished a look at the sessions for {age(quiet)}")
    if watcher.poll_failures:
        return Check("watcher", name, DEGRADED, f"The last look failed: {watcher.last_poll_error}")
    if quiet > WATCHER_LATE_SEC:
        return Check("watcher", name, DEGRADED, f"Last look {age(quiet)} ago")
    sessions = sum(s.state != GONE for s in watcher.snapshot.sessions)
    return Check("watcher", name, OK,
                 f"Watching {sessions} session{'s' if sessions != 1 else ''}; last look {age(quiet)} ago")


def message_outcome(m, now: float) -> Optional[str]:
    """"reached", "lost", or None (pending, queued, cancelled, or never sent
    by JARVIS's own choice)."""
    status = str(m.status)
    if m.delivered_at is not None or status in REACHED_STATUSES:
        return "reached"
    if status == "undelivered":
        return "lost"
    if status == "failed":
        return "lost" if session_steer.outcome_code(m.failure) in LOST_FAILURES else None
    if status == "sent" and now - (m.sent_at or m.staged_at) > UNFOLLOWED_AFTER_SEC:
        return "lost"
    return None


def _why_lost(m) -> Optional[str]:
    """What the channel said when this message was lost, if it said more
    than the bare outcome."""
    if str(m.status) == "undelivered":
        return "it never showed up in the agent's conversation"
    failure = m.failure or ""
    reason = failure.split(":", 1)[1].strip() if ":" in failure else ""
    return reason or None


def outgoing_check(messages: list, now: float) -> Check:
    """Messages to sessions, newest first, from the last hour."""
    name = "Messages to agents"
    recent = [(m, message_outcome(m, now)) for m in messages
              if now - (m.sent_at or m.staged_at) <= MESSAGES_WINDOW_SEC]
    judged = [(m, o) for m, o in recent if o is not None]
    if not judged:
        return Check("outgoing", name, IDLE, "No messages sent in the last hour")
    streak = []
    for m, outcome in judged:
        if outcome == "reached":
            break
        streak.append(m)
    sessions = {m.session_id for m in streak}
    if len(sessions) >= LOST_SESSIONS_DOWN:
        why = _why_lost(streak[0])
        return Check("outgoing", name, DOWN,
                     f"The last {len(streak)} messages, to {len(sessions)} different agents, "
                     f"did not reach them" + (f"; the latest: {why}" if why else ""))
    if streak:
        n = len(streak)
        why = _why_lost(streak[0])
        return Check("outgoing", name, DEGRADED,
                     f"The last {'message' if n == 1 else f'{n} messages'} to "
                     f"{streak[0].agent_name} did not reach it" + (f": {why}" if why else ""))
    reached = sum(o == "reached" for _, o in judged)
    lost = len(judged) - reached
    return Check("outgoing", name, OK,
                 f"{reached} reached their agents in the last hour"
                 + (f", {lost} did not" if lost else ""))


def incoming_check(b: Optional[Beat], waiting: int, now: float) -> Check:
    """Messages sessions drop for Tim: the loop that takes them, and any file
    left waiting."""
    name = "Messages from agents"
    state, detail = judge_loop(b, now)
    if state in (OK, UNKNOWN) and waiting:
        state, detail = DEGRADED, (f"{waiting} message{'s' if waiting != 1 else ''} "
                                   f"waiting over {age(FILE_WAITING_SEC)} to be picked up")
    return Check("incoming", name, state, detail)


def communications(watcher, messages: list, beats_now: dict, waiting: int,
                   now: float, started_at: float) -> Subsystem:
    checks = [
        watcher_check(watcher, now, started_at),
        outgoing_check(messages, now),
        incoming_check(beats_now.get(SESSION_MESSAGES_LOOP), waiting, now),
        Check("needs", "Needs board", *judge_loop(beats_now.get(NEEDS_LOOP), now)),
    ]
    return from_checks("communications", "Communications", checks, checks[0].detail,
                       messages=[message_json(m, now) for m in messages[:20]])


def message_json(m, now: float) -> dict:
    return {"id": m.id, "agentName": m.agent_name, "status": str(m.status),
            "outcome": message_outcome(m, now), "at": m.sent_at or m.staged_at,
            "failure": m.failure}


_STAGE_STATES = {"ok": OK, "warn": DEGRADED, "bad": DOWN, "unknown": UNKNOWN}
_STAGE_NAMES = {"mic": "Microphone", "browser": "Page", "connection": "Connection",
                "recogniser": "Recogniser", "jarvis": "Server"}


def voice_input(summary: dict, now: float) -> Subsystem:
    """Voice input, judged from the voice record's own summary."""
    state = summary["state"]
    dropped = summary.get("lastDropAt")
    checks = [Check(stage, _STAGE_NAMES.get(stage, stage), _STAGE_STATES.get(v["state"], UNKNOWN),
                    "" if v["value"] is None else f"{v['value']:.2f}")
              for stage, v in summary["pipeline"].items()]
    if state == "idle":
        return Subsystem("voice_input", "Voice input", IDLE, "Not listening",
                         [Check(c.id, c.name, IDLE, "") for c in checks])
    cause = summary.get("cause") or ""
    if state == "no-frames" or (dropped is not None and now - dropped < VOICE_DROPPING_SEC):
        return Subsystem("voice_input", "Voice input", DOWN,
                         f"Dropping speech{': ' + cause if cause else ''}", checks)
    if state != "healthy":
        return Subsystem("voice_input", "Voice input", DEGRADED, f"Falling behind: {cause}", checks)
    return Subsystem("voice_input", "Voice input", OK, "Hearing you", checks)


def brain(b, autostart: bool, now: float, started_at: float,
          calls: Optional[Outcomes] = None) -> Subsystem:
    """The brain, from the live Brain; and its tool calls, since a brain that
    cannot use a tool cannot do anything he asks of it."""
    def one(state: str, summary: str) -> Subsystem:
        usage = (Check("usage", "Usage limit", DEGRADED, "At the usage limit")
                 if b is not None and b.rate_limited else
                 Check("usage", "Usage limit", OK, "Within the limit"))
        provider = getattr(getattr(b, "provider", None), "name", None)
        checks = [Check("process", "Process", state, summary), usage]
        if calls is not None and calls.items and not calls.items[-1][1]:
            failing = f"Tool calls are failing: {calls.items[-1][2]}"
            checks.append(Check("tools", "Tool calls", DOWN, failing))
            if state in (OK, DEGRADED):
                state, summary = DOWN, failing
        return Subsystem("brain", f"Brain ({provider})" if provider else "Brain", state, summary,
                         checks)
    if b is None:
        return one(UNKNOWN, "Not started")
    if b.failed:
        return one(DOWN, "Its login has expired" if b.failure_reason == "auth"
                   else "Stopped after crashing repeatedly")
    if b.restarting:
        return one(DEGRADED, "Restarting after a crash or a stall")
    if not b.running:
        if not autostart:
            return one(UNKNOWN, "Not started (autostart is off)")
        if now - started_at < STARTUP_GRACE_SEC:
            return one(UNKNOWN, "Starting")
        return one(DOWN, "No brain process is running")
    if not b.ready:
        return one(UNKNOWN, "Warming up")
    if b.rate_limited:
        return one(DEGRADED, "At the usage limit")
    if b.recent_restarts:
        n = b.recent_restarts
        return one(DEGRADED, f"Restarted {n} time{'s' if n != 1 else ''} in the last "
                             f"{age(b.config.restart_window)}")
    return one(OK, "Ready")


def voice_output(has_key: bool, calls: Outcomes, now: float) -> Subsystem:
    """Fish Audio, from each synthesis's outcome."""
    def one(state: str, summary: str) -> Subsystem:
        return Subsystem("voice_output", "Voice output", state, summary,
                         [Check("fish", "Fish Audio", state, summary)])
    if not has_key:
        return one(DOWN, "No Fish Audio key: nothing can be said")
    items = list(calls.items)
    if not items:
        return one(UNKNOWN, "Nothing said yet")
    last = items[-VOICE_FAILURES_DOWN:]
    if len(last) == VOICE_FAILURES_DOWN and not any(ok for _, ok, _ in last):
        return one(DOWN, f"The last {VOICE_FAILURES_DOWN} sentences could not be voiced")
    failed = [at for at, ok, _ in items if not ok and now - at < VOICE_FAILURE_RECENT_SEC]
    if failed:
        n = len(failed)
        return one(DEGRADED, f"{n} sentence{'s' if n != 1 else ''} not voiced in the last "
                             f"{age(VOICE_FAILURE_RECENT_SEC)}")
    return one(OK, f"Last sentence voiced {age(now - items[-1][0])} ago")


def tool_channel(b, calls: Outcomes, now: float) -> Subsystem:
    """The brain's MCP child and /internal/tool."""
    def one(state: str, summary: str) -> Subsystem:
        taken = [at for at, ok, _ in calls.items if ok]
        last = (f"last tool call {age(now - taken[-1])} ago" if taken else "no tool calls yet")
        return Subsystem("tool_channel", "Tool channel", state, summary,
                         [Check("server", "JARVIS's tools in the brain", state, summary),
                          Check("calls", "Tool calls", OK if taken else IDLE, last.capitalize())])
    if calls.items and not calls.items[-1][1]:
        return one(DOWN, f"Tool calls are failing: {calls.items[-1][2]}")
    if b is None or not b.running:
        return one(UNKNOWN, "The brain is not running")
    server = next((s for s in b.mcp_servers if s.get("name") == TOOL_SERVER), None)
    if server is None:
        return one(UNKNOWN, "The brain has not reported its tools")
    status = str(server.get("status", "")).lower()
    if status == "failed":
        return one(DOWN, "The brain's link to JARVIS's tools would not start")
    if status != "connected":
        return one(DEGRADED, f"The brain's link to JARVIS's tools is {status or 'not connected'}")
    return one(OK, "Connected")


def workflows(active: list, b: Optional[Beat], now: float) -> Subsystem:
    """The workflow monitor: its watch for quiet, and the workflows running."""
    loop_state, loop_detail = judge_loop(b, now)
    quiet = [w for w in active if w.state == workflow_store.SILENT]
    if quiet:
        n = len(quiet)
        shown = (DEGRADED, f"{n} workflow{'s have' if n != 1 else ' has'} gone quiet")
    elif active:
        n = len(active)
        shown = (OK, f"{n} workflow{'s' if n != 1 else ''} running")
    else:
        shown = (IDLE, "No workflows running")
    checks = [Check("running", "Workflows", *shown),
              Check("watch", "Watch for quiet", loop_state, loop_detail)]
    s = from_checks("workflows", "Workflow monitor", checks, shown[1])
    if s.state == OK and shown[0] == IDLE:
        s.state = IDLE                  # watching, with nothing to watch
    return s


def team_server(team: dict, now: float) -> Subsystem:
    """The team server connection, from team_client's snapshot. Optional,
    so at worst degraded: a team server that is gone never turns the
    Diagnostics tab red."""
    state = team.get("state")
    since = ""
    last = team.get("last_contact_at")
    if last:
        try:
            since = age(now - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp())
        except ValueError:
            since = ""
    server = team.get("server_url") or team.get("last_server_url") or ""
    if state == "connected":
        me = (team.get("me") or {}).get("login")
        name = (team.get("team") or {}).get("name")
        shown = (OK, "Online" + (f": {name}" if name else "") + (f" as {me}" if me else "")
                     + (f", last contact {since} ago" if since else ""))
    elif state == "stale":
        shown = (DEGRADED, "Can't reach the team server"
                           + (f"; last contact {since} ago" if since else ""))
    elif state == "signed_out":
        shown = (DEGRADED, team.get("last_error") or "Signed out; sign in again in Settings")
    elif state == "connecting":
        shown = (UNKNOWN, "Connecting")
    elif state == "pairing":
        shown = (IDLE, "Waiting for this Mac to be approved on the team server")
    else:
        shown = (IDLE, "Not connected")
    checks = [Check("connection", "Connection", *shown)]
    if server:
        checks.append(Check("server", "Server", IDLE, server))
    return Subsystem("team_server", "Team server", shown[0], shown[1], checks)


# ── all of them ──────────────────────────────────────────────────────────

def gather(*, watcher, brain_now, autostart: bool, has_fish_key: bool, started_at: float,
           team: Optional[dict] = None, now: Optional[float] = None) -> list[Subsystem]:
    """Every subsystem, read now. Blocking (it reads the database): run it
    off the event loop."""
    now = time.time() if now is None else now
    messages = run_store.list_messages(limit=MESSAGES_READ)
    waiting = session_messages.waiting(data_paths.session_messages_dir(), now, FILE_WAITING_SEC)
    beats_now = dict(beats)
    return [
        communications(watcher, messages, beats_now, waiting, now, started_at),
        voice_input(voice_diagnostics.summary(now), now),
        brain(brain_now, autostart, now, started_at, tool_calls),
        voice_output(has_fish_key, voice_output_calls, now),
        tool_channel(brain_now, tool_calls, now),
        workflows(workflow_store.active(), beats_now.get(WORKFLOW_LOOP), now),
        team_server(team or {}, now),
    ]


def to_json(subsystems: list[Subsystem], now: float) -> dict:
    out = []
    for s in subsystems:
        row = asdict(s)
        if row["messages"] is None:
            del row["messages"]
        out.append(row)
    return {"now": now, "subsystems": out}
